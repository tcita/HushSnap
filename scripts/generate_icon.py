#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Regenerate hushsnap.ico from assets/logo.png with rounded-square corners.

The source logo is an edge-to-edge opaque dark square with the brand mark
in the center. Windows renders this square against the taskbar / Store /
tray, where opaque square corners read as a hard black box. (Separately,
an MSIX tile's transparent padding is filled by the BackgroundColor
declared in the manifest — transparent here — so the background respects the
system theme.)

This script re-masks the logo as a rounded square: the four corners become
transparent so the underlying surface shows through, giving the modern
"app icon" look. It emits a multi-size .ico (16–256) so the taskbar,
Alt-Tab, and the Store each get a properly-scaled entry.

Usage:
    python scripts/generate_icon.py            # writes ./hushsnap.ico
    python scripts/generate_icon.py -o out.ico
    python scripts/generate_icon.py --ratio 0.18
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

# ICO sizes bundled into the .ico. Matches the 7 entries the previous
# hand-made hushsnap.ico carried, so Windows' per-context size selection
# (16 for small taskbar, 24/32 for taskbar+Alt-Tab, 48/256 for jump list /
# large icons) all resolve to a crisp native entry instead of an upscale.
SIZES = (16, 24, 32, 48, 64, 128, 256)

# Corner radius as a fraction of the icon edge. ~0.22 reads as a clear
# rounded square (iOS-squircle-ish) without eating into the brand mark,
# which already sits inside ~23% of padding on the source logo.
DEFAULT_RADIUS_RATIO = 0.22

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "assets" / "logo.png"
DEFAULT_OUTPUT = ROOT / "hushsnap.ico"


def _rounded_mask(size: int, ratio: float) -> Image.Image:
    """Single-channel mask: white rounded square on black, so corners stay
    transparent when applied as the icon's alpha channel."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    radius = max(1.0, size * ratio)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def render_size(src: Image.Image, size: int, ratio: float) -> Image.Image:
    """Downscale the source to ``size``×``size`` and re-mask to a rounded square.

    Each size gets its own mask drawn at that exact pixel size, rather than
    scaling one large mask down — small entries (16/24) keep crisp corner
    anti-aliasing instead of a blurry resampled edge.
    """
    im = src.resize((size, size), Image.LANCZOS).convert("RGBA")
    im.putalpha(_rounded_mask(size, ratio))
    return im


def generate(source: Path, output: Path, ratio: float) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Logo source not found: {source}")
    src = Image.open(source).convert("RGBA")

    rendered = [render_size(src, s, ratio) for s in SIZES]
    # The largest entry is the base image; the rest are appended frames.
    # PIL writes each as a separate ICO directory entry at its native size.
    largest = rendered[-1]
    others = rendered[:-1]
    largest.save(
        output,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=others,
    )
    print(f"[OK] {output}  ({len(SIZES)} sizes: {', '.join(str(s) for s in SIZES)}, radius≈{ratio:.0%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--source", type=Path, default=DEFAULT_SOURCE,
                        help=f"Source logo PNG (default: {DEFAULT_SOURCE})")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output .ico path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--ratio", type=float, default=DEFAULT_RADIUS_RATIO,
                        help=f"Corner radius as a fraction of edge (default: {DEFAULT_RADIUS_RATIO})")
    args = parser.parse_args()

    if not 0.0 < args.ratio < 0.5:
        parser.error("--ratio must be between 0 and 0.5")

    generate(args.source, args.output, args.ratio)


if __name__ == "__main__":
    main()
