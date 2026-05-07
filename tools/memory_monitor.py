"""
HushSnap Memory Monitor
-----------------------
Standalone process memory profiler. Attaches to a running HushSnap process
and samples memory usage on a fixed interval. No code changes needed.

Usage:
    # Monitor packaged HushSnap
    python tools/memory_monitor.py

    # Monitor Python dev process by PID
    python tools/memory_monitor.py --pid 12345

    # Custom interval (seconds) and duration
    python tools/memory_monitor.py --interval 2 --duration 300

    # Output CSV for later analysis
    python tools/memory_monitor.py --csv memory_log.csv

    # Quiet mode (no per-sample print, only summary)
    python tools/memory_monitor.py --quiet

Output columns:
    elapsed    seconds since monitoring started
    rss_mb     Resident Set Size / Working Set (MiB)
    private_mb Private Bytes — memory not shareable with other processes (MiB)
    vms_mb     Virtual Memory Size (MiB)
    handles    open Win32 handle count
    threads    thread count
    cpu_pct     process CPU utilisation (sampling window, may be delayed)
    delta_mb   rss_mb change since previous sample
    note        auto-annotated events (spike, release, etc.)
"""

import argparse
import csv
import logging
import os
import signal
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    sys.exit("Missing psutil. Install with: pip install psutil")

logger = logging.getLogger("memory_monitor")
logging.basicConfig(level=logging.INFO, format="%(message)s")

PROCESS_NAMES = ("HushSnap.exe", "HushSnap_Dev.exe")
FALLBACK_NAMES = ("python.exe", "pythonw.exe")

SPIKE_THRESHOLD_MB = 80   # rss jump >= this → flagged as spike
RELEASE_THRESHOLD_MB = 60  # rss drop >= this → flagged as release
QUIET_MODE = False


def _find_hushsnap_pid(target_pid: int | None = None) -> int | None:
    """Find a running HushSnap process by name or explicit PID."""
    if target_pid is not None:
        if psutil.pid_exists(target_pid):
            return target_pid
        sys.exit(f"PID {target_pid} not found.")

    candidates = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            exe = (proc.info["exe"] or "").lower()
            cmdline = proc.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        # Packaged builds
        if any(pn.lower() in name for pn in PROCESS_NAMES):
            candidates.append((proc.info["pid"], name))
            continue

        # Dev runs: python.exe whose command line mentions hushsnap
        if any(fn.lower() in name for fn in FALLBACK_NAMES):
            full_cmd = " ".join(cmdline).lower()
            if "hushsnap" in full_cmd and "memory_monitor" not in full_cmd:
                candidates.append((proc.info["pid"], "python (dev)"))
                continue

    if len(candidates) == 0:
        sys.exit(
            "No HushSnap process found. "
            "Launch the app first or pass --pid explicitly."
        )
    if len(candidates) == 1:
        pid, label = candidates[0]
        print(f"Attached to {label} (PID {pid})")
        return pid

    print("Multiple candidates found:")
    for i, (pid, label) in enumerate(candidates, 1):
        print(f"  [{i}] {label}  PID={pid}")
    choice = input("Choose [1]: ").strip()
    idx = int(choice or 1) - 1
    pid, label = candidates[idx]
    print(f"Attached to {label} (PID {pid})")
    return pid


def _fmt_mb(byte_value: int) -> str:
    return f"{byte_value / (1024 * 1024):.1f}"


def _note(delta_mb: float, prev_note: str) -> str:
    """Annotate large memory swings."""
    if delta_mb >= SPIKE_THRESHOLD_MB:
        return "▲ SPIKE"
    if delta_mb <= -RELEASE_THRESHOLD_MB:
        return "▼ RELEASE"
    if prev_note in ("▲ SPIKE", "▼ RELEASE"):
        return ""  # back to steady
    return ""


def _print_header():
    header = (
        f"{'elapsed':>8s}  "
        f"{'rss_mb':>8s}  "
        f"{'private_mb':>10s}  "
        f"{'vms_mb':>8s}  "
        f"{'handles':>7s}  "
        f"{'threads':>7s}  "
        f"{'cpu%':>5s}  "
        f"{'delta_mb':>9s}  "
        f"{'note':s}"
    )
    print(header)
    print("-" * len(header))


