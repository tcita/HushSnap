"""HushSnap OCR benchmark library.

High-fidelity memory and latency profiling for OCR engine configuration
A/B testing and regression detection.  Measures the full OCR pipeline
(preprocessing + engine inference + text composition) without UI noise.

Usage as a library::

    from hushsnap.benchmark import BenchmarkRunner, MemorySampler, mann_whitney_u

    with BenchmarkRunner("scratch/sample.png") as bench:
        result = bench.run(iterations=3, profile=True)
        print(result.summary())
        result.to_json("results.json")

Usage as a CLI::

    python -m hushsnap.benchmark sample.png -n 3 -p --json results.json
"""

from ._sampler import MemorySampler, _ws_mb, _pvt_mb
from ._stats import mann_whitney_u, classify_shape
from ._result import BenchmarkResult, IterationResult
from ._runner import BenchmarkRunner

__all__ = [
    "BenchmarkResult",
    "BenchmarkRunner",
    "IterationResult",
    "MemorySampler",
    "classify_shape",
    "mann_whitney_u",
]
