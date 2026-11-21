"""CLI: python -m pdf_ingest <plik|katalog> [--out katalog] [--config ingest.yaml]

Dla każdego PDF zapisuje <nazwa>.txt (tekst) i <nazwa>.ingest.json (metadane),
zachowując strukturę podkatalogów (case1/, case2/...).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import IngestConfig
from .loader import IMAGE_SUFFIXES, PdfLoadError
from .pipeline import extract_document


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pdf_ingest", description="PDF/skan -> tekst + metadane OCR")
    ap.add_argument("input", help="plik PDF/obraz albo katalog (rekurencyjnie)")
    ap.add_argument("--out", help="katalog wyjściowy (domyślnie: wypisz podsumowanie)")
    ap.add_argument("--config", help="plik YAML z konfiguracją")
    ap.add_argument("--engine", choices=["auto", "tesseract", "rapidocr"])
    ap.add_argument("--dpi", type=int)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--priority-pages", type=int, dest="priority_pages")
    ap.add_argument("--ocr-remaining", choices=["all", "budget", "none"], dest="ocr_remaining")
    ap.add_argument("--no-words", action="store_true", help="nie zapisuj bbox słów w JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = IngestConfig.load(args.config, engine=args.engine, dpi=args.dpi, workers=args.workers,
                            priority_pages=args.priority_pages, ocr_remaining=args.ocr_remaining)
    root = Path(args.input)
    suffixes = {".pdf"} | IMAGE_SUFFIXES
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.suffix.lower() in suffixes)
    if not files:
        print(f"Brak plików w {root}", file=sys.stderr)
        return 1

    rc = 0
    for f in files:
        try:
            doc = extract_document(f, cfg)
        except PdfLoadError as exc:
            print(f"[BŁĄD] {f}: {exc}", file=sys.stderr)
            rc = 2
            continue
        srcs = ",".join(sorted({p.source for p in doc.pages}))
        print(f"{f}  pages={doc.page_count} kind={doc.doc_kind} src={srcs} "
              f"conf={doc.mean_confidence:.2f} {doc.processing_ms} ms")
        for w in doc.warnings:
            print(f"    ! {w}")
        if args.out:
            rel = f.relative_to(root) if root.is_dir() else Path(f.name)
            target = Path(args.out) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.with_suffix(".txt").write_text(doc.text, encoding="utf-8")
            target.with_suffix(".ingest.json").write_text(
                json.dumps(doc.to_dict(include_words=not args.no_words), ensure_ascii=False, indent=2),
                encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
