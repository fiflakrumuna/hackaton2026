import os
import json
import time
from pathlib import Path

# Tryb demo: ustaw zmienną środowiskową USE_MOCK=1. Domyślnie działa prawdziwy pipeline odw2json.
USE_MOCK = os.environ.get("USE_MOCK", "0") == "1"

SCHEMAS_DIR = Path(__file__).parent / "schemas"

_PIPELINE = None


def _get_pipeline():
    """Jedna instancja pipeline'u backendu (OCR -> klasyfikacja -> reguły/LLM -> JSON UZP)."""
    global _PIPELINE
    if _PIPELINE is None:
        from odw2json.pipeline import Pipeline
        _PIPELINE = Pipeline()
    return _PIPELINE


def reload_pipeline() -> None:
    """Po dodaniu/usunięciu typu dokumentu – następne przetwarzanie wczyta konfigurację od nowa."""
    global _PIPELINE
    _PIPELINE = None


def get_available_schemas() -> dict[str, dict]:
    """
    Dynamicznie wczytuje wszystkie definicje dokumentów z folderu schemas/.
    Zapewnia rozszerzalność bez modyfikacji kodu (30% oceny w finale).
    """
    schemas = {}
    # Główne źródło: typy dokumentów z config/document_types/*.yaml (te same, których używa backend)
    try:
        from odw2json.config import load_config
        from odw2json.schema_builder import json_schema
        for dt_id, dt in load_config().document_types.items():
            schemas[dt_id] = json_schema(dt)
    except Exception as e:
        schemas["config"] = {"error": f"Błąd odczytu config/document_types: {e}"}

    if not SCHEMAS_DIR.exists():
        return schemas

    for file_path in sorted(SCHEMAS_DIR.glob("*.json")):
        if file_path.stem in schemas or file_path.stat().st_size == 0:
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                schemas[file_path.stem] = json.load(f)
        except Exception as e:
            schemas[file_path.stem] = {"error": f"Błąd odczytu schematu: {e}"}
    return schemas


def validate_pdf_bytes(file_bytes: bytes, filename: str) -> None:
    """Zabezpieczenie przypadków brzegowych (puste lub uszkodzone pliki)."""
    if not file_bytes or len(file_bytes) == 0:
        raise ValueError(f"Plik '{filename}' jest pusty (0 bajtów).")

    # Sprawdzenie nagłówka magic bytes dla PDF
    if not file_bytes[:5] == b"%PDF-":
        raise ValueError(
            f"Plik '{filename}' nie jest poprawnym dokumentem PDF (uszkodzony nagłówek)."
        )


def get_document_types() -> dict[str, str]:
    """Typy dokumentów z config/document_types/*.yaml: {id: tytuł} – do wyboru schematu w UI."""
    try:
        from odw2json.config import load_config
        return {dt_id: dt.title or dt_id for dt_id, dt in load_config().document_types.items()}
    except Exception:
        return {}


def process_document(file_bytes: bytes, filename: str, doc_type: str | None = None) -> dict:
    """
    Główny punkt integracji między UI a logiką AI/OCR.
    Zwraca ustandaryzowany słownik z wynikiem, źródłami i metrykami.
    ``doc_type`` = None -> automatyczne rozpoznanie typu; inaczej wymuszony schemat.
    """
    start_time = time.perf_counter()
    validate_pdf_bytes(file_bytes, filename)

    if USE_MOCK:
        result = _mock_extraction(filename)
        if doc_type:
            result["document_type"] = doc_type
    else:
        result = _real_extraction(file_bytes, filename, doc_type)

    elapsed = round(time.perf_counter() - start_time, 2)

    # Normalizacja odpowiedzi na wypadek, gdyby model pominął opcjonalne klucze
    return {
        "filename": filename,
        "manual_type": bool(doc_type),
        "document_type": result.get("document_type", "nieznany"),
        "extracted_json": result.get("extracted_json", {}),
        "sources": result.get("sources", {}),
        "processing_time_sec": elapsed,
    }


