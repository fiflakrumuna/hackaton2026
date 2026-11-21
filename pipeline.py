import os
import json
import time
from pathlib import Path

# Przełącz na False, gdy koledzy dostarczą pliki ocr.py oraz extractor.py
USE_MOCK = True

SCHEMAS_DIR = Path(__file__).parent / "schemas"


def get_available_schemas() -> dict[str, dict]:
    """
    Dynamicznie wczytuje wszystkie definicje dokumentów z folderu schemas/.
    Zapewnia rozszerzalność bez modyfikacji kodu (30% oceny w finale).
    """
    schemas = {}
    if not SCHEMAS_DIR.exists():
        SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
        return schemas

    for file_path in sorted(SCHEMAS_DIR.glob("*.json")):
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


def process_document(file_bytes: bytes, filename: str) -> dict:
    """
    Główny punkt integracji między UI a logiką AI/OCR.
    Zwraca ustandaryzowany słownik z wynikiem, źródłami i metrykami.
    """
    start_time = time.perf_counter()
    validate_pdf_bytes(file_bytes, filename)

    if USE_MOCK:
        result = _mock_extraction(filename)
    else:
        # Tutaj wpinasz kod kolegów, gdy będzie gotowy:
        from ocr import extract_text_from_pdf
        from extractor import extract_data_with_llm

        pages = extract_text_from_pdf(file_bytes)
        if not pages or all(not p.get("text", "").strip() for p in pages):
            raise ValueError(
                "Nie udało się wyodrębnić tekstu z PDF (pusty dokument lub nieczytelny skan)."
            )
        result = extract_data_with_llm(pages)

    elapsed = round(time.perf_counter() - start_time, 2)

    # Normalizacja odpowiedzi na wypadek, gdyby model pominął opcjonalne klucze
    return {
        "filename": filename,
        "document_type": result.get("document_type", "nieznany"),
        "extracted_json": result.get("extracted_json", {}),
        "sources": result.get("sources", {}),
        "processing_time_sec": elapsed,
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