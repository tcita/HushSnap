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
