"""Verify a minimal cv2.pyd is a drop-in for rapidocr.

Asserts every symbol rapidocr needs is present and runs a functional smoke
over the actual OCR hot-path operations (resize / cvtColor / dilate /
findContours / minAreaRect / warpPerspective / copyMakeBorder / imencode).

By default verifies the repo-root ``cv2/`` package (the minimal cv2 that
development/production import).  Pass ``--pyd`` to verify a freshly built
pyd before it is copied into place -- the pyd's dir is path-injected ahead
of the repo-root package so the bare pyd loads.

Run after scripts/build/build_minimal_opencv.ps1, before trusting the build.

Usage:
  python scripts/build/verify_minimal_cv2.py
  python scripts/build/verify_minimal_cv2.py --pyd <path-to-minimal-cv2.pyd>
"""

import argparse
import importlib
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
# Repo-root cv2/ (the minimal package) must win over site-packages.  When this
# script is run as ``python scripts/build/verify_minimal_cv2.py`` sys.path[0]
# is scripts/build/, not the repo root, so insert the repo root explicitly.
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Must stay in sync with KNOWN_SYMBOLS in tests/test_cv2_symbol_audit.py and
# with BUILD_LIST in scripts/build/build_minimal_opencv.ps1.  These are the cv2
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
    """Path-inject a bare minimal pyd ahead of the repo-root cv2/ package.

    A bare ``cv2.cp313-win_amd64.pyd`` on sys.path imports as module ``cv2``
    (extension-module finder matches ``<name>.<tag>.pyd``).  Putting its dir at
    sys.path[0] makes it win over the repo-root ``cv2/`` package; popping any
    cached ``cv2`` from sys.modules ensures a fresh load.  Used only for the
    ``--pyd`` path (verifying a freshly built pyd before it is copied into
    place); without ``--pyd`` the repo-root cv2/ package is imported directly.
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
                         "(default: verify the repo-root cv2/ package)")
    args = ap.parse_args()

    if args.pyd is not None:
        cv2 = inject_minimal_cv2(args.pyd)
    else:
        import cv2  # noqa: F401 -- repo-root cv2/ wins via sys.path[0]
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
