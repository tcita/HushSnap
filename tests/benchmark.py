import os
import sys
import time
import gc
import argparse
import threading
import psutil
import logging
from pathlib import Path
from PyQt6 import QtWidgets, QtGui, QtCore

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from hushsnap.ocr_controller import OcrController
from hushsnap.system.debug_interface import DebugInterface
from hushsnap.system.memory_utils import (
    get_working_set_mb, get_page_fault_count, get_handle_count, fmt_memory,
)
from hushsnap.config import get_config_path, resolve_ui_lang, ui_text

# Configure logging to capture [ANCHOR] logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────

_process = psutil.Process(os.getpid())

def get_private_bytes_mb():
    return _process.memory_info().private / (1024 * 1024)

def _ws_mb():
    return _process.memory_info().rss / (1024 * 1024)

def _pvt_mb():
    return _process.memory_info().private / (1024 * 1024) if hasattr(
        _process.memory_info(), "private") else -1


# ── High-frequency memory sampler ─────────────────────────────────────

class MemorySampler:
    """Continuously samples WS + Pvt in a background daemon thread.

    Used with --profile to capture the memory *shape* during an OCR call,
    not just the scalar peak.
    """

    def __init__(self, interval_s=0.01):
        self.interval_s = interval_s
        self._samples: list[tuple[float, float, float]] = []  # (elapsed_s, ws, pvt)
        self._running = False
        self._thread = None
        self._t0 = 0.0

    def start(self):
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
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    @property
    def samples(self):
        return list(self._samples)

    def stats(self):
        """Compute shape statistics from captured samples."""
        if not self._samples:
            return {}
        ws_vals = [s[1] for s in self._samples]
        t_vals = [s[0] for s in self._samples]
        ws_baseline = ws_vals[0]
        ws_peak = max(ws_vals)
        ws_final = ws_vals[-1]
        ws_peak_idx = ws_vals.index(ws_peak)

        # Time above 50% of peak-baseline delta
        half_peak = ws_baseline + (ws_peak - ws_baseline) * 0.5
        above_half = sum(1 for v in ws_vals if v > half_peak)
        above_half_s = above_half * self.interval_s

        # Rise time: 10% → 90% of peak-baseline delta
        lo = ws_baseline + (ws_peak - ws_baseline) * 0.1
        hi = ws_baseline + (ws_peak - ws_baseline) * 0.9
        rise_start = next((i for i, v in enumerate(ws_vals) if v >= lo), 0)
        rise_end = next((i for i, v in enumerate(ws_vals) if v >= hi), ws_peak_idx)
        rise_time = (rise_end - rise_start) * self.interval_s

        # Fall time: 90% → 50% (after peak)
        fall_start = next((i for i in range(ws_peak_idx, len(ws_vals))
                           if ws_vals[i] <= hi), ws_peak_idx)
        fall_end = next((i for i in range(fall_start, len(ws_vals))
                         if ws_vals[i] <= half_peak), len(ws_vals) - 1)
        fall_time = (fall_end - fall_start) * self.interval_s

        # AUC: area under curve above baseline (MB·s)
        auc = sum(max(0, v - ws_baseline) for v in ws_vals) * self.interval_s

        # Normalised AUC: like "average excess MB above baseline over duration"
        duration = t_vals[-1] - t_vals[0] if len(t_vals) > 1 else 1.0
        auc_norm = auc / duration if duration > 0 else auc

        # ── Exponential decay λ (falling portion after peak) ─────────
        # Model:  ws(t) = baseline + (peak-baseline) * exp(-λ * (t - t_peak))
        # Fit via linear regression on log(y - baseline) vs t
        decay_lambda = -1.0
        r_squared = -1.0
        tail = ws_vals[ws_peak_idx:]
        tail_t = t_vals[ws_peak_idx:]
        try:
            # Only fit if we have enough tail samples and a meaningful drop
            above_bl = [max(1e-6, v - ws_baseline) for v in tail]
            if len(above_bl) >= 5 and max(above_bl) > 1.0:
                import numpy as np
                y = np.log(above_bl)
                x = np.array(tail_t) - tail_t[0]
                slope, intercept = np.polyfit(x, y, 1)
                decay_lambda = -slope  # positive λ = memory is being released
                # R² for fit quality
                y_pred = intercept + slope * x
                ss_res = np.sum((y - y_pred) ** 2)
                ss_tot = np.sum((y - np.mean(y)) ** 2)
                r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        except Exception:
            pass

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


