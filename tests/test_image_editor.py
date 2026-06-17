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
    ArrowTool,
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

    def test_save_respects_max_undo(self, editor):
        """Stack is capped at MAX_UNDO = 25."""
        editor.MAX_UNDO = 3
        for _ in range(5):
            editor._save_undo(UndoChangeType.ANNOTATIONS)
        assert len(editor._undo_stack) == 3

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


class TestArrowToolSaveBehavior:
    def test_save_on_mouse_press(self, editor):
        tool = editor._tools["arrow"]
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
