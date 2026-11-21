# ⚖️ Odwołania2json

**Automatyczna ekstrakcja danych z akt spraw odwoławczych KIO do kanonicznego modelu JSON.**

Aplikacja przyjmuje pliki PDF wchodzące w skład odwołania (pismo odwołania, pełnomocnictwo, potwierdzenie
przelewu wpisu), sama rozpoznaje typ każdego dokumentu i zwraca jeden plik JSON na dokument w formacie
wymaganym przez UZP. Działa zarówno na PDF-ach z warstwą tekstową, jak i na skanach (OCR).

```
PDF ─► tekst / OCR ─► klasyfikacja typu ─► ekstrakcja (reguły + LLM) ─► normalizacja i walidacja ─► JSON
```

**Najważniejsze cechy**

- 📄 **Trzy typy dokumentów:** odwołanie, pełnomocnictwo, potwierdzenie przelewu. Dokument nierozpoznany daje
  pusty plik `.json`, zgodnie z README UZP.
- 🧩 **Nowe typy i pola bez zmian w kodzie.** Każdy typ dokumentu to jeden plik YAML w `config/document_types/`.
- 🔍 **Transparentność.** Dla każdego pola zapisywany jest cytat źródłowy, numer strony i informacja, który
  ekstraktor dał wartość.
- 🖥️ **On-premise, bez GPU.** OCR działa lokalnie (Tesseract), a LLM jest wołany przez API.
- 🔓 **Wyłącznie open source**, w tym otwarto-źródłowy model językowy.

---

## Wyniki (zbiór treningowy UZP, 180 dokumentów)

Same reguły, bez LLM:

| | F1 (null ignorowane) | F1 + null |
|---|---|---|
| **micro, całość** | **0.993** | 0.994 |
| odwołanie | 1.000 | 1.000 |
| pełnomocnictwo | 0.979 | 0.985 |
| przelew | 0.989 | 0.989 |

168 z 180 dokumentów jest w 100% poprawnych. Szczegóły są w [`docs/raport_jakosci.md`](docs/raport_jakosci.md).

Dane treningowe są syntetyczne i bardzo regularne, a zbiór testowy to realne sprawy (często skany). Dlatego
reguły traktujemy jako szybki i pewny fundament dla pól o stałym formacie (NIP, KRS, numery, kwoty), a LLM
uzupełnia pola, których reguły nie znalazły.

---

## Uruchomienie

### 1. Wymagania

- **Python 3.10+**
- **Tesseract OCR** z pakietami języków `pol` i `osd` (`osd` służy do wykrywania obróconych stron)
  - Windows: instalator z [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki).
    Przy instalacji zaznacz język *Polish*.
  - Linux: `sudo apt install tesseract-ocr tesseract-ocr-pol tesseract-ocr-osd`
  - Sprawdzenie: `tesseract --list-langs` powinno pokazać `osd` i `pol`.
  - Jeśli Tesseract nie jest w `PATH`, ustaw `tesseract_cmd` w `config/ingest.yaml`.

### 2. Instalacja

