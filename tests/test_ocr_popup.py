import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtGui import QHoverEvent, QMouseEvent
from unittest.mock import patch

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
        "ocr_link_open_hint": "Ctrl + Click to open",
    }
    return table.get(key, key).format(**kwargs)


def test_ocr_popup_starts_editable(qapp):
    """After show_text the text is immediately editable."""
    popup = OcrPopup(_translate)
    popup.show_text("hello")

    assert not popup.text_edit.isReadOnly()
    assert popup.text_edit.toPlainText() == "hello"


def test_ocr_popup_text_is_directly_editable(qapp):
    """User can edit text directly without entering any mode."""
    popup = OcrPopup(_translate)
    popup.show_text("original")

    # Just type directly — no button needed
    popup.text_edit.setPlainText("edited text")
    assert popup.get_plain_text() == "edited text"
    assert popup.text_edit.toPlainText() == "edited text"


def test_ocr_popup_viewport_has_hover_enabled(qapp):
    """The text viewport must have WA_Hover so HoverMove is delivered to it.

    Without this, the URL link-hover feedback (Ctrl+Click tooltip + hand
    cursor) wired into the eventFilter never fires in real use — the viewport
    is the widget under the cursor but received no hover events.  Regression
    guard: do not remove this attribute.
    """
    popup = OcrPopup(_translate)
    popup.show_text("https://example.com")
    assert popup.text_edit.viewport().testAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)


def test_ocr_popup_footer_has_only_copy_button(qapp):
    """The footer should only contain the copy button (no edit/update/cancel buttons or mode)."""
    popup = OcrPopup(_translate)
    popup.show_text("hello")

    assert not popup.copy_btn.isHidden()
    assert not hasattr(popup, "edit_btn")
    assert not hasattr(popup, "update_btn")
    assert not hasattr(popup, "cancel_btn")
    assert not hasattr(popup, "_editing")


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


def test_ocr_popup_edge_detection(qapp):
    """Test that the popup correctly identifies resize edges."""
    popup = OcrPopup(_translate)
    popup.resize(400, 400)
    
    # RESIZE_HIT is 28 (matches OUTER_MARGIN)

    # Top-left corner
    assert popup._get_edge(QtCore.QPoint(5, 5)) == (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge)
    # Right edge
    assert popup._get_edge(QtCore.QPoint(390, 200)) == QtCore.Qt.Edge.RightEdge
    # Bottom-right corner
    assert popup._get_edge(QtCore.QPoint(390, 390)) == (QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.RightEdge)
    # Center (no edge)
    assert popup._get_edge(QtCore.QPoint(200, 200)) == QtCore.Qt.Edge(0)
    # Card corner at 18,18 still falls within the 28px hit ring
    assert popup._get_edge(QtCore.QPoint(18, 18)) == (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge)


# ── URL link hover / Ctrl+Click ────────────────────────────────────────────
# The first line of OCR text sits inside the corner resize band (CORNER_HIT),
# so URL hover feedback must take priority over the edge-resize cursor logic —
# otherwise a link at the top-left shows a resize cursor and no tooltip.

def _url_center(popup, url_text, char_offset=5):
    """Return a viewport-space QPointF sitting over the URL text."""
    te = popup.text_edit
    idx = te.toPlainText().find(url_text)
    assert idx >= 0, f"{url_text!r} not in text"
    cursor = te.textCursor()
    cursor.setPosition(idx + char_offset)
    return QtCore.QPointF(popup.text_edit.cursorRect(cursor).center())


def _send_hover(popup, pos, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
    vp = popup.text_edit.viewport()
    he = QHoverEvent(QtCore.QEvent.Type.HoverMove, QtCore.QPointF(-1, -1), pos, modifiers)
    QtWidgets.QApplication.sendEvent(vp, he)
    QtWidgets.QApplication.processEvents()


def test_url_hover_shows_tooltip_even_in_corner_band(qapp, monkeypatch):
    """A URL under the pointer still shows the Ctrl+Click tooltip and an IBeam
    cursor even when the pointer is inside the corner resize band — the edge
    logic must not swallow URL hover feedback."""
    popup = OcrPopup(_translate)
    popup.show_text("https://www.deepseek.com here")
    popup.resize(420, 200)
    popup.show()
    qapp.processEvents()

    # Force the pointer to be reported as inside the top-left corner band so
    # we exercise the URL-priority-over-resize branch regardless of where the
    # window actually mapped on the test display.
    monkeypatch.setattr(
        popup, "_get_edge",
        lambda pos: QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge,
    )

    pos = _url_center(popup, "https://www.deepseek.com")
    _send_hover(popup, pos)
    assert QtWidgets.QToolTip.text() == "Ctrl + Click to open"
    assert popup.text_edit.viewport().cursor().shape() == QtCore.Qt.CursorShape.IBeamCursor


def test_url_hover_with_ctrl_uses_hand_cursor(qapp, monkeypatch):
    """With Ctrl held, hovering a URL switches to the pointing-hand cursor,
    even inside the corner resize band."""
    popup = OcrPopup(_translate)
    popup.show_text("https://www.deepseek.com here")
    popup.resize(420, 200)
    popup.show()
    qapp.processEvents()

    monkeypatch.setattr(
        popup, "_get_edge",
        lambda pos: QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge,
    )

    pos = _url_center(popup, "https://www.deepseek.com")
    _send_hover(popup, pos, modifiers=QtCore.Qt.KeyboardModifier.ControlModifier)
    assert popup.text_edit.viewport().cursor().shape() == QtCore.Qt.CursorShape.PointingHandCursor


def test_ctrl_click_on_url_in_corner_band_opens_it(qapp, monkeypatch):
    """Ctrl+Click on a URL inside the corner band opens the full URL in the
    browser (priority over edge resize)."""
    popup = OcrPopup(_translate)
    popup.show_text("https://www.deepseek.com here")
    popup.resize(420, 200)
    popup.show()
    qapp.processEvents()

    monkeypatch.setattr(
        popup, "_get_edge",
        lambda pos: QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge,
    )

    pos = _url_center(popup, "https://www.deepseek.com")
    opened = []
    me = QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        pos,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.ControlModifier,
    )
    with patch("PyQt6.QtGui.QDesktopServices.openUrl", side_effect=lambda q: opened.append(q.toString())):
        QtWidgets.QApplication.sendEvent(popup.text_edit.viewport(), me)
        qapp.processEvents()
    assert opened == ["https://www.deepseek.com"]


def test_plain_click_on_url_does_not_open(qapp):
    """A click without Ctrl on a URL does not open the browser."""
    popup = OcrPopup(_translate)
    popup.show_text("https://www.deepseek.com here")
    popup.resize(420, 200)
    popup.show()
    qapp.processEvents()

    pos = _url_center(popup, "https://www.deepseek.com")
    opened = []
    me = QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        pos,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    with patch("PyQt6.QtGui.QDesktopServices.openUrl", side_effect=lambda q: opened.append(q.toString())):
        QtWidgets.QApplication.sendEvent(popup.text_edit.viewport(), me)
        qapp.processEvents()
    assert opened == []
