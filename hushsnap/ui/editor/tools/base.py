from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional
from PyQt6 import QtCore, QtGui

if TYPE_CHECKING:
    from ..image_editor import ImageEditorWindow
    from ..widgets.canvas import EditorCanvas

class BaseTool(ABC):
    """Abstract base for all editing tools."""

    def __init__(self, editor: "ImageEditorWindow"):
        self._editor = editor

    @abstractmethod
    def tool_id(self) -> str:
        """Unique identifier for this tool."""
        ...

    def on_activate(self) -> None:
        """Called when this tool becomes active."""
        pass

    def on_deactivate(self) -> None:
        """Called when switching away from this tool."""
        pass

    def on_mouse_press(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        """Return True if event was handled."""
        return False

    def on_mouse_move(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        return False

    def on_mouse_release(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        return False

    def on_key_press(
        self, canvas: "EditorCanvas", event: QtGui.QKeyEvent
    ) -> bool:
        return False

    def on_mouse_double_click(
        self, canvas: "EditorCanvas", event: QtGui.QMouseEvent
    ) -> bool:
        """Return True if handled."""
        return False

    def on_paint(
        self, canvas: "EditorCanvas", painter: QtGui.QPainter
    ) -> None:
        """Optional: paint additional decorations."""
        pass

    def _to_image_coords(
        self, canvas: "EditorCanvas", widget_pos: QtCore.QPoint
    ) -> tuple[int, int]:
        """Convert widget coordinates to image pixel coordinates."""
        offset = canvas._image_offset()
        scale = self._editor._effective_scale()
        return (
            int((widget_pos.x() - offset.x()) / scale),
            int((widget_pos.y() - offset.y()) / scale),
        )

    # ── Shared stroke infrastructure (Pinta-style path accumulation) ─────

    def _stroke_begin(self, pt: tuple[int, int]) -> None:
        """Initialise per-stroke state."""
        img_size = self._editor._pil_image.size
        self._stroke_pixmap = QtGui.QPixmap(QtCore.QSize(*img_size))
        self._stroke_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._stroke_path = QtGui.QPainterPath()
        self._stroke_path.moveTo(QtCore.QPointF(*pt))

    def _stroke_add_point(self, pt: tuple[int, int]) -> None:
        """Add a lineTo to the accumulated path and redraw the stroke pixmap."""
        self._stroke_path.lineTo(QtCore.QPointF(*pt))
        self._stroke_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(self._stroke_pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        if self._stroke_path.elementCount() <= 1:
            # Single point — draw filled circle
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QBrush(getattr(self, '_stroke_color', QtGui.QColor("#000"))))
            r = max(1, getattr(self, '_stroke_size', 3) / 2.0)
            el = self._stroke_path.elementAt(0)
            painter.drawEllipse(QtCore.QPointF(el.x, el.y), r, r)
        else:
            pen = QtGui.QPen(
                getattr(self, '_stroke_color', QtGui.QColor("#000")),
                getattr(self, '_stroke_size', 3),
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawPath(self._stroke_path)
        painter.end()

    def _stroke_commit(self, composition_mode=QtGui.QPainter.CompositionMode.CompositionMode_SourceOver) -> None:
        """Composite per-stroke pixmap onto annotations layer."""
        if not getattr(self, '_stroke_pixmap', None):
            return
        painter = QtGui.QPainter(self._editor._annotations_pixmap)
        painter.setCompositionMode(composition_mode)
        painter.drawPixmap(0, 0, self._stroke_pixmap)
        painter.end()
        self._stroke_pixmap = None
        self._stroke_path = None

    def _stroke_cleanup(self) -> None:
        """Release stroke resources."""
        self._stroke_pixmap = None
        self._stroke_path = None
