"""Guard against rapidocr pulling cv2 symbols from pruned OpenCV modules.

HushSnap ships a minimal OpenCV build -- only core + imgproc + imgcodecs are
compiled in (see scripts/build/build_minimal_opencv.ps1).  The official opencv-python
wheel is an 82 MB monolithic cv2.pyd whose other modules (dnn, ml, video,
features2d, calib3d, flann, photo, ...) are dead weight rapidocr never touches.
PyInstaller excludes cannot help (one .pyd, not a split package like PyQt6).

rapidocr is the *only* runtime cv2 consumer: HushSnap's own shipped code never
calls cv2 directly (the references in hushsnap/ are comments), and image
*decoding* is PIL-mediated (rapidocr LoadImage uses Image.open), so cv2 is on
the hot path only for geometry/color/contour work in imgproc + a little core.

This test makes "the rest is safe redundancy" a *continuously verified* fact
rather than a one-time judgement.  It parses rapidocr's installed package
(plus hushsnap/) with ``ast`` and collects every ``cv2.<attr>`` access, then
asserts each one is a known symbol mapped to an allowed module.  If a rapidocr
upgrade starts using e.g. ``cv2.dnn`` or ``cv2.CascadeClassifier``, the test
fails LOUD at build time -- before a silently-broken minimal cv2 reaches users.

New symbols (from a rapidocr bump) are not in KNOWN_SYMBOLS, so they fail until
a human classifies them: add to KNOWN_SYMBOLS if the symbol lives in
core/imgproc/imgcodecs, or flag the upgrade as incompatible with the minimal
build.  AST parsing ignores comments and docstrings, so only real attribute
accesses in executable code are counted (no false positives).
"""

import ast
import warnings
from pathlib import Path

import pytest

# Mirrors BUILD_LIST in scripts/build/build_minimal_opencv.ps1.  A symbol is safe to
# ship iff its module is in this set.
ALLOWED_MODULES = frozenset({"core", "imgproc", "imgcodecs"})

# Every cv2.<symbol> rapidocr is known to use, mapped to its OpenCV module.
# Verified against the installed rapidocr package on 2026-07-29.  A symbol
# found in source but missing here fails the test -- forcing a human to
# classify it before the minimal build can be trusted.  The module tag is
# documentation/secondary; the load-bearing assertion is "no unknown symbol".
KNOWN_SYMBOLS: dict[str, str] = {
    # --- core: array ops + rotation ---
    "ROTATE_180": "core",
    "add": "core",
    "bitwise_and": "core",
    "bitwise_not": "core",
    "invert": "core",
    "mean": "core",
    "rotate": "core",
    # --- imgproc: geometry / color / drawing / contours ---
    "BORDER_CONSTANT": "imgproc",
    "BORDER_REPLICATE": "imgproc",
    "CHAIN_APPROX_SIMPLE": "imgproc",
    "COLOR_GRAY2BGR": "imgproc",
    "COLOR_RGB2BGR": "imgproc",
    "FONT_HERSHEY_SIMPLEX": "imgproc",
    "INTER_CUBIC": "imgproc",
    "INTER_LINEAR": "imgproc",
    "RETR_LIST": "imgproc",
    "boxPoints": "imgproc",
    "copyMakeBorder": "imgproc",
    "cvtColor": "imgproc",
    "dilate": "imgproc",
    "fillPoly": "imgproc",
    "findContours": "imgproc",
    "getPerspectiveTransform": "imgproc",
    "minAreaRect": "imgproc",
    "polylines": "imgproc",
    "putText": "imgproc",
    "resize": "imgproc",
    "warpPerspective": "imgproc",
    # --- imgcodecs: image encode (debug-vis path only; decode is PIL) ---
    "imencode": "imgcodecs",
    "imwrite": "imgcodecs",
}


def _roots_to_scan() -> list[Path]:
    """Source trees whose cv2 usage must stay within the minimal build.

    rapidocr: the installed dependency -- the silent-upgrade risk this test
        primarily guards.  Skipped if not importable.
    hushsnap: our own shipped runtime.  Today it has zero real cv2 accesses
        (only comments), but scanning it future-proofs against someone adding
        a cv2.dnn call in shipped code.
    scripts/ and tests/ are deliberately excluded: they are dev-only tooling
    not shipped, and legitimately use cv2 features the minimal build lacks.
    """
    roots: list[Path] = []
    try:
        import rapidocr  # noqa: F401
        roots.append(Path(rapidocr.__file__).resolve().parent)
    except ImportError:
        pass
    project = Path(__file__).resolve().parent.parent
    hushsnap = project / "hushsnap"
    if hushsnap.is_dir():
        roots.append(hushsnap)
    return roots


def _cv2_attrs_in_source(roots: list[Path]) -> set[str]:
    """All ``cv2.<attr>`` accesses across *roots*, collected via AST.

    AST parsing ignores comments and docstrings, so only real attribute
    accesses in executable code are counted.  Assumes cv2 is accessed as
    ``cv2.<attr>`` (true for rapidocr: ``import cv2`` throughout, no
    ``from cv2 import`` / ``getattr`` -- verified by grep).
    """
    attrs: set[str] = set()
    for root in roots:
        for py in root.rglob("*.py"):
            try:
                source = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # rapidocr's own source contains invalid escape sequences (e.g.
            # "\d" in regex strings) that raise SyntaxWarning on parse.  They
            # are rapidocr's pre-existing issue and do not affect the AST walk
            # -- suppress so test output stays clean.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                try:
                    tree = ast.parse(source, filename=str(py))
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "cv2"
                ):
                    attrs.add(node.attr)
    return attrs


def test_rapidocr_uses_only_allowed_cv2_modules():
    """Every cv2 symbol rapidocr/hushsnap touches must be known + allowed."""
    roots = _roots_to_scan()
    if not roots:
        pytest.skip("rapidocr not importable and hushsnap/ missing")
    found = _cv2_attrs_in_source(roots)

    unknown = sorted(found - set(KNOWN_SYMBOLS))
    assert not unknown, (
        "rapidocr/hushsnap now reference cv2 symbol(s) not in the minimal-build "
        f"allowlist: {unknown}.  Classify each -- add to KNOWN_SYMBOLS with its "
        "module if it lives in core/imgproc/imgcodecs, or flag the rapidocr "
        "upgrade as incompatible with the minimal cv2 build."
    )

    # Secondary guard: a known symbol mis-tagged to a pruned module.
    bad_module = {
        s: m for s, m in KNOWN_SYMBOLS.items()
        if m not in ALLOWED_MODULES and s in found
    }
    assert not bad_module, (
        f"allowlisted symbol(s) map to a pruned module: {bad_module}"
    )


def test_known_symbols_actually_exist_in_cv2():
    """Sanity: every allowlisted symbol is a real cv2 attribute (typo guard).

    Uses the full installed cv2, which is a superset of the minimal build --
    existence here does NOT mean the minimal build has it.  The end-to-end
    scripts/build/verify_minimal_cv2.py + tests/run_ocr_minimal_cv2.py cover that.
    """
    roots = _roots_to_scan()
    if not roots:
        pytest.skip("rapidocr not importable and hushsnap/ missing")
    try:
        import cv2
    except ImportError:
        pytest.skip("cv2 not importable")
    missing = sorted(s for s in KNOWN_SYMBOLS if not hasattr(cv2, s))
    assert not missing, (
        f"allowlist references non-existent cv2 symbol(s): {missing}"
    )
