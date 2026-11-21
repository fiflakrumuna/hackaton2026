"""CLI aplikacji.

    python main.py                         # ./input -> ./output (wymóg UZP)
    python main.py -i input -o output --audit audit
    python main.py --file sciezka.pdf      # jeden plik, JSON na stdout
    python main.py --schema odwolanie      # podgląd schematu / promptu z YAML
    python main.py --check-config          # walidacja konfiguracji
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from odw2json.config import ConfigError, load_config
from odw2json.pipeline import Pipeline
from odw2json.schema_builder import field_guide, json_schema


def main(argv=None):
    ap = argparse.ArgumentParser(description="Odwołania2json")
    ap.add_argument("-i", "--input", default="input")
    ap.add_argument("-o", "--output", default="output")
    ap.add_argument("--audit", help="katalog na metadane transparentności (cytaty, ostrzeżenia)")
    ap.add_argument("--config", help="katalog konfiguracji (domyślnie ./config)")
    ap.add_argument("--file", help="przetwórz pojedynczy PDF i wypisz wynik")
    ap.add_argument("--schema", metavar="TYP", help="pokaż JSON Schema i opis pól dla typu dokumentu")
    ap.add_argument("--check-config", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    try:
        cfg = load_config(a.config)
    except ConfigError as e:
        print(f"BŁĄD KONFIGURACJI: {e}", file=sys.stderr)
        return 2

    if a.check_config:
        for dt in cfg.document_types.values():
            print(f"OK  {dt.id:16} {len(dt.leaf_paths()):3} pól  ({dt.source_file.name})")
        print(f"Słowniki: {', '.join(cfg.dictionaries) or '-'}")
        return 0
    if a.schema:
        dt = cfg.document_types[a.schema]
        print(field_guide(dt))
        print(json.dumps(json_schema(dt), ensure_ascii=False, indent=2))
        return 0

    pipe = Pipeline(cfg)
    if a.file:
        r = pipe.process_file(a.file)
        print(json.dumps(asdict(r), ensure_ascii=False, indent=2, default=str))
        return 0
    results = pipe.process_dir(a.input, a.output, a.audit)
    n_ok = sum(1 for r in results if r.doc_type)
    print(f"Przetworzono {len(results)} plików ({n_ok} rozpoznanych) -> {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
