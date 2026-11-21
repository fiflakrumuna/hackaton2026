"""Odtwarzanie kolejności czytania z geometrii linii OCR.

Problem: w pismach procesowych etykiety stoją w lewej kolumnie
("Odwołujący:", "Zamawiający:"), a dane w prawej. Tesseract (psm 3) czyta je
jako osobne bloki i wstawia etykietę PO bloku danych – wtedy "Odwołujący:"
ląduje tuż przed danymi Zamawiającego, co myli ekstrakcję.

Rozwiązanie: grupujemy linie w wiersze wizualne po pokryciu w osi Y
(jak `pdftotext -layout`), a w wierszu sortujemy po X.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass
class Line:
    x0: int
    y0: int
    x1: int
    y1: int
    word_ids: list[int]

    @property
    def h(self) -> int:
        return max(1, self.y1 - self.y0)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def group_rows(lines: list[Line]) -> list[list[Line]]:
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda l: (l.cy, l.x0)):
        best, best_ov = None, 0.0
        for row in rows[-4:]:
            ref = row[0]
            ov = _overlap(ln.y0, ln.y1, ref.y0, ref.y1) / min(ln.h, ref.h)
            if ov > best_ov:
                best, best_ov = row, ov
        if best is not None and best_ov >= 0.5:
            best.append(ln)
        else:
            rows.append([ln])
    for row in rows:
        row.sort(key=lambda l: l.x0)
    rows.sort(key=lambda r: min(l.y0 for l in r))
    return rows


def rows_to_text(rows: list[list[Line]], word_texts: list[str]) -> tuple[str, dict[int, int]]:
    """Zwraca tekst i mapowanie word_id -> numer wiersza."""
    if not rows:
        return "", {}
    heights = [l.h for r in rows for l in r]
    mh = median(heights) if heights else 1
    out: list[str] = []
    mapping: dict[int, int] = {}
    prev_bottom = None
    for ri, row in enumerate(rows):
        top = min(l.y0 for l in row)
        if prev_bottom is not None and top - prev_bottom > 1.1 * mh:
            out.append("")
        parts = []
        for ln in row:
            parts.append(" ".join(word_texts[i] for i in ln.word_ids))
            for i in ln.word_ids:
                mapping[i] = ri
        out.append(" ".join(parts))
        prev_bottom = max(l.y1 for l in row)
    return "\n".join(out), mapping
