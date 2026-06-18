import logging
from typing import Optional
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets
from .base import BaseTool
from ..models import UndoChangeType

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

        self._action_apply = QtWidgets.QPushButton(t("editor_crop_confirm"), canvas)
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
        self._editor._save_undo(UndoChangeType.FULL)
        try:
            cropped = self._editor._pil_image.crop(
                (r.left(), r.top(), r.x() + r.width(), r.y() + r.height())
            )
            self._editor._pil_image = cropped
            self._editor._clear_annotations()
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
