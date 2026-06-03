import pytest
from PyQt6 import QtCore, QtWidgets

from hushsnap.ui.ocr_popup import OcrPopup


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


def _translate(key, **kwargs):
    table = {
        "ocr_popup_title": "OCR Text",
        "ocr_copy_btn": "Copy",
        "ocr_copied": "✓ Copied!",
        "ocr_recapture_tooltip": "Capture and OCR",
        "ocr_status_done": "Recognition complete",
        "ocr_status_paste_hint": "Ctrl+V to paste",
        "ocr_editable_hint": "Text is editable",
        "ocr_pin_btn": "Pin",
        "ocr_unpin_btn": "Unpin",
        "close_btn": "Close",
        "ocr_edit_btn": "Edit",
        "ocr_update_btn": "Update",
        "ocr_cancel_btn": "Cancel",
    }
    return table.get(key, key).format(**kwargs)


def test_ocr_popup_text_edit_is_not_read_only(qapp):
    """The underlying text edit widget must be editable (not read-only)."""
    popup = OcrPopup(_translate)

    assert not popup.text_edit.isReadOnly()


def test_ocr_popup_starts_in_read_only_display_mode(qapp):
    """After show_text the label is visible and text_edit is hidden."""
    popup = OcrPopup(_translate)
    popup.show_text("hello")

    assert not popup.text_label.isHidden()
    assert popup.text_edit.isHidden()
    assert not popup._editing


def test_ocr_popup_edit_btn_enters_edit_mode(qapp):
    """Clicking the pencil button switches to editable text_edit."""
    popup = OcrPopup(_translate)
    popup.show_text("original")

    # Click pencil to enter edit mode
    popup.edit_btn.click()

    assert popup._editing
    assert popup.text_label.isHidden()
    assert not popup.text_edit.isHidden()
    assert popup.text_edit.toPlainText() == "original"


def test_ocr_popup_edit_btn_exits_edit_mode_and_saves(qapp):
    """Update button saves edits back to the label."""
    popup = OcrPopup(_translate)
    popup.show_text("original")

    # Enter edit mode
    popup.edit_btn.click()
    popup.text_edit.setPlainText("edited text")
    # Exit edit mode via Update button
    popup.update_btn.click()

    assert not popup._editing
    assert not popup.text_label.isHidden()
    assert popup.text_edit.isHidden()
    assert popup.text_label.text() == "edited text"


def test_ocr_popup_cancel_btn_discards_edits(qapp):
    """Cancel button discards edits and exits edit mode."""
    popup = OcrPopup(_translate)
    popup.show_text("original")

    popup.edit_btn.click()
    popup.text_edit.setPlainText("discarded text")
    popup.cancel_btn.click()

    assert not popup._editing
    assert popup.text_label.text() == "original"


def test_ocr_popup_edit_mode_swaps_button_groups(qapp):
    """Read mode shows Copy+Edit; edit mode shows Update+Cancel."""
    popup = OcrPopup(_translate)
    popup.show_text("hello")

    # Read mode
    assert not popup.copy_btn.isHidden()
    assert not popup.edit_btn.isHidden()
    assert popup.update_btn.isHidden()
    assert popup.cancel_btn.isHidden()

    # Enter edit mode
    popup.edit_btn.click()

    assert popup.copy_btn.isHidden()
    assert popup.edit_btn.isHidden()
    assert not popup.update_btn.isHidden()
    assert not popup.cancel_btn.isHidden()


def test_ocr_popup_copy_button_copies_current_text(qapp):
    popup = OcrPopup(_translate)
    popup.show_text("original")

    # Copy from read-only label
    popup.copy_btn.click()
    assert QtWidgets.QApplication.clipboard().text() == "original"

    # Enter edit mode, modify, then copy
    popup.edit_btn.click()
    popup.text_edit.setPlainText("edited text")
    popup.copy_btn.click()
    assert QtWidgets.QApplication.clipboard().text() == "edited text"


def test_ocr_popup_updates_copy_button_text_on_show(qapp):
    popup = OcrPopup(_translate)

    popup.show_text("hello")

    assert popup.copy_btn.toolTip() == "Copy"
