"""Run PP-OCR detection + greedy line/column clustering on a PNG.

Wraps the production pipeline (ppocr.py) so test code can drive it
without touching Qt / ONNX internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DetectedBox:
    """One raw detection box with its cluster assignment."""
    text: str
    left: float
    top: float
    right: float
    bottom: float
    width: float
    height: float
    center_x: float
    center_y: float
    cluster_id: int = -1


@dataclass
class PipelineResult:
    """Output of detection + clustering on one image."""

    png_path: Path
    boxes: list[DetectedBox] = field(default_factory=list)
    n_raw_blocks: int = 0
    n_normalized: int = 0
    n_clusters: int = 0
    is_vertical: bool = False
    engine_elapse_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Engine management
# ═══════════════════════════════════════════════════════════════════════════

_engine = None


def get_engine():
    """Return the singleton PP-OCR engine, initialising it if needed."""
    global _engine
    if _engine is None:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtWidgets
        import sys
        QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

        from hushsnap.ocr.ppocr import get_ppocr_engine
        _engine = get_ppocr_engine()
    return _engine


def release_engine():
    """Release the engine singleton to free memory."""
    global _engine
    if _engine is not None:
        from hushsnap.ocr.ppocr import release_engine as _ppocr_release
        _ppocr_release()
        _engine = None


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════════════════


def run_pipeline(
    png_path: Path,
    engine=None,
    *,
    is_vertical: bool = False,
    unclip_ratio: float | None = None,
) -> PipelineResult:
    """Run PP-OCR detection + greedy line clustering on a PNG.

    Args:
        png_path: path to a PNG image.
        engine: pre-initialised PP-OCR engine (auto-detected if None).
        is_vertical: force vertical column clustering.
        unclip_ratio: passed through to RapidOCR engine call (None = use
            engine default, currently 1.6).  Values below 1.6 produce
            tighter boxes; 2.0 = PaddleOCR original default.

    Returns:
        :class:`PipelineResult` with every detected box and its cluster_id.
    """
    import time
    import cv2
    import numpy as np
    from PyQt6 import QtGui

    from hushsnap.ocr.ppocr import (
        _acquire_request, _release_request,
        _normalize_blocks, _greedy_line_cluster, _greedy_column_cluster,
        _is_vertical_json, ppocr_box_to_bbox,
    )

    if engine is None:
        engine = get_engine()

    source = QtGui.QImage(str(png_path))
    if source.isNull():
        return PipelineResult(png_path=png_path)

    w, h = source.width(), source.height()
    ptr = source.bits()
    ptr.setsize(source.sizeInBytes())
    arr = np.frombuffer(ptr, np.uint8).reshape((h, w, 4))[:, :, :3].copy()

    t0 = time.perf_counter()
    _acquire_request()
    try:
        if unclip_ratio is not None:
            result = engine(arr, unclip_ratio=unclip_ratio)
        else:
            result = engine(arr)
        json_data = result.to_json()
    finally:
        _release_request()
    elapse = (time.perf_counter() - t0) * 1000

    import gc; del arr, result; gc.collect()

    if not json_data:
        return PipelineResult(png_path=png_path, engine_elapse_ms=elapse)

    # ── Parse raw blocks ──────────────────────────────────────────────────
    raw_blocks = []
    for item in json_data:
        txt = str(item.get("txt", "") or "").strip()
        if not txt:
            continue
        box = item.get("box", [])
        left, top, right, bottom = ppocr_box_to_bbox(box)
        bw, bh = right - left, bottom - top
        if bw <= 0 or bh <= 0:
            continue
        raw_blocks.append({
            "text": txt,
            "box": [[left, top], [right, top], [right, bottom], [left, bottom]],
        })

    n_raw = len(raw_blocks)
    normalized = _normalize_blocks(raw_blocks)
    n_norm = len(normalized)

    if not normalized:
        return PipelineResult(
            png_path=png_path, n_raw_blocks=n_raw, n_normalized=0,
            engine_elapse_ms=elapse,
        )

    # ── Cluster ───────────────────────────────────────────────────────────
    auto_vertical = _is_vertical_json(json_data)
    if is_vertical or auto_vertical:
        clusters = _greedy_column_cluster(normalized)
    else:
        clusters = _greedy_line_cluster(normalized)

    # ── Build result boxes ────────────────────────────────────────────────
    boxes: list[DetectedBox] = []
    for ci, cluster in enumerate(clusters):
        for b in cluster:
            boxes.append(DetectedBox(
                text=b["text"],
                left=b["left"], top=b["top"],
                right=b["right"], bottom=b["bottom"],
                width=b["width"], height=b["height"],
                center_x=b["center_x"], center_y=b["center_y"],
                cluster_id=ci,
            ))

    return PipelineResult(
        png_path=png_path,
        boxes=boxes,
        n_raw_blocks=n_raw,
        n_normalized=n_norm,
        n_clusters=len(clusters),
        is_vertical=bool(is_vertical or auto_vertical),
        engine_elapse_ms=elapse,
    )
