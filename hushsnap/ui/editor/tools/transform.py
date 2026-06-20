import logging
import math
from typing import Optional
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets
from .base import BaseTool
from ..models import UndoChangeType, TextItem

logger = logging.getLogger(__name__)

class MosaicTool(BaseTool):
    """Drag a rectangle to pixelate (mosaic) the region."""

    def __init__(self, editor):
        super().__init__(editor)
        self.block_size = 12
        self._start_point: Optional[tuple[int, int]] = None
        self._current_point: Optional[tuple[int, int]] = None

    def tool_id(self) -> str:
        return "mosaic"

    def on_activate(self) -> None:
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._start_point = self._to_image_coords(canvas, event.position().toPoint())
            self._current_point = self._start_point
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            return True
        return False

    def on_mouse_move(self, canvas, event) -> bool:
        if self._start_point is not None and (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            self._current_point = self._to_image_coords(canvas, event.position().toPoint())
            # Draw selection rect on overlay
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(self._editor._overlay_pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            pen = QtGui.QPen(QtGui.QColor(95, 201, 138, 200), 1.5, QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(95, 201, 138, 30)))
            x1, y1 = self._start_point
            x2, y2 = self._current_point
            painter.drawRect(QtCore.QRect(
                min(x1, x2), min(y1, y2),
                abs(x2 - x1), abs(y2 - y1),
            ))
            painter.end()
            canvas.update()
            return True
        return False

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._start_point:
            end = self._to_image_coords(canvas, event.position().toPoint())
            x1, y1 = self._start_point
            x2, y2 = end
            x1, x2 = min(x1, x2), max(x1, x2)
            y1, y2 = min(y1, y2), max(y1, y2)

            img_w, img_h = self._editor._pil_image.size
            x1 = max(0, min(x1, img_w))
            y1 = max(0, min(y1, img_h))
            x2 = max(0, min(x2, img_w))
            y2 = max(0, min(y2, img_h))

            w, h = x2 - x1, y2 - y1
            if w > 2 and h > 2:
                try:
                    old_region = self._editor._pil_image.crop((x1, y1, x2, y2))
                    region_bytes = old_region.tobytes()
                    self._editor._save_undo(
                        UndoChangeType.REGION,
                        region_bounds=QtCore.QRect(x1, y1, w, h),
                        region_pixels=region_bytes,
                    )
                    region = self._editor._pil_image.crop((x1, y1, x2, y2))
                    bs = max(1, self.block_size)
                    small_w = max(1, region.width // bs)
                    small_h = max(1, region.height // bs)
                    small = region.resize((small_w, small_h), Image.BILINEAR)
                    pixelated = small.resize(region.size, Image.NEAREST)
                    self._editor._pil_image.paste(pixelated, (x1, y1))
                    self._editor._rebuild_display()
                except Exception:
                    logger.exception("MosaicTool: failed to apply mosaic")

            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            self._editor._modified = True
            self._start_point = None
            self._current_point = None
            canvas.update()
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._start_point = None
            self._current_point = None
            self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            canvas.update()
            return True
        return False


class CropTool(BaseTool):
    """Crop tool with draggable corner / edge handles and dimmed overlay."""

    HANDLE_R = 10  # image-pixel hit radius for handles
    MIN_CROP = 10  # minimum crop dimension in image pixels

    def __init__(self, editor):
        super().__init__(editor)
        self._crop_rect: Optional[QtCore.QRect] = None
        self._dragging: Optional[str] = None
        self._drag_start_rect: Optional[QtCore.QRect] = None
        self._drag_start_img: Optional[tuple[int, int]] = None
        # Floating action buttons (children of the canvas)
        self._action_cancel: Optional[QtWidgets.QPushButton] = None
        self._action_apply: Optional[QtWidgets.QPushButton] = None

    def tool_id(self) -> str:
        return "crop"

    def on_activate(self) -> None:
        img_w, img_h = self._editor._pil_image.size
        self._crop_rect = QtCore.QRect(0, 0, img_w, img_h)
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self._create_action_buttons()
        self._redraw_overlay()
        self._editor._canvas.update()

    def on_deactivate(self) -> None:
        self._crop_rect = None
        self._dragging = None
        self._destroy_action_buttons()
        self._editor._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._editor._canvas.update()

    def _create_action_buttons(self) -> None:
        canvas = self._editor._canvas
        t = self._editor._tr

        self._action_cancel = QtWidgets.QPushButton(t("editor_crop_cancel"), canvas)
        self._action_cancel.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._action_cancel.setStyleSheet(f"""
            QPushButton {{
                background: rgba(40,40,40,200);
                border: 1px solid rgba(255,255,255,20);
                border-radius: 11px;
                padding: 4px 14px;
                color: #ccc;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: rgba(60,60,60,220);
                border-color: rgba(255,255,255,35);
                color: #fff;
            }}
            QPushButton:pressed {{
                background: rgba(28,28,28,235);
                border-color: rgba(255,255,255,20);
            }}
        """)
        self._action_cancel.clicked.connect(self.cancel_crop)

        self._action_apply = QtWidgets.QPushButton(t("editor_apply"), canvas)
        self._action_apply.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._action_apply.setStyleSheet(f"""
            QPushButton {{
                background: #5FC98A;
                border: none;
                border-radius: 11px;
                padding: 4px 16px;
                color: #fff;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #6fd99d;
            }}
            QPushButton:pressed {{
                background: #4ab87a;
            }}
        """)
        self._action_apply.clicked.connect(self.apply_crop)

        self._position_action_buttons()

    def _destroy_action_buttons(self) -> None:
        for b in (self._action_cancel, self._action_apply):
            if b:
                b.deleteLater()
        self._action_cancel = None
        self._action_apply = None

    def _position_action_buttons(self) -> None:
        """Place the two floating buttons centred just below the crop rect."""
        r = self._crop_rect
        canvas = self._editor._canvas
        if r is None or not self._action_apply:
            return
        scale = self._editor._effective_scale()
        offset = canvas._image_offset()
        cx = r.center().x() * scale + offset.x()
        cy = r.bottom() * scale + offset.y() + 8
        cancel_w = self._action_cancel.sizeHint().width()
        apply_w = self._action_apply.sizeHint().width()
        gap = 8
        total_w = cancel_w + apply_w + gap
        left = int(cx - total_w / 2)
        self._action_cancel.move(left, int(cy))
        self._action_apply.move(left + cancel_w + gap, int(cy))
        self._action_cancel.show()
        self._action_apply.show()

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        pos = event.position().toPoint()
        handle = self._hit_test(canvas, pos) if self._crop_rect else None
        if handle:
            self._dragging = handle
            self._drag_start_rect = QtCore.QRect(self._crop_rect)
            self._drag_start_img = self._to_image_coords(canvas, pos)
            return True
        img = self._to_image_coords(canvas, pos)
        if self._crop_rect and self._crop_rect.contains(img[0], img[1]):
            self._dragging = "move"
            self._drag_start_rect = QtCore.QRect(self._crop_rect)
            self._drag_start_img = img
            return True
        self._dragging = "pan"
        self._drag_start_img = (
            event.globalPosition().toPoint().x(),
            event.globalPosition().toPoint().y(),
        )
        canvas.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        return True

    def on_mouse_move(self, canvas, event) -> bool:
        pos = event.position().toPoint()
        if self._dragging == "pan":
            cur = event.globalPosition().toPoint()
            sx, sy = self._drag_start_img
            dx, dy = cur.x() - sx, cur.y() - sy
            self._drag_start_img = (cur.x(), cur.y())
            sa = self._editor._scroll_area
            hb = sa.horizontalScrollBar()
            vb = sa.verticalScrollBar()
            hb.setValue(int(hb.value() - dx))
            vb.setValue(int(vb.value() - dy))
            return True

        if self._dragging:
            img = self._to_image_coords(canvas, pos)
            self._update_rect(img[0], img[1])
            self._redraw_overlay()
            canvas.update()
            return True

        h = self._hit_test(canvas, pos) if self._crop_rect else None
        if h in ("tl", "br"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
        elif h in ("tr", "bl"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeBDiagCursor)
        elif h in ("t", "b"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        elif h in ("l", "r"):
            canvas.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        elif h == "move":
            canvas.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        else:
            canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        return bool(h)

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if self._dragging == "pan":
                canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self._dragging = None
            self._drag_start_rect = None
            self._drag_start_img = None
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            self.apply_crop()
            return True
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.cancel_crop()
            return True
        return False

    def apply_crop(self) -> None:
        if not self._crop_rect:
            return
        r = self._crop_rect
        if r.width() < self.MIN_CROP or r.height() < self.MIN_CROP:
            return
        img_w, img_h = self._editor._pil_image.size
        if (r.left() <= 0 and r.top() <= 0
                and r.x() + r.width() >= img_w
                and r.y() + r.height() >= img_h):
            self._editor._activate_tool("pan")
            return
        # Save editable text before baking, so undo can restore it.
        saved_text_items = [
            TextItem(t.text, QtCore.QPointF(t.img_pos), QtGui.QColor(t.color),
                     t.font_family, t.font_size)
            for t in self._editor._text_items
        ] if self._editor._text_items else []
        self._editor._flatten_text()
        self._editor._save_undo(UndoChangeType.FULL)
        # Patch the undo entry with the pre-flatten text so undo restores
        # editable text alongside the pre-crop image.
        if self._editor._undo_stack:
            self._editor._undo_stack[-1].text_items = saved_text_items
        try:
            cropped = self._editor._pil_image.crop(
                (r.left(), r.top(), r.x() + r.width(), r.y() + r.height())
            )
            self._editor._pil_image = cropped
            # Crop annotations and overlay to the same rect (not just clear
            # them — text is now pixels in there and should stay in the crop).
            if self._editor._annotations_pixmap:
                self._editor._annotations_pixmap = (
                    self._editor._annotations_pixmap.copy(
                        QtCore.QRect(r.left(), r.top(), r.width(), r.height())
                    )
                )
            if self._editor._overlay_pixmap:
                self._editor._overlay_pixmap = (
                    self._editor._overlay_pixmap.copy(
                        QtCore.QRect(r.left(), r.top(), r.width(), r.height())
                    )
                )
            self._editor._text_items.clear()
            self._editor._rebuild_display()
            self._editor._resize_canvas()
            self._editor._center_image_on_canvas()
            self._editor._modified = True
        except Exception:
            logger.exception("CropTool: failed to apply crop")
        self._editor._activate_tool("pan")

    def cancel_crop(self) -> None:
        self._editor._activate_tool("pan")

    _HANDLES = {
        "tl":  lambda r: (r.left(),  r.top()),
        "t":   lambda r: (r.center().x(), r.top()),
        "tr":  lambda r: (r.right(), r.top()),
        "l":   lambda r: (r.left(),  r.center().y()),
        "r":   lambda r: (r.right(), r.center().y()),
        "bl":  lambda r: (r.left(),  r.bottom()),
        "b":   lambda r: (r.center().x(), r.bottom()),
        "br":  lambda r: (r.right(), r.bottom()),
    }

    def _hit_test(self, canvas, screen_pos: QtCore.QPoint) -> Optional[str]:
        """Return handle id under *screen_pos*, or 'move', or None."""
        r = self._crop_rect
        if r is None:
            return None
        img = self._to_image_coords(canvas, screen_pos)
        ix, iy = img[0], img[1]
        for name, fn in self._HANDLES.items():
            hx, hy = fn(r)
            if abs(ix - hx) <= self.HANDLE_R and abs(iy - hy) <= self.HANDLE_R:
                return name
        if r.contains(ix, iy):
            return "move"
        return None

    def _update_rect(self, img_x: int, img_y: int) -> None:
        sx, sy = self._drag_start_img
        dx, dy = img_x - sx, img_y - sy
        r = self._drag_start_rect
        img_w, img_h = self._editor._pil_image.size
        d = self._dragging

        nl, nt = r.left(), r.top()
        nr, nb = r.right(), r.bottom()

        if d == "move":
            nw, nh = r.width(), r.height()
            nl = max(0, min(r.left() + dx, img_w - nw))
            nt = max(0, min(r.top() + dy, img_h - nh))
            nr, nb = nl + nw, nt + nh
        else:
            if d in ("tl", "l", "bl"):
                nl = max(0, min(r.left() + dx, r.right() - self.MIN_CROP))
            if d in ("tr", "r", "br"):
                nr = max(r.left() + self.MIN_CROP, min(r.right() + dx, img_w))
            if d in ("tl", "t", "tr"):
                nt = max(0, min(r.top() + dy, r.bottom() - self.MIN_CROP))
            if d in ("bl", "b", "br"):
                nb = max(r.top() + self.MIN_CROP, min(r.bottom() + dy, img_h))

        self._crop_rect = QtCore.QRect(nl, nt, nr - nl, nb - nt)

    def _redraw_overlay(self) -> None:
        overlay = self._editor._overlay_pixmap
        overlay.fill(QtCore.Qt.GlobalColor.transparent)
        r = self._crop_rect
        if r is None:
            return

        painter = QtGui.QPainter(overlay)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        img_w, img_h = self._editor._pil_image.size

        dim = QtGui.QColor(0, 0, 0, 130)
        painter.setBrush(QtGui.QBrush(dim))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        if r.top() > 0:
            painter.drawRect(0, 0, img_w, r.top())
        if r.bottom() < img_h:
            painter.drawRect(0, r.bottom(), img_w, img_h - r.bottom())
        if r.left() > 0:
            painter.drawRect(0, r.top(), r.left(), r.height())
        if r.right() < img_w:
            painter.drawRect(r.right(), r.top(), img_w - r.right(), r.height())

        pen = QtGui.QPen(QtGui.QColor("#5FC98A"), 2.5,
                         QtCore.Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRect(r)

        tw, th = r.width() / 3.0, r.height() / 3.0
        gpen = QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 0.5,
                          QtCore.Qt.PenStyle.DashLine)
        painter.setPen(gpen)
        for i in range(1, 3):
            painter.drawLine(
                QtCore.QPointF(r.left() + tw * i, r.top()),
                QtCore.QPointF(r.left() + tw * i, r.bottom()),
            )
            painter.drawLine(
                QtCore.QPointF(r.left(), r.top() + th * i),
                QtCore.QPointF(r.right(), r.top() + th * i),
            )

        corner_sz = 11
        edge_sz = 9
        painter.setPen(QtGui.QPen(QtGui.QColor("#5FC98A"), 2.0))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
        for name, fn in self._HANDLES.items():
            hx, hy = fn(r)
            sz = corner_sz if len(name) == 2 else edge_sz
            painter.drawRect(QtCore.QRectF(
                hx - sz / 2.0, hy - sz / 2.0, sz, sz,
            ))

        dim_text = f"{r.width()} × {r.height()}"
        font = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.GeneralFont
        )
        font.setPixelSize(11)
        painter.setFont(font)
        fm = QtGui.QFontMetricsF(font)
        text_rect = fm.boundingRect(dim_text)
        pad = 4.0
        bw = text_rect.width() + pad * 2
        bh = text_rect.height() + pad * 2
        label_x = r.right() - bw - 4
        label_y = r.top() - bh - 4
        if label_y < 2:
            label_y = r.top() + 4
        bg = QtCore.QRectF(label_x, label_y, bw, bh)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 160)))
        painter.drawRoundedRect(bg, 3, 3)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
        painter.drawText(bg.adjusted(pad, pad, -pad, -pad),
                         QtCore.Qt.AlignmentFlag.AlignCenter, dim_text)

        painter.end()
        self._position_action_buttons()


class SequenceTool(BaseTool):
    """Draw numbered step bubbles (①, ②, ③...)."""

    def __init__(self, editor):
        super().__init__(editor)
        self._color = QtGui.QColor("#4488FF")
        self._size = 28 # Circle diameter
        self._current_step = 1

    @property
    def color(self) -> QtGui.QColor: return self._color
    @color.setter
    def color(self, v: QtGui.QColor): self._color = v
    @property
    def size(self) -> int: return self._size
    @size.setter
    def size(self, v: int): self._size = v

    def tool_id(self) -> str:
        return "sequence"

    def on_activate(self) -> None:
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._editor._save_undo(UndoChangeType.ANNOTATIONS)
            img_pt = self._to_image_coords(canvas, event.position().toPoint())
            self._draw_step(self._editor._annotations_pixmap, img_pt)
            self._current_step += 1
            self._editor._modified = True
            canvas.update()
            return True
        return False

    def _draw_step(self, target: QtGui.QPixmap, pt: tuple[int, int]) -> None:
        """White-filled bubble with a colored ring and dark number text."""
        painter = QtGui.QPainter(target)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        cx, cy = pt
        r = self._size / 2.0
        ring_w = max(2.0, self._size * 0.12)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
        painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

        ring_pen = QtGui.QPen(self._color, ring_w)
        ring_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(ring_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QtCore.QPointF(cx, cy), r - ring_w / 2.0, r - ring_w / 2.0)

        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.GeneralFont)
        font.setBold(True)
        font.setPixelSize(int(self._size * 0.6))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor("#222222")))
        painter.drawText(
            QtCore.QRectF(cx - r, cy - r, self._size, self._size),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            str(self._current_step),
        )
        painter.end()


