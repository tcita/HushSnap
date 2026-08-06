"""Dedicated OCR pipeline entry point — debug single images or batch a folder.

Runs the full PP-OCR pipeline (OcrService.recognize) on image files, producing
text identical to the thumbnail-popup path.  Prints recognized text to stdout;
images are separated by divider lines so output never runs together.

Usage:
    # OCR a single image:
    python run_ocr.py --image screenshot.png

    # OCR every supported image in this folder:
    python run_ocr.py

    # OCR a specific folder:
    python run_ocr.py --folder C:\\path\\to\\images
"""

import argparse
import sys
import time
from pathlib import Path

# Project root on path so `import hushsnap...` works when run as a script.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"})
_DIVIDER = "=" * 60


def _init_qapp():
    """Create a headless QApplication so QImage/QPixmap work without a display."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _collect_images(folder: Path) -> list[Path]:
    """Return sorted list of supported image paths in *folder*."""
    images = []
    for child in sorted(folder.iterdir()):
        if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
            images.append(child)
    return images


def _ocr_image(image_path: Path) -> tuple[str, str]:
    """Run the thumbnail-popup OCR pipeline on *image_path*.

    Returns (text, error).  On success *error* is "" and *text* is the
    recognized string.  On failure *error* is non-empty and *text* is "".
    """
    from PIL import Image

    from hushsnap.ocr import OcrRequest, OcrService
    from hushsnap.constants import OCR_ENGINE_PPOCR

    pil_img = Image.open(image_path)
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")

    request = OcrRequest(pixmap=pil_img, engine=OCR_ENGINE_PPOCR)

    service = OcrService()
    response = service.recognize(request)
    if response.error:
        return "", response.error
    return response.text, ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="OCR images through the full PP-OCR pipeline (OcrService.recognize)"
    )
    ap.add_argument(
        "--image", type=str, default=None,
        help="OCR a single image file (instead of scanning a folder)",
    )
    ap.add_argument(
        "--folder", type=str, default=None,
        help="Folder to scan for images (default: script's own directory)",
    )
    args = ap.parse_args()

    # ── Resolve image source ──────────────────────────────────────────────
    if args.image:
        img_path = Path(args.image).resolve()
        if not img_path.is_file():
            print(f"ERROR: not a file: {img_path}", file=sys.stderr)
            return 2
        if img_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            print(f"ERROR: unsupported format: {img_path.suffix}", file=sys.stderr)
            print(f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}", file=sys.stderr)
            return 2
        images = [img_path]
        single = True
    else:
        folder = Path(args.folder).resolve() if args.folder else Path(__file__).resolve().parent
        if not folder.is_dir():
            print(f"ERROR: not a directory: {folder}", file=sys.stderr)
            return 2
        images = _collect_images(folder)
        if not images:
            print(f"No supported images found in {folder}", file=sys.stderr)
            print(f"Supported extensions: {', '.join(sorted(SUPPORTED_EXTENSIONS))}", file=sys.stderr)
            return 0
        single = False

    # ── Init ──────────────────────────────────────────────────────────────
    _init_qapp()

    # Warm up engine before the first image so per-image timing is pure
    # inference.  Without this the first image would include ONNX model
    # loading (~200-500ms) in its elapsed time.
    t_warm = time.perf_counter()
    from hushsnap.ocr.ppocr import get_ppocr_engine
    get_ppocr_engine()
    dt_warm = time.perf_counter() - t_warm
    print(f"Engine loaded ({dt_warm*1000:.0f}ms)", file=sys.stderr)
    print(file=sys.stderr)

    # ── Process ───────────────────────────────────────────────────────────
    results: list[tuple[str, float, int, str]] = []  # (name, elapsed_ms, chars, error)

    for i, img_path in enumerate(images, 1):
        t0 = time.perf_counter()
        try:
            text, error = _ocr_image(img_path)
        except Exception as exc:
            dt = (time.perf_counter() - t0) * 1000
            results.append((img_path.name, dt, 0, str(exc)))
            print(f"{_DIVIDER}", file=sys.stderr)
            print(f"FAIL [{i}/{len(images)}] {img_path.name}  ({dt:.0f}ms)", file=sys.stderr)
            print(f"  {exc}", file=sys.stderr)
            continue

        dt = (time.perf_counter() - t0) * 1000
        if error:
            results.append((img_path.name, dt, 0, error))
            print(f"{_DIVIDER}", file=sys.stderr)
            print(f"FAIL [{i}/{len(images)}] {img_path.name}  ({dt:.0f}ms)", file=sys.stderr)
            print(f"  {error}", file=sys.stderr)
            continue

        results.append((img_path.name, dt, len(text), ""))

        if not single:
            # Batch mode: divider + header so output from different images
            # never runs together.
            print(f"{_DIVIDER}")
            print(f"[{i}/{len(images)}] {img_path.name}  ({dt:.0f}ms, {len(text)} chars)")
            print(f"{_DIVIDER}")

        print(text)

        # Blank line between images in batch mode (but not after the last)
        if not single and i < len(images):
            print()

    # ── Summary ───────────────────────────────────────────────────────────
    ok_count = sum(1 for r in results if not r[3])
    fail_count = sum(1 for r in results if r[3])

    if not single:
        print(f"{_DIVIDER}")
        print(f"Processed {len(images)}  ok={ok_count}  fail={fail_count}")

    if fail_count:
        print(file=sys.stderr)
        print("FAILURES:", file=sys.stderr)
        for r in results:
            if r[3]:
                print(f"  {r[0]}: {r[3]}", file=sys.stderr)

    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
