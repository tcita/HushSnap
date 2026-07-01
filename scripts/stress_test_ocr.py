"""Automated OCR stress-test for the MSIX-packaged HushSnap.

Drives the real app purely through synthesized keyboard/mouse input — no
in-process hooks, no auto-restart. You launch the MSIX app yourself and keep
the screen on an interface that contains text; this script then repeats:

    Alt+Q  →  left-click (full-screen capture)  →  click bottom-right
    thumbnail  →  wait for ``[OCR_CHAIN] show_text done`` in the log.

Each step is synced off the ``[OCR_CHAIN]`` log markers added across the
capture→thumbnail→OCR pipeline (see hotkey.py / capture_*.py /
ocr_controller.py / ocr_service.py / ocr_popup.py). If the process dies or a
round times out, the script stops and saves the log slice for that round so
the crash can be localized to the exact pipeline stage that halted.

Why log-marker sync instead of fixed sleeps: the rare crash under
investigation manifests as "thumbnail stuck ~2s then crash". The 2s is the
native OCR inference window. By tailing markers we (a) never click before the
thumbnail actually exists, and (b) know precisely which stage the process
was in when it died — e.g. a log that ends after
``recognize() engine call begin`` but before ``engine call end`` points at a
native onnxruntime crash, which faulthandler cannot capture and only a WER
minidump (see setup_wer_dumps.ps1) can stack-trace.

With ``--benchmark`` the script also collects per-round performance metrics
(per-stage latency, peak working set, private bytes, page faults, handles,
retention) measured entirely OUT-OF-PROCESS against the live MSIX app — no
source changes, no repackaging. See ``scripts/stress_lib/`` for the helper
modules (Win32 input, process sampler, log markers, reporting).

Usage:
    # 1. (once, as admin) enable minidump capture:
    #    powershell -ExecutionPolicy Bypass -File scripts/setup_wer_dumps.ps1
    # 2. launch the MSIX app, leave it on a screen with text
    # 3. run:
    python scripts/stress_test_ocr.py --rounds 500
    python scripts/stress_test_ocr.py --rounds 30 --benchmark

    # Hammer mode: two-click loop — Alt+Q → confirm capture → 0.5s → click
    # thumbnail [trigger OCR] → immediately Alt+Q (next cycle fires while the
    # prior OCR inference is still in flight — the overlap the serial wait-mode
    # test cannot produce):
    python scripts/stress_test_ocr.py --race --rounds 2000

    # If the auto-detected log path is wrong, pass it explicitly:
    python scripts/stress_test_ocr.py --log "C:\\path\\to\\hushsnap.log"

Only depends on the Python standard library + ctypes (no pyautogui / psutil /
pywin32) so it runs on any Windows Python.
"""

import argparse
import math
import sys
import time
from pathlib import Path

# Make `stress_lib` importable when run as a script (python scripts/...).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from stress_lib import win32_input, process_sampler, log_markers, reporting

# Re-exported names keep run_round/main readable and match the pre-refactor API.
primary_monitor_rect = win32_input.primary_monitor_rect
dismiss_popup = win32_input.dismiss_popup
move_to = win32_input.move_to
send_alt_q = win32_input.send_alt_q
click_here = win32_input.click_here
is_hushsnap_running = process_sampler.is_hushsnap_running
ExternalMemorySampler = process_sampler.ExternalMemorySampler
autodetect_log_path = log_markers.autodetect_log_path
read_new_lines = log_markers.read_new_lines
classify_marker = log_markers.classify_marker
RoundBench = log_markers.RoundBench
finalize_bench = log_markers.finalize_bench
CHAIN = log_markers.CHAIN
SUCCESS_MARKER = log_markers.SUCCESS_MARKER
save_round_log = reporting.save_round_log
print_round_bench = reporting.print_round_bench
print_bench_report = reporting.print_bench_report
save_bench_results = reporting.save_bench_results


# ── the test loop ─────────────────────────────────────────────────────────────

