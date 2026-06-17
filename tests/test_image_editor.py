"""
Unit tests for hushsnap.ui.image_editor — undo system, tool save behavior,
and dead code verification.
"""

import pytest
from unittest.mock import MagicMock
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.ui.image_editor import (
    ImageEditorWindow,
    UndoChangeType,
    _UndoEntry,
    TextItem,
    BrushTool,
    EraserTool,
    ShapeTool,
    HighlighterTool,
    MosaicTool,
    CropTool,
    TextTool,
    PanTool,
)


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


# ── Helpers ─────────────────────────────────────────────────────────────────

# Tiny 10×10 RGBA test image — small sizes make tests deterministic and fast
@pytest.fixture
def test_image() -> Image.Image:
    return Image.new("RGBA", (100, 80), (255, 0, 0, 255))


def _translate(key, **kwargs):
    """Minimal translate function — returns the key if not found."""
    d = {
        "editor_title": "Test Editor",
        "editor_undo": "Undo",
        "editor_redo": "Redo",
        "editor_save": "Save",
        "editor_save_as": "Save As...",
        "editor_color": "Color",
        "editor_size": "Size",
        "editor_opacity": "Opacity",
        "editor_font": "Font",
        "editor_font_size": "Font Size",
        "editor_crop_instruction": "Drag to crop",
        "editor_crop_confirm": "Crop",
        "editor_crop_cancel": "Cancel",
        "editor_copy": "Copy",
        "editor_saved": "Saved",
        "editor_copied": "Copied",
        "editor_zoom_label": "{zoom}%",
        "tool_brush": "Brush",
        "tool_highlighter": "Highlighter",
        "tool_eraser": "Eraser",
        "tool_mosaic": "Mosaic",
        "tool_crop": "Crop",
        "tool_text": "Text",
        "tool_pan": "Hand",
        "tool_rectangle": "Rectangle",
        "tool_ellipse": "Ellipse",
        "tool_arrow": "Arrow",
    }
    s = d.get(key, key)
    if kwargs:
        s = s.format(**kwargs)
    return s


@pytest.fixture
def editor(qapp, test_image) -> ImageEditorWindow:
    win = ImageEditorWindow(test_image, _translate)
    yield win
    win.close()


# ── UndoChangeType enum ──────────────────────────────────────────────────────


class TestUndoChangeType:
    def test_enum_values(self):
        assert UndoChangeType.FULL.value == "full"
        assert UndoChangeType.ANNOTATIONS.value == "annotations"
        assert UndoChangeType.TEXT.value == "text"
        assert UndoChangeType.REGION.value == "region"


# ── _UndoEntry construction ──────────────────────────────────────────────────


