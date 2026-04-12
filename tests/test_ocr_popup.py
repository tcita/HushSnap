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
    }
    return table[key].format(**kwargs)


def test_ocr_popup_is_editable(qapp):
    popup = OcrPopup(_translate)

    assert not popup.text_edit.isReadOnly()


def test_ocr_popup_copy_button_copies_current_text(qapp):
    popup = OcrPopup(_translate)
    popup.show_text("original")
    popup.text_edit.setPlainText("edited text")

    popup.copy_btn.click()

    assert QtWidgets.QApplication.clipboard().text() == "edited text"


def test_ocr_popup_updates_copy_button_text_on_show(qapp):
    popup = OcrPopup(_translate)

    popup.show_text("hello", lang="en-US")

    assert popup.copy_btn.text() == "Copy"
    assert popup.title_label.text() == "OCR Text"
