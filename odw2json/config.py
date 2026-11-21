"""Ładowanie i walidacja konfiguracji.

Struktura katalogu konfiguracji::

    config/
      settings.yaml              # ustawienia globalne, wybór pluginów (OCR, klasyfikator, ekstraktor)
      document_types/*.yaml      # JEDEN plik = JEDEN typ dokumentu (schemat JSON + reguły)
      dictionaries/*.yaml        # słowniki (np. powody odwołania 1-12)

Dodanie nowego typu dokumentu = nowy plik w ``document_types/``.
Dodanie nowego pola = nowy wpis w sekcji ``schema`` danego typu.
Kod Pythona nie zna nazw pól ani typów dokumentów.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .normalize import NORMALIZERS
from .validators import VALIDATORS

LEAF_TYPES = {"string", "integer", "number", "date", "enum", "boolean_int"}
CONTAINER_TYPES = {"object", "list"}

# Domyślne normalizatory dla typów – pole może je nadpisać kluczem `normalize`.
DEFAULT_NORMALIZE = {
    "string": ["text"],
    "integer": ["int"],
    "number": ["amount"],
    "date": ["date"],
    "enum": ["text"],
    "boolean_int": ["bool_int"],
}


class ConfigError(ValueError):
    pass


@dataclass
class FieldSpec:
    name: str
    path: str
    type: str
    description: str = ""
    normalize: list = field(default_factory=list)
    validate: list = field(default_factory=list)
    on_invalid: str = "warn"          # warn | null
    rules: list = field(default_factory=list)       # reguły ekstrakcji (regex)
    derive: dict | None = None        # wartość wyliczana z innych pól
    values: list | None = None        # dozwolone wartości (enum)
    fields: dict[str, "FieldSpec"] = field(default_factory=dict)  # dla object
    items: "FieldSpec | None" = None  # dla list
    scope: dict | None = None         # zawężenie tekstu (object/list)
    split: str | None = None          # list: regex dzielący scope na elementy
    findall: dict | None = None       # list: regex zwracający wiele elementów
    min_items: int = 0
    llm: dict = field(default_factory=dict)          # dodatkowe wskazówki dla promptu
    raw: dict = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        return self.type in LEAF_TYPES

    def iter_leaves(self):
        if self.is_leaf:
            yield self
        elif self.type == "object":
            for f in self.fields.values():
                yield from f.iter_leaves()
        elif self.type == "list" and self.items:
            yield from self.items.iter_leaves()


@dataclass
class DocumentType:
    id: str
    title: str
    description: str
    schema: FieldSpec                 # korzeń (type=object)
    classification: dict
    llm: dict
    output: dict
    source_file: Path
    raw: dict

    def leaf_paths(self) -> list[str]:
        return [f.path for f in self.schema.iter_leaves()]


@dataclass
class AppConfig:
    root: Path
    settings: dict
    document_types: dict[str, DocumentType]
    dictionaries: dict[str, list]

    def get(self, dotted: str, default=None):
        cur: Any = self.settings
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


# --------------------------------------------------------------------------- parsing
def _env_expand(obj):
    """${VAR} / ${VAR:-domyślna} w settings.yaml – klucze API nie lądują w repo."""
    if isinstance(obj, str):
        return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: os.environ.get(m.group(1), m.group(2) or ""), obj)
    if isinstance(obj, dict):
        return {k: _env_expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_env_expand(v) for v in obj]
    return obj


def _parse_field(name: str, spec: Any, path: str, src: Path, dict_names: set[str]) -> FieldSpec:
    if isinstance(spec, str):          # skrót:  pole: string
        spec = {"type": spec}
    if not isinstance(spec, dict):
        raise ConfigError(f"{src}: pole '{path}' musi być mapą lub nazwą typu")
    ftype = spec.get("type")
    if ftype is None:
        ftype = "object" if "fields" in spec else "list" if "items" in spec else "string"
    if ftype not in LEAF_TYPES | CONTAINER_TYPES:
        raise ConfigError(f"{src}: pole '{path}' ma nieznany typ '{ftype}'")

    fs = FieldSpec(
        name=name, path=path, type=ftype,
        description=str(spec.get("description", "")).strip(),
        normalize=list(spec.get("normalize", DEFAULT_NORMALIZE.get(ftype, []))),
        validate=list(spec.get("validate", [])),
        on_invalid=spec.get("on_invalid", "warn"),
        rules=list(spec.get("rules", [])),
        derive=spec.get("derive"),
        values=spec.get("values"),
        scope=spec.get("scope"),
        split=spec.get("split"),
        findall=spec.get("findall"),
        min_items=int(spec.get("min_items", 0)),
        llm=dict(spec.get("llm", {})),
        raw=spec,
    )
    for step in fs.normalize:
        n = step if isinstance(step, str) else next(iter(step))
        if n not in NORMALIZERS:
            raise ConfigError(f"{src}: pole '{path}': nieznany normalizator '{n}'. Dostępne: {sorted(NORMALIZERS)}")
        if n == "lookup":
            d = step["lookup"].get("dictionary")
            if d not in dict_names:
                raise ConfigError(f"{src}: pole '{path}': brak słownika '{d}'")
    for v in fs.validate:
        if v not in VALIDATORS:
            raise ConfigError(f"{src}: pole '{path}': nieznany walidator '{v}'. Dostępne: {sorted(VALIDATORS)}")
    for r in fs.rules:
        for key in ("regex", "pattern"):
            if key in r:
                try:
                    re.compile(r[key])
                except re.error as e:
                    raise ConfigError(f"{src}: pole '{path}': błędny regex {r[key]!r}: {e}")

    if ftype == "object":
        sub = spec.get("fields") or {}
        fs.fields = {k: _parse_field(k, v, f"{path}.{k}" if path else k, src, dict_names) for k, v in sub.items()}
    elif ftype == "list":
        items = spec.get("items")
        if items is None:
            raise ConfigError(f"{src}: lista '{path}' wymaga klucza 'items'")
        fs.items = _parse_field("[]", items, f"{path}[]", src, dict_names)
    return fs


def _parse_doc_type(path: Path, dict_names: set[str]) -> DocumentType:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for k in ("id", "schema"):
        if k not in data:
            raise ConfigError(f"{path}: brak wymaganego klucza '{k}'")
    root = _parse_field("", {"type": "object", "fields": data["schema"]}, "", path, dict_names)
    return DocumentType(
        id=data["id"],
        title=data.get("title", data["id"]),
        description=str(data.get("description", "")).strip(),
        schema=root,
        classification=data.get("classification", {}),
        llm=data.get("llm", {}),
        output=data.get("output", {}),
        source_file=path,
        raw=data,
    )


def _load_dotenv(path: Path) -> None:
    """Wczytuje .env z katalogu projektu (klucz API LLM). Nie nadpisuje istniejących zmiennych."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(root: str | Path | None = None) -> AppConfig:
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    root = Path(root or os.environ.get("ODW2JSON_CONFIG", Path(__file__).resolve().parent.parent / "config"))
    settings_path = root / "settings.yaml"
    settings = _env_expand(yaml.safe_load(settings_path.read_text(encoding="utf-8"))) if settings_path.exists() else {}

    dictionaries: dict[str, list] = {}
    for p in sorted((root / "dictionaries").glob("*.y*ml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        dictionaries[d.get("id", p.stem)] = d.get("entries", [])

    doc_types: dict[str, DocumentType] = {}
    disabled = set(settings.get("disabled_document_types", []) or [])
    for p in sorted((root / "document_types").glob("*.y*ml")):
        dt = _parse_doc_type(p, set(dictionaries))
        if dt.id in disabled:
            continue
        if dt.id in doc_types:
            raise ConfigError(f"Zduplikowany id typu dokumentu '{dt.id}' ({p} i {doc_types[dt.id].source_file})")
        doc_types[dt.id] = dt
    if not doc_types:
        raise ConfigError(f"Brak typów dokumentów w {root / 'document_types'}")
    return AppConfig(root=root, settings=settings, document_types=doc_types, dictionaries=dictionaries)
