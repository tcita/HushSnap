"""Benchmark memory-cleanup strategies for recognize_ppocr_qimage.

Strategies tested:
  1. gc_only      — gc.collect() in finally (current production)
  2. trim_only    — _trim_working_set() instead of gc.collect()
  3. neither      — neither gc.collect() nor _trim_working_set()
  4. gc_then_trim — gc.collect() THEN _trim_working_set()
  5. trim_then_gc — _trim_working_set() THEN gc.collect()

Each strategy runs 5 iterations against the same test image.  Between
strategies the engine is fully released so each starts from a comparable
cold-engine baseline on its first iteration.

Usage:
    python scripts/benchmark_memory_strategies.py
"""

import gc
import sys
import time
import textwrap
from pathlib import Path

from PyQt6 import QtWidgets

# -- Add project root to path (in case we're not running via `python -m`) --
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from hushsnap.system.memory_utils import (
    get_working_set_mb,
    get_page_fault_count,
    get_handle_count,
    fmt_memory,
)
from hushsnap.ocr.engine import release_engine, trim_engine
from hushsnap.constants import OCR_ENGINE_PPOCR
from hushsnap.benchmark._runner import BenchmarkRunner, _ws_mb, _pvt_mb
import hushsnap.ocr.ppocr as ppocr_module


# =====================================================================
# Test image
# =====================================================================

def _find_test_image():
    """Find a suitable test PNG, preferring files with real OCR content."""
    candidates = [
        # RapidOCR test files (real Chinese OCR content)
        "scratch/RapidOCR/python/tests/test_files/ch_doc_server.png",
        "scratch/RapidOCR/python/tests/test_files/short.png",
        "scratch/RapidOCR/python/tests/test_files/arabic.png",
        # Project assets
        "scratch/logo.png",
        "assets/logo.png",
    ]
    for rel in candidates:
        p = _project_root / rel
        if p.exists():
            return p
    raise FileNotFoundError(
        "No test PNG found. Tried: " + ", ".join(candidates)
    )


# =====================================================================
# GC monkeypatch utilities
# =====================================================================

_ORIGINAL_GC_COLLECT = gc.collect


def _set_cleanup_strategy(strategy: str):
    """Monkeypatch gc.collect globally to implement the desired strategy.

    ``recognize_ppocr_qimage`` does ``import gc; gc.collect()`` inside its
    ``finally`` block.  Because ``import gc`` returns the same module
    object from ``sys.modules``, replacing ``gc.collect`` globally changes
    what happens during that finally -- without touching the source.
    """
    if strategy == "gc_only":
        gc.collect = _ORIGINAL_GC_COLLECT
    elif strategy == "trim_only":
        gc.collect = lambda generation=0: ppocr_module._trim_working_set()
    elif strategy == "neither":
        gc.collect = lambda generation=0: None
    elif strategy == "gc_then_trim":
        _orig = _ORIGINAL_GC_COLLECT
        gc.collect = lambda generation=0: (_orig(), ppocr_module._trim_working_set())
    elif strategy == "trim_then_gc":
        _orig = _ORIGINAL_GC_COLLECT
        gc.collect = lambda generation=0: (ppocr_module._trim_working_set(), _orig())
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def _restore_gc():
    """Restore the original gc.collect."""
    gc.collect = _ORIGINAL_GC_COLLECT


# =====================================================================
# Benchmark orchestration
# =====================================================================

def _release_and_wait():
    """Release PP-OCR engine fully, then wait for GC+trim to settle."""
    release_engine(OCR_ENGINE_PPOCR)
    gc.collect()
    time.sleep(2.0)


def _print_ws_snapshot(label: str):
    """Print current working set and handle count."""
    ws = get_working_set_mb()
    h = get_handle_count()
    pf = get_page_fault_count()
    print(f"  {label}: WS={ws:.1f} MB  handles={h}  pagefaults={pf}")


def run_benchmark(strategy: str, image_path: Path, iterations: int = 5):
    """Run a single benchmark configuration and return results."""

    desc_map = {
        "gc_only": "gc.collect() in finally",
        "trim_only": "_trim_working_set() instead of gc.collect()",
        "neither": "neither gc.collect() nor _trim_working_set()",
        "gc_then_trim": "gc.collect() THEN _trim_working_set()",
        "trim_then_gc": "_trim_working_set() THEN gc.collect()",
    }
    desc = desc_map.get(strategy, strategy)

    print(f"\n{'#'*70}")
    print(f"#  STRATEGY: {strategy}")
    print(f"#  {desc}")
    print(f"{'#'*70}")

    _set_cleanup_strategy(strategy)

    # Release engine from previous run (if any) and let memory settle
    print("\n[Setup] Releasing engine and letting memory settle...")
    _release_and_wait()
    _print_ws_snapshot("After release")

    print("\n[Setup] Creating BenchmarkRunner (will warm up engine)...")
    runner = BenchmarkRunner(image_path)

    try:
        result = runner.run(
            iterations=iterations,
            interval=3.0,
            profile=False,
            gc_between=False,
            idle_trim=False,
            verbose=True,
        )
    finally:
        runner.__exit__(None, None, None)

    return result


