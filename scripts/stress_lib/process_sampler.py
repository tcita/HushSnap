"""Out-of-process memory / handle queries against the live MSIX HushSnap.exe.

Pure ctypes (no psutil / pywin32). The stress test runs OUTSIDE the app, so
to benchmark memory it must query the app's process counters via Win32:
``CreateToolhelp32Snapshot`` to find the PID, ``OpenProcess`` +
``GetProcessMemoryInfo`` + ``GetProcessHandleCount`` to read WS / Private
Bytes / handles / page faults.

This is the out-of-process counterpart to ``hushsnap.benchmark._sampler``
(which uses psutil on itself). All getters return -1 on failure so callers
can treat missing data as "unknown" rather than zero (which would corrupt
min/avg statistics).
"""

import ctypes
import ctypes.wintypes as wintypes

kernel32 = ctypes.windll.kernel32


# ── PID resolution ────────────────────────────────────────────────────────────

def find_hushsnap_pid():
    """Return the PID of the first HushSnap.exe process, or None.

    Uses CreateToolhelp32Snapshot (pure Win32, no subprocess fork) so it is
    cheap enough to call when refreshing a stale process handle. Falls back
    to tasklist only if the snapshot API fails.
    """
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE or snap is None:
        return None
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return None
        while True:
            if pe.szExeFile == "HushSnap.exe":
                return int(pe.th32ProcessID)
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return None


# ── memory counters ───────────────────────────────────────────────────────────

class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    """Win32 PROCESS_MEMORY_COUNTERS_EX, with a tail pad so cb matches what
    psapi writes on current Windows builds.

    The documented layout (cb, PageFaultCount, 7×SIZE_T) is 72 bytes on x64,
    but GetProcessMemoryInfo on this OS reports cb=96 and rejects a 72-byte
    struct with ERROR_INSUFFICIENT_BUFFER (122). Rather than chase the
    version-specific extra fields, we size the struct to 96 and read only the
    fields we need at their documented offsets. Buffer overruns are harmless
    (the trailing bytes are unused by us).
    """
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
        ("_pad", ctypes.c_ubyte * 24),  # 72 → 96 to satisfy cb on current OS
    ]


# psapi.GetProcessMemoryInfo; resolved lazily so the import survives on builds
# where psapi lives in kernel32 (modern Windows).
_GetProcessMemoryInfo = None
def _get_process_memory_info():
    global _GetProcessMemoryInfo
    if _GetProcessMemoryInfo is not None:
        return _GetProcessMemoryInfo
    try:
        psapi = ctypes.WinDLL("psapi.dll")
        fn = psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
        fn.restype = wintypes.BOOL
    except OSError:
        # Fall back to kernel32 export (Win7+ merged psapi into kernel32).
        fn = kernel32.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
        fn.restype = wintypes.BOOL
    _GetProcessMemoryInfo = fn
    return fn


class ExternalMemorySampler:
    """Reads WS / Private Bytes / handles / page faults of the live MSIX app.

    Caches the OpenProcess handle and only re-resolves the PID when the handle
    goes stale (e.g. the app crashed and was relaunched).
    """

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self):
        self._pid = None
        self._handle = None
        self._GetHandleCount = kernel32.GetProcessHandleCount
        self._GetHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._GetHandleCount.restype = wintypes.BOOL

    def _refresh_handle(self):
        pid = find_hushsnap_pid()
        if pid is None:
            self._pid = None
            self._handle = None
            return False
        if pid != self._pid:
            if self._handle:
                kernel32.CloseHandle(self._handle)
            self._handle = kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            self._pid = pid
        return bool(self._handle)

    def snapshot(self):
        """Return (ws_mb, pv_mb, handles, pagefaults); each -1 if unavailable.

        ws_mb  — Working Set in MB (physical RAM, matches Task Manager).
        pv_mb  — Private Bytes in MB (committed virtual, always >= WS).
        handles— process handle count.
        pagefaults — cumulative PageFaultCount since process start.
        """
        if not self._refresh_handle():
            return -1.0, -1.0, -1, -1
        pmc = PROCESS_MEMORY_COUNTERS_EX()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        if not _get_process_memory_info()(self._handle, ctypes.byref(pmc), pmc.cb):
            return -1.0, -1.0, -1, -1
        ws_mb = pmc.WorkingSetSize / (1024 * 1024)
        pv_mb = pmc.PrivateUsage / (1024 * 1024)
        h = wintypes.DWORD(0)
        if not self._GetHandleCount(self._handle, ctypes.byref(h)):
            h = wintypes.DWORD(-1)
        return ws_mb, pv_mb, int(h.value), int(pmc.PageFaultCount)

    def close(self):
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None
            self._pid = None


# ── process liveness ──────────────────────────────────────────────────────────

def is_hushsnap_running():
    """True if at least one HushSnap.exe process is alive.

    Uses tasklist (always present) rather than a third-party dep.
    """
    import subprocess
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq HushSnap.exe", "/NH", "/FO", "CSV"],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return True  # assume alive if we cannot tell — safer than a false crash
    return "HushSnap.exe" in out
