# odw2json: konfiguracja, normalizacja i ewaluacja F1 (część Osoby 3)

Szkielet aplikacji **Odwołania2json**: PDF → tekst → klasyfikacja → ekstrakcja → normalizacja → JSON.
Kod Pythona **nie zna nazw pól ani typów dokumentów**. Wszystko opisują pliki YAML w `config/`.

```
python main.py                                   # ./input -> ./output (wymóg UZP)
python main.py -i input -o output --audit audit  # + cytaty źródłowe i ostrzeżenia (dla UI)
python main.py --check-config                    # walidacja YAML
python main.py --schema odwolanie                # opis pól (do promptu) + JSON Schema
python evaluate.py --gt ../hacknation-2026/dane --run --label moja_zmiana --compare last
python -m pytest -q                              # 35 testów
```

## Szybki start

**Wymagania:** Python 3.13+, [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) z językiem polskim
(Windows: instalator UB-Mannheim, zaznaczyć *Polish*; macOS: `brew install tesseract tesseract-lang`).

1. Włóż PDF-y do `input/` (mogą być w podfolderach, np. `input/case1/…`).
2. Uruchom:
   - Windows: `start.bat` (dwuklik albo z wiersza poleceń),
   - macOS / Linux: `./start.sh`.
3. Wyniki są w `output/`, w tej samej strukturze folderów i z tymi samymi nazwami plików co w `input/`.
   Dokument nierozpoznany (np. ogłoszenie, wyrok) daje pusty plik `.json`.

GUI w przeglądarce (wgranie PDF-ów, podgląd JSON-a i cytatów źródłowych): `start.bat gui` / `./start.sh gui`,
potem http://localhost:8501.

Skrypty same tworzą `.venv` i instalują `requirements.txt`. Ręcznie: `python -m venv .venv`, aktywacja,
`python -m pip install -r requirements.txt`, `python main.py`.

## Model językowy (LLM)

| | |
|---|---|
| Model | **Qwen3 (`qwen/qwen3.8-27b`)**, otwarte wagi (open source) |
| Dostęp | API zgodne z OpenAI (`/chat/completions`), bez GPU po stronie aplikacji |
| Konfiguracja | plik `.env` w głównym folderze (nie trafia do repozytorium): |

```
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<klucz>
LLM_MODEL=qwen/qwen3.8-27b
```

Model można zmienić bez zmian w kodzie: wystarczy inny `LLM_BASE_URL` / `LLM_MODEL` dowolnego dostawcy z API
zgodnym z OpenAI.

**Jak LLM jest używany:**
- **Reguły (regex z YAML) i LLM działają razem.** Pola o stałym formacie (daty, NIP, KRS, REGON, numery BZP/TED,
  rachunki, kwoty) biorą reguły, bo są szybkie i bezbłędne. Dane stron (Odwołujący, Zamawiający, mocodawcy,
  pełnomocnicy) bierze najpierw LLM (`prefer: [llm, rules]` w YAML), bo układ tych sekcji w prawdziwych pismach
  i skanach jest bardzo różny.
- **Prompt i schemat odpowiedzi są generowane z YAML.** Nowe pole lub typ dokumentu nie wymaga zmian w kodzie.
- **Odpowiedź LLM jest dopasowywana do struktury schematu** (np. gdy model „spłaszczy” zagnieżdżony obiekt)
  i przechodzi przez tę samą normalizację co reguły.
- **Transparentność:** LLM zwraca cytat źródłowy dla każdego pola, a aplikacja sprawdza, czy cytat naprawdę jest
  w dokumencie (`verified`). Cytaty i numery stron widać w GUI i w `--audit`.
- **Odporność:** przy limicie API (HTTP 429) i błędach serwera zapytanie jest ponawiane. Bez klucza albo przy
  awarii API aplikacja działa dalej na samych regułach.

## Wynik bazowy (same reguły, bez LLM, zbiór treningowy, 180 dokumentów)

| | F1 (null ignorowane) | F1 + null |
|---|---|---|
| **micro, całość** | **0.993** | 0.994 |
| odwołanie | 1.000 | 1.000 |
| pełnomocnictwo | 0.979 | 0.985 |
| przelew | 0.989 | 0.989 |

