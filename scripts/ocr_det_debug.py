"""DET (Detection) box diagnostic tool — inspect what PP-OCR detected and how
boxes are sorted by the layout algorithm.

Answers:
  1. How many boxes did DET find?  What are their positions?
  2. Did the engine fall back to recognition-only (no boxes → rec fallback)?
  3. How are boxes grouped and sorted by _greedy_line_cluster / _greedy_column_cluster?
  4. What does the final reading order look like?

Outputs:
  - Console report: box count, positions, fallback status, clustering details
  - Annotated image: raw DET boxes (cyan) + sorted line boxes (magenta)
    with index labels, saved alongside the input image.

Usage:
    python scripts/ocr_det_debug.py "C:\\path\\to\\screenshot.png"
    python scripts/ocr_det_debug.py --no-save "C:\\path\\to\\screenshot.png"
    python scripts/ocr_det_debug.py --verbose screenshot.png

Requirements: PyQt6, opencv-python, numpy, rapidocr (same as the main app).
"""

import argparse
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Colour palette for annotated output
# ---------------------------------------------------------------------------
RAW_BOX_COLOR = (255, 255, 0)     # cyan (BGR) — raw DET boxes
SORTED_LINE_COLOR = (255, 0, 255)  # magenta (BGR) — sorted line boxes
FALLBACK_TEXT_COLOR = (0, 0, 255)  # red (BGR) — fallback indicator
TEXT_COLOR = (0, 0, 0)            # black

COLOR_PALETTE_BGR = [
    (0, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 255),
    (255, 255, 0), (255, 0, 255), (128, 0, 128), (0, 128, 128),
    (128, 128, 0), (0, 0, 128), (72, 200, 72), (200, 72, 72),
]


def _draw_labeled_box(img, left, top, right, bottom, label, color, thickness=2):
    """Draw a rectangle with a numbered label on an OpenCV BGR image."""
    import cv2

    cv2.rectangle(img, (int(left), int(top)), (int(right), int(bottom)), color, thickness)
    # Draw label background + text
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(
        img,
        (int(left), max(0, int(top) - th - 4)),
        (int(left) + tw + 4, int(top)),
        color,
        -1,
    )
    cv2.putText(
        img, label,
        (int(left) + 2, int(top) - 2),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5, TEXT_COLOR, 1,
    )


