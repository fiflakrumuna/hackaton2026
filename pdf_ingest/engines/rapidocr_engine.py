"""RapidOCR (Apache-2.0, ONNX Runtime, czysty pip) – silnik zapasowy.

UWAGA: domyślne modele RapidOCR są chińsko-angielskie i gubią polskie znaki
(ą, ę, ł, ś...). Do polskiego trzeba podać model rozpoznawania "latin"
z PP-OCR (parametr rec_model_path w config: extra.rapidocr_params).
Dlatego domyślnym silnikiem jest Tesseract; RapidOCR to opcja awaryjna,
gdy na maszynie nie da się zainstalować Tesseracta.
Obsługiwane API: rapidocr>=2 (`from rapidocr import RapidOCR`) oraz
starsze rapidocr_onnxruntime.
"""
from __future__ import annotations

import numpy as np

from ..models import Word
from .base import OcrOutput, OcrUnavailable, weighted_confidence


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, params: dict | None = None):
        params = params or {}
        try:
            from rapidocr import RapidOCR  # type: ignore
            self._new_api = True
        except ImportError:
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore
                self._new_api = False
            except ImportError as exc:
                raise OcrUnavailable("Brak pakietu rapidocr (pip install rapidocr onnxruntime)") from exc
        self._engine = RapidOCR(params=params or None) if self._new_api else RapidOCR(**params)

    def info(self) -> dict:
        return {"engine": self.name, "api": "rapidocr" if self._new_api else "rapidocr_onnxruntime"}

    def ocr(self, image: np.ndarray, psm: int | None = None) -> OcrOutput:
        res = self._engine(image)
        items: list[tuple[list, str, float]] = []
        if self._new_api:
            if res is not None and getattr(res, "txts", None):
                items = list(zip(res.boxes.tolist(), res.txts, res.scores))
        else:
            result = res[0] if isinstance(res, tuple) else res
            items = [(b, t, float(s)) for b, t, s in (result or [])]
        # sortowanie w linie: po środku Y, potem X
        boxes = []
        for box, txt, score in items:
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            boxes.append((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)), txt, float(score)))
        boxes.sort(key=lambda b: ((b[1] + b[3]) / 2, b[0]))
        lines: list[list[tuple]] = []
        for b in boxes:
            cy, hgt = (b[1] + b[3]) / 2, b[3] - b[1]
            if lines and abs(cy - lines[-1][-1][6]) < 0.5 * hgt:
                lines[-1].append((*b, cy))
            else:
                lines.append([(*b, cy)])
        words: list[Word] = []
        text_lines = []
        for li, line in enumerate(lines):
            line.sort(key=lambda b: b[0])
            text_lines.append(" ".join(b[4] for b in line))
            for b in line:
                words.append(Word(b[4], round(b[5], 3), (b[0], b[1], b[2], b[3]), li))
        return OcrOutput("\n".join(text_lines), weighted_confidence(words), words)

    def detect_rotation(self, image: np.ndarray) -> int:
        return 0  # RapidOCR ma klasyfikator kierunku linii wewnątrz pipeline'u
