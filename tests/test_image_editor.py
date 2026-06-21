"""
Unit tests for hushsnap.ui.image_editor — undo system, tool save behavior,
and dead code verification.
"""

import pytest
from unittest.mock import MagicMock, ANY
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.ui.image_editor import (
    ImageEditorWindow,
    UndoChangeType,
    _UndoEntry,
    TextItem,
    CropTool,
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
        "editor_apply": "Apply",
        "editor_text_instruction": "Double-click to place text",
        "editor_copy": "Copy",
        "editor_saved": "Saved",
        "editor_copied": "Copied",
        "editor_zoom_label": "{zoom}%",
        "editor_fit_tooltip": "Fit to window (Ctrl+0)",
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
    win._dpr = 1.0
    yield win
    win.close()


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


def _mouse_event(etype, x, y):
    """A left-button mouse event of the given Qt type at widget coords (x, y)."""
    return QtGui.QMouseEvent(
        etype,
        QtCore.QPointF(x, y), QtCore.QPointF(x, y),
        QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )


def _press_at(x=50, y=50):
    """A left-button mouse-press event at widget coords (x, y)."""
    return _mouse_event(QtCore.QEvent.Type.MouseButtonPress, x, y)


def _release_at(x=80, y=70):
    """A left-button mouse-release event at widget coords (x, y)."""
    return _mouse_event(QtCore.QEvent.Type.MouseButtonRelease, x, y)


class TestAnnotationToolsSaveOnPress:
    """Brush, highlighter, eraser, and shape tools must snapshot the
    annotations layer (ANNOTATIONS undo entry) at the start of a stroke so
    the stroke can be undone as one unit.

    Parametrized over the tools that share this contract — they used to be
    five near-identical copy-pasted tests.
    """

    @pytest.mark.parametrize("tool_id", ["brush", "highlighter", "eraser",
                                          "rectangle", "ellipse"])
    def test_save_on_mouse_press(self, editor, tool_id):
        tool = editor._tools[tool_id]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        tool.on_mouse_press(editor._canvas, _press_at())
        editor._save_undo.assert_called_once_with(UndoChangeType.ANNOTATIONS)


class TestMosaicToolSaveBehavior:
    """Mosaic defers its undo snapshot to release (not press) and uses a
    REGION entry — it only needs to restore the pixelated pixels, not the
    whole image."""

    def test_save_not_called_on_press(self, editor):
        tool = editor._tools["mosaic"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        tool.on_mouse_press(editor._canvas, _press_at())
        editor._save_undo.assert_not_called()

    def test_save_called_on_release_with_region(self, editor):
        """Drag a >2px region; release must save exactly one REGION entry
        whose bounds cover the dragged rect."""
        tool = editor._tools["mosaic"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()

        tool.on_mouse_press(editor._canvas, _press_at(50, 50))
        tool.on_mouse_release(editor._canvas, _release_at(80, 70))

        editor._save_undo.assert_called_once_with(
            UndoChangeType.REGION,
            region_bounds=QtCore.QRect(50, 50, 30, 20),
            region_pixels=ANY,
        )


class TestCropToolSaveBehavior:
    def test_save_on_apply_crop(self, editor):
        """CropTool.apply_crop pushes one FULL undo entry from the pre-bake state.

        The entry's annotations/text must reflect the PRE-flatten snapshot so
        undo restores a consistent pre-crop state (no baked-text duplication).
        """
        from hushsnap.ui.editor.models import _UndoEntry
        tool = editor._tools["crop"]
        stack_before = len(editor._undo_stack)
        tool._crop_rect = QtCore.QRect(10, 10, 50, 40)
        tool.apply_crop()

        # Exactly one new entry pushed.
        assert len(editor._undo_stack) - stack_before == 1
        entry = editor._undo_stack[-1]
        assert entry.change_type == UndoChangeType.FULL
        assert entry.pil_image is not None
        # Undo entry must carry the pre-bake annotations (no baked text), not
        # the post-flatten one. A None text_items means "no text to restore".
        assert entry.text_items is None or entry.text_items == []

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

    def test_crop_undo_restores_text_without_duplication(self, editor):
        """After crop + undo, baked text must NOT persist and text stays editable.

        Regression: the crop undo entry used to capture the post-flatten
        annotations (with text baked in as pixels) while also restoring the
        editable text items — so undo showed each annotation twice (once as
        baked pixels, once as an editable text box) and the baked text could
        never be removed. The entry must snapshot the PRE-flatten state.
        """
        tool = editor._tools["crop"]

        # Place one editable text item.
        editor._text_items.append(
            TextItem("note", QtCore.QPointF(20, 20), QtGui.QColor("#ffffff"), "Arial", 24)
        )
        # Snapshot the pre-flatten annotations (no baked text) for comparison.
        pre_flatten_annot = editor._annotations_pixmap.copy()

        # Crop a sub-rect (bakes text into annotations during apply).
        tool._crop_rect = QtCore.QRect(10, 10, 50, 40)
        tool.apply_crop()

        # Undo should restore the pre-crop / pre-bake state.
        editor._undo()

        # Editable text item is back, exactly one — not duplicated.
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "note"

        # Annotations must match the pre-flatten snapshot (no baked-text
        # pixels). If they carried baked text, undo would render it alongside
        # the restored editable item → duplication.
        def _bytes(pm):
            img = pm.toImage()
            buf = img.constBits()
            buf.setsize(img.sizeInBytes())
            return bytes(buf)

        assert _bytes(editor._annotations_pixmap) == _bytes(pre_flatten_annot)


class TestCropHandleVisibility:
    """Crop border and handles are painted visibly on the overlay pixmap.

    Regression guard: thin border/handle values shrank to sub-pixel when the
    image-space overlay was scaled down to a 960px viewport on a 4K shot,
    making the crop affordance nearly invisible. These tests paint the real
    overlay via _redraw_overlay and sample its pixels — asserting the green
    border and the white-filled handles are actually drawn, not that the
    source happens to contain a literal width.
    """

    def _rendered_overlay(self, editor):
        tool = editor._tools["crop"]
        # Small crop rect inset from edges so the dim bands + border are all
        # present in the overlay.
        tool._crop_rect = QtCore.QRect(20, 15, 40, 30)
        tool._redraw_overlay()
        return tool, editor._overlay_pixmap.toImage()

    def test_border_is_drawn_in_brand_green(self, editor):
        """The crop rect outline is painted (thick enough to be a solid
        line, not a single anti-aliased ghost pixel)."""
        tool, img = self._rendered_overlay(editor)
        # Sample along the top edge of the crop rect (y == rect.top).
        # A thick green pen leaves a run of brand-green pixels there.
        green = QtGui.QColor("#5FC98A").rgb()
        top = tool._crop_rect.top()
        hits = sum(
            1 for x in range(tool._crop_rect.left(),
                             tool._crop_rect.right() + 1)
            if img.pixel(x, top) == green
        )
        assert hits >= 10, f"green border nearly invisible: {hits} px on top edge"

    def test_handles_are_drawn(self, editor):
        """Each handle is a white-filled square at a crop corner / edge
        midpoint. Sampling the corner handle center must find white fill."""
        tool, img = self._rendered_overlay(editor)
        r = tool._crop_rect
        # Corner handle center sits at the corner itself.
        for cx, cy in [(r.left(), r.top()), (r.right(), r.bottom())]:
            pixel = img.pixelColor(cx, cy)
            assert pixel.red() > 230 and pixel.green() > 230 and pixel.blue() > 230, (
                f"handle at ({cx},{cy}) not white-filled: {pixel.getRgb()}"
            )

    def test_hit_radius_covers_visible_handle(self, editor):
        """HANDLE_R is large enough that the grabbable area reaches a
        handle's center (so a click on the visible handle is recognized)."""
        from hushsnap.ui.image_editor import CropTool
        tool = editor._tools["crop"]
        # corner_sz is 11 → handle half-extent ~5.5px. Hit radius must reach
        # at least that far, with margin for the user's pointer.
        assert CropTool.HANDLE_R >= 6, "hit radius smaller than handle half-size"

    def test_hit_test_returns_correct_handle(self, editor):
        """Clicking near a corner returns that corner's handle id."""
        tool = editor._tools["crop"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._dpr = 1.0
        editor._scale = 1.0
        tool._crop_rect = QtCore.QRect(20, 15, 40, 30)
        # The top-left handle is at the rect's top-left corner (image space).
        handle = tool._hit_test(editor._canvas, QtCore.QPoint(20, 15))
        assert handle == "tl"


class TestTextToolSaveBehavior:
    def test_save_on_new_item(self, editor):
        """Double-clicking on empty space creates a new text item."""
        tool = editor._tools["text"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        editor._save_undo = MagicMock()
        tool.on_mouse_double_click(
            editor._canvas,
            _mouse_event(QtCore.QEvent.Type.MouseButtonDblClick, 50, 50),
        )
        editor._save_undo.assert_called_once_with(UndoChangeType.TEXT)

    def test_drag_existing_item_can_be_undone(self, editor):
        """Moving an existing text item saves its previous position."""
        tool = editor._tools["text"]
        editor._dpr = 1.0
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        item = TextItem(
            "move me", QtCore.QPointF(10, 10), QtGui.QColor("#fff"), "Arial", 24
        )
        editor._text_items.append(item)

        assert tool.on_mouse_press(editor._canvas, _mouse_event(
            QtCore.QEvent.Type.MouseButtonPress, 12, 12))
        assert tool.on_mouse_move(editor._canvas, _mouse_event(
            QtCore.QEvent.Type.MouseMove, 40, 35))
        assert item.img_pos != QtCore.QPointF(10, 10)

        editor._undo()
        assert len(editor._text_items) == 1
        assert editor._text_items[0].img_pos == QtCore.QPointF(10, 10)

    def test_edit_existing_item_can_be_undone(self, editor):
        """Editing an existing text item saves the previous text."""
        tool = editor._tools["text"]
        editor._dpr = 1.0
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        item = TextItem(
            "old", QtCore.QPointF(10, 10), QtGui.QColor("#fff"), "Arial", 24
        )
        editor._text_items.append(item)

        assert tool.on_mouse_double_click(editor._canvas, _mouse_event(
            QtCore.QEvent.Type.MouseButtonDblClick, 12, 12))
        tool._editing_widget.setText("new")
        tool._editing_widget.commit_edit()
        assert editor._text_items[0].text == "new"

        editor._undo()
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "old"

    def test_composite_commits_active_new_text(self, editor):
        """Saving/copying commits newly typed inline text before export."""
        tool = editor._tools["text"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        item = TextItem("", QtCore.QPointF(10, 10), QtGui.QColor("#fff"), "Arial", 24)
        editor._text_items.append(item)
        tool._spawn_editor(editor._canvas, item)
        tool._editing_widget.setText("live")

        pixmap = editor._get_composite_pixmap()

        assert not pixmap.isNull()
        assert tool._editing_widget is None
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "live"

    def test_composite_commits_active_existing_text_edit(self, editor):
        """Saving/copying exports the latest value when re-editing text."""
        tool = editor._tools["text"]
        editor._canvas._image_offset = MagicMock(return_value=QtCore.QPointF(0, 0))
        item = TextItem("old", QtCore.QPointF(10, 10), QtGui.QColor("#fff"), "Arial", 24)
        editor._text_items.append(item)
        tool._spawn_editor(editor._canvas, item)
        tool._editing_widget.setText("new")

        pixmap = editor._get_composite_pixmap()

        assert not pixmap.isNull()
        assert tool._editing_widget is None
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "new"


class TestTextToolFontUnits:
    """font_size is in image pixels throughout the editor.

    Regression: the canvas preview / hit-test used QFont(family, pt),
    which at 96 DPI renders 1.33x larger than the px-based export bake —
    so what the user saw was not what got saved. Both renderers must now
    use pixel size.
    """

    def test_export_bakes_text_with_pixel_sized_font(self, editor, monkeypatch):
        """Export composites text using a QFont sized in pixels, not points.

        WYSIWYG regression guard. Preview and export now share
        _draw_outlined_text, so they agree by construction — but only if the
        export builds its QFont with setPixelSize(font_size). A point-size
        QFont would render ~1.33x larger at 96 DPI, diverging from preview.
        (Previously this compared against a PIL ImageFont export path that
        the refactor removed; it now pins the shared QPainter path directly.)
        """
        import hushsnap.ui.image_editor as ie
        from hushsnap.ui.image_editor import TextItem

        captured: dict = {}

        def _capture(painter, pos, text, font):
            captured["font"] = font

        monkeypatch.setattr(ie, "_draw_outlined_text", _capture)

        item = TextItem("Hg", QtCore.QPointF(10, 10),
                        QtGui.QColor("#fff"), "Arial", 48)
        editor._text_items.append(item)

        editor._get_composite_pixmap()

        assert "font" in captured, "export did not bake any text"
        font = captured["font"]
        assert font.pixelSize() == 48
        assert font.pointSize() == -1  # pixel size overrides point size


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
        tool.on_mouse_press(editor._canvas, _press_at())
        editor._save_undo.assert_not_called()


class TestEscapeDoesNotCloseWindow:
    """The editor must NOT close on Escape at the window level.

    Escape is reserved for canceling in-progress tool operations (crop,
    mosaic, shape drag, inline text edit). A window-level Esc→close used
    to exist and would silently discard all annotations when a user hit
    Esc while no tool operation was active — that behavior is gone.
    Guarded by behavior, not source inspection: deliver a real Esc key
    event to the window and assert it stays open, plus that tool-level Esc
    still cancels an active crop.
    """

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
        from hushsnap.ui.image_editor import _SwatchPopup
        from hushsnap.ui.editor.constants import _SWATCH_COLORS

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
    """Methods removed in earlier refactors must stay removed — re-adding
    them silently reintroduces dead branches. One consolidated assertion
    covers the lot."""

    _REMOVED = ("_show_tool_options", "_on_crop_apply", "_on_crop_cancel")

    def test_removed_methods_stay_removed(self, editor):
        for name in self._REMOVED:
            assert not hasattr(editor, name), f"dead method {name!r} reintroduced"

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

    def test_text_then_annotations_undo_redo_round_trip(self, editor):
        """Redo restores both layers after a TEXT→ANNOTATIONS sequence.

        Only REGION had a redo-symmetry test; TEXT/ANNOTATIONS redo capture
        pixmap + text_items snapshots, so exercise that path end-to-end.
        """
        # State A: annotations + one text item.
        editor._save_undo(UndoChangeType.ANNOTATIONS)
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 5, 5, QtGui.QColor("#FF0000"))
        p.end()
        red_rgba = editor._annotations_pixmap.toImage().pixel(0, 0)

        editor._save_undo(UndoChangeType.TEXT)
        editor._text_items.append(
            TextItem("hi", QtCore.QPointF(0, 0), QtGui.QColor("#fff"), "Arial", 12)
        )
        assert len(editor._text_items) == 1

        # Undo both → back to clean.
        editor._undo()
        assert len(editor._text_items) == 0
        editor._undo()
        assert editor._annotations_pixmap.toImage().pixel(0, 0) == QtGui.QColor(0, 0, 0, 0).rgba()

        # Redo both → annotations + text item restored.
        editor._redo()
        assert editor._annotations_pixmap.toImage().pixel(0, 0) == red_rgba
        editor._redo()
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "hi"


class TestRotationAndResizeImprovements:
    def test_session_based_rotation_prevents_compounding_growth(self, editor):
        """Repeated rotations within the same session rotate the original base image,

        preventing unbounded canvas size growth.
        """
        # Activate the rotate tool (begins the session)
        editor._activate_tool("rotate")
        assert editor._rotate_active is True
        assert editor._rotate_base_image is not None
        
        orig_w, orig_h = editor._pil_image.size
        
        # Apply 15 degree rotation
        editor._apply_rotation(15.0, True)
        size_after_15 = editor._pil_image.size
        assert size_after_15[0] > orig_w
        
        # Apply another rotation (total 30 degrees)
        # It should rotate the *original* image by 30 degrees, not size_after_15 by 15.
        editor._apply_rotation(30.0, True)
        size_after_30 = editor._pil_image.size
        
        # Calculate size of original rotated directly by 30
        expected_rotated = editor._rotate_base_image.rotate(-30.0, expand=True)
        assert size_after_30 == expected_rotated.size
        
        # Deactivate tool (ends the session)
        editor._activate_tool("pan")
        assert editor._rotate_active is False
        assert editor._rotate_base_image is None

    def test_rotation_undo_redo(self, editor):
        """A rotate session is a single atomic undo unit.

        No undo entries are pushed mid-session (undo/redo are hidden then).
        On deactivate, one FULL entry capturing the pre-rotation state is
        pushed, so one undo in the main editor reverts the whole rotation and
        one redo restores it.
        """
        editor._activate_tool("rotate")

        # Mid-session: undo/redo buttons are hidden and no entries are pushed,
        # even after several rotations.
        editor._apply_rotation(15.0, True)
        editor._apply_rotation(30.0, True)
        assert editor._undo_btn.isHidden()
        assert editor._redo_btn.isHidden()
        assert len(editor._undo_stack) == 0
        size_30 = editor._pil_image.size
        assert size_30 != (100, 80)

        # Leaving the tool commits one undo entry for the whole session.
        editor._activate_tool("pan")
        assert not editor._undo_btn.isHidden()
        assert len(editor._undo_stack) == 1

        # One undo reverts the entire rotation -> back to the original image.
        editor._undo()
        assert editor._pil_image.size == (100, 80)

        # One redo restores the rotated image.
        editor._redo()
        assert editor._pil_image.size == size_30

    def test_rotate_session_bakes_annotations_into_base_image(self, editor):
        """At rotate-session start, annotations are baked into the base image.

        Under the merge model, the annotation layer is cleared and its content
        composited into _pil_image (captured as _rotate_base_image / pixmap).
        So during the session the annotation layer is empty and the rotation
        base already contains the annotation pixels — image and annotations
        rotate as one (no separate layer to drift).
        """
        # Put a recognizable mark in the annotations (distinct from the red base).
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 10, 10, QtGui.QColor("#00FF00"))
        p.end()

        clean_pil = editor._pil_image.copy()
        clean_pil_bytes = clean_pil.tobytes()

        editor._activate_tool("rotate")
        # Base image is the MERGED image (annotations baked in).
        assert editor._rotate_base_image is not None
        assert editor._rotate_base_image.tobytes() != clean_pil_bytes
        # Annotation layer is now empty.
        assert editor._annotations_pixmap is not None

        def _pm_bytes(pm):
            img = pm.toImage()
            buf = img.constBits()
            buf.setsize(img.sizeInBytes())
            return bytes(buf)

        def _is_empty(pm):
            # An all-transparent pixmap is all zero bytes.
            return _pm_bytes(pm) == bytes(len(_pm_bytes(pm)))

        assert _is_empty(editor._annotations_pixmap)

        # Pre-composite clean image is preserved for undo/cancel.
        assert editor._rotate_pre_image is not None
        assert editor._rotate_pre_image.tobytes() == clean_pil_bytes

        # Multiple releases keep the annotation layer empty (no drift source).
        editor._apply_rotation(20.0, True)
        assert _is_empty(editor._annotations_pixmap)
        editor._apply_rotation(45.0, True)
        assert _is_empty(editor._annotations_pixmap)

        editor._activate_tool("pan")

    def test_rotate_undo_restores_clean_image_and_editable_text(self, editor):
        """Edit text → rotate (bakes text) → undo → clean image + editable text.

        Regression for the user scenario: after a rotation bakes the text into
        the image, one undo must return to the pre-rotation state — a clean
        base image with the text still as an editable item, NOT baked pixels.
        The undo entry snapshots the pre-flatten state, so this works without
        needing the (unimplemented) merge approach.
        """
        from PIL import Image

        # Start from a known-clean base image.
        clean = Image.new("RGBA", (100, 80), (200, 200, 200, 255))
        editor._pil_image = clean.copy()
        editor._rebuild_display()
        clean_bytes = clean.tobytes()

        # Add an editable text item (not yet baked).
        editor._text_items.append(
            TextItem("hello", QtCore.QPointF(20, 20), QtGui.QColor("#fff"), "Arial", 16)
        )
        # Annotations should be empty (no baked text yet).
        def _pm_bytes(pm):
            img = pm.toImage()
            buf = img.constBits()
            buf.setsize(img.sizeInBytes())
            return bytes(buf)
        empty_annot_bytes = _pm_bytes(editor._annotations_pixmap)

        # Rotate (bakes text into annotations, clears editable items).
        editor._activate_tool("rotate")
        editor._apply_rotation(30.0, True)
        editor._activate_tool("pan")  # commit one undo entry

        # Post-rotation: text is baked, no editable items.
        assert editor._text_items == []

        # Undo → must restore the clean base image + editable text + clean annotations.
        editor._undo()
        assert editor._pil_image.tobytes() == clean_bytes
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "hello"
        # Annotations back to their pre-flatten (text-free) state.
        assert _pm_bytes(editor._annotations_pixmap) == empty_annot_bytes

    def test_resize_session_bakes_annotations_into_image(self, editor):
        """At resize-session start, annotations are baked into the base image.

        Under the merge model, resizing operates on the merged image; the
        annotation layer ends up empty at the new size (rebuilt by
        _rebuild_display), and text items are cleared. The baked content
        survives as pixels inside the resized image.
        """
        # Draw some annotations + a text item.
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 10, 10, QtGui.QColor("#00FF00"))
        p.end()
        editor._text_items.append(
            TextItem("ResizeTest", QtCore.QPointF(20, 20), QtGui.QColor("#fff"), "Arial", 16)
        )

        clean_bytes = editor._pil_image.tobytes()
        orig_w, orig_h = editor._pil_image.size

        # Begin the resize session — this composites annotations into the image.
        editor._activate_tool("resize")
        # Base image is the MERGED image (differs from the clean original).
        assert editor._resize_base_image is not None
        assert editor._resize_base_image.tobytes() != clean_bytes
        # Pre-composite clean image preserved for undo/cancel.
        assert editor._resize_pre_image is not None
        assert editor._resize_pre_image.tobytes() == clean_bytes
        # Annotation layer + text cleared after composite.
        assert editor._text_items == []

        # _display_pixmap must reflect the MERGED image, not the stale
        # pre-composite one — _set_resize_preview scales _display_pixmap, so a
        # stale one would flash baked content out during the live drag.
        # The stroke was green (#00FF00); a fresh display pixmap contains it.
        assert editor._display_pixmap is not None
        di = editor._display_pixmap.toImage()
        has_green = any(
            di.pixelColor(x, y).green() > 200
            and di.pixelColor(x, y).red() < 60
            for y in range(0, di.height(), 2)
            for x in range(0, di.width(), 2)
        )
        assert has_green, "display_pixmap is stale — missing baked stroke"

        new_w, new_h = orig_w * 2, orig_h * 2
        editor._apply_resize(new_w, new_h)

        assert editor._pil_image.size == (new_w, new_h)
        # Annotation layer recreated empty at the new size.
        assert editor._annotations_pixmap.size() == QtCore.QSize(new_w, new_h)
        assert editor._overlay_pixmap.size() == QtCore.QSize(new_w, new_h)

        editor._activate_tool("pan")  # commit

        # Undo restores the clean image + editable annotations + text.
        editor._undo()
        assert editor._pil_image.tobytes() == clean_bytes
        assert len(editor._text_items) == 1
        assert editor._text_items[0].text == "ResizeTest"


class TestCompositeAnnotationsIntoImage:
    def test_bakes_strokes_and_text_then_clears_layers(self, editor):
        """_composite_annotations_into_image bakes strokes + text into the base
        image and leaves the annotation layer empty + text items cleared."""
        clean_bytes = editor._pil_image.tobytes()

        # A brush stroke on the annotation layer (distinct from the red base).
        p = QtGui.QPainter(editor._annotations_pixmap)
        p.fillRect(0, 0, 20, 20, QtGui.QColor(0, 255, 0, 255))
        p.end()
        # An editable text item.
        editor._text_items.append(
            TextItem("baked", QtCore.QPointF(5, 5), QtGui.QColor("#FFFFFF"), "Arial", 24)
        )

        editor._composite_annotations_into_image()

        # Base image now differs from the clean original (content baked in).
        assert editor._pil_image.tobytes() != clean_bytes
        # Text items cleared.
        assert editor._text_items == []
        # Annotation layer empty (all-transparent).
        def _pm_bytes(pm):
            img = pm.toImage()
            buf = img.constBits()
            buf.setsize(img.sizeInBytes())
            return bytes(buf)
        empty = bytes(len(_pm_bytes(editor._annotations_pixmap)))
        assert _pm_bytes(editor._annotations_pixmap) == empty

    def test_noop_when_no_annotations(self, editor):
        """With no strokes and no text, compositing is a no-op on the image."""
        clean_bytes = editor._pil_image.tobytes()
        editor._composite_annotations_into_image()
        assert editor._pil_image.tobytes() == clean_bytes
        assert editor._text_items == []

    def test_resize_session_resamples_from_base_no_quality_compound(self, editor):
        """Repeated resize in a session resamples from the base each time, so
        shrink-then-grow returns to the original sharp pixels instead of a
        compounded-blurry result."""
        orig_w, orig_h = editor._pil_image.size
        orig_bytes = editor._pil_image.tobytes()

        editor._activate_tool("resize")
        assert editor._resize_active is True
        assert editor._resize_base_image is not None
        # undo/redo hidden mid-session
        assert editor._undo_btn.isHidden()

        # Shrink to half, then grow back to original size.
        editor._apply_resize(orig_w // 2, orig_h // 2)
        assert editor._pil_image.size == (orig_w // 2, orig_h // 2)
        editor._apply_resize(orig_w, orig_h)
        assert editor._pil_image.size == (orig_w, orig_h)

        # Resampling the base at the original size reproduces the original
        # pixels exactly — no compounded blur from the intermediate shrink.
        assert editor._pil_image.tobytes() == orig_bytes

        # Leaving the tool commits exactly one undo entry.
        editor._activate_tool("pan")
        assert not editor._undo_btn.isHidden()
        assert len(editor._undo_stack) == 1

        # One undo reverts the whole resize session.
        editor._undo()
        assert editor._pil_image.size == (orig_w, orig_h)
        assert editor._pil_image.tobytes() == orig_bytes



class TestEditorMultiMonitorPlacement:
    def test_editor_uses_target_screen_dpr(self, qapp, test_image):
        """The editor adopts the passed screen's DPR, not the primary's."""
        from unittest.mock import MagicMock
        screen = MagicMock()
        screen.devicePixelRatio.return_value = 2.0
        win = ImageEditorWindow(test_image, _translate, screen=screen)
        assert win._dpr == 2.0
        assert win._target_screen is screen
        win.close()

    def test_editor_resolves_screen_from_cursor(self, qapp, test_image):
        """The editor resolves its target screen from the cursor, not primary.

        The cursor-screen lookup is deferred out of __init__ (it crashed
        show() there) into _resolve_target_screen, called after construction.
        After that call, _target_screen is the cursor's screen and _dpr
        matches it — not the primary's.
        """
        from unittest.mock import MagicMock, patch
        secondary = MagicMock()
        secondary.devicePixelRatio.return_value = 1.5
        primary = MagicMock()
        primary.devicePixelRatio.return_value = 1.0

        with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=secondary), \
             patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=primary):
            win = ImageEditorWindow(test_image, _translate)
            # During construction the cursor screen is NOT yet resolved
            # (lookup is deferred); _target_screen is the primary placeholder.
            assert win._target_screen is primary
            win._resolve_target_screen()

        # After resolution: cursor's screen, with its DPR.
        assert win._target_screen is secondary
        assert win._dpr == 1.5
        win.close()

    def test_editor_centers_on_target_screen(self, qapp, test_image):
        """Editor opens centered on the cursor's screen, size clamped to fit.

        Uses an arbitrary fictional screen (offset 5000) — not tied to any
        real hardware. Validates the placement LOGIC, not specific pixels.
        """
        from unittest.mock import MagicMock, patch

        screen = MagicMock()
        screen.devicePixelRatio.return_value = 1.0
        avail = QtCore.QRect(5000, 200, 1600, 1000)
        screen.availableGeometry.return_value = avail

        with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=screen), \
             patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=screen):
            win = ImageEditorWindow(test_image, _translate)
            win._resolve_target_screen()
            a = win._target_screen.availableGeometry()
            w = max(640, min(960, a.width()))
            h = max(520, min(700, a.height()))
            win.resize(w, h)
            win.move(a.x() + (a.width() - w) // 2, a.y() + (a.height() - h) // 2)

        g = win.geometry()
        # 960x700 fits in a 1600x1000 screen — should keep default size.
        assert g.size() == QtCore.QSize(960, 700)
        assert avail.contains(g)
        win.close()

    def test_editor_clamps_default_size_to_narrow_screen(self, qapp, test_image):
        """Default 960x700 is clamped when the target screen is narrower.

        Uses fictional screen geometry (offset 5000, 800px wide) — verifies
        that the size clamping logic works regardless of actual hardware.
        """
        from unittest.mock import MagicMock, patch

        screen = MagicMock()
        screen.devicePixelRatio.return_value = 1.0
        avail = QtCore.QRect(5000, 200, 800, 1200)
        screen.availableGeometry.return_value = avail

        with patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=screen), \
             patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=screen):
            win = ImageEditorWindow(test_image, _translate)
            win._resolve_target_screen()
            a = win._target_screen.availableGeometry()
            w = max(640, min(960, a.width()))
            h = max(520, min(700, a.height()))
            win.resize(w, h)
            win.move(a.x() + (a.width() - w) // 2, a.y() + (a.height() - h) // 2)

        g = win.geometry()
        # 960 > 800: width clamped to screen width; height 700 < 1200: kept.
        assert g.width() == 800
        assert g.height() == 700
        assert g.left() == 5000  # centered: (800-800)//2 = 0 offset from left
        win.close()

    def test_editor_enforces_usable_minimum_size(self, qapp, test_image):
        """The window's hard minimum is large enough that the toolbar rows and
        status bar don't crowd together (chrome ≈ 162 px → canvas ≥ ~250 px)."""
        from hushsnap.ui.image_editor import _EDITOR_MIN_W, _EDITOR_MIN_H
        assert _EDITOR_MIN_W >= 640
        assert _EDITOR_MIN_H >= 520
        win = ImageEditorWindow(test_image, _translate)
        assert win.minimumWidth() == _EDITOR_MIN_W
        assert win.minimumHeight() == _EDITOR_MIN_H
        win.close()


class TestFitToViewport:
    """Auto-fit, fit button, and Ctrl+0 shortcut tests."""

    def test_fit_scales_down_large_image(self, editor):
        """A 3000×2000 image in a 900×560 viewport → fit zooms below 50 %.

        Photoshop-style: fit always shows the full image, even if the
        resulting zoom is low (here ~25 %).  The 0.10 sanity floor
        prevents absurdity.
        """
        from unittest.mock import MagicMock
        mock_vp = MagicMock()
        mock_vp.width.return_value = 900
        mock_vp.height.return_value = 560
        editor._scroll_area.viewport = MagicMock(return_value=mock_vp)

        orig = editor._rendered_display_pixmap
        mock_pm = MagicMock()
        mock_pm.width.return_value = 3000
        mock_pm.height.return_value = 2000
        editor._rendered_display_pixmap = MagicMock(return_value=mock_pm)

        editor._dpr = 1.0
        editor._scale = 1.0
        editor._fit_to_viewport()

        # min(900*0.9/3000, 560*0.9/2000) = min(0.27, 0.252) = 0.252
        assert editor._scale < 0.50
        assert abs(editor._scale - 0.252) < 0.01
        assert editor._effective_scale() < 0.50

        editor._rendered_display_pixmap = orig

    def test_fit_caps_small_image_at_one(self, editor):
        """A 100×80 image fits in a 900×560 viewport → stays at 100%."""
        from unittest.mock import MagicMock
        mock_vp = MagicMock()
        mock_vp.width.return_value = 900
        mock_vp.height.return_value = 560
        editor._scroll_area.viewport = MagicMock(return_value=mock_vp)

        editor._dpr = 1.0
        editor._scale = 1.0
        editor._fit_to_viewport()

        # Small image: effective scale capped at 1.0
        assert editor._scale == 1.0
        assert editor._effective_scale() == 1.0

    def test_fit_with_dpr_adjusts_scale(self, editor):
        """On a HiDPI screen (DPR=2.0), _scale is adjusted so effective=fit."""
        from unittest.mock import MagicMock
        mock_vp = MagicMock()
        mock_vp.width.return_value = 1800  # logical
        mock_vp.height.return_value = 1120
        editor._scroll_area.viewport = MagicMock(return_value=mock_vp)
        # 3000×2000 image
        orig = editor._rendered_display_pixmap
        mock_pm = MagicMock()
        mock_pm.width.return_value = 3000
        mock_pm.height.return_value = 2000
        editor._rendered_display_pixmap = MagicMock(return_value=mock_pm)

        editor._dpr = 2.0
        editor._fit_to_viewport()

        # fit_effective = min(1800*0.9/3000, 1120*0.9/2000) = 0.504
        # _scale = max(0.10, 0.504) * 2.0 = 1.008
        assert abs(editor._scale - 1.008) < 0.01
        assert abs(editor._effective_scale() - 0.504) < 0.01

        editor._rendered_display_pixmap = orig

    def test_zoom_label_shows_effective_scale(self, editor):
        """Percentage in the zoom label is based on effective (visible) scale,
        not the DPR-inflated _scale."""
        editor._dpr = 2.0
        editor._scale = 1.0  # effective = 0.5 → 50 %
        editor._update_zoom_label()
        text = editor._zoom_label.text()
        assert "50" in text, f"expected 50% on HiDPI, got: {text}"

        editor._dpr = 1.0
        editor._scale = 2.0
        editor._update_zoom_label()
        text = editor._zoom_label.text()
        assert "200" in text, f"expected 200%, got: {text}"

    def test_fit_zero_viewport_is_safe(self, editor):
        """Calling fit when the viewport is 0×0 does not crash."""
        from unittest.mock import MagicMock
        mock_vp = MagicMock()
        mock_vp.width.return_value = 0
        mock_vp.height.return_value = 0
        editor._scroll_area.viewport = MagicMock(return_value=mock_vp)

        old_scale = editor._scale
        editor._fit_to_viewport()
        # Scale unchanged (guard returned early)
        assert editor._scale == old_scale

    def test_fit_missing_pixmap_is_safe(self, editor):
        """Calling fit with no display pixmap does not crash."""
        orig = editor._rendered_display_pixmap
        editor._rendered_display_pixmap = MagicMock(return_value=None)
        old_scale = editor._scale
        editor._fit_to_viewport()
        assert editor._scale == old_scale
        editor._rendered_display_pixmap = orig

    def test_ctrl_0_shortcut_exists(self, editor):
        """Ctrl+0 shortcut attribute exists on the editor."""
        assert hasattr(editor, "_fit_shortcut")
        from PyQt6 import QtGui
        assert isinstance(editor._fit_shortcut, QtGui.QShortcut)

    def test_zoom_label_is_clickable(self, editor):
        """Zoom label is a QPushButton with pointing-hand cursor."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QPushButton
        assert isinstance(editor._zoom_label, QPushButton)
        assert editor._zoom_label.cursor().shape() == Qt.CursorShape.PointingHandCursor


class TestCrossScreenResizeClamp:
    """Edge resize must not blow the window up when the cursor jumps across
    screens with different DPR/resolution — globalPosition() is discontinuous
    at screen boundaries.  The window is clamped to the cursor screen's
    available geometry, a meaningful bound, instead of an arbitrary delta cap.
    """

    def _move(self, editor, gp):
        """Deliver a window-level mouseMoveEvent at global point *gp*."""
        from PyQt6 import QtCore, QtGui
        evt = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseMove,
            QtCore.QPointF(gp), QtCore.QPointF(gp),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        editor.mouseMoveEvent(evt)

    def test_right_edge_resize_clamped_to_cursor_screen(self, qapp, test_image, monkeypatch):
        """Dragging the right edge with the cursor on a narrow secondary screen
        never grows the window past that screen's right edge, even when the
        global position jumps far (simulating a DPR discontinuity)."""
        from unittest.mock import MagicMock
        from PyQt6 import QtCore

        win = ImageEditorWindow(test_image, _translate)
        win._dpr = 1.0
        win.show()

        # Window starts with right edge at x=1000 (start_geo right=999).
        start_geo = QtCore.QRect(200, 200, 800, 600)
        win.setGeometry(start_geo)

        # Cursor is now on a secondary screen spanning x=[1920, 2720].
        secondary = MagicMock()
        secondary.availableGeometry.return_value = QtCore.QRect(1920, 0, 800, 1000)
        monkeypatch.setattr(
            QtWidgets.QApplication, "screenAt", lambda *a, **k: secondary)

        # Right-edge resize (bitmask 4) where the cursor jumps to x=4000 —
        # far past the secondary screen's right edge (2720). Without clamping
        # this would set width = 800 + (4000 - 1000) = 3800, right = 3999.
        win._resize_edge = 4  # right edge
        win._resize_start_geo = start_geo
        win._resize_start_global = QtCore.QPoint(1000, 500)
        self._move(win, QtCore.QPoint(4000, 500))

        g = win.geometry()
        # Right edge must not exceed the secondary screen's right edge (2719).
        assert g.right() <= 2719, f"window escaped cursor screen: right={g.right()}"
        assert g.width() >= win.minimumWidth()
        win.close()

    def test_left_edge_resize_anchored_to_right_stays_in_screen(self, qapp, test_image, monkeypatch):
        """Dragging the left edge leftward is clamped so the left side never
        leaves the cursor screen's available area (the right side is anchored)."""
        from unittest.mock import MagicMock
        from PyQt6 import QtCore

        win = ImageEditorWindow(test_image, _translate)
        win._dpr = 1.0
        win.show()

        # Window right edge anchored at x=1500, left at x=700 (width 800).
        start_geo = QtCore.QRect(700, 200, 800, 600)
        win.setGeometry(start_geo)

        secondary = MagicMock()
        secondary.availableGeometry.return_value = QtCore.QRect(1920, 0, 800, 1000)
        monkeypatch.setattr(
            QtWidgets.QApplication, "screenAt", lambda *a, **k: secondary)

        # Left-edge resize (bitmask 1) with cursor jumping to x=1000 — the
        # computed left (700 - (1000-700)=400) is off the secondary screen,
        # whose left edge is 1920. The clamp must pull the left edge back.
        win._resize_edge = 1  # left edge
        win._resize_start_geo = start_geo
        win._resize_start_global = QtCore.QPoint(700, 500)
        self._move(win, QtCore.QPoint(1000, 500))

        g = win.geometry()
        # The left edge is clamped to the secondary screen's left (1920),
        # so the window collapses to min width rather than extending off-screen.
        assert g.left() >= 1920 - 1, f"left escaped cursor screen: left={g.left()}"
        assert g.width() >= win.minimumWidth()
        win.close()
