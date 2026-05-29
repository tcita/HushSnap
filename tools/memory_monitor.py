"""
HushSnap Memory Monitor
-----------------------
Standalone process memory profiler. Finds a running HushSnap process,
or auto-launches one if none is found. No code changes needed.

Usage:
    python tools/memory_monitor.py [flags]

Flags:
    --interval N   Seconds between samples (default 1.0)
    --duration N   Stop after N seconds (0 = run until Ctrl+C)
    --csv PATH     Write samples to a CSV file for charting / analysis
    --warmup N     Max seconds to wait for launched process RSS to
                   stabilise before sampling begins (default 15)

Output columns:
    elapsed    seconds since monitoring started
    rss_mb     Working Set — physical RAM in use (MiB)
    pvt_mb     Private Bytes — Commit Size / total memory requested (MiB)
    uss_mb     Unique Set Size — private, non-shareable subset of RSS (MiB)
    handles    open Win32 handle count
    threads    thread count
    cpu_pct    process CPU utilisation across all cores (may exceed 100%)
    delta_mb   pvt_mb change since previous sample
    note       auto-annotated events (spike, release, etc.)
"""

import argparse
import csv
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    sys.exit("Missing psutil. Install with: pip install psutil")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = PROJECT_ROOT / "HushSnap.py"

PROCESS_NAMES = ("HushSnap.exe", "HushSnap_Dev.exe")
FALLBACK_NAMES = ("python.exe", "pythonw.exe")

SPIKE_THRESHOLD_MB = 50
RELEASE_THRESHOLD_MB = 40

# ── process discovery / launch ──────────────────────────────────────────

def _find_running_hushsnap() -> psutil.Process | None:
    """Scan running processes for a HushSnap instance (excludes this script)."""
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = proc.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if any(pn.lower() in name for pn in PROCESS_NAMES):
            return proc

        if any(fn.lower() in name for fn in FALLBACK_NAMES):
            full_cmd = " ".join(cmdline).lower()
            if "hushsnap" in full_cmd and "memory_monitor" not in full_cmd:
                return proc

    return None


def _wait_stable(pid: int, timeout: float) -> psutil.Process:
    """Poll until the launched process reaches a stable memory baseline.

    Returns the psutil.Process handle once RSS growth flattens out.
    """
    check_interval = 0.5
    stabilize_mb = 5.0
    deadline = time.monotonic() + timeout
    prev_rss = 0

    print(f"Waiting for process to stabilize (timeout {timeout:.0f}s):", end="", flush=True)

    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            sys.exit("\nProcess exited during startup.")

        try:
            proc = psutil.Process(pid)
            rss = proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            time.sleep(check_interval)
            continue

        delta_mb = abs(rss - prev_rss) / (1024 * 1024)
        prev_rss = rss

        if rss > 30 * 1024 * 1024 and delta_mb <= stabilize_mb:
            print(f" stable ({_fmt_mb(rss)} MiB)")
            return proc

        print(".", end="", flush=True)
        time.sleep(check_interval)

    # Timed out — use whatever state we have
    try:
        proc = psutil.Process(pid)
        rss = proc.memory_info().rss
        print(f" timed out ({_fmt_mb(rss)} MiB)")
        return proc
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        sys.exit("\nProcess did not start in time.")


# ── app log mirroring (reads child stdout, stderr merged in) ───────────

_ACTION_PATTERNS = [
    (re.compile(r"Capture completed"), lambda m: "  │ capture"),
    (re.compile(r"Engine switched: (\S+) -> (\S+)"), lambda m: f"  │ switch {m[1]} -> {m[2]}"),
    (re.compile(r"OCR Completed in ([\d.]+)s"), lambda m: f"  │ ocr done {m[1]}s"),
    (re.compile(r"OCR finished.*Text length: (\d+)"), lambda m: f"  │ ({m[1]} chars)"),
    (re.compile(r"RapidOCR engine call failed"), lambda m: "  │ ‼ rapidocr crashed"),
    (re.compile(r"OCR result is empty"), lambda m: "  │ (empty)"),
]


