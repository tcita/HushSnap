import math
import os
import time
import logging
from collections import namedtuple

from PyQt6 import QtCore, QtGui, QtWidgets
from PIL import Image

from .styles import BRAND_GREEN, MODERN_MENU_STYLE
from ..constants import (
    THUMBNAIL_WIDTH,
    THUMBNAIL_HEIGHT,
    THUMBNAIL_MARGIN,
    THUMBNAIL_DISPLAY_MS,
    THUMBNAIL_ANIM_MS,
    THUMBNAIL_CORNER_RADIUS,
    THUMBNAIL_DRAG_OPACITY,
    THUMBNAIL_DRAG_SCALE,
)

logger = logging.getLogger(__name__)

# ── Layout / interaction constants ───────────────────────────────────────────
_DRAG_THRESHOLD_PX = 15
_SHADOW_PASSES = 9
_SHADOW_ALPHA_PER_PASS = 25
_ELEVATION_PASSES = 5
_ELEVATION_ALPHA_PER_PASS = 45
_COUNTDOWN_WARN_S = 2.0

# ── Decorative corner ornament (optional, opt-in via config 'thumbnail_frame') ─
# A transparent square PNG (ui/icons/corner_*.png) hung on the thumbnail card's
# top-left CORNER.  The ornament is scaled to a fixed 120x120 canvas, then its
# TOP-LEFT is nailed at (card TL + ox, card TL + oy).  ox/oy is a plain pixel
# offset measured by hand in scripts/ornament_placer.html (drag the PNG where
# you want it, read ox/oy off the tool) - NO alpha-centroid computation, which
# was unstable across different artwork.  Negative ox/oy => the ornament sticks
# out up-left of the card (like a clasp biting the corner); positive => inside.
# The window is enlarged on the TOP-LEFT side only to give outward vines room;
# the card's bottom-right stays anchored (thumbnail still hugs the screen's
# bottom-right corner, never off-screen).
#
# Available ornaments are registered below.  The id is stored in config
# 'thumbnail_frame' ("" = none).  ox/oy is per-asset because each ornament's
# intended bite point differs; values are hand-measured, not computed.
_CornerOrnament = namedtuple(
    "_CornerOrnament", "id filename ox oy"
)
_CORNER_ORNAMENTS = (
    _CornerOrnament("vine",       "corner_vine.png",       -19, -18),
    # Butterfly artwork by gustavorezende (openclipart, Public Domain) - see icons/ATTRIBUTION.md
    _CornerOrnament("butterfly",  "corner_butterfly.png",  -34, -28),
    _CornerOrnament("floral",     "corner_floral.png",     -19, -22),
)
_CORNER_ORNAMENT_BY_ID = {o.id: o for o in _CORNER_ORNAMENTS}
_CORNER_DEFAULT_ID = "vine"          # legacy bool True migrates to this ornament
_CORNER_ORNAMENT_SIZE = 120          # square canvas edge, px (restrained intrusion)
_CORNER_OUT_PAD = 36                 # extra window padding on top-left sides for outward vines
# Default-ornament constants (aliases of the "vine" entry above) - the named
# reference used by the ornament-rect math and by tests.  Other ornaments read
# their own ox/oy from _CORNER_ORNAMENT_BY_ID at draw time.
_vine_meta = _CORNER_ORNAMENT_BY_ID[_CORNER_DEFAULT_ID]
_CORNER_OX = _vine_meta.ox
_CORNER_OY = _vine_meta.oy

