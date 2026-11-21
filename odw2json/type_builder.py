"""Tworzenie nowych typów dokumentów z poziomu UI (bez pisania YAML-a ręcznie).

Użytkownik podaje: nazwę typu, słowa kluczowe do rozpoznawania i listę pól
(nazwa, rodzaj, opis, opcjonalnie etykieta w dokumencie, np. "Sygnatura akt:").
Z tego powstaje zwykły plik ``config/document_types/<id>.yaml`` – dokładnie taki sam
jak typy pisane ręcznie, więc od razu działa w UI, w ``main.py`` i w ``evaluate.py``.

- Pole z etykietą dostaje regułę regex (etykieta -> wartość za nią).
- Pole bez etykiety wypełnia LLM na podstawie opisu (opis trafia do promptu).
"""
from __future__ import annotations

import re
import tempfile
import unicodedata
from pathlib import Path

import yaml

from .config import ConfigError, _parse_doc_type, load_config

USER_MARK = "ui"  # created_by: ui -> typ dodany przez użytkownika (można go usunąć z UI)

_DATE_V = r"\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{1,2}\s+[a-ząćęłńóśźż]+\s+\d{4}"
_AMOUNT_V = r"(?:\d{1,3}(?:[  .]\d{3})+|\d+)(?:[,.]\d{1,2})?"

