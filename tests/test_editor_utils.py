"""Tests for editor utility functions and widgets."""

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


# ── _default_annotation_font ──────────────────────────────────────────

def test_default_font_returns_lang_match_when_installed(qapp, monkeypatch):
    """When the UI language's preferred font is installed, return it."""
    from hushsnap.ui.editor.utils import _default_annotation_font

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Microsoft YaHei", "Segoe UI", "Consolas"],
    )
    assert _default_annotation_font("zh") == "Microsoft YaHei"


def test_default_font_falls_back_to_general_font(qapp, monkeypatch):
    """When the preferred font isn't installed, fall back to GeneralFont."""
    from hushsnap.ui.editor.utils import _default_annotation_font

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Segoe UI", "Consolas"],
    )
    # zh → Microsoft YaHei not installed → fallback to GeneralFont
    monkeypatch.setattr(
        QtGui.QFontDatabase, "systemFont",
        lambda _sf: QtGui.QFont("Segoe UI"),
    )
    assert _default_annotation_font("zh") == "Segoe UI"


def test_default_font_falls_back_to_empty_on_none_general(qapp, monkeypatch):
    """When GeneralFont returns an empty family, return '' (Qt default)."""
    from hushsnap.ui.editor.utils import _default_annotation_font

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Consolas"],
    )
    # GeneralFont returns a font whose family() is "".
    # QFont() defaults to the application font on some platforms;
    # explicitly set an empty family to exercise the fallback.
    empty_font = QtGui.QFont()
    empty_font.setFamily("")
    monkeypatch.setattr(
        QtGui.QFontDatabase, "systemFont",
        lambda _sf: empty_font,
    )
    assert _default_annotation_font("zh") == ""


def test_default_font_unknown_lang_falls_back_to_general(qapp, monkeypatch):
    """An unsupported language code skips tier 1, uses GeneralFont."""
    from hushsnap.ui.editor.utils import _default_annotation_font

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Segoe UI"],
    )
    monkeypatch.setattr(
        QtGui.QFontDatabase, "systemFont",
        lambda _sf: QtGui.QFont("Segoe UI"),
    )
    assert _default_annotation_font("fr") == "Segoe UI"


def test_default_font_ja_returns_yu_gothic_when_installed(qapp, monkeypatch):
    """Japanese UI → Yu Gothic UI when installed."""
    from hushsnap.ui.editor.utils import _default_annotation_font

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Yu Gothic UI", "Segoe UI"],
    )
    assert _default_annotation_font("ja") == "Yu Gothic UI"


def test_default_font_en_has_no_lang_pref_uses_general(qapp, monkeypatch):
    """English has no entry in _LANG_FONTS → straight to GeneralFont."""
    from hushsnap.ui.editor.utils import _default_annotation_font

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Segoe UI"],
    )
    monkeypatch.setattr(
        QtGui.QFontDatabase, "systemFont",
        lambda _sf: QtGui.QFont("Segoe UI"),
    )
    assert _default_annotation_font("en") == "Segoe UI"


# ── _CuratedFontComboBox ──────────────────────────────────────────────

def test_curated_font_combo_always_has_segoe_and_consolas(qapp):
    """Segoe UI and Consolas are always present (not probed)."""
    from hushsnap.ui.editor.widgets.controls import _CuratedFontComboBox

    combo = _CuratedFontComboBox()
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "Segoe UI" in items
    assert "Consolas" in items


def test_curated_font_combo_shows_installed_probed(qapp, monkeypatch):
    """Probed families that are installed appear in the list."""
    from hushsnap.ui.editor.widgets.controls import _CuratedFontComboBox

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Arial", "Microsoft YaHei", "Segoe UI", "Consolas"],
    )
    combo = _CuratedFontComboBox()
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "Arial" in items
    assert "Microsoft YaHei" in items
    # Times New Roman not installed → absent
    assert "Times New Roman" not in items


def test_curated_font_combo_omits_not_installed_probed(qapp, monkeypatch):
    """Probed families that aren't on the system are silently omitted."""
    from hushsnap.ui.editor.widgets.controls import _CuratedFontComboBox

    monkeypatch.setattr(
        QtGui.QFontDatabase, "families",
        lambda: ["Segoe UI", "Consolas"],
    )
    combo = _CuratedFontComboBox()
    items = [combo.itemText(i) for i in range(combo.count())]
    # Only the "always" families appear
    assert items == ["Segoe UI", "Consolas"]


def test_curated_font_combo_items_have_font_role(qapp):
    """Each item has a FontRole set to its own family at 11pt."""
    from hushsnap.ui.editor.widgets.controls import _CuratedFontComboBox

    combo = _CuratedFontComboBox()
    for i in range(combo.count()):
        family = combo.itemText(i)
        font = combo.itemData(i, QtCore.Qt.ItemDataRole.FontRole)
        assert isinstance(font, QtGui.QFont)
        assert font.family() == family
        assert font.pointSize() == 11


def test_curated_font_combo_show_popup_no_crash(qapp):
    """showPopup() should run without error (applies frameless styling)."""
    from hushsnap.ui.editor.widgets.controls import _CuratedFontComboBox

    combo = _CuratedFontComboBox()
    combo.show()
    qapp.processEvents()
    # showPopup triggers the frameless-popup treatment; verify it doesn't crash
    combo.showPopup()
    qapp.processEvents()
    combo.hide()
