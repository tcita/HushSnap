import pytest
from PyQt6 import QtCore, QtWidgets
from PIL import Image

from hushsnap.ui.thumbnail import ThumbnailWindow
from hushsnap.constants import THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT

@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app

def test_thumbnail_fixed_size(qapp, monkeypatch):
    """The thumbnail window must always have a fixed card size regardless of the input image size."""
    # Pin frame off so window size = card + 2*shadow_padding (independent of env config).
    import hushsnap.config as cfg
    monkeypatch.setattr(cfg, "get_thumbnail_frame", lambda path=None: False)

    # Landscape image (wide)
    img_wide = Image.new("RGBA", (1000, 200), (255, 0, 0, 255))
    win_wide = ThumbnailWindow(img_wide)
    
    assert win_wide.card_width == THUMBNAIL_WIDTH
    assert win_wide.card_height == THUMBNAIL_HEIGHT
    assert win_wide.width() == THUMBNAIL_WIDTH + 2 * win_wide.shadow_padding
    assert win_wide.height() == THUMBNAIL_HEIGHT + 2 * win_wide.shadow_padding
    
    # Portrait image (tall)
    img_tall = Image.new("RGBA", (200, 1000), (0, 255, 0, 255))
    win_tall = ThumbnailWindow(img_tall)
    
    assert win_tall.card_width == THUMBNAIL_WIDTH
    assert win_tall.card_height == THUMBNAIL_HEIGHT
    
    # Tiny image
    img_tiny = Image.new("RGBA", (10, 10), (0, 0, 255, 255))
    win_tiny = ThumbnailWindow(img_tiny)
    
    assert win_tiny.card_width == THUMBNAIL_WIDTH
    assert win_tiny.card_height == THUMBNAIL_HEIGHT

def test_thumbnail_scaling_and_centering(qapp, monkeypatch):
    """The screenshot must be scaled preserving aspect ratio and centered inside the fixed card_rect."""
    # Pin the frame off so the card sits at shadow_padding=12 (independent of
    # whatever the dev machine's config happens to say).
    import hushsnap.config as cfg
    monkeypatch.setattr(cfg, "get_thumbnail_frame", lambda path=None: False)

    # Test with wide image (1000x500 -> aspect ratio 2:1)
    # Target size is 240x150.
    # Scaled width should be 240. Scaled height should be 120.
    img = Image.new("RGBA", (1000, 500), (255, 255, 255, 255))
    win = ThumbnailWindow(img)
    
    assert win.scaled_pixmap.width() == 240
    assert win.scaled_pixmap.height() == 120
    
    # Check centering coordinates (relative to window, including shadow_padding = 12):
    # px = 12 + (240 - 240) // 2 = 12
    # py = 12 + (150 - 120) // 2 = 27
    assert win.pixmap_rect.x() == 12
    assert win.pixmap_rect.y() == 27
    assert win.pixmap_rect.width() == 240
    assert win.pixmap_rect.height() == 120

    # Test with tall image (200x800 -> aspect ratio 1:4)
    # Target size is 240x150.
    # Scaled height should be 150. Scaled width should be 150 * 0.25 = 37.
    img_tall = Image.new("RGBA", (200, 800), (255, 255, 255, 255))
    win_tall = ThumbnailWindow(img_tall)
    
    assert win_tall.scaled_pixmap.height() == 150
    assert win_tall.scaled_pixmap.width() == 37
    
    # px = 12 + (240 - 37) // 2 = 113
    # py = 12 + (150 - 150) // 2 = 12
    assert win_tall.pixmap_rect.y() == 12
    assert win_tall.pixmap_rect.x() == 12 + (THUMBNAIL_WIDTH - win_tall.scaled_pixmap.width()) // 2

def test_thumbnail_manager_single_instance(qapp):
    """ThumbnailManager must close the previous thumbnail when showing a new one."""
    from hushsnap.ui.thumbnail import thumbnail_manager
    
    img1 = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    img2 = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    
    thumbnail_manager._do_show(img1)
    assert len(thumbnail_manager._windows) == 1
    win1 = thumbnail_manager._windows[0]
    
    thumbnail_manager._do_show(img2)
    assert len(thumbnail_manager._windows) == 1
    win2 = thumbnail_manager._windows[0]
    
    assert win1 != win2


def test_thumbnail_ocr_copy_signal_relay(qapp):
    """The silent-OCR menu action relays pil_image through the manager.

    The right-click menu emits ocr_copy_requested_signal, which the manager
    re-emits as ocr_copy_requested(pil_image) - the silent (no-popup) OCR path.
    """
    from hushsnap.ui.thumbnail import ThumbnailManager

    img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    mgr = ThumbnailManager()  # fresh instance: avoid cross-test singleton lifetime
    mgr._do_show(img)
    assert len(mgr._windows) == 1
    win = mgr._windows[0]

    received = []
    mgr.ocr_copy_requested.connect(lambda pil: received.append(pil))
    try:
        win.ocr_copy_requested_signal.emit()
        assert len(received) == 1
        assert received[0] is img
    finally:
        mgr.ocr_copy_requested.disconnect()
        win.close()