def main():
    ap = argparse.ArgumentParser(
        description="DET box diagnostic — inspect detection boxes and layout sorting"
    )
    ap.add_argument("image", type=str, help="path to the screenshot / image to analyse")
    ap.add_argument(
        "--no-save", dest="save", action="store_false", default=True,
        help="skip saving the annotated image",
    )
    ap.add_argument(
        "--verbose", "-v", action="store_true", default=False,
        help="print every block's full text and coordinates",
    )
    ap.add_argument(
        "--language-tag", type=str, default="",
        help="language tag passed to the engine (default '')",
    )
    args = ap.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"ERROR: image not found: {image_path}")
        return 2

    # ── Qt setup ──────────────────────────────────────────────────────────
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtGui, QtWidgets
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    # ── Load image ────────────────────────────────────────────────────────
    import cv2
    import numpy as np

    source_image = QtGui.QImage(str(image_path))
    if source_image.isNull():
        print(f"ERROR: QImage could not load: {image_path}")
        return 2

    # Load as BGR for OpenCV drawing
    cv_image = cv2.imread(str(image_path))
    if cv_image is None:
        print(f"ERROR: OpenCV could not read: {image_path}")
        return 2

    print(f"Image: {image_path}  ({source_image.width()}×{source_image.height()})")
    print(f"Language tag: {args.language_tag!r}\n")

    # ── Run the OCR pipeline with instrumentation ─────────────────────────
    from hushsnap.ocr.preprocess import prepare_ocr_image
    from hushsnap.ocr.ppocr import (
        _get_engine,
        _acquire_request,
        _release_request,
        _is_vertical_json,
        _normalize_blocks,
        _greedy_line_cluster,
        _greedy_column_cluster,
        _build_lines_from_clusters,
        _apply_cjk_spacing,
        _apply_indentation,
        _apply_paragraph_breaks,
        compose_ppocr_structures,
        _recognize_without_detection,
        ppocr_box_to_bbox,
    )
    from hushsnap.ocr.models import OcrRecognition
    from hushsnap.constants import OCR_ENGINE_PPOCR

    # Prepare image (same as production pipeline)
    prepared = prepare_ocr_image(source_image)
    width = prepared.width()
    height = prepared.height()
    ptr = prepared.bits()
    ptr.setsize(prepared.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))[:, :, :3].copy()

    # ── Step 1: Run detection + recognition ───────────────────────────────
    t0 = time.perf_counter()
    _acquire_request()
    try:
        engine = _get_engine()
        result = engine(arr)
        elapse = getattr(result, "elapse_list", None)
        json_data = result.to_json()
    finally:
        _release_request()
    dt = time.perf_counter() - t0

    print(f"{'='*70}")
    print(f"STEP 1 — Raw DET+REC result")
    print(f"{'='*70}")
    print(f"Inference time: {dt*1000:.0f} ms")
    if elapse:
        print(f"Elapse breakdown: {elapse}")

    n_raw = len(json_data) if json_data else 0
    print(f"Raw detection blocks: {n_raw}")

    # ── Step 2: Inspect raw boxes ─────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"STEP 2 — Raw DET boxes (before layout sorting)")
    print(f"{'='*70}")

    if not json_data:
        print("⚠️  NO BOXES DETECTED — engine will fall back to recognition-only")
        print(f"    Fallback path: _recognize_without_detection()")
        print(f"    This means: skip detection, run recognition on the whole image")
        print(f"    OcrRecognition will have NO .lines (no per-line boxes)")

    raw_boxes: list[dict] = []  # normalized raw boxes with coordinates
    for i, item in enumerate(json_data or []):
        box = item.get("box", [])
        txt = item.get("txt", "") or ""
        left, top, right, bottom = ppocr_box_to_bbox(box)
        w, h = right - left, bottom - top
        valid = w > 0 and h > 0

        if args.verbose:
            status = "✓" if valid else "✗ INVALID"
            print(f"  [{i:3d}] {status}  "
                  f"({left:6.1f}, {top:6.1f}) → ({right:6.1f}, {bottom:6.1f})  "
                  f"{w:6.1f}×{h:6.1f}  "
                  f"text={txt[:60]!r}")

        if valid:
            raw_boxes.append({
                "index": i,
                "text": txt,
                "left": left, "top": top,
                "right": right, "bottom": bottom,
                "width": w, "height": h,
                "center_x": (left + right) / 2,
                "center_y": (top + bottom) / 2,
            })

    if not args.verbose and raw_boxes:
        # Print a compact summary
        xs = [b["left"] for b in raw_boxes]
        ys = [b["top"] for b in raw_boxes]
        ws = [b["width"] for b in raw_boxes]
        hs = [b["height"] for b in raw_boxes]
        print(f"  Valid boxes: {len(raw_boxes)} (of {n_raw} raw blocks)")
        print(f"  X range:     {min(xs):.0f} – {max(xs):.0f}")
        print(f"  Y range:     {min(ys):.0f} – {max(ys):.0f}")
        print(f"  Box sizes:   {min(ws):.0f}×{min(hs):.0f} – {max(ws):.0f}×{max(hs):.0f}")
        print(f"  (use --verbose to see every block)")

    invalid_count = n_raw - len(raw_boxes)
    if invalid_count > 0:
        print(f"  ⚠ {invalid_count} block(s) had invalid (zero-area) boxes and will be SKIPPED")

    # ── Step 3: Vertical detection ────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"STEP 3 — Layout direction detection")
    print(f"{'='*70}")

    is_vertical = _is_vertical_json(json_data) if json_data else False
    if is_vertical:
        print(f"  → VERTICAL CJK layout detected (area-weighted tall-box fraction ≥ 0.5)")
        print(f"     Columns sorted right→left, within-column top→bottom")
    else:
        print(f"  → HORIZONTAL layout (default)")
        print(f"     Lines sorted top→bottom, within-line left→right")

    # ── Step 4: Fallback check ────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"STEP 4 — Fallback decision")
    print(f"{'='*70}")

    if not json_data:
        print("  ⚠️  FALLBACK TRIGGERED — json_data is empty")
        print("  → Running _recognize_without_detection() on the content-cropped image")
        t_fb = time.perf_counter()
        fallback_result = _recognize_without_detection(engine, arr)
        dt_fb = time.perf_counter() - t_fb
        print(f"  Fallback result: text={fallback_result.text[:120]!r}...")
        print(f"  Fallback time:   {dt_fb*1000:.0f} ms")
        print(f"  Has lines:       {bool(fallback_result.lines)}  (always False for fallback)")
    else:
        print("  ✓ Boxes detected — normal det+rec pipeline, no fallback needed")

    # ── Step 5: Layout sorting (if boxes exist) ───────────────────────────
    lines = []
    if raw_boxes:
        print(f"\n{'='*70}")
        print(f"STEP 5 — Layout sorting ({'column' if is_vertical else 'line'} clustering)")
        print(f"{'='*70}")

        # Normalize (same as production)
        blocks = [{"text": b["text"], "box": [
            [b["left"], b["top"]], [b["right"], b["top"]],
            [b["right"], b["bottom"]], [b["left"], b["bottom"]],
        ]} for b in raw_boxes]

        normalized = _normalize_blocks(blocks)
        print(f"  After _normalize_blocks: {len(normalized)} blocks "
              f"(filtered {len(blocks) - len(normalized)} junk)")

        # Cluster
        if is_vertical:
            clusters = _greedy_column_cluster(normalized)
            cluster_name = "columns"
        else:
            clusters = _greedy_line_cluster(normalized)
            cluster_name = "lines"

        print(f"  Greedy clustering → {len(clusters)} {cluster_name}:")
        for ci, cluster in enumerate(clusters):
            c_texts = [b["text"][:30] for b in cluster]
            c_ys = [f"{b['top']:.0f}" for b in cluster]
            print(f"    [{ci}] {len(cluster):2d} boxes  "
                  f"y={{{','.join(c_ys)}}}  "
                  f"texts={c_texts}")

        # Build lines
        lines = _build_lines_from_clusters(clusters)

        # CJK spacing
        for line in lines:
            line.text = _apply_cjk_spacing(line.text)

        # Paragraph breaks + Indentation (horizontal only)
        if not is_vertical:
            before_pb = len(lines)
            lines = _apply_paragraph_breaks(lines)
            blank_count = len(lines) - before_pb
            if blank_count:
                print(f"\n  _apply_paragraph_breaks: {blank_count} blank line(s) inserted")
            lines = _apply_indentation(lines)

        print(f"\n  Final reading order ({len(lines)} {cluster_name}):")
        for li, line in enumerate(lines):
            b = line.bounding_box
            preview = line.text[:80].replace("\n", "␤")
            print(f"    [{li:2d}] ({b.x:6.0f},{b.y:6.0f}) {b.width:6.0f}×{b.height:6.0f}  "
                  f"'{preview}'")

    # ── Step 6: Verify against production compose_ppocr_structures ────────
    if raw_boxes:
        print(f"\n{'='*70}")
        print(f"STEP 6 — Cross-check with production compose_ppocr_structures()")
        print(f"{'='*70}")
        prod_lines = compose_ppocr_structures(blocks, is_vertical=is_vertical)
        prod_texts = [ln.text for ln in prod_lines]
        our_texts = [ln.text for ln in lines]
        match = prod_texts == our_texts
        print(f"  Match: {'✓ YES' if match else '✗ NO — investigate divergence'}")
        if not match:
            for i, (a, b) in enumerate(zip(prod_texts, our_texts)):
                if a != b:
                    print(f"  diff [{i}]: prod={a!r}  vs  our={b!r}")

    # ── Draw annotated output ─────────────────────────────────────────────
    if args.save:
        out_dir = image_path.parent
        stem = image_path.stem
        out_path = out_dir / f"{stem}_det_debug.png"

        annotated = cv_image.copy()

        # Draw raw DET boxes (cyan) with original indices
        for b in raw_boxes:
            _draw_labeled_box(
                annotated,
                b["left"], b["top"], b["right"], b["bottom"],
                f"D{b['index']}",
                RAW_BOX_COLOR,
                thickness=1,
            )

        # Draw sorted line boxes (magenta) with reading-order index
        for li, line in enumerate(lines):
            bb = line.bounding_box
            _draw_labeled_box(
                annotated,
                bb.x, bb.y, bb.x + bb.width, bb.y + bb.height,
                f"L{li}",
                SORTED_LINE_COLOR,
                thickness=2,
            )

        # If fallback, draw a banner
        if not json_data:
            h, w = annotated.shape[:2]
            cv2.rectangle(annotated, (0, 0), (w, 40), (0, 0, 255), -1)
            cv2.putText(
                annotated, "FALLBACK: no DET boxes — rec-only",
                (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
            )

        cv2.imwrite(str(out_path), annotated)
        print(f"\nAnnotated image saved: {out_path}")
        print(f"  Legend: D# = raw DET box index (cyan)")
        if lines:
            print(f"          L# = sorted line index in reading order (magenta)")

    # ── Cleanup ───────────────────────────────────────────────────────────
    import gc
    del arr, result, json_data
    gc.collect()

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
