"""Check-in photos of the demo company, drawn from the AI-generated demo faces.

Two kinds, so the difference is visible to a person and to a vision model:

- a live selfie: the face with its own background; framing, tilt, light and
  colour temperature change every time, as with a phone held at arm's length
  on a different morning;
- a photo of a screen: the registration photo shown on a phone and
  photographed in the foreman's cabin — the same wall, calendar and window
  every day, glare and screen lines on the picture.

Synthetic on purpose: no real person is in these pictures (faces/faces.csv
says how each face was generated). A photo is drawn when first asked for and
kept; the same recipe always gives the same picture.
"""

from __future__ import annotations

import io
import random
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

FACES = Path(__file__).parent / "faces"
WIDTH, HEIGHT = 480, 640
JPEG_QUALITY = 82
PhotoKind = Literal["live", "screen"]


def _jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def _vertical_gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    strip = Image.new("RGB", (1, HEIGHT))
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        strip.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    return strip.resize((WIDTH, HEIGHT))


def _warmth(image: Image.Image, shift: int) -> Image.Image:
    """Morning light is warmer or cooler from day to day."""
    red, green, blue = image.split()
    return Image.merge(
        "RGB",
        (red.point(lambda v: min(255, max(0, v + shift))), green, blue.point(lambda v: min(255, max(0, v - shift)))),
    )


def _live(face_path: Path, rng: random.Random) -> bytes:
    face = Image.open(face_path).convert("RGB")
    face = face.rotate(rng.uniform(-4, 4), resample=Image.BICUBIC, expand=False)
    # A portrait crop of the square photo, a slightly different part each
    # time, kept off the edges where the tilt leaves empty corners.
    margin = round(face.height * 0.08)
    crop_h = round(face.height * rng.uniform(0.76, 0.83))
    crop_w = round(crop_h * WIDTH / HEIGHT)
    x0 = rng.randint(margin, face.width - crop_w - margin)
    y0 = rng.randint(margin, face.height - crop_h - margin)
    photo = face.crop((x0, y0, x0 + crop_w, y0 + crop_h)).resize((WIDTH, HEIGHT))
    photo = ImageEnhance.Brightness(photo).enhance(rng.uniform(0.82, 1.12))
    photo = ImageEnhance.Contrast(photo).enhance(rng.uniform(0.9, 1.1))
    photo = _warmth(photo, rng.randint(-12, 14))
    return _jpeg(photo.filter(ImageFilter.GaussianBlur(rng.uniform(0, 0.7))))


def _cabin_wall(rng: random.Random) -> Image.Image:
    """The foreman's cabin: always the same wall, calendar, window and table."""
    wall = _vertical_gradient((222, 206, 178), (196, 178, 150))
    draw = ImageDraw.Draw(wall)
    draw.rectangle((330, 40, 470, 200), fill=(190, 215, 232), outline=(240, 240, 236), width=8)
    draw.line((400, 40, 400, 200), fill=(240, 240, 236), width=6)
    draw.rectangle((24, 60, 150, 220), fill=(246, 246, 240), outline=(170, 160, 150), width=2)
    draw.rectangle((24, 60, 150, 92), fill=(196, 48, 48))
    for row in range(5):
        for col in range(6):
            x, y = 34 + col * 19, 104 + row * 22
            draw.rectangle((x, y, x + 12, y + 12), outline=(160, 160, 160))
    draw.rectangle((0, 560, WIDTH, HEIGHT), fill=(120, 82, 52))
    draw.rectangle((0, 552, WIDTH, 562), fill=(96, 64, 40))
    # The light differs a little from day to day; the room does not.
    return ImageEnhance.Brightness(wall).enhance(rng.uniform(0.95, 1.05))


def _screen(face_path: Path, rng: random.Random) -> bytes:
    scene = _cabin_wall(rng)
    phone_w, phone_h = 270, 480
    phone = Image.new("RGBA", (phone_w, phone_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(phone)
    draw.rounded_rectangle((0, 0, phone_w - 1, phone_h - 1), radius=34, fill=(24, 24, 28, 255))
    screen_box = (14, 46, phone_w - 14, phone_h - 46)
    screen_w, screen_h = screen_box[2] - screen_box[0], screen_box[3] - screen_box[1]
    face = Image.open(face_path).convert("RGB")
    face = face.resize((screen_h, screen_h)).crop(((screen_h - screen_w) // 2, 0, (screen_h + screen_w) // 2, screen_h))
    screen = ImageEnhance.Brightness(face).enhance(1.12).convert("RGBA")
    overlay = Image.new("RGBA", screen.size, (0, 0, 0, 0))
    lines = ImageDraw.Draw(overlay)
    for y in range(0, screen_h, 3):  # moiré of a screen photographed by a camera
        lines.line((0, y, screen_w, y), fill=(20, 30, 60, 26))
    lines.polygon([(0, 70), (screen_w, 0), (screen_w, 40), (0, 150)], fill=(255, 255, 255, 70))  # glare
    phone.paste(Image.alpha_composite(screen, overlay), screen_box[:2])
    draw.rounded_rectangle((phone_w // 2 - 30, 18, phone_w // 2 + 30, 26), radius=4, fill=(60, 60, 66, 255))
    phone = phone.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, expand=True)
    x = (WIDTH - phone.width) // 2 + rng.randint(-8, 8)
    scene.paste(phone, (x, 70 + rng.randint(-6, 6)), phone)
    return _jpeg(scene.filter(ImageFilter.GaussianBlur(0.6)))


@lru_cache(maxsize=1024)
def photo(face: str, kind: PhotoKind, seed: int) -> bytes:
    """The JPEG of one check-in: ``face`` is a file in faces/, ``seed`` makes it this day's picture."""
    rng = random.Random(seed)
    path = FACES / face
    return _screen(path, rng) if kind == "screen" else _live(path, rng)
