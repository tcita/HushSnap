"""Generate the Open Graph social preview image for the GitHub Pages site.

Output: docs/og-preview.png  (1200x630, the size Twitter/Facebook expect)

Re-run after any brand/wording change to keep the social card in sync:
    python scripts/generate_og_image.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "og-preview.png"
LOGO = ROOT / "assets" / "logo.png"

W, H = 1200, 630
BG = (6, 6, 8)
GREEN = (95, 201, 138)
GREEN_DIM = (60, 140, 95)
WHITE = (255, 255, 255)
GREY = (150, 166, 186)
DIM = (90, 100, 116)

FONT_BOLD = r"C:\Windows\Fonts\arialbd.ttf"
FONT_REG = r"C:\Windows\Fonts\arial.ttf"


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    except OSError:
        return ImageFont.load_default()


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> int:
    return draw.textbbox((0, 0), s, font=f)[2]


def main() -> None:
    img = Image.new("RGB", (W, H), BG)

    # Brand-green radial glow, upper-right (mirrors the landing scene).
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 780, -360, W + 160, 580], fill=(26, 70, 48))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    img = ImageChops.screen(img, glow)
    draw = ImageDraw.Draw(img)

    x = 88

    # Logo + wordmark
    logo_y = 78
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        lh = 56
        lw = int(logo.width * lh / max(logo.height, 1))
        logo = logo.resize((lw, lh), Image.LANCZOS)
        img.paste(logo, (x, logo_y), logo)
        draw.text((x + lw + 16, logo_y + 6), "HushSnap", font=font(30), fill=WHITE)
    else:
        draw.text((x, logo_y), "HushSnap", font=font(34), fill=WHITE)

    # Headline: Snap. / Recognize. / Done.
    hy = 180
    f_head = font(82)
    draw.text((x, hy), "Snap.", font=f_head, fill=WHITE)
    draw.text((x, hy + 92), "Recognize.", font=f_head, fill=GREEN)
    draw.text((x, hy + 184), "Done.", font=f_head, fill=WHITE)

    # Subtitle
    draw.text((x, hy + 300), "A local screenshot & OCR tool for Windows.",
              font=font(26, bold=False), fill=GREY)

    # Feature pill row
    fy = hy + 352
    pills = ["LOCAL", "PRIVATE", "OFFLINE"]
    px = x
    for p in pills:
        pw = text_w(draw, p, font(18, bold=False)) + 28
        draw.rounded_rectangle([px, fy, px + pw, fy + 34], radius=17,
                               outline=(40, 90, 64), width=1, fill=(16, 36, 26))
        draw.text((px + 14, fy + 8), p, font=font(18, bold=False), fill=GREEN)
        px += pw + 12

    # Right-side abstract mock: a green-bordered capture frame with OCR lines.
    mx, my, mw, mh = 772, 150, 356, 360
    draw.rounded_rectangle([mx, my, mx + mw, my + mh], radius=18,
                           outline=GREEN, width=2, fill=(14, 22, 18))
    # crosshair
    cx, cy = mx + mw // 2, my + mh // 2
    draw.line([mx + 16, cy, mx + mw - 16, cy], fill=GREEN_DIM, width=1)
    draw.line([cx, my + 16, cx, my + mh - 16], fill=GREEN_DIM, width=1)

    # Mock OCR result card overlapping the frame
    ox, oy, ow, oh = mx + 40, my + 120, 300, 180
    draw.rounded_rectangle([ox, oy, ox + ow, oy + oh], radius=14,
                           fill=(26, 26, 30), outline=(60, 60, 66), width=1)
    # header dot row
    for i, c in enumerate([(95, 201, 138), (90, 90, 96), (90, 90, 96)]):
        draw.ellipse([ox + 16 + i * 16, oy + 14, ox + 24 + i * 16, oy + 22], fill=c)
    # text lines
    ly = oy + 40
    for i, frac in enumerate([0.92, 0.74, 0.85, 0.6]):
        lw = int(ow * frac)
        col = GREEN if i == 0 else (110, 120, 136)
        draw.rounded_rectangle([ox + 18, ly, ox + 18 + lw, ly + 10], radius=5, fill=col)
        ly += 26

    # Editor hint: small edit/pin/close action pill above the frame
    pill_w, pill_h = 84, 22
    pill_x = mx + (mw - pill_w) // 2
    pill_y = my - 14
    draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                           radius=11, fill=(0, 0, 0), outline=(60, 60, 66), width=1)
    for i, glyph in enumerate(["✎", "📌", "×"]):
        gx = pill_x + 16 + i * 26
        col = GREEN if i < 2 else WHITE
        draw.text((gx, pill_y + 4), glyph, font=font(13, bold=False), fill=col)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)}  ({W}x{H})")


if __name__ == "__main__":
    main()
