"""Benchmark runner — orchestrates OCR calls and collects measurements."""

import os
import sys
import time
import gc
import logging
from pathlib import Path

from PyQt6 import QtWidgets, QtCore

from hushsnap.ocr_controller import OcrController
from hushsnap.system.debug_interface import DebugInterface
from hushsnap.system.memory_utils import (
    get_working_set_mb, get_page_fault_count, get_handle_count,
)
from hushsnap.config import get_config_path, resolve_ui_lang, ui_text

from ._sampler import _ws_mb, _pvt_mb, MemorySampler
from ._stats import classify_shape
from ._result import BenchmarkResult, IterationResult

logger = logging.getLogger(__name__)


class _NullTextEdit:
    """Minimal mock so that ``popup.text_edit.toPlainText()`` works."""
    @staticmethod
    def toPlainText():
        return ""


class _NullPopup(QtCore.QObject):
    """No-op popup that suppresses UI overhead during benchmarking.

    Replaces the real OcrPopup so that ``show_loading`` / ``show_text``
    are zero-cost, avoiding measurement noise from widget rendering,
    clipboard operations, and tray notifications.
    """
    pin_toggled = QtCore.pyqtSignal(bool)

    # Provide the attributes that OcrController probes.
    text_edit = _NullTextEdit()

    def __init__(self):
        super().__init__()

    def show_loading(self, **kwargs): pass
    def show_text(self, text="", **kwargs): pass
    def set_pinned(self, pinned): pass
    def is_pinned(self): return False
    def isVisible(self): return False
    def set_anchor_pos(self, x, y, width=None, height=None): pass
    def clear_anchor(self): pass
    def apply_font_size(self): pass


