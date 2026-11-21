from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..models import Word


@dataclass
class OcrOutput:
    text: str
    confidence: float                     # 0..1, średnia ważona długością słów
    words: list[Word] = field(default_factory=list)


class OcrEngine(Protocol):
    name: str

    def ocr(self, image: np.ndarray) -> OcrOutput: ...

    def detect_rotation(self, image: np.ndarray) -> int:
        """Zwraca kąt (0/90/180/270), o jaki trzeba obrócić obraz zgodnie z ruchem wskazówek zegara."""
        ...

    def info(self) -> dict: ...


class OcrUnavailable(RuntimeError):
    pass


def weighted_confidence(words: list[Word]) -> float:
    total = sum(len(w.text) for w in words)
    if not total:
        return 0.0
    return round(sum(w.confidence * len(w.text) for w in words) / total, 4)