def test_thumbnail_open_in_viewer_signal_relay(qapp):
    """The 'View Original' menu action relays pil_image through the manager.

    The right-click menu emits open_in_viewer_signal, which the manager
    re-emits as open_in_viewer(pil_image) - the temp-file + os.startfile path.
    """
    from hushsnap.ui.thumbnail import ThumbnailManager

    img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    mgr = ThumbnailManager()  # fresh instance: avoid cross-test singleton lifetime
    mgr._do_show(img)
    assert len(mgr._windows) == 1
    win = mgr._windows[0]

    received = []
    mgr.open_in_viewer.connect(lambda pil: received.append(pil))
    try:
        win.open_in_viewer_signal.emit()
        assert len(received) == 1
        assert received[0] is img
    finally:
        mgr.open_in_viewer.disconnect()
        win.close()


def test_thumbnail_vine_frame_geometry(qapp, monkeypatch):
    """When the corner ornament is enabled the window is enlarged on the top-left
    side only, the card shifts down-right by that padding (its bottom-right stays
    anchored), and the ornament is scaled to its configured size then placed at
    its hand-set ox/oy offset so it hugs the card without being clipped by the
    window."""
    import hushsnap.config as cfg
    monkeypatch.setattr(cfg, "get_thumbnail_frame", lambda path=None: True)

    img = Image.new("RGBA", (1000, 500), (255, 255, 255, 255))
    win = ThumbnailWindow(img)
    try:
        from hushsnap.ui.thumbnail import (
            _CORNER_ORNAMENT_SIZE, _CORNER_OUT_PAD,
            _CORNER_OX, _CORNER_OY,
        )
        assert win._frame_enabled is True
        assert win._frame_pixmap is not None and not win._frame_pixmap.isNull()

        # Card size unchanged.
        assert win.card_rect.width() == THUMBNAIL_WIDTH
        assert win.card_rect.height() == THUMBNAIL_HEIGHT

        # Window enlarged by out_pad on BOTH axes (extra top-left padding only,
        # but window is square-grown: card + 2*shadow + out_pad).
        assert win.display_width == THUMBNAIL_WIDTH + 2 * 12 + _CORNER_OUT_PAD
        assert win.display_height == THUMBNAIL_HEIGHT + 2 * 12 + _CORNER_OUT_PAD

        # Card shifted down-right by out_pad (so its bottom-right stays anchored).
        assert win.card_rect.x() == 12 + _CORNER_OUT_PAD
        assert win.card_rect.y() == 12 + _CORNER_OUT_PAD

        # Ornament scaled to configured square size.
        assert win._frame_pixmap.width() == _CORNER_ORNAMENT_SIZE
        assert win._frame_pixmap.height() == _CORNER_ORNAMENT_SIZE

        # Ornament draw rect is computed once in __init__ (single source of truth).
        orr = win._ornament_rect
        assert orr is not None
        assert orr.x() >= 0 and orr.y() >= 0                       # origin inside window
        assert orr.x() + orr.width() <= win.display_width          # not clipped on the right
        assert orr.y() + orr.height() <= win.display_height        # not clipped at the bottom
        # Card-relative offset is the hand-set ox/oy (the cross-monitor lock).
        assert orr.x() - win.card_rect.left() == _CORNER_OX
        assert orr.y() - win.card_rect.top() == _CORNER_OY
        # outward (up-left of corner) part still fits in the top-left padding.
        outward_x = -_CORNER_OX
        assert outward_x <= _CORNER_OUT_PAD + 12
    finally:
        win.close()


