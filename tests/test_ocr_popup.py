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
    }
    return table.get(key, key).format(**kwargs)


def test_ocr_popup_text_is_editable_by_default(qapp):
    """The text edit is directly editable — no edit mode toggle needed."""
    popup = OcrPopup(_translate)
    popup.show_text("hello")

    assert not popup.text_edit.isReadOnly()
    assert popup.text_edit.toPlainText() == "hello"


def test_ocr_popup_starts_editable(qapp):
    """After show_text the text is immediately editable."""
    popup = OcrPopup(_translate)
    popup.show_text("hello")

    assert not popup.text_edit.isReadOnly()
    assert popup.text_edit.toPlainText() == "hello"


def test_ocr_popup_has_no_edit_button(qapp):
    """The popup no longer has an edit button."""
    popup = OcrPopup(_translate)
    popup.show_text("original")

    assert not hasattr(popup, "edit_btn")
    assert not hasattr(popup, "update_btn")
    assert not hasattr(popup, "cancel_btn")
    assert not hasattr(popup, "_editing")


def test_ocr_popup_text_is_directly_editable(qapp):
    """User can edit text directly without entering any mode."""
    popup = OcrPopup(_translate)
    popup.show_text("original")

    # Just type directly — no button needed
    popup.text_edit.setPlainText("edited text")
    assert popup.get_plain_text() == "edited text"
    assert popup.text_edit.toPlainText() == "edited text"


def test_ocr_popup_footer_has_only_copy_button(qapp):
    """The footer should only contain the copy button (no edit/update/cancel)."""
    popup = OcrPopup(_translate)
    popup.show_text("hello")

    assert not popup.copy_btn.isHidden()
    assert not hasattr(popup, "edit_btn")
    assert not hasattr(popup, "update_btn")
    assert not hasattr(popup, "cancel_btn")


from unittest.mock import patch, MagicMock

def test_ocr_popup_copy_button_copies_current_text(qapp):
    with patch("PyQt6.QtWidgets.QApplication.clipboard") as mock_clipboard_func:
        mock_clipboard = MagicMock()
        mock_clipboard_func.return_value = mock_clipboard

        popup = OcrPopup(_translate)
        popup.show_text("original")

        # Copy from editable text
        popup.copy_btn.click()
        mock_clipboard.setText.assert_called_with("original")

        # Edit directly, then copy again
        popup.text_edit.setPlainText("edited text")
        popup.copy_btn.setEnabled(True)  # Animation disables it; re-enable for testing
        popup.copy_btn.click()
        mock_clipboard.setText.assert_called_with("edited text")


def test_ocr_popup_updates_copy_button_text_on_show(qapp):
    popup = OcrPopup(_translate)

    popup.show_text("hello")

    assert popup.copy_btn.toolTip() == "Copy"
