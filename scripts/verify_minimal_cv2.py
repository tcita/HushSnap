"""Verify a minimal cv2.pyd is a drop-in for rapidocr.

Path-injects the minimal pyd ahead of site-packages, purges any cached cv2,
then asserts every symbol rapidocr needs is present and runs a functional
smoke over the actual OCR hot-path operations (resize / cvtColor / dilate /
findContours / minAreaRect / warpPerspective / copyMakeBorder / imencode).

Run after scripts/build_minimal_opencv.ps1, before trusting the build.

Usage:  python scripts/verify_minimal_cv2.py --pyd <path-to-minimal-cv2.pyd>
"""

import argparse
import importlib
import sys
from pathlib import Path

# Must stay in sync with KNOWN_SYMBOLS in tests/test_cv2_symbol_audit.py and
# with BUILD_LIST in scripts/build_minimal_opencv.ps1.  These are the cv2
# symbols rapidocr touches at runtime.
REQUIRED_SYMBOLS = [
    # core
    "ROTATE_180", "add", "bitwise_and", "bitwise_not", "invert", "mean", "rotate",
    # imgproc
    "BORDER_CONSTANT", "BORDER_REPLICATE", "CHAIN_APPROX_SIMPLE",
    "COLOR_GRAY2BGR", "COLOR_RGB2BGR", "FONT_HERSHEY_SIMPLEX",
    "INTER_CUBIC", "INTER_LINEAR", "RETR_LIST",
    "boxPoints", "copyMakeBorder", "cvtColor", "dilate", "fillPoly",
    "findContours", "getPerspectiveTransform", "minAreaRect",
    "polylines", "putText", "resize", "warpPerspective",
    # imgcodecs
    "imencode", "imwrite",
]


def inject_minimal_cv2(pyd_path: Path):
    """Prepend the pyd's dir to sys.path and force (re)import of cv2.

    A bare ``cv2.cp313-win_amd64.pyd`` on sys.path imports as module ``cv2``
    (extension-module finder matches ``<name>.<tag>.pyd``).  Putting its dir at
    sys.path[0] makes it win over the site-packages ``cv2/`` package; popping
    any cached ``cv2`` from sys.modules ensures a fresh load.
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
    args = ap.parse_args()

    cv2 = inject_minimal_cv2(args.pyd)
    print(f"cv2 file:    {getattr(cv2, '__file__', '?')}")
    print(f"cv2 version: {getattr(cv2, '__version__', '?')}")

    missing = [s for s in REQUIRED_SYMBOLS if not hasattr(cv2, s)]
    if missing:
        print(f"FAIL: missing {len(missing)} symbol(s): {missing}")
        return 1
    print(f"symbols: {len(REQUIRED_SYMBOLS)}/{len(REQUIRED_SYMBOLS)} present")

    # Functional smoke -- the real operations on rapidocr's OCR hot path.
    import numpy as np

    img = (np.random.rand(40, 40, 3) * 255).astype(np.uint8)
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)                       # color convert
    _ = cv2.resize(bgr, (20, 20), interpolation=cv2.INTER_LINEAR)    # det resize
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)                     # findContours needs CV_8UC1
    bin_ = cv2.dilate(gray, np.ones((2, 2), np.uint8))               # DB post-process
    cnts, _ = cv2.findContours(bin_, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        rect = cv2.minAreaRect(cnts[0])                              # det box
        _ = cv2.boxPoints(rect)
    M = cv2.getPerspectiveTransform(
        np.array([[0, 0], [9, 0], [9, 9], [0, 9]], dtype=np.float32),
        np.array([[1, 1], [8, 1], [8, 8], [1, 8]], dtype=np.float32))
    _ = cv2.warpPerspective(bgr, M, (10, 10))                        # perspective crop
    _ = cv2.copyMakeBorder(bgr, 1, 1, 1, 1, cv2.BORDER_REPLICATE)    # padding
    _ = cv2.rotate(bgr, cv2.ROTATE_180)                              # cls rotation
    ok, buf = cv2.imencode(".png", bgr)                              # imgcodecs encode
    assert ok and buf.size > 0, "imencode failed"

    print("smoke: cvtColor/resize/dilate/findContours/minAreaRect/boxPoints/"
          "warpPerspective/copyMakeBorder/rotate/imencode OK")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
