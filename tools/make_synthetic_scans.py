"""Generator syntetycznych "krzywych skanów" z cyfrowych PDF-ów treningowych.

    python tools/make_synthetic_scans.py ../repo/dane synthetic_scans --cases 10

Każdy dokument dostaje losowy (powtarzalny, seed) zestaw degradacji:
przekrzywienie, obrót 90/180, szum, rozmycie, nierówne oświetlenie/cień,
szare tło, kompresja JPEG, niższa rozdzielczość. Wynik: PDF z samym obrazem
(bez warstwy tekstowej) + plik .degradations.json z opisem.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_ingest.loader import SourceDocument  # noqa: E402
from pdf_ingest.preprocess import rotate_bound, rotate_right_angle  # noqa: E402

PROFILES = {
    "clean":   dict(dpi=200, skew=0.0, noise=0, blur=0, shadow=0.0, jpeg=90, rot=0, gray_bg=0),
    "office":  dict(dpi=200, skew=1.2, noise=6, blur=0, shadow=0.0, jpeg=75, rot=0, gray_bg=10),
    "crooked": dict(dpi=200, skew=4.5, noise=10, blur=1, shadow=0.0, jpeg=60, rot=0, gray_bg=0),
    "phone":   dict(dpi=170, skew=2.5, noise=12, blur=1, shadow=0.45, jpeg=55, rot=0, gray_bg=25),
    "upside":  dict(dpi=200, skew=0.8, noise=6, blur=0, shadow=0.0, jpeg=70, rot=180, gray_bg=0),
    "sideways": dict(dpi=200, skew=0.5, noise=6, blur=0, shadow=0.0, jpeg=70, rot=90, gray_bg=0),
    "lowres":  dict(dpi=120, skew=1.5, noise=8, blur=0, shadow=0.15, jpeg=60, rot=0, gray_bg=10),
}


def degrade(gray: np.ndarray, p: dict, rng: random.Random) -> np.ndarray:
    img = gray.astype(np.float32)
    if p["gray_bg"]:
        img = img * (1 - p["gray_bg"] / 255.0)
    if p["shadow"]:
        h, w = img.shape
        xx = np.linspace(0, 1, w)[None, :]
        yy = np.linspace(0, 1, h)[:, None]
        grad = 1 - p["shadow"] * np.clip(0.6 * xx + 0.4 * yy * rng.choice([1, -1]) + rng.uniform(-0.2, 0.2), 0, 1)
        img = img * grad
    img = np.clip(img, 0, 255).astype(np.uint8)
    if p["skew"]:
        img = rotate_bound(img, rng.choice([-1, 1]) * rng.uniform(0.5, 1.0) * p["skew"])
    if p["rot"]:
        img = rotate_right_angle(img, p["rot"])
    if p["blur"]:
        img = cv2.GaussianBlur(img, (3, 3), 0.8)
    if p["noise"]:
        n = np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, p["noise"], img.shape)
        img = np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)
        # trochę "pieprzu" jak z kurzu na szybie skanera
        mask = np.random.default_rng(rng.randint(0, 1 << 30)).random(img.shape) < 0.0008
        img[mask] = 0
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, p["jpeg"]])
    return cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE)


def to_image_pdf(pages: list[np.ndarray], out: Path, dpi: int) -> None:
    ims = [Image.fromarray(p).convert("L") for p in pages]
    ims[0].save(out, "PDF", save_all=True, append_images=ims[1:], resolution=dpi)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--cases", type=int, default=10)
    ap.add_argument("--max-pages", type=int, default=5, help="ile stron z dokumentu skanować (oszczędność czasu)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    src, dst = Path(args.src), Path(args.dst)
    cases = sorted(p for p in src.iterdir() if p.is_dir())
    rng.shuffle(cases)
    names = list(PROFILES)
    for ci, case in enumerate(cases[: args.cases]):
        out_dir = dst / case.name
        out_dir.mkdir(parents=True, exist_ok=True)
        for js in case.glob("*.json"):
            shutil.copy(js, out_dir / js.name)
        for pj, pdf in enumerate(sorted(case.glob("*.pdf"))):
            prof_name = names[(ci * 3 + pj) % len(names)]  # deterministycznie, każdy profil kilka razy
            prof = PROFILES[prof_name]
            with SourceDocument(pdf) as doc:
                n = min(doc.page_count, args.max_pages)
                pages = [degrade(doc.render_gray(i, prof["dpi"], 10_000), prof, rng) for i in range(n)]
            to_image_pdf(pages, out_dir / pdf.name, prof["dpi"])
            (out_dir / f"{pdf.stem}.degradations.json").write_text(
                json.dumps({"profile": prof_name, **prof, "pages": n}, indent=1), encoding="utf-8")
            print(f"{case.name}/{pdf.name}: {prof_name} ({n} str.)")


if __name__ == "__main__":
    main()
