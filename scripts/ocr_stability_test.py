"""OCR inference stability test — call OcrService.recognize() N times on one image.

Answers: "is the OCR result for the same image deterministic, or does it vary
run to run?" This runs the REAL product OCR path (OcrService.recognize →
run_minimal_pipeline → recognize_ppocr_qimage) but WITHOUT the screenshot /
hotkey / thumbnail / popup / terminal machinery that polluted earlier
end-to-end tests. Pure inference, one fixed input, repeated.

Why this exists: an end-to-end stress test showed text_len jumping between 625
and 4000+ across rounds — but that was because each screenshot captured a
different set of on-screen windows (the running terminal, leftover popups),
not because OCR itself was unstable. This script removes every variable except
the engine: same bytes in every round, so any output difference is the engine's
own non-determinism.

Usage:
    python scripts/ocr_stability_test.py --image "C:\\path\\to\\dense.png" --rounds 100

    # compare just the recognized text vs. also the per-line boxes:
    python scripts/ocr_stability_test.py --image img.png --rounds 50 --show-lines

Output:
    - per-round text_len + a hash of the recognized text
    - per-round timing
    - a summary: how many distinct results, the variants, whether boxes varied
    - saves every distinct result to ocr_stability_results/

Notes:
    - A QApplication (offscreen) is required because the OCR path uses QImage /
      QPixmap. We construct one but never run its event loop.
    - The engine is a singleton (ppocr._get_engine); the first call loads the
      ONNX models (warmup), subsequent calls reuse them. The first round's
      timing therefore includes warmup and is reported separately.
    - ONNX inference is floating-point and some operators are non-deterministic
      across runs by default; small differences on borderline characters are
      expected. This test quantifies HOW different, and whether the differences
      are small (a few chars) or large (whole regions changing).
"""

import argparse
import hashlib
import sys
import time
from collections import Counter
from pathlib import Path

# Project root on path so `import hushsnap...` works when run as a script.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def text_hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def qimage_copy(src):
    """Return an independent deep copy of a QImage (detached from src's data)."""
    from PyQt6 import QtGui
    # .copy() returns a deep copy with its own buffer, so the engine never
    # shares mutable pixel state across rounds.
    return QtGui.QImage(src).copy()


def main():
    ap = argparse.ArgumentParser(description="OCR inference stability test (same image, N runs).")
    ap.add_argument("--image", type=str, required=True, help="path to the test image (PNG/JPG).")
    ap.add_argument("--rounds", type=int, default=100, help="number of recognize() calls (default 100).")
    ap.add_argument("--language-tag", type=str, default="", help="language tag passed to the engine (default '').")
    ap.add_argument("--show-lines", action="store_true",
                    help="also hash the per-line bounding boxes, not just the text.")
    ap.add_argument("--save-variants", action="store_true", default=True,
                    help="save each distinct result text to ocr_stability_results/ (default on).")
    ap.add_argument("--no-save-variants", dest="save_variants", action="store_false")
    args = ap.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"ERROR: image not found: {image_path}")
        return 2

    # ── construct a QApplication (offscreen) so QImage/QPixmap work ──────────
    # The OCR path (run_minimal_pipeline, recognize_ppocr_qimage) uses QtGui
    # image types, which require a QGuiApplication. Offscreen platform keeps it
    # headless — no windows appear. We never exec the event loop.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    # Load the image once. OcrRequest.pixmap accepts QImage; recognize() passes
    # it straight to run_minimal_pipeline which normalizes format/DPR. We reload
    # from the SAME QImage bytes every round (a fresh copy each time so there is
    # no chance of shared mutable state between rounds).
    from hushsnap.ocr import OcrRequest, OcrService
    from hushsnap.constants import OCR_ENGINE_PPOCR

    service = OcrService()
    source_image = QtGui.QImage(str(image_path))
    if source_image.isNull():
        print(f"ERROR: QImage could not load: {image_path}")
        return 2
    print(f"image: {image_path}  ({source_image.width()}x{source_image.height()})")
    print(f"rounds={args.rounds}  engine={OCR_ENGINE_PPOCR}  language_tag={args.language_tag!r}")
    print("first round includes model warmup (loading ONNX)...\n")

    # ── run N rounds ──────────────────────────────────────────────────────────
    results = []  # list of (round, elapsed_s, text, text_hash, lines_hash, error)
    for i in range(1, args.rounds + 1):
        # Fresh copy each round so the engine never sees shared state.
        img = qimage_copy(source_image)
        request = OcrRequest(pixmap=img, engine=OCR_ENGINE_PPOCR, language_tag=args.language_tag)
        t0 = time.perf_counter()
        response = service.recognize(request)
        dt = time.perf_counter() - t0

        text = response.text or ""
        err = response.error or ""
        th = text_hash(text)
        lh = ""
        if args.show_lines and response.recognition and response.recognition.lines:
            # Hash line text + box geometry to catch box-level differences even
            # when the composed text is identical.
            parts = []
            for ln in response.recognition.lines:
                b = ln.bounding_box
                parts.append(f"{ln.text}|{b.x:.1f},{b.y:.1f},{b.width:.1f},{b.height:.1f}")
            lh = text_hash("\n".join(parts))
        results.append((i, dt, text, th, lh, err))
        tag = f"warmup" if i == 1 else f"{dt:.3f}s"
        print(f"  [round {i:4d}] {tag:8} len={len(text):5d} text_hash={th} "
              f"{'lines_hash=' + lh if lh else ''} {'ERR=' + err if err else ''}")

    # ── summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    text_hashes = [r[3] for r in results]
    line_hashes = [r[4] for r in results if r[4]]
    errors = [r for r in results if r[5]]

    distinct_text = Counter(text_hashes)
    print(f"rounds:                 {len(results)}")
    print(f"errors:                 {len(errors)}")
    print(f"distinct text results:  {len(distinct_text)}")
    if line_hashes:
        print(f"distinct line/box sets: {len(set(line_hashes))}")

    timings = [r[1] for r in results[1:]]  # exclude warmup
    if timings:
        print(f"timing (excl warmup):   min={min(timings):.3f}s  max={max(timings):.3f}s  "
              f"avg={sum(timings)/len(timings):.3f}s")
    print(f"warmup round:           {results[0][1]:.3f}s")

    print(f"\ndistinct text variants ({len(distinct_text)}):")
    for h, count in distinct_text.most_common():
        # find one example text for this hash
        example = next(r[2] for r in results if r[3] == h)
        preview = example[:60].replace("\n", " ⏎ ")
        print(f"  hash={h}  count={count:4d}  len={len(example):5d}  preview={preview!r}")

    # ── save variants ─────────────────────────────────────────────────────────
    if args.save_variants and len(distinct_text) > 1:
        out_dir = _project_root / "ocr_stability_results"
        out_dir.mkdir(exist_ok=True)
        seen = set()
        for r in results:
            if r[3] in seen:
                continue
            seen.add(r[3])
            dest = out_dir / f"variant_{r[3]}_len{len(r[2])}.txt"
            dest.write_text(r[2], encoding="utf-8")
        print(f"\nsaved {len(seen)} distinct variants to: {out_dir}")
    elif args.save_variants:
        out_dir = _project_root / "ocr_stability_results"
        out_dir.mkdir(exist_ok=True)
        dest = out_dir / f"variant_{results[0][3]}_len{len(results[0][2])}.txt"
        dest.write_text(results[0][2], encoding="utf-8")
        print(f"\nsaved the single result to: {dest}")

    # exit code: 0 if deterministic (1 distinct) and no errors, else 1
    return 0 if (len(distinct_text) == 1 and not errors) else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
