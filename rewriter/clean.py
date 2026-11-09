"""Remove invisible / look-alike characters.

Some text sources carry zero-width characters, bidi controls or Cyrillic/Greek
letters that look Latin. None of them are visible, but they change how text is
tokenised and compared, so we normalise them before anything else.
"""
from __future__ import annotations

import re
import unicodedata

# Zero-width and other invisible formatting characters
INVISIBLE = re.compile(
    "["
    "­"  # soft hyphen
    "᠎"  # mongolian vowel separator
    "​-‏"  # zero-width space/joiners, LRM/RLM
    "‪-‮"  # bidi embedding / override
    "⁠-⁤"  # word joiner, invisible operators
    "⁦-⁩"  # bidi isolates
    "﻿"  # BOM / zero-width no-break space
    "\U000e0000-\U000e007f"  # tag characters
    "]"
)

# Unusual spaces -> plain space
ODD_SPACES = re.compile("[   -   　]")

# Common Cyrillic/Greek homoglyphs of Latin letters
HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
        "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "һ": "h", "ӏ": "l",
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
        "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J", "Ѕ": "S",
        "ο": "o", "α": "a", "ν": "v", "ρ": "p", "τ": "t", "ι": "i",
        "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
        "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X", "Υ": "Y", "Ζ": "Z",
    }
)
_LATIN_WORD_WITH_FOREIGN = re.compile(r"\b(?=\w*[A-Za-z])(?=\w*[Ͱ-ϿЀ-ӿ])\w+\b")


def fix_homoglyphs(text: str) -> str:
    """Only translate look-alikes inside words that also contain Latin letters,
    so genuine Greek/Cyrillic words (and maths like α) are left alone."""
    return _LATIN_WORD_WITH_FOREIGN.sub(lambda m: m.group(0).translate(HOMOGLYPHS), text)


def clean_text(text: str, *, normalize_spaces: bool = True) -> tuple[str, dict]:
    """Return (cleaned_text, stats)."""
    stats = {"invisible": len(INVISIBLE.findall(text))}
    text = INVISIBLE.sub("", text)
    before = text
    text = fix_homoglyphs(text)
    stats["homoglyph_words"] = sum(1 for a, b in zip(before.split(), text.split()) if a != b)
    if normalize_spaces:
        stats["odd_spaces"] = len(ODD_SPACES.findall(text))
        text = ODD_SPACES.sub(" ", text)
    text = unicodedata.normalize("NFC", text)
    return text, stats
