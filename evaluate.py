"""Ewaluacja F1 per pole na zbiorze treningowym UZP.

Użycie:
    python evaluate.py --gt dane --run                      # uruchom pipeline na dane/ i policz F1
    python evaluate.py --gt dane --pred output              # policz F1 dla gotowych wyników
    python evaluate.py --gt dane --run --label prompt_v3 --compare last
    python evaluate.py --gt dane --run --only odwolanie --errors errors.csv --md raport.md

Liczenie (na poziomie pól-liści):
  * listy (Odwolujacy, Zamawiajacy, mocodawca, pelnomocnicy...) są najpierw dopasowywane
    element-do-elementu (algorytm węgierski, maks. liczba zgodnych pól) – kolejność nie karze,
  * TP  – GT ≠ null i predykcja == GT
  * FP  – predykcja ≠ null i (GT == null lub predykcja ≠ GT)
  * FN  – GT ≠ null i (predykcja == null lub predykcja ≠ GT)
  * TN  – oba null. Raportujemy dwa warianty: F1 (null ignorowane) i F1+null (TN liczone jako TP),
    bo nie wiemy, jak UZP traktuje nulle – optymalizujemy pod oba.
  * porównanie "strict" (dokładne, int==float dozwolone) – tak najpewniej liczy organizator;
    "loose" (ignoruje wielkość liter, białe znaki, interpunkcję) – pokazuje, ile tracimy na formacie.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scipy.optimize import linear_sum_assignment
except ImportError:  # pragma: no cover
    linear_sum_assignment = None

RUNS_DIR = Path("eval_runs")


# ---------------------------------------------------------------------------- wczytywanie
def load_json(path: Path):
    if not path.exists():
        return None
    txt = path.read_text(encoding="utf-8").strip()
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        # pliki wzorcowe UZP mają komentarze // – usuwamy je (poza stringami)
        cleaned = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m.group(1) or "", txt)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return json.loads(cleaned)


# ---------------------------------------------------------------------------- porównanie wartości
def _loose(v):
    if v is None:
        return None
    s = str(v).casefold()
    s = re.sub(r"[\s\u00a0]+", " ", s).strip()
    s = re.sub(r"[„”\"'“‘’«».,;:()\-–]", "", s)
    s = s.replace(" ", "")
    try:
        return f"{float(s):.4f}"
    except ValueError:
        return s


def equal(g, p, mode: str = "strict") -> bool:
    if g is None or p is None:
        return g is None and p is None
    if mode == "loose":
        return _loose(g) == _loose(p)
    if isinstance(g, bool) or isinstance(p, bool):
        return g == p
    if isinstance(g, (int, float)) and isinstance(p, (int, float)):
        return abs(float(g) - float(p)) < 1e-6
    return type(g) is type(p) and g == p


# ---------------------------------------------------------------------------- spłaszczanie z dopasowaniem list
def _leaves(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _leaves(v)
    elif isinstance(o, list):
        for v in o:
            yield from _leaves(v)
    else:
        yield o


def _pairs(g, p, path, mode):
    """Zwraca listę (ścieżka, gt, pred) dla wszystkich liści."""
    if isinstance(g, dict) or isinstance(p, dict):
        g = g if isinstance(g, dict) else {}
        p = p if isinstance(p, dict) else {}
        keys = list(g) + [k for k in p if k not in g]
        out = []
        for k in keys:
            out += _pairs(g.get(k), p.get(k), f"{path}.{k}" if path else k, mode)
        return out
    if isinstance(g, list) or isinstance(p, list):
        g = g if isinstance(g, list) else ([] if g is None else [g])
        p = p if isinstance(p, list) else ([] if p is None else [p])
        return _align_lists(g, p, path + "[]", mode)
    return [(path, g, p)]


def _score(a, b, mode):
    return sum(1 for (_, x, y) in _pairs(a, b, "", mode) if x is not None and equal(x, y, mode))


def _align_lists(g: list, p: list, path: str, mode):
    if not g and not p:
        return []
    n, m = len(g), len(p)
    sim = [[_score(g[i], p[j], mode) + 1e-3 for j in range(m)] for i in range(n)]
    pairs = []
    if n and m:
        if linear_sum_assignment is not None:
            import numpy as np
            r, c = linear_sum_assignment(-np.array(sim))
            pairs = list(zip(r.tolist(), c.tolist()))
        else:  # zachłannie
            used = set()
            for i in range(n):
                j = max((j for j in range(m) if j not in used), key=lambda j: sim[i][j], default=None)
                if j is not None:
                    used.add(j)
                    pairs.append((i, j))
    mg = {i for i, _ in pairs}
    mp = {j for _, j in pairs}
    out = []
    for i, j in pairs:
        out += _pairs(g[i], p[j], path, mode)
    for i in range(n):
        if i not in mg:
            out += _pairs(g[i], _null_like(g[i]), path, mode)
    for j in range(m):
        if j not in mp:
            out += _pairs(_null_like(p[j]), p[j], path, mode)
    return out


def _null_like(o):
    if isinstance(o, dict):
        return {k: _null_like(v) for k, v in o.items()}
    if isinstance(o, list):
        return []
    return None


# ---------------------------------------------------------------------------- liczniki
@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, other: "Counts"):
        self.tp += other.tp; self.fp += other.fp; self.fn += other.fn; self.tn += other.tn

    def prf(self, with_nulls=False):
        tp = self.tp + (self.tn if with_nulls else 0)
        p = tp / (tp + self.fp) if tp + self.fp else (1.0 if not self.fn else 0.0)
        r = tp / (tp + self.fn) if tp + self.fn else (1.0 if not self.fp else 0.0)
        f = 2 * p * r / (p + r) if p + r else 0.0
        return p, r, f


@dataclass
class EvalResult:
    by_field: dict = field(default_factory=lambda: defaultdict(Counts))
    by_doc: dict = field(default_factory=lambda: defaultdict(Counts))
    total: Counts = field(default_factory=Counts)
    errors: list = field(default_factory=list)
    docs: int = 0
    missing_pred: int = 0
    exact_docs: int = 0


def evaluate(gt_dir: Path, pred_dir: Path, settings: dict, mode="strict", only: set | None = None) -> EvalResult:
    ev = settings.get("evaluation", {}) or {}
    stem_map = ev.get("gt_to_pdf_stem", {}) or {}
    res = EvalResult()
    for gt_path in sorted(gt_dir.rglob("*.json")):
        case = gt_path.parent.relative_to(gt_dir)
        doc = gt_path.stem
        if only and doc not in only:
            continue
        pred_path = pred_dir / case / f"{stem_map.get(doc, doc)}.json"
        g = load_json(gt_path)
        p = load_json(pred_path)
        if p is None:
            res.missing_pred += 1
            p = {}
        res.docs += 1
        doc_ok = True
        for path, gv, pv in _pairs(g or {}, p or {}, "", mode):
            c = Counts()
            ok = equal(gv, pv, mode)
            if gv is None and pv is None:
                c.tn = 1
            elif ok:
                c.tp = 1
            else:
                doc_ok = False
                if pv is not None:
                    c.fp = 1
                if gv is not None:
                    c.fn = 1
                kind = "FN(brak)" if pv is None else "FP(nadmiar)" if gv is None else "ZŁA_WARTOŚĆ"
                res.errors.append({"case": str(case), "doc": doc, "field": path, "kind": kind,
                                   "gt": json.dumps(gv, ensure_ascii=False), "pred": json.dumps(pv, ensure_ascii=False)})
            key = f"{doc}:{path}"
            res.by_field[key].add(c)
            res.by_doc[doc].add(c)
            res.total.add(c)
        res.exact_docs += doc_ok
    return res


# ---------------------------------------------------------------------------- raport
def summary_dict(r: EvalResult, label: str, mode: str) -> dict:
    def pack(c: Counts):
        p, rr, f = c.prf()
        _, _, fn_ = c.prf(with_nulls=True)
        return {"tp": c.tp, "fp": c.fp, "fn": c.fn, "tn": c.tn, "precision": round(p, 4), "recall": round(rr, 4),
                "f1": round(f, 4), "f1_with_nulls": round(fn_, 4)}
    fields = {k: pack(c) for k, c in sorted(r.by_field.items())}
    macro = sum(v["f1"] for v in fields.values()) / max(len(fields), 1)
    return {
        "label": label, "time": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode,
        "docs": r.docs, "missing_pred": r.missing_pred, "exact_docs": r.exact_docs,
        "micro": pack(r.total), "macro_f1": round(macro, 4),
        "by_doc": {k: pack(c) for k, c in sorted(r.by_doc.items())},
        "by_field": fields,
    }


def print_report(s: dict, prev: dict | None = None, top: int = 100):
    def delta(new, old):
        if old is None:
            return ""
        x = new - old
        return f"  ({'+' if x >= 0 else ''}{x * 100:.1f})" if abs(x) >= 0.0005 else "  (=)"

    print(f"\n=== Ewaluacja [{s['label']}] tryb={s['mode']} dokumentów={s['docs']} "
          f"(brak predykcji: {s['missing_pred']}, w 100% poprawnych: {s['exact_docs']})")
    m = s["micro"]
    pm = prev["micro"] if prev else {}
    print(f"MICRO  F1={m['f1']:.4f}{delta(m['f1'], pm.get('f1'))}   F1+null={m['f1_with_nulls']:.4f}"
          f"{delta(m['f1_with_nulls'], pm.get('f1_with_nulls'))}   P={m['precision']:.3f} R={m['recall']:.3f}   "
          f"MACRO(pola) F1={s['macro_f1']:.4f}{delta(s['macro_f1'], prev.get('macro_f1') if prev else None)}")
    print("\nPer typ dokumentu:")
    for k, v in s["by_doc"].items():
        old = prev["by_doc"].get(k, {}).get("f1") if prev else None
        print(f"  {k:16} F1={v['f1']:.4f}{delta(v['f1'], old)}  F1+null={v['f1_with_nulls']:.4f}  "
              f"TP={v['tp']} FP={v['fp']} FN={v['fn']}")
    print(f"\nPer pole (od najgorszego):\n  {'pole':58} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}  {'P':>5} {'R':>5} {'F1':>6}")
    rows = sorted(s["by_field"].items(), key=lambda kv: (kv[1]["f1"], kv[0]))
    for k, v in rows[:top]:
        old = prev["by_field"].get(k, {}).get("f1") if prev else None
        print(f"  {k:58} {v['tp']:4} {v['fp']:4} {v['fn']:4} {v['tn']:4}  {v['precision']:5.2f} {v['recall']:5.2f} "
              f"{v['f1']:6.3f}{delta(v['f1'], old)}")


def write_md(s: dict, path: Path):
    lines = [f"# Raport jakości ekstrakcji – {s['label']}", "",
             f"Data: {s['time']} · tryb porównania: `{s['mode']}` · dokumentów: {s['docs']}", "",
             f"**Micro F1: {s['micro']['f1']:.4f}** (F1 z nullami: {s['micro']['f1_with_nulls']:.4f}), "
             f"macro F1 po polach: {s['macro_f1']:.4f}, dokumenty w 100% poprawne: {s['exact_docs']}/{s['docs']}", "",
             "| Typ dokumentu | P | R | F1 | F1+null |", "|---|---|---|---|---|"]
    for k, v in s["by_doc"].items():
        lines.append(f"| {k} | {v['precision']:.3f} | {v['recall']:.3f} | {v['f1']:.3f} | {v['f1_with_nulls']:.3f} |")
    lines += ["", "| Pole | TP | FP | FN | P | R | F1 |", "|---|---|---|---|---|---|---|"]
    for k, v in s["by_field"].items():
        lines.append(f"| `{k}` | {v['tp']} | {v['fp']} | {v['fn']} | {v['precision']:.2f} | {v['recall']:.2f} | {v['f1']:.3f} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_prev(arg: str | None):
    if not arg:
        return None
    if arg == "last":
        runs = sorted(RUNS_DIR.glob("*.json"))
        return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None
    return json.loads(Path(arg).read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="F1 per pole dla odw2json")
    ap.add_argument("--gt", required=True, help="katalog z danymi referencyjnymi (np. hacknation-2026/dane)")
    ap.add_argument("--pred", help="katalog z wynikami (struktura jak output/)")
    ap.add_argument("--run", action="store_true", help="najpierw uruchom pipeline na PDF-ach z --gt")
    ap.add_argument("--config", help="katalog konfiguracji")
    ap.add_argument("--mode", choices=["strict", "loose"], default="strict")
    ap.add_argument("--only", help="tylko wybrane typy GT, np. odwolanie,przelew")
    ap.add_argument("--label", default="run")
    ap.add_argument("--compare", help="'last' albo ścieżka do poprzedniego podsumowania JSON")
    ap.add_argument("--errors", help="zapisz listę błędów do CSV")
    ap.add_argument("--md", help="zapisz raport Markdown")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--fail-under", type=float, help="kod wyjścia 1, gdy micro F1 < próg (CI)")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from odw2json.config import load_config
    cfg = load_config(a.config)

    gt_dir = Path(a.gt)
    pred_dir = Path(a.pred) if a.pred else None
    if a.run:
        from odw2json.pipeline import Pipeline
        pred_dir = pred_dir or Path(tempfile.mkdtemp(prefix="odw2json_pred_"))
        Pipeline(cfg).process_dir(gt_dir, pred_dir)
        print(f"Predykcje zapisane w {pred_dir}")
    if pred_dir is None:
        ap.error("podaj --pred albo --run")

    prev = _load_prev(a.compare)
    only = set(a.only.split(",")) if a.only else None
    r = evaluate(gt_dir, pred_dir, cfg.settings, a.mode, only)
    s = summary_dict(r, a.label, a.mode)
    print_report(s, prev, a.top)

    if a.errors:
        with open(a.errors, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["case", "doc", "field", "kind", "gt", "pred"])
            w.writeheader()
            w.writerows(r.errors)
        print(f"\nBłędy ({len(r.errors)}) -> {a.errors}")
    if a.md:
        write_md(s, Path(a.md))
        print(f"Raport Markdown -> {a.md}")
    if not a.no_save:
        RUNS_DIR.mkdir(exist_ok=True)
        out = RUNS_DIR / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{re.sub(r'[^\w-]', '_', a.label)}.json"
        out.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Podsumowanie -> {out}")
    if a.fail_under is not None and s["micro"]["f1"] < a.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
