import os
import time
import tempfile
import logging
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PIL import Image
import io

from ..constants import (
    THUMBNAIL_WIDTH,
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
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        
        # Calculate aspect ratio
        ratio = self.pixmap.height() / self.pixmap.width()
        self.target_height = int(THUMBNAIL_WIDTH * ratio)
        self.setFixedSize(THUMBNAIL_WIDTH, self.target_height)
        
        # 3. Position and Animation
        # Use availableGeometry to account for the taskbar
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.end_x = screen.x() + screen.width() - THUMBNAIL_WIDTH - THUMBNAIL_MARGIN
        self.end_y = screen.y() + screen.height() - self.target_height - THUMBNAIL_MARGIN
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

    def _pil_to_qpixmap(self, pil_img: Image.Image) -> QtGui.QPixmap:
        """Convert PIL Image to QPixmap efficiently."""
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        # CRITICAL: QImage holds a pointer to 'data'. We must .copy() it 
        # because 'data' is a local variable that will be GC'd.
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
        self.timer.start(THUMBNAIL_DISPLAY_MS)

    def enterEvent(self, event):
        """Pause timer on hover."""
        self.timer.stop()
        self.fade_anim.stop()
        self.setWindowOpacity(1.0)

    def leaveEvent(self, event):
        """Resume timer on leave."""
        if not self._is_dragging:
            self.timer.start(THUMBNAIL_DISPLAY_MS)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            return
        if not self._drag_start_pos:
            return
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < 5:
            return

        self._start_drag()

    def _start_drag(self):
        self._is_dragging = True
        self.timer.stop()
        
        # Visual feedback: scale down and transparency
        self.setWindowOpacity(THUMBNAIL_DRAG_OPACITY)
        scaled_w = int(THUMBNAIL_WIDTH * THUMBNAIL_DRAG_SCALE)
        scaled_h = int(self.target_height * THUMBNAIL_DRAG_SCALE)
        # We don't change the actual window size to avoid jumpiness, 
        # just the painting in paintEvent if we wanted to be fancy.
        # For now, just transparency is good.

        # Prepare temporary file
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"Screenshot_{timestamp}.png"
        temp_path = os.path.join(tempfile.gettempdir(), filename)
        self.pil_image.save(temp_path, "PNG")

        # Create Drag object
        drag = QtGui.QDrag(self)
        mime_data = QtCore.QMimeData()
        url = QtCore.QUrl.fromLocalFile(temp_path)
        mime_data.setUrls([url])
        drag.setMimeData(mime_data)
        
        # Set drag icon (the thumbnail itself)
        drag_pixmap = self.pixmap.scaled(
            scaled_w, scaled_h, 
            QtCore.Qt.AspectRatioMode.KeepAspectRatio, 
            QtCore.Qt.TransformationMode.SmoothTransformation
        )
        drag.setPixmap(drag_pixmap)
        drag.setHotSpot(QtCore.QPoint(scaled_w // 2, scaled_h // 2))

        # Execute drag
        drag.exec(QtCore.Qt.DropAction.CopyAction)
        
        # Cleanup file after a short delay to ensure target app has read it
        def cleanup_temp():
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    logger.debug(f"Cleaned up temporary drag file: {temp_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp file {temp_path}: {e}")
        
        # 5 seconds delay is usually safe for OS to finish the copy
        QtCore.QTimer.singleShot(5000, cleanup_temp)
        
        # Cleanup state
        self._is_dragging = False
        self.close()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)

        # Draw rounded rect clip
        path = QtGui.QPainterPath()
        path.addRoundedRect(
            QtCore.QRectF(self.rect()), 
            THUMBNAIL_CORNER_RADIUS, 
            THUMBNAIL_CORNER_RADIUS
        )
        painter.setClipPath(path)

        # Draw pixmap
        painter.drawPixmap(self.rect(), self.pixmap)
        
        # Optional: Subtle border
        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 50), 1))
        painter.drawRoundedRect(
            QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            THUMBNAIL_CORNER_RADIUS,
            THUMBNAIL_CORNER_RADIUS
        )

class ThumbnailManager(QtCore.QObject):
    """
    Manages thumbnail window creation from any thread.
    """
    show_signal = QtCore.pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.show_signal.connect(self._do_show)
        self._windows = [] # Keep references to prevent GC

    def _do_show(self, pil_image: Image.Image):
        # Safely cleanup closed or deleted windows from the reference list
        alive_windows = []
        for w in self._windows:
            try:
                # sip.isdeleted or simply checking if the wrapper still works
                if not w.isHidden():
                    alive_windows.append(w)
            except RuntimeError:
                # "wrapped C/C++ object has been deleted" - skip this one
                continue
        self._windows = alive_windows
        
        win = ThumbnailWindow(pil_image)
        self._windows.append(win)
        win.show()

# Global manager instance
_manager = ThumbnailManager()

def qpixmap_to_pil(pixmap: QtGui.QPixmap) -> Image.Image:
    """Convert QPixmap to PIL Image via PNG buffer for maximum compatibility."""
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QBuffer.OpenModeFlag.ReadWrite)
    pixmap.save(buffer, "PNG")
    return Image.open(io.BytesIO(buffer.data().data()))

def show_thumbnail(pil_image: Image.Image):
    """
    Public API to show a floating thumbnail.
    Safe to call from any thread.
    """
    if _manager:
        _manager.show_signal.emit(pil_image)
