from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore
from PyQt6 import QtWidgets

from hushsnap.capture_session import CaptureSession


class DummyBridge(QtCore.QObject):
    signal = QtCore.pyqtSignal(object)


class FakeWindow(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.show = MagicMock()


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def mock_pixmap():
    return object()


def test_capture_session_ignores_duplicate_window_requests(qapp, mock_pixmap):
    first_window = FakeWindow()
    second_window = FakeWindow()
    factory = MagicMock(side_effect=[first_window, second_window])

    session = CaptureSession(
        on_capture_completed=MagicMock(),
        window_factory=factory,
        bridge=DummyBridge(),
    )

    session.launch_capture_window(mock_pixmap)
    session.launch_capture_window(mock_pixmap)

    factory.assert_called_once_with(mock_pixmap, on_captured=session.on_capture_completed)
    first_window.show.assert_called_once()
    assert session.win is first_window


def test_capture_session_clears_window_when_destroyed(qapp, mock_pixmap):
    window = FakeWindow()
    factory = MagicMock(return_value=window)

    session = CaptureSession(
        on_capture_completed=MagicMock(),
        window_factory=factory,
        bridge=DummyBridge(),
    )

    session.launch_capture_window(mock_pixmap)
    window.destroyed.emit(window)

    assert session.win is None


def test_capture_session_launches_multi_monitor(qapp):
    mock_screen1 = MagicMock()
    mock_screen2 = MagicMock()
    mock_pixmap1 = MagicMock()
    mock_pixmap2 = MagicMock()

    first_window = FakeWindow()
    first_window.destroyed = MagicMock()
    second_window = FakeWindow()
    second_window.destroyed = MagicMock()

    factory = MagicMock(side_effect=[first_window, second_window])

    session = CaptureSession(
        on_capture_completed=MagicMock(),
        window_factory=factory,
        bridge=DummyBridge(),
    )

    screens_and_pixmaps = [(mock_screen1, mock_pixmap1), (mock_screen2, mock_pixmap2)]
    session.launch_capture_window(screens_and_pixmaps)

    assert len(session.wins) == 2
    assert session.wins[0] is first_window
    assert session.wins[1] is second_window

    assert factory.call_count == 2
    _, kwargs1 = factory.call_args_list[0]
    _, kwargs2 = factory.call_args_list[1]

    assert kwargs1["pixmap"] is mock_pixmap1
    assert kwargs1["screen"] is mock_screen1
    assert kwargs2["pixmap"] is mock_pixmap2
    assert kwargs2["screen"] is mock_screen2
    assert callable(kwargs1["on_captured"])
    assert callable(kwargs1["on_closed"])


def test_crop_global_rect_cross_screen(qapp):
    from PyQt6 import QtGui

    session = CaptureSession(
        on_capture_completed=MagicMock(),
        bridge=DummyBridge(),
    )

    # Two screens that tile contiguously in *physical* virtual-desktop
    # pixels (win1 right edge == win2 left edge at x=200).  Mixed DPR:
    # win1 at 2.0 (200x200 native for 100x100 logical), win2 at 1.0.
    # In Qt's *logical* space these would have a gap; physical space does not.
    win1 = FakeWindow()
    win1.physical_rect = MagicMock(return_value=QtCore.QRect(0, 0, 200, 200))
    pixmap1 = QtGui.QPixmap(200, 200)
    pixmap1.fill(QtCore.Qt.GlobalColor.white)
    pixmap1.setDevicePixelRatio(2.0)
    win1.pixmap = pixmap1

    win2 = FakeWindow()
    win2.physical_rect = MagicMock(return_value=QtCore.QRect(200, 0, 100, 100))
    pixmap2 = QtGui.QPixmap(100, 100)
    pixmap2.fill(QtCore.Qt.GlobalColor.black)
    pixmap2.setDevicePixelRatio(1.0)
    win2.pixmap = pixmap2

    session.wins = [win1, win2]

    # Physical selection crossing the boundary at x=200: x[100,300], y[40,140].
    global_rect = QtCore.QRect(100, 40, 200, 100)
    stitched, logical_size = session.crop_global_rect(global_rect)

    # Canvas is 1:1 native: width = 100 native (win1) + 100 native (win2) = 200.
    assert stitched is not None
    assert stitched.width() == 200
    assert stitched.height() == 100
    assert stitched.devicePixelRatio() == 2.0
    # Logical size = physical / max_dpr = 200/2 x 100/2.
    assert logical_size == QtCore.QSize(100, 50)

    # Regression guard: a prior version called a non-existent
    # drawPixmap(QRectF, QPixmap) overload whose TypeError was swallowed by a
    # try/except, yielding a correctly-sized but fully transparent pixmap.
    img = stitched.toImage()
    assert not img.isNull()
    non_transparent = sum(
        1
        for y in range(img.height())
        for x in range(img.width())
        if img.pixelColor(x, y).alpha() != 0
    )
    assert non_transparent > 0, "stitched pixmap must contain visible pixels"


