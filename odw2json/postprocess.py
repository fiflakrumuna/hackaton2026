"""Normalizacja + walidacja + pola wyliczane, sterowane schematem YAML.

Wejście: surowy słownik z ekstraktora (LLM, reguły, cokolwiek) – może mieć
brakujące pola, dodatkowe pola, złe typy, wartości "brak" itd.
Wyjście: JSON dokładnie w strukturze i kolejności z YAML + lista ostrzeżeń.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import AppConfig, DocumentType, FieldSpec
from .normalize import apply_chain
from .validators import VALIDATORS


@dataclass
class PostprocessResult:
    data: dict
    warnings: list[dict] = field(default_factory=list)


def _get(obj: Any, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class Postprocessor:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def run(self, doc_type: DocumentType, raw: dict | None) -> PostprocessResult:
        res = PostprocessResult(data={})
        res.data = self._norm(doc_type.schema, raw or {}, res, path="")
        self._derive(doc_type.schema, res.data, res.data, res, parent_list_len=None)
        return res

    # ---------------------------------------------------------------- normalizacja
    def _norm(self, fs: FieldSpec, value: Any, res: PostprocessResult, path: str):
        if fs.type == "object":
            src = value if isinstance(value, dict) else {}
            return {k: self._norm(sub, src.get(k), res, f"{path}.{k}" if path else k) for k, sub in fs.fields.items()}
        if fs.type == "list":
            if value is None:
                items = []
            elif isinstance(value, list):
                items = value
            else:
                items = [value]
            out = [self._norm(fs.items, it, res, f"{path}[{i}]") for i, it in enumerate(items)]
            # usuwamy całkowicie puste elementy (np. LLM zwrócił {} albo same null)
            out = [o for o in out if not _all_null(o)]
            return out
        # liść
        if isinstance(value, (dict, list)):
            value = _first_scalar(value)
        try:
            v = apply_chain(value, fs.normalize, self.cfg.dictionaries)
        except Exception as e:  # normalizator nie może wywalić pipeline'u
            res.warnings.append({"path": path, "msg": f"Błąd normalizacji: {e}", "value": value})
            v = None
        if v is not None and fs.values and v not in fs.values:
            res.warnings.append({"path": path, "msg": f"Wartość spoza dozwolonych {fs.values}", "value": v})
            v = None
        if v is not None:
            for name in fs.validate:
                msg = VALIDATORS[name](v)
                if msg:
                    res.warnings.append({"path": path, "msg": msg, "value": v})
                    if fs.on_invalid == "null":
                        v = None
                        break
        return v

    # ---------------------------------------------------------------- pola wyliczane
    def _derive(self, fs: FieldSpec, node: Any, root: dict, res: PostprocessResult, parent_list_len):
        if fs.type == "object" and isinstance(node, dict):
            for k, sub in fs.fields.items():
                if sub.is_leaf and sub.derive:
                    node[k] = self._derive_value(sub, node.get(k), node, root, parent_list_len)
                else:
                    self._derive(sub, node.get(k), root, res, parent_list_len)
        elif fs.type == "list" and isinstance(node, list):
            for it in node:
                self._derive(fs.items, it, root, res, len(node))

    def _derive_value(self, fs: FieldSpec, current, obj: dict, root: dict, parent_list_len):
        d = fs.derive
        mode = d.get("mode", "fill")  # fill = tylko gdy brak wartości; override = zawsze

        def ref(p: str):
            # "/A.b" = ścieżka od korzenia dokumentu, "a.b" = względem bieżącego obiektu
            return _get(root, p[1:]) if p.startswith("/") else _get(obj, p)

        if d.get("null_if_single_in_list") and (parent_list_len or 0) <= 1:
            return None
        if mode == "fill" and current is not None:
            return current
        if "present" in d:
            paths = d["present"] if isinstance(d["present"], list) else [d["present"]]
            hit = any(ref(p) not in (None, "", [], {}) for p in paths)
            return d.get("then", 1) if hit else d.get("else", 0)
        if "lookup" in d:
            spec = d["lookup"]
            key_val = ref(spec["from"])
            if key_val is None:
                return current if mode == "fill" else None
            for e in self.cfg.dictionaries.get(spec["dictionary"], []):
                if str(e.get(spec.get("key", "id"))) == str(key_val):
                    return e.get(spec.get("field", "label"))
            return current if mode == "fill" else None
        if "copy" in d:
            return ref(d["copy"])
        if "default" in d:
            return d["default"]
        return current


def _first_scalar(v):
    if isinstance(v, dict):
        for k in ("value", "wartosc", "v"):
            if k in v:
                return v[k]
        for x in v.values():
            if not isinstance(x, (dict, list)):
                return x
        return None
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _all_null(o) -> bool:
    if o is None:
        return True
    if isinstance(o, dict):
        return all(_all_null(v) for v in o.values())
    if isinstance(o, list):
        return all(_all_null(v) for v in o)
    return False
