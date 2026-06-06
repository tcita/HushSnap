
import sys
import threading
import pytest
from hushsnap.system.memory_utils import get_working_set_mb, fmt_memory, get_memory_stats, trim_working_set

@pytest.mark.skipif(sys.platform != "win32", reason="Win32 memory APIs only available on Windows")
def test_trim_working_set():
    # Warm up memory a bit if possible, though even baseline can be trimmed
    before = get_working_set_mb()
    success = trim_working_set()
    after = get_working_set_mb()
    
    assert success is True
    # Note: after can be slightly higher if the OS decides not to swap, 
    # but usually it drops. We mainly care it doesn't crash.
    assert isinstance(after, float)

@pytest.mark.skipif(sys.platform != "win32", reason="Win32 memory APIs only available on Windows")
def test_get_working_set_mb():
    ws = get_working_set_mb()
    assert isinstance(ws, float)
    assert ws > 0

@pytest.mark.skipif(sys.platform != "win32", reason="Win32 memory APIs only available on Windows")
def test_get_memory_stats():
    stats = get_memory_stats()
    assert "working_set_mb" in stats
    assert "peak_working_set_mb" in stats
    assert stats["working_set_mb"] > 0
    assert stats["peak_working_set_mb"] >= stats["working_set_mb"]

@pytest.mark.skipif(sys.platform != "win32", reason="Win32 memory APIs only available on Windows")
def test_fmt_memory():
    fmt = fmt_memory()
    assert fmt.startswith("WS=")
    assert "MB" in fmt

@pytest.mark.skipif(sys.platform != "win32", reason="Win32 memory APIs only available on Windows")
def test_initialization_thread_safety():
    """Stress test the thread-safe initialization of memory_utils."""
    results = []
    
    def worker():
        try:
            results.append(get_working_set_mb())
        except Exception as e:
            results.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for r in results:
        assert isinstance(r, float)
        assert r > 0

def test_non_windows_fallback():
    if sys.platform != "win32":
        assert get_working_set_mb() == -1.0
        assert fmt_memory() == "WS=unavailable"
        stats = get_memory_stats()
        assert stats["working_set_mb"] == -1.0
