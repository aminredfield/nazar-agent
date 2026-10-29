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