class BenchmarkRunner:
    """Measure OCR pipeline performance for a single image.

    Uses the exact same code path as a user-triggered OCR capture
    (preprocessing → engine inference → text composition), but
    suppresses UI side effects (popup, clipboard, tray, idle trim)
    so they don't add noise to the measurements.

    Parameters
    ----------
    image_path:
        Path to the image to OCR.  Must exist.

    Example
    -------

        with BenchmarkRunner("scratch/sample.png") as bench:
            result = bench.run(iterations=3, profile=True)
            print(result.summary())
            result.to_json("out.json")
    """

    # ── Timeouts ──────────────────────────────────────────────────

    OCR_TIMEOUT_S = 120.0       # max wait for a single OCR call
    LOAD_TIMEOUT_S = 60.0     # max wait for engine load

    # ── Lifecycle ─────────────────────────────────────────────────

    def __init__(self, image_path: str | Path):
        self.image_path = Path(image_path)
        if not self.image_path.exists():
            raise FileNotFoundError(f"Image not found: {self.image_path}")

        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

        config_path = get_config_path()
        lang = resolve_ui_lang(config_path)
        translate = lambda key, **kwargs: ui_text(lang, key, **kwargs)
        user_data_dir = Path(os.getenv("APPDATA", "")) / "HushSnap"

        self.controller = OcrController(
            app=self.app,
            translate=translate,
            config_path=config_path,
            user_data_dir=user_data_dir,
            popup=_NullPopup(),          # suppress popup rendering overhead
        )

        # ── Suppress remaining UI side effects ──────────────────────
        # Disconnect the production on_ocr_finished handler so that
        # clipboard writes, tray notifications, and the 5 s idle-trim
        # timer don't fire during benchmarking.
        try:
            self.controller.bridge.ocr_result.disconnect()
        except TypeError:
            pass  # no slots were connected (shouldn't happen, but safe)
        self.controller.bridge.ocr_result.connect(self._on_ocr_finished)

        # ── Internal state ────────────────────────────────────────
        self._finished = False
        self._end_time = 0.0
        self._last_text = ""
        self._ocr_error = False
        self._load_done = False
        self.controller.bridge.load_finished.connect(self._on_load_done)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            self.controller.bridge.ocr_result.disconnect()
        except Exception:
            logger.debug("benchmark: ocr_result disconnect failed", exc_info=True)
        return False  # don't suppress exceptions

    # ── Signal handlers ───────────────────────────────────────────

    def _on_load_done(self):
        self._load_done = True

    def _on_ocr_finished(self, response):
        """Record completion — the OCR pipeline emits exactly one final
        response per request."""
        self._end_time = time.perf_counter()
        if response.recognition is not None:
            self._last_text = response.text or ""
            self._ocr_error = False
        else:
            self._last_text = ""
            self._ocr_error = True
        self._finished = True
        # Mirror the production handler's flag reset so that
        # _trim_current_engine sees the correct state.
        self.controller._expecting_ocr_result = False

    # ── Public API ────────────────────────────────────────────────

    def run(self,
            iterations: int = 5,
            interval: float = 5.0,
            profile: bool = False,
            gc_between: bool = False,
            idle_trim: bool = False,
            trim_delay_s: float = 5.0,
            engine_overrides: dict | None = None,
            verbose: bool = True) -> BenchmarkResult:
        """Run the benchmark and return a structured result.

        Parameters
        ----------
        iterations:
            Number of OCR calls.  Iteration 0 pays a one-time first-
            inference cost that later iterations do not: ``_wait_for_load``
            has already loaded the engine (session build, mmap, graph opt
            - the model-load cost), but ORT has not yet committed the
            detector's input-sized intermediate tensors, so iteration 0's
            first ``engine(arr)`` commits them and triggers a demand-zero
            page-fault storm sized by the image.  This first-inference
            buffer-commit cost is distinct from the model-load cost that
            the load hook handles; it scales with image size, not with steady-
            state throughput, so it is excluded from the latency
            aggregates (``avg_duration_ms`` / ``best_duration_ms``) and
            preserved separately as ``cold_duration_ms``.  See
            ``scripts/OCR_FIRST_INFERENCE.md`` ("Two cold starts") for
            the full derivation.  Iterations 1+ are warm.
        interval:
            Seconds between iterations.
        profile:
            Enable high-frequency memory sampling on the first warm
            iteration (captures rise/fall times, decay λ, AUC).
        gc_between:
            Run ``gc.collect()`` before each iteration.  Off by default
            (matches production behaviour).
        idle_trim:
            Simulate the production 5 s idle-trim timer between warm
            iterations.  After ``trim_delay_s`` seconds of the interval,
            calls ``trim_engine()`` to release the working set, then
            waits the remaining interval before the next OCR.  Use for
            A/B testing trim vs no-trim impact on latency and page faults.
        trim_delay_s:
            Seconds to wait after the previous OCR before firing the idle
            trim.  Must be ≤ *interval*.  Default 5.0 (matches production
            ``_trim_timer.start(5000)``).
        engine_overrides:
            Dict of engine parameters that differ from production
            defaults (recorded in the result for provenance).
        verbose:
            Print progress and summary to stdout.

        Returns
        -------
        BenchmarkResult
            See :class:`BenchmarkResult` for fields.
        """
        if idle_trim and trim_delay_s > interval:
            raise ValueError(
                f"trim_delay_s ({trim_delay_s:.1f}s) must be ≤ interval "
                f"({interval:.1f}s)"
            )

        image_name = self.image_path.name

        if verbose:
            flags = []
            if profile:
                flags.append("memory profile ON")
            if gc_between:
                flags.append("gc_between ON")
            if idle_trim:
                flags.append(f"idle trim ON @ {trim_delay_s:.0f}s")
            print(f"\n{'='*70}")
            print(f" HushSnap OCR Benchmark — {image_name}")
            print(f" {iterations} iterations, {interval}s interval"
                  f"{',  ' + ', '.join(flags) if flags else ''}")
            print(f"{'='*70}")

        self._wait_for_load()

        iter_results: list[IterationResult] = []
        texts_seen: set[str] = set()
        handles_before = get_handle_count()

        for i in range(iterations):
            if verbose:
                print(f"\n[Iteration {i+1}/{iterations}]")

            if gc_between:
                gc.collect()

            # ── Inter-iteration wait (with optional idle trim) ──
            trim_delta = 0.0
            if idle_trim and i > 0:
                # Simulate production idle-trim timer.
                # Wait trim_delay_s, trim, then wait remaining interval.
                time.sleep(trim_delay_s)
                ws_pre_trim = _ws_mb()
                self._trim_current_engine()
                ws_post_trim = _ws_mb()
                trim_delta = ws_pre_trim - ws_post_trim
                if trim_delta > 0 and verbose:
                    print(f"  ── Idle Trim: {ws_pre_trim:.0f} → "
                          f"{ws_post_trim:.0f} MB (Δ={trim_delta:.0f} MB)")
                remaining = interval - trim_delay_s
                if remaining > 0:
                    time.sleep(remaining)
            else:
                time.sleep(interval)

            # ── snapshots before OCR ──
            pv_before = _pvt_mb()
            ws_before = _ws_mb()
            pf_before = get_page_fault_count()
            h_before  = get_handle_count()

            self._finished = False
            self._ocr_error = False
            t_start = time.perf_counter()

            # ── high-freq sampler (warm iteration only) ──
            sampler: MemorySampler | None = None
            profile_iter = 1 if iterations > 1 else 0
            if profile and i == profile_iter:
                sampler = MemorySampler(interval_s=0.01)
                sampler.start()
                time.sleep(0.05)  # let sampler establish baseline

            DebugInterface.simulate_manual_ocr(self.controller, str(self.image_path))

            # ── poll for completion, tracking peak memory ──
            peak_pv = pv_before
            peak_ws = ws_before
            t_poll_start = time.perf_counter()

            while not self._finished:
                self.app.processEvents()
                peak_pv = max(peak_pv, _pvt_mb())
                peak_ws = max(peak_ws, _ws_mb())
                if time.perf_counter() - t_poll_start > self.OCR_TIMEOUT_S:
                    raise TimeoutError(
                        f"OCR did not complete within {self.OCR_TIMEOUT_S:.0f}s "
                        f"(iteration {i+1}/{iterations})"
                    )
                time.sleep(0.01)

            # Let UI repaint + sampler capture post-OCR tail
            for _ in range(5):
                self.app.processEvents()
                time.sleep(0.02)

            if sampler:
                time.sleep(2.0)  # capture ~2s of post-OCR tail
                sampler.stop()

            # ── snapshots after OCR ──
            pv_after = _pvt_mb()
            ws_after = _ws_mb()
            pf_after = get_page_fault_count()
            h_after  = get_handle_count()

            duration_ms = (self._end_time - t_start) * 1000
            pf_delta = pf_after - pf_before if pf_before >= 0 and pf_after >= 0 else -1
            h_delta  = h_after  - h_before  if h_before >= 0 and h_after >= 0 else -1
            retention = ws_after / peak_ws if peak_ws > 0 else -1

            if verbose:
                print(f"  Wall Time:      {duration_ms:8.1f} ms")
                print(f"  Private Bytes:  {peak_pv:8.2f} MB  (peak)")
                print(f"  Working Set:    {peak_ws:8.2f} MB  (peak physical RAM)")
                print(f"  Retention (R):  {retention:8.3f}     "
                      f"({'plateau' if retention > 0.7 else 'spike'})")
                print(f"  Page Faults:    {pf_delta:+8d}     (Δ this iteration)")
                print(f"  Handles:        {h_after:8d}     ({h_delta:+d} Δ)")
                print(f"  Chars:          {len(self._last_text):8d}")
                if self._ocr_error:
                    print(f"  ⚠ OCR reported an error for this iteration")

            ir = IterationResult(
                duration_ms=duration_ms,
                peak_ws_mb=peak_ws,
                peak_pv_mb=peak_pv,
                ws_after_mb=ws_after,
                pv_after_mb=pv_after,
                retention=retention,
                pf_delta=pf_delta,
                h_delta=h_delta,
                text_chars=len(self._last_text),
                trim_delta_mb=trim_delta,
            )
            iter_results.append(ir)
            if self._last_text:
                texts_seen.add(self._last_text)

            # ── print profile detail ──
            if sampler:
                s = sampler.stats()
                ir.profile = s
                if verbose:
                    lam = s["decay_lambda"]
                    r2 = s["decay_r2"]
                    shape = classify_shape(retention, lam, r2)
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
                          f"(R²={r2:.2f})  |  "
                          f"Shape: {shape}")

        # ── Build aggregate result ────────────────────────────────
        result = BenchmarkResult(
            image_name=image_name,
            iterations=iterations,
            profile_enabled=profile,
            idle_trim_enabled=idle_trim,
            trim_delay_s=trim_delay_s if idle_trim else 0.0,
            engine_overrides=dict(engine_overrides) if engine_overrides else {},
            iter_results=iter_results,
        )

        # Derived aggregates
        # Latency: WARM-ONLY.  Iteration 0 pays the first-inference
        # buffer-commit cost (ORT commits the det intermediate tensors,
        # triggering a demand-zero fault storm sized by the input image).
        # This is distinct from the model-load cost _wait_for_load
        # already handled; it is a one-time startup cost, not steady-state
        # throughput, so it is excluded from avg/best and kept standalone
        # as cold_duration_ms.  See scripts/OCR_FIRST_INFERENCE.md.
        # The same warm slice is reused for retention below.
        warm = iter_results[1:] if len(iter_results) > 1 else iter_results
        warm_durations = [r.duration_ms for r in warm]
        result.avg_duration_ms = (sum(warm_durations) / len(warm_durations)
                                  if warm_durations else 0.0)
        result.best_duration_ms = min(warm_durations) if warm_durations else 0.0
        result.cold_duration_ms = iter_results[0].duration_ms if iter_results else 0.0
        result.max_ws_mb = max(r.peak_ws_mb for r in iter_results)
        result.max_pv_mb = max(r.peak_pv_mb for r in iter_results)

        # Retention from warm iterations (reuses the warm slice above;
        # iteration 0 excluded for the same cold-start reason as latency)
        warm_retentions = [r.retention for r in warm]
        result.avg_retention = sum(warm_retentions) / len(warm_retentions) if warm_retentions else -1

        # Idle trim delta (warm iterations only — iteration 0 has no prior OCR to trim)
        trim_deltas = [r.trim_delta_mb for r in warm if r.trim_delta_mb > 0]
        result.avg_trim_delta_mb = sum(trim_deltas) / len(trim_deltas) if trim_deltas else 0.0

        # Profile metrics from the sampled iteration
        sampled = next((r.profile for r in iter_results if r.profile), None)
        if sampled:
            result.decay_lambda = sampled["decay_lambda"]
            result.decay_r2 = sampled["decay_r2"]
            result.auc_norm_mb = sampled["auc_norm_mb"]
        result.shape_classification = classify_shape(
            result.avg_retention, result.decay_lambda, result.decay_r2,
        )

        # Handle tracking (across all iterations)
        handles_final = get_handle_count()
        result.total_handle_delta = (handles_final - handles_before
                                     if handles_before >= 0 and handles_final >= 0 else -1)

        # Text consistency
        if len(texts_seen) <= 1:
            result.text_consistency = "identical" if texts_seen else "no text"
        else:
            result.text_consistency = f"varied ({len(texts_seen)} distinct)"
        if texts_seen:
            first_text = list(texts_seen)[0]
            result.text_preview = first_text[:80].replace('\n', ' ')
            result.text_full = first_text

        # ── Warnings ──────────────────────────────────────────────
        # WS-retention > 0.7 (plateau) is NOT an anomaly - do not warn.
        # Per scripts/OCR_FIRST_INFERENCE.md, the working set stays resident
        # after OCR because the detector's intermediate tensors commit once
        # (first inference) and are never released in production
        # (release_engine is dead code); the OS only reclaims those physical
        # pages via idle-trim (simulate with --idle-trim, reported as
        # trim_delta_mb).  High post-OCR retention is the expected steady
        # state, not a leak and not an arena problem (arena is off by
        # default).  A genuine leak would show Private Bytes (commit) GROWING
        # across warm iterations - not currently checked.
        warm_h_deltas = [r.h_delta for r in warm if r.h_delta != -1]
        if warm_h_deltas and all(d > 0 for d in warm_h_deltas):
            result.warnings.append(
                f"Handle creep: grew in EVERY warm iteration "
                f"(+{sum(warm_h_deltas):+d} total)"
            )
        elif warm_h_deltas and sum(warm_h_deltas) > 20:
            result.warnings.append(
                f"Handle growth: +{sum(warm_h_deltas):+d} across warm iterations"
            )
        if result.max_ws_mb > result.max_pv_mb:
            result.warnings.append(
                "Working Set > Private Bytes — unexpected; check measurement"
            )

        # ── Print summary ─────────────────────────────────────────
        if verbose:
            self._print_summary(result)

        return result

    # ── Internal ──────────────────────────────────────────────────

    def _trim_current_engine(self):
        """Trim the current OCR engine's working set.

        Mirrors ``OcrController._trim_current_engine``.  Only called
        between iterations when ``_finished`` is True, so there is no
        risk of trimming during an active OCR request.
        """
        if not self._finished:
            logger.debug("Skipping trim: OCR still in progress")
            return

        from hushsnap.ocr.engine import trim_engine
        from hushsnap.constants import OCR_ENGINE_PPOCR
        try:
            trim_engine(OCR_ENGINE_PPOCR)
        except Exception:
            logger.exception("Idle trim failed")

    def _wait_for_load(self):
        """Block until engine load completes (or times out).

        Spins a nested event loop that exits on ``load_finished``
        or after ``LOAD_TIMEOUT_S`` seconds.
        """
        if self._load_done:
            return

        loop = QtCore.QEventLoop()
        self.controller.bridge.load_finished.connect(loop.quit)
        timeout = QtCore.QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(int(self.LOAD_TIMEOUT_S * 1000))

        logger.debug("Waiting for engine load (timeout=%.0fs)...", self.LOAD_TIMEOUT_S)
        loop.exec()

        if not self._load_done:
            logger.warning(
                "Engine load did not complete within %.0fs; proceeding anyway",
                self.LOAD_TIMEOUT_S,
            )

    def _print_summary(self, result: BenchmarkResult):
        """Print the formatted summary block to stdout."""
        print(f"\n{'='*70}")
        print(f" Summary ({result.iterations} iterations"
              f"{', idle trim ON' if result.idle_trim_enabled else ''})")
        print(f"{'='*70}")
        print(f" Latency (warm avg):   {result.avg_duration_ms:8.1f} ms  "
              f"(iterations 1+; iter 0 excluded - first-inference buffer commit)")
        print(f" Latency (best):       {result.best_duration_ms:8.1f} ms")
        print(f" Latency (iter0):      {result.cold_duration_ms:8.1f} ms  "
              f"(first-inference buffer commit; info-only, not in avg)")
        print(f" Private Bytes (max):  {result.max_pv_mb:8.2f} MB  "
              f"(committed virtual memory)")
        print(f" Working Set (max):    {result.max_ws_mb:8.2f} MB  "
              f"(physical RAM — Task Manager)")
        if result.idle_trim_enabled and result.avg_trim_delta_mb > 0:
            print(f" Trim Δ (avg warm):    {result.avg_trim_delta_mb:8.1f} MB  "
                  f"(WS freed by idle trim @ {result.trim_delay_s:.0f}s)")
        print(f" Retention (avg warm): {result.avg_retention:8.3f}     "
              f"({'plateau' if result.avg_retention > 0.7 else 'spike'} - "
              f"plateau is expected; det tensors stay resident, see OCR_FIRST_INFERENCE.md)")

        if result.decay_lambda > -0.5:
            src = "warm iter" if result.iterations > 1 else "iter 1"
            print(f" λ (decay rate):       {result.decay_lambda:8.3f} s⁻¹  "
                  f"(R²={result.decay_r2:.2f}, {src})")
        if result.auc_norm_mb > 0:
            print(f" AUC (norm):           {result.auc_norm_mb:8.1f} MB  "
                  f"(avg excess above baseline)")

        lam_note = "" if result.profile_enabled else "  (λ unavailable; use -p for full profile)"
        print(f" Shape classification: {result.shape_classification}{lam_note}")

        print(f" Handles (total Δ):    {result.total_handle_delta:+8d}     "
              f"(across all iterations)")
        print(f" Consistency:          "
              f"{'OK — all identical' if result.text_consistency == 'identical' else result.text_consistency}")
        if result.text_preview:
            print(f" Text preview:         {result.text_preview}...")

        print(f"{'='*70}")
        if result.warnings:
            for w in result.warnings:
                print(f" ⚠ {w}")
        else:
            print(" ✓ No anomalies detected.")
        print(f"{'='*70}")