def _print_results(all_results: dict):
    """Print comparison tables and key findings for all strategies."""
    strategies = ["gc_only", "trim_only", "neither", "gc_then_trim", "trim_then_gc"]
    labels = {
        "gc_only": "GC Only",
        "trim_only": "Trim Only",
        "neither": "Neither",
        "gc_then_trim": "GC+Trim",
        "trim_then_gc": "Trim+GC",
    }

    print(f"\n\n{'='*100}")
    print("  COMPARISON TABLE -- Memory Cleanup Strategies (5 iterations each)")
    print(f"{'='*100}")

    # Header
    header = f"{'Metric':<38}"
    for s in strategies:
        header += f" {labels[s]:>12}"
    print(header)
    print("-" * 100)

    rows = [
        ("Avg Latency (ms)", "avg_duration_ms", ".1f"),
        ("Best Latency (ms)", "best_duration_ms", ".1f"),
        ("Max Working Set (MB)", "max_ws_mb", ".2f"),
        ("Max Private Bytes (MB)", "max_pv_mb", ".2f"),
        ("Avg Retention (warm)", "avg_retention", ".3f"),
        ("Shape Classification", "shape_classification", ""),
        ("Total Handle Delta", "total_handle_delta", ""),
        ("Text Consistency", "text_consistency", ""),
    ]

    for label, attr, fmt_spec in rows:
        line = f"{label:<38}"
        for s in strategies:
            res = all_results.get(s)
            if res is None:
                line += f" {'N/A':>12}"
                continue
            v = getattr(res, attr, "")
            if fmt_spec and isinstance(v, (int, float)):
                line += f" {v:{fmt_spec}}"
            else:
                line += f" {str(v):>12}"
        print(line)

    print("-" * 100)

    # -- Per-iteration breakdown --
    print(f"\n{'='*100}")
    print("  PER-ITERATION BREAKDOWN")
    print(f"{'='*100}")

    for s in strategies:
        res = all_results.get(s)
        if res is None:
            continue
        print(f"\n  -- {labels[s]} ({s}) --")
        print(f"  {'Iter':>5} {'Latency(ms)':>12} {'PeakWS(MB)':>11} "
              f"{'Retention':>10} {'PFaults(d)':>11} {'Handles(d)':>10}")
        for i, ir in enumerate(res.iter_results):
            print(f"  {i+1:>5} {ir.duration_ms:>12.1f} {ir.peak_ws_mb:>11.2f} "
                  f"{ir.retention:>10.3f} {ir.pf_delta:>+11d} {ir.h_delta:>+10d}")

    # -- Key findings --
    gc_res = all_results.get("gc_only")
    if gc_res is None:
        return

    print(f"\n\n{'='*100}")
    print("  KEY FINDINGS (vs gc_only baseline)")
    print(f"{'='*100}")

    baseline_lat = gc_res.avg_duration_ms
    baseline_ws = gc_res.max_ws_mb

    other = [s for s in strategies if s != "gc_only"]
    print(f"\n  {'Strategy':<20} {'Latency Δ':>12} {'WS Δ':>12} "
          f"{'Retention':>10} {'PFaults':>10} {'H Δ':>8}")
    print(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*8}")
    print(f"  {'gc_only (baseline)':<20} {baseline_lat:>11.1f} ms {baseline_ws:>9.1f} MB "
          f"{gc_res.avg_retention:>10.3f} {sum(r.pf_delta for r in gc_res.iter_results if r.pf_delta > 0):>10d} {gc_res.total_handle_delta:>+8d}")

    for s in other:
        res = all_results.get(s)
        if res is None:
            continue
        lat_d = res.avg_duration_ms - baseline_lat
        ws_d = res.max_ws_mb - baseline_ws
        total_pf = sum(r.pf_delta for r in res.iter_results if r.pf_delta > 0)
        print(f"  {labels[s]:<20} {lat_d:>+10.1f} ms {ws_d:>+9.1f} MB "
              f"{res.avg_retention:>10.3f} {total_pf:>10d} {res.total_handle_delta:>+8d}")

    print(f"\n{'='*100}")
    print("  Benchmark complete.")
    print(f"{'='*100}")


# =====================================================================
# Main
# =====================================================================

def main():
    image_path = _find_test_image()
    print(f"Test image: {image_path}")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    strategies = ["gc_only", "trim_only", "neither", "gc_then_trim", "trim_then_gc"]
    all_results = {}

    try:
        for strategy in strategies:
            result = run_benchmark(strategy, image_path, iterations=5)
            all_results[strategy] = result
    finally:
        _restore_gc()

    _print_results(all_results)


if __name__ == "__main__":
    main()
