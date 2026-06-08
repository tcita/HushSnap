import logging
from PyQt6 import QtCore, QtGui, QtWidgets
from PIL import Image
import io

logger = logging.getLogger(__name__)

class PinnedImageWindow(QtWidgets.QWidget):
    """
    Floating, resizable, and draggable image window.
    """
    ocr_requested = QtCore.pyqtSignal(object, object)  # pixmap, source_win

    def __init__(self, pil_image: Image.Image):
        super().__init__()
        self.pil_image = pil_image
        self.pixmap = self._pil_to_qpixmap(pil_image)
        self.aspect_ratio = self.pixmap.width() / self.pixmap.height()
        
        # Important for High-DPI: tell the pixmap its device pixel ratio
        dpr = self.devicePixelRatio()
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
        
        # Initial size (Convert physical pixels to logical pixels)
        img_w = self.pixmap.width() / dpr
        img_h = self.pixmap.height() / dpr
        
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        
        # Limit initial size to 80% of screen
        max_w = screen.width() * 0.8
        max_h = screen.height() * 0.8
        
        if img_w > max_w or img_h > max_h:
            ratio = min(max_w / img_w, max_h / img_h)
            img_w *= ratio
            img_h *= ratio

        # The window size includes shadow padding
        self.resize(int(img_w + 2 * self.shadow_width), int(img_h + 2 * self.shadow_width))
        
        # Position on the right side, vertically centered — keeps the pinned
        # image visible but out of the way of the main work area.
        right_margin = 40
        self.move(
            screen.x() + screen.width() - self.width() - right_margin,
            screen.y() + (screen.height() - self.height()) // 2
        )
        
        # Drag and resize state
        self._is_resizing = False
        self._active_edge = None
        self._drag_start_mouse_pos = None
        self._drag_start_geometry = None
        self._drag_offset = QtCore.QPoint(0, 0)
        self._resize_threshold = 16 # Larger threshold for easier grabbing
        
        # UI Elements
        self.close_btn = QtWidgets.QPushButton("×", self)
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(0, 0, 0, 120);"
            "  color: white;"
            "  border: none;"
            "  border-radius: 11px;"
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

    def _get_content_rect(self) -> QtCore.QRect:
        """Returns the rectangle for the actual image content, excluding shadow padding."""
        return self.rect().adjusted(
            self.shadow_width, self.shadow_width,
            -self.shadow_width, -self.shadow_width
        )

    def _update_ui_positions(self):
        # Close button — top-right corner of the *window*, in the transparent
        # shadow margin outside the image. Always works regardless of image size.
        close_x = self.width() - 26
        close_y = 4
        self.close_btn.move(close_x, close_y)
        self.close_btn.show()
            
    def enterEvent(self, event):
        self._update_ui_positions()
        self.close_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.close_btn.hide()
        super().leaveEvent(event)

    def _get_edge(self, pos):
        hit = self._resize_threshold
        rect = self._get_content_rect()
        
        # Only detect edges within or near the content rect
        if not rect.adjusted(-hit, -hit, hit, hit).contains(pos):
            return QtCore.Qt.Edge(0)
            
        edge = QtCore.Qt.Edge(0)
        is_left = pos.x() < rect.left() + hit
        is_right = pos.x() > rect.right() - hit
        is_top = pos.y() < rect.top() + hit
        is_bottom = pos.y() > rect.bottom() - hit

        # Only trigger for corners (intersection of horizontal + vertical edge).
        # If the window is so small that opposing edges both match, treat it
        # as a drag rather than a broken resize.
        if is_left and is_right:
            is_left = is_right = False
        if is_top and is_bottom:
            is_top = is_bottom = False

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
            self._drag_start_mouse_pos = event.globalPosition().toPoint()
            self._drag_start_geometry = self.geometry()
            
            if self._active_edge:
                self._is_resizing = True
            else:
                # Manual dragging: store the offset
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
                
            event.accept()
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        m_pos = event.globalPosition().toPoint()
        
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            if self._is_resizing:
                g = self._drag_start_geometry
                # The content geometry relative to global coordinates
                cg = g.adjusted(self.shadow_width, self.shadow_width, -self.shadow_width, -self.shadow_width)
                
                # 1. Determine Anchor (the corner that stays fixed)
                # sx, sy are signs for the direction from anchor to the corner being dragged
                if self._active_edge == (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge):
                    ax, ay = cg.right(), cg.bottom(); sx, sy = -1, -1
                elif self._active_edge == (QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.RightEdge):
                    ax, ay = cg.left(), cg.top(); sx, sy = 1, 1
                elif self._active_edge == (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.RightEdge):
                    ax, ay = cg.left(), cg.bottom(); sx, sy = 1, -1
                elif self._active_edge == (QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.LeftEdge):
                    ax, ay = cg.right(), cg.top(); sx, sy = -1, 1
                else:
                    return

                # 2. Resizing Logic with Vector Projection
                # Vector from anchor to mouse
                vx = m_pos.x() - ax
                vy = m_pos.y() - ay
                
                # Vector representing the diagonal direction (normalized-ish)
                dx = sx * self.aspect_ratio
                dy = sy * 1.0
                
                # Project (vx, vy) onto (dx, dy)
                # scale = (v . d) / (d . d)
                dot = vx * dx + vy * dy
                mag_sq = dx * dx + dy * dy
                scale = dot / mag_sq
                
                # Minimum size limit (20px min width)
                scale = max(scale, 20.0 / self.aspect_ratio)
                
                # New content size
                new_cw = abs(scale * dx)
                new_ch = abs(scale * dy)
                
                # New content top-left
                new_cl = ax if sx > 0 else ax - new_cw
                new_ct = ay if sy > 0 else ay - new_ch
                
                # Final window geometry (add shadow padding back)
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
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(
            data, pil_img.size[0], pil_img.size[1],
            QtGui.QImage.Format.Format_RGBA8888
        ).copy()
        return QtGui.QPixmap.fromImage(qimage)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        
        content_rect = self._get_content_rect()
        
        # 1. Draw Drop Shadow
        for i in range(1, self.shadow_width + 1):
            alpha = int(40 * (1.0 - (i / float(self.shadow_width))))
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, alpha), 1))
            painter.drawRoundedRect(
                QtCore.QRectF(content_rect).adjusted(-i + 0.5, -i + 0.5, i - 0.5, i + 0.5),
                self.border_radius + i,
                self.border_radius + i
            )
            
        # 2. Draw Image
        dpr = self.devicePixelRatio()
        # Scale pixmap to exactly fit content_rect
        scaled_pixmap = self.pixmap.scaled(
            int(content_rect.width() * dpr),
            int(content_rect.height() * dpr),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation
        )
        scaled_pixmap.setDevicePixelRatio(dpr)
        
        # Clip to rounded rect if radius > 0
        if self.border_radius > 0:
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(content_rect), self.border_radius, self.border_radius)
            painter.setClipPath(path)
            
        painter.drawPixmap(content_rect.topLeft(), scaled_pixmap)
        painter.setClipping(False)
        
        # 3. Draw Border
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 60), self.border_width))
        painter.drawRoundedRect(
            QtCore.QRectF(content_rect).adjusted(0.5, 0.5, -0.5, -0.5),
            self.border_radius,
            self.border_radius
        )

    def show_toast(self, text, duration_ms=1500):
        """Show a lightweight centred toast overlay that fades out after *duration_ms*."""
        toast = QtWidgets.QLabel(text, self)
        toast.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        toast.setStyleSheet(
            "QLabel {"
            " background-color: rgba(0, 0, 0, 200);"
            " color: #5fc98a;"
            " border-radius: 8px;"
            " padding: 10px 20px;"
            " font-size: 14px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
        )
        toast.adjustSize()
        cx = (self.width() - toast.width()) // 2
        cy = (self.height() - toast.height()) // 2
        toast.move(cx, cy)
        toast.show()

        # Fade-out via manual timer — QPropertyAnimation on
        # QGraphicsOpacityEffect.opacity is unreliable in PyQt6.
        effect = QtWidgets.QGraphicsOpacityEffect(toast)
        effect.setOpacity(1.0)
        toast.setGraphicsEffect(effect)

        fade_timer = QtCore.QTimer(toast)
        fade_step_ms = 30
        fade_total_ms = 400
        fade_steps = fade_total_ms // fade_step_ms
        # Pre-calculate the opacity delta per step (ease-in quadratic)
        step_values = []
        for i in range(fade_steps + 1):
            t = i / fade_steps
            eased = 1.0 - t * t  # quadratic ease-in: starts slow, ends fast
            step_values.append(eased)
        step_idx = [0]  # in list for closure mutability

        def _fade_step():
            idx = step_idx[0]
            if idx >= len(step_values):
                fade_timer.stop()
                toast.deleteLater()
                return
            effect.setOpacity(step_values[idx])
            step_idx[0] += 1

        fade_timer.timeout.connect(_fade_step)
        # Start fade after the display duration
        QtCore.QTimer.singleShot(duration_ms, fade_timer.start)
        fade_timer.setInterval(fade_step_ms)

    def _show_context_menu(self, pos):
        from ..config import resolve_ui_lang, ui_text, get_config_path
        lang = resolve_ui_lang(get_config_path())
        
        menu = QtWidgets.QMenu(self)
        from .styles import MODERN_MENU_STYLE
        menu.setStyleSheet(MODERN_MENU_STYLE)
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        
        ocr_action = menu.addAction(ui_text(lang, "menu_ocr_recognize"))
        copy_action = menu.addAction(ui_text(lang, "ocr_copy_btn"))
        save_action = menu.addAction(ui_text(lang, "thumbnail_save_as"))
        action = menu.exec(pos)

        if action == copy_action:
            cb = QtWidgets.QApplication.clipboard()
            buffer = io.BytesIO()
            self.pil_image.save(buffer, format="PNG")
            qimg = QtGui.QImage.fromData(buffer.getvalue())
            cb.setImage(qimg)
        elif action == save_action:
            default_name = f"pin_{QtCore.QDateTime.currentDateTime().toString('MMdd_HHmmss')}.png"
            file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, ui_text(lang, "thumbnail_save_as"), default_name, "Images (*.png *.jpg *.bmp)"
            )
            if file_path:
                self.pil_image.save(file_path)
        elif action == ocr_action:
            self.ocr_requested.emit(self.pixmap, self)

