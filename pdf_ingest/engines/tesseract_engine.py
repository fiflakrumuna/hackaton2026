"""Tesseract 5 (Apache-2.0) przez pytesseract – główny silnik dla języka polskiego.

Instalacja na Windows: instalator UB Mannheim (https://github.com/UB-Mannheim/tesseract/wiki),
zaznaczyć język "Polish". Ścieżka C:\\Program Files\\Tesseract-OCR jest wykrywana automatycznie.
Można też trzymać pol.traineddata (np. z tessdata_best) w repo i wskazać tessdata_dir.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import numpy as np

from ..layout import Line, group_rows, rows_to_text
from ..models import Word
from .base import OcrOutput, OcrUnavailable, weighted_confidence

_WINDOWS_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
]


def find_tesseract(explicit: str | None = None) -> str | None:
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    for cand in _WINDOWS_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


class TesseractEngine:
    name = "tesseract"

    def __init__(self, lang: str = "pol", psm: int = 3, tesseract_cmd: str | None = None,
                 tessdata_dir: str | None = None, single_thread: bool = True, layout: str = "rows"):
        try:
            import pytesseract
        except ImportError as exc:
            raise OcrUnavailable("Brak pakietu pytesseract (pip install pytesseract)") from exc
        cmd = find_tesseract(tesseract_cmd)
        if not cmd:
            raise OcrUnavailable("Nie znaleziono programu tesseract – zainstaluj Tesseract 5 z językiem polskim")
        pytesseract.pytesseract.tesseract_cmd = cmd
        self._pt = pytesseract
        self.lang = lang
        self.layout = layout
        self.psm = psm
        self.tessdata_dir = tessdata_dir
        if single_thread:
            # równoległość robimy na poziomie stron; wątki OpenMP Tesseracta tylko by się dusiły
            os.environ.setdefault("OMP_THREAD_LIMIT", "1")
        try:
            self.version = str(pytesseract.get_tesseract_version())
            langs = set(pytesseract.get_languages(config=self._tessdata_cfg()))
        except Exception as exc:
            raise OcrUnavailable(f"Tesseract nie działa: {exc}") from exc
        missing = [l for l in lang.split("+") if l not in langs]
        if missing:
            raise OcrUnavailable(f"Tesseract nie ma modeli językowych: {missing} (dostępne: {sorted(langs)})")
        self.has_osd = "osd" in langs

    def _tessdata_cfg(self) -> str:
        return f'--tessdata-dir "{self.tessdata_dir}"' if self.tessdata_dir else ""

    def info(self) -> dict:
        return {"engine": self.name, "version": self.version, "lang": self.lang, "psm": self.psm}

    def ocr(self, image: np.ndarray, psm: int | None = None) -> OcrOutput:
        cfg = f"{self._tessdata_cfg()} --oem 1 --psm {psm or self.psm} -c preserve_interword_spaces=1"
        data = self._pt.image_to_data(image, lang=self.lang, config=cfg, output_type=self._pt.Output.DICT)
        words: list[Word] = []
        lines: dict[tuple[int, int, int], list[int]] = {}
        order: list[tuple[int, int, int]] = []
        for i, txt in enumerate(data["text"]):
            txt = (txt or "").strip()
            conf = float(data["conf"][i])
            if not txt or conf < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            if key not in lines:
                lines[key] = []
                order.append(key)
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            lines[key].append(len(words))
            words.append(Word(txt, round(conf / 100.0, 3), (x, y, x + w, y + h), len(order) - 1))
        if self.layout == "rows":
            # kolejność czytania z geometrii: etykiety z lewej kolumny trafiają do swoich wierszy
            line_objs = []
            for key in order:
                ids = lines[key]
                line_objs.append(Line(min(words[i].bbox[0] for i in ids), min(words[i].bbox[1] for i in ids),
                                      max(words[i].bbox[2] for i in ids), max(words[i].bbox[3] for i in ids), ids))
            text, mapping = rows_to_text(group_rows(line_objs), [w.text for w in words])
            for i, row in mapping.items():
                words[i].line = row
            return OcrOutput(text=text, confidence=weighted_confidence(words), words=words)
        # tryb "tesseract": linie w kolejności bloków Tesseracta
        out_lines: list[str] = []
        prev_par = None
        for key in order:
            par = key[:2]
            if prev_par is not None and par != prev_par:
                out_lines.append("")
            out_lines.append(" ".join(words[i].text for i in lines[key]))
            prev_par = par
        return OcrOutput(text="\n".join(out_lines), confidence=weighted_confidence(words), words=words)

    def detect_rotation(self, image: np.ndarray) -> int:
        if not self.has_osd:
            return 0
        try:
            osd = self._pt.image_to_osd(image, config=f"{self._tessdata_cfg()} --psm 0")
        except Exception:
            return 0  # za mało tekstu do OSD
        rot = re.search(r"Rotate:\s+(\d+)", osd)
        conf = re.search(r"Orientation confidence:\s+([\d.]+)", osd)
        if not rot:
            return 0
        angle = int(rot.group(1)) % 360
        if conf and float(conf.group(1)) < 1.5:
            return 0  # niepewna detekcja – lepiej nie obracać
        return angle
