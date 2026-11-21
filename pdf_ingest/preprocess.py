"""Preprocessing obrazów skanów przed OCR (OpenCV, CPU).

Kolejność: orientacja 90/180/270 -> prostowanie (deskew) -> normalizacja tła
(cienie, szare tło, nierówne oświetlenie) -> odszumianie.
Zostawiamy obraz w skali szarości – silnik LSTM Tesseracta radzi sobie z nim
lepiej niż z twardą binaryzacją.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class PreprocessResult:
    image: np.ndarray
    rotation: int = 0
    skew_deg: float = 0.0
    steps: list[str] = field(default_factory=list)


def rotate_bound(img: np.ndarray, angle_deg: float) -> np.ndarray:
    """Obrót o dowolny kąt bez ucinania rogów, wypełnienie białym."""
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def rotate_right_angle(img: np.ndarray, deg: int) -> np.ndarray:
    deg %= 360
    if deg == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def _ink_mask(gray: np.ndarray) -> np.ndarray:
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return bw


def estimate_skew(gray: np.ndarray, max_deg: float = 15.0) -> float:
    """Kąt przekrzywienia metodą profilu projekcji (odporna na układ dokumentu).

    Szukamy kąta, dla którego suma kwadratów różnic profilu poziomego jest
    największa (wiersze tekstu są wtedy najostrzejsze). Liczone na obrazie
    zmniejszonym do ~1000 px szerokości => ~0.1 s.
    """
    scale = 1000.0 / max(gray.shape[1], 1)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    ink = _ink_mask(small)
    if ink.mean() < 0.5:  # pusta strona
        return 0.0
    # pogrubienie poziome: wiersze liter zlewają się w paski
    ink = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))

    def score(angle: float) -> float:
        rot = rotate_bound(255 - ink, angle)
        prof = (255 - rot).sum(axis=1, dtype=np.float64)
        return float(np.sum(np.diff(prof) ** 2))

    coarse = np.arange(-max_deg, max_deg + 0.01, 1.0)
    best = max(coarse, key=score)
    fine = np.arange(best - 1.0, best + 1.01, 0.1)
    best = max(fine, key=score)
    return round(float(best), 2)


def needs_background_normalization(gray: np.ndarray) -> bool:
    """Wykrywa nierówne tło (cień, zdjęcie telefonem, szary papier)."""
    small = cv2.resize(gray, (256, 362), interpolation=cv2.INTER_AREA)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    bg = cv2.GaussianBlur(bg, (31, 31), 0)
    return float(bg.std()) > 12.0 or float(np.median(bg)) < 200


def normalize_background(gray: np.ndarray) -> np.ndarray:
    """Dzielenie przez oszacowane tło => białe tło, zachowane szarości liter."""
    k = max(31, (min(gray.shape) // 40) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.medianBlur(bg, 21)
    norm = cv2.divide(gray, bg, scale=255)
    return cv2.normalize(norm, None, 0, 255, cv2.NORM_MINMAX)


def noise_level(gray: np.ndarray) -> float:
    """Udział drobnych (1-4 px) plamek wśród wszystkich składowych spójnych."""
    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    ink = _ink_mask(small)
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n <= 1:
        return 0.0
    areas = stats[1:, cv2.CC_STAT_AREA]
    return float((areas <= 4).sum() / len(areas))


def remove_redactions(gray: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Obsługa ciemnych prostokątów. Zwraca (obraz, liczba_zamalowanych, liczba_odwróconych).

    * lity czarny prostokąt (anonimizacja) -> zamalowany na biało (inaczej OCR produkuje
      śmieci typu "EM", "KKM NN", które wyglądają jak dane),
    * ciemny pasek z jasnym tekstem w środku (nagłówki banków, tabel) -> ODWRACANY,
      żeby tekst stał się czarny na białym i został odczytany.

    Otwarcie morfologiczne jądrem ~1.5 mm usuwa kreski liter, zostają tylko lite plamy.
    """
    k = max(7, int(round(gray.shape[1] / 2480 * 15)))   # ~15 px przy 300 DPI
    dark = (gray < 90).astype(np.uint8) * 255
    solid = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    solid = cv2.morphologyEx(solid, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    out = gray
    whitened = inverted = 0
    pad = max(2, k // 4)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < k or h < k or area / float(w * h) < 0.85:
            continue
        if out is gray:
            out = gray.copy()
        roi = gray[y:y + h, x:x + w]
        bright = float((roi > 150).mean())
        y0, y1, x0, x1 = max(0, y - pad), y + h + pad, max(0, x - pad), x + w + pad
        if bright > 0.02:
            # jasny tekst na ciemnym tle – odwróć region (tylko wnętrze, bez marginesu)
            out[y:y + h, x:x + w] = 255 - roi
            inverted += 1
        else:
            out[y0:y1, x0:x1] = 255
            whitened += 1
    return out, whitened, inverted


def remove_dot_leaders(gray: np.ndarray) -> tuple[np.ndarray, int]:
    """Usuwa kropkowane/kreskowane prowadnice ("KWOTA ........ PLN 34.00", "-------").

    Tesseract na takich liniach gubi całe wiersze albo produkuje śmieci i bardzo
    zwalnia. Szukamy drobnych składowych (kropki, kreski) ułożonych w długie
    poziome ciągi i zamalowujemy je na biało. Zwraca (obraz, liczba usuniętych ciągów).
    """
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n < 20:
        return gray, 0
    hs = stats[1:, cv2.CC_STAT_HEIGHT]
    ws = stats[1:, cv2.CC_STAT_WIDTH]
    letters = hs[(hs > 8) & (ws < hs * 3)]
    if len(letters) < 10:
        return gray, 0
    ch = float(np.median(letters))                  # typowa wysokość znaku
    small_ids = np.where((hs <= 0.35 * ch) & (ws <= 1.3 * ch) & (stats[1:, cv2.CC_STAT_AREA] >= 2))[0] + 1
    if len(small_ids) < 8:
        return gray, 0
    small = np.isin(labels, small_ids).astype(np.uint8) * 255
    kx = max(3, int(1.6 * ch))
    chains = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1)))
    m, clabels, cstats, _ = cv2.connectedComponentsWithStats(chains, connectivity=8)
    out = gray
    removed = 0
    for j in range(1, m):
        x, y, w, h, _ = cstats[j]
        if w >= 5 * ch and h <= 0.6 * ch:
            if out is gray:
                out = gray.copy()
            region = small[y:y + h, x:x + w] > 0
            out[y:y + h, x:x + w][region] = 255
            removed += 1
    return out, removed


