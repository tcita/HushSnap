"""Lightweight OCR engine registry. Each engine module self-registers at import time."""

import logging
import time

logger = logging.getLogger(__name__)

_ENGINES: dict[str, dict] = {}
_DEFAULT_ENGINE: str | None = None


def register_engine(engine_id: str, *, recognize, release=None, trim=None, warmup=None, metadata=None):
    """Register an OCR engine implementation.

    Args:
        engine_id: Unique identifier (e.g. "windows", "rapidocr").
        recognize: Callable(image: QImage, language_tag: str) -> OcrRecognition.
        release: Optional zero-arg callable to free engine resources.
        trim: Optional zero-arg callable to trim engine resident memory.
        warmup: Optional zero-arg callable to pre-initialize engine resources.
        metadata: Optional dict (display_name, error_prefixes list, etc.).
    """
    global _DEFAULT_ENGINE
    existing = _ENGINES.get(engine_id)
    _ENGINES[engine_id] = {
        "recognize": recognize,
        "release": release if release is not None else (existing["release"] if existing else None),
        "trim": trim if trim is not None else (existing["trim"] if existing else None),
        "warmup": warmup if warmup is not None else (existing["warmup"] if existing else None),
        "metadata": metadata if metadata is not None else (existing["metadata"] if existing else {}),
    }
    if _DEFAULT_ENGINE is None:
        _DEFAULT_ENGINE = engine_id
    logger.debug("OCR engine registered: %s", engine_id)


def get_default_engine() -> str | None:
    return _DEFAULT_ENGINE


def get_recognize_fn(engine_id: str):
    entry = _ENGINES.get(engine_id)
    return entry["recognize"] if entry else None


def release_engine(engine_id: str):
    """Release resources for a specific engine (no-op if engine has no release hook)."""
    entry = _ENGINES.get(engine_id)
    if entry and entry["release"]:
        entry["release"]()


def trim_engine(engine_id: str):
    """Trim memory for a specific engine (no-op if engine has no trim hook)."""
    entry = _ENGINES.get(engine_id)
    if entry and entry.get("trim"):
        entry["trim"]()


def warmup_engine(engine_id: str):
    """Pre-initialize resources for a specific engine (no-op if no warmup hook)."""
    entry = _ENGINES.get(engine_id)
    if entry and entry.get("warmup"):
        logger.debug("[engine] Calling warmup hook for: %s", engine_id)
        t0 = time.perf_counter()
        entry["warmup"]()
        elapsed = (time.perf_counter() - t0) * 1000
        logger.debug("[engine] Warmup hook finished for %s in %.1fms", engine_id, elapsed)
    else:
        logger.debug("[engine] No warmup hook registered for: %s", engine_id)


def identify_engine_error(error_message: str) -> str | None:
    """Check if an error message matches a known engine-specific error pattern.

    Returns the engine ID if matched, None otherwise.
    """
    lowered = (error_message or "").lower()
    for eid, entry in _ENGINES.items():
        for prefix in entry.get("metadata", {}).get("error_prefixes", []):
            if prefix in lowered:
                return eid
    return None


def registered_engines() -> dict[str, dict]:
    """Return dict of registered engine IDs to their metadata."""
    return {eid: entry["metadata"] for eid, entry in _ENGINES.items()}
