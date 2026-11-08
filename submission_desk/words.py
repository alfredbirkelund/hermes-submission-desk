"""Word counting under explicitly documented conventions.

Institutions count words differently (hyphenation, numbers, dashes, footnotes, references,
tables). We implement two precise conventions and say exactly what they do. When the rule's
convention is unresolved, when the rule excludes parts we cannot separate, when the text came
from PDF extraction, or when the script does not separate words with spaces, the result is an
estimate or unknown, never a certified pass.
"""

from __future__ import annotations

import unicodedata

CONVENTIONS = {
    "whitespace_tokens": (
        "Counts every maximal run of non-whitespace characters, splitting on Unicode whitespace "
        "(Python str.split(): spaces, tabs, newlines, no-break space U+00A0). Zero-width space "
        "U+200B is not whitespace. A spaced dash or bullet counts as a word; 'well-known' counts once."
    ),
    "letters_digits_tokens": (
        "Splits the same way, but counts only tokens that contain at least one letter or digit. "
        "A spaced dash, bullet or ampersand does not count; 'well-known', 'U.S.', \"don't\" and '2027' count once."
    ),
}

# Scripts that do not separate words with spaces; whitespace token counts are meaningless there.
_NO_SPACE_RANGES = (
    (0x3040, 0x30FF),   # Hiragana, Katakana
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x0E00, 0x0E7F),   # Thai
    (0x0E80, 0x0EFF),   # Lao
    (0x1780, 0x17FF),   # Khmer
    (0x1000, 0x109F),   # Myanmar
    (0x20000, 0x2FA1F), # CJK Extensions B-F, compatibility supplement
)


def _no_space_script(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _NO_SPACE_RANGES)


def count_words(text: str, convention: str) -> dict:
    tokens = text.split()
    letters = 0
    no_space = 0
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            letters += 1
            if _no_space_script(ch):
                no_space += 1
    if convention == "letters_digits_tokens":
        count = sum(1 for tok in tokens if any(unicodedata.category(c)[0] in "LN" for c in tok))
    else:
        count = len(tokens)
    share = (no_space / letters) if letters else 0.0
    unsupported = None
    if share > 0.05:
        unsupported = (
            f"{share:.0%} of the letters belong to scripts that do not separate words with spaces "
            "(for example Chinese, Japanese or Thai); this counter does not support them."
        )
    return {
        "count": count,
        "tokens": len(tokens),
        "letters": letters,
        "no_space_script_share": round(share, 4),
        "unsupported": unsupported,
    }
