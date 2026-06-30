"""Per-round + aggregate benchmark reporting and persistence.

Prints the live per-round bench line, the end-of-run aggregate report (with
the CRASH/FAILURE DUMP), and saves benchmark_<stamp>.json + .csv to
stress_results/. Also saves per-round log slices on failure.
"""

import json
import time
from pathlib import Path

# stress_lib/ → scripts/ → repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_DIR = _REPO_ROOT / "stress_results"


def save_round_log(log_path, round_idx, status, detail):
    """Copy this round's log slice to results/ for offline analysis."""
    _RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = _RESULTS_DIR / f"round_{round_idx:04d}_{status}_{stamp}.log"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        # Slice from the last session-start marker so the file is just this run.
        idx = text.rfind("Logging initialized.")
        body = text[idx:] if idx != -1 else text
        dest.write_text(
            f"=== stress test round {round_idx} | status={status} ===\n"
            f"=== detail: {detail} ===\n"
            f"=== saved {stamp} ===\n\n{body}",
            encoding="utf-8",
        )
        print(f"\n  >> log slice saved: {dest}")
    except Exception as exc:
        print(f"\n  !! failed to save log slice: {exc}")


def print_round_bench(rb, dt):
    """Print one round's benchmark line alongside the OK/FAIL line."""
    seg = []
    if rb.engine_ms >= 0:
        seg.append(f"engine={rb.engine_ms:.0f}ms")
    if rb.e2e_ms >= 0:
        seg.append(f"e2e={rb.e2e_ms:.0f}ms")
    if rb.pickup_ms >= 0:
        seg.append(f"pickup={rb.pickup_ms:.0f}ms")
    if rb.callback_ms >= 0:
        seg.append(f"cb={rb.callback_ms:.0f}ms")
    mem = []
    if rb.peak_ws_mb >= 0:
        mem.append(f"peakWS={rb.peak_ws_mb:.0f}MB")
    if rb.peak_pv_mb >= 0:
        mem.append(f"peakPV={rb.peak_pv_mb:.0f}MB")
    if rb.retention >= 0:
        mem.append(f"R={rb.retention:.2f}")
    if rb.pf_delta >= 0:
        mem.append(f"pf={rb.pf_delta:+d}")
    if rb.h_delta >= 0:
        mem.append(f"h={rb.h_delta:+d}")
    seq = f" seq={rb.seq}" if rb.seq is not None else ""
    parts = " ".join(seg) + ("  |  " + " ".join(mem) if mem else "")
    print(f"          bench{seq}: {parts}  (wall={dt:.2f}s)")


def _stats(values):
    """Return (n, min, avg, max, p95) over a list of non-negative numbers."""
    vals = sorted(v for v in values if v >= 0)
    if not vals:
        return 0, -1.0, -1.0, -1.0, -1.0
    n = len(vals)
    avg = sum(vals) / n
    # p95 via nearest-rank
    p95 = vals[max(0, min(n - 1, int(0.95 * n) - 1))]
    return n, vals[0], avg, vals[-1], p95


def print_crash_dump(failed):
    """Print each failed round's last marker + peak memory so a crash's
    halted stage is visible at a glance, alongside the measurements captured
    before the process died.

    The last [OCR_CHAIN] marker is the key diagnostic: a log that stops after
    ``recognize() engine call begin`` but before ``engine call end`` points at
    a native onnxruntime crash inside inference — which faulthandler cannot
    capture and only a WER minidump (see setup_wer_dumps.ps1) can stack-trace.
    """
    print(f"\n{'='*78}")
    print(f" CRASH / FAILURE DUMP  ({len(failed)} round{'s' if len(failed)!=1 else ''})")
    print(f"{'='*78}")
    print(f"  {'round':>5} {'status':<6} {'seq':>4} {'peakWS':>8} {'peakPV':>8}  last marker")
    print(f"  {'-'*5} {'-'*6} {'-'*4} {'-'*8} {'-'*8}  {'-'*40}")
    for r in failed:
        seq = str(r.seq) if r.seq is not None else "-"
        ws = f"{r.peak_ws_mb:.0f}" if r.peak_ws_mb >= 0 else "n/a"
        pv = f"{r.peak_pv_mb:.0f}" if r.peak_pv_mb >= 0 else "n/a"
        lm = r.last_marker or "(none)"
        print(f"  {r.round_idx:>5} {r.status:<6} {seq:>4} {ws:>8} {pv:>8}  {lm}")
    print(f"{'='*78}")
    print("  full per-round detail in benchmark_*.json; log slice in round_*_<status>.log")
    print("  for native crashes (no Python traceback), enable WER minidumps via "
          "scripts/setup_wer_dumps.ps1")
    print(f"{'='*78}")


