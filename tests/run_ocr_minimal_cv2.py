"""End-to-end OCR regression with a minimal cv2.pyd path-injected.

Replaces cv2 in-process with the minimal build, then runs the full PP-OCR
pipeline (OcrService.recognize -- the same path the thumbnail popup uses) over
ocr_batch/*.png.  When the minimal build is a true drop-in, output is identical
to the system-cv2 run produced by ``ocr_batch/run_ocr.py``.

Run after scripts/verify_minimal_cv2.py passes.  Compare char counts / text
against a fresh ``python ocr_batch/run_ocr.py`` to confirm zero regression.

Usage:  python tests/run_ocr_minimal_cv2.py --pyd <path-to-minimal-cv2.pyd>
"""

import argparse
import importlib
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def inject_minimal_cv2(pyd_path: Path):
    """Prepend the pyd's dir to sys.path and force (re)import of cv2.

    Must run BEFORE any rapidocr/hushsnap import so their ``import cv2`` picks
    up the minimal pyd rather than the site-packages wheel.
    """
    if not pyd_path.is_file():
        raise SystemExit(f"minimal cv2 pyd not found: {pyd_path}")
    sys.path.insert(0, str(pyd_path.parent))
    sys.modules.pop("cv2", None)
    importlib.invalidate_caches()
    import cv2  # noqa: F401
    return cv2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pyd", required=True, type=Path,
                    help="path to the minimal cv2.cp313-win_amd64.pyd")
    ap.add_argument("--folder", type=Path,
                    default=_project_root / "ocr_batch",
                    help="folder of images to OCR (default: ocr_batch/)")
    args = ap.parse_args()

    cv2 = inject_minimal_cv2(args.pyd)
    print(f"[minimal-cv2] file={getattr(cv2, '__file__', '?')}", file=sys.stderr)
    print(f"[minimal-cv2] version={getattr(cv2, '__version__', '?')}",
          file=sys.stderr)

    # Reuse the real OCR pipeline entry point -- no duplicated logic.
    ocr_batch = _project_root / "ocr_batch"
    sys.path.insert(0, str(ocr_batch))
    from run_ocr import _init_qapp, _ocr_image, _collect_images  # type: ignore

    _init_qapp()
    from hushsnap.ocr.ppocr import _get_engine
    _get_engine()  # warm so per-image timing reflects inference, not model load

    images = _collect_images(args.folder)
    if not images:
        print(f"no supported images in {args.folder}", file=sys.stderr)
        return 2

    failures = 0
    divider = "=" * 60
    for i, img_path in enumerate(images, 1):
        text, error = _ocr_image(img_path)
        if error:
            failures += 1
            print(f"{divider}", file=sys.stderr)
            print(f"FAIL [{i}/{len(images)}] {img_path.name}: {error}",
                  file=sys.stderr)
            continue
        print(divider)
        print(f"[{i}/{len(images)}] {img_path.name}  ({len(text)} chars)")
        print(divider)
        print(text)
        if i < len(images):
            print()

    print(divider)
    print(f"Processed {len(images)}  ok={len(images) - failures}  "
          f"fail={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
