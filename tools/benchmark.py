"""Benchmark warstwy OCR na syntetycznych skanach.

    python tools/benchmark.py /tmp/synth ../repo/dane

Dla każdego skanu porównuje tekst z OCR z warstwą tekstową oryginalnego
(cyfrowego) PDF-u na tych samych stronach i liczy:
  * char_sim     – podobieństwo znakowe (difflib) po usunięciu białych znaków,
  * field_recall – jaki odsetek wartości z referencyjnego JSON-a, które są
                   dosłownie w tekście cyfrowym, da się też znaleźć w tekście OCR.
                   To bezpośrednio ogranicza F1 całego systemu.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_ingest import IngestConfig, extract_document  # noqa: E402

JSON_FOR = {"odwolanie": "odwolanie.json", "pelnomocnictwo": "pelnomocnictwo.json",
            "potwierdzenie_przelewu": "przelew.json"}


def norm(s: str) -> str:
    s = s.lower().replace("„", "").replace("”", "").replace('"', "").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", s)


def flatten(obj) -> list[str]:
    out = []
    if isinstance(obj, dict):
        for v in obj.values():
            out += flatten(v)
    elif isinstance(obj, list):
        for v in obj:
            out += flatten(v)
    elif isinstance(obj, str) and len(obj) >= 5:
        out.append(obj)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scans")
    ap.add_argument("originals")
    ap.add_argument("--report", default="benchmark_report.json")
    args = ap.parse_args()
    cfg = IngestConfig.load(None, ocr_remaining="all", keep_words=False)
    rows = []
    by_profile = defaultdict(list)
    for pdf in sorted(Path(args.scans).glob("*/*.pdf")):
        meta = json.loads(pdf.with_name(f"{pdf.stem}.degradations.json").read_text())
        orig = Path(args.originals) / pdf.parent.name / pdf.name
        t0 = time.perf_counter()
        doc = extract_document(pdf, cfg)
        dt = time.perf_counter() - t0
        ref = extract_document(orig, cfg)
        n = meta["pages"]
        ocr_t = norm(doc.text_of(list(range(1, n + 1))))
        ref_t = norm(ref.text_of(list(range(1, n + 1))))
        sim = SequenceMatcher(None, ocr_t, ref_t, autojunk=False).ratio()
        expected = json.loads((pdf.parent / JSON_FOR[pdf.stem]).read_text(encoding="utf-8"))
        vals = [v for v in flatten(expected) if norm(v) in ref_t]
        hits = [v for v in vals if norm(v) in ocr_t]
        missed = [v for v in vals if norm(v) not in ocr_t]
        row = dict(file=f"{pdf.parent.name}/{pdf.name}", profile=meta["profile"], pages=n,
                   seconds=round(dt, 2), s_per_page=round(dt / n, 2), conf=doc.mean_confidence,
                   char_sim=round(sim, 4), fields=len(vals), field_hits=len(hits), missed=missed,
                   rotations=[p.rotation_applied for p in doc.pages],
                   skew=[p.skew_corrected_deg for p in doc.pages],
                   steps=sorted({s.split("_")[0] for p in doc.pages for s in p.preprocessing}))
        rows.append(row)
        by_profile[meta["profile"]].append(row)
        print(f"{row['file']:<45} {row['profile']:<9} sim={sim:.3f} conf={doc.mean_confidence:.2f} "
              f"fields={len(hits)}/{len(vals)} {dt / n:.1f}s/str rot={row['rotations'][0]} "
              f"skew={row['skew'][0]:+.1f}" + (f"  MISS: {missed}" if missed else ""))

    print("\n=== podsumowanie per profil ===")
    summary = {}
    for prof, rs in sorted(by_profile.items()):
        f = sum(r["fields"] for r in rs)
        h = sum(r["field_hits"] for r in rs)
        pages = sum(r["pages"] for r in rs)
        summary[prof] = dict(docs=len(rs), char_sim=round(sum(r["char_sim"] for r in rs) / len(rs), 4),
                             field_recall=round(h / f, 4) if f else None,
                             s_per_page=round(sum(r["seconds"] for r in rs) / pages, 2))
        print(f"{prof:<9} docs={len(rs):>2} char_sim={summary[prof]['char_sim']:.3f} "
              f"field_recall={h}/{f}={summary[prof]['field_recall']} s/str={summary[prof]['s_per_page']}")
    f = sum(r["fields"] for r in rows)
    h = sum(r["field_hits"] for r in rows)
    pages = sum(r["pages"] for r in rows)
    total = dict(docs=len(rows), pages=pages, field_recall=round(h / f, 4),
                 char_sim=round(sum(r["char_sim"] for r in rows) / len(rows), 4),
                 s_per_page=round(sum(r["seconds"] for r in rows) / pages, 2))
    print(f"RAZEM     {total}")
    Path(args.report).write_text(json.dumps({"total": total, "profiles": summary, "docs": rows},
                                            ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
