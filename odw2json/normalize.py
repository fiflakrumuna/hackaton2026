"""Warstwa normalizacji wartości pól.

Każdy normalizator to funkcja ``(value, **params) -> value | None`` zarejestrowana
pod nazwą. Nazwy są używane w plikach YAML (``normalize: [digits, nip]``), więc
dodanie nowej reguły dla nowego pola nie wymaga zmian w kodzie – wystarczy
wskazać istniejący normalizator (lub łańcuch normalizatorów) w konfiguracji.

Zasada: normalizator NIGDY nie rzuca wyjątku. Jeśli nie umie znormalizować
wartości, zwraca ``None`` (jeśli wartość jest śmieciem) albo wartość bez zmian
(jeśli ``keep_on_fail=True``).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Callable

NORMALIZERS: dict[str, Callable[..., Any]] = {}


def normalizer(name: str):
    def deco(fn):
        NORMALIZERS[name] = fn
        return fn
    return deco


def apply_chain(value: Any, chain: list, dictionaries: dict | None = None) -> Any:
    """Stosuje listę normalizatorów. Element listy: "nazwa" lub {"nazwa": {params}}."""
    for step in chain or []:
        if value is None:
            return None
        if isinstance(step, str):
            name, params = step, {}
        elif isinstance(step, dict) and len(step) == 1:
            name, params = next(iter(step.items()))
            params = dict(params or {})
        else:
            raise ValueError(f"Niepoprawny krok normalizacji: {step!r}")
        fn = NORMALIZERS.get(name)
        if fn is None:
            raise KeyError(f"Nieznany normalizator '{name}'. Dostępne: {sorted(NORMALIZERS)}")
        if name == "lookup":
            params.setdefault("_dictionaries", dictionaries or {})
        value = fn(value, **params)
    return value


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in {"", "null", "none", "brak", "n/a", "-", "—", "nie dotyczy"})


# --------------------------------------------------------------------------- tekst
@normalizer("blank_to_null")
def blank_to_null(v, **_):
    return None if _is_blank(v) else v


@normalizer("text")
def text(v, **_):
    """Zbija białe znaki (także twarde spacje i łamania linii), usuwa dzielenie wyrazów."""
    if _is_blank(v):
        return None
    s = unicodedata.normalize("NFC", str(v))
    s = s.replace("­", "")              # miękki dywiz
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)  # dzielenie wyrazów na końcu linii
    s = re.sub(r"[\s   ]+", " ", s).strip()
    return s or None


@normalizer("strip_quotes")
def strip_quotes(v, **_):
    if _is_blank(v):
        return None
    s = str(v).strip()
    s = s.strip("„”\"“'‘’«»").strip()
    return s or None


@normalizer("strip_trailing_punct")
def strip_trailing_punct(v, chars: str = ".,;:", **_):
    if _is_blank(v):
        return None
    return str(v).strip().rstrip(chars).strip() or None


@normalizer("smart_trailing_punct")
def smart_trailing_punct(v, **_):
    """Usuwa końcowe ,;: zawsze, a kropkę TYLKO gdy nie kończy skrótu.

    'Belmoria S.A.' -> bez zmian, 'sp. z o.o.' -> bez zmian, 'sp. k.' -> bez zmian,
    'Kancelaria Prawna.' -> 'Kancelaria Prawna'. (Lekcja z ewaluacji: obcinanie kropki
    w formie prawnej kosztowało ~110 błędów na zbiorze treningowym.)
    """
    if _is_blank(v):
        return None
    s = str(v).strip().rstrip(",;:").strip()
    if s.endswith(".") and not s.endswith(".."):
        last = s.split()[-1]
        is_abbrev = "." in last[:-1] or len(last) <= 3 or last.lower() in {"wsp.", "sp.", "zm.", "ul.", "al.", "pl."}
        if not is_abbrev:
            s = s[:-1].rstrip()
    return s or None


@normalizer("company_name")
def company_name(v, **_):
    s = strip_quotes(text(v))
    return smart_trailing_punct(s)


@normalizer("lower")
def lower(v, **_):
    return None if _is_blank(v) else str(v).lower()


@normalizer("upper")
def upper(v, **_):
    return None if _is_blank(v) else str(v).upper()


@normalizer("regex_replace")
def regex_replace(v, pattern: str, repl: str = "", flags: str = "", **_):
    if _is_blank(v):
        return None
    f = re.I if "i" in flags else 0
    return re.sub(pattern, repl, str(v), flags=f).strip() or None


@normalizer("email")
def email(v, **_):
    if _is_blank(v):
        return None
    m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(v))
    return m.group(0).lower().rstrip(".") if m else None


_TITLES = r"(?:r\.\s*pr\.|radc[ayóo]w?\s+prawn\w*|adw\.|adwokat\w*|mec\.|dr\s+hab\.|dr\.?|prof\.|mgr\.?|inż\.|apl\.\s*(?:radc\.|adw\.)|aplikant\w*\s+\w+|pan[i]?\.?)"


@normalizer("person_name")
def person_name(v, **_):
    """'r. pr. Aleksandra  Marciniak (nr wpisu WA 1)' -> 'Aleksandra Marciniak'."""
    s = text(v)
    if s is None:
        return None
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(rf"^\s*(?:{_TITLES}\s*)+", "", s, flags=re.I)
    s = re.split(r",|\s+[–-]\s+|\s+PESEL|\s+nr\s+wpisu", s, maxsplit=1, flags=re.I)[0]
    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return s or None


# --------------------------------------------------------------------------- liczby / identyfikatory
@normalizer("digits")
def digits(v, **_):
    if _is_blank(v):
        return None
    d = re.sub(r"\D", "", str(v))
    return d or None


def _ident(v, lengths: tuple[int, ...], strip_prefix: str = ""):
    if _is_blank(v):
        return None
    s = str(v).upper()
    if strip_prefix:
        s = re.sub(rf"^\s*{strip_prefix}", "", s)
    d = re.sub(r"\D", "", s)
    if not d:
        return None
    if len(d) in lengths:
        return d
    return d  # zostawiamy – walidator oznaczy problem, ale nie gubimy wartości


@normalizer("nip")
def nip(v, **_):
    return _ident(v, (10,), strip_prefix="PL")


@normalizer("regon")
def regon(v, **_):
    return _ident(v, (9, 14))


@normalizer("krs")
def krs(v, **_):
    d = _ident(v, (10,))
    if d and len(d) < 10:
        d = d.zfill(10)  # KRS zawsze 10 cyfr z zerami wiodącymi
    return d


@normalizer("pesel")
def pesel(v, as_int: bool = False, **_):
    d = _ident(v, (11,))
    if d and len(d) != 11:
        return None
    if d and as_int:
        return int(d)
    return d


@normalizer("account")
def account(v, **_):
    """Numer rachunku: usuwa 'PL', spacje, myślniki -> 26 cyfr (NRB)."""
    if _is_blank(v):
        return None
    s = re.sub(r"^\s*(?:PL|IBAN:?\s*PL?)", "", str(v).strip(), flags=re.I)
    d = re.sub(r"\D", "", s)
    return d or None


@normalizer("int")
def to_int(v, **_):
    if _is_blank(v):
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    m = re.search(r"-?\d+", str(v))
    return int(m.group(0)) if m else None


@normalizer("bool_int")
def bool_int(v, **_):
    """tak/nie/true/false/1/0 -> 1/0."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return 1 if v else 0
    s = str(v).strip().lower()
    if s in {"1", "tak", "true", "yes", "t", "y"}:
        return 1
    if s in {"0", "nie", "false", "no", "n", "f"}:
        return 0
    return None


