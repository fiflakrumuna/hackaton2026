"""Główny pipeline: PDF/obraz -> DocumentResult (tekst + metadane per strona).

    from pdf_ingest import extract_document
    doc = extract_document("input/case1/odwolanie.pdf")
    print(doc.doc_kind, doc.mean_confidence)
    print(doc.priority_text)

Strategia:
  1. Analiza KAŻDEJ strony (tania): warstwa tekstowa, jej jakość, pokrycie obrazem.
  2. Strony z dobrą warstwą tekstową -> tekst od razu (ms/stronę).
  3. Strony-skany: najpierw priorytetowe (1..N + ostatnie K), równolegle;
     reszta w ramach budżetu czasu (konfigurowalne).
  4. Każda strona dostaje źródło, pewność, kroki preprocessingu i ostrzeżenia.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np

from .config import IngestConfig
from .engines import OcrUnavailable, get_engine
from .loader import PageAnalysis, PdfLoadError, SourceDocument, sha256_of
from .models import DocumentResult, PageResult
from .postprocess import clean_text
from .preprocess import alternative_preprocess, preprocess
from .quality import text_quality

log = logging.getLogger("pdf_ingest")
CACHE_VERSION = 1


def priority_indices(page_count: int, cfg: IngestConfig) -> list[int]:
    head = list(range(min(cfg.priority_pages, page_count)))
    tail = list(range(max(0, page_count - cfg.tail_pages), page_count))
    return sorted(set(head + tail))


def _decide(a: PageAnalysis, cfg: IngestConfig) -> str:
    """'text' | 'ocr' | 'compare' | 'empty'."""
    if a.is_blank:
        return "empty"
    good_text = a.text_chars >= cfg.min_text_chars and a.text_quality >= cfg.min_text_quality
    scanned = a.image_coverage >= cfg.image_coverage_scan
    if good_text:
        if scanned and cfg.reocr_scanned_text_layer == "always":
            return "ocr"
        if scanned and cfg.reocr_scanned_text_layer == "auto" and a.text_quality < 0.8:
            return "compare"   # skan z warstwą OCR skanera średniej jakości – porównaj
        return "text"
    if a.text_chars > 0 and cfg.reocr_scanned_text_layer == "never" and scanned:
        return "text"
    if a.text_chars >= cfg.min_text_chars:
        return "compare"       # jest trochę tekstu, ale słaby – OCR i wybierz lepszy
    return "ocr"


def _vertical_fraction(words) -> float:
    """Odsetek dłuższych słów o ramce wyższej niż szerszej – sygnał strony obróconej o 90°.
    Tesseract potrafi przeczytać tekst bokiem z wysoką pewnością, ale kolejność wierszy jest wtedy zła."""
    long = [w for w in words if len(w.text) >= 4]
    if len(long) < 5:
        return 0.0
    return sum(1 for w in long if (w.bbox[3] - w.bbox[1]) > (w.bbox[2] - w.bbox[0])) / len(long)


def _ocr_page(engine, gray: np.ndarray, cfg: IngestConfig) -> dict:
    t0 = time.perf_counter()
    kw = dict(deskew=cfg.deskew, max_skew=cfg.max_skew_deg, background=cfg.normalize_background,
              denoise=cfg.denoise, redactions=cfg.remove_redactions, dot_leaders=cfg.remove_dot_leaders)
    pre = preprocess(gray, rotation=0, **kw)
    out = engine.ocr(pre.image)
    attempts = 1
    # OSD (~1 s/stronę) tylko gdy wynik jest podejrzanie słaby – tekst do góry nogami / bokiem
    # zawsze daje niską pewność, więc przy dobrych stronach nic nie tracimy.
    sideways = _vertical_fraction(out.words) > cfg.osd_trigger_vertical
    if cfg.detect_orientation and (out.confidence < cfg.osd_trigger_confidence or sideways):
        f = 1600 / max(gray.shape)
        small = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else gray
        rotation = engine.detect_rotation(np.ascontiguousarray(small))
        # OSD bywa niepewne (np. pewność 1.3 < progu) albo nie działa na małym obrazie –
        # wtedy próbujemy 180° i zostawiamy wynik tylko, jeśli OCR ma wyższą pewność
        candidates = [rotation] if rotation else [180]
        if sideways and rotation not in (90, 270):
            candidates = [90, 270]          # OSD niepewne, ale wiemy, że strona leży bokiem
        best_score = -1.0 if sideways else out.confidence
        for rot in candidates:
            pre_r = preprocess(gray, rotation=rot, **kw)
            out_r = engine.ocr(pre_r.image)
            attempts += 1
            # strona bokiem: Tesseract potrafi ją przeczytać z wysoką pewnością, ale
            # z rozsypaną kolejnością wierszy – wtedy liczy się wynik w poprawnej orientacji
            ok = (not sideways) or _vertical_fraction(out_r.words) < 0.2
            if ok and out_r.confidence > best_score:
                pre, out, best_score = pre_r, out_r, out_r.confidence
    rotation = pre.rotation
    steps, img = list(pre.steps), pre.image
    if out.confidence < cfg.retry_low_confidence:
        alt_img, alt_steps = alternative_preprocess(pre.image)
        alt = engine.ocr(alt_img)
        attempts += 1
        if alt.confidence > out.confidence + 0.02:
            out, img = alt, alt_img
            steps += alt_steps
    return {
        "out": out, "rotation": rotation, "skew": pre.skew_deg, "steps": steps,
        "attempts": attempts, "size": (int(img.shape[1]), int(img.shape[0])),
        "ms": int((time.perf_counter() - t0) * 1000),
    }


def _fill_from_ocr(page: PageResult, r: dict, engine, cfg: IngestConfig) -> None:
    out = r["out"]
    text, fixes = clean_text(out.text, is_ocr=True, do_dehyphenate=cfg.dehyphenate,
                             do_fix_digits=cfg.fix_ocr_digits)
    page.source = "ocr"
    page.text = text
    page.char_count = len(text)
    page.confidence = out.confidence
    page.engine = engine.name
    page.rotation_applied = r["rotation"]
    page.skew_corrected_deg = r["skew"]
    page.preprocessing = r["steps"]
    page.attempts = r["attempts"]
    page.image_size = r["size"]
    page.postprocess_fixes = fixes
    page.low_confidence_words = [w.text for w in out.words if w.confidence < cfg.low_conf_word_threshold][:200]
    page.words = out.words if cfg.keep_words else []
    page.processing_ms += r["ms"]
    if out.confidence < 0.6:
        page.warnings.append(f"niska pewność OCR ({out.confidence:.2f}) – zweryfikuj ręcznie")
    if not text.strip():
        page.warnings.append("OCR nie zwrócił tekstu")


def extract_document(path: str | Path, cfg: IngestConfig | None = None) -> DocumentResult:
    cfg = cfg or IngestConfig.load()
    path = Path(path)
    if not path.is_file():
        raise PdfLoadError(f"Plik nie istnieje: {path}")
    t_start = time.perf_counter()
    digest = sha256_of(path)

    cached = _cache_get(cfg, digest)
    if cached is not None:
        cached.path, cached.file_name, cached.from_cache = str(path), path.name, True
        return cached

    warnings: list[str] = []
    with SourceDocument(path) as src:
        n = src.page_count
        prio = set(priority_indices(n, cfg))
        pages: list[PageResult] = []
        decisions: dict[int, str] = {}
        for i in range(n):
            t0 = time.perf_counter()
            a = src.analyze_page(i)
            d = _decide(a, cfg)
            decisions[i] = d
            p = PageResult(page_number=i + 1, source="pending", text_layer_chars=a.text_chars,
                           text_layer_quality=a.text_quality, image_coverage=a.image_coverage,
                           looks_scanned=a.image_coverage >= cfg.image_coverage_scan, priority=i in prio)
            if d in ("text", "compare"):
                text, _ = clean_text(a.text, is_ocr=False, do_dehyphenate=cfg.dehyphenate)
                p.source, p.text, p.char_count = "text_layer", text, len(text)
                p.confidence = a.text_quality
            elif d == "empty":
                p.source = "empty"
            p.processing_ms = int((time.perf_counter() - t0) * 1000)
            pages.append(p)

        ocr_needed = [i for i, d in decisions.items() if d in ("ocr", "compare")]
        engine = None
        engine_info: dict = {}
        if ocr_needed:
            try:
                engine = get_engine(cfg)
                engine_info = engine.info()
            except OcrUnavailable as exc:
                warnings.append(f"OCR niedostępny: {exc}")
                for i in ocr_needed:
                    if decisions[i] == "ocr":
                        pages[i].source = "ocr_unavailable"
                        pages[i].warnings.append("strona jest skanem, ale brak silnika OCR")

        if engine is not None:
            first = [i for i in ocr_needed if i in prio]
            rest = [i for i in ocr_needed if i not in prio]
            if cfg.ocr_remaining == "none":
                skip, rest = rest, []
            else:
                skip = []
            _run_ocr_batch(src, engine, first, pages, decisions, cfg, deadline=None)
            deadline = None if cfg.ocr_remaining == "all" else t_start + cfg.time_budget_s
            skip += _run_ocr_batch(src, engine, rest, pages, decisions, cfg, deadline=deadline)
            for i in skip:
                if decisions[i] == "ocr":
                    pages[i].source = "skipped"
                    pages[i].warnings.append("strona pominięta (poza priorytetem / budżetem czasu)")
            if skip:
                warnings.append(f"pominięto OCR stron: {[i + 1 for i in sorted(skip)]}")

    kinds = {p.source for p in pages if p.source not in ("empty",)}
    if not kinds:
        doc_kind = "empty"
    elif kinds <= {"text_layer"}:
        doc_kind = "digital"
    elif "text_layer" in kinds:
        doc_kind = "mixed"
    else:
        doc_kind = "scanned"

    result = DocumentResult(
        path=str(path), file_name=path.name, sha256=digest, page_count=len(pages),
        doc_kind=doc_kind, pages=pages, engine_info=engine_info, warnings=warnings,
        processing_ms=int((time.perf_counter() - t_start) * 1000),
    )
    _cache_put(cfg, result)
    return result


def _run_ocr_batch(src: SourceDocument, engine, indices: list[int], pages: list[PageResult],
                   decisions: dict[int, str], cfg: IngestConfig, deadline: float | None) -> list[int]:
    """OCR listy stron. Renderowanie sekwencyjne (PDFium), OCR równolegle. Zwraca pominięte."""
    if not indices:
        return []
    workers = cfg.effective_workers()
    skipped: list[int] = []
    queue = list(indices)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        while queue or futures:
            # dokładaj zadania do puli, ograniczając pamięć do ~2x workers wyrenderowanych stron
            while queue and len(futures) < workers * 2:
                if deadline is not None and time.perf_counter() > deadline:
                    skipped.extend(queue)
                    queue = []
                    break
                i = queue.pop(0)
                t0 = time.perf_counter()
                gray = src.render_gray(i, cfg.dpi, cfg.max_long_side_px)
                pages[i].processing_ms += int((time.perf_counter() - t0) * 1000)
                futures[pool.submit(_ocr_page, engine, gray, cfg)] = i
            if not futures:
                break
            done = next(as_completed(futures))
            i = futures.pop(done)
            try:
                r = done.result()
            except Exception as exc:  # pojedyncza strona nie może wywrócić dokumentu
                log.exception("OCR strony %d nie powiódł się", i + 1)
                pages[i].warnings.append(f"błąd OCR: {exc}")
                if decisions[i] == "ocr":
                    pages[i].source = "ocr_unavailable"
                continue
            if decisions[i] == "compare":
                ocr_q = text_quality(r["out"].text)
                score_ocr = 0.5 * ocr_q + 0.5 * r["out"].confidence
                if score_ocr > pages[i].text_layer_quality:
                    pages[i].warnings.append(
                        f"warstwa tekstowa słaba ({pages[i].text_layer_quality:.2f}) – użyto OCR ({score_ocr:.2f})")
                    _fill_from_ocr(pages[i], r, engine, cfg)
                else:
                    pages[i].warnings.append(
                        f"porównano z OCR ({score_ocr:.2f}) – zostawiono warstwę tekstową")
                    pages[i].processing_ms += r["ms"]
            else:
                _fill_from_ocr(pages[i], r, engine, cfg)
    return skipped


# ---------------- cache ----------------
def _cache_path(cfg: IngestConfig, digest: str) -> Path | None:
    if not cfg.cache_dir:
        return None
    return Path(cfg.cache_dir) / f"{digest}.v{CACHE_VERSION}.json"


def _cache_get(cfg: IngestConfig, digest: str) -> DocumentResult | None:
    p = _cache_path(cfg, digest)
    if p is None or not p.exists():
        return None
    try:
        return DocumentResult.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def _cache_put(cfg: IngestConfig, doc: DocumentResult) -> None:
    p = _cache_path(cfg, doc.sha256)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc.to_dict(), ensure_ascii=False), encoding="utf-8")


def extract_many(paths: list[str | Path], cfg: IngestConfig | None = None) -> list[DocumentResult]:
    cfg = cfg or IngestConfig.load()
    return [extract_document(p, cfg) for p in paths]
