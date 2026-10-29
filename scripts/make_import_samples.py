"""Make the sample files the window offers on the import tab: a notebook photo, a CSV, an Excel sheet.

Each sample has something for the review to catch, against the demo company:
an employee already in NAZAR written the other way round, a name written
twice, a single word, a site written loosely. Run it again after changing
the demo company; the files go next to the window (static/samples).

    python scripts/make_import_samples.py

The notebook is drawn with a handwriting font Windows has (Ink Free); elsewhere
pass another .ttf as the first argument.
"""

from __future__ import annotations

import csv
import io
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "nazar_agent" / "service" / "static" / "samples"
FONT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("C:/Windows/Fonts/Inkfree.ttf")

NOTEBOOK = (
    "Chilonzor crew",
    "1. Normatov Azamat",
    "2. Toirov Jasur",
    "3. Ahmedova Malika",
    "Sergeli warehouse",
    "4. Rahimov",
    "5. Toirov Jasur",
)
CSV_ROWS = (("name", "site"), ("Sardor Qodirov", "Yashnobod"), ("Laylo Usmonova", "Chilonzor"),
            ("Bobur Karimov", "Chilonzor"), ("Umida", ""))
EXCEL_ROWS = (("Full name", "Site"), ("Aziz Saidov", "Sergeli warehouse"), ("Dilnoza Yusupova", "Sergeli"),
              ("Tashpulatova Kamola", "Yashnobod"), ("Otabek Ergashev", "Yashnobod finishing"))


def notebook() -> bytes:
    """A lined page in blue ink, a little crooked, as a phone photographs it."""
    rng = random.Random(7)
    page = Image.new("RGB", (1200, 900), (248, 245, 234))
    draw = ImageDraw.Draw(page)
    for y in range(110, 900, 56):
        draw.line([(0, y), (1200, y)], fill=(176, 204, 230), width=2)
    draw.line([(110, 0), (110, 900)], fill=(226, 150, 150), width=2)
    font = ImageFont.truetype(str(FONT), 46)
    for index, line in enumerate(NOTEBOOK):
        x = 140 if line[0].isdigit() else 125
        draw.text((x + rng.randint(-6, 6), 62 + index * 56 + rng.randint(-3, 3)), line, fill=(28, 44, 130), font=font)
    photo = page.rotate(-2.2, expand=True, fillcolor=(92, 84, 74), resample=Image.Resampling.BICUBIC)
    photo = photo.filter(ImageFilter.GaussianBlur(0.6))
    buffer = io.BytesIO()
    photo.save(buffer, "JPEG", quality=82)
    return buffer.getvalue()


def csv_bytes() -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(CSV_ROWS)
    return buffer.getvalue().encode("utf-8")


def excel() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Crew"
    for row in EXCEL_ROWS:
        sheet.append(row)
    sheet.column_dimensions["A"].width = 26
    sheet.column_dimensions["B"].width = 22
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "notebook.jpg").write_bytes(notebook())
    (OUT / "crew.csv").write_bytes(csv_bytes())
    (OUT / "crew.xlsx").write_bytes(excel())
    print(f"Samples written to {OUT}")


if __name__ == "__main__":
    main()
