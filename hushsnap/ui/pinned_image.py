import logging
import os
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

        # Enforce a minimum content size so tiny screenshots still
        # produce usable pinned windows.
        MIN_DIM = 80
        if img_w < MIN_DIM or img_h < MIN_DIM:
            scale = max(MIN_DIM / img_w, MIN_DIM / img_h)
            img_w *= scale
            img_h *= scale

        # Limit initial size to 80% of screen
        max_w = screen.width() * 0.8
        max_h = screen.height() * 0.8

        if img_w > max_w or img_h > max_h:
            ratio = min(max_w / img_w, max_h / img_h)
            img_w *= ratio
            img_h *= ratio

        # Dynamic shadow: narrower for small content so the shadow
        # doesn't visually dominate the image.
        content_min = min(img_w, img_h)
        if content_min < 80:
            self.shadow_width = 3
        elif content_min < 160:
            self.shadow_width = 5
        # else keep the default 8 (set above)

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
        self._drag_start_geometry = None
        self._drag_offset = QtCore.QPoint(0, 0)
        self._resize_threshold = 16  # max corner hit-zone; scaled down for small windows
        
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
            morph.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)  # smooth landing, no bounce
            
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
        """Reposition the close button; does NOT change visibility."""
        close_x = self.width() - 26
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

        # Dynamic hit threshold: scales with window size so small windows
        # still have a usable drag zone in the centre.
        hit = min(self._resize_threshold, rect.width() // 4, rect.height() // 4)
        if hit < 6:
            hit = min(6, rect.width() // 2, rect.height() // 2)

        # Only detect edges within or near the content rect
        if not rect.adjusted(-hit, -hit, hit, hit).contains(pos):
            return QtCore.Qt.Edge(0)

        edge = QtCore.Qt.Edge(0)
        is_left = pos.x() < rect.left() + hit
        is_right = pos.x() > rect.right() - hit
        is_top = pos.y() < rect.top() + hit
        is_bottom = pos.y() > rect.bottom() - hit

        # When the window is so small that opposing edges both match, treat
        # the click as a drag rather than a broken resize.
        if is_left and is_right:
            is_left = is_right = False
        if is_top and is_bottom:
            is_top = is_bottom = False

        if (is_left or is_right) and (is_top or is_bottom):
            if is_left:
                edge |= QtCore.Qt.Edge.LeftEdge
            if is_right:
                edge |= QtCore.Qt.Edge.RightEdge
            if is_top:
                edge |= QtCore.Qt.Edge.TopEdge
            if is_bottom:
                edge |= QtCore.Qt.Edge.BottomEdge

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
        """Show a lightweight centred toast that fades out after *duration_ms*.

        The toast is an independent top-level window so it is never clipped
        by a small pinned-image window.
        """
        toast = QtWidgets.QLabel(text, None)
        toast.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        toast.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        toast.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        toast.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        toast.setStyleSheet(
            "QLabel {"
            " background-color: rgba(36, 36, 36, 0.94);"
            " color: #f0f0f0;"
            " border-radius: 10px;"
            " padding: 9px 20px;"
            " font-size: 13px;"
            " font-family: \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif;"
            "}"
        )
        toast.adjustSize()

        # Position centred over the pinned image (global screen coordinates)
        global_center = self.mapToGlobal(
            QtCore.QPoint(self.width() // 2, self.height() // 2)
        )
        toast.move(
            global_center.x() - toast.width() // 2,
            global_center.y() - toast.height() // 2,
        )

        # Subtle drop shadow for depth
        shadow = QtWidgets.QGraphicsDropShadowEffect(toast)
        shadow.setBlurRadius(16)
        shadow.setColor(QtGui.QColor(0, 0, 0, 90))
        shadow.setOffset(0, 4)
        toast.setGraphicsEffect(shadow)

        toast.show()

        # ── fade-out timer (uses windowOpacity — toast is a top-level window) ──
        fade_timer = QtCore.QTimer(toast)
        fade_step_ms = 30
        fade_total_ms = 400
        fade_steps = fade_total_ms // fade_step_ms
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
            toast.setWindowOpacity(step_values[idx])
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
        
        # Shadow for menu
        shadow = QtWidgets.QGraphicsDropShadowEffect(menu)
        shadow.setBlurRadius(15)
        shadow.setColor(QtGui.QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        menu.setGraphicsEffect(shadow)
        
        ocr_action = menu.addAction(ui_text(lang, "menu_ocr_recognize"))
        copy_action = menu.addAction(ui_text(lang, "pin_copy_image"))
        menu.addSeparator()
        desktop_action = menu.addAction(ui_text(lang, "thumbnail_save_to_desktop"))
        save_action = menu.addAction(ui_text(lang, "thumbnail_save_as"))
        
        action = menu.exec(pos)

        if action == copy_action:
            cb = QtWidgets.QApplication.clipboard()
            buffer = io.BytesIO()
            self.pil_image.save(buffer, format="PNG")
            qimg = QtGui.QImage.fromData(buffer.getvalue())
            cb.setImage(qimg)
            self.show_toast(ui_text(lang, "pin_image_copied"))
        elif action == desktop_action:
            try:
                desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                timestamp = QtCore.QDateTime.currentDateTime().toString("MMdd_HH-mm-ss")
                base = f"pin_{timestamp}"
                file_path = os.path.join(desktop, f"{base}.png")
                counter = 1
                while os.path.exists(file_path):
                    file_path = os.path.join(desktop, f"{base}({counter}).png")
                    counter += 1
                self.pil_image.save(file_path)
            except Exception:
                logger.exception("Failed to save pinned image to desktop")
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
        
    def pin_image(self, pil_image: Image.Image, morph_pos=None, morph_size=None):
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
            win.set_morph_source(morph_pos, morph_size)
            win.ocr_requested.connect(self.ocr_requested.emit)
            win.show()
            # Dismiss the thumbnail now that the pinned window is taking over
            from .thumbnail import thumbnail_manager
            thumbnail_manager.dismiss_current()
            self._windows.append(win)
            win.destroyed.connect(lambda: self._remove_window(win))
            logger.info(f"Pinned image window shown. Total windows: {len(self._windows)}")
        except Exception as e:
            logger.exception(f"Failed to pin image: {e}")
        
    def _remove_window(self, win):
        if win in self._windows:
            self._windows.remove(win)

pinned_image_manager = PinnedImageManager()
