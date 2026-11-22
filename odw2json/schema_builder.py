"""Generowanie artefaktów ze schematu YAML – dla ekstraktora LLM i interfejsu (UI).

* ``build_pydantic_model(doc_type)`` – dynamiczny model Pydantic (np. do Instructor /
  Structured Outputs). Pola z myślnikiem (``przez-pelnomocnika``) obsługiwane aliasem.
* ``json_schema(doc_type)`` – JSON Schema (np. dla ``response_format`` w API zgodnym z OpenAI).
* ``field_guide(doc_type)`` – lista pól z opisami do wklejenia w prompt.
* ``empty_instance(doc_type)`` – szkielet JSON z nullami, w kolejności z YAML.

Dzięki temu prompt i struktura wyjścia zmieniają się automatycznie po edycji YAML.
"""
from __future__ import annotations

import keyword
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model

from .config import DocumentType, FieldSpec

_PY_TYPES = {"string": str, "integer": int, "number": float, "date": str, "boolean_int": int}


def _ident(name: str) -> str:
    s = re.sub(r"\W", "_", name)
    if not s or s[0].isdigit() or keyword.iskeyword(s):
        s = f"f_{s}"
    return s


def _annotation(fs: FieldSpec, prefix: str, with_evidence: bool):
    if fs.type == "object":
        return _model_for(fs, prefix, with_evidence)
    if fs.type == "list":
        return list[_annotation(fs.items, prefix + "Item", with_evidence)]
    if fs.type == "enum" and fs.values:
        return Optional[Literal[tuple(fs.values)]]
    return Optional[_PY_TYPES.get(fs.type, str)]


def _model_for(fs: FieldSpec, name: str, with_evidence: bool) -> type[BaseModel]:
    defs: dict[str, Any] = {}
    for key, sub in fs.fields.items():
        ann = _annotation(sub, name + _ident(key).title(), with_evidence)
        default = [] if sub.type == "list" else None
        desc = sub.description or None
        if sub.type == "object":
            defs[_ident(key)] = (ann, Field(..., alias=key, description=desc))
        else:
            defs[_ident(key)] = (ann, Field(default, alias=key, description=desc))
    model = create_model(name or "Root", __config__=ConfigDict(populate_by_name=True, extra="ignore"), **defs)
    return model


def build_pydantic_model(doc_type: DocumentType, with_evidence: bool = False) -> type[BaseModel]:
    """Zwraca klasę modelu. ``with_evidence`` dodaje pole ``_zrodla`` {ścieżka: cytat}."""
    base = _model_for(doc_type.schema, _ident(doc_type.id).title(), with_evidence)
    if not with_evidence:
        return base
    return create_model(
        base.__name__ + "WithEvidence",
        __config__=ConfigDict(populate_by_name=True, extra="ignore"),
        dane=(base, Field(..., description="Wyekstrahowane dane")),
        zrodla=(dict[str, str], Field(default_factory=dict, description=(
            "Dla każdego wypełnionego pola: dosłowny cytat (≤ 200 znaków) z dokumentu, "
            "z którego pochodzi wartość. Klucz = ścieżka pola, np. 'Odwolujacy[0].nip'."))),
    )


def json_schema(doc_type: DocumentType, with_evidence: bool = False) -> dict:
    return build_pydantic_model(doc_type, with_evidence).model_json_schema(by_alias=True)


def empty_instance(fs: FieldSpec | DocumentType) -> Any:
    if isinstance(fs, DocumentType):
        fs = fs.schema
    if fs.type == "object":
        return {k: empty_instance(v) for k, v in fs.fields.items()}
    if fs.type == "list":
        return []
    return None


def field_guide(doc_type: DocumentType) -> str:
    """Tekst do promptu: ścieżka – typ – opis (+ dozwolone wartości / wskazówki)."""
    lines = []

    def walk(fs: FieldSpec, indent: int):
        for key, sub in fs.fields.items():
            pad = "  " * indent
            t = sub.type
            if t == "list":
                lines.append(f"{pad}- {key} (lista): {sub.description}".rstrip(": "))
                if sub.items.type == "object":
                    walk(sub.items, indent + 1)
                continue
            if t == "object":
                lines.append(f"{pad}- {key} (obiekt): {sub.description}".rstrip(": "))
                walk(sub, indent + 1)
                continue
            extra = ""
            if sub.values:
                extra = f" Dozwolone: {', '.join(map(str, sub.values))}."
            hint = sub.llm.get("hint")
            if hint:
                extra += f" Wskazówka: {hint}"
            fmt = {"date": " Format YYYY-MM-DD.", "number": " Liczba (kropka dziesiętna).",
                   "integer": " Liczba całkowita.", "boolean_int": " 1 = tak, 0 = nie."}.get(t, "")
            lines.append(f"{pad}- {key} ({t}): {sub.description}{fmt}{extra} Brak w dokumencie → null.")
    walk(doc_type.schema, 0)
    return "\n".join(lines)