# ── Mann-Whitney U test (lightweight, no scipy dependency) ────────────

def mann_whitney_u(a: list[float], b: list[float]) -> dict:
    """Mann-Whitney U test — statistical significance between two samples.

    Returns U statistic, z-score, and approximate two-tailed p-value.
    No scipy dependency — uses normal approximation for N ≥ 8.
    """
    import math
    n1, n2 = len(a), len(b)
    if n1 < 3 or n2 < 3:
        return {"u": -1, "z": 0, "p": -1, "significant": False, "note": "too few samples"}

    # Rank all values
    combined = [(v, 0) for v in a] + [(v, 1) for v in b]
    combined.sort(key=lambda x: x[0])

    ranks = []
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2  # 1-indexed tie-adjusted rank
        for k in range(i, j):
            ranks.append((avg_rank, combined[k][1]))
        i = j

    r1 = sum(r for r, grp in ranks if grp == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    u2 = n1 * n2 - u1
    u = min(u1, u2)

    # Normal approximation
    mu = n1 * n2 / 2
    # Tie correction
    rank_counts = {}
    for r, _ in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    tie_corr = sum(c**3 - c for c in rank_counts.values()) / ((n1 + n2) * (n1 + n2 - 1))
    sigma = math.sqrt(n1 * n2 / 12 * ((n1 + n2 + 1) - tie_corr))

    if sigma < 1e-9:
        z = 0.0
    else:
        z = (u - mu) / sigma

    # Two-tailed p-value via normal approximation
    abs_z = abs(z)
    # Simple normal CDF approximation
    p = 2 * (1 - _normal_cdf_approx(abs_z)) if abs_z > 0 else 1.0

    return {
        "u": u,
        "z": z,
        "p": max(0.0, min(1.0, p)),
        "significant": p < 0.05,
        "n1": n1, "n2": n2,
    }


def _normal_cdf_approx(x: float) -> float:
    """Abramowitz & Stegun 26.2.17 approximation for Φ(x)."""
    import math
    if x < 0:
        return 1 - _normal_cdf_approx(-x)
    # Constants
    b0, b1, b2, b3, b4, b5 = 0.2316419, 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
    t = 1 / (1 + b0 * x)
    phi = (1 / math.sqrt(2 * math.pi)) * math.exp(-x * x / 2)
    return 1 - phi * (b1*t + b2*t**2 + b3*t**3 + b4*t**4 + b5*t**5)


def _classify_shape(retention: float, decay_lambda: float, r_squared: float = -1.0) -> str:
    """Combine R, λ, and fit quality (R²) into a single shape classification.

    Decision tree (ordered, first match wins):

      R > 0.8  AND  λ ok  AND  0 ≤ λ < 0.2  →  PLATEAU  (both metrics agree: held)
      R < 0.5  AND  λ ok  AND  λ > 1.0      →  SPIKE    (both metrics agree: fast release)
      R < 0.5  AND  λ > 0.1                 →  SPIKE    (released, visible tail decay;
                                                 λ gated loosely — false positive merely
                                                 upgrades "unreliable" → "slow tail", both
                                                 are SPIKE family, no material harm)
      R < 0.5                                →  SPIKE    (released, but λ unreliable —
                                                 e.g. short tail window or fit failure;
                                                 retention is the more trustworthy metric)
      λ ok  AND  λ > 0.5                     →  MIXED    (0.5 ≤ R ≤ 0.8, meaningful decay;
                                                 λ is the ONLY signal here — MUST have R²)
      else                                   →  PLATEAU  (R ≥ 0.5, slow/no decay;
                                                 conservative default)

    ``r_squared`` < 0 means "no profile / not sampled" — λ gates that require
    fit quality are skipped (trust retention alone).
    """
    lambda_ok = r_squared < 0 or r_squared >= 0.5

    if retention > 0.8 and lambda_ok and 0 <= decay_lambda < 0.2:
        return "PLATEAU  (memory held, arena-like)"
    if retention < 0.5 and lambda_ok and decay_lambda > 1.0:
        return "SPIKE    (sharp peak, fast release)"
    if retention < 0.5 and decay_lambda > 0.1:
        return "SPIKE    (slow tail but released)"
    if retention < 0.5:
        return "SPIKE    (released, λ unreliable)"
    if lambda_ok and decay_lambda > 0.5:
        return "MIXED    (some retention but decaying)"
    return "PLATEAU  (memory retained)"

class BenchmarkRunner:
    def __init__(self, image_path):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.image_path = image_path

        config_path = get_config_path()
        lang = resolve_ui_lang(config_path)
        translate = lambda key, **kwargs: ui_text(lang, key, **kwargs)

        user_data_dir = Path(os.getenv("APPDATA")) / "HushSnap"

        self.controller = OcrController(
            app=self.app,
            translate=translate,
            config_path=config_path,
            user_data_dir=user_data_dir
        )

        self.finished = False
        self.start_time = 0.0
        self.end_time = 0.0
        self.last_text = ""

        self.controller.bridge.signal.connect(self._on_ocr_finished)

    def _on_ocr_finished(self, response):
        if response.recognition is not None:
            self.end_time = time.perf_counter()
            self.last_text = response.text
            self.finished = True
            logger.debug("[Benchmark] Final result received. Text length: %d",
                         len(self.last_text or ""))
        else:
            logger.debug("[Benchmark] Status update received: %s", response.text)

    def run_benchmark(self, iterations=5, interval=5.0, profile=False):
        sample_name = Path(self.image_path).name
        print(f"\n{'='*70}")
        print(f" HushSnap OCR Benchmark — {sample_name}")
        print(f" {iterations} iterations, {interval}s interval"
              f"{',  memory profile ON' if profile else ''}")
        print(f"{'='*70}")
        print(f" Metric key:")
        print(f"   WS  = Working Set (physical RAM, matches Task Manager)")
        print(f"   Pvt = Private Bytes (committed virtual, always >= WS)")
        print(f"   R   = Retention ratio (WS_after / WS_peak)")
        print(f"          R ≈ 1.0 → plateau (memory held after OCR)")
        print(f"          R ≪ 1.0 → spike  (memory released promptly)")
        print(f"{'='*70}")

        self.results = []
        results = self.results  # alias for local use
        texts = set()
        handles_before = get_handle_count()

        for i in range(iterations):
            print(f"\n[Iteration {i+1}/{iterations}]")
            gc.collect()
            time.sleep(interval)

            # --- snapshots before OCR ---
            pv_before = get_private_bytes_mb()
            ws_before = get_working_set_mb()
            pf_before = get_page_fault_count()
            h_before  = get_handle_count()

            self.finished = False
            self.start_time = time.perf_counter()

            # --- start high-freq sampler on first warm iteration if profiling ---
            # Cold start (i=0) includes model-loading noise; warm iterations
            # reflect steady-state behaviour.  Use i=1 when available, else i=0.
            sampler = None
            profile_iter = 1 if iterations > 1 else 0
            if profile and i == profile_iter:
                sampler = MemorySampler(interval_s=0.01)
                sampler.start()
                # Give sampler a few ticks to establish baseline
                time.sleep(0.05)

            DebugInterface.simulate_manual_ocr(self.controller, self.image_path)

            # --- poll for completion, tracking peak ---
            peak_pv = pv_before
            peak_ws = ws_before

            while not self.finished:
                self.app.processEvents()
                peak_pv = max(peak_pv, get_private_bytes_mb())
                peak_ws = max(peak_ws, get_working_set_mb())
                time.sleep(0.01)

            # Let UI repaint + sampler capture post-OCR tail
            for _ in range(5):
                self.app.processEvents()
                time.sleep(0.02)

            if sampler:
                # Let sampler capture ~2s of post-OCR tail
                time.sleep(2.0)
                sampler.stop()

            # --- snapshots after OCR ---
            pv_after = get_private_bytes_mb()
            ws_after = get_working_set_mb()
            pf_after = get_page_fault_count()
            h_after  = get_handle_count()

            duration = (self.end_time - self.start_time) * 1000
            pf_delta = pf_after - pf_before if pf_before >= 0 and pf_after >= 0 else -1
            h_delta  = h_after  - h_before  if h_before >= 0 and h_after >= 0 else -1
            retention = ws_after / peak_ws if peak_ws > 0 else -1

            print(f"  Wall Time:      {duration:8.1f} ms")
            print(f"  Private Bytes:  {peak_pv:8.2f} MB  (peak)")
            print(f"  Working Set:    {peak_ws:8.2f} MB  (peak physical RAM)")
            print(f"  Retention (R):  {retention:8.3f}     "
                  f"({'⚠ plateau' if retention > 0.7 else '✓ spike'})")
            print(f"  Page Faults:    {pf_delta:+8d}     (Δ this iteration)")
            print(f"  Handles:        {h_after:8d}     ({h_delta:+d} Δ)")
            print(f"  Chars:          {len(self.last_text or ''):8d}")

            result = {
                'duration':   duration,
                'peak_pv':    peak_pv,
                'peak_ws':    peak_ws,
                'ws_after':   ws_after,
                'pv_after':   pv_after,
                'pv_delta':   pv_after - pv_before,
                'ws_delta':   ws_after - ws_before,
                'retention':  retention,
                'pf_delta':   pf_delta,
                'h_delta':    h_delta,
            }
            results.append(result)
            if self.last_text:
                texts.add(self.last_text)

            # --- print profile details for first iteration ---
            if sampler:
                s = sampler.stats()
                lam = s["decay_lambda"]
                r2 = s["decay_r2"]
                shape = _classify_shape(retention, lam, r2)

                print(f"  ── Memory Profile (iteration {i+1}) ──")
                print(f"  Scale:  {s['ws_baseline']:.0f} MB (baseline)  →  "
                      f"{s['ws_peak']:.0f} MB (peak)  →  "
                      f"{s['ws_final']:.0f} MB (final)")
                print(f"  Rise: {s['rise_time_s']:.2f}s  |  "
                      f"Fall: {s['fall_time_s']:.2f}s  |  "
                      f">50% peak: {s['time_above_50pct_s']:.2f}s")
                print(f"  AUC: {s['auc_mb_s']:.0f} MB·s  |  "
                      f"norm AUC: {s['auc_norm_mb']:.1f} MB")
                print(f"  λ (decay):  {lam:+.3f} s⁻¹  "
                      f"(R²={s['decay_r2']:.2f})  |  "
                      f"Shape: {shape}")
                result['profile'] = s

        # ── Summary ─────────────────────────────────────────────────

        print(f"\n{'='*70}")
        print(f" Summary ({iterations} iterations)")
        print(f"{'='*70}")

        avg_dur = sum(r['duration'] for r in results) / len(results)
        max_pv  = max(r['peak_pv']  for r in results)
        max_ws  = max(r['peak_ws']  for r in results)
        handles_final = get_handle_count()
        handles_total_delta = (handles_final - handles_before
                               if handles_before >= 0 and handles_final >= 0 else -1)

        # Retention analysis: warm-iteration retention is the key signal
        warm_retentions = [r['retention'] for r in results[1:]] if len(results) > 1 else []
        avg_retention = sum(warm_retentions) / len(warm_retentions) if warm_retentions else -1

        print(f" Latency (avg):        {avg_dur:8.1f} ms")
        print(f" Latency (best):       {min(r['duration'] for r in results):8.1f} ms")
        print(f" Private Bytes (max):  {max_pv:8.2f} MB  (committed virtual memory)")
        print(f" Working Set (max):    {max_ws:8.2f} MB  (physical RAM — Task Manager)")
        print(f" Retention (avg warm): {avg_retention:8.3f}     "
              f"({'⚠ plateau' if avg_retention > 0.7 else '✓ spike'} "
              f"— memory shape indicator)")

        # Profile-derived metrics from the sampled (warm) iteration
        profile = next((r.get('profile') for r in results if r.get('profile')), None)
        if profile:
            lam = profile["decay_lambda"]
            r2 = profile["decay_r2"]
            shape = _classify_shape(avg_retention, lam, r2)
            source = "warm iter" if len(results) > 1 else "iter 1"
            print(f" λ (decay rate):       {lam:8.3f} s⁻¹  "
                  f"(R²={r2:.2f}, {source})")
            print(f" AUC (norm):           {profile['auc_norm_mb']:8.1f} MB  "
                  f"(avg excess above baseline)")
            print(f" Shape classification: {shape}")
        elif warm_retentions:
            lam = -1.0
            shape = _classify_shape(avg_retention, lam)
            print(f" Shape classification: {shape}  (λ unavailable; use -p for full profile)")

        print(f" Handles (total Δ):    {handles_total_delta:+8d}     "
              f"(across all iterations)")
        print(f" Consistency:          "
              f"{'OK — all identical' if len(texts) == 1 else f'VARIED ({len(texts)} distinct results)'}")
        if texts:
            preview = list(texts)[0][:80].replace('\n', ' ')
            print(f" Text preview:         {preview}...")

        # ── Warnings ──────────────────────────────────────────────

        print(f"{'='*70}")
        warn = False

        if warm_retentions and all(r > 0.7 for r in warm_retentions):
            print(f" ⚠ Memory SHAPE warning: retention > 0.7 in ALL warm iterations.")
            print(f"   Memory is being held after OCR completes (plateau pattern).")
            print(f"   Check arena settings or look for leaked references.")
            warn = True

        warm_h_deltas = [r['h_delta'] for r in results[1:]] if len(results) > 1 else []
        if warm_h_deltas and all(d > 0 for d in warm_h_deltas):
            print(f" ⚠ Handle creep: grew in EVERY warm iteration "
                  f"(+{sum(warm_h_deltas):+d} total).")
            warn = True
        elif warm_h_deltas and sum(warm_h_deltas) > 20:
            print(f" ⚠ Handle growth: +{sum(warm_h_deltas):+d} across warm iterations.")
            warn = True

        if max_ws > max_pv:
            print(f" ⚠ Working Set > Private Bytes — unexpected; check measurement.")
            warn = True

        if not warn:
            print(f" ✓ No anomalies detected.")

        print(f"{'='*70}")


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="HushSnap OCR benchmark — latency, memory, and shape profiling"
    )
    parser.add_argument(
        "image",
        help="Image filename in scratch/ or absolute path"
    )
    parser.add_argument(
        "-s", "--interval",
        type=float, default=5.0,
        help="Seconds between OCR iterations (default: 5.0)"
    )
    parser.add_argument(
        "-n", "--iterations",
        type=int, default=5,
        help="Number of OCR iterations (default: 5)"
    )
    parser.add_argument(
        "-p", "--profile",
        action="store_true",
        help="Enable high-frequency memory sampling on first iteration "
             "(rise/fall times, decay λ, AUC)"
    )
    parser.add_argument(
        "--rec-batch-num",
        type=int, default=None,
        help="Override Rec.rec_batch_num (default: use production setting)"
    )
    parser.add_argument(
        "--intra-op-num-threads",
        type=int, default=None,
        help="Override EngineConfig.onnxruntime.intra_op_num_threads "
             "(default: use production setting)"
    )
    parser.add_argument(
        "--inter-op-num-threads",
        type=int, default=None,
        help="Override EngineConfig.onnxruntime.inter_op_num_threads "
             "(default: use production setting)"
    )
    args = parser.parse_args()

    # Resolve image path
    img_path = Path(args.image)
    if not img_path.is_absolute():
        img_path = project_root / "scratch" / img_path
    if not img_path.exists():
        print(f"Error: Could not find test sample {img_path}")
        sys.exit(1)

    # ── Apply engine parameter overrides for A/B testing ──────────────
    override_params = {}
    if args.rec_batch_num is not None:
        override_params["Rec.rec_batch_num"] = args.rec_batch_num
    if args.intra_op_num_threads is not None:
        override_params["EngineConfig.onnxruntime.intra_op_num_threads"] = args.intra_op_num_threads
    if args.inter_op_num_threads is not None:
        override_params["EngineConfig.onnxruntime.inter_op_num_threads"] = args.inter_op_num_threads

    if override_params:
        from hushsnap.ocr.ppocr import set_engine_params_override
        set_engine_params_override(override_params)
        print(f"[A/B TEST] Engine overrides applied: {override_params}")

    runner = BenchmarkRunner(str(img_path))
    runner.run_benchmark(
        iterations=args.iterations,
        interval=args.interval,
        profile=args.profile,
    )
