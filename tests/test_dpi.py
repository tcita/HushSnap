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
        with patch("PyQt6.QtGui.QGuiApplication.screens", return_value=[]):
            assert grab_full_screen() is None

    def test_composites_all_screens_at_max_dpr(self, qapp):
        """The composite spans the virtual desktop and carries the max DPR."""
        pixmap = grab_full_screen()
        assert pixmap is not None
        assert isinstance(pixmap, QtGui.QPixmap)

        screens = QtGui.QGuiApplication.screens()
        max_dpr = max(s.devicePixelRatio() for s in screens)
        virtual = QtGui.QGuiApplication.primaryScreen().virtualGeometry()
        assert pixmap.devicePixelRatio() == max_dpr
        assert pixmap.width() == int(round(virtual.width() * max_dpr))
        assert pixmap.height() == int(round(virtual.height() * max_dpr))

    def _make_screen(self, geometry, dpr, pm_size):
        screen = MagicMock()
        screen.geometry.return_value = geometry
        screen.devicePixelRatio.return_value = dpr
        screen.grabWindow.return_value = QtGui.QPixmap(*pm_size)
        return screen

    def test_two_screens_composited_and_both_grabbed(self, qapp):
        """Two side-by-side screens are tiled onto one canvas at the max DPR."""
        s1 = self._make_screen(QtCore.QRect(0, 0, 1920, 1080), 1.0, (1920, 1080))
        s2 = self._make_screen(QtCore.QRect(1920, 0, 1280, 720), 2.0, (2560, 1440))
        primary = s1
        primary.virtualGeometry.return_value = QtCore.QRect(0, 0, 3200, 1080)

        with patch("PyQt6.QtGui.QGuiApplication.screens", return_value=[s1, s2]), \
             patch("PyQt6.QtGui.QGuiApplication.primaryScreen", return_value=primary):
            result = grab_full_screen()

        assert result is not None
        # max DPR is 2.0; canvas = virtual logical (3200x1080) × 2.0
        assert result.devicePixelRatio() == 2.0
        assert result.width() == 6400
        assert result.height() == 2160
        s1.grabWindow.assert_called_once_with(0)
        s2.grabWindow.assert_called_once_with(0)

    def test_single_screen_native_resolution_preserved(self, qapp):
        """A single screen at DPR 2.0 yields a 1:1 native composite."""
        screen = self._make_screen(QtCore.QRect(0, 0, 1000, 800), 2.0, (2000, 1600))
        screen.virtualGeometry.return_value = QtCore.QRect(0, 0, 1000, 800)

        with patch("PyQt6.QtGui.QGuiApplication.screens", return_value=[screen]), \
             patch("PyQt6.QtGui.QGuiApplication.primaryScreen", return_value=screen):
            result = grab_full_screen()

        assert result is not None
        assert result.devicePixelRatio() == 2.0
        assert result.width() == 2000
        assert result.height() == 1600
        screen.grabWindow.assert_called_once_with(0)
