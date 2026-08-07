"""
STUB — NOT THE REAL ``requests`` LIBRARY
=========================================

This file REPLACES the real ``requests`` package in PyInstaller builds.
PyInstaller picks it up via ``pathex=['_stubs']`` in HushSnap.spec instead
of the ~2.5 MB requests + urllib3 + certifi + charset_normalizer + idna
chain from site-packages.

**Why this exists**

rapidocr does ``import requests`` at module level in two files::

    rapidocr/utils/download_file.py   (model download)
    rapidocr/utils/load_image.py      (URL → image loading)

HushSnap never triggers either path:
- ONNX models are pre-bundled → download skipped (checksum match).
- OCR input is always a numpy array (QImage → ndarray), never a URL string.

In an MSIX install the model directory is read-only
(``C:\\Program Files\\WindowsApps\\...``), so even if a download were
triggered the file write would fail with PermissionError — the HTTP
request leaks to the network for zero benefit.

**What happens if this stub is reached**

Every callable entry point raises ``NetworkDisabledError`` with a
descriptive message.  If you see this error::

    1. Check that all .onnx files exist under rapidocr/models/ in the
       install directory.
    2. Check that their SHA256 matches default_models.yaml.
    3. If a rapidocr upgrade added new import-time network calls, this
       stub may need updating (grep for "import requests" in rapidocr).

**Maintenance**

When upgrading rapidocr, verify the actual API surface it uses::

    grep -rn "requests[.]"  $VENV/Lib/site-packages/rapidocr/
    grep -rn "import requests" $VENV/Lib/site-packages/rapidocr/

Update this stub if new attributes/methods are referenced at import time.
"""


class NetworkDisabledError(RuntimeError):
    """Raised when code attempts any network operation in this build.

    HushSnap MSIX builds ship all ONNX models pre-bundled and run from a
    read-only install path.  There is no scenario where a network request
    can succeed AND produce a usable result — the downloaded file cannot
    be written back to the install directory.
    """
    pass


class Response:
    """Type-compatible stub so ``requests.Response`` resolves in rapidocr's
    type hints (``-> requests.Response``).  Never instantiated because
    ``get()`` always raises ``NetworkDisabledError``."""
    headers: dict = {}
    raw: bytes = b""


class RequestException(Exception):
    """Type-compatible stub so ``except requests.RequestException`` compiles."""
    pass


def get(url: str, stream: bool = False, timeout: int = 60):
    raise NetworkDisabledError(
        f"Network is disabled in this build.\n\n"
        f"rapidocr attempted requests.get({url!r}) — this should never happen "
        f"because all ONNX models are pre-bundled and SHA256-verified at startup.\n\n"
        f"If you see this error, a model file is missing or corrupted in the "
        f"install directory.  Check that the following files exist and their "
        f"SHA256 matches rapidocr/default_models.yaml:\n"
        f"  - PP-OCRv6_det_small.onnx\n"
        f"  - PP-OCRv6_rec_small.onnx\n"
        f"  - ch_ppocr_mobile_v2.0_cls_mobile.onnx\n\n"
        f"This stub lives at _stubs/requests/__init__.py and is selected by "
        f"pathex=['_stubs'] in HushSnap.spec."
    )