```powershell
git clone https://github.com/fiflakrumuna/hackaton2026.git
cd hackaton2026
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Konfiguracja LLM (opcjonalna)

Utwórz plik `.env` w katalogu głównym:

```ini
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=twoj_klucz
LLM_MODEL=qwen/qwen3.8-27b
```

Bez tych zmiennych aplikacja działa na samych regułach. Ekstraktor LLM jest wtedy po prostu pomijany.

### 4. Przetwarzanie wsadowe (format UZP)

Wrzuć PDF-y do `input/` (mogą być w podfolderach, np. jeden folder na sprawę) i uruchom:

```powershell
python main.py                                   # input/ -> output/
python main.py -i input -o output --audit audit  # + cytaty źródłowe i ostrzeżenia
python main.py --file sciezka\do\pliku.pdf       # jeden plik, wynik na ekranie
```

Wynik trafia do `output/` z zachowaniem struktury podfolderów (`input/sprawa1/0003.pdf` → `output/sprawa1/0003.json`).

### 5. Interfejs graficzny

```powershell
streamlit run app.py
```

W przeglądarce można wgrać dokumenty, uruchomić analizę i obejrzeć wyodrębnione dane obok cytatów źródłowych.
Tryb demo z danymi testowymi włącza zmienna środowiskowa `USE_MOCK=1`.

---

## Użyty model językowy (LLM)

| | |
|---|---|
| **Model** | `qwen/qwen3.8-27b` (Qwen, otwarte wagi) |
| **Dostęp** | API zgodne z OpenAI (`/chat/completions`), obecnie przez Groq |
| **Konfiguracja** | `.env` (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) oraz `config/settings.yaml` → `llm` |
| **Parametry** | `temperature: 0`, timeout 60 s |

**Jak LLM współpracuje z regułami:**

1. Prompt i schemat JSON są **generowane automatycznie z plików YAML** (opisy pól, typy, podpowiedzi `llm.hint`).
   Nowe pole w YAML od razu trafia do promptu.
2. Kolejność ekstraktorów w `settings.yaml → pipeline.extractors` to priorytet scalania. Reguły są pierwsze,
   a LLM uzupełnia tylko pola, których reguły nie znalazły. Pojedyncze pole może to nadpisać przez `prefer: [...]`.
3. Wynik LLM przechodzi przez tę samą normalizację i walidację co reguły (daty, kwoty, NIP/REGON/KRS, rachunki),
   więc model nie musi idealnie trzymać formatu.
4. Gdy API nie odpowiada, zostają wyniki reguł i aplikacja dalej zwraca poprawny JSON.

Dostawcę zmienia się w samym `.env`. Działa każdy serwer zgodny z API OpenAI, także lokalny
(np. Ollama, vLLM, llama.cpp server).

---

## Architektura

```
config/
  settings.yaml                 wybór pluginów (OCR, klasyfikator, ekstraktory), LLM, wyjście, ewaluacja
  ingest.yaml                   ustawienia OCR i preprocessingu skanów
  document_types/*.yaml         1 plik = 1 typ dokumentu: schemat JSON, opisy, reguły, klasyfikacja
  dictionaries/*.yaml           słowniki (np. czynnosc_powod 1–12)
pdf_ingest/                     PDF -> tekst: warstwa tekstowa albo OCR (Tesseract / RapidOCR),
                                prostowanie, obrót, odszumianie, usuwanie anonimizacji, cache wyników
odw2json/
  config.py         ładowanie i walidacja konfiguracji (błąd w YAML = czytelny komunikat przy starcie)
  document.py       kontrakty: Page, ExtractedDocument, Classifier, Extractor
  classify.py       klasyfikator regułowy (wagi słów kluczowych z YAML)
  extract_rules.py  ekstraktor regułowy sterowany YAML (regex / słownik / scope / split / findall)
  extract_llm.py    ekstraktor LLM (API zgodne z OpenAI), prompt generowany z YAML
  schema_builder.py YAML -> JSON Schema, opis pól do promptu, pusty szablon
  normalize.py      normalizatory (daty, kwoty, NIP, REGON, KRS, rachunki, BZP, TED, nazwy…)
  validators.py     sumy kontrolne NIP/REGON/PESEL/IBAN (ostrzeżenia dla transparentności)
  postprocess.py    normalizacja, walidacja, pola wyliczane, kolejność pól z YAML
  pipeline.py       orkiestracja, scalanie ekstraktorów, zapis output/ i audit/
main.py             CLI (przetwarzanie wsadowe)
app.py              interfejs Streamlit
evaluate.py         F1 per pole / typ / całość, raport błędów, porównanie z poprzednim runem
```

**Transparentność:** z opcją `--audit audit` dla każdego pliku powstaje `audit/<sprawa>/<plik>.json`.
Zawiera wynik klasyfikacji (punkty i powody), cytat źródłowy i numer strony dla każdego pola, informację,
który ekstraktor dał wartość, oraz ostrzeżenia walidacji. Katalog `output/` zawiera wyłącznie JSON-y w formacie UZP.

**Punkty rozszerzeń** (wymiana komponentu = zmiana jednej linii w `settings.yaml`):

| Komponent | Interfejs | Gdzie się wpina |
|---|---|---|
| OCR / ekstrakcja tekstu | `extract(path) -> ExtractedDocument` | `pipeline.text_extractor.class` |
| Klasyfikator | `classify(doc) -> ClassificationResult` | `pipeline.classifier.class` |
| Ekstraktor danych | `extract(doc, doc_type) -> ExtractionResult` | lista `pipeline.extractors` |

---

## Jak dodać nowe pole albo typ dokumentu (bez kodu)

Nowe pole w istniejącym typie dopisujemy do sekcji `schema` w YAML:

```yaml
      kwota_wpisu:
        type: number
        description: "Kwota wpisu od odwołania"        # trafia do promptu LLM i do UI
        rules: [{regex: 'Wpis\s+od\s+odwołania\s*:?\s*(?P<v>[\d ]+,\d{2})'}]
```

Nowy typ dokumentu to nowy plik, np. `config/document_types/faktura.yaml`:

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

Od razu działają: klasyfikacja, reguły, prompt i JSON Schema dla LLM, normalizacja, walidacja i ewaluacja.
Sprawdza to test `test_new_doc_type_without_code`. Poprawność konfiguracji sprawdza `python main.py --check-config`.

- **Typy pól:** `string, integer, number, date, enum (values: [...]), boolean_int, object (fields), list (items)`
- **Normalizatory:** `text, strip_quotes, smart_trailing_punct, company_name, person_name, email, lower, upper,
  digits, nip, regon, krs, pesel, account, int, bool_int, amount, date, bzp, ted, case_number, lookup,
  regex_replace, blank_to_null`
- **Pola wyliczane (`derive`):** `lookup` (np. opis z id w słowniku), `present` (1/0, gdy inne pole jest
  wypełnione), `null_if_single_in_list`, `copy`, `default`

---

## Ewaluacja i testy

```powershell
python evaluate.py --gt ..\hacknation-2026\dane --run --label moja_zmiana --compare last --errors bledy.csv --md raport.md
python -m pytest -q
```

- Listy (Odwołujący, Zamawiający, mocodawcy, pełnomocnicy) są dopasowywane algorytmem węgierskim, więc kolejność
  elementów nie obniża wyniku.
- TP/FP/FN liczone są per pole-liść, w dwóch wariantach: **F1** (para null–null pomijana) i **F1+null**
  (null–null = TP).
- Tryb `strict` (porównanie dokładne, domyślny) oraz `loose` (ignoruje wielkość liter i interpunkcję; pokazuje,
  ile punktów tracimy na samym formacie).
- Każdy run zapisuje się w `eval_runs/`, a `--compare last` pokazuje zmianę per pole.
- `--fail-under 0.95` zwraca kod 1, co pozwala użyć skryptu jako bramki w CI.

---

## Wnioski z analizy danych

1. **PESEL w danych referencyjnych jest stringiem** (`"85071204278"`), choć `oczekiwane-json/pelnomocnictwo.json`
   pokazuje liczbę. Ustawiony jest `type: string`.
2. **Nie obcinamy kropek w nazwach firm** („S.A.”, „sp. z o.o.”, „sp. k.”). Zwykłe `rstrip('.')` kosztowało
   ~110 błędów. Służy do tego normalizator `company_name`.
3. `data` i `miejscowosc` pochodzą **tylko z linii nagłówka**. Gdy jej brak, referencja ma `null`, a data
   z podpisu na końcu pisma byłaby błędem.
4. `czynnosc_powod_id` wynika z zaskarżonej czynności Zamawiającego (pierwszej, jeśli jest ich kilka), a nie
   z tytułu zarzutu. `czynnosc_opis` jest zawsze brany ze słownika na podstawie id.
5. Numery BZP/TED/postępowania bierzemy tylko z nagłówka. W treści zarzutów bywają numery **innych** postępowań.
6. Rachunki zapisujemy bez `PL` i spacji (26 cyfr). Kwota to `float` (`200.0`).
7. Dokument nierozpoznany (np. ogłoszenie TED) daje **pusty plik** `.json`.
