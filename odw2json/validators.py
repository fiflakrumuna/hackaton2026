"""Walidatory – nie zmieniają wartości, zwracają listę ostrzeżeń.

Używane do transparentności (UI może pokazać "NIP nie przechodzi sumy kontrolnej")
i do decyzji, czy wynik reguły jest wiarygodny. Zachowanie przy błędzie
(``on_invalid: warn | null``) ustawia się w YAML pola.
"""
from __future__ import annotations

import re
from typing import Any, Callable

VALIDATORS: dict[str, Callable[[Any], str | None]] = {}


def validator(name):
    def deco(fn):
        VALIDATORS[name] = fn
        return fn
    return deco


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v))


@validator("nip_checksum")
def nip_checksum(v):
    d = _digits(v)
    if len(d) != 10:
        return f"NIP powinien mieć 10 cyfr (ma {len(d)})"
    w = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    if sum(int(a) * b for a, b in zip(d, w)) % 11 != int(d[9]):
        return "NIP: niepoprawna suma kontrolna"
    return None


@validator("regon_checksum")
def regon_checksum(v):
    d = _digits(v)
    if len(d) == 9:
        w = [8, 9, 2, 3, 4, 5, 6, 7]
    elif len(d) == 14:
        w = [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8]
    else:
        return f"REGON powinien mieć 9 lub 14 cyfr (ma {len(d)})"
    c = sum(int(a) * b for a, b in zip(d, w)) % 11 % 10
    return None if c == int(d[-1]) else "REGON: niepoprawna suma kontrolna"


@validator("krs_format")
def krs_format(v):
    return None if re.fullmatch(r"\d{10}", str(v)) else "KRS powinien mieć 10 cyfr"


@validator("nrb_checksum")
def nrb_checksum(v):
    d = _digits(v)
    if len(d) != 26:
        return f"Rachunek powinien mieć 26 cyfr (ma {len(d)})"
    num = int(d[2:] + "2521" + d[:2])  # PL = 25 21
    return None if num % 97 == 1 else "Rachunek: niepoprawna suma kontrolna IBAN"


@validator("pesel_checksum")
def pesel_checksum(v):
    d = _digits(v)
    if len(d) != 11:
        return "PESEL powinien mieć 11 cyfr"
    w = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    c = (10 - sum(int(a) * b for a, b in zip(d, w)) % 10) % 10
    return None if c == int(d[10]) else "PESEL: niepoprawna suma kontrolna"


@validator("iso_date")
def iso_date(v):
    return None if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)) else "Data nie jest w formacie YYYY-MM-DD"


@validator("positive")
def positive(v):
    try:
        return None if float(v) > 0 else "Wartość powinna być dodatnia"
    except (TypeError, ValueError):
        return "Wartość nie jest liczbą"
