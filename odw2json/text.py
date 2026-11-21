"""Domyślny ekstraktor tekstu z PDF: warstwa tekstowa + awaryjny OCR (Tesseract CLI).

To jest implementacja zastępcza – Osoba 1 podmienia ją na docelowy moduł OCR
(wskazując klasę w settings.yaml -> pipeline.text_extractor). Kontrakt:
``extract(path) -> ExtractedDocument`` (strony z numerem, źródłem i pewnością OCR).

OCR uruchamia się tylko dla stron bez warstwy tekstowej (skany) i tylko dla
pierwszych ``ocr_max_pages`` stron (kluczowe dane są na początku dokumentu).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .document import ExtractedDocument, Page

log = logging.getLogger("odw2json.text")


class PdfTextExtractor:
    def __init__(self, cfg=None, max_pages: int | None = None, backend: str = "auto",
                 ocr: bool = True, ocr_lang: str = "pol", ocr_dpi: int = 200, ocr_max_pages: int = 4,
                 min_chars_per_page: int = 40, **_):
        self.max_pages = max_pages
        self.backend = backend
        self.ocr = ocr
        self.ocr_lang = ocr_lang
        self.ocr_dpi = ocr_dpi
        self.ocr_max_pages = ocr_max_pages
        self.min_chars = min_chars_per_page

    # ------------------------------------------------------------------ warstwa tekstowa
    def _text_pages(self, path: Path) -> tuple[list[Page], str]:
        backend = self.backend
        if backend == "auto":
            backend = "pdftotext" if shutil.which("pdftotext") else "pypdf"
        pages: list[Page] = []
        if backend == "pdftotext":
            args = ["pdftotext", "-enc", "UTF-8"]
            if self.max_pages:
                args += ["-l", str(self.max_pages)]
            out = subprocess.run(args + [str(path), "-"], capture_output=True, text=True, timeout=120).stdout
            parts = out.split("\f")
            if parts and not parts[-1].strip():
                parts = parts[:-1]
            pages = [Page(number=i + 1, text=t) for i, t in enumerate(parts)] or [Page(1, "")]
        else:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            for i, p in enumerate(reader.pages):
                if self.max_pages and i >= self.max_pages:
                    break
                pages.append(Page(number=i + 1, text=p.extract_text() or ""))
        return pages, backend

    # ------------------------------------------------------------------ OCR
    def _ocr_available(self) -> bool:
        return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))

    def _ocr_page(self, path: Path, number: int, tmp: Path) -> Page:
        prefix = tmp / f"p{number}"
        subprocess.run(["pdftoppm", "-r", str(self.ocr_dpi), "-f", str(number), "-l", str(number), "-png",
                        str(path), str(prefix)], check=True, capture_output=True, timeout=120)
        img = next(tmp.glob(f"p{number}*.png"))
        tsv = subprocess.run(["tesseract", str(img), "-", "-l", self.ocr_lang, "--psm", "3", "tsv"],
                             capture_output=True, text=True, timeout=300).stdout
        lines: dict[tuple, list[str]] = {}
        confs = []
        for row in tsv.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or not c[11].strip():
                continue
            key = (int(c[2]), int(c[3]), int(c[4]))  # block, paragraph, line
            lines.setdefault(key, []).append(c[11])
            try:
                if float(c[10]) >= 0:
                    confs.append(float(c[10]))
            except ValueError:
                pass
        text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
        conf = round(sum(confs) / len(confs) / 100, 3) if confs else 0.0
        return Page(number=number, text=text, source="ocr", confidence=conf)

    def extract(self, path: Path) -> ExtractedDocument:
        path = Path(path)
        pages, backend = self._text_pages(path)
        meta = {"backend": backend, "ocr_pages": []}
        need = [p.number for p in pages if len(p.text.strip()) < self.min_chars][: self.ocr_max_pages]
        if need and self.ocr:
            if self._ocr_available():
                with tempfile.TemporaryDirectory() as td:
                    for n in need:
                        try:
                            pages[n - 1] = self._ocr_page(path, n, Path(td))
                            meta["ocr_pages"].append(n)
                        except Exception as e:  # OCR nie może zatrzymać pipeline'u
                            log.warning("OCR strony %s w %s nie powiódł się: %s", n, path.name, e)
            else:
                log.warning("%s wygląda na skan, ale brak tesseract/pdftoppm – pomijam OCR", path.name)
                meta["ocr_missing"] = True
        doc = ExtractedDocument(path=path, pages=pages, meta=meta)
        doc.meta["has_text_layer"] = any(p.source == "text" and len(p.text.strip()) >= self.min_chars for p in pages)
        return doc