168 z 180 dokumentów jest w 100% poprawnych. Szczegóły są w `docs/raport_jakosci.md`.
**Uwaga:** dane treningowe są syntetyczne i bardzo regularne. Zbiór testowy to „realne sprawy”, a szablonowy
przykład UZP (`szablon-aplikacja/input`) jest **skanem** prawdziwego, zanonimizowanego odwołania. Reguły traktujemy
jako szybki, tani i pewny fundament dla pól o stałym formacie (NIP, KRS, numery, kwoty). LLM ma pokrywać resztę.

## Architektura

```
config/
  settings.yaml                 # wybór pluginów (OCR, klasyfikator, ekstraktory), LLM, wyjście, ewaluacja
  document_types/*.yaml         # 1 plik = 1 typ dokumentu: schemat JSON + opisy + reguły + klasyfikacja
  dictionaries/*.yaml           # słowniki (czynnosc_powod 1–12)
odw2json/
  config.py         ładowanie + walidacja konfiguracji (błąd w YAML = czytelny komunikat przy starcie)
  document.py       kontrakty: Page, ExtractedDocument, Classifier, Extractor (interfejsy dla zespołu)
  text.py           PDF -> tekst (pdftotext) + awaryjny OCR Tesseract dla skanów   [Osoba 1 podmienia]
  classify.py       klasyfikator regułowy (wagi słów kluczowych z YAML)            [Osoba 2 może dodać LLM]
  extract_rules.py  ekstraktor regułowy sterowany YAML (regex / słownik / scope / split / findall)
  extract_llm.py    SZKIELET ekstraktora LLM (API zgodne z OpenAI), prompt generowany z YAML  [Osoba 2]
  schema_builder.py YAML -> model Pydantic (Instructor), JSON Schema, opis pól do promptu, pusty szablon
  normalize.py      rejestr normalizatorów (daty, kwoty, NIP, REGON, KRS, rachunki, BZP, TED, nazwy…)
  validators.py     sumy kontrolne NIP/REGON/PESEL/IBAN (ostrzeżenia dla transparentności)
  postprocess.py    normalizacja + walidacja + pola wyliczane, wymuszenie struktury i kolejności z YAML
  pipeline.py       orkiestracja, scalanie wielu ekstraktorów, zapis output/ i audit/
main.py             CLI
evaluate.py         F1 per pole / typ / całość, raport błędów CSV, Markdown, porównanie z poprzednim runem
```

**Scalanie ekstraktorów:** kolejność w `settings.yaml → pipeline.extractors` to priorytet. Wygrywa pierwsza
niepusta wartość. Pole może to nadpisać: `prefer: [rules, llm]` (np. NIP z regexu, tytuł z LLM). Gdy LLM nie
odpowiada, zostają reguły i aplikacja dalej zwraca wynik.

**Transparentność:** z `--audit audit` dla każdego pliku powstaje `audit/<sprawa>/<plik>.json` z wynikiem klasyfikacji
(punkty i powody), cytatem źródłowym i numerem strony dla każdego pola, informacją, który ekstraktor dał wartość,
oraz ostrzeżeniami walidacji. Z tego korzysta UI (Osoba 4). Katalog `output/` zawiera wyłącznie JSON-y
w formacie UZP.

## Jak dodać nowe pole albo typ dokumentu (bez kodu)

Nowe pole w istniejącym typie dopisujemy do sekcji `schema` w YAML:

```yaml
      kwota_wpisu:
        type: number
        description: "Kwota wpisu od odwołania"        # trafia do promptu LLM i do UI
        rules: [{regex: 'Wpis\s+od\s+odwołania\s*:?\s*(?P<v>[\d ]+,\d{2})'}]
```

Nowy typ dokumentu to nowy plik `config/document_types/faktura.yaml`:

```yaml
id: faktura
title: Faktura VAT
classification:
  min_score: 3
  keywords: [{pattern: 'Faktura\s+(?:VAT\s+)?nr', weight: 3}]
schema:
  numer: {type: string, rules: [{regex: 'Faktura nr (?P<v>\S+)'}]}
  kwota_brutto: number          # skrót: domyślna normalizacja kwoty
  data: date                    # skrót: każda polska data -> YYYY-MM-DD
```

Od razu działają: klasyfikacja, reguły, prompt i JSON Schema dla LLM, normalizacja, walidacja, ewaluacja.
Sprawdza to test `test_new_doc_type_without_code`.

