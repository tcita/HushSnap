import logging
import os
import io
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets, QtSvg
from .constants import TEXT_OUTLINE_WIDTH, TEXT_OUTLINE_COLOR, TEXT_FILL_COLOR

logger = logging.getLogger(__name__)

def _load_editor_icon(name: str, color: QtGui.QColor = QtGui.QColor("#ccc")) -> QtGui.QIcon:
    """Load an SVG icon, apply color, and return QIcon."""
    icon_path = os.path.join(os.path.dirname(__file__), "..", "icons", f"edit_{name}.svg")
    if not os.path.exists(icon_path):
        logger.warning(f"Editor icon not found: {icon_path}")
        return QtGui.QIcon()

    try:
        with open(icon_path, "r", encoding="utf-8") as f:
            svg_data = f.read()
        
        # Colorize by replacing currentColor
        svg_data = svg_data.replace('currentColor', color.name())
        
        renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg_data.encode("utf-8")))
        pixmap = QtGui.QPixmap(32, 32)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(pixmap)
        renderer.render(painter)
        painter.end()

        icon = QtGui.QIcon()
        icon.addPixmap(pixmap, QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
        # Explicit disabled variant: the same glyph drawn at low opacity so a
        # disabled icon button reads as clearly inactive.
        disabled_px = QtGui.QPixmap(32, 32)
        disabled_px.fill(QtCore.Qt.GlobalColor.transparent)
        dp = QtGui.QPainter(disabled_px)
        dp.setOpacity(0.35)
        dp.drawPixmap(0, 0, pixmap)
        dp.end()
        icon.addPixmap(disabled_px, QtGui.QIcon.Mode.Disabled, QtGui.QIcon.State.Off)
        return icon
    except Exception as e:
        logger.error(f"Failed to load editor icon {name}: {e}")
        return QtGui.QIcon()

def _draw_outlined_text(
    painter: QtGui.QPainter,
    pos: QtCore.QPointF,
    text: str,
    font: QtGui.QFont,
) -> None:
    """Draw text as a black outline with a white fill on top."""
    painter.save()
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

    path = QtGui.QPainterPath()
    path.addText(pos, font, text)

    # Calculate outline width based on actual rendered pixel size.
    # QFont.pixelSize() returns the value we *set*, which may differ from
    # the actual rendered size for non-scalable fonts (e.g. "Roman").
    # QFontInfo.pixelSize() gives the true rendered size — use that so the
    # outline stays proportional to the glyphs, not the request.
    ps = QtGui.QFontInfo(font).pixelSize()
    if ps <= 0:
        ps = font.pixelSize()

    outline_w = max(1.0, ps * TEXT_OUTLINE_WIDTH)
    
    # 1. Draw the black outline
    outline_pen = QtGui.QPen(QtGui.QColor(TEXT_OUTLINE_COLOR), outline_w)
    outline_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    outline_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    painter.setPen(outline_pen)
    painter.setBrush(QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush))
    painter.drawPath(path)

    # 2. Draw the white fill on top
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QBrush(QtGui.QColor(TEXT_FILL_COLOR)))
    painter.drawPath(path)
    
    painter.restore()

def _make_circle_cursor(size: int = 12) -> QtGui.QCursor:
    """Create a Photoshop-style circle cursor matching the brush size."""
    size = max(4, size)
    s = size + 4
    px = QtGui.QPixmap(s, s)
    px.fill(QtCore.Qt.GlobalColor.transparent)
    
    painter = QtGui.QPainter(px)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    
    cx, cy = s / 2.0, s / 2.0
    r = size / 2.0
    
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1.2))
    painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)
    painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 180), 0.8))
    painter.drawEllipse(QtCore.QPointF(cx, cy), r - 0.5, r - 0.5)
    painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1))
    painter.drawPoint(QtCore.QPointF(cx, cy))
    
    painter.end()
    return QtGui.QCursor(px, int(cx), int(cy))

def _pil_to_qpixmap(pil_img: Image.Image) -> QtGui.QPixmap:
    """Convert PIL Image to QPixmap."""
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimage = QtGui.QImage(
        data, pil_img.size[0], pil_img.size[1],
        QtGui.QImage.Format.Format_RGBA8888,
    ).copy()
    return QtGui.QPixmap.fromImage(qimage)

def _qpixmap_to_pil(pixmap: QtGui.QPixmap) -> Image.Image:
    """Convert QPixmap to PIL Image via PNG buffer."""
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QBuffer.OpenModeFlag.ReadWrite)
    pixmap.save(buffer, "PNG")
    return Image.open(io.BytesIO(buffer.data().data()))
