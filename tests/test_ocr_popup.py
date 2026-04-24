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
        "ocr_lang_english": "Lang: EN",
        "ocr_lang_chinese_simplified": "简体中文",
        "ocr_lang_chinese_traditional": "繁體中文",
        "ocr_lang_selector_tooltip": "Select OCR language",
        "ocr_lang_missing_switch_btn": "Switch to {available_lang}",
        "ocr_lang_missing_open_settings_btn": "Open language settings",
        "ocr_lang_installed_fallback": "Installed language",
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


def test_ocr_popup_maps_traditional_chinese_variants_to_traditional_option(qapp):
    popup = OcrPopup(_translate)

    popup.show_text("hello", lang="zh-TW")

    assert popup.lang_combo.currentData() == "zh-TW"


def test_ocr_popup_maps_simplified_chinese_variants_to_simplified_option(qapp):
    popup = OcrPopup(_translate)

    popup.show_text("hello", lang="zh-SG")

    assert popup.lang_combo.currentData() == "zh-CN"


def test_ocr_popup_can_show_and_hide_language_notice(qapp):
    popup = OcrPopup(_translate)

    popup.show_language_notice("Missing OCR language pack", available_lang="zh-CN")

    assert popup.notice_frame.isHidden() is False
    assert popup.notice_switch_btn.isEnabled() is True

    popup.hide_language_notice()

    assert popup.notice_frame.isHidden() is True