Typy pól: `string, integer, number, date, enum (values: [...]), boolean_int, object (fields), list (items)`.
Normalizatory: `text, strip_quotes, smart_trailing_punct, company_name, person_name, email, lower, upper,
digits, nip, regon, krs, pesel, account, int, bool_int, amount, date, bzp, ted, case_number, lookup,
regex_replace, blank_to_null`.
Pola wyliczane (`derive`): `lookup` (np. opis z id w słowniku), `present` (1/0, gdy inne pole jest wypełnione),
`null_if_single_in_list`, `copy`, `default`.

## Ewaluacja

```
python evaluate.py --gt dane --run --label prompt_v3 --compare last --errors bledy.csv --md raport.md
```

- Listy (Odwołujący, Zamawiający, mocodawcy, pełnomocnicy) są dopasowywane algorytmem węgierskim, więc kolejność
  elementów nie obniża wyniku.
- TP/FP/FN liczone są per pole-liść. Dwa warianty: **F1** (para null–null pomijana) i **F1+null** (null–null = TP),
  bo nie wiemy, jak liczy UZP.
- Tryb `strict` (domyślny, porównanie dokładne, tak najpewniej liczy organizator) oraz `loose` (ignoruje wielkość
  liter i interpunkcję; pokazuje, ile punktów tracimy na samym formacie).
- Każdy run zapisuje się w `eval_runs/`, a `--compare last` pokazuje deltę per pole (np. `(+2.4)`).
- `--fail-under 0.95` zwraca kod 1, co pozwala użyć skryptu jako bramki w CI.

## Kontrakty dla zespołu

| Osoba | Co implementuje | Jak się wpina |
|---|---|---|
| 1 – OCR | klasa z `extract(path) -> ExtractedDocument` (strony: `number`, `text`, `source`, `confidence`) | `settings.yaml → pipeline.text_extractor.class` |
| 2 – LLM | `Extractor.extract(doc, doc_type) -> ExtractionResult(data, evidence)`; prompt i schemat z `schema_builder` | dopisać na początek `pipeline.extractors`; szkielet: `odw2json/extract_llm.py` |
| 2 – klasyfikacja | `Classifier.classify(doc) -> ClassificationResult` | `pipeline.classifier.class` |
| 4 – UI | `Pipeline().process_file(path)` zwraca `DocumentResult` (data, evidence, warnings, classification) | import w Streamlit/FastAPI |

Normalizację robi zawsze `postprocess.py`, więc LLM nie musi idealnie trzymać formatu dat, kwot ani NIP-ów.

## Wnioski z danych (ważne dla F1)

1. **PESEL w danych referencyjnych jest stringiem** (`"85071204278"`), choć `oczekiwane-json/pelnomocnictwo.json`
   pokazuje liczbę. Ustawiony jest `type: string`.
2. **Nie obcinać kropek w nazwach firm** („S.A.”, „sp. z o.o.”, „sp. k.”). Zwykłe `rstrip('.')` kosztowało ~110 błędów.
   Służy do tego normalizator `company_name`.
3. `data` i `miejscowosc` pochodzą **tylko z linii nagłówka**. Gdy jej brak (15/60), GT ma `null`, a data z podpisu
   na końcu pisma jest błędem.
4. `czynnosc_powod_id` wynika ze zdania „Zamawiający w dniu <data> <czynność>”, a nie z tytułu zarzutu.
   Jeśli takich zdań jest kilka, liczy się pierwsze, w którym jest czynność ze słownika (przykład 40).
   `czynnosc_opis` jest zawsze brany ze słownika na podstawie id.
5. Numery BZP/TED/postępowania bierzemy tylko z nagłówka (przed „Działając w imieniu…”). W treści zarzutów bywają
   numery **innych** postępowań.
6. Rachunki: bez `PL` i spacji (26 cyfr). Kwota to `float` (`200.0`). Nazwa pliku GT `przelew.json` odpowiada PDF
   `potwierdzenie_przelewu.pdf` (mapowanie w `settings.yaml → evaluation`).
7. Dokument nierozpoznany (np. ogłoszenie TED) daje **pusty plik** `.json` (wymóg README UZP).
8. Id 12 słownika powodów nie występuje w danych treningowych. Etykietę trzeba potwierdzić u mentorów.
9. Pozostałe błędy reguł dotyczą przypisania podpisujących do członków konsorcjum w pełnomocnictwach.
   To zadanie dla LLM.