def run_round(round_idx, log_path, start_offset, cfg, sampler=None):
    """Execute one capture→OCR round.

    Returns (status, end_offset, detail, round_bench). status ∈ {"ok",
    "crash", "hang"}. round_bench is a RoundBench (populated only when
    cfg.benchmark and a sampler is supplied; otherwise a bare shell with just
    status).

    Thumbnail location is deterministic — ui/thumbnail.py:185-186 places the
    window at the bottom-right of the screen the cursor was on when capture
    ended, so we compute its center from the constants rather than enumerating
    windows (EnumWindows matching on a frameless Qt tool window with no title
    was fragile across multi-monitor/DPR setups). The capture click lands at
    the primary screen center, so the thumbnail lands at the primary screen's
    bottom-right; its center is the screen's bottom-right corner inset by
    (140, 95) physical px:
        display_width = card_width + 2*shadow_padding = 240 + 24 = 264
        display_height = card_height + 2*shadow_padding = 150 + 24 = 174
        end_x = screen.right - 264 - MARGIN(20) + shadow_padding(12) = right-272
        end_y = screen.bottom - 174 - 20 + 12 = bottom-182
        center = (end_x + 132, end_y + 87) = (right-140, bottom-95)
    """
    primary = primary_monitor_rect()

    # Benchmark: snapshot baseline + peak trackers BEFORE the round starts.
    # peak_* are updated on every poll so the OCR window's high-water mark is
    # captured even if we crash before re-sampling.
    bench = bool(cfg.benchmark and sampler is not None)
    rb = RoundBench(round_idx, "ok")
    if bench:
        ws0, pv0, h0, pf0 = sampler.snapshot()
        peak_ws, peak_pv = ws0, pv0
    else:
        ws0 = pv0 = h0 = pf0 = -1
        peak_ws = peak_pv = -1.0

    # 0. Dismiss any OCR popup left over from the previous round so it is NOT
    #    captured into this round's screenshot (the popup auto-hides on losing
    #    focus; clicking empty desktop steals focus). Keeps each round's OCR
    #    input identical to the static text screen the user prepared.
    dismiss_popup(primary)

    # 1. Resolve the capture-click point. Default to the primary screen center
    #    (where the user is expected to keep text); --capture-point overrides
    #    for multi-monitor setups where the text is on a secondary screen.
    if cfg.capture_point:
        cap_x, cap_y = cfg.capture_point
    else:
        cap_x = primary.left + (primary.right - primary.left) // 2
        cap_y = primary.top + (primary.bottom - primary.top) // 2
    move_to(cap_x, cap_y)
    time.sleep(0.2)
    print(f"  [round {round_idx}] Alt+Q (capture click at {cap_x},{cap_y})")
    send_alt_q()

    # 2. Wait for the capture overlay to come up, then clean-click for a
    #    full-screen capture. A fixed delay is fine here — the overlay is
    #    fullscreen and the click just needs to land after it appears.
    time.sleep(cfg.overlay_delay)
    move_to(cap_x, cap_y)
    click_here()

    # 3. Wait for the thumbnail to appear, then click its center. The slide-in
    #    animation is 300ms; 1s is enough for capture→callback→show to settle.
    #    Crash during this window is caught below by the liveness check.
    thumb_x = primary.right - 140
    thumb_y = primary.bottom - 95
    time.sleep(cfg.thumbnail_delay)
    if not is_hushsnap_running():
        rb.status = "crash"
        return "crash", start_offset, "process died before thumbnail click", rb
    print(f"  [round {round_idx}] click thumbnail at {thumb_x},{thumb_y}")
    move_to(thumb_x, thumb_y)
    time.sleep(0.15)
    click_here()

    # 4. Wait for the OCR result: tail the log until show_text done / crash /
    #    timeout. The [OCR_CHAIN] markers are stamped as they appear so
    #    per-stage timing can be computed on resolve; liveness is checked each
    #    poll so a crash is caught and the last marker (the stage that halted)
    #    is recorded.
    offset = start_offset
    last_markers = []
    stamps = {}          # stage_key -> perf_counter() at first observation
    # Benchmark mode tightens the tail poll so marker-detection latency (which
    # bounds every segment's accuracy) stays well below the segments we measure.
    poll = cfg.bench_poll if cfg.benchmark else 0.15

    def _stamp(msg):
        if not bench:
            return
        key, seq = classify_marker(msg)
        if key and key not in stamps:
            stamps[key] = time.perf_counter()
            if seq is not None and rb.seq is None:
                rb.seq = seq

    def _sample_peak():
        nonlocal peak_ws, peak_pv
        if not bench:
            return
        ws, pv, _, _ = sampler.snapshot()
        if ws > peak_ws:
            peak_ws = ws
        if pv > peak_pv:
            peak_pv = pv

    deadline = time.monotonic() + cfg.ocr_timeout
    while time.monotonic() < deadline:
        text, offset = read_new_lines(log_path, offset)
        if text:
            for line in text.splitlines():
                if CHAIN in line:
                    # keep just the marker portion for a compact timeline
                    m = line.split(CHAIN, 1)[1].strip()
                    last_markers.append(m)
                    print(f"  [round {round_idx}] log: {m}")
                    _stamp(m)
            if SUCCESS_MARKER in text:
                if bench:
                    finalize_bench(rb, stamps, sampler, ws0, pv0, h0, pf0, peak_ws, peak_pv)
                dismiss_popup(primary)  # clean the screen for the next round
                rb.status = "ok"
                return "ok", offset, "show_text done", rb
        _sample_peak()
        if not is_hushsnap_running():
            if bench:
                finalize_bench(rb, stamps, sampler, ws0, pv0, h0, pf0, peak_ws, peak_pv)
            rb.status = "crash"
            return "crash", offset, "process died during OCR; last marker: " + (last_markers[-1] if last_markers else "(none)"), rb
        time.sleep(poll)

    # Timed out without success marker.
    if bench:
        finalize_bench(rb, stamps, sampler, ws0, pv0, h0, pf0, peak_ws, peak_pv)
    if not is_hushsnap_running():
        rb.status = "crash"
        return "crash", offset, "process died after timeout; last marker: " + (last_markers[-1] if last_markers else "(none)"), rb
    rb.status = "hang"
    return "hang", offset, "OCR did not complete; last marker: " + (last_markers[-1] if last_markers else "(none)"), rb


