"""Czyszczenie tekstu – zachowawcze, żeby nie psuć danych dla modułu ekstrakcji."""
from __future__ import annotations

import re
import unicodedata

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_SPACES = {" ": " ", " ": " ", " ": " ", " ": " ", " ": " ", "　": " "}
_INVISIBLE = {"­": "", "​": "", "‌": "", "‍": "", "﻿": ""}
_TRANS = str.maketrans({**_LIGATURES, **_SPACES, **_INVISIBLE})

# polskie małe litery
_LOW = "a-ząćęłńóśźż"
_HYPHEN_RE = re.compile(rf"([{_LOW}]{{2,}})[-‐‑]\n([{_LOW}]{{2,}})([^\s]*)")

# OCR myli cyfry z literami w numerach (NIP, KRS, REGON, PESEL, rachunki, kwoty)
_DIGIT_MAP = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1", "|": "1",
                            "i": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9"})
_ID_LABEL_RE = re.compile(
    r"(?P<label>\b(?:NIP|KRS|REGON|PESEL|nr\s+rachunku|rachunek|rachunku)\b[\s:.\-–]*(?:nr\.?\s*)?)"
    r"(?P<val>(?:PL\s?)?[0-9OoDQlIi|SsBZzg][0-9OoDQlIi|SsBZzg\s\-]{4,40}[0-9OoDQlIi|SsBZzg])",
    re.IGNORECASE,
)
_MOSTLY_DIGIT_TOKEN = re.compile(r"(?<![\w])(?=[0-9OolI|SB]*\d)[0-9OolI|SB]{5,}(?![\w])")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_TRANS)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def dehyphenate(text: str) -> str:
    """'przetar-\\ngu nieograniczonego' -> 'przetargu\\nnieograniczonego'. Tylko małe litery po obu stronach."""
    return _HYPHEN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}\n", text).replace("\n\n\n", "\n\n")


def _fix_value(val: str) -> str:
    prefix = ""
    if val[:2].upper() == "PL":
        prefix, val = val[:2].upper(), val[2:]
    return prefix + val.translate(_DIGIT_MAP)


def fix_ocr_digits(text: str) -> tuple[str, int]:
    """Poprawia pomyłki litera/cyfra w wartościach po etykietach NIP/KRS/REGON/PESEL/rachunek
    oraz w samodzielnych tokenach złożonych głównie z cyfr. Zwraca (tekst, liczba poprawek)."""
    fixes = 0

    def by_label(m: re.Match) -> str:
        nonlocal fixes
        val = m.group("val")
        new = _fix_value(val)
        # zmieniamy tylko gdy po poprawce wartość to w >=80% cyfry (bez spacji/myślników)
        core = re.sub(r"[\s\-]", "", new[2:] if new.startswith("PL") else new)
        if new != val and core and sum(c.isdigit() for c in core) / len(core) >= 0.8:
            fixes += 1
            return m.group("label") + new
        return m.group(0)

    text = _ID_LABEL_RE.sub(by_label, text)

    def token(m: re.Match) -> str:
        nonlocal fixes
        tok = m.group(0)
        digits = sum(c.isdigit() for c in tok)
        if digits / len(tok) >= 0.7 and digits != len(tok):
            fixes += 1
            return tok.translate(_DIGIT_MAP)
        return tok

    text = _MOSTLY_DIGIT_TOKEN.sub(token, text)
    return text, fixes


# --- typowe błędy OCR w polskich dokumentach prawnych ---
# "@" czytane jako Q, ©, ®, (a), (@); spacje wokół @
_EMAIL_RE = re.compile(
    r"(?<![\w@])([A-Za-z0-9][\w.\-+]*?)\s?(?:[qQ]?@|Q|\(?[©®]\)?|\(a\)|\(@\))\s?([A-Za-z0-9][\w\-]*(?:\.[\w\-]+)*\.[a-z]{2,10})\b")
# formy prawne spółek – ważne, bo nazwy firm są porównywane dosłownie
_LEGAL_FORMS = [
    (re.compile(r"\b[sS][pP]\s?[.,]?\s*[zZ2]\s*[oO0]\s?[.,]\s?[oO0]\s?\.?(?=\s|$|[,;)])"), "sp. z o.o."),
    (re.compile(r"\b[sS][pP]\s?[.,]\s?[kK]\s?\.?(?=\s|$|[,;)])"), "sp.k."),
    (re.compile(r"\b[sS][pP]\s?[.,]\s?[jJ]\s?\.?(?=\s|$|[,;)])"), "sp. j."),
    (re.compile(r"(?<![\w.])[S5]\s?[.,]\s?A\s?\.(?=\s|$|[,;)])"), "S.A."),
]


# numer ogłoszenia BZP: "2026/BZ.P00999352", "2026/B2P 00999352"
_BZP_RE = re.compile(r"(\d{4})\s?/\s?B\s?[Z2]\s?[.,]?\s?P\s?(\d{8})")
# ciągi cyfr typu IBAN/rachunek z zabłąkanymi symbolami: "243€020219", "8658'8132"
_IBAN_NOISE_RE = re.compile(r"(?<=\d)[€'‘’`´¢£]+(?=\s?\d)")


def fix_ocr_patterns(text: str) -> tuple[str, int]:
    fixes = 0
    def bzp(m: re.Match) -> str:
        nonlocal fixes
        sep = " " if m.group(0)[-9] == " " else ""       # zachowaj spację przed 8 cyframi, jeśli była
        out = f"{m.group(1)}/BZP{sep}{m.group(2)}"
        if out != m.group(0):
            fixes += 1
        return out

    text = _BZP_RE.sub(bzp, text)
    text, n = _IBAN_NOISE_RE.subn("", text)
    fixes += n

    def email(m: re.Match) -> str:
        nonlocal fixes
        fixed = f"{m.group(1)}@{m.group(2)}"
        if fixed != m.group(0):
            fixes += 1
        return fixed

    # e-maile tylko tam, gdzie po "@" jest domena z kropką – nie ruszamy zwykłych słów z "Q"
    text = _EMAIL_RE.sub(email, text)
    for rx, repl in _LEGAL_FORMS:
        def legal(m: re.Match, repl=repl) -> str:
            nonlocal fixes
            out = repl
            if repl.startswith("sp") and m.group(0)[0] == "S":
                out = "S" + repl[1:]          # zachowaj "Sp. z o.o." z wielkiej litery
            if m.group(0) != out:
                fixes += 1
            return out
        text = rx.sub(legal, text)
    return text, fixes


def clean_text(text: str, *, is_ocr: bool, do_dehyphenate: bool = True, do_fix_digits: bool = True) -> tuple[str, int]:
    text = normalize(text)
    if do_dehyphenate:
        text = dehyphenate(text)
    fixes = 0
    if is_ocr and do_fix_digits:
        text, fixes = fix_ocr_digits(text)
    if is_ocr:
        text, f2 = fix_ocr_patterns(text)
        fixes += f2
    return text, fixes
