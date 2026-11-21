"""Fabryka silników OCR. Silnik ładowany leniwie – PDF-y cyfrowe działają nawet bez OCR."""
from __future__ import annotations

import threading

from ..config import IngestConfig
from .base import OcrEngine, OcrOutput, OcrUnavailable

_lock = threading.Lock()
_cache: dict[tuple, OcrEngine] = {}


def get_engine(cfg: IngestConfig) -> OcrEngine:
    key = (cfg.engine, cfg.lang, cfg.tesseract_psm, cfg.tesseract_cmd, cfg.tessdata_dir, cfg.layout)
    with _lock:
        if key in _cache:
            return _cache[key]
        order = {"auto": ["tesseract", "rapidocr"], "tesseract": ["tesseract"], "rapidocr": ["rapidocr"]}
        errors = []
        for name in order.get(cfg.engine, [cfg.engine]):
            try:
                if name == "tesseract":
                    from .tesseract_engine import TesseractEngine
                    eng = TesseractEngine(cfg.lang, cfg.tesseract_psm, cfg.tesseract_cmd, cfg.tessdata_dir,
                                          single_thread=cfg.effective_workers() > 1, layout=cfg.layout)
                elif name == "rapidocr":
                    from .rapidocr_engine import RapidOcrEngine
                    eng = RapidOcrEngine(cfg.extra.get("rapidocr_params"))
                else:
                    raise OcrUnavailable(f"Nieznany silnik: {name}")
                _cache[key] = eng
                return eng
            except OcrUnavailable as exc:
                errors.append(f"{name}: {exc}")
        raise OcrUnavailable("; ".join(errors))


__all__ = ["get_engine", "OcrEngine", "OcrOutput", "OcrUnavailable"]
