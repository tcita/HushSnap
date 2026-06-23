from typing import TYPE_CHECKING
from PyQt6 import QtCore, QtGui, QtWidgets
from ..utils import _draw_outlined_text

if TYPE_CHECKING:
    from ..image_editor import ImageEditorWindow

class EditorCanvas(QtWidgets.QWidget):
    """Custom QWidget that renders the working image and tool overlays."""

    def __init__(self, editor_window: "ImageEditorWindow"):
        super().__init__()
        self._editor = editor_window
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def _image_offset(self) -> QtCore.QPointF:
        """Offset to center the image within the canvas."""
        pm = self._editor._rendered_display_pixmap()
        if not pm:
            return QtCore.QPointF(0, 0)
        scale = self._editor._effective_scale()
        scaled_w = pm.width() * scale
        scaled_h = pm.height() * scale
        ox = (self.width() - scaled_w) / 2.0
        oy = (self.height() - scaled_h) / 2.0
        return QtCore.QPointF(ox, oy)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        scale = self._editor._effective_scale()
        if scale < 1.0:
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QtGui.QColor("#1a1a1a"))

        self._draw_checkerboard(painter, event.rect())

        pm = self._editor._rendered_display_pixmap()
        preview = self._editor._preview_active()
        angle = self._editor._preview_angle
        if pm:
            scale = self._editor._effective_scale()
            offset = self._image_offset()
            painter.save()
            painter.translate(offset)
            painter.scale(scale, scale)
            # Paint-time rotation: rotate the original display pixmap around its
            # center. No pixmap allocation, no widget resize — just a transform
            # on the existing draw, so dragging is flicker-free and the pivot
            # (image center) is exact.
            if angle is not None and angle != 0.0:
                painter.setRenderHint(
                    QtGui.QPainter.RenderHint.SmoothPixmapTransform, True
                )
                cx, cy = pm.width() / 2.0, pm.height() / 2.0
                painter.translate(cx, cy)
                painter.rotate(angle)
                painter.translate(-cx, -cy)
            painter.drawPixmap(0, 0, pm)
            # Annotations / strokes / overlay are laid out on the original image
            # geometry, so they only make sense when not previewing a transform.
            if angle is not None:
                # Rotation preview: the painter is already rotated/translated
                # above, so annotations (with text baked in by _flatten_text at
                # session start) must be drawn inside the same transform to stay
                # aligned with the rotated image. Use the session-base
                # annotations (via _rendered_annotations_pixmap), NOT the
                # mutated _annotations_pixmap — each release re-rotates from
                # base, so the preview must too, or image and annotations would
                # rotate on different origins and drift apart. Skip the live
                # stroke/overlay layers — they belong to draw tools and have no
                # meaning mid-rotation.
                annot = self._editor._rendered_annotations_pixmap()
                if annot:
                    painter.drawPixmap(0, 0, annot)
            elif not preview:
                if self._editor._annotations_pixmap:
                    painter.drawPixmap(0, 0, self._editor._annotations_pixmap)
                tool = self._editor._active_tool
                spm = getattr(tool, '_stroke_pixmap', None) if tool else None
                if spm:
                    painter.drawPixmap(0, 0, spm)
                if self._editor._overlay_pixmap:
                    painter.drawPixmap(0, 0, self._editor._overlay_pixmap)
            painter.restore()

        if not preview:
            self._render_text_items(painter)

        # Tool decorations (handles, bounding boxes) are drawn in screen space,
        # so they remain valid — and required — during a transform preview.
        tool = self._editor._active_tool
        if tool:
            tool.on_paint(self, painter)

        painter.end()

    def _render_text_items(self, painter: QtGui.QPainter) -> None:
        """Render persistent text items."""
        scale = self._editor._effective_scale()
        offset = self._image_offset()

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        text_tool = self._editor._tools.get("text")
        editing_item = None
        if text_tool and getattr(text_tool, "_editing_widget", None):
            editing_item = text_tool._editing_widget._item

        for item in self._editor._text_items:
            if item is editing_item:
                continue
            if not item.text:
                continue

            fs = max(1, int(item.font_size * scale))
            font = QtGui.QFont(item.font_family)
            font.setPixelSize(fs)
            painter.setFont(font)

            screen_x = item.img_pos.x() * scale + offset.x()
            screen_y = item.img_pos.y() * scale + offset.y()

            metrics = painter.fontMetrics()
            baseline = QtCore.QPointF(screen_x, screen_y + metrics.ascent())
            _draw_outlined_text(painter, baseline, item.text, font)

        painter.restore()

    def _checkerboard_brush(self) -> QtGui.QBrush:
        """A cached tileable checkerboard brush, built once per canvas.

        One 2*cs pixmap is generated and let Qt tile it via QBrush — far
        cheaper than the prior per-cell fillRect loop, which at high zoom
        redrew thousands of cells per repaint.
        """
        brush = getattr(self, "_checker_brush", None)
        if brush is not None:
            return brush

        cs = 8
        light = QtGui.QColor("#2a2a2a")
        dark = QtGui.QColor("#222222")
        tile = QtGui.QPixmap(cs * 2, cs * 2)
        tile.fill(light)
        p = QtGui.QPainter(tile)
        p.fillRect(QtCore.QRect(0, 0, cs, cs), dark)
        p.fillRect(QtCore.QRect(cs, cs, cs, cs), dark)
        p.end()
        brush = QtGui.QBrush(tile)
        self._checker_brush = brush
        return brush

    def _draw_checkerboard(self, painter: QtGui.QPainter,
                           clip_rect: QtCore.QRect) -> None:
        """Draw a subtle checkerboard pattern over the image's visible area.

        Always drawn, regardless of zoom — transparency should read as
        transparency at every scale, not flip to the canvas background past
        100%.
        """
        pm = self._editor._rendered_display_pixmap()
        if not pm:
            return
        scale = self._editor._effective_scale()
        angle = self._editor._preview_angle
        offset = self._image_offset()

        # During a rotation preview the rotated image exposes triangular
        # corners that only become transparent *after* the rotation commits
        # (expand=True) — mid-session the base image is still fully opaque, so
        # painting the transparency checkerboard there would advertise
        # transparency that doesn't exist yet (and the old code filled an
        # even larger diagonal-circumscribed square, leaking the pattern well
        # past the real bounding box). Let those corners fall through to the
        # canvas background (#1a1a1a) instead. Once the tool commits and
        # _preview_angle clears, the real transparent pixels own the
        # checkerboard via the plain (non-rotated) branch below.
        if angle is not None and angle != 0.0:
            return

        img_rect = QtCore.QRectF(
            offset.x(), offset.y(),
            pm.width() * scale, pm.height() * scale,
        )

        painter.fillRect(clip_rect & img_rect.toRect(),
                         self._checkerboard_brush())

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_press(self, event):
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_move(self, event):
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_release(self, event):
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_mouse_double_click(self, event):
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        """Zoom in/out centered on cursor position."""
        editor = self._editor
        if not editor._rendered_display_pixmap():
            event.ignore()
            return

        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return

        cursor_pos = event.position()
        old_offset = self._image_offset()

        old_effective = editor._effective_scale()
        img_x = (cursor_pos.x() - old_offset.x()) / old_effective
        img_y = (cursor_pos.y() - old_offset.y()) / old_effective

        factor = 1.10 if delta > 0 else 1.0 / 1.10
        new_scale = editor._scale * factor
        new_scale = max(0.10, min(new_scale, 5.0))
        if abs(new_scale - editor._scale) < 0.001:
            event.accept()
            return
        editor._scale = new_scale
        editor._resize_canvas()

        old_scroll_x = editor._scroll_area.horizontalScrollBar().value()
        old_scroll_y = editor._scroll_area.verticalScrollBar().value()
        new_offset = self._image_offset()
        new_effective = editor._effective_scale()
        new_scroll_x = int(img_x * new_effective + new_offset.x() - cursor_pos.x() + old_scroll_x)
        new_scroll_y = int(img_y * new_effective + new_offset.y() - cursor_pos.y() + old_scroll_y)

        h_bar = editor._scroll_area.horizontalScrollBar()
        v_bar = editor._scroll_area.verticalScrollBar()
        h_bar.setValue(max(h_bar.minimum(), min(new_scroll_x, h_bar.maximum())))
        v_bar.setValue(max(v_bar.minimum(), min(new_scroll_y, v_bar.maximum())))

        self.update()
        editor._update_zoom_label()
        event.accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        tool = self._editor._active_tool
        if tool and tool.on_key_press(self, event):
            event.accept()
        else:
            super().keyPressEvent(event)