def preprocess(gray: np.ndarray, *, rotation: int = 0, deskew: bool = True, max_skew: float = 15.0,
               background: str = "auto", denoise: str = "auto", redactions: bool = True,
               dot_leaders: bool = True) -> PreprocessResult:
    steps: list[str] = []
    img = gray
    if rotation:
        img = rotate_right_angle(img, rotation)
        steps.append(f"rotate_{rotation}")

    skew = 0.0
    if deskew:
        skew = estimate_skew(img, max_skew)
        if abs(skew) >= 0.3:
            img = rotate_bound(img, skew)
            steps.append(f"deskew_{skew:+.1f}")
        else:
            skew = 0.0

    if background == "always" or (background == "auto" and needs_background_normalization(img)):
        img = normalize_background(img)
        steps.append("normalize_background")

    if denoise == "always" or (denoise == "auto" and noise_level(img) > 0.35):
        img = cv2.medianBlur(img, 3)
        steps.append("median_denoise")

    if dot_leaders:
        img, n_lead = remove_dot_leaders(img)
        if n_lead:
            steps.append(f"dot_leaders_removed_{n_lead}")

    if redactions:
        img, n_white, n_inv = remove_redactions(img)
        if n_white:
            steps.append(f"redactions_removed_{n_white}")
        if n_inv:
            steps.append(f"inverted_dark_banners_{n_inv}")

    return PreprocessResult(image=img, rotation=rotation, skew_deg=skew, steps=steps)


def alternative_preprocess(gray: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Druga próba dla stron z niską pewnością: wyrównanie kontrastu + binaryzacja adaptacyjna."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    blur = cv2.GaussianBlur(clahe, (3, 3), 0)
    bw = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) if noise_level(gray) > 0.35 else bw
    return bw, ["clahe", "adaptive_threshold"]
