"""Heurystyczna ocena jakości tekstu (0..1) – bez słowników i modeli.

Używana do:
  * decyzji, czy warstwa tekstowa PDF nadaje się do użycia (czy to nie "krzaki"
    z błędnym mapowaniem fontów albo kiepski OCR skanera),
  * porównania wyniku warstwy tekstowej z naszym OCR.
"""
from __future__ import annotations

import re

_POLISH_LETTERS = "a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
_WORD_RE = re.compile(rf"^[\(\[„\"'«]*[{_POLISH_LETTERS}][{_POLISH_LETTERS}\-]+[\)\]\.,;:!?”\"'»]*$")
_NUM_RE = re.compile(r"^[\(\[]?[\d][\d\.,/\-:]*[\)\]\.,;:]?$|^[A-Z]{1,4}[\d][\w\.\-/]*$")
_VOWELS = set("aeiouyąęóAEIOUYĄĘÓ")
_STOPWORDS = {
    "i", "w", "z", "na", "do", "się", "nie", "oraz", "że", "o", "od", "dnia", "przez",
    "jest", "lub", "dla", "po", "ul", "w.", "a", "to", "pn", "nr", "sp", "r", "jak",
    "ze", "za", "przy", "art", "ust", "pkt", "który", "która", "które", "tym",
}
_BAD_CHARS_RE = re.compile(r"[�-\x00-\x08\x0b\x0e-\x1f]")


def text_quality(text: str) -> float:
    """Zwraca 0..1. Dobry polski tekst ~0.8-1.0, śmieci < 0.4."""
    stripped = re.sub(r"\s+", "", text)
    if len(stripped) < 10:
        return 0.0
    bad = len(_BAD_CHARS_RE.findall(text)) + 5 * text.count("(cid:")
    bad_ratio = bad / len(stripped)

    tokens = text.split()
    if not tokens:
        return 0.0
    ok = sum(1 for t in tokens if _WORD_RE.match(t) or _NUM_RE.match(t))
    wordlike = ok / len(tokens)

    letters = [c for c in stripped if c.isalpha()]
    vowel_pen = 1.0
    if len(letters) >= 40:
        vr = sum(1 for c in letters if c in _VOWELS) / len(letters)
        # polski tekst: ~0.33-0.45 samogłosek; dużo poza zakresem => podejrzane mapowanie znaków
        if vr < 0.22 or vr > 0.6:
            vowel_pen = 0.6

    stop_bonus = 1.0
    if len(tokens) >= 60:
        sw = sum(1 for t in tokens if t.lower().strip(".,;:()") in _STOPWORDS) / len(tokens)
        if sw < 0.02:
            stop_bonus = 0.75   # długi tekst bez żadnych polskich "małych" słów

    score = wordlike * vowel_pen * stop_bonus * max(0.0, 1.0 - 8 * bad_ratio)
    return round(max(0.0, min(1.0, score)), 4)
