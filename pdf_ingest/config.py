"""Konfiguracja warstwy wejściowej (PDF + OCR).

Wszystkie parametry mają rozsądne wartości domyślne; można je nadpisać plikiem
YAML (config/ingest.yaml) albo zmiennymi środowiskowymi PDF_INGEST_<NAZWA>.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any


@dataclass
class IngestConfig:
    # --- detekcja warstwy tekstowej ---
    min_text_chars: int = 40            # mniej znaków na stronie => strona traktowana jak skan
    min_text_quality: float = 0.55      # 0..1, poniżej => warstwa tekstowa uznana za śmieciową
    image_coverage_scan: float = 0.6    # obraz zajmuje >= 60% strony => strona "wygląda jak skan"
    # co zrobić ze stroną, która jest skanem z (niewidoczną) warstwą OCR skanera:
    #   "auto"   - użyj warstwy, jeśli jej jakość jest dobra, inaczej OCR i wybierz lepszy wynik
    #   "always" - zawsze robimy własny OCR
    #   "never"  - zawsze ufamy warstwie tekstowej
    reocr_scanned_text_layer: str = "auto"

    # --- priorytet stron ---
    priority_pages: int = 4             # pierwsze N stron OCR-ujemy zawsze
    tail_pages: int = 1                 # + ostatnie K stron (podpisy, daty, załączniki)
    ocr_remaining: str = "budget"       # "all" | "budget" | "none" - co z resztą stron skanu
    time_budget_s: float = 45.0         # budżet czasu na dokument dla stron spoza priorytetu

    # --- OCR ---
    engine: str = "auto"                # "auto" | "tesseract" | "rapidocr"
    lang: str = "pol"
    dpi: int = 300
    max_long_side_px: int = 3508        # limit renderu (A4@300dpi); chroni przed skanami z dużym MediaBox
    layout: str = "rows"                # "rows" - kolejność z geometrii (etykiety z lewej kolumny) | "tesseract"
    workers: int = 0                   # 0 => liczba rdzeni CPU (max 8)
    tesseract_cmd: str | None = None    # ścieżka do tesseract.exe (None => autodetekcja)
    tessdata_dir: str | None = None     # własny katalog z pol.traineddata (np. tessdata_best)
    tesseract_psm: int = 3
    retry_low_confidence: float = 0.70  # średnia pewność < progu => druga próba z innym preprocessingiem
    keep_words: bool = True             # zapisuj słowa z bbox i pewnością (transparentność / GUI)
    low_conf_word_threshold: float = 0.6

    # --- preprocessing skanów ---
    detect_orientation: bool = True     # obrót 90/180/270 (Tesseract OSD)
    osd_trigger_confidence: float = 0.65  # OSD tylko gdy 1. podejście OCR ma pewność poniżej progu
    osd_trigger_vertical: float = 0.35    # ...albo gdy tyle słów ma ramki "pionowe" (strona bokiem)
    remove_dot_leaders: bool = True     # usuń kropkowane prowadnice "KWOTA ...... 34,00"
    remove_redactions: bool = True      # zamaluj czarne prostokąty anonimizacji (inaczej OCR daje śmieci)
    deskew: bool = True                 # prostowanie drobnych przekrzywień
    max_skew_deg: float = 15.0
    denoise: str = "auto"               # "auto" | "always" | "never"
    normalize_background: str = "auto"  # "auto" | "always" | "never" - cienie, nierówne oświetlenie

    # --- postprocessing tekstu ---
    dehyphenate: bool = True
    fix_ocr_digits: bool = True         # O->0, l->1 itp. w tokenach będących numerami (NIP, KRS...)

    # --- inne ---
    cache_dir: str | None = None        # np. ".cache/ingest" - wyniki po sha256 pliku
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> "IngestConfig":
        data: dict[str, Any] = {}
        if path is None:
            default = Path(os.environ.get("PDF_INGEST_CONFIG", "config/ingest.yaml"))
            path = default if default.exists() else None
        if path is not None:
            import yaml  # lokalny import: yaml potrzebny tylko gdy jest plik
            with open(path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            data.update(loaded.get("ingest", loaded))
        known = {f.name: f for f in fields(cls)}
        for name, f in known.items():
            env = os.environ.get(f"PDF_INGEST_{name.upper()}")
            if env is not None:
                data[name] = _coerce(env, f.type)
        data.update({k: v for k, v in overrides.items() if v is not None})
        extra = {k: v for k, v in data.items() if k not in known}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        cfg.extra.update(extra)
        return cfg

    def effective_workers(self) -> int:
        if self.workers and self.workers > 0:
            return self.workers
        return max(1, min(8, os.cpu_count() or 1))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce(value: str, typ: Any) -> Any:
    t = str(typ)
    if "bool" in t:
        return value.strip().lower() in {"1", "true", "yes", "tak"}
    if "int" in t and "float" not in t:
        return int(value)
    if "float" in t:
        return float(value)
    return value
