"""Struktury danych zwracane przez warstwę wejściową.

To jest KONTRAKT z resztą zespołu (moduł AI / backend):
    doc = extract_document("plik.pdf")
    doc.text                 -> cały tekst (strony rozdzielone znacznikiem \f)
    doc.priority_text        -> tylko strony priorytetowe (1..N + ostatnie)
    doc.pages[i].text        -> tekst strony
    doc.pages[i].source      -> "text_layer" | "ocr" | "skipped" | "empty" | "ocr_unavailable"
    doc.pages[i].confidence  -> 0..1 (dla warstwy tekstowej = ocena jakości tekstu)
    doc.to_dict()            -> JSON-owalny słownik z metadanymi (transparentność)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

PAGE_SEPARATOR = "\n\f\n"


@dataclass
class Word:
    text: str
    confidence: float           # 0..1
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 w pikselach obrazu po preprocessingu
    line: int                   # numer linii w obrębie strony


@dataclass
class PageResult:
    page_number: int            # od 1
    source: str                 # text_layer | ocr | skipped | empty | ocr_unavailable
    text: str = ""
    confidence: float = 0.0     # 0..1
    char_count: int = 0
    # diagnostyka detekcji
    text_layer_chars: int = 0
    text_layer_quality: float = 0.0
    image_coverage: float = 0.0
    looks_scanned: bool = False
    # diagnostyka OCR
    engine: str | None = None
    rotation_applied: int = 0          # 0/90/180/270
    skew_corrected_deg: float = 0.0
    preprocessing: list[str] = field(default_factory=list)
    attempts: int = 0
    low_confidence_words: list[str] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    image_size: tuple[int, int] | None = None   # (szer, wys) obrazu, do którego odnoszą się bbox
    postprocess_fixes: int = 0
    processing_ms: int = 0
    priority: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, include_words: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if not include_words:
            d.pop("words", None)
        return d


@dataclass
class DocumentResult:
    path: str
    file_name: str
    sha256: str
    page_count: int
    doc_kind: str                       # digital | scanned | mixed | empty
    pages: list[PageResult]
    processing_ms: int = 0
    engine_info: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    from_cache: bool = False

    @property
    def text(self) -> str:
        return PAGE_SEPARATOR.join(p.text for p in self.pages)

    @property
    def priority_text(self) -> str:
        return PAGE_SEPARATOR.join(p.text for p in self.pages if p.priority)

    def text_of(self, pages: list[int]) -> str:
        wanted = set(pages)
        return PAGE_SEPARATOR.join(p.text for p in self.pages if p.page_number in wanted)

    @property
    def mean_confidence(self) -> float:
        scored = [p for p in self.pages if p.source in ("ocr", "text_layer") and p.char_count]
        if not scored:
            return 0.0
        total = sum(p.char_count for p in scored)
        return round(sum(p.confidence * p.char_count for p in scored) / total, 4)

    def to_dict(self, include_words: bool = True, include_text: bool = True) -> dict[str, Any]:
        pages = []
        for p in self.pages:
            pd = p.to_dict(include_words=include_words)
            if not include_text:
                pd.pop("text", None)
            pages.append(pd)
        return {
            "path": self.path,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "page_count": self.page_count,
            "doc_kind": self.doc_kind,
            "mean_confidence": self.mean_confidence,
            "processing_ms": self.processing_ms,
            "engine_info": self.engine_info,
            "warnings": self.warnings,
            "from_cache": self.from_cache,
            "pages": pages,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DocumentResult":
        pages = []
        for pd in d["pages"]:
            pd = dict(pd)
            pd["words"] = [Word(w["text"], w["confidence"], tuple(w["bbox"]), w["line"])
                           for w in pd.get("words", [])]
            if pd.get("image_size"):
                pd["image_size"] = tuple(pd["image_size"])
            pages.append(PageResult(**pd))
        return cls(
            path=d["path"], file_name=d["file_name"], sha256=d["sha256"],
            page_count=d["page_count"], doc_kind=d["doc_kind"], pages=pages,
            processing_ms=d.get("processing_ms", 0), engine_info=d.get("engine_info", {}),
            warnings=d.get("warnings", []), from_cache=d.get("from_cache", False),
        )
