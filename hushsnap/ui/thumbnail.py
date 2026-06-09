import os
import logging
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
from PIL import Image
import io

from .styles import MODERN_MENU_STYLE
from ..constants import (
    THUMBNAIL_WIDTH,
    THUMBNAIL_HEIGHT,
    THUMBNAIL_MARGIN,
    THUMBNAIL_DISPLAY_MS,
    THUMBNAIL_ANIM_MS,
    THUMBNAIL_CORNER_RADIUS,
    THUMBNAIL_DRAG_OPACITY,
    THUMBNAIL_DRAG_SCALE,
)

logger = logging.getLogger(__name__)

class ThumbnailWindow(QtWidgets.QWidget):
    """
    Floating thumbnail window with slide-in animation, auto-hide, 
    and drag-and-drop save functionality.
    """
    # Signals for local handling, Manager will relay these globally
    clicked_signal = QtCore.pyqtSignal()
    open_viewer_signal = QtCore.pyqtSignal()
    save_to_desktop_signal = QtCore.pyqtSignal()
    save_requested_signal = QtCore.pyqtSignal()
    pin_requested_signal = QtCore.pyqtSignal()

    def __init__(self, pil_image: Image.Image):
        super().__init__()
        self.pil_image = pil_image
        
        # 1. Convert PIL to QPixmap for display
        self.pixmap = self._pil_to_qpixmap(pil_image)
        
        # 2. Window configuration
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.WindowStaysOnTopHint |
            QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        
        # Shadow padding for custom drop shadow
        self.shadow_padding = 12
        
        # The card size is fixed
        self.card_width = THUMBNAIL_WIDTH
        self.card_height = THUMBNAIL_HEIGHT
        
        # The window size includes shadow padding
        self.display_width = self.card_width + 2 * self.shadow_padding
        self.display_height = self.card_height + 2 * self.shadow_padding
        self.setFixedSize(self.display_width, self.display_height)

        # Card rect within the window
        self.card_rect = QtCore.QRect(
            self.shadow_padding, 
            self.shadow_padding, 
            self.card_width, 
            self.card_height
        )

        # Scale original pixmap to fit inside the fixed card dimensions using KeepAspectRatio.
        self.scaled_pixmap = self.pixmap.scaled(
            self.card_width, self.card_height,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )

        # Center the scaled pixmap inside the card_rect
        pw = self.scaled_pixmap.width()
        ph = self.scaled_pixmap.height()
        px = self.shadow_padding + (self.card_width - pw) // 2
        py = self.shadow_padding + (self.card_height - ph) // 2
        self.pixmap_rect = QtCore.QRect(px, py, pw, ph)
        
        # Close button (small 'x' in top-right of card)
        self.close_btn = QtWidgets.QPushButton("×", self)
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(0, 0, 0, 160);"
            "  color: white;"
            "  border: none;"
            "  border-radius: 10px;"
            "  font-size: 14px;"
            "  font-weight: bold;"
            "  line-height: 18px;"
            "}"
            "QPushButton:hover {"
            "  background-color: rgba(255, 60, 60, 220);"
            "}"
        )
        self.close_btn.move(self.shadow_padding + self.card_width - 26, self.shadow_padding + 6)
        self.close_btn.clicked.connect(self.close)
        self.close_btn.hide()

        # 3. Position and Animation
        # Use cursor-based screen detection for multi-monitor awareness
        active_screen = (
            QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
            or QtWidgets.QApplication.primaryScreen()
        )
        screen = active_screen.availableGeometry()
        self.end_x = screen.x() + screen.width() - self.display_width - THUMBNAIL_MARGIN + self.shadow_padding
        self.end_y = screen.y() + screen.height() - self.display_height - THUMBNAIL_MARGIN + self.shadow_padding
        self.start_x = screen.x() + screen.width()
        
        self.move(self.start_x, self.end_y)
        
        # Slide-in animation
        self.pos_anim = QtCore.QPropertyAnimation(self, b"pos")
        self.pos_anim.setDuration(THUMBNAIL_ANIM_MS)
        self.pos_anim.setStartValue(QtCore.QPoint(self.start_x, self.end_y))
        self.pos_anim.setEndValue(QtCore.QPoint(self.end_x, self.end_y))
        self.pos_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        
        # Fade-out animation
        self.fade_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self.fade_anim.setDuration(THUMBNAIL_ANIM_MS)
        self.fade_anim.setStartValue(1.0)
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.finished.connect(self.close)
        
        # 4. Timer
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fade_anim.start)
        
        self._is_dragging = False
        self._drag_start_pos = None
        self._menu_active = False
        self._hovered = False

    def _get_display_ms(self) -> int:
        """Get the configured display duration from settings."""
        try:
            from ..config import get_thumbnail_display_time, get_config_path
            return get_thumbnail_display_time(get_config_path())
        except Exception:
            return 10000 # Fallback to 10s

    def _pil_to_qpixmap(self, pil_img: Image.Image) -> QtGui.QPixmap:
        """Convert PIL Image to QPixmap efficiently."""
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(
            data, 
            pil_img.size[0], 
            pil_img.size[1], 
            QtGui.QImage.Format.Format_RGBA8888
        ).copy()
        return QtGui.QPixmap.fromImage(qimage)

    def showEvent(self, event):
        super().showEvent(event)
        self.pos_anim.start()
        self.timer.start(self._get_display_ms())

    def enterEvent(self, event):
        """Pause timer on hover, activate visual feedback, and show buttons."""
        self.timer.stop()
        self.fade_anim.stop()
        self.setWindowOpacity(1.0)
        self._hovered = True
        self.update()
        self.close_btn.show()
        self.close_btn.raise_()

    def leaveEvent(self, event):
        """Resume timer on leave, deactivate visual feedback, and hide buttons."""
        if not self._is_dragging and not self._menu_active:
            self.timer.start(self._get_display_ms())
        self._hovered = False
        self.update()
        self.close_btn.hide()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self.card_rect.contains(pos):
                self._drag_start_pos = pos

    def mouseMoveEvent(self, event):
        if not (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            return
        if not self._drag_start_pos:
            return
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < 15:
            return
        self._start_drag()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            release_pos = event.position().toPoint()
            if self.card_rect.contains(release_pos):
                if self.close_btn.geometry().contains(release_pos):
                    return
                logger.debug(f"Thumbnail click triggered at {release_pos}")
                self.clicked_signal.emit()
                self.close()
            else:
                logger.debug(f"Thumbnail release outside card at {release_pos}, click ignored.")
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            if self.card_rect.contains(event.position().toPoint()):
                self._show_context_menu(event.globalPosition().toPoint())

    def _show_context_menu(self, pos):
        from ..config import resolve_ui_lang, ui_text, get_config_path
        lang = resolve_ui_lang(get_config_path())

        self._menu_active = True
        self._hovered = True
        self.update()
        self.timer.stop()
        self.fade_anim.stop()
        self.setWindowOpacity(1.0)

        menu = QtWidgets.QMenu(self)
        from .styles import MODERN_MENU_STYLE
        menu.setStyleSheet(MODERN_MENU_STYLE)
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        
        shadow = QtWidgets.QGraphicsDropShadowEffect(menu)
        shadow.setBlurRadius(15)
        shadow.setColor(QtGui.QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        menu.setGraphicsEffect(shadow)

        pin_action = menu.addAction(ui_text(lang, "thumbnail_pin"))
        view_action = menu.addAction(ui_text(lang, "thumbnail_view_image"))
        menu.addSeparator()
        desktop_action = menu.addAction(ui_text(lang, "thumbnail_save_to_desktop"))
        save_action = menu.addAction(ui_text(lang, "thumbnail_save_as"))

        action = menu.exec(pos)
        self._menu_active = False

        if action == view_action:
            self.open_viewer_signal.emit()
            self.close()
        elif action == desktop_action:
            self.save_to_desktop_signal.emit()
            self.close()
        elif action == save_action:
            self.save_requested_signal.emit()
            self.close()
        elif action == pin_action:
            self.pin_requested_signal.emit()
            self.close()
        else:
            self._hovered = False
            self.update()
            self.timer.start(self._get_display_ms())

    def _start_drag(self):
        self._is_dragging = True
        self.timer.stop()
        
        logger.debug("--- Drag-and-Drop Start ---")
        
        # Visual feedback
        self.setWindowOpacity(THUMBNAIL_DRAG_OPACITY)
        scaled_w = int(self.card_width * THUMBNAIL_DRAG_SCALE)
        scaled_h = int(self.card_height * THUMBNAIL_DRAG_SCALE)

        # Single rotating cache file — each drag overwrites the last.
        # No cleanup needed: one 100 KB PNG is harmless, and the file
        # is reused on the next drag so it never grows beyond that.
        from ..config import get_user_data_dir, resolve_physical_path
        raw_cache_dir = os.path.join(get_user_data_dir(), "drag_cache")
        cache_path_obj = resolve_physical_path(Path(raw_cache_dir))
        cache_dir = str(cache_path_obj)
        cache_path_obj.mkdir(parents=True, exist_ok=True)

        temp_path = os.path.join(cache_dir, "drag.png")

        try:
            with open(temp_path, "wb") as f:
                self.pil_image.save(f, "PNG")
                f.flush()
                os.fsync(f.fileno())
            logger.debug(f"Temporary file saved and fsync'd: {temp_path}")
        except Exception as e:
            logger.error(f"Failed to save temp file for drag: {e}")
            self._is_dragging = False
            return

        # Notify Shell
        if os.name == 'nt':
            try:
                import ctypes
                shell32 = ctypes.windll.shell32
                shell32.SHChangeNotify(0x00000002, 0x00000005, temp_path, None)
            except Exception:
                pass

        drag = QtGui.QDrag(self)
        mime_data = QtCore.QMimeData()
        mime_data.setUrls([QtCore.QUrl.fromLocalFile(temp_path)])
        drag.setMimeData(mime_data)
        
        drag_pixmap = self.pixmap.scaled(
            scaled_w, scaled_h, 
            QtCore.Qt.AspectRatioMode.KeepAspectRatio, 
            QtCore.Qt.TransformationMode.SmoothTransformation
        )
        drag.setPixmap(drag_pixmap)
        drag.setHotSpot(QtCore.QPoint(scaled_w // 2, scaled_h // 2))

        logger.debug("Executing drag.exec()...")
        result = drag.exec(QtCore.Qt.DropAction.CopyAction | QtCore.Qt.DropAction.MoveAction)
        logger.debug(f"Drag finished. Result: {result}")
        
        if result != QtCore.Qt.DropAction.IgnoreAction and os.name == 'nt':
            try:
                shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
            except Exception:
                pass

        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return

        self._is_dragging = False
        
        if result == QtCore.Qt.DropAction.IgnoreAction:
            cursor_pos = self.mapFromGlobal(QtGui.QCursor.pos())
            if self.card_rect.contains(cursor_pos):
                self.clicked_signal.emit()
                self.close()
            else:
                self.setWindowOpacity(1.0)
                self._hovered = False
                self.close_btn.hide()
                self.timer.start(self._get_display_ms())
                self.update()
        else:
            self.close()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    def dropEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)

        # Shadow
        for i in range(1, 10):
            alpha = int(25 * (1.0 - (i / 10.0)))
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, alpha), 2))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect).adjusted(-i + 0.5, -i + 3.5, i - 0.5, i + 3.5),
                THUMBNAIL_CORNER_RADIUS + i,
                THUMBNAIL_CORNER_RADIUS + i
            )

        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.card_rect), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)
        painter.fillPath(path, QtGui.QColor(30, 30, 30, 220))
        painter.setClipPath(path)
        painter.drawPixmap(self.pixmap_rect, self.scaled_pixmap)
        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 40), 1))
        painter.drawRoundedRect(QtCore.QRectF(self.card_rect).adjusted(0.5, 0.5, -0.5, -0.5), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)

        if self._hovered:
            painter.setPen(QtGui.QPen(QtGui.QColor("#5fc98a"), 1.5))
            painter.drawRoundedRect(QtCore.QRectF(self.card_rect).adjusted(1, 1, -1, -1), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)