def _mirror_logs(stream):
    """Read stdout, echo a one-line summary for each key action."""
    for line in stream:
        text = line.decode("utf-8", errors="replace").rstrip()
        for pattern, fmt in _ACTION_PATTERNS:
            m = pattern.search(text)
            if m:
                print(fmt(m))
                break


def _launch_hushsnap(warmup: float) -> psutil.Process:
    """Launch HushSnap, wait for stable state, return psutil handle."""
    if not ENTRY_POINT.exists():
        sys.exit(f"HushSnap entry point not found at {ENTRY_POINT}")

    print(f"Launching: python {ENTRY_POINT.name}  (PID will appear below)")
    child = subprocess.Popen(
        [sys.executable, str(ENTRY_POINT)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    threading.Thread(target=_mirror_logs, args=(child.stdout,), daemon=True).start()
    pid = child.pid
    print(f"PID: {pid}")
    return _wait_stable(pid, warmup)


def _resolve_process(warmup: float) -> psutil.Process:
    """Find or launch a HushSnap process."""
    existing = _find_running_hushsnap()
    if existing is not None:
        print(f"Attached to running process (PID {existing.pid})")
        return existing

    return _launch_hushsnap(warmup)


# ── formatting ──────────────────────────────────────────────────────────

def _fmt_mb(byte_value: int) -> str:
    return f"{byte_value / (1024 * 1024):.1f}"


def _note(delta_mb: float, prev_note: str) -> str:
    if delta_mb >= SPIKE_THRESHOLD_MB:
        return "▲ SPIKE"
    if delta_mb <= -RELEASE_THRESHOLD_MB:
        return "▼ RELEASE"
    if prev_note in ("▲ SPIKE", "▼ RELEASE"):
        return ""
    return ""


def _print_header():
    header = (
        f"{'elapsed':>8s}  "
        f"{'rss_mb':>8s}  "
        f"{'pvt_mb':>8s}  "
        f"{'uss_mb':>8s}  "
        f"{'handles':>7s}  "
        f"{'threads':>7s}  "
        f"{'cpu%':>5s}  "
        f"{'delta_mb':>9s}  "
        f"{'note':s}"
    )
    print(header)
    print("-" * len(header))


# ── sampling ────────────────────────────────────────────────────────────

def _sample(proc: psutil.Process) -> dict:
    try:
        mem = proc.memory_full_info()
        return {
            "rss": mem.rss,
            "pvt": getattr(mem, "private", mem.vms),  # 'private' is Commit Size on Win
            "uss": mem.uss,
            "handles": getattr(proc, "num_handles", lambda: 0)(),
            "threads": proc.num_threads(),
            "cpu": proc.cpu_percent(),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        sys.exit("\nProcess exited. Stopping monitor.")


def _print_row(elapsed: float, snap: dict, delta_mb: float, note_str: str, csv_writer):
    row = (
        f"{elapsed:8.1f}  "
        f"{_fmt_mb(snap['rss']):>8s}  "
        f"{_fmt_mb(snap['pvt']):>8s}  "
        f"{_fmt_mb(snap['uss']):>8s}  "
        f"{snap['handles']:7d}  "
        f"{snap['threads']:7d}  "
        f"{snap['cpu']:5.1f}  "
        f"{delta_mb:+.1f}".rjust(9) + "  "
        f"{note_str:s}"
    )
    print(row)
    if csv_writer:
        csv_writer.writerow([
            f"{elapsed:.1f}",
            f"{snap['rss'] / (1024 * 1024):.1f}",
            f"{snap['pvt'] / (1024 * 1024):.1f}",
            f"{snap['uss'] / (1024 * 1024):.1f}",
            str(snap["handles"]),
            str(snap["threads"]),
            f"{snap['cpu']:.1f}",
            f"{delta_mb:.1f}",
            note_str,
        ])


def _print_summary(samples: list[dict], peaks: dict,
                   start_rss: int, end_rss: int,
                   start_pvt: int, end_pvt: int, duration: float):
    if not samples:
        return
    rss_values = [s["rss"] for s in samples]
    pvt_values = [s["pvt"] for s in samples]
    uss_values = [s["uss"] for s in samples]
    print()
    print("=" * 56)
    print("Summary")
    print("=" * 56)
    print(f"  Duration:           {duration:.0f} s")
    print(f"  Samples:            {len(samples)}")
    print(f"  RSS  start / end:   {_fmt_mb(start_rss):>6s} / {_fmt_mb(end_rss):>6s} MiB")
    print(f"  PVT  start / end:   {_fmt_mb(start_pvt):>6s} / {_fmt_mb(end_pvt):>6s} MiB (Commit)")
    print(f"  PVT  min / avg / max:  "
          f"{_fmt_mb(min(pvt_values)):>6s} / "
          f"{_fmt_mb(int(sum(pvt_values) / len(pvt_values))):>6s} / "
          f"{_fmt_mb(max(pvt_values)):>6s} MiB")
    if any(uss_values):
        print(f"  USS max:            {_fmt_mb(max(uss_values)):>6s} MiB")
    if peaks:
        print(f"  Peak PVT:           {_fmt_mb(peaks['pvt_value']):>6s} MiB  "
              f"(+{peaks['elapsed']:.1f}s)")
    net = _fmt_mb(end_pvt - start_pvt)
    sign = "+" if end_pvt >= start_pvt else ""
    print(f"  Net PVT change:     {sign}{net} MiB")


# ── main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HushSnap memory monitor")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Sampling interval in seconds (default 1.0)")
    parser.add_argument("--duration", type=float, default=0,
                        help="Stop after N seconds (0 = until Ctrl+C)")
    parser.add_argument("--csv", type=str, default="",
                        help="Write samples to CSV file")
    parser.add_argument("--warmup", type=float, default=15.0,
                        help="Max seconds to wait for launched process to stabilize (default 15)")
    args = parser.parse_args()

    proc = _resolve_process(warmup=args.warmup)

    # Warm-up cpu_percent (first call always returns 0)
    proc.cpu_percent()
    time.sleep(0.1)

    csv_file = None
    csv_writer = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([
            "elapsed_s", "rss_mb", "pvt_mb", "uss_mb",
            "handles", "threads", "cpu_pct", "delta_mb", "note",
        ])

    samples: list[dict] = []
    peaks: dict = {}
    prev_snap: dict | None = None
    prev_note = ""
    start_ts = time.monotonic()

    _print_header()

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while True:
            elapsed = time.monotonic() - start_ts
            snap = _sample(proc)

            delta_mb = 0.0
            if prev_snap:
                # Delta is now based on PVT for more accurate leak/spike detection
                delta_mb = (snap["pvt"] - prev_snap["pvt"]) / (1024 * 1024)

            note_str = _note(delta_mb, prev_note)
            prev_note = note_str

            if snap["pvt"] > peaks.get("pvt_value", 0):
                peaks = {"pvt_value": snap["pvt"], "elapsed": elapsed}

            samples.append(snap)
            _print_row(elapsed, snap, delta_mb, note_str, csv_writer)
            prev_snap = snap

            if args.duration > 0 and elapsed >= args.duration:
                break

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if csv_file:
            csv_file.close()
            print(f"\nCSV written to {args.csv}")

    duration = time.monotonic() - start_ts
    start_rss = samples[0]["rss"] if samples else 0
    end_rss = samples[-1]["rss"] if samples else 0
    start_pvt = samples[0]["pvt"] if samples else 0
    end_pvt = samples[-1]["pvt"] if samples else 0
    _print_summary(samples, peaks, start_rss, end_rss, start_pvt, end_pvt, duration)



if __name__ == "__main__":
    main()
