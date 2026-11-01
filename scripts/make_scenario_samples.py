"""Draw the two owner's schedules the Scenarios page reads: an Excel table and a chat message.

    python scripts/make_scenario_samples.py

The photos of the scenes (shift photos, check-in selfies) were drawn by Muse Image once;
static/scenarios/photos.csv keeps how. Fonts: Segoe UI, as Windows has it; elsewhere pass
a .ttf folder as the first argument.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "nazar_agent" / "service" / "static" / "scenarios"
FONTS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("C:/Windows/Fonts")

TABLE = (
    ("Employee", "Location", "Days", "Shift"),
    ("Dana Brooks", "Fresh Market — Riverside", "Mon–Sat", "09:00–18:00"),
    ("Andy Kim", "Fresh Market — Riverside", "Mon–Sat", "09:00–18:00"),
    ("Sophie Martin", "Fresh Market — Riverside", "Tue–Sun", "14:00–23:00"),
    ("Owen Edwards", "Fresh Market — Northside", "Mon–Fri", "08:00–17:00"),
    ("Maria Lopez", "Fresh Market — Northside", "Mon–Fri", "08:00–17:00"),
    ("Jake Reynolds", "Corner Café, 15 Market Street", "Wed–Sun", "10:00–22:00"),
    ("Zara Hughes", "Corner Café, 15 Market Street", "Wed–Sun", "10:00–22:00"),
    ("Frank Sullivan", "Corner Café, 15 Market Street", "Mon–Fri", "07:00–15:00"),
)
CHAT = (
    "Shifts from next week:",
    "Fresh Market Riverside — Dana, Andy 9-6 (mon-sat)",
    "Sophie 2pm-11pm, tue thru sun",
    "Northside branch: Owen + Maria 8 to 5, weekdays",
    "Corner Café (15 Market St): Jake & Zara 10am–10pm,",
    "wed–sun",
    "Frank: de 7 a 3, entre semana",
)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def excel() -> Image.Image:
    regular, bold = font("segoeui.ttf", 22), font("segoeuib.ttf", 22)
    widths = (260, 330, 130, 160)
    image = Image.new("RGB", (sum(widths) + 40, 60 + 42 * len(TABLE)), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, 34], fill=(33, 115, 70))
    draw.text((12, 4), "Staff schedule — November.xlsx", fill="white", font=bold)
    for r, row in enumerate(TABLE):
        y, x = 48 + r * 42, 20
        for c, cell in enumerate(row):
            draw.rectangle([x, y, x + widths[c], y + 42], outline=(200, 200, 200), fill=(232, 240, 233) if r == 0 else None)
            draw.text((x + 8, y + 8), cell, fill="black", font=bold if r == 0 else regular)
            x += widths[c]
    return image


def chat() -> Image.Image:
    regular, small = font("segoeui.ttf", 26), font("segoeui.ttf", 18)
    image = Image.new("RGB", (900, 120 + 40 * len(CHAT)), (230, 236, 241))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 900, 60], fill=(82, 136, 193))
    draw.text((24, 14), "Fresh Market & Corner Café — managers", fill="white", font=regular)
    top, bottom = 84, 84 + 40 * len(CHAT) + 30
    draw.rounded_rectangle([40, top, 860, bottom], radius=18, fill="white")
    for index, line in enumerate(CHAT):
        draw.text((64, top + 16 + index * 40), line, fill=(20, 20, 20), font=regular)
    draw.text((800, bottom - 28), "21:47", fill=(120, 150, 120), font=small)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    excel().save(OUT / "roster-excel.png")
    chat().save(OUT / "roster-chat.png")
    print(f"Written to {OUT}")


if __name__ == "__main__":
    main()
