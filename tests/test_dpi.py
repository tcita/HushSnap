"""
Unit tests for hushsnap.dpi — the unified DPR / pixel-conversion module.
"""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.dpi import (
    current_dpr,
    logical_to_physical_rect,
    logical_to_physical_size,
    physical_to_logical_size,
    grab_full_screen,
)


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


# ── current_dpr ──────────────────────────────────────────────────────────────

class TestCurrentDpr:
    def test_returns_dpr_when_screen_available(self, qapp):
        """current_dpr() returns the primary screen's devicePixelRatio."""
        dpr = current_dpr()
        assert isinstance(dpr, float)
        assert dpr >= 1.0

    def test_returns_1_when_no_screen(self):
        """current_dpr() returns 1.0 when primaryScreen() is None."""
        with patch("PyQt6.QtGui.QGuiApplication.primaryScreen", return_value=None):
            assert current_dpr() == 1.0


# ── logical_to_physical_rect ─────────────────────────────────────────────────

class TestLogicalToPhysicalRect:
    def test_basic_conversion_explicit_dpr(self):
        rect = QtCore.QRect(10, 20, 100, 200)
        result = logical_to_physical_rect(rect, dpr=2.0)
        assert result.x() == 20
        assert result.y() == 40
        assert result.width() == 200
        assert result.height() == 400

    def test_basic_conversion_default_dpr(self, qapp):
        """Uses current_dpr() when no explicit dpr is given."""
        rect = QtCore.QRect(10, 20, 100, 200)
        result = logical_to_physical_rect(rect)
        dpr = current_dpr()
        assert result.x() == int(10 * dpr)
        assert result.y() == int(20 * dpr)
        assert result.width() == int(100 * dpr)
        assert result.height() == int(200 * dpr)

    def test_fractional_dpr_rounds_down(self):
        """int() truncates toward zero — verify behaviour on 1.5× scaling."""
        rect = QtCore.QRect(3, 3, 7, 7)
        result = logical_to_physical_rect(rect, dpr=1.5)
        # 3 * 1.5 = 4.5 → int → 4; 7 * 1.5 = 10.5 → int → 10
        assert result.x() == 4
        assert result.y() == 4
        assert result.width() == 10
        assert result.height() == 10

    def test_zero_dimensions(self):
        rect = QtCore.QRect(0, 0, 0, 0)
        result = logical_to_physical_rect(rect, dpr=2.0)
        assert result == QtCore.QRect(0, 0, 0, 0)


# ── logical_to_physical_size ─────────────────────────────────────────────────

class TestLogicalToPhysicalSize:
    def test_basic_conversion(self):
        assert logical_to_physical_size(100, 200, dpr=2.0) == (200, 400)

    def test_fractional_dpr(self):
        assert logical_to_physical_size(3, 5, dpr=1.5) == (4, 7)

    def test_zero(self):
        assert logical_to_physical_size(0, 0, dpr=2.0) == (0, 0)


# ── physical_to_logical_size ─────────────────────────────────────────────────

class TestPhysicalToLogicalSize:
    def test_basic_conversion(self):
        w, h = physical_to_logical_size(200, 400, dpr=2.0)
        assert w == 100.0
        assert h == 200.0

    def test_fractional_result(self):
        """Physical pixels don't always divide evenly by DPR."""
        w, h = physical_to_logical_size(100, 100, dpr=3.0)
        assert w == pytest.approx(33.333, rel=1e-3)
        assert h == pytest.approx(33.333, rel=1e-3)

    def test_dpr_1_identity(self):
        assert physical_to_logical_size(42, 99, dpr=1.0) == (42.0, 99.0)


# ── grab_full_screen ─────────────────────────────────────────────────────────

class TestGrabFullScreen:
    def test_returns_none_when_no_screen(self):
        # Both the cursor screen lookup and the primary fallback must be None.
        with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=None), \
             patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=None):
            assert grab_full_screen() is None

    def test_grabs_and_tags_pixmap(self, qapp):
        pixmap = grab_full_screen()
        assert pixmap is not None
        assert isinstance(pixmap, QtGui.QPixmap)
        # Must be tagged with the DPR of the screen that was actually grabbed
        # (the one under the cursor — may differ from the primary on mixed-DPR
        # multi-monitor setups).
        grabbed_screen = (
            QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
            or QtWidgets.QApplication.primaryScreen()
        )
        assert pixmap.devicePixelRatio() == grabbed_screen.devicePixelRatio()

    def test_grabs_screen_under_cursor(self, qapp):
        """Multi-monitor: the screen returned by screenAt(cursor) is the one grabbed."""
        cursor_screen = MagicMock()
        cursor_pixmap = QtGui.QPixmap(100, 100)
        cursor_screen.grabWindow.return_value = cursor_pixmap
        cursor_screen.devicePixelRatio.return_value = 2.0

        primary_screen = MagicMock()
        primary_screen.grabWindow.return_value = QtGui.QPixmap(50, 50)
        primary_screen.devicePixelRatio.return_value = 1.0

        with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=cursor_screen), \
             patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=primary_screen):
            result = grab_full_screen()

        assert result is cursor_pixmap
        cursor_screen.grabWindow.assert_called_once_with(0)
        # Primary screen must never be touched when screenAt() resolved a screen.
        primary_screen.grabWindow.assert_not_called()

    def test_falls_back_to_primary_when_cursor_offscreen(self, qapp):
        """When the cursor is outside any screen, fall back to the primary screen."""
        primary_screen = MagicMock()
        primary_pixmap = QtGui.QPixmap(80, 80)
        primary_screen.grabWindow.return_value = primary_pixmap
        primary_screen.devicePixelRatio.return_value = 1.5

        with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=None), \
             patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=primary_screen):
            result = grab_full_screen()

        assert result is primary_pixmap
        assert result.devicePixelRatio() == 1.5
        primary_screen.grabWindow.assert_called_once_with(0)

    def test_mocked_screen_dpr_propagation(self, qapp):
        """When the screen reports DPR=2.0 the result pixmap must carry 2.0."""
        mock_screen = MagicMock()
        mock_pixmap = QtGui.QPixmap(100, 100)
        mock_screen.grabWindow.return_value = mock_pixmap
        mock_screen.devicePixelRatio.return_value = 2.0

        with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=mock_screen), \
             patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=mock_screen):
            result = grab_full_screen()
            assert result is not None
            assert result.devicePixelRatio() == 2.0
            mock_screen.grabWindow.assert_called_once_with(0)