# ── Whole-image transforms (direct-manipulation tools) ─────────────────────
#
# Rotation and resize replace the entire image. They are canvas tools you drag
# directly — mirroring the pinned-image resize feel — rather than numeric
# dialogs.
#
# Rotation is rendered *paint-time*: the editor stores only an angle and the
# canvas applies a QPainter rotate around the (fixed) image center in
# paintEvent. This avoids allocating a rotated QPixmap and resizing the canvas
# widget on every mouse-move — both of which caused visible flicker and a
# drifting pivot (the old code measured angles against the rotating preview's
# moving bounding box). Resize still swaps a preview pixmap, but commits only
# run the PIL resample on release. Both go through _save_undo(FULL) and clear
# annotations on commit, since their geometry no longer matches the image.

_HANDLE_R = 9      # screen-pixel hit radius for resize handles
_MIN_DIM = 8       # minimum resulting image dimension in pixels
_MAX_DIM = 20000
_ROT_SNAP = 15.0   # Shift-snap increment, degrees
_ROT_ZERO_SNAP = 4.0  # snap back to 0 within this many degrees (PS-style)

# Shared styling for the floating Apply/Cancel buttons used by the rotate and
# resize tools (mirrors CropTool's action buttons).
_BTN_STYLE_CANCEL = """
    QPushButton {
        background: rgba(40,40,40,200);
        border: 1px solid rgba(255,255,255,20);
        border-radius: 11px;
        padding: 4px 14px;
        color: #ccc;
        font-size: 12px;
    }
    QPushButton:hover {
        background: rgba(60,60,60,220);
        border-color: rgba(255,255,255,35);
        color: #fff;
    }
    QPushButton:pressed {
        background: rgba(28,28,28,235);
        border-color: rgba(255,255,255,20);
    }
"""
_BTN_STYLE_APPLY = """
    QPushButton {
        background: #5FC98A;
        border: none;
        border-radius: 11px;
        padding: 4px 16px;
        color: #fff;
        font-size: 12px;
        font-weight: 600;
    }
    QPushButton:hover { background: #6fd99d; }
    QPushButton:pressed { background: #4ab87a; }
"""