def print_bench_report(results, cfg):
    """Print an aggregate benchmark summary across all collected rounds."""
    ok_results = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status != "ok"]
    if not ok_results:
        print(f"\n(no successful rounds to benchmark — {len(failed)} failed)")
        print_crash_dump(failed)
        return

    print(f"\n{'='*78}")
    print(f" BENCHMARK REPORT  ({len(ok_results)} ok rounds"
          f"{', ' + str(len(results) - len(ok_results)) + ' failed' if len(results) != len(ok_results) else ''})")
    print(f"{'='*78}")
    print(f" timing accuracy: ±{cfg.bench_poll*1000:.0f}ms (log-tail poll interval)")
    print(f" measured out-of-process against live MSIX HushSnap.exe")
    print("-" * 78)

    rows = [
        ("engine (ONNX inference)", [r.engine_ms for r in ok_results]),
        ("e2e (infer→show)",        [r.e2e_ms for r in ok_results]),
        ("callback (end→emit)",     [r.callback_ms for r in ok_results]),
        ("schedule (async→pickup)", [r.pickup_ms for r in ok_results]),
    ]
    # poll_ms is the log-tail detection latency — any segment whose observed
    # max is below it is smaller than the measurement resolution: the two
    # markers land in the same/adjacent poll window and the delta is just
    # perf_counter noise, not a real timing. Report it as below-resolution
    # rather than printing a misleadingly precise sub-ms number.
    poll_ms = cfg.bench_poll * 1000
    print(f"  {'stage':<26} {'n':>4} {'min':>9} {'avg':>9} {'max':>9} {'p95':>9}  (ms)")
    for label, vals in rows:
        n, mn, av, mx, p95 = _stats(vals)
        if n == 0:
            print(f"  {label:<26} {'n/a':>4}  (markers not observed — DEBUG-only marker or crash)")
        elif 0 <= mx < poll_ms:
            print(f"  {label:<26} {n:>4} {'<res':>9} {'<res':>9} {'<res':>9} {'<res':>9}"
                  f"  (≤{poll_ms:.0f}ms; below poll resolution)")
        else:
            print(f"  {label:<26} {n:>4} {mn:>9.1f} {av:>9.1f} {mx:>9.1f} {p95:>9.1f}")

    print("-" * 78)
    mem_rows = [
        ("Peak Working Set (MB)",   [r.peak_ws_mb for r in ok_results]),
        ("Peak Private Bytes (MB)", [r.peak_pv_mb for r in ok_results]),
        ("Retention (ws/peak)",     [r.retention for r in ok_results]),
        ("Page Faults (Δ/round)",   [r.pf_delta for r in ok_results]),
        ("Handles (Δ/round)",       [r.h_delta for r in ok_results]),
    ]
    print(f"  {'metric':<26} {'n':>4} {'min':>9} {'avg':>9} {'max':>9}")
    for label, vals in mem_rows:
        n, mn, av, mx, _ = _stats(vals)
        if n == 0:
            print(f"  {label:<26} {'n/a':>4}")
            continue
        if "Faults" in label or "Handles" in label:
            print(f"  {label:<26} {n:>4} {mn:>9.0f} {av:>9.0f} {mx:>9.0f}")
        elif "Retention" in label:
            print(f"  {label:<26} {n:>4} {mn:>9.3f} {av:>9.3f} {mx:>9.3f}")
        else:  # MB
            print(f"  {label:<26} {n:>4} {mn:>9.2f} {av:>9.2f} {mx:>9.2f}")

    # Handle/fault accounting across the whole run.
    # A real handle LEAK shows up as per-round growth that keeps happening in
    # warm rounds — not as a one-time jump in round 1 (engine/worker/thread
    # lazy init creates handles once, then reuses them). So we report the
    # round-1 init cost separately and only flag creep if WARM rounds (round 2+)
    # keep growing: either every warm round grew, or the warm cumulative net
    # exceeds a threshold. This avoids false "leak" alarms on legit first-use
    # allocation (e.g. the +39 seen on round 1, then +0 for 29 rounds).
    total_h = sum(r.h_delta for r in ok_results if r.h_delta >= 0)
    total_pf = sum(r.pf_delta for r in ok_results if r.pf_delta >= 0)
    warm = [r for r in ok_results if r.round_idx > 1 and r.h_delta >= 0]
    warm_grew = [r for r in warm if r.h_delta > 0]
    warm_net = sum(r.h_delta for r in warm)
    init_h = next((r.h_delta for r in ok_results if r.round_idx == 1 and r.h_delta >= 0), 0)
    print("-" * 78)
    print(f"  cumulative across {len(ok_results)} ok rounds: "
          f"page faults +{total_pf}  handles {'+' if total_h>=0 else ''}{total_h}"
          f"  (round 1 init: {'+' if init_h>=0 else ''}{init_h})")
    # Leak heuristics — both must be growth in WARM rounds, not round-1 init:
    #   (a) every warm round grew (monotone creep), OR
    #   (b) warm cumulative net > 20 (sustained growth across warm rounds).
    all_warm_grew = bool(warm) and len(warm_grew) == len(warm)
    if all_warm_grew or warm_net > 20:
        print(f"  ⚠ handle creep: warm rounds (2+) net {'+' if warm_net>=0 else ''}{warm_net} "
              f"({len(warm_grew)}/{len(warm)} rounds grew) — possible leak")
    elif init_h > 0 and warm_net == 0:
        print(f"  ✓ handles stable: round-1 init +{init_h} then flat — not a leak")
    print(f"{'='*78}")

    if failed:
        print_crash_dump(failed)