class PinnedImageManager(QtCore.QObject):
    """Manages multiple pinned image windows."""
    ocr_requested = QtCore.pyqtSignal(object, object)  # pixmap, source_win

    def __init__(self):
        super().__init__()
        self._windows = []
        
    def pin_image(self, pil_image: Image.Image):
        try:
            logger.info(f"Pinning image: {pil_image.size} mode={pil_image.mode}")
            win = PinnedImageWindow(pil_image)

            # Cascade offset so multiple pins don't stack on top of each other.
            screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
            cascade = 30  # px per existing window
            n = len(self._windows)
            x = win.x() + n * cascade
            y = win.y() + n * cascade

            # Clamp to keep the window fully on screen.
            if x + win.width() > screen.right():
                x = screen.right() - win.width()
            if y + win.height() > screen.bottom():
                y = screen.bottom() - win.height()

            win.move(x, y)
            win.ocr_requested.connect(self.ocr_requested.emit)
            win.show()
            self._windows.append(win)
            win.destroyed.connect(lambda: self._remove_window(win))
            logger.info(f"Pinned image window shown. Total windows: {len(self._windows)}")
        except Exception as e:
            logger.exception(f"Failed to pin image: {e}")
        
    def _remove_window(self, win):
        if win in self._windows:
            self._windows.remove(win)

pinned_image_manager = PinnedImageManager()