# ── race mode ────────────────────────────────────────────────────────────────

def run_race_loop(args, log_path, cfg):
    """Two-click race that re-enters capture WHILE the prior OCR is in flight.

    Each cycle::

        Alt+Q  →  (overlay_delay)  →  left-click center   [confirm full-screen capture]
                →  (race_delay)    →  left-click bottom-right thumbnail  [trigger OCR]
                →  immediately Alt+Q                                   [next cycle, OCR still running]

    The two clicks are required: the first confirms the capture on the overlay
    (which is fullscreen-topmost, so a single click on bottom-right would only
    ever hit the overlay, never the thumbnail — OCR could never fire). After the
    confirm click the thumbnail appears at the primary screen's bottom-right;
    race_delay (default 0.5s) is the wait for it to come up. The second click
    lands on the thumbnail and kicks off OCR.

    The defining behavior: after the thumbnail click we do NOT wait for the OCR
    callback. The next Alt+Q fires immediately, so grab_all_screens() + overlay
    show re-enter capture WHILE the prior cycle's OCR inference (~2-4s) is still
    running in the worker thread. This is what makes race DIFFERENT from the
    normal wait-mode stress test (which serializes every cycle behind
    ``show_text done`` + cooldown): it creates the overlap — capture/overlay/grab
    running concurrently with in-flight OCR — that the serial test cannot. That
    overlap is the edge case this mode exists to cover.

    Consequence of not waiting: OcrService is a single-slot overwrite, NOT a
    queue, so the next cycle's capture→thumbnail→OCR will usually supersede the
    prior cycle's in-flight request (``worker result superseded``) and DROP its
    callback before on_ocr_finished runs. That is expected here — the race being
    exercised is on the capture/overlay/inference side, not the callback side.

    We do NOT dismiss the OCR popup between cycles (the next Alt+Q's overlay
    covers it). Crash detection is by process liveness, checked at the top of
    each cycle and right before the thumbnail click — a crash from a cycle's
    in-flight OCR typically surfaces a cycle or two later, once the inference
    window elapses. We passively tail the log so the last [OCR_CHAIN] marker
    seen before death is reported and the log slice is saved, localizing the
    crash to the pipeline stage that halted.
    """
    primary = primary_monitor_rect()
    thumb_x = primary.right - 140
    thumb_y = primary.bottom - 95
    cap_x = primary.left + (primary.right - primary.left) // 2
    cap_y = primary.top + (primary.bottom - primary.top) // 2
    if cfg.capture_point:
        cap_x, cap_y = cfg.capture_point

    print(f"RACE MODE (two-click, re-enter mid-OCR): per cycle")
    print(f"  Alt+Q → {cfg.overlay_delay}s → click center ({cap_x},{cap_y}) [confirm capture]")
    print(f"        → {cfg.race_delay}s → click thumbnail ({thumb_x},{thumb_y}) [trigger OCR]")
    print(f"        → immediately Alt+Q [next cycle, prior OCR still in flight]")
    print(f"  x{args.rounds} (or until crash). Does NOT wait for OCR — re-enters capture mid-inference.")
    if args.no_stop_on_fail:
        print("--no-stop-on-fail: keep cycling after a crash is detected (restart the app yourself).")
    print()

    ok = 0
    fail = 0
    try:
        offset = log_path.stat().st_size if log_path.exists() else 0
    except OSError:
        offset = 0
    last_marker = ""

    def _drain_log():
        nonlocal offset, last_marker
        try:
            text, offset = read_new_lines(log_path, offset)
        except Exception:
            return
        if not text:
            return
        for line in text.splitlines():
            if CHAIN in line:
                last_marker = line.split(CHAIN, 1)[1].strip()

    try:
        for i in range(1, args.rounds + 1):
            # Liveness check at the top — this is where a deferred crash from an
            # earlier cycle's in-flight OCR is caught.
            if not is_hushsnap_running():
                fail += 1
                detail = f"process died before cycle start; last marker: {last_marker or '(none)'}"
                print(f"\n  [cycle {i}] CRASH — {detail}")
                save_round_log(log_path, i, "crash", detail)
                print(f"  cumulative: ok={ok}  fail={fail}\n")
                if not args.no_stop_on_fail:
                    print("Stopping. Restart the MSIX app and re-run to continue.")
                    return ok, fail
                print("(--no-stop-on-fail: waiting 2s — restart the app to resume racing.)")
                time.sleep(2)
                continue

            # 1. Alt+Q: grab_all_screens() + show capture overlay.
            send_alt_q()
            # 2. Wait for the overlay, then left-click the center to confirm a
            #    full-screen capture (a click < CAPTURE_CLICK_THRESHOLD_PX between
            #    down/up). This closes the overlay and spawns the thumbnail.
            time.sleep(cfg.overlay_delay)
            move_to(cap_x, cap_y)
            time.sleep(0.1)
            click_here()
            # 3. Wait for the thumbnail to slide in (300ms anim + show settle),
            #    then click its center to trigger OCR on it.
            time.sleep(cfg.race_delay)
            if not is_hushsnap_running():
                _drain_log()
                fail += 1
                detail = f"process died before thumbnail click; last marker: {last_marker or '(none)'}"
                print(f"\n  [cycle {i}] CRASH — {detail}")
                save_round_log(log_path, i, "crash", detail)
                print(f"  cumulative: ok={ok}  fail={fail}\n")
                if not args.no_stop_on_fail:
                    print("Stopping. Restart the MSIX app and re-run to continue.")
                    return ok, fail
                time.sleep(2)
                continue
            move_to(thumb_x, thumb_y)
            time.sleep(0.05)
            click_here()
            # 4. Immediately re-trigger: the next Alt+Q fires WITHOUT waiting
            #    for this cycle's OCR to return. grab_all_screens() + overlay
            #    re-enter while the prior inference is still in the worker
            #    thread — the overlap that distinguishes race from the serial
            #    wait-mode test. Just drain the log + tick the counter; no wait,
            #    no cooldown.
            _drain_log()
            ok += 1
            if i % 10 == 0 or i <= 3:
                print(f"  [cycle {i}] alive  ok={ok} fail={fail}  last_marker: {last_marker or '(none)'}")
            # Immediately re-trigger: the next iteration's Alt+Q fires right
            # after the thumbnail click, overlapping the next capture onto the
            # prior cycle's in-flight OCR. No cooldown.
    except KeyboardInterrupt:
        print("\ninterrupted by user.")

    print(f"\n=== race done: ok={ok}  fail={fail} ===")
    return ok, fail


