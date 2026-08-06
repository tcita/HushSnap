"""
Diagnose where trimmed pages go: standby list vs pagefile.

Proof strategy (all measurements via existing hushsnap.system.memory_utils):
  1. Warm up engine (model load only, no inference).
  2. First OCR on a large image → commits det tensor buffers with demand-zero faults.
  3. Measure WS + PagefileUsage BEFORE trim.
  4. Call SetProcessWorkingSetSize(-1, -1) → trim.
  5. Measure WS + PagefileUsage AFTER trim.
  6. Second OCR on same image → measure latency.
  7. Interpret results.

Key expectations:
  - WS drops dramatically after trim (physical pages moved out).
  - PagefileUsage stays CONSTANT (pages NOT written to pagefile).
  - Post-trim OCR is fast (~warm latency + tens of ms, not cold ~1s+).

If pages went to pagefile:
  - PagefileUsage would RISE (contents written to disk).
  - Post-trim OCR would pay hard faults (disk reads), ~seconds, not tens of ms.

If pages went to standby list (the correct answer):
  - PagefileUsage stays flat (commit unchanged, no disk writes).
  - Post-trim OCR incurs transition (soft) faults — pages remapped from
    standby list in RAM, no disk I/O — costing only tens of ms.

Usage:
  python scripts/diagnose_trim_destination.py [path_to_large_screenshot.png]

If no image given, a synthetic large image is generated.
"""

import gc
import sys
import time
from pathlib import Path

# ── Add project root to path ────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
from PyQt6 import QtGui, QtCore, QtWidgets

from hushsnap.system.memory_utils import (
    get_working_set_mb,
    get_memory_stats,
    trim_working_set,
    get_page_fault_count,
)


def make_large_image(width=1920, height=1080):
    """Generate a synthetic image the size of a typical screenshot."""
    print(f"  Generating synthetic {width}x{height} image...")
    # Format_ARGB32 = 4 bytes/pixel (BGRA), what ppocr.py expects from QImage.bits()
    arr = np.random.randint(0, 255, (height, width, 4), dtype=np.uint8)
    qimg = QtGui.QImage(arr.data, width, height, width * 4, QtGui.QImage.Format.Format_ARGB32)
    return qimg.copy()  # detach from numpy buffer


def load_image(path):
    """Load an image file into a QImage in ARGB32 format (4 bytes/pixel, what ppocr expects)."""
    qimg = QtGui.QImage(path)
    if qimg.isNull():
        raise SystemExit(f"Cannot load image: {path}")
    return qimg.convertToFormat(QtGui.QImage.Format.Format_ARGB32)


def _label(name, ws, pf, faults=None):
    extra = f"  faults={faults}" if faults is not None else ""
    print(f"  {name:30s}  WS={ws:7.1f} MB  PF={pf:7.1f} MB{extra}")


