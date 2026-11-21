"""Ekstraktor regułowy – całe zachowanie opisane w YAML (``rules``, ``scope``, ``split``, ``findall``).

Rola w systemie:
  1. baseline bez LLM (do porównania w ewaluacji),
  2. "pewne" pola (NIP, KRS, kwoty, numery) – regex jest tu dokładniejszy i tańszy niż LLM,
  3. fallback, gdy API LLM nie odpowiada.

Typy reguł dla liścia (pierwsza reguła, która zwróci wartość, wygrywa):
  - regex: '...(?P<v>...)...'
      opcje: flags ("i" domyślnie; "" = rozróżniaj wielkość liter; "m", "s"),
             nth (które dopasowanie, 0 = pierwsze), source: flat|raw (raw = oryginalne linie),
             page: N (tylko z source: raw), before: regex (szukaj tylko przed znacznikiem),
             map: {regex: wartość} (zamiana dopasowania na wartość), value: stała przy dopasowaniu
  - const: wartość
  - dictionary: {name, field: id, anchor: regex, window: 400, sentence: bool,
                 anchor_required: bool, return: entry|anchor}
      dla każdej kotwicy po kolei szuka regexów `patterns` pozycji słownika w oknie;
      pierwsze okno z trafieniem wygrywa (return: anchor -> zwraca grupę v z kotwicy, np. datę)
  UWAGA: przy domyślnej fladze "i" klasy [A-ZĄĆ...] łapią też małe litery – dla imion
  używaj (?-i:...).
Kontenery:
  - scope: {start: regex, end: regex, max_chars: N, required: bool}  zawęża tekst
  - split: regex                    (lista) dzieli tekst na elementy
  - findall: {regex: ..., field: nazwa_pola}  (lista obiektów z jednym polem tekstowym)
"""
from __future__ import annotations

import re
from typing import Any

from .config import AppConfig, DocumentType, FieldSpec
from .document import ExtractedDocument, ExtractionResult


def _flags(s: str | None) -> int:
    f = 0
    for ch in ("i" if s is None else s):
        f |= {"i": re.I, "s": re.S, "m": re.M, "x": re.X}.get(ch, 0)
    return f


