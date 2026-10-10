"""Build web logo assets from the single-colour logo on a flat light background."""
# Usage: python frontend/scripts/make_logo_assets.py frontend/images/logo.png frontend
import sys
from pathlib import Path
from PIL import Image, ImageDraw

src, frontend = Path(sys.argv[1]), Path(sys.argv[2])
im = Image.open(src).convert("RGB")
bg = im.getpixel((2, 2))
gray = im.convert("L")
bg_l = sum(bg) / 3

# Ink colour: mean of the solidly dark pixels.
dark = [p for p, l in zip(im.getdata(), gray.getdata()) if l < 60]
ink = tuple(round(sum(c[i] for c in dark) / len(dark)) for i in range(3))
ink_l = sum(ink) / 3

# Alpha from darkness relative to the background; keeps anti-aliased edges.
alpha = gray.point(lambda v: max(0, min(255, round((bg_l - v) / (bg_l - ink_l) * 255))))
mark = Image.new("RGBA", im.size, ink + (0,))
mark.putalpha(alpha)
mark = mark.crop(alpha.point(lambda a: 255 if a > 8 else 0).getbbox())

def square(img, size, pad_ratio, background=None, radius_ratio=0.0):
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if background:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=round(size * radius_ratio), fill=255)
        canvas.paste(Image.new("RGBA", (size, size), background + (255,)), (0, 0), mask)
    inner = round(size * (1 - 2 * pad_ratio))
    scale = inner / max(img.size)
    fitted = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return canvas

public, app = frontend / "public", frontend / "src" / "app"
# Transparent mark for the header (inverted in dark mode via CSS).
square(mark, 256, 0.02).save(public / "logo-mark.png", optimize=True)
# Tab / home-screen icons keep the light tile so they read on dark tabs.
square(mark, 512, 0.14, background=bg, radius_ratio=0.22).save(app / "icon.png", optimize=True)
square(mark, 180, 0.14, background=bg).convert("RGB").save(app / "apple-icon.png", optimize=True)
square(mark, 256, 0.10, background=bg, radius_ratio=0.22).save(
    app / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)]
)
print("ink", ink, "bg", bg, "mark", mark.size)
