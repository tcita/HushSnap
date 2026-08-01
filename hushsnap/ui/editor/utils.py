import logging
import os
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets, QtSvg
from .constants import TEXT_OUTLINE_WIDTH, TEXT_OUTLINE_COLOR, TEXT_FILL_COLOR

logger = logging.getLogger(__name__)

# Per-UI-language preferred annotation fonts (HushSnap's own UI language, not
# the OS display language). Each is the canonical system font for that script
# on Windows, with complete glyph coverage and stable metrics - important
# because the editor's outlined text (_draw_outlined_text) scales stroke width
# to the font's pixel size, so a metrics-stable font renders cleanly. Absent a
# matching UI language, the editor falls back to the OS GeneralFont and then to
# Qt's default sans-serif (see _default_annotation_font).
_LANG_FONTS = {
    "zh": "Microsoft YaHei",
    "zh_tw": "Microsoft JhengHei",
    "ja": "Yu Gothic UI",
}


def _default_annotation_font(lang: str) -> str:
    """Return the default annotation font family for the given UI language.

    Three-tier fallback:
      1. The script-appropriate system font for HushSnap's UI language, used
         only if it is actually installed. This keeps the annotation font
         aligned with the UI language the user chose - e.g. a Simplified-Chinese
         UI defaults to Microsoft YaHei rather than the OS-display-language
         GeneralFont (which may be Segoe UI and lack CJK glyphs, forcing an
         unstable Qt fallback chain for outlined text).
      2. The OS GeneralFont (Windows = Segoe UI). A concrete, latin-complete
         system font for the common case where tier 1 doesn't apply.
      3. Empty string -> let Qt pick a default sans-serif. Almost never
         reached; a defensive guard if GeneralFont itself returns nothing.

    HushSnap runs on Windows only, so the families above are the Windows ones.
    QFontDatabase.hasFamily is not exposed as a static method in PyQt6, so we
    check membership in families() instead.
    """
    installed = set(QtGui.QFontDatabase.families())
    preferred = _LANG_FONTS.get(lang)
    if preferred and preferred in installed:
        return preferred
    sys_family = QtGui.QFontDatabase.systemFont(
        QtGui.QFontDatabase.SystemFont.GeneralFont
    ).family()
    if sys_family:
        return sys_family
    return ""


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
    """Convert a QPixmap to a PIL Image via a direct memory copy.

    A PNG round-trip (``pixmap.save(buf, "PNG")`` then ``Image.open``) would
    do the same job — but only after ~70ms–1.7s of pure-CPU compress/decompress
    that scales with both resolution and image entropy (see thumbnail.py's
    ``qpixmap_to_pil`` for the full rationale). A raw ARGB→RGBA8888 convert +
    ``constBits()`` copy produces byte-identical pixels in constant time.

    Stride safety: ``QImage.bytesPerLine`` may exceed ``width*4`` when the
    scanlines are aligned, so we copy line-by-line instead of assuming a
    contiguous buffer.
    """
    if pixmap.isNull():
        return Image.new("RGBA", (0, 0))

    img = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    w = img.width()
    h = img.height()
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(h * bpl)
    raw = bytes(ptr)

    if bpl == w * 4:
        data = raw
    else:
        # Strip per-line padding so PIL gets a tight RGBA buffer.
        data = b"".join(raw[y * bpl:(y * bpl) + w * 4] for y in range(h))

    return Image.frombytes("RGBA", (w, h), data)