def save_bench_results(results, cfg):
    """Persist per-round benchmark data to stress_results/ as JSON + CSV."""
    _RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    rows = []
    for r in results:
        rows.append({
            "round": r.round_idx, "status": r.status, "seq": r.seq,
            "engine_ms": r.engine_ms, "e2e_ms": r.e2e_ms,
            "pickup_ms": r.pickup_ms, "callback_ms": r.callback_ms,
            "peak_ws_mb": r.peak_ws_mb, "peak_pv_mb": r.peak_pv_mb,
            "ws_after_mb": r.ws_after_mb, "retention": r.retention,
            "pf_delta": r.pf_delta, "h_delta": r.h_delta,
            "last_marker": r.last_marker, "detail": r.detail,
        })

    meta = {
        "bench_poll_s": cfg.bench_poll,
        "rounds": len(results),
        "note": "timing accuracy is ±bench_poll_s (log-tail detection latency); "
                "pickup_ms is -1 at INFO log level (its markers are DEBUG-only)",
    }
    json_path = _RESULTS_DIR / f"benchmark_{stamp}.json"
    json_path.write_text(json.dumps({"meta": meta, "rounds": rows}, indent=2),
                         encoding="utf-8")

    csv_path = _RESULTS_DIR / f"benchmark_{stamp}.csv"
    cols = ["round", "status", "seq", "engine_ms", "e2e_ms", "pickup_ms",
            "callback_ms", "peak_ws_mb", "peak_pv_mb", "ws_after_mb",
            "retention", "pf_delta", "h_delta", "last_marker", "detail"]

    def _csv_field(v):
        # Quote fields containing commas/quotes/newlines per RFC 4180 so the
        # free-text `detail` / `last_marker` (which embed "last marker: ...")
        # don't break column alignment.
        s = "" if v is None else str(v)
        if any(c in s for c in (",", '"', "\n", "\r")):
            return '"' + s.replace('"', '""') + '"'
        return s

    with open(csv_path, "w", encoding="utf-8") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(",".join(_csv_field(r[c]) for c in cols) + "\n")

    print(f"\n  >> benchmark saved: {json_path}")
    print(f"  >> benchmark saved: {csv_path}")