class RuleExtractor:
    name = "rules"

    def __init__(self, cfg: AppConfig, **_):
        self.cfg = cfg

    def extract(self, doc: ExtractedDocument, doc_type: DocumentType) -> ExtractionResult:
        evidence: dict[str, dict] = {}
        text = doc.flat_text
        data = self._object(doc_type.schema, text, doc, evidence, "")
        return ExtractionResult(data=data, evidence=evidence, method=self.name)

    # ------------------------------------------------------------------ helpers
    def _scope(self, fs: FieldSpec, text: str) -> str | None:
        sc = fs.scope
        if not sc:
            return text
        start = 0
        if sc.get("start"):
            m = re.search(sc["start"], text, flags=_flags(sc.get("flags")))
            if not m:
                return None if sc.get("required") else text
            start = m.end() if not sc.get("include_start") else m.start()
        sub = text[start:]
        if sc.get("end"):
            m = re.search(sc["end"], sub, flags=_flags(sc.get("flags")))
            if m:
                sub = sub[: m.start()]
        if sc.get("max_chars"):
            sub = sub[: int(sc["max_chars"])]
        return sub

    def _object(self, fs: FieldSpec, text: str, doc, ev, path) -> dict | None:
        scoped = self._scope(fs, text)
        if scoped is None:
            return {k: ([] if s.type == "list" else None) for k, s in fs.fields.items()}
        out = {}
        for key, sub in fs.fields.items():
            p = f"{path}.{key}" if path else key
            if sub.type == "object":
                out[key] = self._object(sub, scoped, doc, ev, p)
            elif sub.type == "list":
                out[key] = self._list(sub, scoped, doc, ev, p)
            else:
                out[key] = self._leaf(sub, scoped, doc, ev, p)
        return out

    def _list(self, fs: FieldSpec, text: str, doc, ev, path) -> list:
        scoped = self._scope(fs, text)
        if scoped is None:
            return []
        items = fs.items
        if fs.findall:
            fa = fs.findall
            pat = re.compile(fa["regex"], _flags(fa.get("flags")))
            seen, out = set(), []
            for m in pat.finditer(scoped):
                val = (m.groupdict().get("v") or m.group(1) or "").strip()
                if not val or val.lower() in seen:
                    continue
                seen.add(val.lower())
                i = len(out)
                if items.type == "object":
                    obj = {k: None for k in items.fields}
                    obj[fa["field"]] = val
                    out.append(obj)
                    ev[f"{path}[{i}].{fa['field']}"] = self._ev(doc, m.group(0), "regex")
                else:
                    out.append(val)
                    ev[f"{path}[{i}]"] = self._ev(doc, m.group(0), "regex")
                if fa.get("max_items") and len(out) >= int(fa["max_items"]):
                    break
            return out
        chunks = re.split(fs.split, scoped, flags=re.I) if fs.split else [scoped]
        out = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            i = len(out)
            if items.type == "object":
                obj = self._object(items, chunk, doc, ev, f"{path}[{i}]")
                if obj and any(v not in (None, [], {}) for v in obj.values()):
                    out.append(obj)
            else:
                v = self._leaf(items, chunk, doc, ev, f"{path}[{i}]")
                if v is not None:
                    out.append(v)
        return out

    def _leaf(self, fs: FieldSpec, text: str, doc, ev, path) -> Any:
        for rule in fs.rules:
            val, quote = self._apply_rule(rule, text, doc)
            if val not in (None, ""):
                ev[path] = self._ev(doc, quote, rule.get("name") or ("regex" if "regex" in rule else "rule"))
                return val
        return None

    def _apply_rule(self, rule: dict, text: str, doc) -> tuple[Any, str]:
        if "const" in rule:
            return rule["const"], ""
        if "regex" in rule:
            src = doc.text if rule.get("source") == "raw" else text
            if rule.get("page") and rule.get("source") == "raw":
                pg = [p for p in doc.pages if p.number == int(rule["page"])]
                src = pg[0].text if pg else ""
            if rule.get("before"):  # szukaj tylko PRZED pierwszym wystąpieniem znacznika
                mb = re.search(rule["before"], src, flags=_flags(rule.get("before_flags")))
                if mb:
                    src = src[: mb.start()]
            ms = list(re.finditer(rule["regex"], src, flags=_flags(rule.get("flags"))))
            nth = int(rule.get("nth", 0))
            if not ms or nth >= len(ms) or nth < -len(ms):
                return None, ""
            m = ms[nth]
            gd = m.groupdict()
            val = gd.get("v") if "v" in gd else (m.group(1) if m.groups() else m.group(0))
            if val is not None and "map" in rule:  # mapowanie znalezionego tekstu na wartość
                for k, v in rule["map"].items():
                    if re.search(k, val, flags=re.I):
                        return v, m.group(0)
                return rule.get("map_default"), m.group(0)
            if val is not None and "value" in rule:  # sam fakt dopasowania daje wartość
                return rule["value"], m.group(0)
            return (val.strip() if isinstance(val, str) else val), m.group(0)
        if "dictionary" in rule:
            spec = rule["dictionary"]
            entries = self.cfg.dictionaries.get(spec["name"], [])
            # Okna do przeszukania: każde wystąpienie kotwicy (po kolei) albo cały tekst.
            # Pierwsze okno, w którym pasuje jakikolwiek wzorzec słownika, wygrywa.
            windows: list[tuple[str, re.Match | None]] = []
            if spec.get("anchor"):
                for m in re.finditer(spec["anchor"], text, flags=re.I):
                    w = text[m.start(): m.start() + int(spec.get("window", 400))]
                    if spec.get("sentence"):  # utnij na końcu zdania
                        se = re.search(r"[.;]\s+(?-i:[A-ZĄĆĘŁŃÓŚŹŻ])", w[m.end() - m.start():])
                        if se:
                            w = w[: m.end() - m.start() + se.start() + 1]
                    windows.append((w, m))
                if not windows and not spec.get("anchor_required"):
                    windows = [(text, None)]
            else:
                windows = [(text, None)]
            for window, am in windows:
                best = None  # (pozycja, wpis, fragment) – w oknie wygrywa najwcześniejsze dopasowanie
                for e in entries:
                    for p in e.get("patterns", []):
                        mm = re.search(p, window, flags=re.I)
                        if mm and (best is None or mm.start() < best[0]):
                            best = (mm.start(), e, window[: mm.end() + 20] if am else window[max(0, mm.start() - 60): mm.end() + 20])
                if best:
                    if spec.get("return") == "anchor" and am is not None:  # zwróć grupę v z kotwicy (np. datę)
                        return am.groupdict().get("v"), best[2]
                    return best[1].get(spec.get("field", "id")), best[2]
            return None, ""
        return None, ""

    @staticmethod
    def _ev(doc, quote: str, method: str) -> dict:
        q = (quote or "").strip()
        return {"quote": q[:300], "page": doc.find_page(q) if q else None, "method": method}
