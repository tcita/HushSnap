"""
Print per-type handle counts for a target process.

This is a lightweight Windows diagnostic helper used when a process handle
count changes but full Sysinternals/System Informer inspection would be noisy.
It does not duplicate or name individual handles; it only groups kernel handle
entries by object type index.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import sys


if sys.platform != "win32":
    sys.exit("This diagnostic only runs on Windows.")


ULONG = wintypes.ULONG
USHORT = wintypes.USHORT
HANDLE = wintypes.HANDLE
PVOID = wintypes.LPVOID
NTSTATUS = wintypes.LONG

SystemExtendedHandleInformation = 64
ObjectTypeInformation = 2
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", PVOID),
        ("UniqueProcessId", ctypes.c_size_t),
        ("HandleValue", ctypes.c_size_t),
        ("GrantedAccess", ULONG),
        ("CreatorBackTraceIndex", USHORT),
        ("ObjectTypeIndex", USHORT),
        ("HandleAttributes", ULONG),
        ("Reserved", ULONG),
    ]


class SYSTEM_HANDLE_INFORMATION_EX_HEADER(ctypes.Structure):
    _fields_ = [
        ("NumberOfHandles", ctypes.c_size_t),
        ("Reserved", ctypes.c_size_t),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", USHORT),
        ("MaximumLength", USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
NtQuerySystemInformation = ntdll.NtQuerySystemInformation
NtQuerySystemInformation.argtypes = [ULONG, PVOID, ULONG, ctypes.POINTER(ULONG)]
NtQuerySystemInformation.restype = NTSTATUS

NtQueryObject = ntdll.NtQueryObject
NtQueryObject.argtypes = [HANDLE, ULONG, PVOID, ULONG, ctypes.POINTER(ULONG)]
NtQueryObject.restype = NTSTATUS

kernel32.GetCurrentProcess.restype = HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = HANDLE
kernel32.DuplicateHandle.argtypes = [
    HANDLE,
    HANDLE,
    HANDLE,
    ctypes.POINTER(HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
kernel32.DuplicateHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x00000002


def query_handle_entries():
    size = 1024 * 1024
    while True:
        buffer = ctypes.create_string_buffer(size)
        required = ULONG(0)
        status = NtQuerySystemInformation(
            SystemExtendedHandleInformation,
            buffer,
            size,
            ctypes.byref(required),
        )
        if status == 0:
            header = SYSTEM_HANDLE_INFORMATION_EX_HEADER.from_buffer_copy(buffer)
            offset = ctypes.sizeof(SYSTEM_HANDLE_INFORMATION_EX_HEADER)
            entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
            for index in range(header.NumberOfHandles):
                start = offset + index * entry_size
                yield SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_buffer_copy(buffer, start)
            return
        if ctypes.c_ulong(status).value == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, int(required.value * 1.5) if required.value else size * 2)
            continue
        raise OSError(f"NtQuerySystemInformation failed: 0x{ctypes.c_ulong(status).value:08x}")


def query_type_name(handle: HANDLE) -> str:
    size = 4096
    buffer = ctypes.create_string_buffer(size)
    required = ULONG(0)
    status = NtQueryObject(handle, ObjectTypeInformation, buffer, size, ctypes.byref(required))
    if status != 0:
        return ""
    name = UNICODE_STRING.from_buffer_copy(buffer)
    if not name.Buffer or not name.Length:
        return ""
    return ctypes.wstring_at(name.Buffer, name.Length // ctypes.sizeof(ctypes.c_wchar))


def resolve_type_names(pid: int, representatives: dict[int, int]) -> dict[int, str]:
    names: dict[int, str] = {}
    source_process = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
    if not source_process:
        return names

    current_process = kernel32.GetCurrentProcess()
    try:
        for type_index, handle_value in representatives.items():
            duplicated = HANDLE()
            ok = kernel32.DuplicateHandle(
                source_process,
                HANDLE(handle_value),
                current_process,
                ctypes.byref(duplicated),
                0,
                False,
                DUPLICATE_SAME_ACCESS,
            )
            if not ok:
                continue
            try:
                names[type_index] = query_type_name(duplicated)
            finally:
                kernel32.CloseHandle(duplicated)
    finally:
        kernel32.CloseHandle(source_process)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize process handles by object type index")
    parser.add_argument("pid", type=int)
    args = parser.parse_args()

    counts: dict[int, int] = {}
    representatives: dict[int, int] = {}
    total = 0
    for entry in query_handle_entries():
        if int(entry.UniqueProcessId) != args.pid:
            continue
        total += 1
        type_index = int(entry.ObjectTypeIndex)
        counts[type_index] = counts.get(type_index, 0) + 1
        representatives.setdefault(type_index, int(entry.HandleValue))

    names = resolve_type_names(args.pid, representatives)

    print(f"PID {args.pid}: {total} handles")
    print("ObjectTypeIndex Type                Count")
    print("--------------- ------------------- -----")
    for type_index, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        name = names.get(type_index, "")
        print(f"{type_index:15d} {name[:19]:19s} {count:5d}")


if __name__ == "__main__":
    main()
