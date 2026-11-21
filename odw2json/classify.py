"""Klasyfikator regułowy – słowa kluczowe i wagi z sekcji ``classification`` w YAML.

Działa bez LLM (szybko, deterministycznie). Osoba 2 może go zastąpić lub
połączyć z klasyfikatorem LLM (np. LLM tylko gdy ``confidence`` jest niska).
"""
from __future__ import annotations

import re

from .config import AppConfig
from .document import ClassificationResult, ExtractedDocument


class RuleClassifier:
    def __init__(self, cfg: AppConfig, head_chars: int = 3000, use_filename: bool = True, **_):
        self.cfg = cfg
        self.head_chars = head_chars
        self.use_filename = use_filename

    def classify(self, doc: ExtractedDocument) -> ClassificationResult:
        full = doc.flat_text
        head = full[: self.head_chars]
        fname = doc.path.stem.lower()
        scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}
        for dt in self.cfg.document_types.values():
            c = dt.classification or {}
            s = 0.0
            why = []
            for kw in c.get("keywords", []):
                pat, w = (kw, 1.0) if isinstance(kw, str) else (kw["pattern"], float(kw.get("weight", 1)))
                where = head if (isinstance(kw, dict) and kw.get("head_only", True)) else full
                if re.search(pat, where, flags=re.I):
                    s += w
                    why.append(f"+{w:g} '{pat}'")
            for kw in c.get("negative_keywords", []):
                pat, w = (kw, 1.0) if isinstance(kw, str) else (kw["pattern"], float(kw.get("weight", 1)))
                if re.search(pat, head, flags=re.I):
                    s -= w
                    why.append(f"-{w:g} '{pat}'")
            if self.use_filename:
                for pat in c.get("filename_patterns", []):
                    if re.search(pat, fname, flags=re.I):
                        w = float(c.get("filename_weight", 1.0))
                        s += w
                        why.append(f"+{w:g} plik~'{pat}'")
                        break
            scores[dt.id] = s
            reasons[dt.id] = why
        if not scores:
            return ClassificationResult(None, method="rules")
        best = max(scores, key=scores.get)
        best_score = scores[best]
        min_score = float(self.cfg.document_types[best].classification.get("min_score", 2))
        ordered = sorted(scores.values(), reverse=True)
        margin = ordered[0] - (ordered[1] if len(ordered) > 1 else 0)
        if best_score < min_score:
            return ClassificationResult(None, 0.0, scores, "rules",
                                        f"Najlepszy wynik {best}={best_score:g} < min_score={min_score:g}")
        conf = min(1.0, 0.5 + margin / (2 * max(best_score, 1)))
        return ClassificationResult(best, round(conf, 3), scores, "rules", "; ".join(reasons[best]))