class ThumbnailWindow(QtWidgets.QWidget):
    """
    Floating thumbnail window with slide-in animation, auto-hide, 
    and drag-and-drop save functionality.
    """
    # Signals for local handling, Manager will relay these globally
    clicked_signal = QtCore.pyqtSignal()
    save_to_desktop_signal = QtCore.pyqtSignal()
    copy_image_signal = QtCore.pyqtSignal()
    pin_requested_signal = QtCore.pyqtSignal()
    edit_requested_signal = QtCore.pyqtSignal()
    open_in_viewer_signal = QtCore.pyqtSignal()

    def __init__(self, pil_image: Image.Image):
        super().__init__()
        self.pil_image = pil_image

        # 1. Convert PIL to QPixmap for display
        self.pixmap = self._pil_to_qpixmap(pil_image)
        
        # 2. Window configuration
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.WindowStaysOnTopHint |
            QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        # Receive mouse-move events with no button held so hover can follow the
        # cursor into/out of card_rect (the click region).  Without this Qt only
        # delivers mouseMoveEvent while a button is pressed, so the hover glow
        # could not be turned off when the cursor drifts out onto the ornament.
        self.setMouseTracking(True)
        
        # Shadow padding for custom drop shadow
        self.shadow_padding = 12

        # The card size is fixed
        self.card_width = THUMBNAIL_WIDTH
        self.card_height = THUMBNAIL_HEIGHT

        # Optional decorative corner ornament (config 'thumbnail_frame').
        # A square PNG hangs on the card's top-left CORNER: its top-left is nailed
        # at (card TL + ox, card TL + oy), so dense vines stick out up-left (outside
        # the card, like a clasp) and only the sparse tail restrains itself over the
        # screenshot.  ox/oy is hand-set per ornament (scripts/ornament_placer.html).
        # The window is enlarged on the TOP-LEFT side only to room the outward
        # vines; the card's bottom-right stays anchored so the thumbnail still hugs
        # the screen's bottom-right corner and never goes off-screen.  card_rect
        # remains the single hit-test target - the outward vines are not clickable.
        self._frame_id = self._read_selected_ornament()   # "" or a registry id
        self._frame_enabled = bool(self._frame_id)
        self._frame_pixmap = None
        # Extra top-left padding so outward vines have room.  0 when ornament off.
        self._corner_out_pad = 0
        if self._frame_id:
            self._frame_pixmap = self._build_corner_ornament(self._frame_id)
            if self._frame_pixmap is None:
                logger.warning("[FRAME] ornament %r enabled in config but asset unavailable; downgrading to no ornament", self._frame_id)
                self._frame_id = ""
                self._frame_enabled = False
            else:
                self._corner_out_pad = _CORNER_OUT_PAD
                logger.debug(
                    "[FRAME] on: ornament=%s %dx%d out_pad=%d (hangs on card TL corner)",
                    self._frame_id, self._frame_pixmap.width(), self._frame_pixmap.height(), self._corner_out_pad,
                )

        # Window size: card + shadow_padding on every side, PLUS extra top-left
        # padding when the ornament is on (vines stick out up-left only).
        self.display_width = self.card_width + 2 * self.shadow_padding + self._corner_out_pad
        self.display_height = self.card_height + 2 * self.shadow_padding + self._corner_out_pad
        self.setFixedSize(self.display_width, self.display_height)

        # Card sits at shadow_padding + out_pad from the top-left, so the extra
        # padding (and the outward vines) are above/left of the card.  The card's
        # bottom-right thus stays at the same window-relative spot as the no-ornament
        # case, keeping the screen-corner anchoring unchanged.
        card_x = self.shadow_padding + self._corner_out_pad
        card_y = self.shadow_padding + self._corner_out_pad
        self.card_rect = QtCore.QRect(card_x, card_y, self.card_width, self.card_height)
        logger.debug(
            "[FRAME] window=%dx%d card_rect=%s (ornament %s)",
            self.display_width, self.display_height,
            f"({card_x},{card_y},{self.card_width}x{self.card_height})",
            "on" if self._frame_enabled else "off",
        )

        # Ornament draw rect: computed ONCE here as the single source of truth so
        # paintEvent cannot drift from it.  It is a pure function of card_rect +
        # the per-ornament ox/oy offset - NO screen / DPI / resolution input - so
        # the ornament stays glued to the card corner on every monitor: the screen
        # only decides where the *window* sits on the desktop, and the ornament
        # rides inside it as a rigid part of the window.  ox/oy is the ornament
        # canvas's top-left relative to the card's top-left (logical px, hand-set).
        self._ornament_rect = None
        if self._frame_enabled and self._frame_pixmap is not None:
            fp = self._frame_pixmap
            meta = _CORNER_ORNAMENT_BY_ID[self._frame_id]
            ox = card_x + meta.ox
            oy = card_y + meta.oy
            self._ornament_rect = QtCore.QRect(ox, oy, fp.width(), fp.height())
            # Guard: the ornament must stay fully inside the window or it clips -
            # a visible form of misalignment.  An ox/oy/size/pad change that breaks
            # this is a regression; log it loudly instead of silently clipping.
            if (ox < 0 or oy < 0
                    or ox + fp.width() > self.display_width
                    or oy + fp.height() > self.display_height):
                logger.warning(
                    "[FRAME] ornament rect %s outside window %dx%d - will clip; "
                    "check ox/oy / _CORNER_ORNAMENT_SIZE / _CORNER_OUT_PAD",
                    self._ornament_rect, self.display_width, self.display_height,
                )

        # Scale original pixmap to fit inside the fixed card dimensions using KeepAspectRatio.
        self.scaled_pixmap = self.pixmap.scaled(
            self.card_width, self.card_height,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )

        # Center the scaled pixmap inside the card_rect
        pw = self.scaled_pixmap.width()
        ph = self.scaled_pixmap.height()
        px = self.card_rect.x() + (self.card_width - pw) // 2
        py = self.card_rect.y() + (self.card_height - ph) // 2
        self.pixmap_rect = QtCore.QRect(px, py, pw, ph)

        # 3. Blurred background: crop-to-fill → Gaussian blur → QPixmap
        self.blurred_bg = self._create_blurred_background(pil_image)

        # 3. Action Pill (Edit + Pin + Close)
        self.action_pill = QtWidgets.QFrame(self)
        self.action_pill.setObjectName("actionPill")
        self.action_pill.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.action_pill.setFixedSize(110, 28)

        pill_layout = QtWidgets.QHBoxLayout(self.action_pill)
        pill_layout.setContentsMargins(8, 0, 8, 0)
        pill_layout.setSpacing(4)

        self.edit_btn = QtWidgets.QPushButton(self.action_pill)
        self.edit_btn.setFixedSize(24, 24)
        self.edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.edit_btn.setToolTip("Edit Image")
        self.edit_btn.setIcon(self._make_edit_icon())
        self.edit_btn.setIconSize(QtCore.QSize(14, 14))
        self.edit_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self.edit_btn.clicked.connect(self.edit_requested_signal.emit)

        self.pin_btn = QtWidgets.QPushButton(self.action_pill)
        self.pin_btn.setFixedSize(24, 24)
        self.pin_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setToolTip("Pin to Screen")
        self.pin_btn.setIcon(self._make_pin_icon())
        self.pin_btn.setIconSize(QtCore.QSize(14, 14))
        self.pin_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self.pin_btn.clicked.connect(self.pin_requested_signal.emit)

        # Vertical separator
        sep = QtWidgets.QFrame(self.action_pill)
        sep.setFixedSize(1, 14)
        sep.setAutoFillBackground(False)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 40);")

        self.close_btn = QtWidgets.QPushButton(self.action_pill)
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("Close")
        self.close_btn.setIcon(self._make_close_icon())
        self.close_btn.setIconSize(QtCore.QSize(14, 14))
        self.close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self.close_btn.clicked.connect(self.close)

        # Event filters for pill hover effect
        self.edit_btn.installEventFilter(self)
        self.pin_btn.installEventFilter(self)
        self.close_btn.installEventFilter(self)
        self._pill_hover_timer = QtCore.QTimer(self)
        self._pill_hover_timer.setSingleShot(True)
        self._pill_hover_timer.timeout.connect(self._restore_pill_style)
        
        pill_layout.addWidget(self.edit_btn)
        pill_layout.addWidget(self.pin_btn)
        pill_layout.addSpacing(2)
        pill_layout.addWidget(sep)
        pill_layout.addSpacing(2)
        pill_layout.addWidget(self.close_btn)
        
        # Center the pill at the top of the card
        pill_x = self.card_rect.x() + (self.card_width - self.action_pill.width()) // 2
        self.action_pill.move(pill_x, self.card_rect.y() + 6)
        self.action_pill.hide()

        # 4. Position and Animation
        # Resolve the screen under the cursor in *physical* space so a
        # mixed-DPR dead zone (a high-DPR neighbour logically shorter than the
        # desktop box) doesn't make screenAt() return None and fall back to
        # the primary screen — which would place this thumbnail on the wrong
        # monitor when the capture ends at a secondary screen's bottom edge.
        from ..dpi import cursor_screen
        active_screen = cursor_screen() or QtWidgets.QApplication.primaryScreen()
        if active_screen is None:
            # No screen available (e.g. monitors disconnected / RDP session
            # switching). Fall back to a degenerate geometry so __init__ still
            # completes (animations/timers/drag-state must be initialized);
            # the thumbnail simply stays at the origin, off the user's view.
            screen = QtCore.QRect(0, 0, 0, 0)
        else:
            screen = active_screen.availableGeometry()
        # Position so the thumbnail *card's* bottom-right corner sits THUMBNAIL_MARGIN
        # in from the screen's bottom-right.  We anchor on the card (not the window):
        # card_off is the card's offset within the window.  Since the corner ornament
        # overlays the card without resizing the window, card_off == shadow_padding
        # always, so the card lands in the same spot whether the ornament is on/off.
        # Position so the thumbnail *card's* bottom-right corner sits THUMBNAIL_MARGIN
        # in from the screen's bottom-right - identical to the no-ornament case so
        # toggling the ornament never moves the card.  We anchor on the card's
        # bottom-right, not the window's: the ornament enlarges the window on the
        # TOP-LEFT only, so the card's BR is offset from the window's BR by exactly
        # shadow_padding (the right/bottom shadow band, which is unchanged).  Thus:
        #   window_BR = card_BR + shadow_padding
        #   card_BR_target = screen_BR - MARGIN
        #   end_xy (window top-left) = window_BR - display_size
        sp = self.shadow_padding
        self.end_x = screen.x() + screen.width() - THUMBNAIL_MARGIN + sp - self.display_width
        self.end_y = screen.y() + screen.height() - THUMBNAIL_MARGIN + sp - self.display_height
        # Slide-in start point. Previously this was ``screen.x() + screen.width()``
        # - the screen's right edge, which is also the *neighbour* screen's
        # left edge. The window was therefore born straddling the monitor
        # boundary, and Qt re-associated it between the two screens (different
        # DPRs => different logical coord spaces) as the slide animation pulled
        # it inward, producing the visible "pop to the wrong spot then snap
        # back" jitter on the first frames. Keeping the start point inside the
        # target screen (clamped so the full window fits) means the window
        # never crosses the boundary during the slide, so no screen
        # re-association and no jitter.
        slide = THUMBNAIL_MARGIN - sp
        if slide < 4:
            slide = 4
        self.start_x = min(self.end_x + slide,
                           screen.x() + screen.width() - self.display_width)

        self.move(self.start_x, self.end_y)

        # Slide-in animation
        self.pos_anim = QtCore.QPropertyAnimation(self, b"pos")
        self.pos_anim.setDuration(THUMBNAIL_ANIM_MS)
        self.pos_anim.setStartValue(QtCore.QPoint(self.start_x, self.end_y))
        self.pos_anim.setEndValue(QtCore.QPoint(self.end_x, self.end_y))
        self.pos_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        # Fade-in animation. The slide distance is intentionally short (the
        # start point is clamped inside the target screen to avoid the
        # cross-monitor jitter), so the slide alone no longer reads as
        # motion. Layering an opacity 0→1 fade-in on top restores the
        # "appears" feel without crossing the screen boundary. Starts at 0
        # so the window doesn't flash fully opaque on the first frame.
        self.setWindowOpacity(0.0)
        self.fade_in_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self.fade_in_anim.setDuration(THUMBNAIL_ANIM_MS)
        self.fade_in_anim.setStartValue(0.0)
        self.fade_in_anim.setEndValue(1.0)
        self.fade_in_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        # Fade-out animation
        self.fade_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self.fade_anim.setDuration(THUMBNAIL_ANIM_MS)
        self.fade_anim.setStartValue(1.0)
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.finished.connect(self.close)
        
        # 4. Timer
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_auto_dismiss)
        
        self._is_dragging = False
        self._drag_start_pos = None
        self._menu_active = False
        self._hovered = False
        self._loading = False
        self._loading_progress = 0.0
        self._loading_anim = None  # QVariantAnimation for pulsing bar
        self._pill_state = 'none'  # 'none' | 'edit' | 'pin' | 'close' — drives paintEvent

        # Ripple effect on click — 4 concentric wave rings from the press
        # point, each with a dark trough + bright crest for a wave feel.
        self._ripple_center = None       # QtCore.QPointF or None
        self._ripple_progress = 0.0       # master progress 0.0 → 1.0
        self._ripple_anim = None          # QVariantAnimation

        # Countdown progress bar — thin line at card bottom that shrinks
        # over the display duration, so the user always knows how much
        # time is left before the thumbnail auto-dismisses.
        self._countdown_deadline = None   # monotonic timestamp (seconds) or None
        self._countdown_total_s = 0.0     # total configured display time
        self._countdown_tick = QtCore.QTimer(self)
        self._countdown_tick.setInterval(50)  # ~20 fps — smooth enough
        self._countdown_tick.timeout.connect(self._tick_countdown)

    @staticmethod
    def _make_edit_icon():
        from .icon_utils import load_svg_icon
        return load_svg_icon("edit_brush", BRAND_GREEN, "#8ef0b6", size=24)

    @staticmethod
    def _make_pin_icon():
        from .icon_utils import load_svg_icon
        return load_svg_icon("pin_unlocked", BRAND_GREEN, "#8ef0b6", size=24)

    @staticmethod
    def _make_close_icon():
        from .icon_utils import load_svg_icon
        return load_svg_icon("close", "#ffffff", "#ff5c5c", size=24)

    @staticmethod
    def _read_selected_ornament():
        """Read the selected corner-ornament id from config ('thumbnail_frame').

        Returns "" (none) or a registry id such as "vine".  Normalizes legacy
        bool values (True -> default ornament, False/None -> "") so old configs
        and bool test stubs keep working; unknown string ids fall back to "".
        """
        try:
            from ..config import get_thumbnail_frame, get_config_path
            raw = get_thumbnail_frame(get_config_path())
        except Exception:
            logger.warning("[FRAME] failed to read 'thumbnail_frame' config; defaulting to off", exc_info=True)
            return ""
        if raw is True:
            return _CORNER_DEFAULT_ID
        if raw is False or raw is None:
            return ""
        if isinstance(raw, str):
            return raw if raw in _CORNER_ORNAMENT_BY_ID else ""
        return ""

    def _build_corner_ornament(self, ornament_id):
        """Load the named corner-ornament PNG and scale it to overlay the card's
        top-left.

        ornament_id selects an asset from _CORNER_ORNAMENTS.  The asset is a
        square transparent canvas whose vine content is concentrated in the
        top-left and fades toward the bottom-right.  We scale it to the ornament
        size and the caller draws it at the card's top-left corner, so the dense
        TL vines sit on the corner and the sparse tail trails across the card.
        Returns the scaled QPixmap, or None if the id is unknown or the asset is
        missing/unreadable.
        """
        meta = _CORNER_ORNAMENT_BY_ID.get(ornament_id)
        if meta is None:
            logger.warning("[FRAME] unknown ornament id %r; cannot load", ornament_id)
            return None
        import os
        path = os.path.join(os.path.dirname(__file__), "icons", meta.filename)
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            logger.warning("[FRAME] %s missing/unreadable at %s", meta.filename, path)
            return None

        orig_w, orig_h = pixmap.width(), pixmap.height()
        logger.debug("[FRAME] loaded %s (%dx%d)", path, orig_w, orig_h)

        # Scale the square canvas to the ornament size so the TL cluster covers
        # roughly the top-left quarter of the card.
        target = _CORNER_ORNAMENT_SIZE
        pixmap = pixmap.scaled(
            target, target,
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        logger.debug("[FRAME] ornament %s scaled to %dx%d", ornament_id, pixmap.width(), pixmap.height())
        return pixmap



    def _get_display_ms(self) -> int:
        """Get the configured display duration from settings."""
        try:
            from ..config import get_thumbnail_display_time, get_config_path
            return get_thumbnail_display_time(get_config_path())
        except Exception:
            return THUMBNAIL_DISPLAY_MS

    def _pil_to_qpixmap(self, pil_img: Image.Image) -> QtGui.QPixmap:
        from .editor.utils import _pil_to_qpixmap as _shared
        return _shared(pil_img)

    def _create_blurred_background(self, pil_img: Image.Image) -> QtGui.QPixmap:
        """Crop-to-fill the card aspect ratio, scale down, apply Gaussian blur,
        and return a QPixmap to use as the card's decorative background."""
        from PIL import ImageFilter

        card_w, card_h = self.card_width, self.card_height
        img_w, img_h = pil_img.size
        card_aspect = card_w / card_h
        img_aspect = img_w / img_h

        fill = pil_img.copy()
        # Center-crop to match the card's 16:10 aspect ratio
        if img_aspect > card_aspect:
            new_w = int(img_h * card_aspect)
            offset = (img_w - new_w) // 2
            fill = fill.crop((offset, 0, offset + new_w, img_h))
        else:
            new_h = int(img_w / card_aspect)
            offset = (img_h - new_h) // 2
            fill = fill.crop((0, offset, img_w, offset + new_h))

        fill = fill.resize((card_w, card_h), Image.LANCZOS)
        blurred = fill.filter(ImageFilter.GaussianBlur(radius=20))
        return self._pil_to_qpixmap(blurred)

    def start_loading(self):
        """Switch to loading state: stop timer, show a pulsing progress bar."""
        self._loading = True
        self.timer.stop()
        self.fade_anim.stop()
        self._pause_countdown()
        self.setWindowOpacity(1.0)
        self.action_pill.hide()

        self._loading_anim = QtCore.QVariantAnimation(self)
        self._loading_anim.setDuration(1200)
        self._loading_anim.setStartValue(0.0)
        self._loading_anim.setEndValue(1.0)
        self._loading_anim.setLoopCount(-1)
        self._loading_anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)
        self._loading_anim.valueChanged.connect(self._on_loading_tick)
        self._loading_anim.start()
        self.update()

    def _on_loading_tick(self, value: float):
        self._loading_progress = value
        self.update()

    # ── Ripple effect ─────────────────────────────────────────────────
    _RIPPLE_RING_COUNT = 4
    _RIPPLE_RING_STAGGER = 0.07  # ~70 ms between rings at 1000 ms total

    def _start_ripple(self, pos):
        """Begin a 4-ring wave ripple from *pos* (card-relative px)."""
        self._ripple_center = QtCore.QPointF(pos)
        self._ripple_progress = 0.0
        if self._ripple_anim is not None:
            self._ripple_anim.stop()
        self._ripple_anim = QtCore.QVariantAnimation(self)
        self._ripple_anim.setDuration(1000)
        self._ripple_anim.setStartValue(0.0)
        self._ripple_anim.setEndValue(1.0)
        self._ripple_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._ripple_anim.valueChanged.connect(self._on_ripple_tick)
        self._ripple_anim.finished.connect(self._on_ripple_done)
        self._ripple_anim.start()

    def _on_ripple_tick(self, value: float):
        self._ripple_progress = value
        self.update()

    def _on_ripple_done(self):
        self._ripple_center = None
        self._ripple_progress = 0.0
        self._ripple_anim = None
        self.update()

    def dismiss(self):
        """Stop loading / ripple and close the thumbnail (called when OCR popup is ready)."""
        self._loading = False
        self._stop_countdown()
        if self._loading_anim is not None:
            self._loading_anim.stop()
            self._loading_anim = None
        if self._ripple_anim is not None:
            self._ripple_anim.stop()
            self._ripple_anim = None
        self._ripple_center = None
        self.close()

    def _start_timer(self):
        """Start the auto-dismiss timer if a finite display time is configured.
        When display_ms is 0 ('Never hide'), the timer is not started.  """
        ms = self._get_display_ms()
        if ms > 0:
            self.timer.start(ms)

    def _on_auto_dismiss(self):
        """Timer fired — stop the countdown bar and begin fade-out."""
        self._stop_countdown()
        self.fade_in_anim.stop()
        self.setWindowOpacity(1.0)
        self.fade_anim.start()

    # ── Countdown progress bar ──────────────────────────────────────────
    def _tick_countdown(self):
        """Called every 50 ms to repaint the countdown bar."""
        if self._countdown_deadline is None:
            self._countdown_tick.stop()
            return
        remaining = self._countdown_deadline - time.monotonic()
        if remaining <= 0:
            self._countdown_deadline = None
            self._countdown_tick.stop()
        self.update()

    def _start_countdown(self):
        """Begin / resume the countdown progress bar.
        Mirrors _start_timer so the bar always reflects the same duration.  """
        ms = self._get_display_ms()
        if ms <= 0:
            self._countdown_deadline = None
            self._countdown_tick.stop()
            return
        self._countdown_total_s = ms / 1000.0
        self._countdown_deadline = time.monotonic() + self._countdown_total_s
        self._countdown_tick.start()

    def _pause_countdown(self):
        """Freeze the countdown bar (e.g. on hover / drag / menu)."""
        self._countdown_tick.stop()

    def _stop_countdown(self):
        """Tear down the countdown entirely (e.g. on dismiss / close)."""
        self._countdown_tick.stop()
        self._countdown_deadline = None
        self.update()

    def refresh_timer(self):
        """Re-read config and immediately apply the new display duration.

        Called when the user changes the thumbnail-display-time setting so
        the currently-visible thumbnail reacts without waiting for the next
        screenshot.  Switches between never-hide ↔ countdown seamlessly.
        """
        ms = self._get_display_ms()
        if ms <= 0:
            # "Never hide" — cancel any running timer / countdown, restore full opacity
            self.timer.stop()
            self.fade_anim.stop()
            self._stop_countdown()
            self.setWindowOpacity(1.0)
        else:
            # Finite duration — restart both timer and countdown from now
            self.timer.stop()
            self.fade_anim.stop()
            self.setWindowOpacity(1.0)
            self.timer.start(ms)
            self._start_countdown()

    def showEvent(self, event):
        super().showEvent(event)
        self.pos_anim.start()
        self.fade_in_anim.start()
        self._start_timer()
        self._start_countdown()

    def _cursor_over_card(self) -> bool:
        """Whether the cursor is currently inside the card_rect (the click region).

        Hover and click share this single region so their behavior can never
        diverge - the corner ornament, which extends the window up-left of the
        card, is irrelevant to both."""
        return self.card_rect.contains(self.mapFromGlobal(QtGui.QCursor.pos()))

    def _set_hovered(self, over_card: bool) -> None:
        """Set hover visuals (green glow + action pill) on/off, no-op if unchanged.

        Single write site for _hovered + pill visibility, so enter/move/leave
        can't drift out of sync."""
        if over_card == self._hovered:
            return
        self._hovered = over_card
        self.update()
        if over_card:
            self.action_pill.show()
            self.action_pill.raise_()
        else:
            self.action_pill.hide()

    def enterEvent(self, event):
        """Pause auto-hide on entering the window; light up only if over the card."""
        self.timer.stop()
        self.fade_anim.stop()
        self._pause_countdown()
        self.setWindowOpacity(1.0)
        self._set_hovered(self._cursor_over_card())

    def leaveEvent(self, event):
        """Resume auto-hide on leaving the window; clear hover visuals."""
        if not self._is_dragging and not self._menu_active:
            self._start_timer()
            self._start_countdown()
        self._set_hovered(False)
        self._restore_pill_style()

    # ── Pill hover ─────────────────────────────────────────────────
    # Hover state is tracked via self._pill_state and rendered in paintEvent.
    # No stylesheet gradients — parent QPainter handles it artifact-free.

    def _restore_pill_style(self):
        self._pill_hover_timer.stop()
        self._pill_state = 'none'
        self.update()

    def eventFilter(self, obj, event):
        if obj == self.edit_btn:
            if event.type() == QtCore.QEvent.Type.Enter:
                self._pill_hover_timer.stop()
                self._pill_state = 'edit'
                self.update()
            elif event.type() == QtCore.QEvent.Type.Leave:
                self._pill_hover_timer.start(60)
        elif obj == self.pin_btn:
            if event.type() == QtCore.QEvent.Type.Enter:
                self._pill_hover_timer.stop()
                self._pill_state = 'pin'
                self.update()
            elif event.type() == QtCore.QEvent.Type.Leave:
                self._pill_hover_timer.start(60)
        elif obj == self.close_btn:
            if event.type() == QtCore.QEvent.Type.Enter:
                self._pill_hover_timer.stop()
                self._pill_state = 'close'
                self.update()
            elif event.type() == QtCore.QEvent.Type.Leave:
                self._pill_hover_timer.start(60)
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self.card_rect.contains(pos):
                self._drag_start_pos = pos
                self._start_ripple(pos)

    def mouseMoveEvent(self, event):
        # Plain hover moves (no button): keep the glow + pill glued to card_rect.
        if not (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            self._set_hovered(self.card_rect.contains(event.position().toPoint()))
            return
        if not self._drag_start_pos:
            return
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < _DRAG_THRESHOLD_PX:
            return
        self._start_drag()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            release_pos = event.position().toPoint()
            if self.card_rect.contains(release_pos):
                if self.action_pill.geometry().contains(release_pos):
                    return
                logger.debug("[OCR_CHAIN] thumbnail clicked at %s", release_pos)
                self.clicked_signal.emit()
                # Don't close here — the ripple plays while OCR starts,
                # and dismiss() interrupts the ripple when the popup is ready.
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            if self.card_rect.contains(event.position().toPoint()):
                self._show_context_menu(event.globalPosition().toPoint())

    def _show_context_menu(self, pos):
        from ..config import resolve_ui_lang, ui_text, get_config_path
        lang = resolve_ui_lang(get_config_path())

        self._menu_active = True
        self._hovered = True
        self.update()
        self.timer.stop()
        self.fade_anim.stop()
        self._pause_countdown()
        self.setWindowOpacity(1.0)

        menu = QtWidgets.QMenu(self)
        from .styles import MODERN_MENU_STYLE, apply_menu_shadow
        menu.setStyleSheet(MODERN_MENU_STYLE)
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        apply_menu_shadow(menu)

        # The hover pill already exposes Edit / Pin / Close, so the right-click
        # menu carries only actions the pill does NOT: View Original,
        # Copy Image, and Save to Desktop.
        view_action = menu.addAction(ui_text(lang, "thumbnail_open_in_viewer"))
        copy_action = menu.addAction(ui_text(lang, "pin_copy_image"))
        desktop_action = menu.addAction(ui_text(lang, "thumbnail_save_to_desktop"))

        action = menu.exec(pos)
        self._menu_active = False

        if action == view_action:
            self.open_in_viewer_signal.emit()
            self.close()
        elif action == copy_action:
            self.copy_image_signal.emit()
            self.close()
        elif action == desktop_action:
            self.save_to_desktop_signal.emit()
            self.close()
        else:
            self._hovered = False
            self.update()
            self._start_timer()
            self._start_countdown()

    def _start_drag(self):
        self._is_dragging = True
        self.timer.stop()
        self._pause_countdown()
        
        logger.debug("--- Drag-and-Drop Start ---")
        
        # Visual feedback
        self.setWindowOpacity(THUMBNAIL_DRAG_OPACITY)
        scaled_w = int(self.card_width * THUMBNAIL_DRAG_SCALE)
        scaled_h = int(self.card_height * THUMBNAIL_DRAG_SCALE)

        # Rotating cache: keep the last 2 files so a slow upload (e.g.
        # browser on a sluggish network) can still read the source after
        # drag.exec() returns.  The drop target may defer the actual file
        # read — deleting immediately would break that upload.
        # Startup purge cleans everything on next launch.
        from ..system.drag_cache import create_temp
        temp_path_obj = create_temp(self.pil_image)
        temp_path = str(temp_path_obj)

        drag = QtGui.QDrag(self)
        mime_data = QtCore.QMimeData()
        mime_data.setUrls([QtCore.QUrl.fromLocalFile(temp_path)])
        drag.setMimeData(mime_data)
        
        drag_pixmap = self.pixmap.scaled(
            scaled_w, scaled_h, 
            QtCore.Qt.AspectRatioMode.KeepAspectRatio, 
            QtCore.Qt.TransformationMode.SmoothTransformation
        )
        drag.setPixmap(drag_pixmap)
        drag.setHotSpot(QtCore.QPoint(scaled_w // 2, scaled_h // 2))

        logger.debug("Executing drag.exec()...")
        # Force CopyAction: the external app gets a copy, our source file
        # stays in drag_cache.  We do NOT delete the file here — the drop
        # target may defer reading the file contents (e.g. a browser
        # uploading over a slow network stores the path and reads later).
        # The rotating cache (keep last 2) + startup purge handle cleanup.
        result = drag.exec(QtCore.Qt.DropAction.CopyAction)
        logger.debug(f"Drag finished. Result: {result}")
        
        if result != QtCore.Qt.DropAction.IgnoreAction and os.name == 'nt':
            # The shell handled the copy/move, but some Explorer views
            # (especially cloud-backed folders) may not refresh on their
            # own.  SHCNE_UPDATEDIR asks the shell to re-enumerate folder
            # contents so the file appears immediately without a manual F5.
            try:
                SHCNE_UPDATEDIR = 0x00001000
                SHCNF_IDLIST = 0x00000000
                SHCNF_FLUSH = 0x00001000
                shell32.SHChangeNotify(SHCNE_UPDATEDIR,
                                       SHCNF_IDLIST | SHCNF_FLUSH,
                                       None, None)
            except Exception:
                logger.debug("thumbnail: SHChangeNotify(UPDATEDIR) failed", exc_info=True)

        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return

        self._is_dragging = False
        
        if result == QtCore.Qt.DropAction.IgnoreAction:
            cursor_pos = self.mapFromGlobal(QtGui.QCursor.pos())
            if self.card_rect.contains(cursor_pos):
                self.clicked_signal.emit()
                self.close()
            else:
                self.setWindowOpacity(1.0)
                self._hovered = False
                self.action_pill.hide()
                self._start_timer()
                self._start_countdown()
                self.update()
        else:
            self.close()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    def dropEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)

        # Shadow
        for i in range(1, _SHADOW_PASSES + 1):
            alpha = int(_SHADOW_ALPHA_PER_PASS * (1.0 - (i / (_SHADOW_PASSES + 1))))
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, alpha), 2))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect).adjusted(-i + 0.5, -i + 2.0, i - 0.5, i + 2.0),
                THUMBNAIL_CORNER_RADIUS + i,
                THUMBNAIL_CORNER_RADIUS + i
            )

        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.card_rect), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)
        painter.setClipPath(path)

        # Blurred background fills the card
        painter.drawPixmap(self.card_rect, self.blurred_bg)

        # Subtle dark overlay so the sharp thumbnail pops against the blurred bg
        painter.fillPath(path, QtGui.QColor(0, 0, 0, 50))

        # Soft elevation shadow underneath the thumbnail — multi-pass blur
        # so the sharp content "floats" above the blurred background rather
        # than sitting flat against it.
        shadow_rect = QtCore.QRectF(self.pixmap_rect)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        for i in range(1, _ELEVATION_PASSES + 1):
            alpha = int(_ELEVATION_ALPHA_PER_PASS * (1.0 - i / (_ELEVATION_PASSES + 1)))
            spread = i * 2.0
            painter.setBrush(QtGui.QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(
                shadow_rect.adjusted(spread, spread + 1.5, spread, spread + 1.5),
                8 + i, 8 + i,
            )

        # Sharp thumbnail centered on top
        painter.drawPixmap(self.pixmap_rect, self.scaled_pixmap)

        # Thin separator border around the sharp thumbnail
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        thumb_path = QtGui.QPainterPath()
        thumb_rect = QtCore.QRectF(self.pixmap_rect).adjusted(-0.5, -0.5, 0.5, 0.5)
        thumb_path.addRoundedRect(thumb_rect, 6, 6)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 45), 1))
        painter.drawPath(thumb_path)

        # Loading indicator — thin animated bar at the bottom of the card
        if self._loading:
            bar_h = 2
            margin = 10
            bar_y = self.card_rect.bottom() - bar_h - 3
            track_w = self.card_rect.width() - margin * 2

            # Background track
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 18))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect.left() + margin, bar_y, track_w, bar_h),
                1, 1,
            )

            # Animated segment — oscillates left↔right with InOutSine easing
            seg_w = min(50, track_w)
            travel = track_w - seg_w
            t = self._loading_progress
            offset = (math.sin(t * math.pi * 2 - math.pi / 2) + 1) / 2 * travel
            painter.setBrush(QtGui.QColor(BRAND_GREEN))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect.left() + margin + offset, bar_y, seg_w, bar_h),
                1, 1,
            )
            # Reset brush so it doesn't leak into the card border fill below
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        # Countdown progress bar — thin shrinking line at card bottom.
        # Only shown when idle (not loading, not hovered) and a finite
        # display time is configured.  Hidden during hover because the
        # timer is paused then — a frozen bar conveys no useful info.
        elif (not self._hovered
              and self._countdown_deadline is not None
              and self._countdown_total_s > 0):
            remaining = max(0.0, self._countdown_deadline - time.monotonic())
            progress = remaining / self._countdown_total_s  # 1.0 → 0.0

            bar_h = 3
            margin = 10
            bar_y = self.card_rect.bottom() - bar_h - 3
            track_w = self.card_rect.width() - margin * 2

            # Subtle background track
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 25))
            painter.drawRoundedRect(
                QtCore.QRectF(self.card_rect.left() + margin, bar_y, track_w, bar_h),
                1, 1,
            )

            # Fill colour: neutral white, warming to a soft red in the last 2 s
            if remaining <= _COUNTDOWN_WARN_S:
                t = max(0.0, remaining) / 2.0  # 1.0 → 0.0 (2 s → 0 s)
                r = 255
                g = int(90 + (255 - 90) * t)
                b = int(90 + (255 - 90) * t)
                a = int(55 + (65 - 55) * (1.0 - t))  # 55 → 65 (bolder as time runs out)
            else:
                r, g, b, a = 255, 255, 255, 55

            fill_w = int(track_w * progress)
            if fill_w > 0:
                painter.setBrush(QtGui.QColor(r, g, b, a))
                painter.drawRoundedRect(
                    QtCore.QRectF(self.card_rect.left() + margin, bar_y, fill_w, bar_h),
                    1, 1,
                )

            # Reset brush so it doesn't leak into the card border fill below
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        # Ripple — 4 concentric wave rings, each with a dark trough
        # (water dips, reflects less light) + a bright crest (water piles
        # up, catches light). Staggered ~70 ms apart for a wave feel.
        if self._ripple_center is not None:
            stagger = self._RIPPLE_RING_STAGGER
            for i in range(self._RIPPLE_RING_COUNT):
                delay = i * stagger
                if self._ripple_progress <= delay:
                    continue
                p = (self._ripple_progress - delay) / (1.0 - delay)
                scale = 1.0 - i * 0.18  # later rings progressively fainter

                max_r = 85.0
                r = max_r * p

                # Trough — dark band just inside the crest. Narrow gap
                # that widens toward the end as the wave flattens.
                trough_gap = 5.0 * (1.0 - p * 0.6)
                trough_r = max(r - trough_gap, 0.0)
                trough_alpha = int(16 * scale * (1.0 - p))
                painter.setPen(QtGui.QPen(
                    QtGui.QColor(0, 0, 0, trough_alpha), 1.8))
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                if trough_r > 0:
                    painter.drawEllipse(self._ripple_center, trough_r, trough_r)

                # Crest — bright outer stroke at the wave peak.
                crest_alpha = int(32 * scale * (1.0 - p))
                pen_w = 2.0 * (1.0 - p * 0.3)
                painter.setPen(QtGui.QPen(
                    QtGui.QColor(255, 255, 255, crest_alpha), pen_w))
                painter.drawEllipse(self._ripple_center, r, r)

        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 40), 1))
        painter.drawRoundedRect(QtCore.QRectF(self.card_rect).adjusted(0.5, 0.5, -0.5, -0.5), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)

        if self._hovered:
            painter.setPen(QtGui.QPen(QtGui.QColor(BRAND_GREEN), 1.5))
            painter.drawRoundedRect(QtCore.QRectF(self.card_rect).adjusted(1, 1, -1, -1), THUMBNAIL_CORNER_RADIUS, THUMBNAIL_CORNER_RADIUS)

        # ── Corner ornament (optional, hugs the card's top-left corner) ──
        # Drawn AFTER the card content/border but BEFORE the action pill, using
        # the rect computed once in __init__ (self._ornament_rect) so the painted
        # position can never drift from the intended one.  NOT clipped by the card
        # path - the vine is meant to cross the card border and reach outside,
        # like a clasp biting the corner.
        if self._frame_enabled and self._frame_pixmap is not None and self._ornament_rect is not None:
            painter.drawPixmap(self._ornament_rect.topLeft(), self._frame_pixmap)

        # ── Pill background ────────────────────────────────────────────────
        # Drawn by the parent painter so Qt never pre-fills a child-widget
        # bounding rect with white before border-radius is applied.
        if self.action_pill.isVisible():
            pill_geom = QtCore.QRectF(self.action_pill.geometry())
            pill_path = QtGui.QPainterPath()
            pill_path.addRoundedRect(pill_geom, 14.0, 14.0)

            # Base dark fill
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(18, 18, 18, 185))
            painter.drawPath(pill_path)

            # Soft colour wash on the hovered button — layered over base
            if self._pill_state == 'edit':
                grad = QtGui.QLinearGradient(pill_geom.left(), 0, pill_geom.right(), 0)
                grad.setColorAt(0.00, QtGui.QColor(95, 201, 138, 58))
                grad.setColorAt(0.28, QtGui.QColor(95, 201, 138, 16))
                grad.setColorAt(0.42, QtGui.QColor(0, 0, 0, 0))
                grad.setColorAt(1.00, QtGui.QColor(0, 0, 0, 0))
                painter.setBrush(QtGui.QBrush(grad))
                painter.drawPath(pill_path)
            elif self._pill_state == 'pin':
                grad = QtGui.QLinearGradient(pill_geom.left(), 0, pill_geom.right(), 0)
                grad.setColorAt(0.00, QtGui.QColor(0, 0, 0, 0))
                grad.setColorAt(0.15, QtGui.QColor(95, 201, 138, 10))
                grad.setColorAt(0.35, QtGui.QColor(95, 201, 138, 48))
                grad.setColorAt(0.55, QtGui.QColor(95, 201, 138, 10))
                grad.setColorAt(1.00, QtGui.QColor(0, 0, 0, 0))
                painter.setBrush(QtGui.QBrush(grad))
                painter.drawPath(pill_path)
            elif self._pill_state == 'close':
                grad = QtGui.QLinearGradient(pill_geom.left(), 0, pill_geom.right(), 0)
                grad.setColorAt(0.00, QtGui.QColor(0, 0, 0, 0))
                grad.setColorAt(0.60, QtGui.QColor(0, 0, 0, 0))
                grad.setColorAt(0.75, QtGui.QColor(210, 50, 50, 36))
                grad.setColorAt(1.00, QtGui.QColor(210, 50, 50, 78))
                painter.setBrush(QtGui.QBrush(grad))
                painter.drawPath(pill_path)

            # 1px hairline border
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1.0))
            painter.drawPath(pill_path)

