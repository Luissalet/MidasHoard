"""Draw Midas's icon procedurally (no source art): a gold coin with a rising line.

    python scripts/make_icon.py [--preview out.png]

The coin is a radial ramp from #5c4400 (rim) to #f2c230 (centre-top) on the family's flat dark
background, with an inner ring and a rising polyline with an arrow head. Drawn at 4x and
downsampled for clean edges.

Outputs: app-icon.png (1254 px), client/public/icon-512.png, icon-192.png, favicon.ico (16-256)
and dist-icons/Midas hoard.png. Needs: pillow (requirements-icon.txt).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1254
SS = 2  # supersampling factor
BACKGROUND = (26, 20, 4)
RAMP_DARK = (0x5C, 0x44, 0x00)
RAMP_LIGHT = (0xF2, 0xC2, 0x30)
LINE = (0x1A, 0x14, 0x04)
RING = (0xFF, 0xE5, 0x8A)


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def build() -> Image.Image:
    n = SIZE * SS
    img = Image.new("RGB", (n, n), BACKGROUND)
    d = ImageDraw.Draw(img)
    cx = cy = n / 2
    radius = n * 0.36
    steps = 160
    # radial ramp: concentric discs, light centre shifted up-left
    for i in range(steps):
        t = i / (steps - 1)  # 0 = outer
        r = radius * (1 - t * 0.98)
        ox = -radius * 0.18 * t
        oy = -radius * 0.22 * t
        d.ellipse([cx + ox - r, cy + oy - r, cx + ox + r, cy + oy + r], fill=mix(RAMP_DARK, RAMP_LIGHT, t ** 0.85))
    # rim and inner ring
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=(0x3A, 0x2B, 0x00), width=int(n * 0.012))
    rin = radius * 0.80
    d.ellipse([cx - rin, cy - rin, cx + rin, cy + rin], outline=RING, width=int(n * 0.008))
    # rising line with arrow head
    w = int(n * 0.028)
    pts = [(cx - radius * 0.52, cy + radius * 0.36), (cx - radius * 0.16, cy + radius * 0.02), (cx + radius * 0.06, cy + radius * 0.18),
           (cx + radius * 0.46, cy - radius * 0.34)]
    d.line(pts, fill=LINE, width=w, joint="curve")
    for p in pts[:-1]:
        d.ellipse([p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2], fill=LINE)
    tip = pts[-1]
    ang = math.atan2(pts[-1][1] - pts[-2][1], pts[-1][0] - pts[-2][0])
    head = radius * 0.22
    left = (tip[0] - head * math.cos(ang - 0.5), tip[1] - head * math.sin(ang - 0.5))
    right = (tip[0] - head * math.cos(ang + 0.5), tip[1] - head * math.sin(ang + 0.5))
    d.polygon([tip, left, right], fill=LINE)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="also write the icon to this path")
    args = ap.parse_args()
    icon = build()
    icon.save(ROOT / "app-icon.png")
    (ROOT / "client" / "public").mkdir(parents=True, exist_ok=True)
    icon.resize((512, 512), Image.LANCZOS).save(ROOT / "client" / "public" / "icon-512.png")
    icon.resize((192, 192), Image.LANCZOS).save(ROOT / "client" / "public" / "icon-192.png")
    icon.save(ROOT / "client" / "public" / "favicon.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    (ROOT / "dist-icons").mkdir(exist_ok=True)
    icon.save(ROOT / "dist-icons" / "Midas hoard.png")
    if args.preview:
        icon.save(args.preview)
    print("icons written")


if __name__ == "__main__":
    main()
