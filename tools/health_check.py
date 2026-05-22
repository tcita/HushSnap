"""
HushSnap health check.

Records process health metrics for a running packaged HushSnap instance and
prints a compact report. The script only observes; the developer decides what
manual operations to perform during the run.

Usage:
    python tools/health_check.py
    python tools/health_check.py --duration 900 --checkpoint
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.wintypes
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:
    sys.exit("Missing psutil. Install with: pip install psutil")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "tools" / "health_reports"
PROCESS_NAMES = ("HushSnap.exe", "HushSnap_Dev.exe")
FALLBACK_NAMES = ("python.exe", "pythonw.exe")


@dataclass
class Sample:
    phase: str
    elapsed_s: float
    total_elapsed_s: float
    rss_mb: float
    uss_mb: float
    handles: int
    threads: int
    cpu_pct: float
    delta_mb: float
    gdi_objects: int | None
    user_objects: int | None


def _fmt_mb(value: float) -> str:
    return f"{value:.1f} MB"


def _find_running_hushsnap() -> psutil.Process | None:
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = proc.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if any(process_name.lower() in name for process_name in PROCESS_NAMES):
            return proc

        if any(fallback.lower() in name for fallback in FALLBACK_NAMES):
            full_cmd = " ".join(cmdline).lower()
            if "hushsnap" in full_cmd and "health_check" not in full_cmd:
                return proc

    return None


def _gui_resource_counts(pid: int) -> tuple[int | None, int | None]:
    if sys.platform != "win32":
        return None, None

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010

        kernel32.OpenProcess.argtypes = [
            ctypes.wintypes.DWORD,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        user32.GetGuiResources.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
        user32.GetGuiResources.restype = ctypes.wintypes.DWORD

        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            return None, None
        try:
            gdi = int(user32.GetGuiResources(handle, 0))
            user = int(user32.GetGuiResources(handle, 1))
            return gdi, user
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None, None


def _sample_process(proc: psutil.Process, phase: str, phase_start: float, total_start: float,
                    previous_rss_mb: float | None) -> Sample:
    try:
        mem = proc.memory_full_info()
        rss_mb = mem.rss / (1024 * 1024)
        uss_mb = getattr(mem, "uss", 0) / (1024 * 1024)
        handles = getattr(proc, "num_handles", lambda: 0)()
        threads = proc.num_threads()
        cpu = proc.cpu_percent()
        gdi, user = _gui_resource_counts(proc.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        sys.exit("\nHushSnap process exited or became inaccessible.")

    delta_mb = 0.0 if previous_rss_mb is None else rss_mb - previous_rss_mb
    now = time.monotonic()
    return Sample(
        phase=phase,
        elapsed_s=now - phase_start,
        total_elapsed_s=now - total_start,
        rss_mb=rss_mb,
        uss_mb=uss_mb,
        handles=handles,
        threads=threads,
        cpu_pct=cpu,
        delta_mb=delta_mb,
        gdi_objects=gdi,
        user_objects=user,
    )


def _print_sample(sample: Sample) -> None:
    gui = ""
    if sample.gdi_objects is not None and sample.user_objects is not None:
        gui = f" gdi={sample.gdi_objects:4d} user={sample.user_objects:4d}"
    print(
        f"{sample.phase:<18} "
        f"{sample.elapsed_s:7.1f}s "
        f"rss={sample.rss_mb:7.1f} "
        f"uss={sample.uss_mb:7.1f} "
        f"handles={sample.handles:5d} "
        f"threads={sample.threads:3d} "
        f"cpu={sample.cpu_pct:6.1f} "
        f"d={sample.delta_mb:+6.1f}"
        f"{gui}"
    )


def _collect_for_duration(proc: psutil.Process, phase: str, duration: float, interval: float,
                          total_start: float) -> list[Sample]:
    print(f"\n[{phase}] Sampling for {duration:.0f}s.")
    phase_start = time.monotonic()
    samples: list[Sample] = []
    previous_rss_mb: float | None = None

    proc.cpu_percent()
    while True:
        sample = _sample_process(proc, phase, phase_start, total_start, previous_rss_mb)
        samples.append(sample)
        previous_rss_mb = sample.rss_mb
        _print_sample(sample)

        if duration > 0 and sample.elapsed_s >= duration:
            return samples
        time.sleep(interval)


def _collect_until_enter(proc: psutil.Process, phase: str, prompt: str, interval: float,
                         total_start: float) -> list[Sample]:
    print(f"\n[{phase}] {prompt}")
    print("Sampling is active. Press Enter here when the action is complete.")

    stop_event = threading.Event()
    samples: list[Sample] = []
    phase_start = time.monotonic()
    lock = threading.Lock()

    def sampler() -> None:
        previous_rss_mb: float | None = None
        proc.cpu_percent()
        while not stop_event.is_set():
            sample = _sample_process(proc, phase, phase_start, total_start, previous_rss_mb)
            previous_rss_mb = sample.rss_mb
            with lock:
                samples.append(sample)
            _print_sample(sample)
            stop_event.wait(interval)

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    input()
    stop_event.set()
    thread.join(timeout=interval + 1)
    with lock:
        return list(samples)


def _write_csv(samples: list[Sample], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            "event",
            "phase",
            "elapsed_s",
            "total_elapsed_s",
            "rss_mb",
            "uss_mb",
            "handles",
            "threads",
            "cpu_pct",
            "delta_mb",
            "gdi_objects",
            "user_objects",
        ])
        for sample in samples:
            is_event = sample.phase.startswith("checkpoint_")
            writer.writerow([
                sample.phase if is_event else "",
                sample.phase,
                "" if is_event else f"{sample.elapsed_s:.1f}",
                f"{sample.total_elapsed_s:.1f}",
                "" if is_event else f"{sample.rss_mb:.1f}",
                "" if is_event else f"{sample.uss_mb:.1f}",
                "" if is_event else sample.handles,
                "" if is_event else sample.threads,
                "" if is_event else f"{sample.cpu_pct:.1f}",
                "" if is_event else f"{sample.delta_mb:.1f}",
                "" if is_event or sample.gdi_objects is None else sample.gdi_objects,
                "" if is_event or sample.user_objects is None else sample.user_objects,
            ])


def _phase_summary(samples: list[Sample]) -> dict[str, float | int | str]:
    if not samples:
        return {
            "phase": "",
            "count": 0,
            "rss_start": 0.0,
            "rss_end": 0.0,
            "rss_max": 0.0,
            "uss_start": 0.0,
            "uss_end": 0.0,
            "uss_max": 0.0,
            "handles_start": 0,
            "handles_end": 0,
            "handles_max": 0,
            "threads_start": 0,
            "threads_end": 0,
            "threads_max": 0,
            "gdi_start": 0,
            "gdi_end": 0,
            "user_start": 0,
            "user_end": 0,
        }

    gdi_values = [s.gdi_objects for s in samples if s.gdi_objects is not None]
    user_values = [s.user_objects for s in samples if s.user_objects is not None]
    return {
        "phase": samples[0].phase,
        "count": len(samples),
        "rss_start": samples[0].rss_mb,
        "rss_end": samples[-1].rss_mb,
        "rss_max": max(s.rss_mb for s in samples),
        "rss_avg": statistics.fmean(s.rss_mb for s in samples),
        "uss_start": samples[0].uss_mb,
        "uss_end": samples[-1].uss_mb,
        "uss_max": max(s.uss_mb for s in samples),
        "handles_start": samples[0].handles,
        "handles_end": samples[-1].handles,
        "handles_max": max(s.handles for s in samples),
        "threads_start": samples[0].threads,
        "threads_end": samples[-1].threads,
        "threads_max": max(s.threads for s in samples),
        "gdi_start": gdi_values[0] if gdi_values else 0,
        "gdi_end": gdi_values[-1] if gdi_values else 0,
        "user_start": user_values[0] if user_values else 0,
        "user_end": user_values[-1] if user_values else 0,
    }


def _judge_idle(summary: dict[str, float | int | str]) -> str:
    rss_growth = float(summary["rss_end"]) - float(summary["rss_start"])
    uss_growth = float(summary["uss_end"]) - float(summary["uss_start"])
    handle_growth = int(summary["handles_end"]) - int(summary["handles_start"])
    thread_growth = int(summary["threads_end"]) - int(summary["threads_start"])
    gdi_growth = int(summary["gdi_end"]) - int(summary["gdi_start"])
    user_growth = int(summary["user_end"]) - int(summary["user_start"])

    warnings: list[str] = []
    if uss_growth > 10:
        warnings.append(f"USS grew by {_fmt_mb(uss_growth)}")
    if rss_growth > 25 and uss_growth > 5:
        warnings.append(f"RSS grew by {_fmt_mb(rss_growth)}")
    if handle_growth > 50:
        warnings.append(f"handles grew by {handle_growth}")
    if thread_growth > 10:
        warnings.append(f"threads grew by {thread_growth}")
    if gdi_growth > 25:
        warnings.append(f"GDI objects grew by {gdi_growth}")
    if user_growth > 25:
        warnings.append(f"USER objects grew by {user_growth}")

    if warnings:
        return "CHECK: " + "; ".join(warnings)
    return "OK: stable"


def _write_report(samples_by_phase: dict[str, list[Sample]], path: Path) -> None:
    lines = [
        "# HushSnap Health Check",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Phase | Samples | RSS start/end/max | USS start/end/max | Handles start/end/max | Threads start/end/max | GUI start/end | Verdict |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]

    for phase, samples in samples_by_phase.items():
        summary = _phase_summary(samples)
        verdict = _judge_idle(summary)
        gui = "n/a"
        if int(summary["gdi_start"]) or int(summary["user_start"]):
            gui = (
                f"GDI {summary['gdi_start']}->{summary['gdi_end']}, "
                f"USER {summary['user_start']}->{summary['user_end']}"
            )
        lines.append(
            "| "
            f"{phase} | "
            f"{summary['count']} | "
            f"{summary['rss_start']:.1f}/{summary['rss_end']:.1f}/{summary['rss_max']:.1f} MB | "
            f"{summary['uss_start']:.1f}/{summary['uss_end']:.1f}/{summary['uss_max']:.1f} MB | "
            f"{summary['handles_start']}/{summary['handles_end']}/{summary['handles_max']} | "
            f"{summary['threads_start']}/{summary['threads_end']}/{summary['threads_max']} | "
            f"{gui} | "
            f"{verdict} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_instructions(duration: float, checkpoint: bool) -> None:
    duration_text = "until Ctrl+C" if duration <= 0 else f"for {duration:.0f}s"
    checkpoint_text = (
        "Press Enter during the run to mark checkpoints."
        if checkpoint
        else "No manual input is needed after the run starts."
    )
    print(
        f"""
