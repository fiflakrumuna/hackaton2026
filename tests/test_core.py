"""Testy warstwy normalizacji, postprocessingu, konfiguracji i ewaluatora.

Uruchom:  python -m pytest -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from odw2json import normalize as N  # noqa: E402
from odw2json.config import ConfigError, load_config  # noqa: E402
from odw2json.postprocess import Postprocessor  # noqa: E402
from odw2json.schema_builder import build_pydantic_model, empty_instance, field_guide  # noqa: E402
from odw2json.validators import VALIDATORS  # noqa: E402
import evaluate as E  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config(ROOT / "config")


# ------------------------------------------------------------------ normalizatory
@pytest.mark.parametrize("raw,exp", [
    ("2026-03-21", "2026-03-21"), ("21.03.2026", "2026-03-21"), ("04-07-2026", "2026-07-04"),
    ("3 września 2026 roku", "2026-09-03"), ("Gdańsk, dnia 1 kwietnia 2026 r.", "2026-04-01"),
    ("26 maja 2026", "2026-05-26"), ("22 grudnia 2025", "2025-12-22"), ("15 października 2026", "2026-10-15"),
    ("31.02.2026", None), ("brak", None), (None, None),
])
def test_date(raw, exp):
    assert N.to_date(raw) == exp


@pytest.mark.parametrize("raw,exp", [
    ("200,00 PLN", 200.0), ("10 000,00 zł", 10000.0), ("1.234,56", 1234.56), ("200.00", 200.0),
    ("PLN 17.00", 17.0), (34, 34.0), ("15 000,00 zł", 15000.0), ("1,234.56", 1234.56), ("x", None),
])
def test_amount(raw, exp):
    assert N.amount(raw) == exp


def test_identifiers():
    assert N.nip("PL 999-284-40-65") == "9992844065"
    assert N.krs("54588") == "0000054588"
    assert N.regon("999 642 785") == "999642785"
    assert N.account("PL64 6217 1965 0035 1583 6600 8774") == "64621719650035158366008774"
    assert N.pesel("85071204278") == "85071204278"
    assert N.pesel("123") is None


def test_procurement_numbers():
    assert N.bzp("2026/BZP 00999731") == "2026/BZP00999731"
    assert N.bzp("2025/BZP 00270698/01") == "2025/BZP00270698/01"
    assert N.bzp("999073-2026") is None                     # TED to nie BZP
    assert N.ted("2026/S086-999073") == "999073-2026"
    assert N.ted("2025/S 233-998150") == "998150-2025"
    assert N.ted("999137-2026") == "999137-2026"
    assert N.case_number("ZP.154.2026.") == "ZP.154.2026"


def test_names():
    assert N.person_name("r. pr. Aleksandra Marciniak (nr wpisu na listę: WA 93198)") == "Aleksandra Marciniak"
    assert N.person_name("adw. Natalia Górecka") == "Natalia Górecka"
    # lekcja z ewaluacji: kropka w formie prawnej MUSI zostać
    assert N.company_name("Belmoria Rozwiązania S.A.") == "Belmoria Rozwiązania S.A."
    assert N.company_name("Lunareth Automatyka sp. z o.o.,") == "Lunareth Automatyka sp. z o.o."
    assert N.company_name("Danevia Serwis sp. k.") == "Danevia Serwis sp. k."
    assert N.company_name("„Kancelaria Prawna.”") == "Kancelaria Prawna"


def test_validators():
    assert VALIDATORS["nip_checksum"]("5260250274") is None          # poprawny NIP
    assert VALIDATORS["nip_checksum"]("5260250275") is not None
    assert VALIDATORS["pesel_checksum"]("44051401458") is None


def test_lookup(cfg):
    d = cfg.dictionaries
    assert N.lookup("4", dictionary="czynnosc_powod", _dictionaries=d) == 4
    assert N.lookup("Modyfikacja treści SWZ", dictionary="czynnosc_powod", _dictionaries=d) == 5


# ------------------------------------------------------------------ konfiguracja i schemat
def test_config_loads(cfg):
    assert {"odwolanie", "pelnomocnictwo", "przelew"} <= set(cfg.document_types)
    assert "OdwolanieKIO.czynnosc_powod_id" in cfg.document_types["odwolanie"].leaf_paths()


def test_bad_config_fails_fast(tmp_path):
    (tmp_path / "document_types").mkdir()
    (tmp_path / "document_types" / "x.yaml").write_text("id: x\nschema:\n  a: {type: string, normalize: [nie_ma]}\n")
    with pytest.raises(ConfigError, match="nieznany normalizator"):
        load_config(tmp_path)


def test_new_doc_type_without_code(tmp_path, cfg):
    """Nowy typ dokumentu = sam plik YAML -> działa schemat, prompt, model Pydantic i postprocessing."""
    import shutil
    shutil.copytree(ROOT / "config", tmp_path / "config")
    (tmp_path / "config" / "document_types" / "faktura.yaml").write_text(
        "id: faktura\nschema:\n  numer: {type: string, rules: [{regex: 'Faktura nr (?P<v>\\S+)'}]}\n"
        "  kwota_brutto: {type: number}\n  data: date\n", encoding="utf-8")
    c = load_config(tmp_path / "config")
    dt = c.document_types["faktura"]
    assert empty_instance(dt) == {"numer": None, "kwota_brutto": None, "data": None}
    assert "kwota_brutto (number)" in field_guide(dt)
    m = build_pydantic_model(dt)
    assert m.model_validate({"numer": "F/1"}).model_dump(by_alias=True)["numer"] == "F/1"
    out = Postprocessor(c).run(dt, {"kwota_brutto": "1 230,00 zł", "data": "5 maja 2026", "smiec": 1}).data
    assert out == {"numer": None, "kwota_brutto": 1230.0, "data": "2026-05-05"}


def test_pydantic_alias_with_dash(cfg):
    m = build_pydantic_model(cfg.document_types["odwolanie"])
    schema = m.model_json_schema(by_alias=True)
    assert "przez-pelnomocnika" in json.dumps(schema)


# ------------------------------------------------------------------ postprocessing
def test_postprocess_odwolanie(cfg):
    raw = {
        "OdwolanieKIO": {"data": "13.10.2025", "czynnosc_powod_id": "4", "czynnosc_opis": "cokolwiek"},
        "Odwolujacy": [{"nazwa": " STRABAG  Sp. z o.o. ", "nip": "PL 526-025-02-74",
                        "pelnomocnik": {"imie_nazwisko": "r. pr. Marcel Kielce", "mail": "Info@Prawnik.XY"}}],
        "Zamawiajacy": [{"nazwa": "A", "prowadzacy_postepowanie": 1}],
        "ZamowieniePubliczne": {"numer_ted": "2025/S 199-641651", "numer_bzp": "brak"},
    }
    r = Postprocessor(cfg).run(cfg.document_types["odwolanie"], raw)
    d = r.data
    assert list(d) == ["OdwolanieKIO", "Odwolujacy", "Zamawiajacy", "ZamowieniePubliczne"]   # kolejność z YAML
    assert d["OdwolanieKIO"]["data"] == "2025-10-13"
    assert d["OdwolanieKIO"]["czynnosc_powod_id"] == 4
    assert d["OdwolanieKIO"]["czynnosc_opis"] == "Publikacja specyfikacji warunków zamówienia (SWZ)"  # ze słownika
    o = d["Odwolujacy"][0]
    assert o["nazwa"] == "STRABAG Sp. z o.o." and o["nip"] == "5260250274"
    assert o["przez-pelnomocnika"] == 1                                   # wyliczone
    assert o["pelnomocnik"] == {"imie_nazwisko": "Marcel Kielce", "nazwa": None, "mail": "info@prawnik.xy"}
    assert d["Zamawiajacy"][0]["prowadzacy_postepowanie"] is None         # jeden zamawiający -> null
    assert d["ZamowieniePubliczne"] == {"numer_bzp": None, "numer_postepowania": None,
                                        "numer_ted": "641651-2025", "tytul": None}


def test_postprocess_many_zamawiajacy(cfg):
    raw = {"Zamawiajacy": [{"nazwa": "A", "prowadzacy_postepowanie": 1}, {"nazwa": "B"}]}
    d = Postprocessor(cfg).run(cfg.document_types["odwolanie"], raw).data
    assert [z["prowadzacy_postepowanie"] for z in d["Zamawiajacy"]] == [1, 0]


# ------------------------------------------------------------------ ewaluator
def test_eval_list_alignment_is_order_independent():
    g = {"p": [{"n": "A", "x": 1}, {"n": "B", "x": 2}]}
    p = {"p": [{"n": "B", "x": 2}, {"n": "A", "x": 1}]}
    pairs = E._pairs(g, p, "", "strict")
    assert all(E.equal(a, b) for _, a, b in pairs)


def test_eval_counts(tmp_path):
    gt = tmp_path / "gt" / "c1"
    pr = tmp_path / "pr" / "c1"
    gt.mkdir(parents=True); pr.mkdir(parents=True)
    (gt / "przelew.json").write_text(json.dumps({"x": {"a": "1", "b": None, "c": 5.0}}))
    (pr / "potwierdzenie_przelewu.json").write_text(json.dumps({"x": {"a": "1", "b": "z", "c": 5}}))
    (gt / "neg.json").write_text("")                    # dokument negatywny
    (pr / "neg.json").write_text("")
    settings = {"evaluation": {"gt_to_pdf_stem": {"przelew": "potwierdzenie_przelewu"}}}
    r = E.evaluate(tmp_path / "gt", tmp_path / "pr", settings)
    assert (r.total.tp, r.total.fp, r.total.fn) == (2, 1, 0)   # a, c trafione; b nadmiarowe
    assert E.equal(5.0, 5) and not E.equal("5", 5)


def test_load_json_with_comments(tmp_path):
    f = tmp_path / "a.json"
    f.write_text('{"a": "http://x", // komentarz\n "b": 1, // c\n}')
    assert E.load_json(f) == {"a": "http://x", "b": 1}


# ------------------------------------------------------------------ scalanie ekstraktorów
def test_merge_priority_and_prefer(cfg):
    from odw2json.document import ExtractionResult
    from odw2json.pipeline import merge_results
    dt = cfg.document_types["przelew"]
    llm = ExtractionResult({"przelew_bankowy": {"kwota": 99.0, "tytul": "z LLM", "z_rachunku": None}}, method="llm")
    rules = ExtractionResult({"przelew_bankowy": {"kwota": 100.0, "tytul": "z reguł", "z_rachunku": "123"}},
                             evidence={"przelew_bankowy.z_rachunku": {"quote": "123"}}, method="rules")
    dt.schema.fields["przelew_bankowy"].fields["kwota"].raw["prefer"] = ["rules", "llm"]   # jak `prefer:` w YAML
    try:
        merged, ev = merge_results(dt, [llm, rules])
    finally:
        dt.schema.fields["przelew_bankowy"].fields["kwota"].raw.pop("prefer")
    b = merged["przelew_bankowy"]
    assert b["tytul"] == "z LLM"          # domyślnie pierwszy ekstraktor wygrywa
    assert b["kwota"] == 100.0            # prefer: [rules, llm]
    assert b["z_rachunku"] == "123"       # LLM dał null -> fallback do reguł
    assert ev["przelew_bankowy.z_rachunku"]["extractor"] == "rules"