def _sample(proc: psutil.Process) -> dict:
    """Collect one memory snapshot. Failures become zeroes so the loop keeps running."""
    try:
        mem = proc.memory_info()
        rss = mem.rss  # working set on Windows
        vms = getattr(mem, "vms", 0)
        private = getattr(mem, "private", 0)  # Windows specific; 0 on other platforms
        handles = getattr(proc, "num_handles", lambda: 0)()
        threads = proc.num_threads()
        cpu = proc.cpu_percent()
        return {
            "rss": rss,
            "private": private,
            "vms": vms,
            "handles": handles,
            "threads": threads,
            "cpu": cpu,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        sys.exit("\nProcess exited. Stopping monitor.")


def _print_row(elapsed: float, snap: dict, delta_mb: float, note_str: str, csv_writer):
    row = (
        f"{elapsed:8.1f}  "
        f"{_fmt_mb(snap['rss']):>8s}  "
        f"{_fmt_mb(snap['private']):>10s}  "
        f"{_fmt_mb(snap['vms']):>8s}  "
        f"{snap['handles']:7d}  "
        f"{snap['threads']:7d}  "
        f"{snap['cpu']:5.1f}  "
        f"{delta_mb:+.1f}".rjust(9) + "  "
        f"{note_str:s}"
    )
    if not QUIET_MODE:
        print(row)
    if csv_writer:
        csv_writer.writerow([
            f"{elapsed:.1f}",
            f"{snap['rss'] / (1024*1024):.1f}",
            f"{snap['private'] / (1024*1024):.1f}",
            f"{snap['vms'] / (1024*1024):.1f}",
            str(snap["handles"]),
            str(snap["threads"]),
            f"{snap['cpu']:.1f}",
            f"{delta_mb:.1f}",
            note_str,
        ])


def _print_summary(
    samples: list[dict],
    peaks: dict,
    start_rss: int,
    end_rss: int,
    duration: float,
):
    if not samples:
        return
    rss_values = [s["rss"] for s in samples]
    private_values = [s["private"] for s in samples]
    print()
    print("=" * 56)
    print("Summary")
    print("=" * 56)
    print(f"  Duration:           {duration:.0f} s")
    print(f"  Samples:            {len(samples)}")
    print(f"  RSS  start / end:   {_fmt_mb(start_rss):>6s} / {_fmt_mb(end_rss):>6s} MiB")
    print(f"  RSS  min / avg / max:  "
          f"{_fmt_mb(min(rss_values)):>6s} / "
          f"{_fmt_mb(int(sum(rss_values) / len(rss_values))):>6s} / "
          f"{_fmt_mb(max(rss_values)):>6s} MiB")
    if any(private_values):
        print(f"  Private max:        {_fmt_mb(max(private_values)):>6s} MiB")
    if peaks:
        print(f"  Peak RSS:           {_fmt_mb(peaks['rss_value']):>6s} MiB  "
              f"(+{peaks['elapsed']:.1f}s)")
    net = _fmt_mb(end_rss - start_rss)
    sign = "+" if end_rss >= start_rss else ""
    print(f"  Net change:         {sign}{net} MiB")


def main():
    global QUIET_MODE

    parser = argparse.ArgumentParser(description="HushSnap memory monitor")
    parser.add_argument("--pid", type=int, default=None, help="Target process PID")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Sampling interval in seconds (default 1.0)")
    parser.add_argument("--duration", type=float, default=0,
                        help="Stop after N seconds (0 = until Ctrl+C)")
    parser.add_argument("--csv", type=str, default="",
                        help="Write samples to CSV file")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-sample output, print summary only")
    args = parser.parse_args()

    QUIET_MODE = args.quiet

    target_pid = _find_hushsnap_pid(args.pid)
    proc = psutil.Process(target_pid)

    # Warm-up cpu_percent (first call always returns 0)
    proc.cpu_percent()
    time.sleep(0.1)

    csv_writer = None
    csv_file = None
    if args.csv:
        csv_path = Path(args.csv)
        csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([
            "elapsed_s", "rss_mb", "private_mb", "vms_mb",
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
                delta_mb = (snap["rss"] - prev_snap["rss"]) / (1024 * 1024)

            note_str = _note(delta_mb, prev_note)
            prev_note = note_str

            # Track peak RSS
            if snap["rss"] > peaks.get("rss_value", 0):
                peaks = {"rss_value": snap["rss"], "elapsed": elapsed}

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
            if not QUIET_MODE:
                print(f"\nCSV written to {args.csv}")

    duration = time.monotonic() - start_ts
    start_rss = samples[0]["rss"] if samples else 0
    end_rss = samples[-1]["rss"] if samples else 0
    _print_summary(samples, peaks, start_rss, end_rss, duration)


if __name__ == "__main__":
    main()
