"""Genera icon.ico dell'app: nota musicale bianca su quadrato sfumato violetto."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
RADIUS = 52

# gradiente verticale violetto
TOP = (110, 70, 220)
BOTTOM = (45, 25, 115)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
grad = Image.new("RGBA", (SIZE, SIZE))
for y in range(SIZE):
    t = y / (SIZE - 1)
    color = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,)
    for x in range(SIZE):
        grad.putpixel((x, y), color)

# angoli arrotondati trasparenti
mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=RADIUS, fill=255)
img.paste(grad, (0, 0), mask)

# nota musicale
font = None
for name in ("seguisym.ttf", "segoeui.ttf", "arial.ttf"):
    path = Path("C:/Windows/Fonts") / name
    if path.is_file():
        try:
            font = ImageFont.truetype(str(path), 170)
            break
        except OSError:
            continue
if font is None:
    font = ImageFont.load_default()

draw = ImageDraw.Draw(img)
glyph = "\u266B"  # ♫ doppia nota
bbox = draw.textbbox((0, 0), glyph, font=font)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((SIZE - w) / 2 - bbox[0], (SIZE - h) / 2 - bbox[1] - 4), glyph, font=font, fill=(255, 255, 255, 255))

out = Path(__file__).resolve().parent.parent / "icon.ico"
img.save(out, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
img.save(out.with_suffix(".png"))
print("Icona creata:", out)
