"""
A/B benchmark: CPU EP arena_extend_strategy (kSameAsRequested vs kNextPowerOfTwo).

Tests with cn.png, en.png, ja.png against ground-truth .txt files.

Background
  ──────────
  ONNX Runtime's CPUExecutionProvider has an arena_extend_strategy option:
  - kSameAsRequested  — allocates exactly the requested size (lower memory,
                         more malloc/free churn).  This was the old default.
  - kNextPowerOfTwo   — rounds up to the next power of two (less churn,
                         slightly higher peak memory).  New production default.

  HushSnap aggressively trims the working set after each OCR call via
  SetProcessWorkingSetSize(-1, -1), so the extra arena memory from
  kNextPowerOfTwo is reclaimed promptly — the trade-off favours speed.

Usage
  ─────
  python scripts/benchmark_cpu_opts.py -n 7 -p
     7 iterations per config, enable memory profiling.

  python scripts/benchmark_cpu_opts.py --quick
     Fast smoke test.

Metrics
  ───────
  • Warm latency (avg / best, excluding cold iteration 0)
  • Peak Working Set (physical RAM)
  • Accuracy vs ground-truth .txt (SequenceMatcher ratio)
"""

import argparse
import difflib
import sys
import time
import unicodedata
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

SCRATCH_DIR = _project_root / "scratch"
TEST_CASES = {
    "cn": (SCRATCH_DIR / "cn.png", SCRATCH_DIR / "cn.txt"),
    "en": (SCRATCH_DIR / "en.png", SCRATCH_DIR / "en.txt"),
    "ja": (SCRATCH_DIR / "ja.png", SCRATCH_DIR / "ja.txt"),
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def compare_text(ocr_text: str, gt_text: str) -> dict:
    ocr_norm = _normalize(ocr_text)
    gt_norm = _normalize(gt_text)
    if ocr_norm == gt_norm:
        return {"ratio": 1.0, "match": "exact", "diff": "",
                "ocr_len": len(ocr_norm), "gt_len": len(gt_norm)}
    if not ocr_norm or not gt_norm:
        return {"ratio": 0.0, "match": "empty", "diff": "",
                "ocr_len": len(ocr_norm), "gt_len": len(gt_norm)}
    ratio = difflib.SequenceMatcher(None, ocr_norm, gt_norm).ratio()
    match_label = "near-exact" if ratio > 0.95 else ("partial" if ratio > 0.7 else "diverged")
    diff_lines = list(difflib.unified_diff(
        gt_norm.splitlines(keepends=True),
        ocr_norm.splitlines(keepends=True),
        fromfile="ground_truth", tofile="ocr", n=1,
    ))[:6]
    return {"ratio": ratio, "match": match_label, "diff": "".join(diff_lines),
            "ocr_len": len(ocr_norm), "gt_len": len(gt_norm)}


def _run_config(image_path: str, iterations: int, interval: float,
                profile: bool, arena_strategy: str):
    """Run benchmark with a specific arena_extend_strategy."""
    from hushsnap.benchmark import BenchmarkRunner
    from hushsnap.ocr.ppocr import set_engine_params_override, release_engine

    set_engine_params_override({
        "EngineConfig.onnxruntime.cpu_ep_cfg.arena_extend_strategy": arena_strategy,
    })

    with BenchmarkRunner(image_path) as runner:
        result = runner.run(
            iterations=iterations, interval=interval, profile=profile,
            verbose=True,
            engine_overrides={"arena_extend_strategy": arena_strategy},
        )

    release_engine()
    return result


def main():
    ap = argparse.ArgumentParser(
        description="A/B: arena_extend_strategy kSameAsRequested vs kNextPowerOfTwo"
    )
    ap.add_argument("-n", "--iterations", type=int, default=5,
                    help="OCR iterations per config (default: 5)")
    ap.add_argument("-p", "--profile", action="store_true",
                    help="Enable memory profiling on first warm iteration")
    ap.add_argument("-i", "--interval", type=float, default=3.0,
                    help="Seconds between iterations (default: 3.0)")
    ap.add_argument("--quick", action="store_true",
                    help="Quick test: 3 iters, 0.5 s interval")
    ap.add_argument("--images", nargs="+",
                    choices=list(TEST_CASES), default=list(TEST_CASES))
    args = ap.parse_args()

    iterations = 3 if args.quick else args.iterations
    interval = 0.5 if args.quick else args.interval

    configs = [
        ("kSameAsReq", "kSameAsRequested"),
        ("kNextPow2", "kNextPowerOfTwo"),
    ]

    print(f"\n{'=' * 72}")
    print(f"  ARENA EXTEND STRATEGY A/B BENCHMARK")
    print(f"  {iterations} iterations per config  |  {interval}s interval"
          f"{'  |  profile ON' if args.profile else ''}")
    print(f"  Configs: {', '.join(c[0] for c in configs)}")
    print(f"{'=' * 72}")

    all_results: dict[str, "BenchmarkResult"] = {}
    accuracy: dict[str, dict] = {}

    for img_key in args.images:
        img_path = str(TEST_CASES[img_key][0])
        gt_path = TEST_CASES[img_key][1]
        gt_text = gt_path.read_text(encoding="utf-8") if gt_path.exists() else ""

        print(f"\n{'─' * 72}")
        print(f"  Image: {img_key}.png  "
              f"(GT: {len(gt_text)} chars)" if gt_text else f"  Image: {img_key}.png")

        for config_name, arena_strategy in configs:
            label = f"{img_key}/{config_name}"
            print(f"\n  [{label}]", flush=True)
            t0 = time.perf_counter()

            result = _run_config(img_path, iterations, interval,
                                 args.profile, arena_strategy)
            elapsed = time.perf_counter() - t0
            all_results[label] = result

            warm = (result.iter_results[1:] if len(result.iter_results) > 1
                    else result.iter_results)
            times = [r.duration_ms for r in warm]
            if times:
                avg = sum(times) / len(times)
                best = min(times)
                peak_ws = max(r.peak_ws_mb for r in warm)
                peak_pv = max(r.peak_pv_mb for r in warm)
                chars = warm[0].text_chars if warm else 0
                print(f"     Warm avg: {avg:7.0f} ms  |  best: {best:7.0f} ms  "
                      f"|  peak WS: {peak_ws:6.0f} MB  "
                      f"|  Pvt: {peak_pv:6.0f} MB  |  chars: {chars}"
                      f"  ({elapsed:.0f}s wall)")

            if gt_text and result.text_full:
                sim = compare_text(result.text_full, gt_text)
                accuracy[label] = sim
                print(f"     Accuracy:  {sim['match']:>10}  "
                      f"(ratio={sim['ratio']:.3f},  "
                      f"OCR={sim['ocr_len']}c, GT={sim['gt_len']}c)")
                if sim["diff"] and sim["ratio"] < 0.99:
                    for line in sim["diff"].splitlines()[:2]:
                        print(f"       {line}")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 72}")
    print(f"  SUMMARY (warm avg, excluding cold iteration 0)")
    print(f"{'=' * 72}")
    header = (f"  {'Image':<8} {'kSameAsReq':>11} {'kNextPow2':>11} "
              f"{'Speedup':>10} {'WS Δ':>8}  Accuracy")
    print(header)
    print(f"  {'─' * 8} {'─' * 11} {'─' * 11} {'─' * 10} {'─' * 8}  {'─' * 16}")

    all_bl_avgs, all_opt_avgs = [], []

    for img_key in args.images:
        bl = all_results[f"{img_key}/kSameAsReq"]
        opt = all_results[f"{img_key}/kNextPow2"]

        def warm_avg(r):
            w = r.iter_results[1:] if len(r.iter_results) > 1 else r.iter_results
            return sum(x.duration_ms for x in w) / len(w) if w else 0

        bl_avg = warm_avg(bl)
        opt_avg = warm_avg(opt)
        all_bl_avgs.append(bl_avg)
        all_opt_avgs.append(opt_avg)

        speedup = bl_avg / opt_avg if opt_avg > 0 else 0

        def warm_ws(r):
            w = r.iter_results[1:] if len(r.iter_results) > 1 else r.iter_results
            return max(x.peak_ws_mb for x in w) if w else 0

        bl_ws = warm_ws(bl)
        opt_ws = warm_ws(opt)
        ws_delta = opt_ws - bl_ws

        acc_bl = accuracy[f"{img_key}/kSameAsReq"]
        acc_opt = accuracy[f"{img_key}/kNextPow2"]
        acc_line = f"BL={acc_bl['ratio']:.3f} OPT={acc_opt['ratio']:.3f}" if acc_bl and acc_opt else ""

        print(f"  {img_key:<8} {bl_avg:>9.0f} ms {opt_avg:>9.0f} ms "
              f"{speedup:>8.2f}× {ws_delta:>+7.1f} MB  {acc_line}")

    if all_bl_avgs and all_opt_avgs:
        overall_bl = sum(all_bl_avgs) / len(all_bl_avgs)
        overall_opt = sum(all_opt_avgs) / len(all_opt_avgs)
        pct = (overall_opt / overall_bl - 1) * 100
        print(f"\n  Overall: {overall_bl:.0f} ms → {overall_opt:.0f} ms  "
              f"({pct:+.1f}% {'faster' if pct < 0 else 'slower'})")
        if abs(pct) < 2:
            print(f"  ≈ No measurable difference — arena strategy is noise-level")
        elif pct < 0:
            print(f"  ✓ kNextPowerOfTwo is faster ({-pct:.1f}%)")
        else:
            print(f"  ⚠ kNextPowerOfTwo is slower — investigate")

    print(f"\n{'=' * 72}")
    print(f"  Done.")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