def _create_action_buttons(tool, apply_slot, cancel_slot) -> None:
    """Create the floating Apply/Cancel buttons parented on the scroll viewport.

    Shared by RotateTool and ResizeTool. The buttons stay pinned to the
    bottom-centre of the visible area (see _position_action_buttons).
    """
    parent = tool._editor._scroll_area.viewport()
    t = tool._editor._tr

    tool._action_cancel = QtWidgets.QPushButton(t("editor_crop_cancel"), parent)
    tool._action_cancel.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    tool._action_cancel.setStyleSheet(_BTN_STYLE_CANCEL)
    tool._action_cancel.clicked.connect(cancel_slot)

    tool._action_apply = QtWidgets.QPushButton(t("editor_apply"), parent)
    tool._action_apply.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    tool._action_apply.setStyleSheet(_BTN_STYLE_APPLY)
    tool._action_apply.clicked.connect(apply_slot)

    _position_action_buttons(tool)


def _destroy_action_buttons(tool) -> None:
    for b in (getattr(tool, "_action_cancel", None),
              getattr(tool, "_action_apply", None)):
        if b:
            b.deleteLater()
    tool._action_cancel = None
    tool._action_apply = None


def _position_action_buttons(tool) -> None:
    """Pin the buttons to the bottom-centre of the visible viewport."""
    if not getattr(tool, "_action_apply", None) or not getattr(tool, "_action_cancel", None):
        return
    vp = tool._editor._scroll_area.viewport()
    vw, vh = vp.width(), vp.height()
    # Ensure both buttons have been laid out so sizeHint() is accurate, then
    # force them to the same visual height so they sit on the same baseline.
    tool._action_cancel.adjustSize()
    tool._action_apply.adjustSize()
    shared_h = max(tool._action_cancel.sizeHint().height(),
                   tool._action_apply.sizeHint().height())
    tool._action_cancel.setFixedHeight(shared_h)
    tool._action_apply.setFixedHeight(shared_h)
    # Recompute widths after the height may have triggered a re-layout.
    cancel_w = tool._action_cancel.sizeHint().width()
    apply_w = tool._action_apply.sizeHint().width()
    gap = 8
    total_w = cancel_w + apply_w + gap
    left = int((vw - total_w) / 2)
    bottom_y = vh - shared_h - 16
    tool._action_cancel.move(left, bottom_y)
    tool._action_apply.move(left + cancel_w + gap, bottom_y)
    tool._action_cancel.show()
    tool._action_apply.show()
    tool._action_cancel.raise_()
    tool._action_apply.raise_()