@normalizer("amount")
def amount(v, decimals: int = 2, **_):
    """'10 000,00 zł' / '1.234,56 PLN' / '200.00' / 200 -> float."""
    if _is_blank(v):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), decimals)
    s = str(v)
    s = re.sub(r"(?i)(pln|zł|zl|eur|€)", "", s)
    s = s.replace(" ", " ").strip()
    m = re.search(r"-?\d[\d\s.,']*", s)
    if not m:
        return None
    n = re.sub(r"[\s']", "", m.group(0)).rstrip(".,")
    if "," in n and "." in n:
        # separator dziesiętny to ten, który występuje jako ostatni
        if n.rfind(",") > n.rfind("."):
            n = n.replace(".", "").replace(",", ".")
        else:
            n = n.replace(",", "")
    elif "," in n:
        head, _, tail = n.rpartition(",")
        n = (head.replace(",", "") + "." + tail) if len(tail) in (1, 2) else n.replace(",", "")
    elif n.count(".") > 1 or (n.count(".") == 1 and len(n.rpartition(".")[2]) == 3 and len(n) > 4):
        n = n.replace(".", "")  # 10.000 -> 10000
    try:
        return round(float(n), decimals)
    except ValueError:
        return None


# --------------------------------------------------------------------------- daty
_MONTHS = {
    "stycz": 1, "luty": 2, "lute": 2, "lut": 2, "marz": 3, "marc": 3, "kwie": 4, "maj": 5,
    "czerw": 6, "lip": 7, "sierp": 8, "wrze": 9, "paźdz": 10, "pazdz": 10, "październik": 10,
    "listop": 11, "grud": 12,
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
}


