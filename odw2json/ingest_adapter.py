"""Łącznik: moduł OCR Osoby 1 (pdf_ingest) -> kontrakt backendu (ExtractedDocument).

Włączenie w config/settings.yaml:
    text_extractor: {class: odw2json.ingest_adapter:PdfIngestExtractor}

Jeśli pdf_ingest nie da się zaimportować (brak opencv/pypdfium2) albo padnie na pliku,
używamy zastępczego PdfTextExtractor, żeby aplikacja dalej zwracała wynik.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .document import ExtractedDocument, Page
from .text import PdfTextExtractor

log = logging.getLogger("odw2json.ingest")

_SOURCE_MAP = {"text_layer": "text", "ocr": "ocr"}


class PdfIngestExtractor:
    def __init__(self, cfg=None, max_pages: int | None = None, ingest_config: str | None = None, **opts):
        self.max_pages = max_pages
        self.ingest_config = ingest_config
        self.fallback = PdfTextExtractor(cfg, max_pages=max_pages, **opts)
        try:
            from pdf_ingest import IngestConfig, extract_document
            self._extract = extract_document
            default = Path(__file__).resolve().parent.parent / "config" / "ingest.yaml"
            path = ingest_config or (default if default.exists() else None)
            self._icfg = IngestConfig.load(path)
        except Exception as e:  # brak zależności OCR -> fallback
            log.warning("pdf_ingest niedostępny (%s) – używam PdfTextExtractor", e)
            self._extract = None

    def extract(self, path: Path) -> ExtractedDocument:
        path = Path(path)
        if self._extract is None:
            return self.fallback.extract(path)
        try:
            res = self._extract(path, self._icfg)
        except Exception as e:
            log.warning("pdf_ingest nie przetworzył %s (%s) – używam PdfTextExtractor", path.name, e)
            return self.fallback.extract(path)

        src_pages = res.pages[: self.max_pages] if self.max_pages else res.pages
        pages = [
            Page(
                number=p.page_number,
                text=p.text or "",
                source=_SOURCE_MAP.get(p.source, p.source),
                confidence=p.confidence if p.source == "ocr" else None,
            )
            for p in src_pages
        ] or [Page(1, "")]
        meta = {
            "backend": "pdf_ingest",
            "doc_kind": res.doc_kind,
            "ocr_pages": [p.page_number for p in src_pages if p.source == "ocr"],
            "mean_confidence": res.mean_confidence,
            "warnings": res.warnings,
            "has_text_layer": any(p.source == "text_layer" for p in src_pages),
        }
        return ExtractedDocument(path=path, pages=pages, meta=meta)