def _real_extraction(file_bytes: bytes, filename: str, doc_type: str | None = None) -> dict:
    """PDF (bajty z uploadu) -> odw2json.Pipeline -> format oczekiwany przez app.py."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        pdf_path = Path(td) / Path(filename).name
        pdf_path.write_bytes(file_bytes)
        r = _get_pipeline().process_file(pdf_path, doc_type=doc_type)

    if r.error:
        raise RuntimeError(r.error.splitlines()[0])

    sources = {
        path: {"page": ev.get("page"), "quote": ev.get("quote"), "extractor": ev.get("extractor")}
        for path, ev in (r.evidence or {}).items()
    }
    return {
        # dokument nierozpoznany -> pusty JSON (wymóg UZP)
        "document_type": r.doc_type or "nierozpoznany",
        "extracted_json": r.data if r.data is not None else {},
        "sources": sources,
        "warnings": r.warnings,
        "classification": r.classification,
    }


def _mock_extraction(filename: str) -> dict:
    """Symulacja działania pipeline'u dla celów budowy i testów UI."""
    time.sleep(0.6)
    lower_name = filename.lower()

    if "pelnomocnictwo" in lower_name:
        return {
            "document_type": "pelnomocnictwo",
            "extracted_json": {
                "mocodawca": "TechBud Infrastruktura S.A.",
                "pelnomocnik": "r.pr. Anna Kowalska",
                "zakres": "Reprezentacja przed Krajową Izbą Odwoławczą",
                "data_udzielenia": "2026-11-10",
            },
            "sources": {
                "mocodawca": {
                    "page": 1,
                    "quote": "Ja, niżej podpisany Jan Nowak, działając w imieniu TechBud Infrastruktura S.A...",
                },
                "pelnomocnik": {
                    "page": 1,
                    "quote": "...niniejszym udzielam pełnomocnictwa radcy prawnemu Annie Kowalskiej...",
                },
                "data_udzielenia": {
                    "page": 1,
                    "quote": "Warszawa, dnia 10 listopada 2026 r.",
                },
            },
        }

    if "przelew" in lower_name or "oplata" in lower_name:
        return {
            "document_type": "potwierdzenie_przelewu",
            "extracted_json": {
                "kwota": 15000.00,
                "waluta": "PLN",
                "nadawca": "TechBud Infrastruktura S.A.",
                "odbiorca": "Urząd Zamówień Publicznych",
                "tytul_przelewu": "Wpis od odwołania - postępowanie ZP/2026/44",
                "data_operacji": "2026-11-12",
            },
            "sources": {
                "kwota": {"page": 1, "quote": "Kwota operacji: 15 000,00 PLN"},
                "odbiorca": {
                    "page": 1,
                    "quote": "Nazwa odbiorcy: Urząd Zamówień Publicznych, ul. Postępu 17A",
                },
                "tytul_przelewu": {
                    "page": 1,
                    "quote": "Tytuł: Wpis od odwołania - postępowanie ZP/2026/44",
                },
            },
        }

    # Domyślnie zwraca strukturę odwołania
    return {
        "document_type": "odwolanie",
        "extracted_json": {
            "sygnatura_postepowania": "ZP/2026/44",
            "odwolujacy": {
                "nazwa": "TechBud Infrastruktura S.A.",
                "nip": "5213456789",
                "adres": "ul. Prosta 51, 00-838 Warszawa",
            },
            "zamawiajacy": {
                "nazwa": "Generalna Dyrekcja Dróg Krajowych i Autostrad",
                "adres": "ul. Wronia 53, 00-874 Warszawa",
            },
            "zarzuty": [
                "Art. 226 ust. 1 pkt 2 lit. b ustawy Pzp",
                "Art. 16 pkt 1 ustawy Pzp",
            ],
        },
        "sources": {
            "sygnatura_postepowania": {
                "page": 1,
                "quote": "Dotyczy postępowania o udzielenie zamówienia publicznego nr ZP/2026/44",
            },
            "odwolujacy.nazwa": {
                "page": 1,
                "quote": "Odwołujący: TechBud Infrastruktura S.A. z siedzibą w Warszawie",
            },
            "odwolujacy.nip": {
                "page": 1,
                "quote": "wpisana do KRS pod nr 0000123456, NIP: 521-345-67-89",
            },
            "zamawiajacy.nazwa": {
                "page": 1,
                "quote": "Zamawiający: Generalna Dyrekcja Dróg Krajowych i Autostrad",
            },
            "zarzuty": {
                "page": 2,
                "quote": "Zamawiającemu zarzucam naruszenie art. 226 ust. 1 pkt 2 lit. b oraz art. 16 pkt 1 Pzp",
            },
        },
    }