Health check will sample {duration_text}.
Do whatever operation sequence you want to test while it runs.
{checkpoint_text}

Tip: use a short idle period at the end of your manual test. The final plateau
is usually more important than transient OCR/load spikes.
""".strip()
    )


def _listen_for_checkpoints(stop_event: threading.Event, checkpoints: list[tuple[float, str]],
                            total_start: float) -> None:
    index = 1
    while not stop_event.is_set():
        try:
            input()
        except EOFError:
            return
        if stop_event.is_set():
            return
        label = f"checkpoint_{index}"
        checkpoints.append((time.monotonic() - total_start, label))
        print(f"Marked {label}.")
        index += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="HushSnap process health check")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Sampling interval in seconds (default 1.0)")
    parser.add_argument("--duration", type=float, default=900.0,
                        help="Total sampling duration in seconds; 0 = until Ctrl+C (default 900)")
    parser.add_argument("--phase-name", default="manual_test",
                        help="Label to write into the phase column (default manual_test)")
    parser.add_argument("--checkpoint", action="store_true",
                        help="Let Enter mark checkpoints while sampling")
    args = parser.parse_args()

    proc = _find_running_hushsnap()
    if proc is None:
        sys.exit("No running HushSnap process found. Start the packaged app first.")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = REPORT_DIR / f"health_check_{timestamp}.csv"
    report_path = REPORT_DIR / f"health_check_{timestamp}.md"

    print(f"Attached to HushSnap PID {proc.pid}")
    print(f"CSV:    {csv_path}")
    print(f"Report: {report_path}")
    _print_instructions(args.duration, args.checkpoint)
    input("\nPress Enter to start.")

    total_start = time.monotonic()
    checkpoints: list[tuple[float, str]] = []
    stop_event = threading.Event()
    checkpoint_thread = None
    if args.checkpoint:
        checkpoint_thread = threading.Thread(
            target=_listen_for_checkpoints,
            args=(stop_event, checkpoints, total_start),
            daemon=True,
        )
        checkpoint_thread.start()

    samples_by_phase: dict[str, list[Sample]] = {}
    try:
        samples_by_phase[args.phase_name] = _collect_for_duration(
            proc, args.phase_name, args.duration, args.interval, total_start
        )
    except KeyboardInterrupt:
        print("\nStopped by Ctrl+C.")
    finally:
        stop_event.set()

    all_samples = [sample for phase_samples in samples_by_phase.values() for sample in phase_samples]
    for elapsed, label in checkpoints:
        all_samples.append(
            Sample(
                phase=label,
                elapsed_s=0.0,
                total_elapsed_s=elapsed,
                rss_mb=0.0,
                uss_mb=0.0,
                handles=0,
                threads=0,
                cpu_pct=0.0,
                delta_mb=0.0,
                gdi_objects=None,
                user_objects=None,
            )
        )
    all_samples.sort(key=lambda sample: sample.total_elapsed_s)
    _write_csv(all_samples, csv_path)
    _write_report(samples_by_phase, report_path)

    print("\nHealth check complete.")
    print(f"CSV written to:    {csv_path}")
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
