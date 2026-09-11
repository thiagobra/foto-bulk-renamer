"""Generate assets/icon.ico for the built .exe.

Run once:  python make_icon.py
The .ico is committed, so you only need this if you want to change the icon.
"""

from pathlib import Path

from PIL import Image, ImageDraw

BG = (28, 28, 28)
ACCENT = (76, 194, 255)
LENS = (18, 18, 18)

SIZE = 256
out = Path("assets")
out.mkdir(exist_ok=True)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Rounded dark tile
d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=56, fill=BG)

# Camera body
body = [34, 78, SIZE - 34, SIZE - 52]
d.rounded_rectangle(body, radius=22, fill=ACCENT)
# Viewfinder bump
d.rounded_rectangle([86, 56, 170, 92], radius=12, fill=ACCENT)
# Lens
cx, cy, r = SIZE / 2, 146, 44
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=LENS)
d.ellipse([cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12], fill=BG)
# Shutter dot
d.ellipse([SIZE - 78, 96, SIZE - 58, 116], fill=LENS)

img.save(out / "icon.ico",
         sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
img.resize((128, 128), Image.LANCZOS).save(out / "icon.png")
print(f"wrote {out / 'icon.ico'} and {out / 'icon.png'}")
