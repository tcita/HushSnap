"""
HushSnap In-Process Memory Leak & Footprint Diagnostic Suite
-----------------------------------------------------------
Tests both RapidOCR and Windows OCR engines inside a real PyQt6 QApplication context,
analyzing Python heap allocations using tracemalloc to isolate leaks down to the line of code.
"""

import sys
import gc
import tracemalloc
from pathlib import Path
from PyQt6 import QtCore, QtGui, QtWidgets

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hushsnap.constants import OCR_ENGINE_RAPID, OCR_ENGINE_WINDOWS
from hushsnap.ocr.recognition import recognize_result_from_pixmap
from hushsnap.ocr.rapidocr import recognize_rapidocr_result_from_pixmap
from hushsnap.ocr.engine import release_engine


def create_test_pixmap() -> QtGui.QPixmap:
    """Create a QPixmap containing text to give the OCR engines real data to parse."""
    pixmap = QtGui.QPixmap(600, 200)
    pixmap.fill(QtCore.Qt.GlobalColor.white)
    
    painter = QtGui.QPainter(pixmap)
    painter.setPen(QtCore.Qt.GlobalColor.black)
    font = painter.font()
    font.setPointSize(24)
    font.setBold(True)
    painter.setFont(font)
    
    painter.drawText(pixmap.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "Hello World! HushSnap Memory Test 123")
    painter.end()
    
    return pixmap


def print_differential_report(title: str, snapshot_before, snapshot_after, limit: int = 8):
    """Print the top line-by-line differences between two memory snapshots."""
    stats = snapshot_after.compare_to(snapshot_before, "lineno")
    print(f"\n--- {title} ---")
    print(f"{'File and Line Number':<65} | {'Memory Change':<12}")
    print("-" * 83)
    
    total_change = 0
    for stat in stats[:limit]:
        # Filter out standard library / unittest noise to keep it focused on project files if possible
        file_line = f"{stat.traceback[0].filename}:{stat.traceback[0].lineno}"
        # Make the filename relative for readability
        if "HushSnap" in file_line:
            file_line = file_line.split("HushSnap")[-1].lstrip("\\/")
        elif "site-packages" in file_line:
            file_line = "site-packages/" + file_line.split("site-packages")[-1].lstrip("\\/")
            
        mem_change_kb = stat.size_diff / 1024.0
        total_change += stat.size_diff
        print(f"{file_line[:65]:<65} | {mem_change_kb:+10.2f} KiB")
        
    print("-" * 83)
    print(f"{'Total Top Allocation Change:':<65} | {total_change / 1024.0:+10.2f} KiB")


def run_engine_leak_test(engine_name: str, recognize_fn, runs: int = 5):
    """Execute consecutive OCR runs and print memory reports using tracemalloc."""
    print("\n" + "=" * 83)
    print(f" TESTING ENGINE: {engine_name.upper()} ({runs} Runs) ")
    print("=" * 83)
    
    pixmap = create_test_pixmap()
    
    # 1. Warm-up (ONNX/WinRT loading memory is a one-time baseline setup, not a recurring leak)
    print("Pre-warming engine...")
    res = recognize_fn(pixmap)
    print(f"Warm-up complete. Recognized text: '{res.text.strip()}'")
    
    # Force initial cleanup
    gc.collect()
    
    # 2. Start tracemalloc tracking
    tracemalloc.start()
    baseline = tracemalloc.take_snapshot()
    
    # 3. Perform repeated runs
    print(f"Running {runs} consecutive OCR iterations...")
    for i in range(1, runs + 1):
        recognize_fn(pixmap)
        current, peak = tracemalloc.get_traced_memory()
        print(f"  Iteration {i:2d} | Current tracked memory: {current / (1024*1024):.2f} MB | Peak tracked memory: {peak / (1024*1024):.2f} MB")
            
    after_runs = tracemalloc.take_snapshot()
    
    # 4. Release engine and collect garbage to check if memory fully returns to baseline
    print("Releasing engine resources and forcing Garbage Collection...")
    release_engine(engine_name)
    
    # Multiple GC cycles to resolve circular refs
    for _ in range(3):
        gc.collect()
        
    final = tracemalloc.take_snapshot()
    
    # Stop tracing
    tracemalloc.stop()
    
    # 5. Reports
    print_differential_report(
        f"OPERATIONAL PEAK MEMORY GROWTH ({engine_name.upper()})",
        baseline,
        after_runs
    )
    
    print_differential_report(
        f"PERMANENT / UNRECLAIMED MEMORY LEAKS ({engine_name.upper()})",
        baseline,
        final
    )


def main():
    # Headless GUI context
    app = QtWidgets.QApplication(sys.argv)
    
    # Run test for Windows OCR
    try:
        run_engine_leak_test(
            OCR_ENGINE_WINDOWS,
            lambda pix: recognize_result_from_pixmap(pix),
            runs=10
        )
    except Exception as exc:
        print(f"\nWindows OCR Test skipped or failed: {exc}")
        
    # Run test for RapidOCR
    try:
        run_engine_leak_test(
            OCR_ENGINE_RAPID,
            lambda pix: recognize_rapidocr_result_from_pixmap(pix),
            runs=10
        )
    except Exception as exc:
        print(f"\nRapidOCR Test skipped or failed: {exc}")
        
    sys.exit(0)


if __name__ == "__main__":
    main()