def _image_screen_rect(editor, canvas) -> QtCore.QRectF:
    """Screen-space rect of the *original* (un-rotated) image.

    Based on the pixmap the canvas is actually rendering (the rotate session's
    base pixmap while rotating, otherwise the display pixmap) + its centered
    offset. Using the rendered pixmap — not _display_pixmap, which may hold a
    stale rotated copy mid-session — keeps the rect and the rotation pivot
    stable across mouse-moves. This is what eliminates the drift and the
    jumping compass/border.
    """
    pm = editor._rendered_display_pixmap()
    if not pm:
        return QtCore.QRectF()
    scale = editor._effective_scale()
    offset = canvas._image_offset()
    return QtCore.QRectF(offset.x(), offset.y(),
                         pm.width() * scale, pm.height() * scale)


def _rendered_screen_rect(editor, canvas) -> QtCore.QRectF:
    """Screen-space rect of whatever pixmap the canvas is currently rendering.

    Preview-aware (follows a swapped resize pixmap); used by the resize tool so
    its handles track the scaled preview. Falls back to the original when no
    preview is set.
    """
    pm = editor._rendered_display_pixmap()
    if not pm:
        return QtCore.QRectF()
    scale = editor._effective_scale()
    offset = canvas._image_offset()
    return QtCore.QRectF(offset.x(), offset.y(),
                         pm.width() * scale, pm.height() * scale)


