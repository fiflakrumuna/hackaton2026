"""Wczytywanie PDF (pypdfium2) i analiza stron: warstwa tekstowa vs skan.

pypdfium2: licencja Apache-2.0/BSD, gotowe wheele na Windows/Linux/macOS,
nie wymaga Popplera ani Ghostscripta – ważne dla laptopa testowego.
PDFium nie jest thread-safe, dlatego wszystkie wywołania idą przez _PDFIUM_LOCK.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .quality import text_quality

_PDFIUM_LOCK = threading.RLock()
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# typy obiektów PDFium (FPDF_PAGEOBJ_*)
_OBJ_TEXT, _OBJ_PATH, _OBJ_IMAGE = 1, 2, 3


class PdfLoadError(RuntimeError):
    pass


@dataclass
class PageAnalysis:
    index: int                 # od 0
    width_pt: float
    height_pt: float
    text: str
    text_chars: int
    text_quality: float
    image_coverage: float
    path_objects: int

    @property
    def looks_scanned(self) -> bool:
        return self.image_coverage >= 0.6

    @property
    def is_blank(self) -> bool:
        return self.text_chars == 0 and self.image_coverage < 0.02 and self.path_objects < 30


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SourceDocument:
    """Wspólny interfejs dla PDF i pojedynczych obrazów (zdjęcie/skan jako JPG/PNG/TIFF)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise PdfLoadError(f"Plik nie istnieje: {self.path}")
        self.is_image = self.path.suffix.lower() in IMAGE_SUFFIXES
        self._pdf = None
        self._images: list[Image.Image] = []
        if self.is_image:
            self._open_image()
        else:
            self._open_pdf()

    # ---------- otwieranie ----------
    def _open_pdf(self) -> None:
        import pypdfium2 as pdfium
        with _PDFIUM_LOCK:
            try:
                self._pdf = pdfium.PdfDocument(str(self.path))
            except pdfium.PdfiumError as exc:
                # zaszyfrowany z pustym hasłem użytkownika -> spróbuj ""
                try:
                    self._pdf = pdfium.PdfDocument(str(self.path), password="")
                except Exception:
                    raise PdfLoadError(f"Nie można otworzyć PDF {self.path.name}: {exc}") from exc

    def _open_image(self) -> None:
        try:
            img = Image.open(self.path)
            frames = []
            i = 0
            while True:  # wielostronicowy TIFF
                try:
                    img.seek(i)
                except EOFError:
                    break
                frames.append(img.convert("L").copy())
                i += 1
            self._images = frames or [img.convert("L")]
        except Exception as exc:
            raise PdfLoadError(f"Nie można otworzyć obrazu {self.path.name}: {exc}") from exc

    def close(self) -> None:
        if self._pdf is not None:
            with _PDFIUM_LOCK:
                self._pdf.close()
            self._pdf = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- informacje ----------
    @property
    def page_count(self) -> int:
        if self.is_image:
            return len(self._images)
        with _PDFIUM_LOCK:
            return len(self._pdf)

    def analyze_page(self, index: int) -> PageAnalysis:
        if self.is_image:
            w, h = self._images[index].size
            return PageAnalysis(index, w, h, "", 0, 0.0, 1.0, 0)
        with _PDFIUM_LOCK:
            page = self._pdf[index]
            try:
                w, h = page.get_size()
                area = max(w * h, 1.0)
                tp = page.get_textpage()
                text = tp.get_text_bounded() or ""
                tp.close()
                img_area, paths = 0.0, 0
                for obj in page.get_objects(max_depth=3):
                    if obj.type == _OBJ_IMAGE:
                        # pypdfium2 >=5: get_bounds(); 4.x: get_pos()
                        getter = getattr(obj, "get_bounds", None) or obj.get_pos
                        l, b, r, t = getter()
                        # przycięcie do granic strony
                        l, r = max(0, min(l, r)), min(w, max(l, r))
                        b, t = max(0, min(b, t)), min(h, max(b, t))
                        img_area += max(0.0, r - l) * max(0.0, t - b)
                    elif obj.type == _OBJ_PATH:
                        paths += 1
            finally:
                page.close()
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        chars = len("".join(text.split()))
        return PageAnalysis(
            index=index, width_pt=w, height_pt=h, text=text, text_chars=chars,
            text_quality=text_quality(text) if chars else 0.0,
            image_coverage=round(min(1.0, img_area / area), 4), path_objects=paths,
        )

    def render_gray(self, index: int, dpi: int = 300, max_long_side: int = 3508) -> np.ndarray:
        """Renderuje stronę do obrazu w skali szarości (uint8, HxW)."""
        if self.is_image:
            img = self._images[index]
            arr = np.asarray(img, dtype=np.uint8)
            # zdjęcia w niskiej rozdzielczości: podbij do ~300 DPI dla A4 (szer. ~2480 px)
            if arr.shape[1] < 1600:
                import cv2
                scale = 2480 / arr.shape[1]
                arr = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            return arr
        with _PDFIUM_LOCK:
            page = self._pdf[index]
            try:
                w, h = page.get_size()
                scale = min(dpi / 72.0, max_long_side / max(w, h, 1.0))
                bitmap = page.render(scale=scale, grayscale=True, fill_color=(255, 255, 255, 255))
                pil = bitmap.to_pil()
            finally:
                page.close()
        return np.asarray(pil.convert("L"), dtype=np.uint8)
