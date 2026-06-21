import logging
import os
from PyQt6 import QtCore, QtGui, QtWidgets
from PIL import Image
import io
from pathlib import Path
import time

from .styles import MODERN_MENU_STYLE
from .toast import show_toast
from ..dpi import current_dpr, logical_to_physical_size, physical_to_logical_size

logger = logging.getLogger(__name__)

class PinnedImageWindow(QtWidgets.QWidget):
    """
    Floating, resizable, and draggable image window.
    """
    ocr_requested = QtCore.pyqtSignal(object, object)  # pixmap, source_win
    edit_requested = QtCore.pyqtSignal(object)  # pil_image

    def __init__(self, pil_image: Image.Image, logical_size: QtCore.QSize = None, screen=None):
        super().__init__()
        self.pil_image = pil_image
        self.pixmap = self._pil_to_qpixmap(pil_image)
        self.aspect_ratio = self.pixmap.width() / self.pixmap.height()

        # 1. Resolve the target screen (multi-monitor aware).  Defaults to the
        #    screen under the cursor so the pinned window lands on the same
        #    monitor the user is working on, not always the primary.
        screen = (
            screen
            or QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
            or QtWidgets.QApplication.primaryScreen()
        )
        dpr = screen.devicePixelRatio() if screen else current_dpr()

        # 2. Fix pixmap scaling for High-DPI rendering
        self.pixmap.setDevicePixelRatio(dpr)
        
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.WindowStaysOnTopHint |
            QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        
        # UI configuration
        self.shadow_width = 8
        self.border_width = 1
        self.border_radius = 4
        
        # 3. Calculate initial logical size (User hand-feel size)
        # Use explicitly passed logical_size if available (matches capture selection exactly)
        if logical_size:
            img_w = logical_size.width()
            img_h = logical_size.height()
        else:
            phys_w, phys_h = pil_image.size
            img_w, img_h = physical_to_logical_size(phys_w, phys_h, dpr=dpr)

        screen_geo = screen.availableGeometry()

        # Limit initial size to 80% of screen
        max_w = screen_geo.width() * 0.8
        max_h = screen_geo.height() * 0.8

        if img_w > max_w or img_h > max_h:
            ratio = min(max_w / img_w, max_h / img_h)
            img_w *= ratio
            img_h *= ratio

        # Dynamic shadow
        content_min = min(img_w, img_h)
        if content_min < 80:
            self.shadow_width = 3
        elif content_min < 160:
            self.shadow_width = 5

        # The window size includes shadow padding
        self.resize(int(img_w + 2 * self.shadow_width), int(img_h + 2 * self.shadow_width))

        # Position on the right side, vertically centered on the target screen
        right_margin = 40
        self.move(
            screen_geo.x() + screen_geo.width() - self.width() - right_margin,
            screen_geo.y() + (screen_geo.height() - self.height()) // 2
        )

        # Drag and resize state
        self._is_resizing = False
        self._active_edge = None
        self._drag_start_geometry = None
        self._drag_offset = QtCore.QPoint(0, 0)
        self._resize_threshold = 16
        
        # UI Elements
        self.close_btn = QtWidgets.QPushButton("×", self)
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(0, 0, 0, 120);"
            "  color: white;"
            "  border: none;"
            "  border-radius: 12px;"
            "  font-size: 16px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover {"
            "  background-color: rgba(255, 60, 60, 200);"
            "}"
        )
        self.close_btn.clicked.connect(self.close)
        self.close_btn.hide()

        self._update_ui_positions()
        self._morph_source = None

    def set_morph_source(self, pos, size):
        """Set the screen rect to morph from when showing."""
        if pos and size:
            self._morph_source = QtCore.QRect(pos, size)

    def showEvent(self, event):
        if self._morph_source:
            start_geom = self._morph_source
            target_geom = self.geometry()
            self._morph_source = None
            
            self.setWindowOpacity(0.2)
            self._show_anim = QtCore.QParallelAnimationGroup(self)
            
            fade = QtCore.QPropertyAnimation(self, b"windowOpacity")
            fade.setDuration(250)
            fade.setStartValue(0.2)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            
            morph = QtCore.QPropertyAnimation(self, b"geometry")
            morph.setDuration(300)
            morph.setStartValue(start_geom)
            morph.setEndValue(target_geom)
            morph.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            
            self._show_anim.addAnimation(fade)
            self._show_anim.addAnimation(morph)
            self._show_anim.start()
        super().showEvent(event)

    def _get_content_rect(self) -> QtCore.QRect:
        """Returns the rectangle for the actual image content, excluding shadow padding."""
        return self.rect().adjusted(
            self.shadow_width, self.shadow_width,
            -self.shadow_width, -self.shadow_width
        )

    def _update_ui_positions(self):
        """Reposition the close button."""
        close_x = self.width() - 28
        close_y = 4
        self.close_btn.move(close_x, close_y)

    def enterEvent(self, event):
        self._update_ui_positions()
        self.close_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.close_btn.hide()
        super().leaveEvent(event)

    def _get_edge(self, pos):
        rect = self._get_content_rect()
        hit = min(self._resize_threshold, rect.width() // 4, rect.height() // 4)
        if hit < 6:
            hit = min(6, rect.width() // 2, rect.height() // 2)

        if not rect.adjusted(-hit, -hit, hit, hit).contains(pos):
            return QtCore.Qt.Edge(0)

        edge = QtCore.Qt.Edge(0)
        is_left = pos.x() < rect.left() + hit
        is_right = pos.x() > rect.right() - hit
        is_top = pos.y() < rect.top() + hit
        is_bottom = pos.y() > rect.bottom() - hit

        if is_left and is_right: is_left = is_right = False
        if is_top and is_bottom: is_top = is_bottom = False

        if (is_left or is_right) and (is_top or is_bottom):
            if is_left: edge |= QtCore.Qt.Edge.LeftEdge
            if is_right: edge |= QtCore.Qt.Edge.RightEdge
            if is_top: edge |= QtCore.Qt.Edge.TopEdge
            if is_bottom: edge |= QtCore.Qt.Edge.BottomEdge
        return edge

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            self._active_edge = self._get_edge(pos)
            self._drag_start_geometry = self.geometry()
            if self._active_edge:
                self._is_resizing = True
            else:
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        m_pos = event.globalPosition().toPoint()
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            if self._is_resizing:
                g = self._drag_start_geometry
                cg = g.adjusted(self.shadow_width, self.shadow_width, -self.shadow_width, -self.shadow_width)
                if self._active_edge == (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge):
                    ax, ay = cg.right(), cg.bottom(); sx, sy = -1, -1
                elif self._active_edge == (QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.RightEdge):
                    ax, ay = cg.left(), cg.top(); sx, sy = 1, 1
                elif self._active_edge == (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.RightEdge):
                    ax, ay = cg.left(), cg.bottom(); sx, sy = 1, -1
                elif self._active_edge == (QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.LeftEdge):
                    ax, ay = cg.right(), cg.top(); sx, sy = -1, 1
                else: return

                vx, vy = m_pos.x() - ax, m_pos.y() - ay
                dx, dy = sx * self.aspect_ratio, sy * 1.0
                dot = vx * dx + vy * dy
                mag_sq = dx * dx + dy * dy
                scale = max(dot / mag_sq, 20.0 / self.aspect_ratio)
                new_cw, new_ch = abs(scale * dx), abs(scale * dy)
                new_cl = ax if sx > 0 else ax - new_cw
                new_ct = ay if sy > 0 else ay - new_ch
                self.setGeometry(
                    int(round(new_cl - self.shadow_width)),
                    int(round(new_ct - self.shadow_width)),
                    int(round(new_cw + 2 * self.shadow_width)),
                    int(round(new_ch + 2 * self.shadow_width))
                )
                event.accept()
            else:
                self.move(m_pos - self._drag_offset)
                event.accept()
        else:
            local_pos = self.mapFromGlobal(m_pos)
            edge = self._get_edge(local_pos)
            if edge == (QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.TopEdge) or \
               edge == (QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.BottomEdge):
                self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
            elif edge == (QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.TopEdge) or \
                 edge == (QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.BottomEdge):
                self.setCursor(QtCore.Qt.CursorShape.SizeBDiagCursor)
            else:
                self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._is_resizing = False
        self._active_edge = None
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_ui_positions()

    def _pil_to_qpixmap(self, pil_img: Image.Image) -> QtGui.QPixmap:
        if pil_img.mode != "RGBA": pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(data, pil_img.size[0], pil_img.size[1], QtGui.QImage.Format.Format_RGBA8888).copy()
        return QtGui.QPixmap.fromImage(qimage)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        content_rect = self._get_content_rect()
        for i in range(1, self.shadow_width + 1):
            alpha = int(40 * (1.0 - (i / float(self.shadow_width))))
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, alpha), 1))
            painter.drawRoundedRect(QtCore.QRectF(content_rect).adjusted(-i + 0.5, -i + 0.5, i - 0.5, i + 0.5), self.border_radius + i, self.border_radius + i)
        dpr = self.devicePixelRatio()
        pw, ph = logical_to_physical_size(int(content_rect.width()), int(content_rect.height()), dpr=dpr)
        scaled_pixmap = self.pixmap.scaled(pw, ph, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
        scaled_pixmap.setDevicePixelRatio(dpr)
        if self.border_radius > 0:
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(content_rect), self.border_radius, self.border_radius)
            painter.setClipPath(path)
        painter.drawPixmap(content_rect.topLeft(), scaled_pixmap)
        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 60), self.border_width))
        painter.drawRoundedRect(QtCore.QRectF(content_rect).adjusted(0.5, 0.5, -0.5, -0.5), self.border_radius, self.border_radius)

    def _show_context_menu(self, pos):
        from ..config import resolve_ui_lang, ui_text, get_config_path
        lang = resolve_ui_lang(get_config_path())
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(MODERN_MENU_STYLE)
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        shadow = QtWidgets.QGraphicsDropShadowEffect(menu)
        shadow.setBlurRadius(15)
        shadow.setColor(QtGui.QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        menu.setGraphicsEffect(shadow)
        ocr_action = menu.addAction(ui_text(lang, "menu_ocr_recognize"))
        copy_action = menu.addAction(ui_text(lang, "pin_copy_image"))
        edit_action = menu.addAction(ui_text(lang, "thumbnail_edit"))
        menu.addSeparator()
        desktop_action = menu.addAction(ui_text(lang, "thumbnail_save_to_desktop"))
        action = menu.exec(pos)
        if action == edit_action:
            self.edit_requested.emit(self.pil_image)
        elif action == copy_action:
            cb = QtWidgets.QApplication.clipboard()
            buffer = io.BytesIO(); self.pil_image.save(buffer, format="PNG")
            cb.setImage(QtGui.QImage.fromData(buffer.getvalue()))
            show_toast(ui_text(lang, "pin_image_copied"))
        elif action == desktop_action:
            try:
                desktop = Path.home() / "Desktop"
                timestamp = QtCore.QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss_zzz')
                base = f"HushSnap_{timestamp}"
                file_path = desktop / f"{base}.png"
                counter = 1
                while file_path.exists():
                    file_path = desktop / f"{base}({counter}).png"
                    counter += 1
                self.pil_image.save(file_path)
                show_toast(ui_text(lang, "pin_saved_to_desktop"))
            except Exception: logger.exception("Failed to save pinned image to desktop")
        elif action == ocr_action:
            self.ocr_requested.emit(self.pixmap, self)

class PinnedImageManager(QtCore.QObject):
    """Manages multiple pinned image windows."""
    ocr_requested = QtCore.pyqtSignal(object, object)
    edit_requested = QtCore.pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._windows = []
        
    def pin_image(self, pil_image: Image.Image, morph_pos=None, morph_size=None, logical_size=None):
        try:
            # Resolve the target screen from the morph origin (the thumbnail's
            # global position), so the pinned window lands on the same monitor
            # the screenshot came from.  Falls back to the cursor's screen,
            # then the primary screen.
            screen = (
                (QtWidgets.QApplication.screenAt(morph_pos) if morph_pos is not None else None)
                or QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
                or QtWidgets.QApplication.primaryScreen()
            )
            win = PinnedImageWindow(pil_image, logical_size=logical_size, screen=screen)
            avail = screen.availableGeometry()
            n = len(self._windows)
            x, y = win.x() + n * 30, win.y() + n * 30
            if x + win.width() > avail.right(): x = avail.right() - win.width()
            if y + win.height() > avail.bottom(): y = avail.bottom() - win.height()
            win.move(x, y)
            win.set_morph_source(morph_pos, morph_size)
            win.ocr_requested.connect(self.ocr_requested.emit)
            win.edit_requested.connect(self.edit_requested.emit)
            win.show()
            from .thumbnail import thumbnail_manager
            thumbnail_manager.dismiss_current()
            self._windows.append(win)
            win.destroyed.connect(lambda: self._remove_window(win))
        except Exception as e: logger.exception(f"Failed to pin image: {e}")
        
    def _remove_window(self, win):
        if win in self._windows: self._windows.remove(win)

pinned_image_manager = PinnedImageManager()
