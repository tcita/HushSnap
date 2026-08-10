"""Temp-file cache for drag-and-drop operations.

A drag source on Windows needs a real file path — QMimeData can't carry raw
pixel data that every drop target understands.  This module writes a throwaway
PNG and manages the cache directory lifecycle.

Rotation keeps the last 2 files so a slow upload (e.g. a browser on a sluggish
network) can still read the source after ``drag.exec()`` returns — the drop
target may defer the actual file read, so deleting immediately would break it.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from PIL import Image

from ..config import get_user_data_dir, resolve_physical_path

logger = logging.getLogger(__name__)

_CACHE_DIR_NAME = "drag_cache"
_MAX_FILES = 3
_FILE_PREFIX = "HushSnap_"


def _cache_dir() -> Path:
    return resolve_physical_path(get_user_data_dir() / _CACHE_DIR_NAME)


def _rotate(cache_dir: Path) -> None:
    """Keep at most ``_MAX_FILES`` most-recent files; delete the rest."""
    try:
        existing = sorted(
            [f for f in cache_dir.glob(f"{_FILE_PREFIX}*.png") if f.is_file()],
            key=lambda x: x.name,  # timestamp sort name → chronological
        )
        if len(existing) <= _MAX_FILES:
            return
        to_delete = existing[:-_MAX_FILES]
        logger.debug(
            "drag_cache rotate: %d files, deleting %d oldest",
            len(existing), len(to_delete),
        )
        for f in to_delete:
            try:
                f.unlink()
            except OSError:
                logger.debug("rotate skip (likely locked): %s", f.name)
    except Exception:
        logger.warning("drag_cache rotate failed", exc_info=True)


def create_temp(pil_image: Image.Image) -> Path:
    """Write *pil_image* as a PNG into the drag cache and return its path.

    The caller is responsible for feeding the path to ``QDrag`` — this
    function handles directory creation, rotation, and notifying Explorer
    that the file exists before the drag begins.
    """
    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    ms = int(time.time() * 1000) % 1000
    path = cache_dir / f"{_FILE_PREFIX}{ts}_{ms:03d}.png"

    with open(path, "wb") as f:
        pil_image.save(f, "PNG")
        f.flush()
        os.fsync(f.fileno())

    # Tell Explorer the file exists before QDrag references it — otherwise
    # some shell views won't recognise the URL as a valid drop source.
    if os.name == "nt":
        try:
            import ctypes
            SHCNE_CREATE = 0x00000002
            SHCNF_PATH = 0x00000001
            SHCNF_FLUSHNOWAIT = 0x00000004
            ctypes.windll.shell32.SHChangeNotify(
                SHCNE_CREATE, SHCNF_PATH | SHCNF_FLUSHNOWAIT, str(path), None,
            )
        except Exception:
            logger.debug("SHChangeNotify(CREATE) failed", exc_info=True)

    # Rotate AFTER creating the new file so _MAX_FILES is the exact cap
    # on disk, not _MAX_FILES+1.
    _rotate(cache_dir)

    logger.debug("drag_cache created: %s", path.name)
    return path


def purge() -> None:
    """Delete the entire drag-cache directory (called at startup)."""
    cache_dir = _cache_dir()
    try:
        if cache_dir.exists() and cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
            logger.debug("drag_cache purged: %s", cache_dir)
    except Exception:
        logger.debug("drag_cache purge failed", exc_info=True)