def _widget_to_image_f(editor, canvas, pos: QtCore.QPointF) -> QtCore.QPointF:
    """Widget coords -> fractional image-pixel coords (float, for smooth handles)."""
    offset = canvas._image_offset()
    scale = editor._effective_scale()
    return QtCore.QPointF((pos.x() - offset.x()) / scale,
                          (pos.y() - offset.y()) / scale)


class RotateTool(BaseTool):
    """Photoshop-style rotate: click-drag anywhere to swing the image around
    its center. A compass dial at the center shows the current angle.

    - Shift snaps to 15° increments.
    - Within ~4° of 0° it snaps flat (straighten feel).
    - Esc cancels and reverts; releasing commits with expand=True (all content
      kept; rotated corners stay transparent).
    """

    def __init__(self, editor):
        super().__init__(editor)
        self._angle = 0.0          # current preview angle, clockwise-positive
        self._dragging = False
        self._start_cursor_angle = 0.0
        self._base_angle = 0.0
        # Floating Done / Cancel buttons (children of the canvas), mirroring
        # CropTool. Give the rotation an explicit confirm affordance so users
        # don't have to discover that switching tools commits.
        self._action_cancel: Optional[QtWidgets.QPushButton] = None
        self._action_apply: Optional[QtWidgets.QPushButton] = None

    def tool_id(self) -> str:
        return "rotate"

    def on_activate(self) -> None:
        self._angle = 0.0
        self._editor._begin_rotate_session()
        _create_action_buttons(self, self.apply_rotation, self.cancel_rotation)

    def on_deactivate(self) -> None:
        _destroy_action_buttons(self)
        self._editor._end_rotate_session()

    # ── Floating Apply / Cancel buttons (shared helpers above) ─────────────

    def apply_rotation(self) -> None:
        """Confirm: end the session (commits one undo entry if rotated)."""
        self._editor._activate_tool("pan")

    def cancel_rotation(self) -> None:
        """Abandon: restore the pre-rotation base state, no undo entry."""
        self._editor._canvas.setFocus()
        ke = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Escape,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        self.on_key_press(self._editor._canvas, ke)

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        pivot = _image_screen_rect(self._editor, canvas).center()
        if pivot.isNull():
            return False
        # PS feel: start a rotation from anywhere on the canvas.
        self._dragging = True
        pos = event.position()
        self._start_cursor_angle = math.degrees(
            math.atan2(pos.y() - pivot.y(), pos.x() - pivot.x())
        )
        self._base_angle = self._editor._rotate_cumulative_angle
        self._angle = self._editor._rotate_cumulative_angle
        canvas.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        return True

    def on_mouse_move(self, canvas, event) -> bool:
        if not self._dragging:
            canvas.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            return False

        # Pivot is the original image center — recomputed but stable (no
        # preview dependence), so the angle delta never drifts.
        pivot = _image_screen_rect(self._editor, canvas).center()
        pos = event.position()
        cur = math.degrees(math.atan2(pos.y() - pivot.y(), pos.x() - pivot.x()))
        angle = self._base_angle + (cur - self._start_cursor_angle)
        # Normalize into (-180, 180].
        angle = ((angle + 180.0) % 360.0) - 180.0
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            angle = round(angle / _ROT_SNAP) * _ROT_SNAP
        if abs(angle) < _ROT_ZERO_SNAP:
            angle = 0.0
        self._angle = angle
        self._editor._set_rotation_preview(angle)
        return True

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            canvas.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            if self._angle != self._base_angle:
                self._editor._apply_rotation(self._angle, True)
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            # Abandon the whole session: restore the pre-rotation base state
            # (image + annotations + editable text), no undo entry.
            ed = self._editor
            if ed._rotate_base_image:
                ed._pil_image = ed._rotate_base_image.copy()
                # Restore pre-flatten annotations so editable text comes back.
                if ed._rotate_pre_annot is not None:
                    ed._annotations_pixmap = ed._rotate_pre_annot.copy()
                if ed._rotate_base_overlay is not None:
                    ed._overlay_pixmap = ed._rotate_base_overlay.copy()
                ed._text_items = [
                    TextItem(t.text, QtCore.QPointF(t.img_pos), QtGui.QColor(t.color),
                             t.font_family, t.font_size)
                    for t in ed._rotate_pre_text
                ] if ed._rotate_pre_text else []
                ed._rebuild_display()
            ed._rotate_cumulative_angle = 0.0
            ed._set_rotation_preview(0.0)
            ed._end_rotate_session()
            ed._activate_tool("pan")
            return True
        return False

    def on_paint(self, canvas, painter: QtGui.QPainter) -> None:
        rect = _image_screen_rect(self._editor, canvas)
        if rect.isNull():
            return
        center = rect.center()
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Compass dial at the pivot. (No image border: it added visual noise and
        # jumped when the rendered pixmap changed size mid-session.)
        r = 26.0
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 160), 4.0))
        painter.drawEllipse(center, r, r)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1.6))
        painter.drawEllipse(center, r, r)

        # Vertical "up" reference tick (original top of image).
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 160), 1.4))
        painter.drawLine(QtCore.QPointF(center.x(), center.y() - r),
                         QtCore.QPointF(center.x(), center.y() - r + 8))

        # Rotating indicator pointing to current angle. Angle is clockwise-
        # positive; screen y is down, so +angle rotates the indicator clockwise.
        angle = self._angle if self._dragging else self._editor._rotate_cumulative_angle
        rad = math.radians(angle - 90.0)  # -90 so 0° points up
        ix = center.x() + math.cos(rad) * (r - 4)
        iy = center.y() + math.sin(rad) * (r - 4)
        painter.setPen(QtGui.QPen(QtGui.QColor("#5FC98A"), 2.4))
        painter.drawLine(center, QtCore.QPointF(ix, iy))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#5FC98A")))
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.2))
        painter.drawEllipse(QtCore.QPointF(ix, iy), 3.2, 3.2)

        # Center dot + angle readout.
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 220)))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(center, 2.0, 2.0)

        label = "{:+.1f}°".format(angle)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        fm = QtGui.QFontMetricsF(font)
        tw, th = fm.horizontalAdvance(label), fm.height()
        bx = center.x() - tw / 2 - 5
        by = center.y() + r + 8
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 180)))
        painter.drawRoundedRect(QtCore.QRectF(bx, by, tw + 10, th + 4), 4, 4)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
        painter.drawText(QtCore.QPointF(bx + 5, by + fm.ascent()), label)

        painter.restore()

        # Keep the floating Apply/Cancel buttons pinned to the viewport.
        _position_action_buttons(self)