def main():
    ap = argparse.ArgumentParser(description="OCR stress-test for MSIX HushSnap (keyboard/mouse only).")
    ap.add_argument("--rounds", type=int, default=500, help="max rounds to run (default 500)")
    ap.add_argument("--log", type=str, default=None, help="path to hushsnap.log (auto-detected if omitted)")
    ap.add_argument("--capture-point", type=str, default=None,
                    help='"x,y" physical pixel for the full-screen capture click (default: primary screen center). '
                         'Note: the thumbnail is always clicked at the PRIMARY screen bottom-right, '
                         'so keep the capture on the primary screen unless you also move the thumbnail click.')
    ap.add_argument("--overlay-delay", type=float, default=0.8,
                    help="seconds to wait for the capture overlay after Alt+Q (default 0.8)")
    ap.add_argument("--thumbnail-delay", type=float, default=1.0,
                    help="seconds to wait after the capture click before clicking the thumbnail (default 1.0)")
    ap.add_argument("--ocr-timeout", type=float, default=20.0,
                    help="seconds to wait for OCR to complete per round in wait mode (default 20.0)")
    ap.add_argument("--cooldown", type=float, default=1.5,
                    help="seconds between rounds (default 1.5)")
    ap.add_argument("--no-stop-on-fail", action="store_true",
                    help="keep running after a crash/hang instead of stopping (you must restart the app manually)")
    ap.add_argument("--benchmark", action="store_true",
                    help="collect per-round performance metrics (latency stages, peak working set, "
                         "private bytes, page faults, handles, retention) measured OUT-OF-PROCESS against "
                         "the live MSIX app. Uses the existing [OCR_CHAIN] log markers + Win32 memory "
                         "queries — zero source changes, no repackaging. Saves benchmark_<stamp>.json + "
                         ".csv to stress_results/.")
    ap.add_argument("--bench-poll", type=float, default=0.05,
                    help="log-tail poll interval in seconds when --benchmark is on (default 0.05). Bounds "
                         "the marker-detection latency that limits per-stage timing accuracy (±this value).")
    ap.add_argument("--prep", type=float, default=3.0,
                    help="seconds to wait before round 1 begins (default 3.0). Gives you time to minimize "
                         "this terminal — the capture is fullscreen, so a console left over the text region "
                         "would be OCR'd into round 1's screenshot. Set 0 to skip.")
    ap.add_argument("--race", action="store_true",
                    help="RACE MODE (two-click, re-enter mid-OCR): per cycle — Alt+Q → (overlay-delay) → "
                         "click center [confirm capture] → (race-delay) → click bottom-right thumbnail "
                         "[trigger OCR] → immediately Alt+Q [next cycle, prior OCR still in flight]. Two "
                         "clicks are required: a single click could only ever hit the fullscreen overlay and "
                         "never the thumbnail, so OCR would never fire. The defining behavior: it does NOT "
                         "wait for OCR to return before re-entering, so grab_all_screens() + overlay re-enter "
                         "capture WHILE the prior cycle's OCR inference is still running — the overlap that "
                         "distinguishes race from the serial wait-mode stress test (which waits for "
                         "show_text done + cooldown each cycle). Expected side effect: the next cycle usually "
                         "supersedes the prior in-flight OCR (OcrService is single-slot, not a queue) and "
                         "drops its callback; that's fine — the overlap here is on the capture/overlay/"
                         "inference side, not the callback side. Does NOT dismiss the popup. Crash detection "
                         "is by process liveness; the last [OCR_CHAIN] marker before death is reported + "
                         "saved. Incompatible with --benchmark.")
    ap.add_argument("--race-delay", type=float, default=0.5,
                    help="seconds between the capture-confirm click and the thumbnail click in race mode "
                         "(default 0.5) — the wait for the thumbnail to slide in after capture. Lower it to "
                         "tighten the overlap (too low and the thumbnail won't be up yet when you click).")
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else autodetect_log_path()
    if log_path is None or not log_path.exists():
        print("ERROR: could not find hushsnap.log. Pass it explicitly with --log.")
        print("       (MSIX stores it under %LOCALAPPDATA%\\Packages\\<PFN>\\LocalState\\)")
        return 2
    print(f"log file: {log_path}")

    class Cfg:
        pass
    cfg = Cfg()
    cfg.overlay_delay = args.overlay_delay
    cfg.thumbnail_delay = args.thumbnail_delay
    cfg.ocr_timeout = args.ocr_timeout
    cfg.capture_point = None
    cfg.benchmark = args.benchmark
    cfg.bench_poll = args.bench_poll
    cfg.race_delay = args.race_delay

    if not args.race:
        print(f"WAIT MODE: rounds={args.rounds}  overlay_delay={args.overlay_delay}s  "
              f"thumbnail_delay={args.thumbnail_delay}s  ocr_timeout={args.ocr_timeout}s  "
              f"cooldown={args.cooldown}s")
    print("make sure the MSIX app is running and the screen shows text.")
    if args.benchmark:
        print(f"BENCHMARK: poll={cfg.bench_poll}s (±{cfg.bench_poll*1000:.0f}ms timing accuracy), "
              "measuring out-of-process against HushSnap.exe.")
    print()
    if args.capture_point:
        try:
            parts = args.capture_point.split(",")
            cfg.capture_point = (int(parts[0]), int(parts[1]))
        except Exception:
            print(f"ERROR: bad --capture-point {args.capture_point!r}; expected 'x,y'")
            return 2

    # ── Prep countdown ──────────────────────────────────────────────────────
    # The capture is FULLSCREEN, so any window covering the text region —
    # including this console — gets OCR'd into round 1. Give the operator a
    # moment to minimize the terminal before the first Alt+Q fires.
    prep = max(0.0, args.prep)
    if prep > 0:
        print(f"starting in {math.ceil(prep)}s — MINIMIZE THIS TERMINAL NOW "
              f"(fullscreen capture would include it).")
        remaining = prep
        while remaining > 0:
            tick = min(1.0, remaining)
            print(f"  {math.ceil(remaining)}...", flush=True)
            time.sleep(tick)
            remaining -= tick
        print("  go")

    # Race mode: a tight loop that re-enters capture mid-OCR. It is
    # incompatible with --benchmark (no per-round success marker to time
    # against), so honor --race first and return before the paced loop.
    if args.race:
        if args.benchmark:
            print("NOTE: --benchmark is ignored in --race mode (no per-round OCR success to time).")
        ok, fail = run_race_loop(args, log_path, cfg)
        print(f"\n=== done: ok={ok}  fail={fail} ===")
        return 0

    ok = 0
    fail = 0
    sampler = ExternalMemorySampler() if args.benchmark else None
    bench_results: list[RoundBench] = []
    try:
        for i in range(1, args.rounds + 1):
            if not is_hushsnap_running():
                print(f"\n[round {i}] HushSnap is not running. Start it and re-run, or it crashed earlier.")
                if not args.no_stop_on_fail:
                    break
                time.sleep(2)
                continue

            # Snapshot the log offset so we only tail this round's lines.
            try:
                start_offset = log_path.stat().st_size
            except OSError:
                start_offset = 0

            t0 = time.monotonic()
            status, _end_offset, detail, rb = run_round(i, log_path, start_offset, cfg, sampler)
            dt = time.monotonic() - t0
            # Fold the outcome reason into the bench record so a crash's cause
            # (last [OCR_CHAIN] marker = the stage that halted) travels with
            # its measurements in benchmark_*.json/csv — no need to cross-
            # reference round_*.log by hand.
            rb.detail = detail
            rb.last_marker = detail.rsplit("last marker:", 1)[-1].strip() if "last marker:" in detail else ""
            bench_results.append(rb)

            if status == "ok":
                ok += 1
                if args.benchmark:
                    print_round_bench(rb, dt)
                else:
                    print(f"  [round {i}] OK ({dt:.2f}s)\n")
            else:
                fail += 1
                print(f"\n  [round {i}] {status.upper()} after {dt:.2f}s — {detail}")
                if args.benchmark:
                    print_round_bench(rb, dt)
                save_round_log(log_path, i, status, detail)
                print(f"  cumulative: ok={ok}  fail={fail}\n")
                if not args.no_stop_on_fail:
                    print("Stopping. Restart the MSIX app and re-run to continue.")
                    break
            time.sleep(args.cooldown)
    finally:
        if sampler is not None:
            sampler.close()

    print(f"\n=== done: ok={ok}  fail={fail} ===")
    if args.benchmark and bench_results:
        print_bench_report(bench_results, cfg)
        save_bench_results(bench_results, cfg)


if __name__ == "__main__":
    sys.exit(main() or 0)
