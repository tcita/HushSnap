"""
Diagnostic: measure what Task Manager sees before and after release_engine().
Reports Private Working Set (the same metric Task Manager shows by default).
"""
import os
import gc
import sys
import ctypes
import ctypes.wintypes
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Windows API: query our own process memory counters ──────────────
class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def get_memory_mb() -> dict:
    """Return memory stats in MB as a dict."""
    pmc = PROCESS_MEMORY_COUNTERS_EX()
    pmc.cb = ctypes.sizeof(pmc)
    # Use proper 64-bit handle
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    handle = kernel32.GetCurrentProcess()
    # GetProcessMemoryInfo via psapi with proper types
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), ctypes.wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), ctypes.sizeof(pmc))
    return {
        "working_set_mb": pmc.WorkingSetSize / (1024 * 1024),
        "private_bytes_mb": pmc.PrivateUsage / (1024 * 1024),
        "peak_working_set_mb": pmc.PeakWorkingSetSize / (1024 * 1024),
    }


def fmt(stats: dict) -> str:
    return (
        f"Working Set: {stats['working_set_mb']:.1f} MB | "
        f"Private Bytes: {stats['private_bytes_mb']:.1f} MB | "
        f"Peak WS: {stats['peak_working_set_mb']:.1f} MB"
    )


def trim_working_set():
    """Call SetProcessWorkingSetSize with proper 64-bit handle."""
    kernel32 = ctypes.windll.kernel32
    # Use the correct handle type for 64-bit Windows
    GetCurrentProcess = kernel32.GetCurrentProcess
    GetCurrentProcess.restype = ctypes.c_void_p
    handle = GetCurrentProcess()

    SetProcessWorkingSetSize = kernel32.SetProcessWorkingSetSize
    SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_ssize_t]
    SetProcessWorkingSetSize.restype = ctypes.c_int
    result = SetProcessWorkingSetSize(handle, -1, -1)
    return result


# ── Main diagnostic ─────────────────────────────────────────────────
if __name__ == "__main__":
    # Need QApplication for QImage
    from PyQt6.QtWidgets import QApplication
    from PyQt6 import QtGui
    app = QApplication(sys.argv)

    print("=" * 70)
    print(" RELEASE ENGINE MEMORY DIAGNOSTIC")
    print("=" * 70)

    print(f"\n[1/6] Baseline (before any OCR loading):")
    print(f"  {fmt(get_memory_mb())}")

    # Load and warm up PP-OCR
    print(f"\n[2/6] Loading PP-OCR engine and running one OCR pass...")
    from hushsnap.ocr.ppocr import _get_engine, release_engine, recognize_ppocr_qimage

    # Create a test image
    image = QtGui.QImage(200, 100, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    painter.setPen(QtGui.QColor("black"))
    painter.setFont(QtGui.QFont("Arial", 24))
    painter.drawText(10, 60, "Hello Test 123")
    painter.end()

    result = recognize_ppocr_qimage(image)
    print(f"  OCR result: '{result.text}'")
    print(f"  {fmt(get_memory_mb())}")

    # Run a few more passes to stabilize
    print(f"\n[3/6] Running 5 more OCR passes to stabilize...")
    for i in range(5):
        recognize_ppocr_qimage(image)
    print(f"  {fmt(get_memory_mb())}")

    # Now release the engine
    print(f"\n[4/6] Calling release_engine()...")
    release_engine()
    print(f"  After release_engine (before trim):")
    print(f"  {fmt(get_memory_mb())}")

    # Try aggressive GC
    print(f"\n[5/6] Aggressive GC (5 passes)...")
    for _ in range(5):
        gc.collect()
    print(f"  After GC:")
    print(f"  {fmt(get_memory_mb())}")

    # Try working set trim with proper handle types
    print(f"\n[6/6] Calling SetProcessWorkingSetSize (proper 64-bit handle)...")
    ok = trim_working_set()
    print(f"  SetProcessWorkingSetSize returned: {ok} (1=success, 0=failure)")
    print(f"  After trim:")
    print(f"  {fmt(get_memory_mb())}")

    # Check if original release_engine's trim might have failed silently
    print(f"\n{'=' * 70}")
    print(" ANALYSIS")
    print(f"{'=' * 70}")
    
    stats = get_memory_mb()
    baseline_approx = 50  # rough baseline MB
    if stats["working_set_mb"] > 150:
        print(f"\n  ⚠ Working Set is still {stats['working_set_mb']:.0f} MB after release+trim.")
        print(f"    This means loaded DLLs (onnxruntime, numpy) still hold resident pages.")
        print(f"    These shared libraries cannot be unloaded without restarting the process.")
        print(f"\n  Private Bytes is {stats['private_bytes_mb']:.0f} MB.")
        print(f"    This is the true 'owned' memory. If this is also high, the C++ allocator")
        print(f"    kept freed pages mapped in virtual memory for reuse.")
    else:
        print(f"\n  ✓ Working Set dropped to {stats['working_set_mb']:.0f} MB — trim worked!")