def _month(word: str) -> int | None:
    w = word.lower().strip(".")
    if w in _MONTHS:
        return _MONTHS[w]
    for k, m in _MONTHS.items():
        if len(k) > 2 and w.startswith(k):
            return m
    return None


def _mk(y, m, d):
    try:
        y = int(y)
        if y < 100:
            y += 2000
        return date(y, int(m), int(d)).isoformat()
    except (ValueError, TypeError):
        return None


@normalizer("date")
def to_date(v, **_):
    """Dowolny polski zapis daty -> 'YYYY-MM-DD'. Pierwsza znaleziona data w tekście."""
    if _is_blank(v):
        return None
    s = str(v).strip()
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3))
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4}|\d{2})\b", s)
    if m:
        return _mk(m.group(3), m.group(2), m.group(1))
    m = re.search(r"(\d{1,2})\s*([A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ]+\.?)\s*(\d{4})", s)
    if m and _month(m.group(2)):
        return _mk(m.group(3), _month(m.group(2)), m.group(1))
    return None


# --------------------------------------------------------------------------- zamówienia publiczne
@normalizer("bzp")
def bzp(v, **_):
    """'2026/BZP 00999453/01' -> '2026/BZP00999453/01'. Odrzuca numery TED."""
    if _is_blank(v):
        return None
    s = str(v).upper()
    m = re.search(r"(\d{4})\s*/\s*BZP\s*(\d{6,10})(?:\s*/\s*(\d{1,2}))?", s)
    if not m:
        return None
    out = f"{m.group(1)}/BZP{m.group(2)}"
    if m.group(3):
        out += f"/{m.group(3)}"
    return out


@normalizer("ted")
def ted(v, **_):
    """'2026/S 086-999073' | '999073-2026' | 'OJ S 107/2025 364525-2025' -> 'NNNNNN-YYYY'."""
    if _is_blank(v):
        return None
    s = str(v).upper()
    m = re.search(r"(\d{4})\s*/\s*S\s*\d{1,3}\s*[-–]\s*(\d{5,7})", s)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    m = re.search(r"\b(\d{5,7})\s*[-–]\s*(\d{4})\b", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


@normalizer("case_number")
def case_number(v, **_):
    """Numer postępowania: bez spacji wokół separatorów, bez kropki na końcu."""
    s = text(v)
    if s is None:
        return None
    s = re.sub(r"^(?:nr|numer)\s*(?:referencyjny|sprawy|postępowania)?\s*:?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*([./\-_])\s*", r"\1", s)
    return s.strip(" .,;:") or None


# --------------------------------------------------------------------------- słowniki
@normalizer("lookup")
def lookup(v, dictionary: str, key: str = "id", value: str = "id", match: str = "auto", _dictionaries=None, **_):
    """Mapuje wartość na pozycję słownika (z config/dictionaries/*.yaml).

    match=auto: dokładne dopasowanie klucza, potem dowolnego aliasu/etykiety,
    potem regexów `patterns` danej pozycji.
    """
    if _is_blank(v):
        return None
    entries = (_dictionaries or {}).get(dictionary)
    if entries is None:
        raise KeyError(f"Brak słownika '{dictionary}'")
    sv = str(v).strip().lower()
    for e in entries:
        if str(e.get(key)).lower() == sv:
            return e.get(value)
    for e in entries:
        labels = [e.get("label", "")] + list(e.get("aliases", []))
        if any(sv == str(lbl).strip().lower() for lbl in labels if lbl):
            return e.get(value)
    for e in entries:
        for p in e.get("patterns", []):
            if re.search(p, str(v), flags=re.I):
                return e.get(value)
    return None
