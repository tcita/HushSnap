import pytest
from PIL import Image

from hushsnap.system.drag_cache import create_temp, purge


@pytest.fixture
def pil_img():
    """A tiny 10x10 RGBA image for drag-cache tests."""
    return Image.new("RGBA", (10, 10), (255, 0, 0, 255))


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    """Point drag_cache at a temp directory so tests never touch the real one."""
    monkeypatch.setattr(
        "hushsnap.system.drag_cache.get_user_data_dir",
        lambda: tmp_path,
    )
    return tmp_path


class TestCreateTemp:
    def test_creates_valid_png(self, pil_img, cache_root):
        path = create_temp(pil_img)
        assert path.exists()
        assert path.suffix == ".png"
        # Round-trip: the saved PNG should be readable and match dimensions.
        reloaded = Image.open(path)
        assert reloaded.size == (10, 10)

    def test_returns_path_inside_cache_dir(self, pil_img, cache_root):
        path = create_temp(pil_img)
        assert path.parent == cache_root / "drag_cache"

    def test_filename_format(self, pil_img):
        path = create_temp(pil_img)
        # HushSnap_YYYYMMDD_HHMMSS_mmm.png
        assert path.name.startswith("HushSnap_")
        assert path.name.endswith(".png")
        parts = path.stem.split("_")  # ["HushSnap", "YYYYMMDD", "HHMMSS", "mmm"]
        assert len(parts) == 4

    def test_multiple_calls_do_not_error(self, pil_img, cache_root):
        # Rotation keeps at most _MAX_FILES (3); earlier paths are deleted.
        for _ in range(5):
            create_temp(pil_img)
        # At most _MAX_FILES survive — the point is that nothing crashed.
        cache_dir = cache_root / "drag_cache"
        files = sorted(cache_dir.glob("HushSnap_*.png"))
        assert 1 <= len(files) <= 3


class TestRotation:
    def test_keeps_at_most_max_files(self, pil_img, cache_root):
        for _ in range(6):
            create_temp(pil_img)

        cache_dir = cache_root / "drag_cache"
        files = sorted(cache_dir.glob("HushSnap_*.png"))
        # _MAX_FILES = 3, exactly 3 should remain after rotation.
        assert len(files) == 3

    def test_oldest_deleted_first(self, pil_img, cache_root):
        # Create files with a small sleep so filename order is unambiguous.
        # _MAX_FILES = 3, so the 4th file triggers rotation and deletes the oldest.
        import time
        path1 = create_temp(pil_img)  # oldest
        time.sleep(0.02)
        path2 = create_temp(pil_img)
        time.sleep(0.02)
        path3 = create_temp(pil_img)
        time.sleep(0.02)
        path4 = create_temp(pil_img)  # rotation deletes path1

        cache_dir = cache_root / "drag_cache"
        remaining = {p.name for p in cache_dir.glob("HushSnap_*.png")}
        assert path1.name not in remaining
        assert path2.name in remaining
        assert path3.name in remaining
        assert path4.name in remaining

    def test_under_limit_keeps_all(self, pil_img, cache_root):
        p1 = create_temp(pil_img)
        p2 = create_temp(pil_img)

        cache_dir = cache_root / "drag_cache"
        files = sorted(cache_dir.glob("HushSnap_*.png"))
        assert len(files) == 2  # under _MAX_FILES=3, nothing deleted
        assert p1.exists()
        assert p2.exists()


class TestPurge:
    def test_removes_cache_directory(self, pil_img, cache_root):
        create_temp(pil_img)
        cache_dir = cache_root / "drag_cache"
        assert cache_dir.is_dir()

        purge()
        assert not cache_dir.exists()

    def test_idempotent_when_no_cache(self, cache_root):
        # purge() on a non-existent directory should not raise.
        cache_dir = cache_root / "drag_cache"
        assert not cache_dir.exists()
        purge()  # should not raise
        assert not cache_dir.exists()

    def test_idempotent_when_already_purged(self, pil_img, cache_root):
        create_temp(pil_img)
        purge()
        purge()  # second call should be a no-op, not raise
