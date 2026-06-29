#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HushSnap MSIX Asset Generator.
Uses PIL to render high-quality PNG visual assets directly from the project's assets/logo.png.
"""

import os
import sys
from PIL import Image, ImageDraw, ImageChops

# Corner radius as a fraction of the icon edge.
# 0.18 is the Windows 11 Fluent Design standard corner radius (softer squircle).
RADIUS_RATIO = 0.18

def _rounded_mask(size: int, ratio: float) -> Image.Image:
    """Single-channel mask: white rounded square on black."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    radius = max(1.0, size * ratio)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask

def generate_assets(logo_path, output_dir):
    if not os.path.exists(logo_path):
        # Fall back to checking relative directory if absolute doesn't exist
        logo_path = os.path.join(os.path.dirname(__file__), "..", "assets", "logo.png")
        if not os.path.exists(logo_path):
            print(f"Error: Logo source file '{logo_path}' not found.")
            sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    src_img = Image.open(logo_path).convert("RGBA")

    # Asset specifications: (filename, width, height, icon_target_size, bg_color, is_rounded)
    specs = [
        ("StoreLogo.png", 50, 50, 50, None, True),
        ("Square150x150Logo.png", 150, 150, 150, None, True),
        ("Square44x44Logo.png", 44, 44, 44, None, True),
        ("Wide310x150Logo.png", 310, 150, 150, None, True),
        ("SplashScreen.png", 620, 300, 120, (30, 30, 36, 255), True), # #1e1e24
        
        # Standard targetsize assets (plated)
        ("Square44x44Logo.targetsize-16.png", 16, 16, 16, None, True),
        ("Square44x44Logo.targetsize-24.png", 24, 24, 24, None, True),
        ("Square44x44Logo.targetsize-32.png", 32, 32, 32, None, True),
        ("Square44x44Logo.targetsize-48.png", 48, 48, 48, None, True),
        ("Square44x44Logo.targetsize-256.png", 256, 256, 256, None, True),

        # Unplated targetsize assets (for transparent background on taskbar)
        ("Square44x44Logo.targetsize-16_altform-unplated.png", 16, 16, 16, None, True),
        ("Square44x44Logo.targetsize-24_altform-unplated.png", 24, 24, 24, None, True),
        ("Square44x44Logo.targetsize-32_altform-unplated.png", 32, 32, 32, None, True),
        ("Square44x44Logo.targetsize-48_altform-unplated.png", 48, 48, 48, None, True),
        ("Square44x44Logo.targetsize-256_altform-unplated.png", 256, 256, 256, None, True),
    ]

    print(f"Generating MSIX visual assets under '{output_dir}'...")

    for filename, w, h, icon_sz, bg_color, is_rounded in specs:
        # 1. Create target canvas
        canvas = Image.new("RGBA", (w, h), bg_color or (0, 0, 0, 0))

        # 2. Resize source logo to the target icon size using LANCZOS
        resized_icon = src_img.resize((icon_sz, icon_sz), Image.Resampling.LANCZOS)

        # 3. Apply rounded mask if requested
        if is_rounded:
            mask = _rounded_mask(icon_sz, RADIUS_RATIO)
            # Re-apply alpha channel multiplication so we mask transparent corners
            orig_alpha = resized_icon.split()[3]
            new_alpha = ImageChops.multiply(orig_alpha, mask)
            resized_icon.putalpha(new_alpha)

        # 4. Calculate centering coordinates
        x = (w - icon_sz) // 2
        y = (h - icon_sz) // 2

        # 5. Paste the icon onto the canvas
        canvas.paste(resized_icon, (x, y), resized_icon)

        # 6. Save as PNG
        target_path = os.path.join(output_dir, filename)
        canvas.save(target_path, "PNG")
        print(f"  [Created] {filename} ({w}x{h})")

    print("MSIX assets generation completed successfully.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        # Try to locate logo.png automatically if not enough arguments
        logo_p = os.path.join(os.path.dirname(__file__), "..", "assets", "logo.png")
        if not os.path.exists(logo_p):
            print("Usage: python generate_msix_assets.py <path_to_logo_png> <output_directory>")
            sys.exit(1)
        out_d = os.path.join(os.path.dirname(__file__), "..", "build", "msix_stage", "Assets")
    else:
        input_path = sys.argv[1]
        if input_path.endswith(".ico"):
            logo_p = os.path.join(os.path.dirname(input_path), "assets", "logo.png")
            if not os.path.exists(logo_p):
                logo_p = os.path.join(os.path.dirname(input_path), "logo.png")  # fallback
        else:
            logo_p = input_path
        out_d = sys.argv[2]

    generate_assets(logo_p, out_d)
