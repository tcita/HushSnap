"""
HushSnap lightweight image editor.
Refactored into hushsnap.ui.editor package.
"""

from __future__ import annotations

import logging
import gc
import sys
from pathlib import Path
from typing import Optional, Callable

from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import get_config_path, get_editor_window_geometry, set_editor_window_geometry
from ..dpi import current_dpr, cursor_screen

# Modularized imports
from .editor.constants import (
    EDITOR_WINDOW_STYLE, EDITOR_TOOLBAR_ROW_STYLE, EDITOR_TOOL_BUTTON_STYLE,
    EDITOR_PUSH_BUTTON_STYLE, EDITOR_OPTION_TOGGLE_STYLE, EDITOR_STATUS_STYLE,
    EDITOR_OPTIONS_STYLE, BRAND_GREEN
)
from .editor.utils import (
    _load_editor_icon, _pil_to_qpixmap, _qpixmap_to_pil,
    _make_circle_cursor, _draw_outlined_text
)
from .editor.models import UndoChangeType, TextItem, _UndoEntry
from .editor.tools.base import BaseTool
from .editor.tools.drawing import BrushTool, HighlighterTool, EraserTool
from .editor.tools.shapes import ShapeTool
from .editor.tools.text import TextTool
from .editor.tools.navigation import PanTool
from .editor.tools.transform import CropTool, MosaicTool, SequenceTool, RotateTool, ResizeTool
from .editor.widgets.canvas import EditorCanvas
from .editor.widgets.controls import (
    _EditorFontComboBox, _ColorButton, _SwatchPopup
)

logger = logging.getLogger(__name__)

# ── Editor Window ────────────────────────────────────────────────────────────

# Window sizing. The minimum leaves enough vertical room for the title bar,
# two toolbar rows, and the status bar (~162 px of chrome) plus a usable
# canvas (~250 px); narrower than this and the tool buttons crowd together.
_EDITOR_MIN_W, _EDITOR_MIN_H = 640, 520
_EDITOR_DEFAULT_W, _EDITOR_DEFAULT_H = 960, 700


