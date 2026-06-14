import io
import math
import os
import time
import logging
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
from PIL import Image

from .styles import BRAND_GREEN, MODERN_MENU_STYLE
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

        # 3. Blurred background: crop-to-fill → Gaussian blur → QPixmap
        self.blurred_bg = self._create_blurred_background(pil_image)
        
        # 3. Action Pill (Pin + Close)
        self.action_pill = QtWidgets.QFrame(self)
        self.action_pill.setObjectName("actionPill")
        # WA_TranslucentBackground makes the QFrame a transparent hit-test
        # container only — its background is drawn by the parent paintEvent
        # so Qt never gets a chance to pre-fill the bounding rect with white.
        self.action_pill.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.action_pill.setFixedSize(76, 28)

        pill_layout = QtWidgets.QHBoxLayout(self.action_pill)
        pill_layout.setContentsMargins(8, 0, 8, 0)
        pill_layout.setSpacing(5)

        self.pin_btn = QtWidgets.QPushButton(self.action_pill)
        self.pin_btn.setFixedSize(24, 24)
        self.pin_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setToolTip("Pin to Screen")
        self.pin_btn.setIcon(self._make_pin_icon())
        self.pin_btn.setIconSize(QtCore.QSize(14, 14))
        self.pin_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self.pin_btn.clicked.connect(self.pin_requested_signal.emit)

        # Vertical separator
        sep = QtWidgets.QFrame(self.action_pill)
        sep.setFixedSize(1, 14)
        sep.setAutoFillBackground(False)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 40);")

        self.close_btn = QtWidgets.QPushButton(self.action_pill)
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("Close")
        self.close_btn.setIcon(self._make_close_icon())
        self.close_btn.setIconSize(QtCore.QSize(14, 14))
        self.close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self.close_btn.clicked.connect(self.close)

        # Event filters for half-pill hover effect
        self.pin_btn.installEventFilter(self)
        self.close_btn.installEventFilter(self)
        self._pill_hover_timer = QtCore.QTimer(self)
        self._pill_hover_timer.setSingleShot(True)
        self._pill_hover_timer.timeout.connect(self._restore_pill_style)
        
        pill_layout.addWidget(self.pin_btn)
        pill_layout.addWidget(sep)
        pill_layout.addWidget(self.close_btn)
        
        # Center the pill at the top of the card
        pill_x = self.shadow_padding + (self.card_width - self.action_pill.width()) // 2
        self.action_pill.move(pill_x, self.shadow_padding + 6)
        self.action_pill.hide()

        # 4. Position and Animation
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
        self.timer.timeout.connect(self._on_auto_dismiss)
        
        self._is_dragging = False
        self._drag_start_pos = None
        self._menu_active = False
        self._hovered = False
        self._loading = False
        self._loading_progress = 0.0
        self._loading_anim = None  # QVariantAnimation for pulsing bar
        self._pill_state = 'none'  # 'none' | 'pin' | 'close' — drives paintEvent

        # Countdown progress bar — thin line at card bottom that shrinks
        # over the display duration, so the user always knows how much
        # time is left before the thumbnail auto-dismisses.
        self._countdown_deadline = None   # monotonic timestamp (seconds) or None
        self._countdown_total_s = 0.0     # total configured display time
        self._countdown_tick = QtCore.QTimer(self)
        self._countdown_tick.setInterval(50)  # ~20 fps — smooth enough
        self._countdown_tick.timeout.connect(self._tick_countdown)

    @staticmethod
    def _make_pin_icon():
        """Creates a vector pin icon matching OcrPopup style."""
        def draw_pin(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(QtGui.QPen(QtGui.QColor(color_str), 2, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
            # Tilted look
            p.translate(12, 12)
            p.rotate(-45)
            p.translate(-12, -12)
            path = QtGui.QPainterPath()
            path.moveTo(12, 17); path.lineTo(12, 22)
            path.moveTo(9, 11); path.lineTo(6, 14); path.lineTo(6, 16); path.lineTo(18, 16); path.lineTo(18, 14); path.lineTo(15, 11); path.lineTo(15, 6); path.lineTo(9, 6)
            path.closeSubpath()
            path.addEllipse(QtCore.QRectF(8, 2, 8, 4))
            p.drawPath(path)
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_pin(BRAND_GREEN), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_pin("#8ef0b6"), QtGui.QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_close_icon():
        """Creates a vector X icon matching OcrPopup style."""
        def draw_close(color_str):
            pixmap = QtGui.QPixmap(24, 24)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pixmap)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(QtGui.QPen(QtGui.QColor(color_str), 2.2, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
            p.drawLine(QtCore.QPointF(8, 8), QtCore.QPointF(16, 16))
            p.drawLine(QtCore.QPointF(16, 8), QtCore.QPointF(8, 16))
            p.end()
            return pixmap

        icon = QtGui.QIcon()
        icon.addPixmap(draw_close("#ffffff"), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(draw_close("#ff5c5c"), QtGui.QIcon.Mode.Active)
        return icon

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

    def _create_blurred_background(self, pil_img: Image.Image) -> QtGui.QPixmap:
        """Crop-to-fill the card aspect ratio, scale down, apply Gaussian blur,
        and return a QPixmap to use as the card's decorative background."""
        from PIL import ImageFilter

        card_w, card_h = self.card_width, self.card_height
        img_w, img_h = pil_img.size
        card_aspect = card_w / card_h
        img_aspect = img_w / img_h

        fill = pil_img.copy()
        # Center-crop to match the card's 16:10 aspect ratio
        if img_aspect > card_aspect:
            new_w = int(img_h * card_aspect)
            offset = (img_w - new_w) // 2
            fill = fill.crop((offset, 0, offset + new_w, img_h))
        else:
            new_h = int(img_w / card_aspect)
            offset = (img_h - new_h) // 2
            fill = fill.crop((0, offset, img_w, offset + new_h))

        fill = fill.resize((card_w, card_h), Image.LANCZOS)
        blurred = fill.filter(ImageFilter.GaussianBlur(radius=20))
        return self._pil_to_qpixmap(blurred)

    def start_loading(self):
        """Switch to loading state: stop timer, show a pulsing progress bar."""
        self._loading = True
        self.timer.stop()
        self.fade_anim.stop()
        self._pause_countdown()
        self.setWindowOpacity(1.0)
        self.action_pill.hide()

        self._loading_anim = QtCore.QVariantAnimation(self)
        self._loading_anim.setDuration(1200)
        self._loading_anim.setStartValue(0.0)
        self._loading_anim.setEndValue(1.0)
        self._loading_anim.setLoopCount(-1)
        self._loading_anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)
        self._loading_anim.valueChanged.connect(self._on_loading_tick)
        self._loading_anim.start()
        self.update()

    def _on_loading_tick(self, value: float):
        self._loading_progress = value
        self.update()

    def dismiss(self):
        """Stop loading and close the thumbnail (called when OCR popup is ready)."""
        self._loading = False
        self._stop_countdown()
        if self._loading_anim is not None:
            self._loading_anim.stop()
            self._loading_anim = None
        self.close()

    def _start_timer(self):
        """Start the auto-dismiss timer if a finite display time is configured.
        When display_ms is 0 ('Never hide'), the timer is not started.  """
        ms = self._get_display_ms()
        if ms > 0:
            self.timer.start(ms)

    def _on_auto_dismiss(self):
        """Timer fired — stop the countdown bar and begin fade-out."""
        self._stop_countdown()
        self.fade_anim.start()

    # ── Countdown progress bar ──────────────────────────────────────────
    def _tick_countdown(self):
        """Called every 50 ms to repaint the countdown bar."""
        if self._countdown_deadline is None:
            self._countdown_tick.stop()
            return
        remaining = self._countdown_deadline - time.monotonic()
        if remaining <= 0:
            self._countdown_deadline = None
            self._countdown_tick.stop()
        self.update()

    def _start_countdown(self):
        """Begin / resume the countdown progress bar.
        Mirrors _start_timer so the bar always reflects the same duration.  """
        ms = self._get_display_ms()
        if ms <= 0:
            self._countdown_deadline = None
            self._countdown_tick.stop()
            return
        self._countdown_total_s = ms / 1000.0
        self._countdown_deadline = time.monotonic() + self._countdown_total_s
        self._countdown_tick.start()

    def _pause_countdown(self):
        """Freeze the countdown bar (e.g. on hover / drag / menu)."""
        self._countdown_tick.stop()

    def _stop_countdown(self):
        """Tear down the countdown entirely (e.g. on dismiss / close)."""
        self._countdown_tick.stop()
        self._countdown_deadline = None
        self.update()

    def refresh_timer(self):
        """Re-read config and immediately apply the new display duration.

        Called when the user changes the thumbnail-display-time setting so
        the currently-visible thumbnail reacts without waiting for the next
        screenshot.  Switches between never-hide ↔ countdown seamlessly.
        """
        ms = self._get_display_ms()
        if ms <= 0:
            # "Never hide" — cancel any running timer / countdown, restore full opacity
            self.timer.stop()
            self.fade_anim.stop()
            self._stop_countdown()
            self.setWindowOpacity(1.0)
        else:
            # Finite duration — restart both timer and countdown from now
            self.timer.stop()
            self.fade_anim.stop()
            self.setWindowOpacity(1.0)
            self.timer.start(ms)
            self._start_countdown()

    def showEvent(self, event):
        super().showEvent(event)
        self.pos_anim.start()
        self._start_timer()
        self._start_countdown()

    def enterEvent(self, event):
        """Pause timer on hover, activate visual feedback, and show buttons."""
        self.timer.stop()
        self.fade_anim.stop()
        self._pause_countdown()
        self.setWindowOpacity(1.0)
        self._hovered = True
        self.update()
        self.action_pill.show()
        self.action_pill.raise_()

    def leaveEvent(self, event):
        """Resume timer on leave, deactivate visual feedback, and hide buttons."""
        if not self._is_dragging and not self._menu_active:
            self._start_timer()
            self._start_countdown()
        self._hovered = False
        self.update()
        self._restore_pill_style()
        self.action_pill.hide()

    # ── Pill hover ─────────────────────────────────────────────────
    # Hover state is tracked via self._pill_state and rendered in paintEvent.
    # No stylesheet gradients — parent QPainter handles it artifact-free.

    def _restore_pill_style(self):
        self._pill_hover_timer.stop()
        self._pill_state = 'none'
        self.update()

    def eventFilter(self, obj, event):
        if obj == self.pin_btn:
            if event.type() == QtCore.QEvent.Type.Enter:
                self._pill_hover_timer.stop()
                self._pill_state = 'pin'
                self.update()
            elif event.type() == QtCore.QEvent.Type.Leave:
                self._pill_hover_timer.start(60)
        elif obj == self.close_btn:
            if event.type() == QtCore.QEvent.Type.Enter:
                self._pill_hover_timer.stop()
                self._pill_state = 'close'
                self.update()
            elif event.type() == QtCore.QEvent.Type.Leave:
                self._pill_hover_timer.start(60)
        return super().eventFilter(obj, event)

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
                if self.action_pill.geometry().contains(release_pos):
                    return
                logger.debug(f"Thumbnail click triggered at {release_pos}")
                self.clicked_signal.emit()
                if not self._loading:
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
        self._pause_countdown()
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
            # Don't close yet — the pinned window will dismiss us
            # after it appears, keeping the transition seamless.
        else:
            self._hovered = False
            self.update()
            self._start_timer()
            self._start_countdown()

    def _start_drag(self):
        self._is_dragging = True
        self.timer.stop()
        self._pause_countdown()
        
        logger.debug("--- Drag-and-Drop Start ---")
        
        # Visual feedback
        self.setWindowOpacity(THUMBNAIL_DRAG_OPACITY)
        scaled_w = int(self.card_width * THUMBNAIL_DRAG_SCALE)
        scaled_h = int(self.card_height * THUMBNAIL_DRAG_SCALE)

        # Rotating cache: keep the last 5 files to prevent race conditions 
        # where an ongoing slow upload might fail if we delete its source 
        # file too aggressively.
        from ..config import get_user_data_dir, resolve_physical_path
        cache_dir_path = resolve_physical_path(get_user_data_dir() / "drag_cache")
        cache_dir_path.mkdir(parents=True, exist_ok=True)

        # Use filename sorting: drag_YYYYMMDD_HHMMSS_mmm.png sorts perfectly alphabetically
        try:
            existing_files = sorted(
                [f for f in cache_dir_path.glob("drag_*.png") if f.is_file()],
                key=lambda x: x.name
            )
            
            file_count = len(existing_files)
            if file_count > 4:
                # Keep the 4 most recent, delete everything else
                to_delete = existing_files[:-4]
                logger.debug(f"Cache rotation: found {file_count} files, deleting {len(to_delete)} oldest.")
                for f in to_delete:
                    try:
                        f.unlink()
                    except OSError as e:
                        # On Windows, this usually means the file is still locked by a browser/explorer
                        logger.debug(f"Rotation skip: could not delete {f.name} (likely locked): {e}")
            else:
                logger.debug(f"Cache rotation: {file_count} files present, no cleanup needed.")
        except Exception as e:
            logger.warning(f"Error during cache rotation: {e}")

        ts = time.strftime("%Y%m%d_%H%M%S")
        ms = int(time.time() * 1000) % 1000
        temp_path_obj = cache_dir_path / f"drag_{ts}_{ms:03d}.png"
        temp_path = str(temp_path_obj)

        logger.debug(f"Saving drag cache to: {temp_path}")

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

        # Tell Explorer the temp file exists before QDrag references it.
        if os.name == 'nt':
            try:
                import ctypes
                shell32 = ctypes.windll.shell32
                SHCNE_CREATE = 0x00000002
                SHCNF_PATH = 0x00000001
                SHCNF_FLUSHNOWAIT = 0x00000004
                shell32.SHChangeNotify(SHCNE_CREATE,
                                       SHCNF_PATH | SHCNF_FLUSHNOWAIT,
                                       temp_path, None)
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
        # Force CopyAction only. This ensures the file stays in our drag_cache 
        # so our 5-file rotation logic can safely manage its lifecycle. 
        # Otherwise, Windows may 'Move' the file to the browser/folder, 
        # deleting it from our cache immediately and bypassing our safety buffer.
        result = drag.exec(QtCore.Qt.DropAction.CopyAction)
        logger.debug(f"Drag finished. Result: {result}")
        
        if result != QtCore.Qt.DropAction.IgnoreAction and os.name == 'nt':
            # The shell handled the copy/move, but some Explorer views
            # (especially cloud-backed folders) may not refresh on their
            # own.  SHCNE_UPDATEDIR asks the shell to re-enumerate folder
            # contents so the file appears immediately without a manual F5.
            try:
                SHCNE_UPDATEDIR = 0x00001000
                SHCNF_IDLIST = 0x00000000
                SHCNF_FLUSH = 0x00001000
                shell32.SHChangeNotify(SHCNE_UPDATEDIR,
                                       SHCNF_IDLIST | SHCNF_FLUSH,
                                       None, None)
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
                self.action_pill.hide()
                self._start_timer()
                self._start_countdown()
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
        painter.setClipPath(path)

        # Blurred background fills the card
        painter.drawPixmap(self.card_rect, self.blurred_bg)

        # Subtle dark overlay so the sharp thumbnail pops against the blurred bg
        painter.fillPath(path, QtGui.QColor(0, 0, 0, 50))

        # Soft elevation shadow underneath the thumbnail — multi-pass blur
        # so the sharp content "floats" above the blurred background rather
        # than sitting flat against it.
        shadow_rect = QtCore.QRectF(self.pixmap_rect)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        for i in range(1, 6):
            alpha = int(45 * (1.0 - i / 6.0))
            spread = i * 2.0
            painter.setBrush(QtGui.QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(
                shadow_rect.adjusted(spread, spread + 1.5, spread, spread + 1.5),
                8 + i, 8 + i,
            )

        # Sharp thumbnail centered on top
        painter.drawPixmap(self.pixmap_rect, self.scaled_pixmap)

        # Thin separator border around the sharp thumbnail
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        thumb_path = QtGui.QPainterPath()
        thumb_rect = QtCore.QRectF(self.pixmap_rect).adjusted(-0.5, -0.5, 0.5, 0.5)
        thumb_path.addRoundedRect(thumb_rect, 6, 6)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 45), 1))
        painter.drawPath(thumb_path)

        # Loading indicator — thin animated bar at the bottom of the card
        if self._loading:
            bar_h = 2
            margin = 10
            bar_y = self.card_rect.bottom() - bar_h - 3
            track_w = self.card_rect.width() - margin * 2

            # Background track
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 18))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect.left() + margin, bar_y, track_w, bar_h),
                1, 1,
            )

            # Animated segment — oscillates left↔right with InOutSine easing
            seg_w = min(50, track_w)
            travel = track_w - seg_w
            t = self._loading_progress
            offset = (math.sin(t * math.pi * 2 - math.pi / 2) + 1) / 2 * travel
            painter.setBrush(QtGui.QColor(BRAND_GREEN))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect.left() + margin + offset, bar_y, seg_w, bar_h),
                1, 1,
            )
            # Reset brush so it doesn't leak into the card border fill below
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        # Countdown progress bar — thin shrinking line at card bottom.
        # Only shown when idle (not loading, not hovered) and a finite
        # display time is configured.  Hidden during hover because the
        # timer is paused then — a frozen bar conveys no useful info.
        elif (not self._hovered
              and self._countdown_deadline is not None
              and self._countdown_total_s > 0):
            remaining = max(0.0, self._countdown_deadline - time.monotonic())
            progress = remaining / self._countdown_total_s  # 1.0 → 0.0

            bar_h = 3
            margin = 10
            bar_y = self.card_rect.bottom() - bar_h - 3
            track_w = self.card_rect.width() - margin * 2

            # Subtle background track
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 25))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect.left() + margin, bar_y, track_w, bar_h),
                1, 1,
            )

            # Fill colour: neutral white, warming to a soft red in the last 2 s
            if remaining <= 2.0:
                t = max(0.0, remaining) / 2.0  # 1.0 → 0.0 (2 s → 0 s)
                r = 255
                g = int(90 + (255 - 90) * t)
                b = int(90 + (255 - 90) * t)
                a = int(55 + (65 - 55) * (1.0 - t))  # 55 → 65 (bolder as time runs out)
            else:
                r, g, b, a = 255, 255, 255, 55

            fill_w = int(track_w * progress)
            if fill_w > 0:
                painter.setBrush(QtGui.QColor(r, g, b, a))
                painter.drawRoundedRect(
                    QtCore.QRectF(self.card_rect.left() + margin, bar_y, fill_w, bar_h),
                    1, 1,
                )

            # Reset brush so it doesn't leak into the card border fill below
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 40), 1))
        painter.drawRoundedRect(QtCore.QRectF(self.card_rect).adjusted(0.5, 0.5, -0.5, -0.5), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)

        if self._hovered:
            painter.setPen(QtGui.QPen(QtGui.QColor(BRAND_GREEN), 1.5))
            painter.drawRoundedRect(QtCore.QRectF(self.card_rect).adjusted(1, 1, -1, -1), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)

        # ── Pill background ────────────────────────────────────────────────
        # Drawn by the parent painter so Qt never pre-fills a child-widget
        # bounding rect with white before border-radius is applied.
        if self.action_pill.isVisible():
            pill_geom = QtCore.QRectF(self.action_pill.geometry())
            pill_path = QtGui.QPainterPath()
            pill_path.addRoundedRect(pill_geom, 14.0, 14.0)

            # Base dark fill
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(18, 18, 18, 185))
            painter.drawPath(pill_path)

            # Soft colour wash on the hovered half — layered over base
            if self._pill_state == 'pin':
                grad = QtGui.QLinearGradient(pill_geom.left(), 0, pill_geom.right(), 0)
                grad.setColorAt(0.00, QtGui.QColor(95, 201, 138, 58))
                grad.setColorAt(0.36, QtGui.QColor(95, 201, 138, 16))
                grad.setColorAt(0.52, QtGui.QColor(0, 0, 0, 0))
                grad.setColorAt(1.00, QtGui.QColor(0, 0, 0, 0))
                painter.setBrush(QtGui.QBrush(grad))
                painter.drawPath(pill_path)
            elif self._pill_state == 'close':
                grad = QtGui.QLinearGradient(pill_geom.left(), 0, pill_geom.right(), 0)
                grad.setColorAt(0.00, QtGui.QColor(0, 0, 0, 0))
                grad.setColorAt(0.48, QtGui.QColor(0, 0, 0, 0))
                grad.setColorAt(0.62, QtGui.QColor(210, 50, 50, 36))
                grad.setColorAt(1.00, QtGui.QColor(210, 50, 50, 78))
                painter.setBrush(QtGui.QBrush(grad))
                painter.drawPath(pill_path)

            # 1px hairline border
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1.0))
            painter.drawPath(pill_path)

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

    def current_window(self):
        """Return the current visible ThumbnailWindow, or None."""
        for w in self._windows:
            try:
                if w.isVisible():
                    return w
            except RuntimeError:
                pass
        return None

    def dismiss_current(self):
        """Close the current thumbnail immediately (called when OCR result is ready)."""
        for w in self._windows:
            try:
                w.dismiss()
            except Exception:
                pass
        self._windows.clear()

    def refresh_current(self):
        """Re-apply the display-time setting to the visible thumbnail.

        Called from the settings dialog so the user sees the change
        take effect on the currently-shown thumbnail immediately.
        """
        win = self.current_window()
        if win is not None:
            try:
                win.refresh_timer()
            except Exception:
                pass

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
