"""Structured result types for benchmark runs."""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict


@dataclass
class IterationResult:
    """Metrics for a single OCR iteration."""
    duration_ms: float
    peak_ws_mb: float
    peak_pv_mb: float
    ws_after_mb: float
    pv_after_mb: float
    retention: float
    pf_delta: int
    h_delta: int
    text_chars: int
    trim_delta_mb: float = 0.0   # WS reduction from idle trim (0 = no trim this iteration)
    profile: dict | None = None


@dataclass
class BenchmarkResult:
    """Aggregate results from a ``BenchmarkRunner.run()`` call.

    Can be serialised to JSON via :meth:`to_json` or ``dataclasses.asdict``.
    """
    image_name: str
    iterations: int
    profile_enabled: bool
    idle_trim_enabled: bool = False
    trim_delay_s: float = 5.0
    engine_overrides: dict = field(default_factory=dict)
    iter_results: list[IterationResult] = field(default_factory=list)

    # ── Computed aggregates ──
    avg_duration_ms: float = 0.0
    best_duration_ms: float = 0.0
    max_ws_mb: float = 0.0
    max_pv_mb: float = 0.0
    avg_retention: float = 0.0
    avg_trim_delta_mb: float = 0.0   # mean WS freed by idle trim (warm iters)
    shape_classification: str = ""
    decay_lambda: float = -1.0
    decay_r2: float = -1.0
    auc_norm_mb: float = 0.0
    total_handle_delta: int = 0
    text_consistency: str = ""
    text_preview: str = ""
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return a human-readable multi-line summary string."""
        flags = []
        if self.profile_enabled:
            flags.append("profiled")
        if self.idle_trim_enabled:
            flags.append(f"idle-trim@{self.trim_delay_s:.0f}s")
        flag_str = f", {', '.join(flags)}" if flags else ""

        lines = [
            f"Benchmark: {self.image_name}  "
            f"({self.iterations} iterations{flag_str})",
        ]
        if self.engine_overrides:
            lines.append(f"Engine overrides: {self.engine_overrides}")
        lines += [
            f"  Latency  avg={self.avg_duration_ms:.0f}ms  "
            f"best={self.best_duration_ms:.0f}ms",
            f"  Memory   WS peak={self.max_ws_mb:.0f}MB  "
            f"Pvt peak={self.max_pv_mb:.0f}MB",
        ]
        if self.idle_trim_enabled and self.avg_trim_delta_mb > 0:
            lines.append(
                f"  Trim     avg Δ={self.avg_trim_delta_mb:.0f}MB WS freed  "
                f"(delay={self.trim_delay_s:.0f}s)"
            )
        lines += [
            f"  Shape    retention={self.avg_retention:.3f}  "
            f"λ={self.decay_lambda:.3f}s⁻¹  "
            f"AUC(norm)={self.auc_norm_mb:.1f}MB  "
            f"→ {self.shape_classification}",
            f"  Text     {self.text_consistency}",
        ]
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)

    def to_json(self, path: str | Path | None = None) -> str:
        """Serialise to JSON.

        Parameters
        ----------
        path:
            If given, writes the JSON to this file path.

        Returns
        -------
        str
            The JSON string.
        """
        d = asdict(self)
        # ws_vals is huge — strip from JSON (keep it in-memory only)
        for ir in d.get("iter_results", []):
            if ir.get("profile") and "ws_vals" in ir["profile"]:
                del ir["profile"]["ws_vals"]
        j = json.dumps(d, indent=2, default=str)
        if path:
            Path(path).write_text(j, encoding="utf-8")
        return j
