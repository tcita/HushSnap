from hushsnap.app import Application


class _FakeEditorWindow:
    def __init__(self, visible=True, deleted=False):
        self._visible = visible
        self._deleted = deleted

    def isVisible(self):
        if self._deleted:
            raise RuntimeError("wrapped C/C++ object has been deleted")
        return self._visible


def _app_with_windows(*windows):
    class _AppHarness:
        _untrack_editor_window = Application._untrack_editor_window
        _last_visible_editor_window = Application._last_visible_editor_window
        _has_visible_editor_window = Application._has_visible_editor_window

    app = _AppHarness()
    app._editor_windows = list(windows)
    app._editor_window = windows[-1] if windows else None
    return app


def test_editor_active_when_any_window_visible():
    hidden = _FakeEditorWindow(False)
    visible = _FakeEditorWindow(True)
    app = _app_with_windows(hidden, visible)

    assert app._has_visible_editor_window() is True
    assert app._editor_window is visible


def test_editor_inactive_only_when_no_windows_visible():
    app = _app_with_windows(_FakeEditorWindow(False), _FakeEditorWindow(False))

    assert app._has_visible_editor_window() is False
    assert app._editor_window is None


def test_deleted_editor_windows_are_pruned():
    deleted = _FakeEditorWindow(deleted=True)
    visible = _FakeEditorWindow(True)
    app = _app_with_windows(deleted, visible)

    assert app._has_visible_editor_window() is True
    assert deleted not in app._editor_windows
    assert app._editor_window is visible


def test_untracking_old_window_keeps_new_visible_window_active():
    old = _FakeEditorWindow(True)
    new = _FakeEditorWindow(True)
    app = _app_with_windows(old, new)
    app._editor_window = old

    app._untrack_editor_window(old)

    assert app._editor_windows == [new]
    assert app._editor_window is new
