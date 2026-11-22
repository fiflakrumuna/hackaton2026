"""Samodzielna demonstracja wywołania LLM (klasyfikacja + ekstrakcja) na pliku tekstowym.

Nie jest częścią aplikacji – właściwy ekstraktor LLM to odw2json/extract_llm.py
(prompt i schemat generowane z config/document_types/*.yaml).
Użycie: tekst dokumentu w tools/odwolanie.txt, klucz GROQ_API_KEY w .env, potem
    python tools/llm_api_demo.py
"""
import json
import os
import requests

current_dir = os.path.dirname(os.path.abspath(__file__))
candidates = [
    os.path.join(current_dir, ".env"),
    os.path.join(current_dir, "..", ".env"),
    os.path.join(current_dir, "..", "..", ".env")
]

for env_path in candidates:
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8-sig") as f:
            for line in f:
                clean = line.strip()
                if clean and "=" in clean and not clean.startswith("#"):
                    k, v = clean.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
        break

api_key = os.environ.get("GROQ_API_KEY") or ""
url = "https://api.groq.com/openai/v1/chat/completions"
model_name = "qwen/qwen3.8-27b"

txt_path = os.path.join(current_dir, "odwolanie.txt")
with open(txt_path, encoding="utf-8") as f:
    doc_text = f.read()

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

powody_kio = {
    1: "Decyzja o odrzuceniu oferty",
    2: "Ogłoszenie wyboru oferty",
    3: "Decyzja o wykluczeniu wykonawcy",
    4: "Publikacja specyfikacji warunków zamówienia (SWZ)",
    5: "Modyfikacja treści SWZ",
    6: "Odpowiedzi/wyjaśnienia do SWZ",
    7: "Zaniechanie odrzucenia oferty",
    8: "Zaniechanie wykluczenia wykonawcy",
    9: "Decyzja o unieważnieniu postępowania",
    10: "Zaniechanie unieważnienia postępowania",
    11: "Opis przedmiotu zamówienia (OPZ)",
    12: "Projektowane postanowienia umowy"
}

classify_payload = {
    "model": model_name,
    "messages": [
        {
            "role": "system",
            "content": "Klasyfikujesz pisma z postepowan KIO. Zwroc wylacznie JSON: {\"typ\": \"odwolanie\" | \"pelnomocnictwo\" | \"przelew\" | \"inne\"}."
        },
        {
            "role": "user",
            "content": doc_text[:3000]
        }
    ],
    "response_format": {"type": "json_object"},
    "temperature": 0
}

classify_resp = requests.post(url, headers=headers, json=classify_payload, timeout=30)
classify_resp.raise_for_status()
classify_content = classify_resp.json()["choices"][0]["message"]["content"]
doc_type = json.loads(classify_content).get("typ", "inne")

skeletons = {
    "odwolanie": {
        "OdwolanieKIO": {
            "data": None,
            "czynnosc_dzien": None,
            "czynnosc_powod_id": None,
            "czynnosc_opis": None,
            "miejscowosc": None
        },
        "Odwolujacy": [
            {
                "krs": None,
                "nazwa": None,
                "nip": None,
                "regon": None,
                "przez-pelnomocnika": 0,
                "pelnomocnik": {
                    "imie_nazwisko": None,
                    "nazwa": None,
                    "mail": None
                }
            }
        ],
        "Zamawiajacy": [
            {
                "krs": None,
                "nazwa": None,
                "nip": None,
                "regon": None,
                "prowadzacy_postepowanie": None
            }
        ],
        "ZamowieniePubliczne": {
            "numer_bzp": None,
            "numer_postepowania": None,
            "numer_ted": None,
            "tytul": None
        }
    },
    "pelnomocnictwo": {
        "mocodawca": [
            {
                "data": {
                    "imie_nazwisko": None,
                    "krs": None,
                    "nazwa": None,
                    "nip": None,
                    "typ": None
                },
                "podpisujacy": [
                    {
                        "imie_nazwisko": None
                    }
                ]
            }
        ],
        "pelnomocnicy": [
            {
                "imie_nazwisko": None,
                "pesel": None
            }
        ]
    },
    "przelew": {
        "przelew_bankowy": {
            "data_operacji": None,
            "kwota": None,
            "na_rachunek": None,
            "numer_transakcji": None,
            "tytul": None,
            "z_rachunku": None
        }
    }
}

target_skeleton = skeletons.get(doc_type, skeletons["odwolanie"])

extract_rules = (
    "Wyciagnij dane z tekstu dokumentu do podanego szkieletu JSON. "
    "Zachowaj dokladne klucze i typy. Brak danych oznacz jako null. "
    "Dla odwolania: czynnosc_powod_id ustal wylacznie jako liczbe 1-12 na podstawie pierwszego zarzutu/czynnosci: "
    "1=Decyzja o odrzuceniu oferty, 2=Ogloszenie wyboru oferty, 3=Decyzja o wykluczeniu wykonawcy, "
    "4=Publikacja specyfikacji warunkow zamowienia (SWZ), 5=Modyfikacja tresci SWZ, 6=Odpowiedzi/wyjasnienia do SWZ, "
    "7=Zaniechanie odrzucenia oferty, 8=Zaniechanie wykluczenia wykonawcy, 9=Decyzja o uniewaznieniu postepowania, "
    "10=Zaniechanie uniewaznienia postepowania, 11=Opis przedmiotu zamowienia (OPZ), 12=Projektowane postanowienia umowy. "
    "Pole przez-pelnomocnika zwracaj jako 1 lub 0. "
    "Zwroc wylacznie poprawny JSON."
)

extract_payload = {
    "model": model_name,
    "messages": [
        {
            "role": "system",
            "content": extract_rules
        },
        {
            "role": "user",
            "content": f"Typ dokumentu: {doc_type}\nSzkielet JSON:\n{json.dumps(target_skeleton, ensure_ascii=False)}\n\nTekst dokumentu:\n{doc_text[:8000]}"
        }
    ],
    "response_format": {"type": "json_object"},
    "temperature": 0
}

extract_resp = requests.post(url, headers=headers, json=extract_payload, timeout=45)
extract_resp.raise_for_status()
extracted_json = json.loads(extract_resp.json()["choices"][0]["message"]["content"])

if doc_type == "odwolanie" and isinstance(extracted_json.get("OdwolanieKIO"), dict):
    raw_id = extracted_json["OdwolanieKIO"].get("czynnosc_powod_id")
    if isinstance(raw_id, str) and raw_id.isdigit():
        raw_id = int(raw_id)
    if isinstance(raw_id, int) and raw_id in powody_kio:
        extracted_json["OdwolanieKIO"]["czynnosc_powod_id"] = raw_id
        extracted_json["OdwolanieKIO"]["czynnosc_opis"] = powody_kio[raw_id]
    else:
        extracted_json["OdwolanieKIO"]["czynnosc_powod_id"] = None

    if isinstance(extracted_json.get("Odwolujacy"), list):
        for odwolujacy in extracted_json["Odwolujacy"]:
            if isinstance(odwolujacy, dict):
                val = odwolujacy.get("przez-pelnomocnika")
                if val is True or val == 1 or str(val).strip().lower() in ("1", "true", "tak"):
                    odwolujacy["przez-pelnomocnika"] = 1
                else:
                    odwolujacy["przez-pelnomocnika"] = 0

print("Wykryty typ:", doc_type)
print(json.dumps(extracted_json, indent=2, ensure_ascii=False))