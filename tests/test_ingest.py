"""Testy warstwy wejściowej.

Uruchomienie:  pytest -q
Dane treningowe: ustaw HACKNATION_DATA na katalog `dane` z repo UZP
(domyślnie ../repo/dane albo ../hacknation-2026/dane).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_ingest import IngestConfig, extract_document  # noqa: E402
from pdf_ingest.layout import Line, group_rows, rows_to_text  # noqa: E402
from pdf_ingest.postprocess import clean_text  # noqa: E402
from pdf_ingest.preprocess import estimate_skew, remove_dot_leaders, remove_redactions, rotate_bound  # noqa: E402
from pdf_ingest.quality import text_quality  # noqa: E402


def _data_dir() -> Path | None:
    for cand in [os.environ.get("HACKNATION_DATA"), ROOT.parent / "repo" / "dane",
                 ROOT.parent / "hacknation-2026" / "dane"]:
        if cand and Path(cand).is_dir():
            return Path(cand)
    return None


DATA = _data_dir()
HAS_TESSERACT = shutil.which("tesseract") is not None
CFG = IngestConfig(cache_dir=None, keep_words=True)


def _font(size: int):
    for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]:  # macOS
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _make_scan(tmp_path: Path, lines: list[str], angle: float = 0.0, rot180: bool = False) -> Path:
    img = Image.new("L", (1654, 2339), 255)          # A4 @ 200 DPI
    d = ImageDraw.Draw(img)
    y = 150
    for ln in lines:
        d.text((150, y), ln, fill=0, font=_font(34))
        y += 60
    arr = np.asarray(img)
    if angle:
        arr = rotate_bound(arr, angle)
    if rot180:
        arr = arr[::-1, ::-1]
    out = tmp_path / "scan.pdf"
    Image.fromarray(np.ascontiguousarray(arr)).save(out, "PDF", resolution=200)
    return out


SCAN_LINES = [
    "Warszawa, dnia 16 czerwca 2025 r.",
    "Odwołujący: Zakład Usług Komunalnych sp. z o.o.",
    "KRS: 0000123456, NIP: 5261040828, REGON: 012345678",
    "Zamawiający: Gmina Miasto Przykładowo",
    "Numer postępowania: ZP.271.15.2025",
    "Działając w imieniu wykonawcy wnoszę odwołanie od czynności",
    "zamawiającego polegającej na odrzuceniu oferty odwołującego.",
]


# ---------------- jednostkowe ----------------
def test_quality_good_vs_garbage():
    good = "Działając w imieniu wykonawcy wnoszę odwołanie od czynności zamawiającego w postępowaniu " * 3
    garbage = "ÿþ#$% ¤¤ (cid:12)(cid:13) xq zzk ¬¬ ~~ 0x0x \ufffd\ufffd qwrtp " * 5
    assert text_quality(good) > 0.8
    assert text_quality(garbage) < 0.4


def test_postprocess_ocr_fixes():
    txt, n = clean_text("NIP: 99975S33l9, biuro Qbiuro.example, Firma sp. Z 0.0.", is_ocr=True)
    assert "NIP: 9997553319" in txt
    assert "biuro@biuro.example" in txt
    assert "sp. z o.o." in txt
    assert n >= 3


def test_postprocess_does_not_touch_digital_ids():
    s = "Belmoria Automatyka S.A, a w szczególności"
    assert clean_text(s, is_ocr=False)[0] == s
    assert clean_text(s, is_ocr=True)[0] == s


def test_dehyphenation():
    txt, _ = clean_text("w trybie przetar-\ngu nieograniczonego", is_ocr=True)
    assert "przetargu" in txt


def test_layout_puts_left_label_next_to_its_block():
    # etykieta z lewej kolumny wyrównana do 2. linii bloku danych
    words = ["Firma", "X", "Odwołujący:", "ul.", "Długa", "Zamawiający:", "Gmina", "Y"]
    lines = [Line(500, 100, 800, 130, [0, 1]), Line(500, 140, 800, 170, [3, 4]),
             Line(50, 142, 300, 168, [2]), Line(500, 300, 800, 330, [6, 7]), Line(50, 302, 300, 328, [5])]
    text, _ = rows_to_text(group_rows(lines), words)
    assert text.index("Odwołujący:") < text.index("Zamawiający:") < text.index("Gmina")
    assert "Odwołujący: ul. Długa" in text


def test_skew_estimation():
    img = np.full((1200, 900), 255, np.uint8)
    for y in range(100, 1100, 40):
        img[y:y + 12, 80:820] = 0
    rotated = rotate_bound(img, 4.0)
    assert abs(estimate_skew(rotated) + 4.0) < 0.6


def test_redaction_whitened_and_banner_inverted():
    img = np.full((1000, 2480), 255, np.uint8)
    img[100:160, 200:700] = 0                       # anonimizacja
    img[400:500, 200:1800] = 30                     # ciemny pasek...
    for x in range(300, 1600, 40):                  # ...z jasnym "tekstem" (kreski ~6 px jak litery)
        img[430:470, x:x + 6] = 255
    out, whitened, inverted = remove_redactions(img)
    assert whitened == 1 and inverted == 1
    assert out[130, 400] == 255
    assert out[410, 250] > 200                      # tło paska odwrócone na jasne


def test_dot_leaders_removed():
    img = Image.new("L", (2000, 300), 255)
    d = ImageDraw.Draw(img)
    d.text((50, 100), "KWOTA PRZELEWU " + "." * 60 + " PLN 34.00", fill=0, font=_font(36))
    arr = np.asarray(img)
    out, n = remove_dot_leaders(arr)
    assert n >= 1
    assert (out < 128).sum() < (arr < 128).sum()


# ---------------- integracyjne: PDF cyfrowe ----------------
@pytest.mark.skipif(DATA is None, reason="brak danych treningowych UZP")
def test_all_training_pdfs_are_digital_and_contain_ids():
    import json
    for case in sorted(DATA.glob("przyklad_*"))[:15]:   # bez folderu negatywne/
        doc = extract_document(case / "odwolanie.pdf", CFG)
        assert doc.doc_kind == "digital"
        assert all(p.source == "text_layer" for p in doc.pages)
        exp = json.loads((case / "odwolanie.json").read_text(encoding="utf-8"))
        nip = exp["Odwolujacy"][0]["nip"]
        if nip:
            assert nip in doc.text.replace(" ", "")


@pytest.mark.skipif(DATA is None, reason="brak danych treningowych UZP")
def test_digital_pdf_is_fast():
    import time
    t = time.perf_counter()
    extract_document(sorted(DATA.glob("przyklad_*"))[0] / "odwolanie.pdf", CFG)
    assert time.perf_counter() - t < 2.0


# ---------------- integracyjne: OCR ----------------
@pytest.mark.skipif(not HAS_TESSERACT, reason="brak Tesseracta")
def test_scan_detected_and_ocred(tmp_path):
    doc = extract_document(_make_scan(tmp_path, SCAN_LINES), CFG)
    p = doc.pages[0]
    assert doc.doc_kind == "scanned" and p.source == "ocr"
    assert p.confidence > 0.8
    flat = doc.text.replace(" ", "")
    for v in ["5261040828", "0000123456", "ZP.271.15.2025"]:
        assert v in flat
    assert p.words and all(0 <= w.confidence <= 1 for w in p.words)


@pytest.mark.skipif(not HAS_TESSERACT, reason="brak Tesseracta")
def test_crooked_and_upside_down_scan(tmp_path):
    doc = extract_document(_make_scan(tmp_path, SCAN_LINES, angle=3.5, rot180=True), CFG)
    p = doc.pages[0]
    assert p.rotation_applied == 180
    assert abs(abs(p.skew_corrected_deg) - 3.5) < 1.0
    assert "5261040828" in doc.text.replace(" ", "")


def test_image_input(tmp_path):
    if not HAS_TESSERACT:
        pytest.skip("brak Tesseracta")
    pdf = _make_scan(tmp_path, SCAN_LINES)
    png = tmp_path / "skan.png"
    from pdf_ingest.loader import SourceDocument
    with SourceDocument(pdf) as s:
        Image.fromarray(s.render_gray(0, 200)).save(png)
    doc = extract_document(png, CFG)
    assert doc.pages[0].source == "ocr" and "5261040828" in doc.text.replace(" ", "")


def test_missing_file_raises():
    from pdf_ingest import PdfLoadError
    with pytest.raises(PdfLoadError):
        extract_document("nie_ma_takiego.pdf", CFG)