class ResizeTool(BaseTool):
    """Drag a corner handle to resize the image, mirroring the pinned-image feel.

    Aspect ratio is locked by default; hold Shift to resize freely. The opposite
    corner stays anchored so the grab point tracks the cursor.
    """

    _CORNERS = {
        "tl": (-1, -1),
        "tr": (1, -1),
        "bl": (-1, 1),
        "br": (1, 1),
    }

    def __init__(self, editor):
        super().__init__(editor)
        self._dragging: Optional[str] = None  # corner name, or "pan"
        self._target = QtCore.QSizeF(0, 0)   # current preview size (image px)
        self._anchor_img = QtCore.QPointF()  # anchored opposite corner (image px)
        self._pan_start = QtCore.QPoint()    # global pos at pan start
        # Floating Apply/Cancel buttons (shared with RotateTool).
        self._action_cancel: Optional[QtWidgets.QPushButton] = None
        self._action_apply: Optional[QtWidgets.QPushButton] = None

    def tool_id(self) -> str:
        return "resize"

    def on_activate(self) -> None:
        self._dragging = None
        self._editor._begin_resize_session()
        _create_action_buttons(self, self.apply_resize, self.cancel_resize)
        self._editor._canvas.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def on_deactivate(self) -> None:
        _destroy_action_buttons(self)
        self._editor._end_resize_session()

    def apply_resize(self) -> None:
        """Confirm: end the session (commits one undo entry if resized)."""
        self._editor._activate_tool("pan")

    def cancel_resize(self) -> None:
        """Abandon: revert to the pre-resize base state, no undo entry."""
        self._editor._canvas.setFocus()
        ke = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Escape,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        self.on_key_press(self._editor._canvas, ke)

    def _corner_screen(self, rect: QtCore.QRectF, name: str) -> QtCore.QPointF:
        sx, sy = self._CORNERS[name]
        cx = rect.right() if sx > 0 else rect.left()
        cy = rect.bottom() if sy > 0 else rect.top()
        return QtCore.QPointF(cx, cy)

    def _hit_corner(self, rect: QtCore.QRectF, pos: QtCore.QPointF) -> Optional[str]:
        for name in self._CORNERS:
            if (pos - self._corner_screen(rect, name)).manhattanLength() <= _HANDLE_R + 4:
                return name
        return None

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        rect = _rendered_screen_rect(self._editor, canvas)
        if rect.isNull():
            return False
        name = self._hit_corner(rect, event.position())
        if name:
            self._dragging = name
            # Anchor = opposite corner, in image-pixel coords.
            osx, osy = self._CORNERS[name]
            ax_sign, ay_sign = -osx, -osy
            pm = self._editor._rendered_display_pixmap()
            self._anchor_img = QtCore.QPointF(
                pm.width() if ax_sign > 0 else 0.0,
                pm.height() if ay_sign > 0 else 0.0,
            )
            self._target = QtCore.QSizeF(pm.width(), pm.height())
            return True
        # Not on a corner: pan the canvas (like the crop tool), so the user can
        # reposition the image without switching tools.
        self._dragging = "pan"
        self._pan_start = event.globalPosition().toPoint()
        canvas.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        return True

    def on_mouse_move(self, canvas, event) -> bool:
        rect = _rendered_screen_rect(self._editor, canvas)

        if self._dragging == "pan":
            cur = event.globalPosition().toPoint()
            dx, dy = cur.x() - self._pan_start.x(), cur.y() - self._pan_start.y()
            self._pan_start = cur
            sa = self._editor._scroll_area
            sa.horizontalScrollBar().setValue(int(sa.horizontalScrollBar().value() - dx))
            sa.verticalScrollBar().setValue(int(sa.verticalScrollBar().value() - dy))
            return True

        if not self._dragging:
            if not rect.isNull():
                name = self._hit_corner(rect, event.position())
                if name in ("tl", "br"):
                    canvas.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
                elif name in ("tr", "bl"):
                    canvas.setCursor(QtCore.Qt.CursorShape.SizeBDiagCursor)
                else:
                    # Open hand hints the body is draggable to pan.
                    canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            return False
        if rect.isNull():
            return True

        sx, sy = self._CORNERS[self._dragging]
        free = bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
        mouse_img = _widget_to_image_f(self._editor, canvas, event.position())
        ax, ay = self._anchor_img.x(), self._anchor_img.y()
        vx, vy = mouse_img.x() - ax, mouse_img.y() - ay

        if free:
            new_w = abs(vx)
            new_h = abs(vy)
        else:
            # Project the cursor onto the anchor->corner diagonal so the grabbed
            # corner tracks the mouse while preserving aspect ratio (same math
            # the pinned-image window uses).
            orig = self._editor._display_pixmap
            aspect = orig.width() / orig.height() if orig.height() else 1.0
            dx, dy = sx * aspect, sy * 1.0
            dot = vx * dx + vy * dy
            mag_sq = dx * dx + dy * dy
            scale = dot / mag_sq if mag_sq else 0.0
            new_w = abs(scale * dx)
            new_h = abs(scale * dy)

        new_w = max(_MIN_DIM, min(new_w, _MAX_DIM))
        new_h = max(_MIN_DIM, min(new_h, _MAX_DIM))
        self._target = QtCore.QSizeF(new_w, new_h)
        self._editor._set_resize_preview(new_w, new_h)
        return True

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        if self._dragging == "pan":
            self._dragging = None
            canvas.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            return True
        if self._dragging:
            name = self._dragging
            self._dragging = None
            w, h = int(round(self._target.width())), int(round(self._target.height()))
            orig = self._editor._display_pixmap
            if (w, h) != (orig.width(), orig.height()) and w > 0 and h > 0:
                self._editor._apply_resize(w, h)
            else:
                self._editor._set_preview_pixmap(None)
            return True
        return False

    def on_key_press(self, canvas, event) -> bool:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            # Abandon the resize session: revert to the pre-resize base state.
            self._editor._cancel_resize_session()
            self._editor._activate_tool("pan")
            return True
        return False

    def on_paint(self, canvas, painter: QtGui.QPainter) -> None:
        rect = _rendered_screen_rect(self._editor, canvas)
        if rect.isNull():
            return
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        painter.setPen(QtGui.QPen(QtGui.QColor("#5FC98A"), 1.4,
                                  QtCore.Qt.PenStyle.DashLine))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        for name in self._CORNERS:
            c = self._corner_screen(rect, name)
            painter.setPen(QtGui.QPen(QtGui.QColor("#5FC98A"), 1.6))
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
            painter.drawRect(QtCore.QRectF(c.x() - _HANDLE_R / 2,
                                           c.y() - _HANDLE_R / 2,
                                           _HANDLE_R, _HANDLE_R))

        # Dimension readout
        if self._dragging:
            w = int(round(self._target.width()))
            h = int(round(self._target.height()))
        else:
            pm = self._editor._display_pixmap
            w, h = pm.width(), pm.height()
        label = "{} × {}".format(w, h)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        fm = QtGui.QFontMetricsF(font)
        tw, th = fm.horizontalAdvance(label), fm.height()
        bx = rect.right() - tw - 12
        by = rect.top() + 6
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 170)))
        painter.drawRoundedRect(QtCore.QRectF(bx, by, tw + 10, th + 4), 4, 4)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
        painter.drawText(QtCore.QPointF(bx + 5, by + fm.ascent()), label)

        painter.restore()

        # Keep the floating Apply/Cancel buttons pinned to the viewport.
        _position_action_buttons(self)