class ThumbnailManager(QtCore.QObject):
    """
    Manages thumbnail window creation from any thread.
    """
    show_signal = QtCore.pyqtSignal(object)
    clicked = QtCore.pyqtSignal(object)
    open_viewer = QtCore.pyqtSignal(object)
    save_to_desktop = QtCore.pyqtSignal(object)
    save_requested = QtCore.pyqtSignal(object)
    pin_requested = QtCore.pyqtSignal(object, object, object)

    def __init__(self):
        super().__init__()
        self.show_signal.connect(self._do_show)
        self._windows = []

    def _do_show(self, pil_image: Image.Image):
        for w in self._windows:
            try:
                w.close()
            except Exception:
                pass
        self._windows = []
        
        win = ThumbnailWindow(pil_image)
        win.clicked_signal.connect(lambda: self.clicked.emit(pil_image))
        win.open_viewer_signal.connect(lambda: self.open_viewer.emit(pil_image))
        win.save_to_desktop_signal.connect(lambda: self.save_to_desktop.emit(pil_image))
        win.save_requested_signal.connect(lambda: self.save_requested.emit(pil_image))
        win.pin_requested_signal.connect(
            lambda: self.pin_requested.emit(
                pil_image, 
                win.mapToGlobal(win.card_rect.topLeft()), 
                win.card_rect.size()
            )
        )
        win.destroyed.connect(lambda: self._windows.remove(win) if win in self._windows else None)
        self._windows.append(win)
        win.show()

    def current_window_center(self):
        for w in self._windows:
            try:
                if w.isVisible():
                    geo = w.geometry()
                    return (geo.center().x(), geo.center().y())
            except RuntimeError:
                pass
        return None

    def current_window_rect(self):
        for w in self._windows:
            try:
                if w.isVisible():
                    return w.mapToGlobal(w.card_rect.topLeft()), w.card_rect.size()
            except RuntimeError:
                pass
        return None, None

thumbnail_manager = ThumbnailManager()

def qpixmap_to_pil(pixmap: QtGui.QPixmap) -> Image.Image:
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QBuffer.OpenModeFlag.ReadWrite)
    pixmap.save(buffer, "PNG")
    return Image.open(io.BytesIO(buffer.data().data()))

def show_thumbnail(pil_image: Image.Image):
    if thumbnail_manager:
        thumbnail_manager.show_signal.emit(pil_image)