class ThumbnailManager(QtCore.QObject):
    """
    Manages thumbnail window creation from any thread.
    """
    show_signal = QtCore.pyqtSignal(object)
    clicked = QtCore.pyqtSignal(object)
    save_to_desktop = QtCore.pyqtSignal(object)
    copy_image = QtCore.pyqtSignal(object)
    pin_requested = QtCore.pyqtSignal(object, object, object)
    edit_requested = QtCore.pyqtSignal(object)
    open_in_viewer = QtCore.pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.show_signal.connect(self._do_show)
        self._windows = []

    def _do_show(self, pil_image: Image.Image):
        # Only one thumbnail is ever visible at a time: a hidden/off-screen
        # thumbnail carries no information for the user, so each new capture
        # replaces the previous window. The list is cleared synchronously
        # here, and the old window's close() fires its `destroyed` signal
        # later — by then `win` is no longer in `self._windows`, so the
        # destroyed handler below is a guarded no-op for it. The handler
        # exists to drop the *current* window from the list when it
        # self-dismisses (auto-timeout / fade-out close).
        for w in self._windows:
            try:
                w.close()
            except Exception:
                logger.debug("thumbnail: failed to close previous window", exc_info=True)
        self._windows = []

        win = ThumbnailWindow(pil_image)
        win.clicked_signal.connect(lambda: self.clicked.emit(pil_image))
        win.save_to_desktop_signal.connect(lambda: self.save_to_desktop.emit(pil_image))
        win.copy_image_signal.connect(lambda: self.copy_image.emit(pil_image))
        win.edit_requested_signal.connect(lambda: self.edit_requested.emit(pil_image))
        win.open_in_viewer_signal.connect(lambda: self.open_in_viewer.emit(pil_image))
        win.pin_requested_signal.connect(
            lambda: self.pin_requested.emit(
                pil_image,
                win.mapToGlobal(win.card_rect.topLeft()),
                win.card_rect.size()
            )
        )
        # `win` is captured per-call (each _do_show gets its own closure), so
        # this removes exactly this window, not whichever happens to be last.
        win.destroyed.connect(lambda: self._windows.remove(win) if win in self._windows else None)
        self._windows.append(win)
        win.show()

    def current_window(self):
        """Return the current visible ThumbnailWindow, or None."""
        for w in self._windows:
            try:
                if w.isVisible():
                    return w
            except RuntimeError:
                logger.debug("thumbnail: current_window on deleted window", exc_info=True)
        return None

    def dismiss_current(self):
        """Close the current thumbnail immediately (called when OCR result is ready)."""
        logger.debug("[OCR_CHAIN] thumbnail dismiss_current")
        for w in self._windows:
            try:
                w.dismiss()
            except Exception:
                logger.debug("thumbnail: dismiss failed", exc_info=True)
        self._windows.clear()

    def refresh_current(self):
        """Re-apply the display-time setting to the visible thumbnail.

        Called from the settings dialog so the user sees the change
        take effect on the currently-shown thumbnail immediately.
        """
        win = self.current_window()
        if win is not None:
            try:
                win.refresh_timer()
            except Exception:
                logger.debug("thumbnail: refresh_timer failed", exc_info=True)

    def current_window_center(self):
        for w in self._windows:
            try:
                if w.isVisible():
                    geo = w.geometry()
                    return (geo.center().x(), geo.center().y())
            except RuntimeError:
                logger.debug("thumbnail: center on deleted window", exc_info=True)
        return None

    def current_window_rect(self):
        for w in self._windows:
            try:
                if w.isVisible():
                    return w.mapToGlobal(w.card_rect.topLeft()), w.card_rect.size()
            except RuntimeError:
                logger.debug("thumbnail: rect on deleted window", exc_info=True)
        return None, None

thumbnail_manager = ThumbnailManager()

def qpixmap_to_pil(pixmap: QtGui.QPixmap) -> Image.Image:
    from .editor.utils import _qpixmap_to_pil as _shared
    return _shared(pixmap)


def show_thumbnail(pil_image: Image.Image):
    if thumbnail_manager:
        thumbnail_manager.show_signal.emit(pil_image)
