"""Shared SVG icon loader for UI widgets.

Centralizes the read-currentColor-replace-render pattern that was previously
duplicated in thumbnail.py, ocr_popup.py, and settings_dialog.py. All three
rendered the same SVG files the same way; the only differences were the
number of color variants (one vs. two) and the pixmap size.
"""

import os

from PyQt6 import QtCore, QtGui, QtSvg


def load_svg_icon(name, normal_color, active_color=None, size=24):
    """Load an SVG icon from the ``ui/icons`` directory, tinted per color.

    Args:
        name (str): Icon file name without the ``.svg`` extension.
        normal_color (str): Hex color applied to the ``currentColor`` token
            for the icon's normal (resting) pixmap.
        active_color (str | None): If given, a second pixmap is added in
            ``QIcon.Mode.Active`` so the icon recolors on hover/press.
            When ``None``, a single-color icon is returned.
        size (int): Edge length in pixels of the rendered pixmap.

    Returns:
        QtGui.QIcon: Empty QIcon if the SVG file is missing.
    """
    icons_dir = os.path.join(os.path.dirname(__file__), "icons")
    path = os.path.join(icons_dir, f"{name}.svg")
    if not os.path.isfile(path):
        return QtGui.QIcon()

    def _render(color_str):
        with open(path, "r", encoding="utf-8") as f:
            svg = f.read().replace("currentColor", color_str)
        renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        renderer.render(p)
        p.end()
        return pm

    if active_color is None:
        return QtGui.QIcon(_render(normal_color))

    icon = QtGui.QIcon()
    icon.addPixmap(_render(normal_color), QtGui.QIcon.Mode.Normal)
    icon.addPixmap(_render(active_color), QtGui.QIcon.Mode.Active)
    return icon
