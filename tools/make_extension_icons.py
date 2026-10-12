"""Generate the extension icons.

A single source image is rendered down to the sizes Chrome requests. The glyph
is a downward download arrow inside a rounded terminal-style frame, in the
neon-green palette the popup uses.

Run:  py tools/make_extension_icons.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "chrome_extension" / "icons"

BG = (10, 14, 12, 255)          # near-black
FRAME = (0, 255, 136, 255)      # neon green
ARROW = (0, 255, 136, 255)
ACCENT = (0, 214, 214, 255)     # cyan


def glyph(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    # Rounded square background with a subtle inner glow.
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=max(2, size // 6),
        fill=BG,
        outline=FRAME,
        width=max(1, size // 28),
    )

    cx = size / 2
    # Arrow shaft.
    shaft_w = size * 0.16
    top = size * 0.26
    bottom = size * 0.62
    draw.rectangle(
        [cx - shaft_w / 2, top, cx + shaft_w / 2, bottom],
        fill=ARROW,
    )
    # Arrow head, pointing down.
    head_w = size * 0.42
    head_top = size * 0.50
    head_tip = size * 0.76
    draw.polygon(
        [
            (cx - head_w / 2, head_top),
            (cx + head_w / 2, head_top),
            (cx, head_tip),
        ],
        fill=ARROW,
    )
    # Cyan underline, like a terminal cursor.
    line_y = size * 0.84
    draw.rectangle(
        [size * 0.28, line_y, size * 0.72, line_y + max(1, size // 22)],
        fill=ACCENT,
    )
    return img


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        img = glyph(size)
        # The 16px icon is busy; soften it slightly so the shape stays legible.
        if size == 16:
            img = img.filter(ImageFilter.SMOOTH_MORE)
        target = OUT / f"icon{size}.png"
        img.save(target, "PNG")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
