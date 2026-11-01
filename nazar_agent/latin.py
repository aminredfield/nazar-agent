"""One spelling for names written in Cyrillic or Latin: «Хасанов», "Khasanov" and "Xasanov" meet here.

Owners in Uzbekistan write names both ways, and NAZAR keeps whatever was typed;
comparing names, sites and answers goes through this so the script never decides.
"""

from __future__ import annotations

import re

_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
})


def to_latin(text: str) -> str:
    """Lower-case Latin letters, Uzbek apostrophes dropped, kh/x/zh folded: "Xo'jaev" and «Хужаев» both give "hojaev"-like words."""
    plain = re.sub(r"[ʻʼ'`’‘]", "", text.lower()).translate(_LATIN)
    return plain.replace("kh", "h").replace("x", "h").replace("zh", "j")


# A model writing Uzbek in Latin letters sometimes slips into Turkish-style ö/ğ
# despite the instruction; Uzbek writes those sounds with an apostrophe.
_UZBEK_APOSTROPHES = {"ö": "o'", "Ö": "O'", "ō": "o'", "Ō": "O'", "ğ": "g'", "Ğ": "G'", "ǵ": "g'", "Ǵ": "G'"}
_UZBEK_SLIPS = str.maketrans(_UZBEK_APOSTROPHES)


def fix_uzbek_latin(text: str) -> str:
    """Uzbek Latin as Uzbek writes it: "körsatilgan" → "ko'rsatilgan", "notöğri" → "noto'g'ri".

    "rö'yxat" carries the apostrophe twice after the swap, so one is dropped.
    """
    return re.sub(r"([OoGg])''", r"\1'", text.translate(_UZBEK_SLIPS))