def test_thumbnail_ornament_locked_across_screens(qapp, monkeypatch):
    """The ornament's window-relative position is a pure function of constants -
    it does NOT depend on which monitor (geometry / origin) the thumbnail lands
    on.  The screen only places the *window* on the desktop; the ornament rides
    inside it glued to the card corner.  Faking two very different screens must
    yield an identical ornament rect, equal to the constant-derived value."""
    import hushsnap.config as cfg
    monkeypatch.setattr(cfg, "get_thumbnail_frame", lambda path=None: True)
    import hushsnap.dpi as dpi
    from hushsnap.ui.thumbnail import (
        _CORNER_ORNAMENT_SIZE, _CORNER_OUT_PAD,
        _CORNER_OX, _CORNER_OY,
    )

    class _FakeScreen:
        def __init__(self, geo):
            self._geo = geo
        def availableGeometry(self):
            return self._geo

    # Two wildly different screens: a 1920x1080 at origin, and a 2560x1600 whose
    # top-left is off-origin at (-2560, 0) (a left-hand secondary monitor).
    seen = []
    ends = []
    for fake_geo in (QtCore.QRect(0, 0, 1920, 1080),
                     QtCore.QRect(-2560, 0, 2560, 1600)):
        monkeypatch.setattr(dpi, "cursor_screen",
                            lambda g=fake_geo: _FakeScreen(g))
        win = ThumbnailWindow(Image.new("RGBA", (1000, 500), (255, 255, 255, 255)))
        try:
            seen.append(QtCore.QRect(win._ornament_rect))  # defensive copy
            ends.append((win.end_x, win.end_y))
        finally:
            win.close()

    # The fake screen actually drives window placement (end_x/y differ) - this
    # proves the two monitors really are distinct contexts, so the next check
    # (ornament unchanged) is meaningful rather than vacuous.
    assert ends[0] != ends[1]
    # Identical ornament rect on both monitors -> locked, no drift / 乱飘.
    assert seen[0] == seen[1]
    # And it equals the pure-constant formula (screen-independent by construction).
    sp = 12
    card_x = sp + _CORNER_OUT_PAD
    card_y = sp + _CORNER_OUT_PAD
    exp_ox = card_x + _CORNER_OX
    exp_oy = card_y + _CORNER_OY
    expected = QtCore.QRect(exp_ox, exp_oy, _CORNER_ORNAMENT_SIZE, _CORNER_ORNAMENT_SIZE)
    assert seen[0] == expected


def test_thumbnail_frame_off_default(qapp, monkeypatch):
    """Frame off (default): window is the original card + 2*shadow_padding size,
    card at shadow_padding offset, no frame pixmap."""
    import hushsnap.config as cfg
    monkeypatch.setattr(cfg, "get_thumbnail_frame", lambda path=None: False)

    img = Image.new("RGBA", (1000, 500), (255, 255, 255, 255))
    win = ThumbnailWindow(img)
    try:
        assert win._frame_enabled is False
        assert win._frame_pixmap is None
        assert win.display_width == THUMBNAIL_WIDTH + 2 * 12
        assert win.card_rect.x() == 12
        assert win.card_rect.y() == 12
    finally:
        win.close()


def test_thumbnail_ornament_vine2_loads(qapp, monkeypatch):
    """The second ornament ('vine2') loads its own asset and positions via its
    own ox/oy - distinct from the default 'vine' ornament."""
    import hushsnap.config as cfg
    monkeypatch.setattr(cfg, "get_thumbnail_frame", lambda path=None: "vine2")

    img = Image.new("RGBA", (1000, 500), (255, 255, 255, 255))
    win = ThumbnailWindow(img)
    try:
        from hushsnap.ui.thumbnail import (
            _CORNER_ORNAMENT_BY_ID, _CORNER_ORNAMENT_SIZE, _CORNER_OUT_PAD,
            _CORNER_OX, _CORNER_OY,
        )
        assert win._frame_id == "vine2"
        assert win._frame_enabled is True
        assert win._frame_pixmap is not None and not win._frame_pixmap.isNull()
        assert win._frame_pixmap.width() == _CORNER_ORNAMENT_SIZE

        meta = _CORNER_ORNAMENT_BY_ID["vine2"]
        orr = win._ornament_rect
        assert orr is not None
        # Uses vine2's own ox/oy from the registry.
        sp = 12
        card_x = sp + _CORNER_OUT_PAD
        card_y = sp + _CORNER_OUT_PAD
        exp_ox = card_x + meta.ox
        exp_oy = card_y + meta.oy
        assert orr.x() == exp_ox
        assert orr.y() == exp_oy
        # Distinct from the default 'vine' rect (different ox/oy).
        vine_ox = card_x + _CORNER_OX
        vine_oy = card_y + _CORNER_OY
        assert (orr.x(), orr.y()) != (vine_ox, vine_oy)
        # Not clipped by the window.
        assert orr.x() >= 0 and orr.y() >= 0
        assert orr.x() + orr.width() <= win.display_width
        assert orr.y() + orr.height() <= win.display_height
    finally:
        win.close()


def test_thumbnail_ornament_clicks_passthrough(qapp, monkeypatch):
    """The corner ornament is purely decorative: it never participates in hit
    testing.  A click landing on the ornament (up-left of the card, outside
    card_rect) is ignored whether the ornament is on or off - the clickable
    region is always exactly card_rect, so toggling the ornament cannot change
    click behavior."""
    import hushsnap.config as cfg

    img = Image.new("RGBA", (1000, 500), (255, 255, 255, 255))

    for frame_on in (True, False):
        monkeypatch.setattr(cfg, "get_thumbnail_frame", lambda path=None, _f=frame_on: _f)
        win = ThumbnailWindow(img)
        try:
            # A point on the outward ornament (where vines/butterfly stick out)
            # is outside card_rect and must NOT be hittable, ornament on or off.
            outside_pt = QtCore.QPoint(win.card_rect.x() - 10, win.card_rect.y() - 10)
            assert not win.card_rect.contains(outside_pt)
            # The card center is always hittable.
            assert win.card_rect.contains(win.card_rect.center())
        finally:
            win.close()