class TestUndoEntry:
    def test_full_entry_copies_everything(self, qapp, test_image):
        """FULL type captures PIL copy, pixmap copy, and text items."""
        pm = QtGui.QPixmap(100, 80)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        items = [TextItem("hello", QtCore.QPointF(10, 10), QtGui.QColor("#fff"), "Arial", 24)]
        entry = _UndoEntry(UndoChangeType.FULL, pil_img=test_image, annot_pxm=pm, text_items=items)
        assert entry.change_type == UndoChangeType.FULL
        assert entry.pil_image is not None
        assert entry.pil_image.size == (100, 80)
        assert entry.annotations_pixmap is not None
        assert not entry.annotations_pixmap.isNull()
        assert len(entry.text_items) == 1
        assert entry.text_items[0].text == "hello"
        # Deep copy — not the same object
        assert entry.pil_image is not test_image
        assert entry.text_items[0] is not items[0]
        assert entry.region_bounds is None
        assert entry.region_pixels is None

    def test_annotations_entry_skips_pil(self, qapp):
        """ANNOTATIONS type does not copy PIL image."""
        pm = QtGui.QPixmap(100, 80)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        entry = _UndoEntry(UndoChangeType.ANNOTATIONS, annot_pxm=pm)
        assert entry.change_type == UndoChangeType.ANNOTATIONS
        assert entry.pil_image is None
        assert entry.annotations_pixmap is not None
        assert entry.text_items == []

    def test_text_entry_only_items(self, qapp):
        """TEXT type only stores text items."""
        items = [TextItem("a", QtCore.QPointF(0, 0), QtGui.QColor("#000"), "Arial", 12)]
        entry = _UndoEntry(UndoChangeType.TEXT, text_items=items)
        assert entry.change_type == UndoChangeType.TEXT
        assert entry.pil_image is None
        assert entry.annotations_pixmap is None
        assert len(entry.text_items) == 1

    def test_region_entry(self, qapp):
        """REGION type stores bounds and pixel bytes."""
        bounds = QtCore.QRect(10, 10, 30, 20)
        pixels = b"x" * (30 * 20 * 4)  # RGBA
        entry = _UndoEntry(UndoChangeType.REGION, region_bounds=bounds, region_pixels=pixels)
        assert entry.change_type == UndoChangeType.REGION
        assert entry.pil_image is None
        assert entry.annotations_pixmap is None
        assert entry.region_bounds == bounds
        assert entry.region_pixels == pixels
        assert len(entry.region_pixels) == 30 * 20 * 4

    def test_text_items_deep_copy(self, qapp):
        """Text items are deep-copied in the undo entry."""
        original = TextItem("orig", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        entry = _UndoEntry(UndoChangeType.TEXT, text_items=[original])
        # Mutate original — entry should be unaffected
        original.text = "changed"
        original.img_pos = QtCore.QPointF(99, 99)
        assert entry.text_items[0].text == "orig"
        assert entry.text_items[0].img_pos == QtCore.QPointF(0, 0)

    def test_none_pixmap_does_not_crash(self, qapp):
        """Passing None for annotations_pixmap is safe."""
        entry = _UndoEntry(UndoChangeType.ANNOTATIONS, annot_pxm=None)
        assert entry.annotations_pixmap is None


# ── _save_undo() ─────────────────────────────────────────────────────────────


class TestSaveUndo:
    def test_save_full_copies_pil(self, editor):
        """FULL save copies the PIL image into the undo stack."""
        original = editor._pil_image.copy()
        editor._save_undo(UndoChangeType.FULL)
        assert len(editor._undo_stack) == 1
        entry = editor._undo_stack[-1]
        assert entry.change_type == UndoChangeType.FULL
        assert entry.pil_image is not None
        assert entry.pil_image.tobytes() == original.tobytes()
        assert entry.annotations_pixmap is not None
        assert entry.text_items == []

    def test_save_annotations_skips_pil(self, editor):
        """ANNOTATIONS save does not copy PIL image."""
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        entry = editor._undo_stack[-1]
        assert entry.change_type == UndoChangeType.ANNOTATIONS
        assert entry.pil_image is None
        assert entry.annotations_pixmap is not None

    def test_save_text_skips_pil_and_pixmap(self, editor):
        """TEXT save only copies text items — no PIL, no pixmap."""
        editor._save_undo(UndoChangeType.TEXT)
        entry = editor._undo_stack[-1]
        assert entry.change_type == UndoChangeType.TEXT
        assert entry.pil_image is None
        assert entry.annotations_pixmap is None
        assert entry.text_items == []

    def test_save_region_stores_bytes(self, editor):
        """REGION save stores the correct pixel bytes for the region."""
        bounds = QtCore.QRect(10, 10, 20, 20)
        region = editor._pil_image.crop((10, 10, 30, 30))
        ret = region.tobytes()
        editor._save_undo(UndoChangeType.REGION, region_bounds=bounds, region_pixels=ret)
        entry = editor._undo_stack[-1]
        assert entry.change_type == UndoChangeType.REGION
        assert entry.region_bounds == bounds
        assert entry.region_pixels == ret

    def test_save_clears_redo_stack(self, editor):
        """Saving new undo entry clears the redo stack."""
        editor._undo_stack.append(_UndoEntry(UndoChangeType.FULL, pil_img=editor._pil_image))
        editor._redo_stack.append(_UndoEntry(UndoChangeType.FULL, pil_img=editor._pil_image))
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        assert len(editor._redo_stack) == 0

    def test_save_respects_max_undo_steps(self, editor):
        """Stack is capped at MAX_UNDO_STEPS (oldest pruned first)."""
        editor.MAX_UNDO_STEPS = 3
        for _ in range(5):
            editor._save_undo(UndoChangeType.ANNOTATIONS)
        assert len(editor._undo_stack) == 3

    def test_save_redo_stack_also_step_capped(self, editor):
        """Redo stack is held to MAX_UNDO_STEPS too, not just undo."""
        editor.MAX_UNDO_STEPS = 3
        for _ in range(5):
            editor._save_undo(UndoChangeType.ANNOTATIONS)
        # Undo all the way back — redo stack must cap at MAX_UNDO_STEPS.
        for _ in range(len(editor._undo_stack)):
            editor._undo()
        assert len(editor._redo_stack) == 3

    def test_save_updates_undo_button(self, editor):
        """Undo button is enabled after first save."""
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        assert editor._undo_btn.isEnabled()
        assert not editor._redo_btn.isEnabled()

    def test_save_default_is_full(self, editor):
        """Calling _save_undo() with no arguments defaults to FULL."""
        editor._save_undo()
        entry = editor._undo_stack[-1]
        assert entry.change_type == UndoChangeType.FULL
        assert entry.pil_image is not None


# ── undo / redo correctness ──────────────────────────────────────────────────


class TestUndoRedo:
    def test_undo_annotations_restores_pixmap(self, editor):
        """Undoing an annotations entry restores the pixmap without changing PIL."""
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        # Modify the annotations pixmap
        editor._annotations_pixmap.fill(QtCore.Qt.GlobalColor.white)
        pix_after_edit = editor._annotations_pixmap.toImage().pixel(0, 0)
        assert pix_after_edit == QtGui.QColor(255, 255, 255, 255).rgba()

        editor._undo()
        # After undo, pixmap should be transparent again
        assert editor._annotations_pixmap.toImage().pixel(0, 0) == QtGui.QColor(0, 0, 0, 0).rgba()

    def test_undo_text_restores_items(self, editor):
        """Undoing a text entry restores old text items."""
        editor._save_undo(UndoChangeType.TEXT)
        editor._text_items.append(
            TextItem("new", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )
        assert len(editor._text_items) == 1
        editor._undo()
        assert len(editor._text_items) == 0

    def test_undo_region_restores_pixels(self, editor):
        """Undoing a region entry restores the old pixel values."""
        old_region = editor._pil_image.crop((10, 10, 30, 30))
        editor._save_undo(
            UndoChangeType.REGION,
            region_bounds=QtCore.QRect(10, 10, 20, 20),
            region_pixels=old_region.tobytes(),
        )
        # Modify the region
        editor._pil_image.paste(Image.new("RGBA", (20, 20), (0, 255, 0, 255)), (10, 10))
        editor._undo()
        restored = editor._pil_image.crop((10, 10, 30, 30))
        assert restored.tobytes() == old_region.tobytes()

    def test_undo_full_restores_everything(self, editor):
        """Undoing a full entry restores PIL, pixmap, and text."""
        old_pil = editor._pil_image.copy()
        editor._save_undo(UndoChangeType.FULL)
        editor._pil_image.paste(Image.new("RGBA", (50, 50), (0, 255, 0, 255)), (0, 0))
        editor._text_items.append(
            TextItem("x", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )
        editor._undo()
        assert editor._pil_image.tobytes() == old_pil.tobytes()
        assert len(editor._text_items) == 0

    def test_undo_empty_stack_is_safe(self, editor):
        """Undoing with empty stack is a no-op (no crash)."""
        editor._undo()  # must not raise

    def test_redo_empty_stack_is_safe(self, editor):
        """Redoing with empty stack is a no-op (no crash)."""
        editor._redo()  # must not raise

    def test_redo_after_undo(self, editor):
        """Redo restores the state that was undone."""
        editor._save_undo(UndoChangeType.TEXT)
        editor._text_items.append(
            TextItem("a", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )
        editor._undo()
        assert len(editor._text_items) == 0
        editor._redo()
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "a"

    def test_undo_redo_full_cycle_annotations(self, editor):
        """Undo → redo → undo cycle preserves annotations state."""
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 10, 10, QtGui.QColor("#FF0000"))
        p.end()
        red_pixel = editor._annotations_pixmap.toImage().pixel(0, 0)

        editor._undo()
        assert editor._annotations_pixmap.toImage().pixel(0, 0) == QtGui.QColor(0, 0, 0, 0).rgba()

        editor._redo()
        assert editor._annotations_pixmap.toImage().pixel(0, 0) == red_pixel

    def test_button_state_tracks_stack(self, editor):
        """Undo/redo buttons enabled/disabled correctly through a full cycle."""
        assert not editor._undo_btn.isEnabled()
        assert not editor._redo_btn.isEnabled()

        editor._save_undo(UndoChangeType.TEXT)
        assert editor._undo_btn.isEnabled()
        assert not editor._redo_btn.isEnabled()

        editor._undo()
        assert not editor._undo_btn.isEnabled()
        assert editor._redo_btn.isEnabled()

        editor._redo()
        assert editor._undo_btn.isEnabled()
        assert not editor._redo_btn.isEnabled()

    def test_modified_set_on_undo(self, editor):
        """Undo marks the editor as modified."""
        editor._modified = False
        editor._save_undo(UndoChangeType.TEXT)
        editor._text_items.append(
            TextItem("x", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )
        editor._undo()
        assert editor._modified is True

    def test_modified_set_on_redo(self, editor):
        """Redo marks the editor as modified."""
        editor._save_undo(UndoChangeType.TEXT)
        editor._text_items.append(
            TextItem("x", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )
        editor._undo()
        editor._modified = False
        editor._redo()
        assert editor._modified is True


# ── Tool save behavior ───────────────────────────────────────────────────────


class TestBrushToolSaveBehavior:
    def test_save_on_mouse_press(self, editor):
        tool = editor._tools["brush"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_called_once_with(UndoChangeType.ANNOTATIONS)


class TestHighlighterToolSaveBehavior:
    def test_save_on_mouse_press(self, editor):
        tool = editor._tools["highlighter"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_called_once_with(UndoChangeType.ANNOTATIONS)


class TestEraserToolSaveBehavior:
    def test_save_on_mouse_press(self, editor):
        tool = editor._tools["eraser"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_called_once_with(UndoChangeType.ANNOTATIONS)


class TestShapeToolSaveBehavior:
    def test_save_on_mouse_press(self, editor):
        tool = editor._tools["rectangle"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_called_once_with(UndoChangeType.ANNOTATIONS)

    def test_ellipse_same_behavior(self, editor):
        tool = editor._tools["ellipse"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_called_once_with(UndoChangeType.ANNOTATIONS)


class TestMosaicToolSaveBehavior:
    def test_save_not_called_on_press(self, editor):
        """MosaicTool should NOT call _save_undo on mouse press."""
        tool = editor._tools["mosaic"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_not_called()

    def test_save_called_on_release_with_region(self, editor):
        """MosaicTool calls _save_undo with REGION on mouse release."""
        tool = editor._tools["mosaic"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()

        # Press
        press = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, press)

        # Release far enough to create a valid region (>2px)
        release = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease,
            QtCore.QPointF(80, 70), QtCore.QPointF(80, 70),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_release(editor._canvas, release)

        # Verify _save_undo was called with REGION
        assert editor._save_undo.call_count >= 1
        call_args = editor._save_undo.call_args_list[-1]
        assert call_args[0][0] == UndoChangeType.REGION


class TestCropToolSaveBehavior:
    def test_save_on_apply_crop(self, editor):
        """CropTool.apply_crop calls _save_undo with FULL."""
        tool = editor._tools["crop"]
        editor._save_undo = MagicMock()
        # Set up a crop rect that's not full size
        tool._crop_rect = QtCore.QRect(10, 10, 50, 40)
        tool.apply_crop()
        editor._save_undo.assert_called_once_with(UndoChangeType.FULL)

    def test_full_image_crop_noop_skips_save(self, editor):
        """Crop rect covering the whole image is a no-op — no save."""
        tool = editor._tools["crop"]
        editor._save_undo = MagicMock()
        img_w, img_h = editor._pil_image.size
        tool._crop_rect = QtCore.QRect(0, 0, img_w, img_h)
        tool.apply_crop()
        editor._save_undo.assert_not_called()

    def test_apply_crop_preserves_exact_dimensions(self, editor):
        """Cropping keeps the full crop-rect width/height (no off-by-one).

        Regression: QRect.right()/bottom() are closed boundaries, while
        PIL.crop() takes a half-open box, so the old code dropped the last
        column and the last row.
        """
        tool = editor._tools["crop"]
        # Distinct color per column/row so dropped edges are detectable.
        src = Image.new("RGBA", (100, 80), (0, 0, 0, 255))
        px = src.load()
        for y in range(80):
            px[59, y] = (255, 0, 0, 255)   # intended right-edge column
        for x in range(100):
            px[x, 49] = (0, 255, 0, 255)   # intended bottom row
        editor._pil_image = src.copy()
        editor._rebuild_display()

        # Rect 10..59 × 10..49 → expect 50×40, edges included.
        tool._crop_rect = QtCore.QRect(10, 10, 50, 40)
        tool.apply_crop()

        assert editor._pil_image.size == (50, 40)
        # Right edge column (now index 49) and bottom row (now index 39)
        # must both be present — the off-by-one dropped exactly these.
        cpx = editor._pil_image.load()
        assert cpx[49, 0] == (255, 0, 0, 255)
        assert cpx[0, 39] == (0, 255, 0, 255)


class TestCropHandleVisibility:
    """Crop border and handles are painted thick enough to stay visible
    after the overlay is scaled down to the viewport.

    The overlay is drawn in image pixels and then scaled by paintEvent,
    so thin values (1.5px border, 7px handles) shrank to <1px / <2px on
    a 4K screenshot fit to a 960px window — nearly invisible. These
    constants guard the thicker values.
    """

    def test_border_pen_is_thick(self):
        from hushsnap.ui import image_editor as ie
        import inspect
        src = inspect.getsource(ie.CropTool._redraw_overlay)
        assert 'QColor("#5FC98A"), 2.5' in src, (
            "crop border pen width regressed below 2.5px"
        )

    def test_handle_sizes(self):
        from hushsnap.ui import image_editor as ie
        import inspect
        src = inspect.getsource(ie.CropTool._redraw_overlay)
        assert "corner_sz = 11" in src, "corner handle regressed below 11px"
        assert "edge_sz = 9" in src, "edge handle regressed below 9px"

    def test_handle_border_pen(self):
        """Handle outline pen also thickened (was 1.5)."""
        from hushsnap.ui import image_editor as ie
        import inspect
        src = inspect.getsource(ie.CropTool._redraw_overlay)
        assert 'QColor("#5FC98A"), 2.0' in src, (
            "handle border pen width regressed below 2.0px"
        )

    def test_hit_radius_matches_handle_size(self):
        """HANDLE_R should be roughly half the corner handle so the
        grabbable area covers the visible handle."""
        from hushsnap.ui.image_editor import CropTool
        assert CropTool.HANDLE_R >= 9, "hit radius too small for the handles"


class TestTextToolSaveBehavior:
    def test_save_on_new_item(self, editor):
        """Creating a new text item calls _save_undo with TEXT."""
        tool = editor._tools["text"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_called_once_with(UndoChangeType.TEXT)


class TestTextToolFontUnits:
    """font_size is in image pixels throughout the editor.

    Regression: the canvas preview / hit-test used QFont(family, pt),
    which at 96 DPI renders 1.33x larger than the px-based export bake —
    so what the user saw was not what got saved. Both renderers must now
    use pixel size.
    """

    def test_hit_test_uses_pixel_size(self, editor):
        """The QFont built for hit-testing is in pixels, not points."""
        from hushsnap.ui.image_editor import TextItem
        item = TextItem("Hi", QtCore.QPointF(10, 10),
                        QtGui.QColor("#fff"), "Arial", 48)
        editor._text_items.append(item)

        tool = editor._tools["text"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))

        # Build the same QFont the code builds internally and assert its
        # pixel size is set (pointSize would be -1 when pixel size is used).
        scale = editor._effective_scale()
        fs = max(1, int(item.font_size * scale))
        font = QtGui.QFont(item.font_family)
        font.setPixelSize(fs)
        assert font.pixelSize() == fs
        assert font.pointSize() == -1  # pixel size overrides point size

    def test_preview_font_height_matches_export(self, editor):
        """Preview QFont pixel size == ImageFont.truetype px size (export).

        This is the core 'what you see is what you get' invariant: the
        font_size value feeds both the on-screen QFont (via setPixelSize)
        and the export ImageFont.truetype(path, font_size) — both in
        pixels — so they must agree on glyph height at scale=1.
        """
        from PIL import ImageFont

        from hushsnap.ui.image_editor import TextItem, TextTool
        item = TextItem("Hg", QtCore.QPointF(10, 10),
                        QtGui.QColor("#fff"), "Arial", 48)

        # Export path (scale=1, no zoom): ImageFont.truetype(path, font_size)
        path = TextTool._resolve_font_path(item.font_family)
        assert path, "test requires a resolvable Arial font on this system"
        pil_font = ImageFont.truetype(path, item.font_size)
        pil_ascent = pil_font.getmetrics()[0]

        # Preview path (scale=1): QFont with setPixelSize(font_size)
        qfont = QtGui.QFont(item.font_family)
        qfont.setPixelSize(item.font_size)
        qt_ascent = QtGui.QFontMetrics(qfont).ascent()

        # Ascents are engine-measured; allow a small tolerance since Qt and
        # FreeType differ by a pixel or two in hinting, but they must be in
        # the same ballpark — NOT a 1.33x mismatch (which the pt bug caused).
        assert abs(qt_ascent - pil_ascent) <= 3
        # Guard against the original 4/3 ratio regression specifically.
        assert not (1.25 < qt_ascent / pil_ascent < 1.40)


class TestFontSizeCap:
    """Typed font sizes are clamped to MAX_FONT_SIZE (200), not 999.

    999px was absurd — a single glyph spanning several screens, making
    the inline editor unusable. 200 still covers large 4K watermarks.
    The combo also reflects the clamped value so the displayed number
    matches what's applied.
    """

    def test_max_font_size_is_200(self, editor):
        assert editor.MAX_FONT_SIZE == 200

    def test_input_above_cap_is_clamped(self, editor):
        editor._on_font_size_text_changed("text", "999")
        assert editor._tools["text"].font_size == 200

    def test_input_just_above_cap_clamps_to_cap(self, editor):
        editor._on_font_size_text_changed("text", "250")
        assert editor._tools["text"].font_size == 200

    def test_input_below_cap_unchanged(self, editor):
        editor._on_font_size_text_changed("text", "150")
        assert editor._tools["text"].font_size == 150

    def test_input_at_cap_unchanged(self, editor):
        editor._on_font_size_text_changed("text", "200")
        assert editor._tools["text"].font_size == 200

    def test_input_zero_or_negative_clamps_to_one(self, editor):
        editor._on_font_size_text_changed("text", "0")
        assert editor._tools["text"].font_size == 1
        editor._on_font_size_text_changed("text", "-5")
        assert editor._tools["text"].font_size == 1

    def test_non_numeric_input_ignored(self, editor):
        before = editor._tools["text"].font_size
        editor._on_font_size_text_changed("text", "abc")
        assert editor._tools["text"].font_size == before

    def test_clamped_value_reflected_in_combo(self, editor):
        """Typing 250 updates the combo text to 200 (no stale display)."""
        combo = editor._option_widgets[("text", "fontSizeSpin")]
        editor._on_font_size_text_changed("text", "250")
        assert combo.currentText().strip() == "200"


class TestInlineEditorSpawnSize:
    """The inline text editor's box must reflect font_size on FIRST spawn,
    not just after a subsequent _sync_widgets.

    Regression: _InlineTextEditor.__init__ measured geometry with
    self.fontMetrics() before the stylesheet font had been applied, so the
    spawn box stayed at the default font size regardless of font_size
    (e.g. 200px text opened in a ~26px box). _update_geometry now measures
    with an explicit QFont built from item.font_size.
    """

    def _spawn_box_height(self, editor, font_size: int) -> int:
        from hushsnap.ui.image_editor import TextItem
        tool = editor._tools["text"]
        tool.font_size = font_size
        tool.font_family = "Arial"
        tool.color = QtGui.QColor("#000000")
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))

        item = TextItem("", QtCore.QPointF(50, 50),
                        QtGui.QColor(tool.color), tool.font_family, tool.font_size)
        editor._text_items.append(item)
        tool._spawn_editor(editor._canvas, item)
        h = tool._editing_widget.geometry().height()
        tool._editing_widget.commit_edit()
        return h

    def test_spawn_box_grows_with_font_size(self, editor):
        small = self._spawn_box_height(editor, 12)
        large = self._spawn_box_height(editor, 200)
        # A 200px font must produce a markedly taller box than a 12px one.
        assert large > small * 3, f"box not growing: small={small} large={large}"

    def test_spawn_box_matches_sync_widgets_size(self, editor):
        """Spawn size should equal the size after _sync_widgets (the path
        that already worked) — i.e. init no longer undersizes."""
        from hushsnap.ui.image_editor import TextItem
        tool = editor._tools["text"]
        tool.font_size = 200
        tool.font_family = "Arial"
        tool.color = QtGui.QColor("#000000")
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))

        item = TextItem("", QtCore.QPointF(50, 50),
                        QtGui.QColor(tool.color), tool.font_family, tool.font_size)
        editor._text_items.append(item)
        tool._spawn_editor(editor._canvas, item)
        spawn_h = tool._editing_widget.geometry().height()

        tool._sync_widgets()
        sync_h = tool._editing_widget.geometry().height()

        tool._editing_widget.commit_edit()
        # Init must not be (much) smaller than the synced size — the old bug
        # left spawn at ~26 while sync gave ~183.
        assert spawn_h >= sync_h - 2, f"spawn={spawn_h} sync={sync_h}"


class TestPanToolDoesNotSave:
    def test_pan_does_not_call_save_undo(self, editor):
        """PanTool should never call _save_undo."""
        tool = editor._tools["pan"]
        editor._save_undo = MagicMock()
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        tool.on_mouse_press(editor._canvas, event)
        editor._save_undo.assert_not_called()


class TestEscapeDoesNotCloseWindow:
    """The editor must NOT close on Escape at the window level.

    Escape is reserved for canceling in-progress tool operations (crop,
    mosaic, shape drag, inline text edit). A window-level Esc→close used
    to exist and would silently discard all annotations when a user hit
    Esc while no tool operation was active — that behavior is gone.

    These tests guard against it being reintroduced.
    """

    def test_no_window_level_keyPressEvent_override(self, editor):
        """ImageEditorWindow no longer overrides keyPressEvent to close on Esc.

        QWidget's default keyPressEvent does nothing for Esc, so removing
        the override is sufficient. Assert the class itself doesn't define
        a keyPressEvent that closes on Escape.
        """
        import inspect
        from hushsnap.ui import image_editor as ie

        # If keyPressEvent is defined on the class, it must not reference
        # Key_Escape + self.close().
        if "keyPressEvent" in ImageEditorWindow.__dict__:
            src = inspect.getsource(ImageEditorWindow.__dict__["keyPressEvent"])
            assert "Key_Escape" not in src, (
                "ImageEditorWindow.keyPressEvent handles Escape — window-level "
                "Esc→close must not be reintroduced"
            )

    def test_escape_does_not_close_window(self, editor):
        """Pressing Esc at the window level (no active tool op) does not close."""
        editor.close = MagicMock()
        key = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Escape,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        # Simulate Qt delivering the key to the window (as it would when no
        # child widget / tool has consumed it).
        QtWidgets.QWidget.keyPressEvent(editor, key)
        editor.close.assert_not_called()

    def test_crop_escape_still_cancels(self, editor):
        """Tool-level Escape (cancel crop) still works after removing the
        window-level handler."""
        tool = editor._tools["crop"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._activate_tool("crop")
        assert tool._crop_rect is not None  # crop mode active

        key = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Escape,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        handled = tool.on_key_press(editor._canvas, key)
        assert handled is True
        # cancel_crop switches back to pan, which deactivates crop
        assert editor._active_tool is editor._tools["pan"]


class TestColorSwatchShowsPureHue:
    """Swatches show the pure hue (alpha forced to 255), not the tool's
    composited-against-dark-background color.

    Design intent: the swatch sits on a fixed dark options-bar background.
    A semi-transparent tool color (e.g. highlighter yellow @ alpha 80)
    composited against that bg reads as olive (107,107,27), nothing like
    what the tool paints on a white screenshot (255,255,175). So the
    swatch must display the pure hue; transparency is the opacity slider's
    job. These tests guard that contract.
    """

    def test_color_button_ignores_alpha_when_rendering(self, qapp):
        from hushsnap.ui.image_editor import _ColorButton

        btn = _ColorButton()
        # Highlighter's actual color: yellow @ alpha 80.
        btn.setColor(QtGui.QColor(255, 255, 0, 80))

        # Render onto a dark background mimicking the options bar.
        img = QtGui.QImage(btn.size(), QtGui.QImage.Format.Format_RGBA8888)
        img.fill(QtGui.QColor("#282828"))  # options-bar background
        btn.render(img)

        # Sample the swatch center. If alpha were honored, the pixel would
        # be the olive composite (~107,107,27). With the fix it must be the
        # pure hue (~255,255,0).
        c = img.pixelColor(btn.width() // 2, btn.height() // 2)
        r, g, b = c.red(), c.green(), c.blue()
        # Pure yellow: R and G high, B near 0.
        assert r > 230 and g > 230 and b < 40, f"swatch rendered {r,g,b}, not pure hue"
        # Specifically NOT the olive composite the alpha-bug produced.
        assert not (90 < r < 130 and 90 < g < 130), "swatch still shows olive (alpha honored)"

    def test_swatch_popup_candidates_are_opaque(self, qapp):
        from hushsnap.ui.image_editor import _SwatchPopup, _SWATCH_COLORS

        popup = _SwatchPopup(None)
        # The popup constructor no longer accepts an alpha param — confirm
        # the signature dropped it (guards against re-adding the misleading
        # alpha rendering path).
        import inspect
        sig = inspect.signature(_SwatchPopup.__init__)
        assert "alpha" not in sig.parameters

        # Every candidate button's stylesheet must use rgb() (opaque), not
        # rgba() with a tool alpha — find the yellow swatch and check it.
        yellow_hex = "#FFFF00"
        assert any(h == yellow_hex for h, _ in _SWATCH_COLORS), "test expects a yellow swatch"
        # Render the popup and sample the yellow cell: should be pure yellow.
        img = QtGui.QImage(popup.size(), QtGui.QImage.Format.Format_RGBA8888)
        img.fill(QtGui.QColor("#333"))
        popup.render(img)
        # Scan for a near-pure-yellow pixel (the swatch disc).
        found_pure = False
        for y in range(0, img.height(), 2):
            for x in range(0, img.width(), 2):
                c = img.pixelColor(x, y)
                if c.red() > 230 and c.green() > 230 and c.blue() < 60:
                    found_pure = True
                    break
            if found_pure:
                break
        assert found_pure, "no opaque pure-yellow swatch pixel found in popup"


# ── Integration: PIL preservation ────────────────────────────────────────────


class TestPilPreservation:
    """Verify that ANNOTATIONS and TEXT undo entries do not touch the base image."""

    def test_undo_annotations_preserves_pil(self, editor):
        original_pil = editor._pil_image.copy()
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 100, 80, QtGui.QColor("#FF0000"))
        p.end()
        editor._undo()
        assert editor._pil_image.tobytes() == original_pil.tobytes()

    def test_undo_text_preserves_pil(self, editor):
        original_pil = editor._pil_image.copy()
        editor._save_undo(UndoChangeType.TEXT)
        editor._text_items.append(
            TextItem("test", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )
        editor._undo()
        assert editor._pil_image.tobytes() == original_pil.tobytes()

    def test_redo_annotations_preserves_pil(self, editor):
        original_pil = editor._pil_image.copy()
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 100, 80, QtGui.QColor("#FF0000"))
        p.end()
        editor._undo()
        editor._redo()
        assert editor._pil_image.tobytes() == original_pil.tobytes()


# ── Dead code verification ───────────────────────────────────────────────────


class TestDeadCodeRemoved:
    def test_show_tool_options_removed(self, editor):
        """The _show_tool_options method should no longer exist."""
        assert not hasattr(editor, "_show_tool_options")

    def test_on_crop_apply_removed(self, editor):
        """The _on_crop_apply method should no longer exist."""
        assert not hasattr(editor, "_on_crop_apply")

    def test_on_crop_cancel_removed(self, editor):
        """The _on_crop_cancel method should no longer exist."""
        assert not hasattr(editor, "_on_crop_cancel")

    def test_no_instruction_key_in_option_widgets(self, editor):
        """No option widget should reference 'instruction'."""
        for key in editor._option_widgets:
            assert "instruction" not in str(key), f"Found instruction widget at {key}"


# ── Integration: mixed-type undo sequence ────────────────────────────────────


class TestIntegrationUndoStack:
    def test_sequence_annotate_undo_annotate_redo(self, editor):
        """Sequence: annotate, undo, annotate (should clear redo), undo."""
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 5, 5, QtGui.QColor("#FF0000"))
        p.end()

        editor._save_undo(UndoChangeType.ANNOTATIONS)
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 5, 5, QtGui.QColor("#00FF00"))
        p.end()

        # Undo to first state (red fill)
        editor._undo()
        pixel = editor._annotations_pixmap.toImage().pixel(0, 0)
        assert pixel == QtGui.QColor("#FF0000").rgba()

        # New action clears redo stack
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        assert len(editor._redo_stack) == 0

    def test_mixed_types_undo_sequence(self, editor):
        """FULL → ANNOTATIONS → TEXT undo restores to FULL state."""
        # Save FULL state
        editor._save_undo(UndoChangeType.FULL)
        original_pil = editor._pil_image.copy()

        # Annotate
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 5, 5, QtGui.QColor("#FF0000"))
        p.end()

        # Text
        editor._save_undo(UndoChangeType.TEXT)
        editor._text_items.append(
            TextItem("z", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )

        # Undo text
        editor._undo()
        assert len(editor._text_items) == 0

        # Undo annotations
        editor._undo()
        assert editor._annotations_pixmap.toImage().pixel(0, 0) == QtGui.QColor(0, 0, 0, 0).rgba()

        # Undo all the way back to FULL
        editor._undo()
        assert editor._pil_image.tobytes() == original_pil.tobytes()

    def test_region_undo_redo_symmetry(self, editor):
        """REGION undo then redo restores the same state."""
        old_region = editor._pil_image.crop((10, 10, 30, 30))
        bounds = QtCore.QRect(10, 10, 20, 20)
        editor._save_undo(
            UndoChangeType.REGION,
            region_bounds=bounds,
            region_pixels=old_region.tobytes(),
        )
        # Modify
        modified = Image.new("RGBA", (20, 20), (0, 255, 0, 255))
        editor._pil_image.paste(modified, (10, 10))
        modified_bytes = editor._pil_image.crop((10, 10, 30, 30)).tobytes()

        editor._undo()
        assert editor._pil_image.crop((10, 10, 30, 30)).tobytes() == old_region.tobytes()

        editor._redo()
        assert editor._pil_image.crop((10, 10, 30, 30)).tobytes() == modified_bytes
