"""End-to-end OCR regression with the minimal cv2.

Runs the full PP-OCR pipeline (OcrService.recognize -- the same path the
thumbnail popup uses) over ocr_batch/*.png.  By default this uses the
repo-root ``cv2/`` package (the minimal cv2 development/production import);
pass ``--pyd`` to path-inject a freshly built pyd before it is copied into
place.  When the minimal build is a true drop-in, output is identical to the
system-cv2 run produced by ``ocr_batch/run_ocr.py``.

Run after scripts/build/verify_minimal_cv2.py passes.  Compare char counts / text
against a fresh ``python ocr_batch/run_ocr.py`` to confirm zero regression.

Usage:
  python tests/run_ocr_minimal_cv2.py
  python tests/run_ocr_minimal_cv2.py --pyd <path-to-minimal-cv2.pyd>
"""

import argparse
import importlib
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
# Repo-root cv2/ (the minimal package) must win over site-packages.  When this
# script is run as ``python tests/run_ocr_minimal_cv2.py`` sys.path[0] is
# tests/, not the repo root, so insert the repo root explicitly and
# unconditionally (the ``not in`` guard could leave site-packages ahead).
sys.path.insert(0, str(_project_root))


def inject_minimal_cv2(pyd_path: Path):
    """Path-inject a bare minimal pyd ahead of the repo-root cv2/ package.

    Used only for the ``--pyd`` path (verifying a freshly built pyd before it
    is copied into place); without ``--pyd`` the repo-root cv2/ package is
    imported directly (it already wins via sys.path[0]).
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
    ap.add_argument("--pyd", type=Path, default=None,
                    help="path to a minimal cv2.cp313-win_amd64.pyd to verify "
                         "(default: use the repo-root cv2/ package)")
    ap.add_argument("--folder", type=Path,
                    default=_project_root / "ocr_batch",
                    help="folder of images to OCR (default: ocr_batch/)")
    args = ap.parse_args()

    if args.pyd is not None:
        cv2 = inject_minimal_cv2(args.pyd)
    else:
        import cv2  # noqa: F401 -- repo-root cv2/ wins via sys.path[0]
    print(f"[minimal-cv2] file={getattr(cv2, '__file__', '?')}", file=sys.stderr)
    print(f"[minimal-cv2] version={getattr(cv2, '__version__', '?')}",
          file=sys.stderr)

    # Reuse the real OCR pipeline entry point -- no duplicated logic.
    ocr_batch = _project_root / "ocr_batch"
    sys.path.insert(0, str(ocr_batch))
    from run_ocr import _init_qapp, _ocr_image, _collect_images  # type: ignore

    _init_qapp()
    from hushsnap.ocr.ppocr import get_ppocr_engine
    get_ppocr_engine()  # warm so per-image timing reflects inference, not model load

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
