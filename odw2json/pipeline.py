"""Orkiestracja: PDF -> tekst -> klasyfikacja -> ekstrakcja (N ekstraktorów) -> scalenie -> normalizacja -> JSON.

Wszystkie komponenty wskazuje ``config/settings.yaml``::

    pipeline:
      text_extractor: {class: odw2json.text:PdfTextExtractor, options: {max_pages: 4}}
      classifier:     {class: odw2json.classify:RuleClassifier}
      extractors:                       # kolejność = priorytet przy scalaniu
        - {class: team.llm:LLMExtractor, options: {model: ...}}
        - {class: odw2json.extract_rules:RuleExtractor}
"""
from __future__ import annotations

import importlib
import json
import logging
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import AppConfig, DocumentType, FieldSpec, load_config
from .document import ClassificationResult, ExtractedDocument, ExtractionResult
from .postprocess import Postprocessor
from .schema_builder import empty_instance

log = logging.getLogger("odw2json")


def load_plugin(spec: dict | str, cfg: AppConfig):
    if isinstance(spec, str):
        spec = {"class": spec}
    mod_name, _, cls_name = spec["class"].partition(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    return cls(cfg, **(spec.get("options") or {}))


@dataclass
class DocumentResult:
    input_path: str
    doc_type: str | None
    data: dict | None
    classification: dict
    evidence: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    extractors: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    error: str | None = None


class Pipeline:
    def __init__(self, cfg: AppConfig | None = None):
        self.cfg = cfg or load_config()
        p = self.cfg.get("pipeline", {}) or {}
        self.text_extractor = load_plugin(p.get("text_extractor", "odw2json.text:PdfTextExtractor"), self.cfg)
        self.classifier = load_plugin(p.get("classifier", "odw2json.classify:RuleClassifier"), self.cfg)
        self.extractors = [load_plugin(s, self.cfg) for s in p.get("extractors", ["odw2json.extract_rules:RuleExtractor"])]
        self.post = Postprocessor(self.cfg)

    # ------------------------------------------------------------------ jeden plik
    def process_file(self, path: str | Path, doc_type: str | None = None) -> DocumentResult:
        """``doc_type`` (opcjonalnie) wymusza typ dokumentu z config/document_types zamiast klasyfikacji."""
        path = Path(path)
        t0 = time.perf_counter()
        timings = {}
        try:
            if doc_type is not None and doc_type not in self.cfg.document_types:
                raise ValueError(f"Nieznany typ dokumentu: {doc_type!r} (dostępne: {', '.join(self.cfg.document_types)})")
            doc = self.text_extractor.extract(path)
            timings["text"] = time.perf_counter() - t0
            cls = self.classifier.classify(doc)
            if doc_type is not None:  # wybór użytkownika (np. w UI) ma pierwszeństwo przed klasyfikatorem
                auto = cls.doc_type
                cls = ClassificationResult(doc_type, 1.0, cls.scores, "manual",
                                           f"Typ wybrany ręcznie (klasyfikator: {auto or 'nierozpoznany'})")
            timings["classify"] = time.perf_counter() - t0 - timings["text"]
            if cls.doc_type is None:
                return DocumentResult(str(path), None, None, asdict(cls), timings=timings)
            dt = self.cfg.document_types[cls.doc_type]
            results: list[ExtractionResult] = []
            for ex in self.extractors:
                t1 = time.perf_counter()
                try:
                    results.append(ex.extract(doc, dt))
                except Exception as e:  # jeden ekstraktor padł -> reszta działa dalej
                    log.warning("Ekstraktor %s: %s", getattr(ex, "name", ex), e)
                    results.append(ExtractionResult(data={}, method=getattr(ex, "name", "?"), error=str(e)))
                timings[f"extract:{getattr(ex, 'name', '?')}"] = time.perf_counter() - t1
            merged, evidence = merge_results(dt, results)
            pp = self.post.run(dt, merged)
            timings["total"] = time.perf_counter() - t0
            return DocumentResult(
                str(path), dt.id, pp.data, asdict(cls), evidence, pp.warnings,
                {r.method: {"error": r.error} for r in results}, timings,
            )
        except Exception as e:
            log.exception("Błąd przetwarzania %s", path)
            return DocumentResult(str(path), None, None, {}, error=f"{e}\n{traceback.format_exc()}")

    # ------------------------------------------------------------------ katalog input/ -> output/
    def process_dir(self, input_dir: str | Path, output_dir: str | Path, audit_dir: str | Path | None = None) -> list[DocumentResult]:
        input_dir, output_dir = Path(input_dir), Path(output_dir)
        out_cfg = self.cfg.get("output", {}) or {}
        audit_dir = Path(audit_dir) if audit_dir else (Path(out_cfg["audit_dir"]) if out_cfg.get("audit_dir") else None)
        results = []
        for pdf in sorted(p for p in input_dir.rglob("*") if p.suffix.lower() == ".pdf"):
            rel = pdf.relative_to(input_dir).with_suffix(".json")
            r = self.process_file(pdf)
            results.append(r)
            write_output(output_dir / rel, r, out_cfg)
            if audit_dir:
                (audit_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                (audit_dir / rel).write_text(json.dumps(asdict(r), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            log.info("%s -> %s", pdf, r.doc_type or "NIEROZPOZNANY")
        return results


def write_output(path: Path, r: DocumentResult, out_cfg: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    if r.data is None:
        # dokument negatywny / nierozpoznany: README UZP -> "pusty plik .json"
        mode = out_cfg.get("negative", "empty_file")
        path.write_text("" if mode == "empty_file" else "{}", encoding="utf-8")
        return
    path.write_text(json.dumps(r.data, ensure_ascii=False, indent=int(out_cfg.get("indent", 2))), encoding="utf-8")


# ---------------------------------------------------------------------- scalanie wyników ekstraktorów
def merge_results(dt: DocumentType, results: list[ExtractionResult]) -> tuple[dict, dict]:
    """Scala wyniki wielu ekstraktorów zgodnie z priorytetem.

    Domyślnie: pierwszy (wg kolejności w settings) niepusty wynik wygrywa.
    Pole może wymusić preferencję w YAML: ``prefer: [rules, llm]``.
    Listy bierzemy w całości z jednego ekstraktora (nie mieszamy elementów).
    """
    evidence: dict[str, dict] = {}

    def pick(fs: FieldSpec, values: list[tuple[str, Any]], path: str):
        order = fs.raw.get("prefer")
        if order:
            rank = {n: i for i, n in enumerate(order)}
            values = sorted(values, key=lambda nv: rank.get(nv[0], len(rank)))
        if fs.type == "object":
            out = {}
            for k, sub in fs.fields.items():
                sub_vals = [(n, (v or {}).get(k) if isinstance(v, dict) else None) for n, v in values]
                out[k] = pick(sub, sub_vals, f"{path}.{k}" if path else k)
            return out
        for name, v in values:
            if v not in (None, "", [], {}):
                src = next(r for r in results if r.method == name)
                prefix = path
                for ep, e in src.evidence.items():
                    if ep == prefix or ep.startswith(prefix + ".") or ep.startswith(prefix + "["):
                        evidence[ep] = {**e, "extractor": name}
                return v
        return [] if fs.type == "list" else None

    merged = pick(dt.schema, [(r.method, r.data) for r in results], "")
    return merged, evidence
