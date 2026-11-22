"""Wspólne struktury danych i interfejsy (kontrakt między modułami).

Moduł odczytu PDF implementuje ``TextExtractor``, klasyfikator ``Classifier``,
ekstraktory (reguły, LLM) ``Extractor``.
Implementacje wskazuje się w ``config/settings.yaml`` ścieżką ``modul:Klasa``,
więc podmiana silnika OCR albo modelu LLM nie wymaga zmian w pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class Page:
    number: int                      # 1-based
    text: str
    source: str = "text"             # "text" (warstwa tekstowa) | "ocr"
    confidence: float | None = None  # średnia pewność OCR 0..1 (None dla warstwy tekstowej)


@dataclass
class ExtractedDocument:
    path: Path
    pages: list[Page]
    meta: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\f\n".join(p.text for p in self.pages)

    @property
    def flat_text(self) -> str:
        """Tekst ze zbitymi białymi znakami – wygodny dla regexów."""
        t = self.text.replace("­", "")
        t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
        return re.sub(r"[\s ]+", " ", t).strip()

    def find_page(self, snippet: str) -> int | None:
        """Numer strony, na której występuje fragment (do transparentności)."""
        if not snippet:
            return None
        probe = re.sub(r"\s+", " ", snippet).strip()[:60]
        for p in self.pages:
            if probe and probe in re.sub(r"\s+", " ", p.text):
                return p.number
        return None


@dataclass
class ClassificationResult:
    doc_type: str | None             # None = dokument nierozpoznany / negatywny
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    method: str = ""
    reason: str = ""


@dataclass
class ExtractionResult:
    data: dict
    # ścieżka pola -> {"quote": str, "page": int|None, "method": str, ...}
    evidence: dict[str, dict] = field(default_factory=dict)
    method: str = ""
    error: str | None = None


@runtime_checkable
class TextExtractor(Protocol):
    def extract(self, path: Path) -> ExtractedDocument: ...


@runtime_checkable
class Classifier(Protocol):
    def classify(self, doc: ExtractedDocument) -> ClassificationResult: ...


@runtime_checkable
class Extractor(Protocol):
    name: str

    def extract(self, doc: ExtractedDocument, doc_type: Any) -> ExtractionResult: ...