# etykieta w UI -> (fragment specyfikacji pola w YAML, wzorzec wartości dla reguły z etykietą)
FIELD_KINDS: dict[str, tuple[dict, str]] = {
    "Tekst":              ({"type": "string"}, r"[^\n]+"),
    "Data":               ({"type": "date", "validate": ["iso_date"]}, _DATE_V),
    "Kwota / liczba":     ({"type": "number"}, _AMOUNT_V),
    "Liczba całkowita":   ({"type": "integer"}, r"\d+"),
    "NIP":                ({"type": "string", "normalize": ["nip"], "validate": ["nip_checksum"]}, r"[\d\s-]{10,16}"),
    "REGON":              ({"type": "string", "normalize": ["regon"], "validate": ["regon_checksum"]}, r"[\d\s]{9,17}"),
    "KRS":                ({"type": "string", "normalize": ["krs"], "validate": ["krs_format"]}, r"[\d\s]{6,12}"),
    "PESEL":              ({"type": "string", "normalize": ["pesel"], "validate": ["pesel_checksum"]}, r"\d{11}"),
    "Numer rachunku":     ({"type": "string", "normalize": ["account"], "validate": ["nrb_checksum"]}, r"(?:PL)?[\d\s]{26,34}"),
    "E-mail":             ({"type": "string", "normalize": ["email"]}, r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "Nazwa firmy":        ({"type": "string", "normalize": ["company_name"]}, r"[^\n]+"),
    "Imię i nazwisko":    ({"type": "string", "normalize": ["person_name"]}, r"[^\n]+"),
}


def slugify(text: str) -> str:
    """'Pozew o zapłatę' -> 'pozew_o_zaplate' (bezpieczne id / nazwa pola / nazwa pliku)."""
    t = unicodedata.normalize("NFKD", (text or "").replace("ł", "l").replace("Ł", "L"))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    if t and t[0].isdigit():
        t = "_" + t
    return t


def _phrase_regex(phrase: str) -> str:
    """Zwykła fraza od użytkownika -> regex odporny na wielokrotne spacje / złamania wierszy z OCR."""
    return r"\s+".join(re.escape(w) for w in phrase.split())


def build_doc_type(title: str, description: str, keywords: list[str], fields: list[dict],
                   type_id: str | None = None) -> dict:
    """Składa słownik w formacie config/document_types/*.yaml.

    ``fields``: [{"name": "Sygnatura akt", "kind": "Tekst", "description": "...", "label": "Sygn. akt:"}, ...]
    """
    title = (title or "").strip()
    type_id = slugify(type_id or title)
    if not type_id:
        raise ConfigError("Podaj nazwę typu dokumentu.")
    kws = [k.strip() for k in keywords if k and k.strip()]
    if not kws:
        raise ConfigError("Podaj co najmniej jedno słowo kluczowe – po nim system rozpozna ten typ dokumentu.")

    schema: dict[str, dict] = {}
    for f in fields:
        label_ui = str(f.get("name") or "").strip()
        if not label_ui:
            continue
        key = slugify(label_ui)
        if not key:
            raise ConfigError(f"Nieprawidłowa nazwa pola: {label_ui!r}")
        if key in schema:
            raise ConfigError(f"Pole '{label_ui}' występuje dwa razy.")
        kind = f.get("kind") or "Tekst"
        if kind not in FIELD_KINDS:
            raise ConfigError(f"Pole '{label_ui}': nieznany rodzaj '{kind}'.")
        base, value_re = FIELD_KINDS[kind]
        spec = {k: (list(v) if isinstance(v, list) else v) for k, v in base.items()}
        spec["description"] = str(f.get("description") or "").strip() or label_ui
        label_doc = str(f.get("label") or "").strip()
        if label_doc:
            spec["rules"] = [{
                # [ \t]* zamiast \s* – wartość musi stać w tej samej linii co etykieta
                "regex": _phrase_regex(label_doc.rstrip(":")) + r"[ \t]*:?[ \t]*(?P<v>" + value_re + ")",
                "source": "raw",  # tekst z podziałem na linie (flat_text zlewa wszystko w jedną linię)
                "name": "etykieta:" + label_doc,
            }]
        schema[key] = spec
    if not schema:
        raise ConfigError("Dodaj co najmniej jedno pole.")

    return {
        "id": type_id,
        "title": title or type_id,
        "description": (description or "").strip(),
        "created_by": USER_MARK,
        "classification": {
            "min_score": 2,
            "keywords": [{"pattern": _phrase_regex(k), "weight": 2} for k in kws],
        },
        "schema": schema,
    }


def _types_dir(root: str | Path | None) -> Path:
    return Path(load_config(root).root) / "document_types"


def validate_doc_type(data: dict, root: str | Path | None = None, overwrite: bool = False) -> None:
    """Sprawdza typ tym samym parserem co przy starcie aplikacji – błędny YAML nie trafi na dysk."""
    if not isinstance(data, dict):
        raise ConfigError("Definicja typu musi być mapą YAML (klucze id, title, schema…).")
    cfg = load_config(root)
    type_id = data.get("id")
    if not type_id or slugify(str(type_id)) != str(type_id):
        raise ConfigError("Klucz 'id' jest wymagany i może zawierać tylko małe litery, cyfry i '_' (np. pozew_o_zaplate).")
    if type_id in cfg.document_types and not overwrite:
        raise ConfigError(f"Typ '{type_id}' już istnieje. Wybierz inną nazwę.")
    # plik tymczasowy POZA config/, żeby równoległe load_config() go nie wczytało
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / f"{type_id}.yaml"
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        _parse_doc_type(tmp, set(cfg.dictionaries))


def save_doc_type(data: dict, root: str | Path | None = None, overwrite: bool = False) -> Path:
    validate_doc_type(data, root, overwrite)
    path = _types_dir(root) / f"{data['id']}.yaml"
    header = "# Typ dokumentu utworzony w interfejsie aplikacji (kreator). Można go dalej edytować ręcznie.\n"
    path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    load_config(root)  # kontrola końcowa: cała konfiguracja nadal się ładuje
    return path


def user_types(root: str | Path | None = None) -> dict[str, str]:
    """Typy dodane z UI: {id: tytuł}."""
    return {i: dt.title for i, dt in load_config(root).document_types.items()
            if dt.raw.get("created_by") == USER_MARK}


def delete_user_type(type_id: str, root: str | Path | None = None) -> None:
    dt = load_config(root).document_types.get(type_id)
    if dt is None:
        raise ConfigError(f"Nie ma typu '{type_id}'.")
    if dt.raw.get("created_by") != USER_MARK:
        raise ConfigError("Z interfejsu można usuwać tylko typy dodane w kreatorze.")
    dt.source_file.unlink()


def to_yaml(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
