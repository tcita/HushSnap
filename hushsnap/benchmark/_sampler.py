"""High-frequency memory sampler for OCR performance profiling."""

import logging
import os
import time
import threading

import psutil
import numpy as np

logger = logging.getLogger(__name__)

_process = psutil.Process(os.getpid())


def _ws_mb():
    """Working Set in MB (physical RAM, matches Task Manager)."""
    return _process.memory_info().rss / (1024 * 1024)


def _pvt_mb():
    """Private Bytes in MB (committed virtual, always >= WS)."""
    return _process.memory_info().private / (1024 * 1024) if hasattr(
        _process.memory_info(), "private") else -1


class MemorySampler:
    """Continuously samples working set and private bytes in a background
    daemon thread at a configurable interval.

    Use with ``BenchmarkRunner(profile=True)`` to capture the memory
    *shape* during an OCR call — rise/fall times, exponential decay rate
    (λ), and area under the curve above baseline.

    Parameters
    ----------
    interval_s:
        Sampling period in seconds (default 0.01 = 100 Hz).
    """

    def __init__(self, interval_s: float = 0.01):
        self.interval_s = interval_s
        self._samples: list[tuple[float, float, float]] = []  # (elapsed_s, ws, pvt)
        self._running = False
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def start(self):
        """Begin sampling.  Call immediately before the measured operation."""
        self._samples.clear()
        self._running = True
        self._t0 = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while self._running:
            t = time.perf_counter() - self._t0
            self._samples.append((t, _ws_mb(), _pvt_mb()))
            time.sleep(self.interval_s)

    def stop(self):
        """Stop sampling and join the background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    @property
    def samples(self) -> list[tuple[float, float, float]]:
        """Return a copy of all ``(elapsed_s, ws_mb, pvt_mb)`` samples."""
        return list(self._samples)

    def stats(self) -> dict:
        """Compute shape statistics from captured samples.

        Returns a dictionary with keys:

        * ``ws_baseline`` — WS at sampling start (MB)
        * ``ws_peak`` — maximum WS during the window (MB)
        * ``ws_final`` — WS at sampling end (MB)
        * ``rise_time_s`` — 10%→90% of peak-baseline delta
        * ``fall_time_s`` — 90%→50% of peak-baseline delta (after peak)
        * ``time_above_50pct_s`` — duration WS exceeded half-peak
        * ``auc_mb_s`` — area under curve above baseline (MB·s)
        * ``auc_norm_mb`` — normalised AUC (average excess MB)
        * ``decay_lambda`` — exponential decay rate (s⁻¹); -1 if unavailable
        * ``decay_r2`` — R² of the exponential fit; -1 if unavailable
        * ``ws_vals`` — raw WS sample list
        """
        if not self._samples:
            return {}
        ws_vals = [s[1] for s in self._samples]
        t_vals = [s[0] for s in self._samples]
        ws_baseline = ws_vals[0]
        ws_peak = max(ws_vals)
        ws_final = ws_vals[-1]
        ws_peak_idx = ws_vals.index(ws_peak)

        # ── Time above 50% of peak-baseline delta ──
        half_peak = ws_baseline + (ws_peak - ws_baseline) * 0.5
        above_half = sum(1 for v in ws_vals if v > half_peak)
        above_half_s = above_half * self.interval_s

        # ── Rise time: 10% → 90% ──
        lo = ws_baseline + (ws_peak - ws_baseline) * 0.1
        hi = ws_baseline + (ws_peak - ws_baseline) * 0.9
        rise_start = next((i for i, v in enumerate(ws_vals) if v >= lo), 0)
        rise_end = next((i for i, v in enumerate(ws_vals) if v >= hi), ws_peak_idx)
        rise_time = (rise_end - rise_start) * self.interval_s

        # ── Fall time: 90% → 50% (after peak) ──
        fall_start = next((i for i in range(ws_peak_idx, len(ws_vals))
                           if ws_vals[i] <= hi), ws_peak_idx)
        fall_end = next((i for i in range(fall_start, len(ws_vals))
                         if ws_vals[i] <= half_peak), len(ws_vals) - 1)
        fall_time = (fall_end - fall_start) * self.interval_s

        # ── AUC ──
        auc = sum(max(0, v - ws_baseline) for v in ws_vals) * self.interval_s
        duration = t_vals[-1] - t_vals[0] if len(t_vals) > 1 else 1.0
        auc_norm = auc / duration if duration > 0 else auc

        # ── Exponential decay λ (falling portion after peak) ──
        # Model:  ws(t) = baseline + (peak-baseline) · exp(-λ · (t - t_peak))
        # Fit via linear regression on log(y - baseline) vs t
        decay_lambda = -1.0
        r_squared = -1.0
        tail = ws_vals[ws_peak_idx:]
        tail_t = t_vals[ws_peak_idx:]
        try:
            above_bl = [max(1e-6, v - ws_baseline) for v in tail]
            if len(above_bl) >= 5 and max(above_bl) > 1.0:
                y = np.log(above_bl)
                x = np.array(tail_t) - tail_t[0]
                slope, intercept = np.polyfit(x, y, 1)
                decay_lambda = -slope  # positive λ → memory is being released
                y_pred = intercept + slope * x
                ss_res = np.sum((y - y_pred) ** 2)
                ss_tot = np.sum((y - np.mean(y)) ** 2)
                r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        except Exception:
            logger.debug("benchmark: decay-rate polyfit skipped (insufficient data)", exc_info=True)

        return {
            "ws_baseline": ws_baseline,
            "ws_peak":     ws_peak,
            "ws_final":    ws_final,
            "time_above_50pct_s": above_half_s,
            "rise_time_s": rise_time,
            "fall_time_s": fall_time,
            "auc_mb_s":    auc,
            "auc_norm_mb": auc_norm,
            "decay_lambda": decay_lambda,
            "decay_r2":    r_squared,
            "ws_vals":     ws_vals,
        }
