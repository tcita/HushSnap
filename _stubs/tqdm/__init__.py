"""
STUB — NOT THE REAL ``tqdm`` LIBRARY
=====================================

This file REPLACES the real ``tqdm`` package in PyInstaller builds, found
via ``pathex=['_stubs']`` in HushSnap.spec.

rapidocr.utils.download_file does ``from tqdm import tqdm`` at module level
and uses it inside ``DownloadFile._save_response_with_progress()`` — a code
path that ``requests.get()`` (also stubbed) never reaches.

Every method raises ``RuntimeError`` so that if the download path is somehow
reached despite the requests stub, the failure is loud and traceable rather
than a silent no-op.
"""


class _TqdmDisabledError(RuntimeError):
    """Raised when tqdm is instantiated in this build — should be unreachable
    because ``requests.get()`` (also stubbed) raises first."""
    pass


class tqdm:
    def __init__(self, total=0, unit="", unit_scale=False, disable=False):
        raise _TqdmDisabledError(
            "tqdm() was instantiated — this means rapidocr reached the "
            "download-progress code path.  This should be impossible because "
            "requests.get() is stubbed and raises first.  Check "
            "_stubs/requests/__init__.py for the root cause."
        )

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        pass

    def update(self, n=1):
        raise _TqdmDisabledError("tqdm.update() called — download path reached.")