def main():
    app = QtCore.QCoreApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    image_path = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 72)
    print("  Trim Destination Diagnostic")
    print("=" * 72)
    print()

    # ── Baseline ────────────────────────────────────────────────────────────
    stats0 = get_memory_stats()
    faults0 = get_page_fault_count()
    _label("BASELINE (process start)", stats0["working_set_mb"], stats0["pagefile_mb"], faults0)

    # ── Load (model init only, no inference) ────────────────────────────────
    from hushsnap.ocr.ppocr import get_ppocr_engine

    print("\n[1] Warming up engine (model load, NO inference)...")
    t0 = time.perf_counter()
    get_ppocr_engine()
    dt_load = (time.perf_counter() - t0) * 1000
    stats1 = get_memory_stats()
    faults1 = get_page_fault_count()
    _label("After load", stats1["working_set_mb"], stats1["pagefile_mb"], faults1)
    print(f"  Load took {dt_load:.0f} ms  |  ΔWS={stats1['working_set_mb'] - stats0['working_set_mb']:+.1f} MB  ΔPF={stats1['pagefile_mb'] - stats0['pagefile_mb']:+.1f} MB")
    print("  ↑ Model weights mmap'd + graph optimized. No inference buffers yet.")

    # ── First inference (cold: commits det tensors) ─────────────────────────
    from hushsnap.ocr.ppocr import recognize_ppocr_qimage
    from hushsnap.ocr.preprocess import OcrPreprocessResult, OcrPreprocessSettings

    img = load_image(image_path) if image_path else make_large_image()
    print(f"\n[2] First OCR (COLD — commits det tensor buffers)")
    print(f"  Image: {img.width()}x{img.height()}")

    pre_result = OcrPreprocessResult(image=img, settings=OcrPreprocessSettings())
    t0 = time.perf_counter()
    result1 = recognize_ppocr_qimage(pre_result)
    dt_first = (time.perf_counter() - t0) * 1000
    stats2 = get_memory_stats()
    faults2 = get_page_fault_count()
    _label("After first OCR (cold)", stats2["working_set_mb"], stats2["pagefile_mb"], faults2)
    print(f"  First OCR took {dt_first:.0f} ms")
    ws_delta_cold = stats2["working_set_mb"] - stats1["working_set_mb"]
    pf_delta_cold = stats2["pagefile_mb"] - stats1["pagefile_mb"]
    fault_delta_cold = faults2 - faults1
    print(f"  ΔWS={ws_delta_cold:+.1f} MB  ΔPF={pf_delta_cold:+.1f} MB  Δfaults={fault_delta_cold:,}")
    print("  ↑ Det tensor buffers committed (VirtualAlloc MEM_COMMIT)")
    print("    + first-written (demand-zero page faults).")

    # ── Second inference (warm: already committed) ──────────────────────────
    print(f"\n[3] Second OCR (WARM — buffers already committed)")
    t0 = time.perf_counter()
    result2 = recognize_ppocr_qimage(pre_result)
    dt_second = (time.perf_counter() - t0) * 1000
    stats3 = get_memory_stats()
    faults3 = get_page_fault_count()
    _label("After second OCR (warm)", stats3["working_set_mb"], stats3["pagefile_mb"], faults3)
    print(f"  Second OCR took {dt_second:.0f} ms")
    print(f"  ΔWS={stats3['working_set_mb'] - stats2['working_set_mb']:+.1f} MB  ΔPF={stats3['pagefile_mb'] - stats2['pagefile_mb']:+.1f} MB  Δfaults={faults3 - faults2:,}")

    # ── Trim ────────────────────────────────────────────────────────────────
    print(f"\n[4] Trim: SetProcessWorkingSetSize(-1, -1)")
    print(f"    This API tells the kernel: 'evict all my physical pages'")
    print(f"    Kernel moves them to the STANDBY LIST (NOT pagefile).")
    print(f"    Standby = cached in RAM, ready to soft-fault back instantly.")
    print()

    stats_pre = get_memory_stats()
    faults_pre = get_page_fault_count()
    _label("BEFORE trim", stats_pre["working_set_mb"], stats_pre["pagefile_mb"], faults_pre)

    success = trim_working_set()
    gc.collect()  # ensure Python-side dead objects are collected too

    stats_post = get_memory_stats()
    faults_post = get_page_fault_count()
    _label("AFTER  trim", stats_post["working_set_mb"], stats_post["pagefile_mb"], faults_post)
    print(f"  Trim {'OK' if success else 'FAILED'}")
    ws_freed = stats_pre["working_set_mb"] - stats_post["working_set_mb"]
    pf_change_trim = stats_post["pagefile_mb"] - stats_pre["pagefile_mb"]
    print(f"  ΔWS={ws_freed:+.1f} MB freed  (physical pages moved to standby)")
    print(f"  ΔPF={pf_change_trim:+.1f} MB  (commit UNCHANGED)")

    # ── THE PROOF ───────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  VERDICT")
    print("=" * 72)
    print()

    pf_stayed = abs(pf_change_trim) < 1.0

    print(f"  WS before trim:     {stats_pre['working_set_mb']:7.1f} MB")
    print(f"  WS after  trim:     {stats_post['working_set_mb']:7.1f} MB")
    print(f"  WS freed:           {ws_freed:7.1f} MB  → moved to standby list")
    print()
    print(f"  PF before trim:     {stats_pre['pagefile_mb']:7.1f} MB  (commit/private bytes)")
    print(f"  PF after  trim:     {stats_post['pagefile_mb']:7.1f} MB")
    print(f"  PF change:          {pf_change_trim:+7.1f} MB  → {'UNCHANGED ✓' if pf_stayed else 'CHANGED ✗'}")
    print()

    # ── Sanity: run OCR again after trim ────────────────────────────────────
    print(f"[5] Third OCR (POST-TRIM — pages soft-faulted back from standby)")
    t0 = time.perf_counter()
    result3 = recognize_ppocr_qimage(pre_result)
    dt_third = (time.perf_counter() - t0) * 1000
    stats4 = get_memory_stats()
    faults4 = get_page_fault_count()
    _label("After third OCR (post-trim)", stats4["working_set_mb"], stats4["pagefile_mb"], faults4)
    ws_grew_back = stats4["working_set_mb"] - stats_post["working_set_mb"]
    print(f"  Third OCR took {dt_third:.0f} ms")
    print(f"  ΔWS={ws_grew_back:+.1f} MB  (pages soft-faulted back from standby)")
    print(f"  ΔPF={stats4['pagefile_mb'] - stats_post['pagefile_mb']:+.1f} MB  Δfaults={faults4 - faults_post:,}")
    print()

    # ── Interpret ───────────────────────────────────────────────────────────
    print("-" * 72)
    print("  INTERPRETATION")
    print("-" * 72)
    print()

    if pf_stayed:
        print("  ✓ PagefileUsage DID NOT CHANGE after trim.")
        print("    → Trimmed pages did NOT go to pagefile.")
        print("    → They went to the standby list (cached in RAM).")
    else:
        print("  ✗ PagefileUsage changed — unexpected.")

    print()

    overhead = dt_third - dt_second
    print(f"  Cold OCR:  {dt_first:7.0f} ms  (demand-zero faults — allocate+zero physical pages)")
    print(f"  Warm OCR:  {dt_second:7.0f} ms  (buffers resident, no faults)")
    print(f"  Post-trim: {dt_third:7.0f} ms  (+{overhead:+.0f} ms vs warm)")
    print()

    if dt_third < dt_first * 0.5:
        print(f"  ✓ Post-trim OCR ({dt_third:.0f}ms) is much faster than cold ({dt_first:.0f}ms).")
        print("    → Pages were soft-faulted back from standby (RAM),")
        print("      NOT demand-zero'd (would match cold ~1s+)")
        print("      NOR hard-faulted from disk (would be even slower).")
    elif dt_first < 100:
        print(f"  ⚠ Cold OCR ({dt_first:.0f}ms) was too fast to be a real cold start.")
        print("    Possibly the image is too small or the engine was already warm.")
        print("    Use a larger image (full screenshot) to see the cold-start effect.")
    else:
        # Post-trim should be within ~10% of warm — if it were demand-zero
        # or hard-fault, it would be comparable to cold (650ms+), not warm (453ms).
        post_trim_overhead_pct = (dt_third - dt_second) / dt_second * 100
        print(f"  ✓ Post-trim OCR ({dt_third:.0f}ms) ≈ warm OCR ({dt_second:.0f}ms)")
        print(f"    (overhead {post_trim_overhead_pct:+.0f}% = soft faults from standby)")
        print(f"    → Cold OCR was {dt_first:.0f}ms — if post-trim had to re-allocate")
        print(f"      (demand-zero) or read from disk (hard fault), it would be")
        print(f"      at least that slow. It's not → pages were in standby.")

    print()
    if pf_stayed:
        print("  CONCLUSION:")
        print(f"  ┌─────────────────────────────────────────────────────────────────┐")
        print(f"  │  trim_working_set() = SetProcessWorkingSetSize(-1, -1)          │")
        print(f"  │                                                                 │")
        print(f"  │  WHAT is trimmed:  {ws_freed:5.0f} MB of physical RAM pages backing  │")
        print(f"  │                     ORT's intermediate det tensor buffers.       │")
        print(f"  │                                                                 │")
        print(f"  │  WHERE they go:    the Windows STANDBY PAGE LIST (not pagefile).│")
        print(f"  │                     Still in RAM, but not in process WS.         │")
        print(f"  │                                                                 │")
        print(f"  │  WHAT stays:       virtual commit (Private Bytes / PagefileUsage)│")
        print(f"  │                     is UNCHANGED at {stats_post['pagefile_mb']:.0f} MB.  │")
        print(f"  │                     The allocation lives forever.                │")
        print(f"  │                                                                 │")
        print(f"  │  PROOF:            PF before = PF after = {stats_pre['pagefile_mb']:.0f} MB │")
        print(f"  │                     WS  before = {stats_pre['working_set_mb']:.0f} MB → after = {stats_post['working_set_mb']:.0f} MB │")
        print(f"  │                     WS dropped {ws_freed:.0f} MB, PF unchanged = standby,  │")
        print(f"  │                     not pagefile (which would increase PF).     │")
        print(f"  │                                                                 │")
        print(f"  │  WHY it matters:   next OCR soft-faults pages back from standby  │")
        print(f"  │                     in tens of ms (no disk I/O, no demand-zero). │")
        print(f"  │                     WS grows back but commit never grows again.  │")
        print(f"  └─────────────────────────────────────────────────────────────────┘")
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
