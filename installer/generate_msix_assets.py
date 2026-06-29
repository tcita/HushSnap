#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HushSnap MSIX Asset Generator.
Uses PyQt6 to render high-quality PNG visual assets from the project's hushsnap.ico file.
"""

import os
import sys

# Disable High DPI scaling to prevent QIcon from returning device-pixel scaled pixmaps
# which would cause clipping and off-center alignment on high-DPI screens.
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
os.environ["QT_SCALE_FACTOR"] = "1"

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPixmap, QPainter, QColor


def generate_assets(icon_path, output_dir):
    # Ensure QApplication is initialized for QPixmap/QPainter
    app = QGuiApplication(sys.argv)

    if not os.path.exists(icon_path):
        print(f"Error: Icon file '{icon_path}' not found.")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    icon = QIcon(icon_path)

    # Asset specifications: (filename, width, height, icon_target_size, bg_color)
    # If bg_color is None, background is transparent.
    #
    # icon_target_size is set to the canvas's short edge so the (square) logo
    # fills the tile edge-to-edge with no transparent margin. Any transparent
    # margin is filled by the BackgroundColor declared in AppxManifest's
    # VisualElements (transparent here) — independent of the system theme.
    # Wide310x150Logo is a banner (2:1), so the logo fills the 150px height
    # and centers horizontally; SplashScreen keeps a dark opaque background.
    specs = [
        ("StoreLogo.png", 50, 50, 50, None),
        ("Square150x150Logo.png", 150, 150, 150, None),
        ("Square44x44Logo.png", 44, 44, 44, None),
        ("Wide310x150Logo.png", 310, 150, 150, None),
        ("SplashScreen.png", 620, 300, 120, QColor("#1e1e24")),
        
        # Standard targetsize assets (plated)
        ("Square44x44Logo.targetsize-16.png", 16, 16, 16, None),
        ("Square44x44Logo.targetsize-24.png", 24, 24, 24, None),
        ("Square44x44Logo.targetsize-32.png", 32, 32, 32, None),
        ("Square44x44Logo.targetsize-48.png", 48, 48, 48, None),
        ("Square44x44Logo.targetsize-256.png", 256, 256, 256, None),

        # Unplated targetsize assets (for transparent background on taskbar)
        ("Square44x44Logo.targetsize-16_altform-unplated.png", 16, 16, 16, None),
        ("Square44x44Logo.targetsize-24_altform-unplated.png", 24, 24, 24, None),
        ("Square44x44Logo.targetsize-32_altform-unplated.png", 32, 32, 32, None),
        ("Square44x44Logo.targetsize-48_altform-unplated.png", 48, 48, 48, None),
        ("Square44x44Logo.targetsize-256_altform-unplated.png", 256, 256, 256, None),
    ]

    print(f"Generating MSIX visual assets under '{output_dir}'...")

    for filename, w, h, icon_sz, bg_color in specs:
        # Create target pixmap
        pixmap = QPixmap(w, h)
        if bg_color is not None:
            pixmap.fill(bg_color)
        else:
            pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Get actual icon pixmap for target size
        icon_pixmap = icon.pixmap(QSize(icon_sz, icon_sz))

        # Calculate centering positions
        x = (w - icon_pixmap.width()) // 2
        y = (h - icon_pixmap.height()) // 2

        # Draw icon
        painter.drawPixmap(x, y, icon_pixmap)
        painter.end()

        # Save output file
        target_path = os.path.join(output_dir, filename)
        if pixmap.save(target_path, "PNG"):
            print(f"  [Created] {filename} ({w}x{h})")
        else:
            print(f"  [Error] Failed to save {filename}")

    print("MSIX assets generation completed successfully.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python generate_msix_assets.py <path_to_ico> <output_directory>")
        sys.exit(1)

    icon_p = sys.argv[1]
    out_d = sys.argv[2]
    generate_assets(icon_p, out_d)