class ImageEditorWindow(QtWidgets.QWidget):
    """Main editor window with toolbar, canvas, and controls."""

    MAX_UNDO_STEPS = 10
    MAX_FONT_SIZE = 200  # upper bound for typed font sizes (px)

    def __init__(
        self,
        pil_image: Image.Image,
        translate_fn: Callable[[str], str],
        parent: Optional[QtWidgets.QWidget] = None,
        screen: Optional[QtGui.QScreen] = None,
    ):
        super().__init__(parent)
        self._tr = translate_fn
        self._original_pil = pil_image.copy()
        self._pil_image = pil_image.copy()

        # Resolve the target screen (multi-monitor aware). The cursor-screen
        # lookup is deferred to _resolve_target_screen (called after the window
        # is fully constructed) because calling screenAt(QCursor.pos()) inside
        # __init__ crashed show() with a hard fault.
        self._target_screen = screen or QtWidgets.QApplication.primaryScreen()
        self._dpr = (
            self._target_screen.devicePixelRatio()
            if self._target_screen else current_dpr()
        )

        # Layers
        self._display_pixmap: Optional[QtGui.QPixmap] = None
        self._annotations_pixmap: Optional[QtGui.QPixmap] = None
        self._overlay_pixmap: Optional[QtGui.QPixmap] = None
        # Live rotation preview pixmap; when set, the canvas renders this instead
        # of _display_pixmap and skips annotations (whose geometry no longer fits).
        self._preview_pixmap: Optional[QtGui.QPixmap] = None
        # Paint-time rotation preview angle (degrees, clockwise). When not None,
        # the canvas rotates the original display pixmap around its center in
        # paintEvent — no pixmap allocation or widget resize per frame, so no
        # flicker and a stable pivot. Used by the rotate tool.
        self._preview_angle: Optional[float] = None
        # ── Transform session (shared by crop / rotate / resize) ───────────
        # Entering any image transform bakes annotations + text into the base
        # image once (text becomes non-editable pixels for the duration). The
        # pre-composite snapshot below lets undo / Esc-cancel restore the clean
        # base image + editable annotations + text. The whole session is one
        # atomic undo unit, committed on tool deactivate. See
        # _begin/_commit/_cancel_transform_session.
        self._transform_active = False
        # Tool-local resampling bases (only the active tool uses its own):
        # rotate samples from _rotate_base_image every release so repeated
        # rotations don't compound; resize does the same with _resize_base_image.
        self._rotate_base_image: Optional[Image.Image] = None
        self._rotate_base_pixmap: Optional[QtGui.QPixmap] = None
        self._rotate_cumulative_angle = 0.0
        self._resize_base_image: Optional[Image.Image] = None

        # State
        self._scale = 1.0
        self._modified = False
        self._undo_stack: list[_UndoEntry] = []
        self._redo_stack: list[_UndoEntry] = []
        self._active_tool: Optional[BaseTool] = None
        self._tools: dict[str, BaseTool] = {}
        self._tool_buttons: dict[str, QtWidgets.QToolButton] = {}
        self._option_widgets: dict[tuple[str, str], QtWidgets.QWidget] = {}
        # Text annotation data (persistent items)
        self._text_items: list[TextItem] = []

        self._setup_ui()
        self._setup_tools()
        self._init_from_image()
        self._activate_tool("pan")

    def _resolve_target_screen(self) -> None:
        """Resolve the cursor's screen now that the window is constructed.

        Called after __init__ (from show_image_editor). The cursor-screen
        lookup can't run inside __init__ — it crashed show() with a hard
        fault, likely by re-entering the windowing system mid-construction.
        """
        resolved = cursor_screen()
        if resolved is not None:
            self._target_screen = resolved
            self._dpr = resolved.devicePixelRatio()

    # ── UI Setup ──────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setObjectName("editorWindow")
        self.setStyleSheet(EDITOR_WINDOW_STYLE)
        self.setWindowTitle(self._tr("editor_title"))
        self.setMinimumSize(_EDITOR_MIN_W, _EDITOR_MIN_H)
        self.resize(_EDITOR_DEFAULT_W, _EDITOR_DEFAULT_H)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Debounce canvas relayout during window resize — the system sends a
        # flood of resize events while dragging the border; we only relayout
        # once the user pauses, so the image doesn't flicker.
        self._resize_debounce = QtCore.QTimer(self)
        self._resize_debounce.setSingleShot(True)
        self._resize_debounce.setInterval(50)
        self._resize_debounce.timeout.connect(self._on_resize_settled)

        # Toolbar row 1 — tools + transforms
        toolbar1 = self._create_toolbar_row1()
        main_layout.addWidget(toolbar1)

        # Toolbar row 2 — tool options
        self._options_stack = QtWidgets.QStackedWidget()
        self._options_stack.setObjectName("optionsArea")
        self._options_stack.setStyleSheet(EDITOR_OPTIONS_STYLE)
        self._options_stack.setMaximumHeight(38)
        self._setup_option_pages()
        main_layout.addWidget(self._options_stack)

        # Canvas (in scroll area)
        self._canvas = EditorCanvas(self)
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setWidget(self._canvas)
        self._scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #1a1a1a; }")
        self._scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        main_layout.addWidget(self._scroll_area, 1)

        # Status bar
        status = self._create_status_bar()
        main_layout.addWidget(status)

        # Ctrl+0 = fit to viewport
        self._fit_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+0"), self, self._fit_to_viewport
        )

    def _create_toolbar_row1(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setObjectName("toolbarRow")
        bar.setStyleSheet(EDITOR_TOOLBAR_ROW_STYLE)
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(3)

        def _add_sep():
            s = QtWidgets.QFrame()
            s.setFrameShape(QtWidgets.QFrame.Shape.VLine)
            s.setStyleSheet("background-color: rgba(255,255,255,12); border: none;")
            s.setFixedWidth(1)
            s.setFixedHeight(20)
            layout.addSpacing(6)
            layout.addWidget(s)
            layout.addSpacing(6)

        def _add_tools(tools):
            for tid, label_key, icon_name in tools:
                btn = QtWidgets.QToolButton()
                btn.setIcon(_load_editor_icon(icon_name))
                btn.setIconSize(QtCore.QSize(20, 20))
                btn.setToolTip(self._tr(label_key))
                btn.setCheckable(True)
                btn.setStyleSheet(EDITOR_TOOL_BUTTON_STYLE)
                btn.clicked.connect(lambda checked, t=tid: self._activate_tool(t))
                layout.addWidget(btn)
                self._tool_buttons[tid] = btn

        _add_tools([
            ("rectangle", "tool_rectangle", "rectangle"),
            ("ellipse", "tool_ellipse", "ellipse"),
            ("line", "tool_line", "line"),
            ("sequence", "tool_sequence", "sequence"),
        ])

        _add_sep()

        _add_tools([
            ("text", "tool_text", "text"),
            ("brush", "tool_brush", "brush"),
            ("highlighter", "tool_highlighter", "highlighter"),
        ])

        _add_sep()

        _add_tools([
            ("mosaic", "tool_mosaic", "mosaic"),
            ("eraser", "tool_eraser", "eraser"),
            ("crop", "tool_crop", "crop"),
            ("rotate", "tool_rotate", "rotate"),
            ("resize", "tool_resize", "resize"),
        ])

        layout.addStretch()

        # Undo / Redo
        self._undo_btn = QtWidgets.QPushButton()
        self._undo_btn.setIcon(_load_editor_icon("undo"))
        self._undo_btn.setIconSize(QtCore.QSize(20, 20))
        self._undo_btn.setToolTip(self._tr("editor_undo") + " (Ctrl+Z)")
        self._undo_btn.setShortcut("Ctrl+Z")
        self._undo_btn.setFixedSize(32, 28)
        self._undo_btn.setStyleSheet(EDITOR_PUSH_BUTTON_STYLE)
        self._undo_btn.clicked.connect(self._undo)
        self._undo_btn.setEnabled(False)
        layout.addWidget(self._undo_btn)

        self._redo_btn = QtWidgets.QPushButton()
        self._redo_btn.setIcon(_load_editor_icon("redo"))
        self._redo_btn.setIconSize(QtCore.QSize(20, 20))
        self._redo_btn.setToolTip(self._tr("editor_redo") + " (Ctrl+Y, Ctrl+Shift+Z)")
        self._redo_btn.setShortcut("Ctrl+Y")
        self._redo_shift_sc = QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+Shift+Z"), self, self._redo
        )
        self._redo_shift_sc.setEnabled(False)
        self._redo_btn.setFixedSize(32, 28)
        self._redo_btn.setStyleSheet(EDITOR_PUSH_BUTTON_STYLE)
        self._redo_btn.clicked.connect(self._redo)
        self._redo_btn.setEnabled(False)
        layout.addWidget(self._redo_btn)

        _add_sep()

        self._reset_btn = QtWidgets.QPushButton()
        self._reset_btn.setToolTip(self._tr("editor_reset"))
        self._reset_btn.setIcon(_load_editor_icon("reset", QtGui.QColor("#ff5050")))
        self._reset_btn.setIconSize(QtCore.QSize(20, 20))
        self._reset_btn.setFixedSize(32, 28)
        self._reset_btn.setStyleSheet(EDITOR_PUSH_BUTTON_STYLE)
        self._reset_btn.clicked.connect(self._reset_image)
        layout.addWidget(self._reset_btn)

        _add_sep()

        _add_tools([
            ("pan", "tool_pan", "pan"),
        ])

        return bar

    def _setup_option_pages(self) -> None:
        """Create tool-specific option pages in the stacked widget."""
        # Page 0: Brush options
        page_brush = self._make_options_page(["color", "size"], "brush")
        self._options_stack.addWidget(page_brush)

        # Page 1: Highlighter options
        page_hl = self._make_options_page(["color", "size", "opacity"], "highlighter")
        self._options_stack.addWidget(page_hl)

        # Page 2: Eraser options
        page_eraser = self._make_options_page(["size"], "eraser")
        self._options_stack.addWidget(page_eraser)

        # Page 3: Mosaic options
        page_mosaic = self._make_options_page(["size"], "mosaic")
        self._options_stack.addWidget(page_mosaic)

        # Page 4: Crop tool
        page_crop = QtWidgets.QWidget()
        page_crop.setStyleSheet(EDITOR_OPTIONS_STYLE)
        crop_layout = QtWidgets.QHBoxLayout(page_crop)
        crop_layout.setContentsMargins(10, 2, 10, 2)
        crop_instruction = QtWidgets.QLabel(self._tr("editor_crop_instruction"))
        crop_instruction.setStyleSheet("color: #aaa; font-size: 11px; background: transparent;")
        crop_layout.addWidget(crop_instruction)
        crop_layout.addStretch()
        self._options_stack.addWidget(page_crop)

        # Page 5: Text options
        page_text = self._make_options_page(["font", "font_size"], "text")
        # Append an instruction label on the right so the double-click-to-create
        # behaviour is discoverable without crowding the font/size controls left.
        tlayout = page_text.layout()
        if tlayout:
            tinst = QtWidgets.QLabel(self._tr("editor_text_instruction"))
            tinst.setStyleSheet("color: #aaa; font-size: 11px; background: transparent;")
            tlayout.addStretch()
            tlayout.addWidget(tinst)
        self._options_stack.addWidget(page_text)

        # Page 6: Pan tool
        page_pan = QtWidgets.QWidget()
        self._options_stack.addWidget(page_pan)

        # Page 7: Rectangle options
        page_rect = self._make_options_page(["color", "size", "fill"], "rectangle")
        self._options_stack.addWidget(page_rect)

        # Page 8: Ellipse options
        page_ellipse = self._make_options_page(["color", "size", "fill"], "ellipse")
        self._options_stack.addWidget(page_ellipse)

        # Page 9: Line options
        page_line = self._make_options_page(["color", "size", "arrow", "double_arrow"], "line")
        self._options_stack.addWidget(page_line)

        # Page 10: Sequence options
        page_sequence = self._make_options_page(["color", "size"], "sequence")
        self._options_stack.addWidget(page_sequence)

        # Page 11: Rotate tool — instruction line only
        page_rotate = QtWidgets.QWidget()
        page_rotate.setStyleSheet(EDITOR_OPTIONS_STYLE)
        rlayout = QtWidgets.QHBoxLayout(page_rotate)
        rlayout.setContentsMargins(10, 2, 10, 2)
        rlabel = QtWidgets.QLabel(self._tr("editor_rotate_instruction"))
        rlabel.setStyleSheet("color: #aaa; font-size: 11px; background: transparent;")
        rlayout.addWidget(rlabel)
        rlayout.addStretch()
        self._options_stack.addWidget(page_rotate)

        # Page 12: Resize tool — instruction line only
        page_resize = QtWidgets.QWidget()
        page_resize.setStyleSheet(EDITOR_OPTIONS_STYLE)
        slayout = QtWidgets.QHBoxLayout(page_resize)
        slayout.setContentsMargins(10, 2, 10, 2)
        slabel = QtWidgets.QLabel(self._tr("editor_resize_instruction"))
        slabel.setStyleSheet("color: #aaa; font-size: 11px; background: transparent;")
        slayout.addWidget(slabel)
        slayout.addStretch()
        self._options_stack.addWidget(page_resize)

    PAGE_INDEX = {"brush": 0, "highlighter": 1, "eraser": 2, "mosaic": 3, "crop": 4, "text": 5,
                  "pan": 6, "rectangle": 7, "ellipse": 8, "line": 9, "sequence": 10,
                  "rotate": 11, "resize": 12}

    def _make_options_page(
        self, option_keys: list[str], tool_id: str
    ) -> QtWidgets.QWidget:
        """Create a horizontal bar of option widgets for a tool."""
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(8)

        for key in option_keys:
            if key == "color":
                lbl = QtWidgets.QLabel(self._tr("editor_color") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                btn = _ColorButton()
                btn.setToolTip(self._tr("editor_color"))
                btn.setObjectName(f"colorBtn_{tool_id}")
                btn.clicked.connect(lambda: self._pick_color(tool_id))
                layout.addWidget(btn)
                self._option_widgets[(tool_id, "colorBtn")] = btn

            elif key == "opacity":
                lbl = QtWidgets.QLabel(self._tr("editor_opacity") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                slider.setRange(10, 255)
                slider.setValue(80)
                slider.setFixedWidth(100)
                slider.setToolTip(self._tr("editor_opacity"))
                slider.setObjectName(f"opacitySlider_{tool_id}")
                slider.valueChanged.connect(
                    lambda v, t=tool_id: self._on_opacity_changed(t, v)
                )
                layout.addWidget(slider)
                self._option_widgets[(tool_id, "opacitySlider")] = slider
                val_lbl = QtWidgets.QLabel("80")
                val_lbl.setObjectName(f"opacityLabel_{tool_id}")
                val_lbl.setStyleSheet("color: #aaa; font-size: 11px; background: transparent; min-width: 24px;")
                slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
                layout.addWidget(val_lbl)

            elif key == "size":
                # Mosaic's "size" is pixel-block size (thin/thick code),
                # not brush thickness — give it a dedicated label so it
                # doesn't read as the brush's Size.
                size_key = "editor_mosaic_size" if tool_id == "mosaic" else "editor_size"
                lbl = QtWidgets.QLabel(self._tr(size_key) + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)

                layout.addWidget(self._create_size_presets(tool_id))

                slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                slider.setFixedWidth(100)
                slider.setToolTip(self._tr(size_key))
                slider.setObjectName(f"sizeSlider_{tool_id}")
                if tool_id == "mosaic":
                    slider.setRange(2, 40)
                    slider.setValue(12)
                elif tool_id == "eraser":
                    slider.setRange(3, 60)
                    slider.setValue(24)
                elif tool_id == "highlighter":
                    slider.setRange(5, 80)
                    slider.setValue(24)
                else:
                    slider.setRange(1, 50)
                    slider.setValue(3)
                slider.valueChanged.connect(
                    lambda v, t=tool_id: self._on_size_changed(t, v)
                )
                layout.addWidget(slider)
                self._option_widgets[(tool_id, "sizeSlider")] = slider
                val_lbl = QtWidgets.QLabel(str(slider.value()))
                val_lbl.setObjectName(f"sizeLabel_{tool_id}")
                val_lbl.setStyleSheet("color: #aaa; font-size: 11px; background: transparent; min-width: 20px;")
                slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
                layout.addWidget(val_lbl)
                self._sync_size_presets(tool_id, slider.value())

            elif key == "font":
                lbl = QtWidgets.QLabel(self._tr("editor_font") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                combo = _EditorFontComboBox()
                combo.setFontFilters(
                    QtWidgets.QFontComboBox.FontFilter.ScalableFonts
                )
                combo.setWritingSystem(QtGui.QFontDatabase.WritingSystem.Any)
                sys_family = QtGui.QFontDatabase.systemFont(
                    QtGui.QFontDatabase.SystemFont.GeneralFont
                ).family()
                idx = combo.findText(sys_family)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.setObjectName(f"fontCombo_{tool_id}")
                combo.currentTextChanged.connect(
                    lambda t, tool=tool_id: self._on_font_changed(tool, t)
                )
                layout.addWidget(combo)
                self._option_widgets[(tool_id, "fontCombo")] = combo

            elif key == "font_size":
                lbl = QtWidgets.QLabel(self._tr("editor_font_size") + ":")
                lbl.setObjectName("optionLabel")
                layout.addWidget(lbl)
                spin = QtWidgets.QSpinBox()
                spin.setRange(1, self.MAX_FONT_SIZE)
                spin.setValue(32)
                spin.setSuffix(" px")
                spin.setObjectName(f"fontSizeSpin_{tool_id}")
                # QSpinBox natively accepts only integers in range, so letters
                # are blocked at the keystroke and out-of-range values are
                # clamped — no extra validator or downstream parsing needed.
                spin.valueChanged.connect(
                    lambda v, tid=tool_id: self._on_font_size_changed(tid, v)
                )
                layout.addWidget(spin)
                self._option_widgets[(tool_id, "fontSizeSpin")] = spin

            elif key == "fill":
                btn = QtWidgets.QToolButton()
                btn.setCheckable(True)
                btn.setText(self._tr("editor_fill"))
                btn.setToolTip(self._tr("editor_fill"))
                btn.setStyleSheet(EDITOR_OPTION_TOGGLE_STYLE)
                btn.setObjectName(f"fillBtn_{tool_id}")
                btn.clicked.connect(lambda checked, tid=tool_id: self._on_fill_changed(tid, checked))
                layout.addWidget(btn)
                self._option_widgets[(tool_id, "fillBtn")] = btn

            elif key == "arrow":
                btn = QtWidgets.QToolButton()
                btn.setCheckable(True)
                btn.setIcon(_load_editor_icon("arrow"))
                btn.setIconSize(QtCore.QSize(16, 16))
                btn.setToolTip(self._tr("tool_arrow"))
                btn.setStyleSheet(EDITOR_OPTION_TOGGLE_STYLE)
                btn.setObjectName(f"arrowBtn_{tool_id}")
                btn.clicked.connect(lambda checked, tid=tool_id: self._on_arrow_changed(tid, checked))
                layout.addWidget(btn)
                self._option_widgets[(tool_id, "arrowBtn")] = btn

            elif key == "double_arrow":
                btn = QtWidgets.QToolButton()
                btn.setCheckable(True)
                btn.setIcon(_load_editor_icon("double_arrow"))
                btn.setIconSize(QtCore.QSize(16, 16))
                btn.setToolTip(self._tr("editor_double_arrow"))
                btn.setStyleSheet(EDITOR_OPTION_TOGGLE_STYLE)
                btn.setObjectName(f"doubleArrowBtn_{tool_id}")
                btn.clicked.connect(lambda checked, tid=tool_id: self._on_double_arrow_changed(tid, checked))
                layout.addWidget(btn)
                self._option_widgets[(tool_id, "doubleArrowBtn")] = btn

        layout.addStretch()
        return page

    def _create_status_bar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background-color: #1e1e1e; border-top: 1px solid rgba(255,255,255,8);")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 10, 0)
        layout.setSpacing(6)

        self._status_label = QtWidgets.QLabel()
        self._status_label.setObjectName("statusLabel")
        self._status_label.setStyleSheet(EDITOR_STATUS_STYLE)
        layout.addWidget(self._status_label)
        layout.addStretch()

        copy_label = self._tr("editor_copy")
        copy_btn = QtWidgets.QPushButton(f"  {copy_label}")
        copy_btn.setIcon(_load_editor_icon("copy"))
        copy_btn.setIconSize(QtCore.QSize(14, 14))
        copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 13px;
                padding: 4px 12px 4px 8px;
                color: #bbb;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: #383838;
                border-color: #4d4d4d;
                color: #e0e0e0;
            }}
            QPushButton:pressed {{
                background: #333;
            }}
        """)
        copy_btn.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(copy_btn)

        save_label = self._tr("editor_save")
        save_btn = QtWidgets.QPushButton(f"  {save_label}")
        save_btn.setIcon(_load_editor_icon("save", QtGui.QColor("#5FC98A")))
        save_btn.setIconSize(QtCore.QSize(14, 14))
        save_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        save_btn.setShortcut("Ctrl+S")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 13px;
                padding: 5px 14px 5px 8px;
                color: #d0d0d0;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: #383838;
                border-color: #4d4d4d;
                color: #e8e8e8;
            }}
            QPushButton:pressed {{
                background: #333;
            }}
        """)
        save_btn.clicked.connect(self._save_as)
        layout.addWidget(save_btn)

        self._zoom_label = QtWidgets.QPushButton()
        self._zoom_label.setObjectName("zoomLabel")
        self._zoom_label.setFlat(True)
        self._zoom_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._zoom_label.setToolTip(self._tr("editor_fit_tooltip"))
        self._zoom_label.setStyleSheet("""
            QPushButton#zoomLabel {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                color: #999;
                font-size: 11px;
                padding: 2px 10px;
            }
            QPushButton#zoomLabel:hover {
                background: #2d2d2d;
                border-color: #4a4a4a;
                color: #ccc;
            }
            QPushButton#zoomLabel:pressed {
                background: #222;
                border-color: #5FC98A;
            }
        """)
        self._zoom_label.clicked.connect(self._fit_to_viewport)
        layout.addWidget(self._zoom_label)

        # Frameless resize is handled by window-level mouse events
        # (edge/corner detection in mousePressEvent/mouseMoveEvent) — no
        # dedicated corner grip needed.
        return bar

    def _reset_image(self) -> None:
        if not self._modified and not self._text_items and (
            not self._annotations_pixmap or self._annotations_pixmap.isNull()
        ):
            return
            
        self._save_undo(UndoChangeType.FULL)
        self._pil_image = self._original_pil.copy()
        self._clear_annotations()
        self._rebuild_display()
        self._resize_canvas()
        self._center_image_on_canvas()
        self._modified = True

    # ── Tools ─────────────────────────────────────────────────────────────

    def _setup_tools(self) -> None:
        self._tools = {
            "brush": BrushTool(self),
            "highlighter": HighlighterTool(self),
            "eraser": EraserTool(self),
            "mosaic": MosaicTool(self),
            "crop": CropTool(self),
            "text": TextTool(self),
            "rectangle": ShapeTool(self, "rectangle"),
            "ellipse": ShapeTool(self, "ellipse"),
            "line": ShapeTool(self, "line"),
            "sequence": SequenceTool(self),
            "pan": PanTool(self),
            "rotate": RotateTool(self),
            "resize": ResizeTool(self),
        }

    def _activate_tool(self, tool_id: str) -> None:
        for tid, btn in self._tool_buttons.items():
            btn.setChecked(tid == tool_id)
        if tool_id not in self._tools:
            if self._active_tool:
                self._active_tool.on_deactivate()
                self._active_tool = None
            self._canvas.update()
            return
        if self._active_tool:
            self._active_tool.on_deactivate()
        self._active_tool = self._tools[tool_id]
        self._active_tool.on_activate()
        page_idx = self.PAGE_INDEX.get(tool_id, 0)
        self._options_stack.setCurrentIndex(page_idx)
        self._sync_options_from_tool(tool_id)
        self._update_undo_button_visibility()
        self._canvas.update()

    def _update_undo_button_visibility(self) -> None:
        """Show undo/redo only when they're meaningful.

        Rotate and resize sessions are atomic, commit-on-deactivate operations,
        so mid-session undo/redo are disabled (Esc abandons instead). Hiding
        the buttons communicates that: visible = usable.
        """
        hide = self._transform_active
        for w in (self._undo_btn, self._redo_btn):
            w.setVisible(not hide)
        if hide:
            for w in (self._undo_btn, self._redo_btn):
                w.setEnabled(False)
            if self._redo_shift_sc is not None:
                self._redo_shift_sc.setEnabled(False)
        else:
            # Re-sync enabled state with the actual stacks now that the
            # buttons are visible again.
            self._update_undo_buttons()

    def _effective_scale(self) -> float:
        return self._scale / self._dpr

    def _update_tool_cursor(self) -> None:
        if self._active_tool and hasattr(self._active_tool, "size"):
            screen_size = int(self._active_tool.size * self._effective_scale())
            self._canvas.setCursor(_make_circle_cursor(screen_size))

    def _sync_options_from_tool(self, tool_id: str) -> None:
        tool = self._tools.get(tool_id)
        if not tool:
            return
        ow = self._option_widgets
        if hasattr(tool, "color"):
            btn = ow.get((tool_id, "colorBtn"))
            if btn:
                btn.setColor(tool.color)
        slider = ow.get((tool_id, "sizeSlider"))
        if slider and hasattr(tool, "size"):
            slider.blockSignals(True)
            slider.setValue(tool.size)
            slider.blockSignals(False)
        op = ow.get((tool_id, "opacitySlider"))
        if op and hasattr(tool, "color"):
            op.blockSignals(True)
            op.setValue(tool.color.alpha())
            op.blockSignals(False)
        fc = ow.get((tool_id, "fontCombo"))
        if fc and hasattr(tool, "font_family"):
            fc.blockSignals(True)
            idx = fc.findText(tool.font_family)
            if idx >= 0:
                fc.setCurrentIndex(idx)
            fc.blockSignals(False)
        fs = ow.get((tool_id, "fontSizeSpin"))
        if fs and hasattr(tool, "font_size"):
            fs.blockSignals(True)
            fs.setValue(tool.font_size)
            fs.blockSignals(False)
        ft = ow.get((tool_id, "fillBtn"))
        if ft and hasattr(tool, "fill"):
            ft.blockSignals(True)
            ft.setChecked(tool.fill)
            ft.blockSignals(False)
        ae = ow.get((tool_id, "arrowBtn"))
        if ae and hasattr(tool, "arrow_end"):
            ae.blockSignals(True)
            ae.setChecked(tool.arrow_end)
            ae.blockSignals(False)
        da = ow.get((tool_id, "doubleArrowBtn"))
        if da and hasattr(tool, "double_arrow"):
            da.blockSignals(True)
            da.setChecked(tool.double_arrow)
            da.blockSignals(False)

    def _pick_color(self, tool_id: str) -> None:
        tool = self._tools.get(tool_id)
        if not tool or not hasattr(tool, "color"):
            return
        anchor = self._option_widgets.get((tool_id, "colorBtn"))
        popup = _SwatchPopup(self)
        popup.color_selected.connect(
            lambda c, tid=tool_id: self._on_color_picked(tid, c)
        )
        popup.show_near(anchor or self)

    def _on_color_picked(self, tool_id: str, color: QtGui.QColor) -> None:
        tool = self._tools.get(tool_id)
        if not tool or not hasattr(tool, "color"):
            return
        color.setAlpha(tool.color.alpha())
        tool.color = color
        self._sync_options_from_tool(tool_id)

    def _size_range_for(self, tool_id: str) -> tuple[int, int]:
        if tool_id == "mosaic":
            return (2, 40)
        if tool_id == "eraser":
            return (3, 60)
        if tool_id == "highlighter":
            return (5, 80)
        return (1, 50)

    _SIZE_PRESETS: dict[str, tuple[int, int, int]] = {
        "brush": (2, 5, 10),
        "highlighter": (12, 24, 40),
        "eraser": (12, 24, 48),
        "mosaic": (6, 12, 20),
        "rectangle": (2, 5, 10),
        "ellipse": (2, 5, 10),
        "line": (2, 5, 10),
        "sequence": (20, 28, 40),
    }

    def _size_preset_values(self, tool_id: str) -> list[int]:
        lo, hi = self._size_range_for(tool_id)
        vals = list(self._SIZE_PRESETS.get(tool_id, (3, 10, 25)))
        return [min(hi, max(lo, v)) for v in vals]

    def _create_size_presets(self, tool_id: str) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        values = self._size_preset_values(tool_id)
        dot_sizes = [6, 10, 14]

        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        buttons: list[tuple[int, QtWidgets.QToolButton]] = []

        for val, dot in zip(values, dot_sizes):
            b = QtWidgets.QToolButton()
            b.setText("●")
            b.setCheckable(True)
            b.setFixedSize(22, 22)
            b.setToolTip(str(val))
            b.setStyleSheet(
                "QToolButton { background: transparent; border: none; "
                f"color: #888; font-size: {dot}px; }}"
                "QToolButton:hover { color: #ccc; }"
                "QToolButton:checked { color: #5FC98A; }"
            )
            b.clicked.connect(
                lambda _checked=False, v=val, t=tool_id: self._apply_size_preset(t, v)
            )
            group.addButton(b)
            h.addWidget(b)
            buttons.append((val, b))

        self._option_widgets[(tool_id, "sizePresets")] = buttons
        self._option_widgets[(tool_id, "sizePresetGroup")] = group
        return container

    def _apply_size_preset(self, tool_id: str, value: int) -> None:
        slider = self._option_widgets.get((tool_id, "sizeSlider"))
        if slider:
            slider.setValue(value)
        self._sync_size_presets(tool_id, value)

    def _sync_size_presets(self, tool_id: str, value: int) -> None:
        buttons = self._option_widgets.get((tool_id, "sizePresets"))
        if not buttons:
            return
        for val, b in buttons:
            b.blockSignals(True)
            b.setChecked(val == value)
            b.blockSignals(False)

    def _on_size_changed(self, tool_id: str, value: int) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "size"):
            tool.size = value
        if tool and hasattr(tool, "block_size"):
            tool.block_size = value
        if self._active_tool == tool:
            self._update_tool_cursor()
        if tool_id == "text":
            tool._sync_widgets()
        self._sync_size_presets(tool_id, value)

    def _on_opacity_changed(self, tool_id: str, value: int) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "color"):
            c = tool.color
            c.setAlpha(value)
            tool.color = c
            self._sync_options_from_tool(tool_id)

    def _on_double_arrow_changed(self, tool_id: str, checked: bool) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "double_arrow"):
            tool.double_arrow = checked
            if checked and hasattr(tool, "arrow_end") and tool.arrow_end:
                tool.arrow_end = False
                self._sync_options_from_tool(tool_id)

    def _on_arrow_changed(self, tool_id: str, checked: bool) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "arrow_end"):
            tool.arrow_end = checked
            if checked and hasattr(tool, "double_arrow") and tool.double_arrow:
                tool.double_arrow = False
                self._sync_options_from_tool(tool_id)

    def _on_fill_changed(self, tool_id: str, checked: bool) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "fill"):
            tool.fill = checked

    def _on_font_changed(self, tool_id: str, family: str) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "font_family"):
            tool.font_family = family
            # Log if the selected font is non-scalable — these fonts
            # (e.g. legacy .fon bitmap fonts) render at a fixed size
            # regardless of the requested font_size.
            if not QtGui.QFontDatabase.isSmoothlyScalable(family):
                logger.debug(
                    "Non-scalable font selected: %r (will not respond "
                    "to font-size changes for glyphs it lacks)",
                    family,
                )
            if tool_id == "text":
                tool._sync_widgets()

    def _on_font_size_changed(self, tool_id: str, value: int) -> None:
        tool = self._tools.get(tool_id)
        if tool and hasattr(tool, "font_size"):
            # QSpinBox already enforces the [1, MAX_FONT_SIZE] range, so value
            # is always valid — clamp is a no-op kept as a defensive guard.
            clamped = max(1, min(value, self.MAX_FONT_SIZE))
            tool.font_size = clamped
            spin = self._option_widgets.get((tool_id, "fontSizeSpin"))
            if spin and spin.value() != clamped:
                spin.blockSignals(True)
                spin.setValue(clamped)
                spin.blockSignals(False)
            if tool_id == "text":
                tool._sync_widgets()

    # ── Image init & text items ─────────────────────────────────────────

    def _init_from_image(self) -> None:
        self._display_pixmap = _pil_to_qpixmap(self._pil_image)
        img_size = QtCore.QSize(*self._pil_image.size)
        self._annotations_pixmap = QtGui.QPixmap(img_size)
        self._annotations_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._overlay_pixmap = QtGui.QPixmap(img_size)
        self._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._update_status()
        self._resize_canvas()

    def _resize_canvas(self) -> None:
        pm = self._rendered_display_pixmap()
        if not pm:
            return
        vp = self._scroll_area.viewport()
        if not vp:
            return
        vw, vh = vp.width(), vp.height()
        if vw <= 0 or vh <= 0:
            return
        scale = self._effective_scale()
        iw = int(pm.width() * scale)
        ih = int(pm.height() * scale)
        pad_w = max(vw, iw) * 0.15
        pad_h = max(vh, ih) * 0.15
        cw = max(int(vw * 1.05), iw + int(pad_w) * 2)
        ch = max(int(vh * 1.05), ih + int(pad_h) * 2)
        self._canvas.resize(cw, ch)
        text_tool = self._tools.get("text")
        if text_tool:
            text_tool._sync_widgets()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        # The system sends a flood of resize events while dragging the border;
        # debounce the (expensive) canvas relayout so it only runs once the
        # user pauses.
        self._resize_debounce.start()
        # Keep the active transform tool's floating buttons pinned to the
        # viewport bottom on window resize.
        from .editor.tools.transform import _position_action_buttons
        if self._transform_active and self._active_tool is not None:
            _position_action_buttons(self._active_tool)

    def _on_resize_settled(self) -> None:
        """Called 50 ms after the last resize event — relayout the canvas."""
        self._resize_canvas()
        self._center_image_on_canvas()

    def _center_image_on_canvas(self) -> None:
        h_bar = self._scroll_area.horizontalScrollBar()
        v_bar = self._scroll_area.verticalScrollBar()
        h_bar.setValue(round((h_bar.minimum() + h_bar.maximum()) / 2))
        v_bar.setValue(round((v_bar.minimum() + v_bar.maximum()) / 2))

    def _fit_to_viewport(self) -> None:
        """Scale the image to fit within the viewport with 10% visual margin.

        Small images are capped at effective 1.0 (no upscaling).
        The canvas pasteboard (15 % padding per side in _resize_canvas)
        ensures the Pan tool has scroll range even at fit zoom.
        """
        pm = self._rendered_display_pixmap()
        if not pm:
            return
        vp = self._scroll_area.viewport()
        if not vp:
            return
        vw, vh = vp.width(), vp.height()
        if vw <= 0 or vh <= 0 or pm.width() <= 0 or pm.height() <= 0:
            return
        fit_effective = min(
            vw * 0.90 / pm.width(),
            vh * 0.90 / pm.height(),
            1.0,  # don't upscale small images
        )
        # Effective range [0.10 … 5.0], DPR-adjusted so the user sees the
        # same limits regardless of screen density.
        self._scale = max(0.10, fit_effective) * self._dpr
        self._resize_canvas()
        self._center_image_on_canvas()
        self._update_zoom_label()
        self._canvas.update()

    def _rebuild_display(self) -> None:
        self._display_pixmap = _pil_to_qpixmap(self._pil_image)
        new_size = QtCore.QSize(*self._pil_image.size)
        if self._annotations_pixmap is None or self._annotations_pixmap.size() != new_size:
            self._annotations_pixmap = QtGui.QPixmap(new_size)
            self._annotations_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        if self._overlay_pixmap is None or self._overlay_pixmap.size() != new_size:
            self._overlay_pixmap = QtGui.QPixmap(new_size)
            self._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._update_status()
        self._canvas.update()

    def _clear_annotations(self) -> None:
        if self._annotations_pixmap:
            self._annotations_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        if self._overlay_pixmap:
            self._overlay_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        self._text_items.clear()

    def _flatten_text(self) -> None:
        """Render all text items onto the annotations pixmap and clear them.

        After this, text is baked into the annotation pixels — no longer
        individually editable, but now survives whole-image transforms (crop,
        rotate, resize) that only operate on pixmaps, without needing per-item
        coordinate gymnastics.
        """
        if not self._text_items:
            return
        target = self._annotations_pixmap
        if target is None or target.isNull():
            return
        painter = QtGui.QPainter(target)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        for item in self._text_items:
            if not item.text:
                continue
            font = QtGui.QFont(item.font_family)
            font.setPixelSize(item.font_size)
            painter.setFont(font)
            # Use the SAME baseline math as the live canvas render and
            # _get_composite_pixmap: img_pos is the text's TOP, baseline is
            # img_pos.y + ascent. Baking with img_pos directly as the baseline
            # would shift baked text down by one ascent vs. its live position —
            # visible as a jump the instant annotations are flattened.
            metrics = painter.fontMetrics()
            baseline = QtCore.QPointF(item.img_pos.x(), item.img_pos.y() + metrics.ascent())
            _draw_outlined_text(painter, baseline, item.text, font)
        painter.end()
        self._text_items.clear()

    def _composite_annotations_into_image(self) -> None:
        """Bake annotations + text into _pil_image, then clear annotation layers.

        After this the base image holds all annotation pixel data and the
        annotation layer is empty. Call at the start of a transform session
        (rotate / resize / crop) so the transform operates on a single merged
        image — no separate layer to keep in sync, no per-item coordinate
        gymnastics, and baked text rotates/scales as pixels (sidestepping the
        "which angle should an editable text box be?" problem).
        """
        self._flatten_text()  # text items → annotations_pixmap pixels
        if self._annotations_pixmap and not self._annotations_pixmap.isNull():
            pm = _pil_to_qpixmap(self._pil_image)
            painter = QtGui.QPainter(pm)
            painter.drawPixmap(0, 0, self._annotations_pixmap)
            painter.end()
            merged = _qpixmap_to_pil(pm)
            # _qpixmap_to_pil returns RGBA unconditionally (raw RGBA8888 copy),
            # so _pil_image keeps a consistent mode across the session and undo
            # byte-compares — no PNG alpha-drop to correct for.
            self._pil_image = merged
            self._annotations_pixmap.fill(QtCore.Qt.GlobalColor.transparent)

    # ── Undo / Redo ───────────────────────────────────────────────────────

    def _save_undo(
        self,
        change_type: UndoChangeType = UndoChangeType.FULL,
        region_bounds: Optional[QtCore.QRect] = None,
        region_pixels: Optional[bytes] = None,
        rotate_angle: Optional[float] = None,
    ) -> None:
        if change_type == UndoChangeType.REGION:
            assert region_bounds is not None and region_pixels is not None
            entry = _UndoEntry(
                change_type, region_bounds=region_bounds, region_pixels=region_pixels,
            )
        elif change_type == UndoChangeType.TEXT:
            entry = _UndoEntry(change_type, text_items=self._text_items)
        elif change_type == UndoChangeType.ANNOTATIONS:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            entry = _UndoEntry(change_type, annot_pxm=annot_copy, text_items=self._text_items)
        else:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            entry = _UndoEntry(change_type, pil_img=self._pil_image,
                               annot_pxm=annot_copy, text_items=self._text_items,
                               rotate_angle=rotate_angle)

        self._undo_stack.append(entry)
        self._enforce_stack_limits(self._undo_stack)
        self._redo_stack.clear()
        self._update_undo_buttons()

    def _enforce_stack_limits(self, stack: list[_UndoEntry]) -> None:
        while len(stack) > self.MAX_UNDO_STEPS:
            stack.pop(0)

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        self._capture_current_for_redo(entry)
        self._apply_undo_entry(entry)
        self._update_undo_buttons()

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        entry = self._redo_stack.pop()
        self._capture_current_for_undo(entry)
        self._apply_undo_entry(entry)
        self._update_undo_buttons()

    def _capture_current_for_redo(self, undone_entry: _UndoEntry) -> None:
        ct = undone_entry.change_type
        if ct == UndoChangeType.FULL:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.FULL, pil_img=self._pil_image,
                annot_pxm=annot_copy, text_items=self._text_items,
                rotate_angle=self._rotate_cumulative_angle if self._transform_active else None
            ))
        elif ct == UndoChangeType.ANNOTATIONS:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.ANNOTATIONS, annot_pxm=annot_copy, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.TEXT:
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.TEXT, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.REGION:
            b = undone_entry.region_bounds
            current = self._pil_image.crop(
                (b.x(), b.y(), b.x() + b.width(), b.y() + b.height())
            )
            self._redo_stack.append(_UndoEntry(
                UndoChangeType.REGION,
                region_bounds=QtCore.QRect(b),
                region_pixels=current.tobytes(),
            ))
        self._enforce_stack_limits(self._redo_stack)

    def _capture_current_for_undo(self, redone_entry: _UndoEntry) -> None:
        ct = redone_entry.change_type
        if ct == UndoChangeType.FULL:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.FULL, pil_img=self._pil_image,
                annot_pxm=annot_copy, text_items=self._text_items,
                rotate_angle=self._rotate_cumulative_angle if self._transform_active else None
            ))
        elif ct == UndoChangeType.ANNOTATIONS:
            annot_copy = self._annotations_pixmap.copy() if self._annotations_pixmap else None
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.ANNOTATIONS, annot_pxm=annot_copy, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.TEXT:
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.TEXT, text_items=self._text_items,
            ))
        elif ct == UndoChangeType.REGION:
            b = redone_entry.region_bounds
            current = self._pil_image.crop(
                (b.x(), b.y(), b.x() + b.width(), b.y() + b.height())
            )
            self._undo_stack.append(_UndoEntry(
                UndoChangeType.REGION,
                region_bounds=QtCore.QRect(b),
                region_pixels=current.tobytes(),
            ))
        self._enforce_stack_limits(self._undo_stack)

    def _apply_undo_entry(self, entry: _UndoEntry) -> None:
        if entry.change_type == UndoChangeType.FULL:
            if entry.pil_image is not None:
                self._pil_image = entry.pil_image
            if entry.annotations_pixmap is not None:
                self._annotations_pixmap = entry.annotations_pixmap
            self._text_items = entry.text_items[:] if entry.text_items is not None else []
            self._rebuild_display()
        elif entry.change_type == UndoChangeType.ANNOTATIONS:
            if entry.annotations_pixmap is not None:
                self._annotations_pixmap = entry.annotations_pixmap
            self._text_items = entry.text_items[:] if entry.text_items is not None else []
            self._canvas.update()
        elif entry.change_type == UndoChangeType.TEXT:
            self._text_items = entry.text_items[:] if entry.text_items is not None else []
            self._canvas.update()
        elif entry.change_type == UndoChangeType.REGION:
            b = entry.region_bounds
            if b is not None and entry.region_pixels is not None:
                restored = Image.frombytes(
                    self._pil_image.mode,
                    (b.width(), b.height()),
                    entry.region_pixels,
                )
                self._pil_image.paste(restored, (b.x(), b.y()))
                self._rebuild_display()
        self._modified = True

    def _update_undo_buttons(self) -> None:
        # During a transform session undo/redo are hidden (see
        # _update_undo_button_visibility); don't fight that here.
        if self._transform_active:
            return
        can_undo = len(self._undo_stack) > 0
        can_redo = len(self._redo_stack) > 0
        self._undo_btn.setEnabled(can_undo)
        self._redo_btn.setEnabled(can_redo)
        self._redo_shift_sc.setEnabled(can_redo)

    # ── Copy to clipboard ─────────────────────────────────────────────────

    def _commit_active_text_edit(self) -> None:
        text_tool = self._tools.get("text")
        editing_widget = getattr(text_tool, "_editing_widget", None) if text_tool else None
        if editing_widget:
            editing_widget.commit_edit()

    def _get_composite_pixmap(self) -> QtGui.QPixmap:
        self._commit_active_text_edit()
        res = _pil_to_qpixmap(self._pil_image)
        painter = QtGui.QPainter(res)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        if self._annotations_pixmap and not self._annotations_pixmap.isNull():
            painter.drawPixmap(0, 0, self._annotations_pixmap)
        for item in self._text_items:
            if not item.text.strip():
                continue
            font = QtGui.QFont(item.font_family)
            font.setPixelSize(item.font_size)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            baseline = QtCore.QPointF(item.img_pos.x(), item.img_pos.y() + metrics.ascent())
            _draw_outlined_text(painter, baseline, item.text, font)
        painter.end()
        return res

    def _copy_to_clipboard(self) -> None:
        try:
            pixmap = self._get_composite_pixmap()
            QtWidgets.QApplication.clipboard().setPixmap(pixmap)
            from .toast import show_toast
            show_toast(self._tr("editor_copied"))
        except Exception:
            logger.exception("Copy to clipboard failed")

    # ── Status ────────────────────────────────────────────────────────────

    def _update_status(self) -> None:
        if self._display_pixmap:
            w, h = self._display_pixmap.width(), self._display_pixmap.height()
            self._status_label.setText(f"{w} × {h}")
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        pct = round(self._effective_scale() * 100)
        label = self._tr("editor_zoom_label", zoom=pct)
        self._zoom_label.setText(f"⊞  {label}")

    # ── Save / Close ──────────────────────────────────────────────────────

    def _save_as(self) -> None:
        import time
        from ..config import get_last_save_directory, update_last_save_directory
        default_dir = get_last_save_directory(get_config_path())
        ts = time.strftime('%Y%m%d_%H%M%S')
        ms = int(time.time() * 1000) % 1000
        default_name = f"HushSnap_{ts}_{ms:03d}.png"
        file_path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self._tr("editor_save_as"),
            str(Path(default_dir) / default_name),
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)",
        )
        if not file_path_str:
            return
        try:
            file_path = Path(file_path_str)
            update_last_save_directory(file_path.parent, get_config_path())
            composite = self._get_composite_pixmap()
            save_img = _qpixmap_to_pil(composite)
            if file_path_str.lower().endswith((".jpg", ".jpeg")):
                save_img = save_img.convert("RGB")
            save_img.save(file_path_str)
            self._modified = False
            try:
                from .toast import show_toast
                show_toast(self._tr("editor_saved"))
            except Exception:
                pass
        except Exception:
            logger.exception("Failed to save image")

    # ── Whole-image transforms (rotate / resize) ────────────────────────────

    def _preview_active(self) -> bool:
        """True while a transform preview is overriding the canvas display."""
        return self._preview_pixmap is not None or self._preview_angle is not None

    def _rendered_display_pixmap(self) -> Optional[QtGui.QPixmap]:
        """Pixmap the canvas should render for layout/offset math.

        During a resize preview this is the swapped preview pixmap; during a
        rotation preview (paint-time) the original is returned and the canvas
        applies the angle itself.
        """
        if self._transform_active and self._rotate_base_pixmap is not None:
            return self._rotate_base_pixmap
        return self._preview_pixmap if self._preview_pixmap is not None else self._display_pixmap

    def _rendered_annotations_pixmap(self) -> Optional[QtGui.QPixmap]:
        """Annotations pixmap the canvas should render.

        During a transform session the annotation layer is empty (annotations
        were baked into the merged image at session start), so this just
        returns the empty layer — a no-op when drawn. Kept as an indirection
        so the canvas stays decoupled from session state.
        """
        return self._annotations_pixmap

    def _set_preview_pixmap(self, pm: Optional[QtGui.QPixmap]) -> None:
        """Swap the pixmap the canvas renders, or None to restore the real image.

        Used by the resize tool for live preview. Only the real PIL transform
        runs on commit; this just sets a stand-in display.
        """
        self._preview_pixmap = pm
        self._resize_canvas()
        self._canvas.update()

    # ── Rotation (paint-time preview) ──────────────────────────────────────
    #
    # The rotate tool never swaps a pixmap: it sets _preview_angle and the
    # canvas applies the rotation in paintEvent. So a drag costs only a
    # canvas.update() per move — no QPixmap allocation, no widget resize —
    # which is why it doesn't flicker. The canvas is sized to the image
    # diagonal for the duration of the session (see _resize_canvas) so rotated
    # corners stay visible.

    # ── Transform session (shared by crop / rotate / resize) ────────────────
    #
    # Every image transform bakes annotations + text into the base image once
    # on enter (text becomes non-editable pixels for the duration). The session
    # is one atomic undo unit: a FULL undo entry is pushed at session start
    # (pre-bake state — clean image + editable annotations + text).  On commit
    # the entry stays on the stack (one undo restores the pre-transform state);
    # on cancel it is popped and applied directly (no redo — the session never
    # "happened").  The undo stack itself holds the snapshot, so we don't carry
    # separate pre-state fields.

    def _begin_transform_session(self) -> None:
        """Push a FULL undo entry, then bake annotations + text.

        Called by every transform tool's on_activate. The undo entry captures
        the pre-bake state (clean image + editable annotations + text). After
        this the image holds all annotation pixels and the annotation/text
        layers are empty, so the transform operates on one merged image.
        """
        # Push BEFORE baking — this records the clean, editable pre-state.
        self._save_undo()  # defaults to FULL
        self._composite_annotations_into_image()
        # Rebuild _display_pixmap from the now-merged PIL image — otherwise
        # the canvas still shows the pre-bake pixmap and text/strokes appear
        # to vanish (the crop-on-enter bug).
        self._rebuild_display()
        self._transform_active = True
        self._update_undo_button_visibility()

    def _commit_transform_session(self) -> None:
        """End the session, leaving the undo entry on the stack.

        The entry that _begin_transform_session pushed stays — one undo
        restores the editable pre-transform state.  No-op if the session was
        already cancelled.
        """
        if not self._transform_active:
            return
        self._transform_active = False
        self._update_undo_button_visibility()

    def _cancel_transform_session(self) -> None:
        """Pop the pre-bake undo entry and apply it; no redo.

        Restores the state from before the session started (clean image +
        editable annotations + text) as if nothing happened.  The popped entry
        is NOT pushed to the redo stack — the session never "happened".
        """
        if not self._transform_active:
            return
        if self._undo_stack:
            entry = self._undo_stack.pop()
            self._apply_undo_entry(entry)
        self._transform_active = False
        self._update_undo_button_visibility()

    # ── Rotation (paint-time preview) ──────────────────────────────────────

    def _begin_rotate_session(self) -> None:
        """Bake + capture a rotation resampling base (cumulative, no compound)."""
        self._preview_angle = 0.0
        self._begin_transform_session()
        # Rotate samples from this merged base every release so repeated
        # rotations don't compound. Annotations are baked in above, so there's
        # no separate annotation base to track.
        self._rotate_base_image = self._pil_image.copy()
        self._rotate_base_pixmap = _pil_to_qpixmap(self._rotate_base_image)
        self._rotate_cumulative_angle = 0.0
        self._canvas.update()

    def _end_rotate_session(self) -> None:
        """Clear rotate-local state; the undo entry stays on the stack."""
        self._rotate_base_image = None
        self._rotate_base_pixmap = None
        self._rotate_cumulative_angle = 0.0
        self._preview_angle = None
        self._commit_transform_session()
        self._resize_canvas()
        self._center_image_on_canvas()
        self._canvas.update()

    def _cancel_rotate_session(self) -> None:
        """Esc: clear rotate-local state, then restore pre-state via shared cancel."""
        self._rotate_base_image = None
        self._rotate_base_pixmap = None
        self._rotate_cumulative_angle = 0.0
        self._preview_angle = None
        self._cancel_transform_session()
        self._resize_canvas()
        self._center_image_on_canvas()
        self._canvas.update()

    def _set_rotation_preview(self, angle: float) -> None:
        """Set the live rotation angle (degrees, clockwise). 0 = upright."""
        self._preview_angle = angle
        self._canvas.update()

    def _apply_rotation(self, angle: float, expand: bool) -> None:
        """Apply a rotation of *angle* degrees (cumulative from session base).

        No undo entry is pushed here: the whole rotate session is a single
        atomic undo unit, committed once when the tool is deactivated (see
        _end_rotate_session). Mid-session undo/redo are disabled, so there's
        nothing to record per drag.
        """
        self._preview_angle = angle
        try:
            img = self._rotate_base_image
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            # Rotate the merged PIL image (annotations were baked in at session
            # start). The annotation layer is empty, so there's nothing else to
            # rotate — image and annotations stay locked as one.
            rotated = img.rotate(
                -angle, expand=expand, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0)
            )
            # Trim fully-transparent outer borders back to the content bbox.
            # expand=True grows the canvas to fit the rotated image, and each
            # new rotate session captures the previous expanded image as its
            # base — so without trimming, repeated rotate/commit cycles
            # compound the transparent padding (canvas ×(cos+sin) each time)
            # and the checkerboard balloons. getbbox() on RGBA excludes the
            # (0,0,0,0) fill, so this recovers the true content bounding box.
            # After a single rotation the content touches all four edges, so
            # this is a no-op then; it only ever strips the compounded outer
            # padding from second rotation onward. (Safe for HushSnap:
            # transparency here only ever comes from rotation.)
            bbox = rotated.getbbox()
            if bbox:
                rotated = rotated.crop(bbox)
            self._pil_image = rotated

            self._rotate_cumulative_angle = angle
            self._rebuild_display()
            self._modified = True
        except Exception:
            logger.exception("Failed to apply rotation")
            self._canvas.update()

    # ── Resize (resample from base, no compound quality loss) ───────────────

    def _begin_resize_session(self) -> None:
        """Bake + capture a resize resampling base (no compound quality loss)."""
        self._begin_transform_session()  # already rebuilds _display_pixmap
        self._resize_base_image = self._pil_image.copy()
        self._set_preview_pixmap(None)

    def _end_resize_session(self) -> None:
        """Commit one undo entry if resized; clear resize-local state."""
        self._resize_base_image = None
        self._set_preview_pixmap(None)
        self._commit_transform_session()

    def _cancel_resize_session(self) -> None:
        """Esc: restore pre-resize state via the shared cancel."""
        self._resize_base_image = None
        self._set_preview_pixmap(None)
        self._cancel_transform_session()

    def _set_resize_preview(self, width: float, height: float) -> None:
        """Live resize preview: a scaled copy of the image + annotations.

        The preview pixmap is a *composite* of the scaled display pixmap and
        the scaled annotations layer (text was baked into annotations by
        _flatten_text at session start).  Because annotations are folded into
        the preview pixmap, the canvas must NOT draw annotations again while
        this preview is active — see the angle/preview branch in
        EditorCanvas.paintEvent.
        """
        pm = self._display_pixmap
        if not pm or pm.isNull() or width <= 0 or height <= 0:
            self._set_preview_pixmap(None)
            return
        w, h = int(round(width)), int(round(height))
        scaled = pm.scaled(
            w, h,
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        annot = self._annotations_pixmap
        if annot and not annot.isNull():
            annot_scaled = annot.scaled(
                w, h,
                QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            painter = QtGui.QPainter(scaled)
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(0, 0, annot_scaled)
            painter.end()
        self._set_preview_pixmap(scaled)

    def _apply_resize(self, width: int, height: int) -> None:
        """Resize to *width* × *height*, resampling from the session base image.

        Resampling from the base (not the already-resized _pil_image) every
        time is what keeps repeated shrink/grow from compounding quality loss.
        No undo entry is pushed here — the whole resize session is one atomic
        undo unit, committed on tool deactivate (see _end_resize_session).

        Annotations were baked into the base image at session start, so only
        the PIL image is resampled; _rebuild_display recreates an empty
        annotation layer at the new size.
        """
        if width <= 0 or height <= 0:
            return
        base = self._resize_base_image if self._transform_active else self._pil_image
        if base is None:
            return
        try:
            # Resample the merged image from the high-quality base.
            self._pil_image = base.resize((width, height), Image.LANCZOS)
            self._preview_pixmap = None

            self._rebuild_display()
            self._resize_canvas()
            # Don't hard-recenter: keep the image where it is so the view doesn't
            # jump. _resize_canvas already sized the canvas; the existing scroll
            # position stays, which feels more stable than snapping to center.
            self._modified = True
        except Exception:
            logger.exception("Failed to apply resize")
            self._preview_pixmap = None
            self._canvas.update()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # Persist the window geometry so the next editor opens at the same
        # size/position (when on the same screen — see show_image_editor).
        try:
            g = self.geometry()
            set_editor_window_geometry(g.x(), g.y(), g.width(), g.height(),
                                       get_config_path())
        except Exception:
            logger.exception("Failed to persist editor window geometry")
        self._cleanup_resources()
        event.accept()
        super().closeEvent(event)

    def _cleanup_resources(self) -> None:
        # Commit/release any in-progress inline text editor first, so the
        # widget↔tool reference cycle is broken and the widget is scheduled
        # for deletion via the normal path rather than waiting on Qt parent
        # destruction + cyclic GC.
        self._commit_active_text_edit()
        self._active_tool = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._text_items.clear()
        self._display_pixmap = None
        self._annotations_pixmap = None
        self._overlay_pixmap = None
        self._preview_pixmap = None
        self._preview_angle = None
        self._transform_active = False
        self._rotate_base_image = None
        self._rotate_base_pixmap = None
        self._rotate_cumulative_angle = 0.0
        self._resize_base_image = None
        self._pil_image = None
        self._original_pil = None
        if self._tools:
            self._tools.clear()
        gc.collect()

# ── Public entry point ───────────────────────────────────────────────────────

def show_image_editor(
    pil_image: Image.Image,
    translate_fn: Callable[[str], str],
) -> ImageEditorWindow:
    """Create and show the image editor window for the given PIL image.

    Opens on the screen under the cursor. Geometry (size + position) is
    restored from the last session when the stored window lies on the
    cursor's screen; otherwise the remembered size is reused but the
    window is centered on the current screen (the standard multi-monitor
    editor behavior — never pop a window onto a screen the user isn't on).
    """
    win = ImageEditorWindow(pil_image, translate_fn)
    win.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    win._resolve_target_screen()
    target = win._target_screen

    remembered = get_editor_window_geometry(get_config_path())

    if target is not None:
        avail = target.availableGeometry()
        if remembered is not None and avail.contains(
            QtCore.QPoint(remembered["x"], remembered["y"])
        ):
            # Last session's window is on the cursor's screen → full restore,
            # but still respect the minimum size and the screen's available area
            # (a stale entry could be smaller than the current minimum or poke
            # past the right/bottom edge on a since-shrunk display).
            w = max(_EDITOR_MIN_W, min(remembered["w"], avail.width()))
            h = max(_EDITOR_MIN_H, min(remembered["h"], avail.height()))
            x = min(remembered["x"], avail.x() + avail.width() - w)
            y = min(remembered["y"], avail.y() + avail.height() - h)
        else:
            # Reuse remembered size (clamped to this screen), center here.
            w = max(_EDITOR_MIN_W, min(
                remembered["w"] if remembered else _EDITOR_DEFAULT_W, avail.width()))
            h = max(_EDITOR_MIN_H, min(
                remembered["h"] if remembered else _EDITOR_DEFAULT_H, avail.height()))
            x = avail.x() + (avail.width() - w) // 2
            y = avail.y() + (avail.height() - h) // 2
        # Assign the target screen before show() — otherwise Windows may
        # place the window on the primary screen instead of the cursor's.
        _ = win.winId()
        wh = win.windowHandle()
        if wh is not None and target is not None:
            wh.setScreen(target)
        win.resize(w, h)
        win.move(x, y)
    win.show()
    # Defer fit until the layout is settled — the viewport has zero
    # dimensions during __init__ (before show()).
    QtCore.QTimer.singleShot(0, win._fit_to_viewport)
    return win


