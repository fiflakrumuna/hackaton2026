import json
import zipfile
from io import BytesIO
import streamlit as st
import pandas as pd
import yaml
from pipeline import process_document, get_available_schemas, get_document_types, reload_pipeline, USE_MOCK
from odw2json.config import ConfigError
from odw2json import type_builder as tb

AUTO = "__auto__"
NEW = "__new__"

st.set_page_config(
    page_title="Odwołania2json | UZP KIO",
    page_icon="⚖️",
    layout="wide",
)

# --- PASEK BOCZNY: ARCHITEKTURA I SCHEMATY ---
with st.sidebar:
    st.title("⚖️ Odwołania2json")
    st.caption("Transformacja akt spraw KIO do kanonicznego modelu JSON")
    st.divider()

    if USE_MOCK:
        st.warning("🧪 Tryb: **DEMO (Dane testowe)**\n\nZmień `USE_MOCK = False` w `pipeline.py` po podpięciu modelu.")
    else:
        st.success("🟢 Tryb: **PRODUKCYJNY (OCR + Open-Source LLM)**")

    st.subheader("📂 Schemat ekstrakcji")
    st.caption("Typy dokumentów ładowane dynamicznie z `config/document_types/*.yaml` – nowy typ = nowy plik YAML, bez zmiany kodu.")

    doc_types = get_document_types()
    type_options = [AUTO] + list(doc_types) + [NEW]
    # po zapisaniu nowego typu w kreatorze – od razu go zaznacz
    pending = st.session_state.pop("_select_after_save", None)
    if pending in type_options:
        st.session_state["doc_type_choice"] = pending
    if st.session_state.get("doc_type_choice") not in type_options:
        st.session_state["doc_type_choice"] = AUTO

    def _type_label(t):
        if t == AUTO:
            return "🤖 Automatycznie (rozpoznaj typ)"
        if t == NEW:
            return "➕ Dodaj nowy typ…"
        return f"📄 {doc_types[t]}"

    selected_type = st.radio(
        "Typ dokumentu:",
        type_options,
        key="doc_type_choice",
        format_func=_type_label,
        help="„Automatycznie” – system sam rozpozna typ każdego pliku. "
             "Wybór konkretnego typu wymusza jego schemat dla wszystkich wgranych plików. "
             "„Dodaj nowy typ” – zdefiniuj własny rodzaj dokumentu i jego pola.",
    )
    forced_type = None if selected_type in (AUTO, NEW) else selected_type

    schemas = get_available_schemas()
    if forced_type and forced_type in schemas:
        with st.expander(f"Pokaż schemat JSON: {doc_types[forced_type]}", expanded=False):
            st.json(schemas[forced_type])
    elif schemas:
        preview = st.selectbox("Podgląd schematu:", list(schemas.keys()),
                               format_func=lambda s: doc_types.get(s, s))
        with st.expander("Pokaż definicję JSON", expanded=False):
            st.json(schemas[preview])
    else:
        st.error("Brak typów dokumentów w config/document_types/")

    if st.button("🔄 Odśwież schematy"):
        st.rerun()

    st.divider()
    st.markdown(
        "**Środowisko:**\n"
        "- Przetwarzanie lokalne (CPU)\n"
        "- Brak zależności od GPU\n"
        "- Komponenty 100% Open Source"
    )

# --- KREATOR NOWEGO TYPU DOKUMENTU ---
def _clean(v) -> str:
    return "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()


def _after_save(type_id: str, path):
    reload_pipeline()
    st.session_state["_select_after_save"] = type_id
    st.session_state["_saved_msg"] = f"✅ Zapisano typ **{type_id}** → `{path}`. Jest już dostępny na liście w panelu bocznym."
    st.rerun()


def render_type_builder():
    st.header("➕ Nowy typ dokumentu")
    st.markdown(
        "Brakuje rodzaju dokumentu, który chcesz przetwarzać (np. **pozew**, **faktura**, **wezwanie**)? "
        "Zdefiniuj go tutaj. Zostanie zapisany jako plik `config/document_types/<id>.yaml`, "
        "więc od razu zadziała w interfejsie, w przetwarzaniu wsadowym (`main.py`) i w ewaluacji – bez zmiany kodu."
    )
    tab_wiz, tab_yaml, tab_mine = st.tabs(["🧩 Kreator", "📝 Edytor YAML (zaawansowany)", "🗂️ Moje typy"])

    with tab_wiz:
        c1, c2 = st.columns([2, 1])
        title = c1.text_input("Nazwa typu dokumentu *", placeholder="np. Pozew o zapłatę")
        type_id = c2.text_input("Identyfikator", value=tb.slugify(title), help="Nazwa pliku i klucz typu. Tworzy się sam z nazwy.")
        description = st.text_area("Opis (trafia do promptu LLM)", placeholder="np. Pozew wniesiony do sądu powszechnego o zapłatę należności.", height=70)
        keywords = st.text_area(
            "Słowa kluczowe do rozpoznawania *  (jedno w wierszu)",
            placeholder="POZEW\nPowód:\nPozwany:",
            help="Frazy, które występują w tym dokumencie. Po nich system automatycznie rozpozna typ pliku. "
                 "Wielkość liter nie ma znaczenia.",
            height=100,
        )

        st.markdown("**Pola do wyodrębnienia** – dodawaj wiersze przyciskiem ➕ pod tabelą.")
        st.caption(
            "**Etykieta w dokumencie** (opcjonalnie): tekst stojący przed wartością, np. `Sygn. akt:` – "
            "wtedy wartość wyciągają szybkie reguły. Bez etykiety pole wypełnia model językowy na podstawie **opisu**."
        )
        fields_df = st.data_editor(
            pd.DataFrame([
                {"name": "", "kind": "Tekst", "description": "", "label": ""},
            ]),
            num_rows="dynamic",
            use_container_width=True,
            key="tb_fields",
            column_config={
                "name": st.column_config.TextColumn("Nazwa pola *", help="np. Sygnatura akt"),
                "kind": st.column_config.SelectboxColumn("Rodzaj", options=list(tb.FIELD_KINDS), default="Tekst", required=True),
                "description": st.column_config.TextColumn("Opis (dla LLM)", width="large"),
                "label": st.column_config.TextColumn("Etykieta w dokumencie", help="np. Sygn. akt:"),
            },
        )
        fields = [{k: _clean(v) for k, v in row.items()} for row in fields_df.to_dict("records")]

        data, err = None, None
        try:
            data = tb.build_doc_type(title, description, keywords.splitlines(), fields, type_id or None)
        except ConfigError as e:
            err = str(e)

        b1, b2 = st.columns([1, 3])
        if b1.button("💾 Zapisz typ", type="primary", use_container_width=True, disabled=data is None):
            try:
                _after_save(data["id"], tb.save_doc_type(data))
            except ConfigError as e:
                st.error(f"Nie zapisano: {e}")
        if err and (title or keywords or any(f["name"] for f in fields)):
            b2.warning(err)
        if data:
            with st.expander("👁️ Podgląd pliku YAML, który zostanie zapisany", expanded=False):
                st.code(tb.to_yaml(data), language="yaml")

    with tab_yaml:
        st.caption("Pełna kontrola nad definicją typu – ten sam format co pliki w `config/document_types/`. "
                   "Możesz wkleić lub wgrać gotowy plik.")
        up = st.file_uploader("Wgraj plik .yaml", type=["yaml", "yml"], key="tb_yaml_upload")
        example = tb.to_yaml(tb.build_doc_type(
            "Pozew o zapłatę", "Pozew o zapłatę wniesiony do sądu.", ["POZEW", "Powód:"],
            [{"name": "Sygnatura akt", "kind": "Tekst", "description": "Sygnatura sprawy", "label": "Sygn. akt:"},
             {"name": "Wartość przedmiotu sporu", "kind": "Kwota / liczba", "description": "WPS w PLN", "label": "Wartość przedmiotu sporu:"},
             {"name": "Powód", "kind": "Nazwa firmy", "description": "Nazwa powoda"}]))
        text = st.text_area("Definicja YAML", value=up.getvalue().decode("utf-8") if up else example, height=380, key="tb_yaml_text")
        overwrite = st.checkbox("Nadpisz, jeśli typ o tym id już istnieje", value=False)
        if st.button("✔️ Sprawdź i zapisz", type="primary"):
            try:
                d = yaml.safe_load(text)
                if isinstance(d, dict):
                    d.setdefault("created_by", tb.USER_MARK)
                _after_save(d["id"], tb.save_doc_type(d, overwrite=overwrite))
            except yaml.YAMLError as e:
                st.error(f"Błąd składni YAML: {e}")
            except (ConfigError, KeyError, TypeError) as e:
                st.error(f"Nie zapisano: {e}")

    with tab_mine:
        mine = tb.user_types()
        if not mine:
            st.info("Nie dodano jeszcze żadnego własnego typu.")
        for tid, ttl in mine.items():
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"📄 **{ttl}**  `{tid}`")
            if c2.button("🗑️ Usuń", key=f"del_{tid}"):
                try:
                    tb.delete_user_type(tid)
                    reload_pipeline()
                    st.session_state["_saved_msg"] = f"Usunięto typ **{tid}**."
                    st.rerun()
                except ConfigError as e:
                    st.error(str(e))
        st.caption("Typy wbudowane (odwołanie, pełnomocnictwo, przelew) są chronione – z interfejsu można usuwać tylko typy dodane tutaj.")


if "_saved_msg" in st.session_state:
    st.success(st.session_state.pop("_saved_msg"))

if selected_type == NEW:
    render_type_builder()
    st.stop()

# --- GŁÓWNY EKRAN ---
st.header("📄 Ekstrakcja danych ze sprawy odwoławczej")
st.markdown(
    "Wgraj pojedynczy dokument PDF lub całą paczkę akt sprawy (np. **odwołanie**, **pełnomocnictwo**, **potwierdzenie przelewu**). "
    "System automatycznie rozpozna typ każdego pliku, wyodrębni dane i wskaże ich źródło w tekście."
)

uploaded_files = st.file_uploader(
    "Wybierz lub przeciągnij pliki PDF ze sprawy",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        run_btn = st.button("🚀 Uruchom ekstrakcję", type="primary", use_container_width=True)
    with col_info:
        mode = f"schemat: **{doc_types.get(forced_type, forced_type)}**" if forced_type else "typ rozpoznawany **automatycznie**"
        st.info(f"Wybrano plików: **{len(uploaded_files)}** · {mode}")

    if run_btn:
        results = []
        errors = []

        for file in uploaded_files:
            with st.status(f"Przetwarzanie: **{file.name}**...", expanded=True) as status:
                try:
                    file_bytes = file.read()
                    res = process_document(file_bytes, file.name, doc_type=forced_type)
                    results.append(res)
                    how = "wybrany ręcznie" if res.get("manual_type") else "rozpoznany"
                    status.update(
                        label=f"✅ **{file.name}** → Typ ({how}): `{res['document_type']}` ({res['processing_time_sec']}s)",
                        state="complete",
                        expanded=False,
                    )
                except Exception as e:
                    errors.append({"filename": file.name, "error": str(e)})
                    status.update(
                        label=f"❌ **{file.name}** → Błąd: {e}",
                        state="error",
                        expanded=True,
                    )

        st.session_state["extraction_results"] = results
        st.session_state["extraction_errors"] = errors

# --- PREZENTACJA WYNIKÓW ---
if "extraction_results" in st.session_state and st.session_state["extraction_results"]:
    results = st.session_state["extraction_results"]
    st.divider()

    # Podsumowanie i eksport zbiorczy całej sprawy
    st.subheader("📦 Zbiorczy wynik sprawy (Gotowy do eksportu)")

    # Składamy jeden kanoniczny obiekt JSON dla całej sprawy
    case_bundle = {
        "liczba_dokumentow": len(results),
        "dokumenty": [
            {
                "plik_zrodlowy": r["filename"],
                "klasa_dokumentu": r["document_type"],
                "dane": r["extracted_json"],
            }
            for r in results
        ],
    }
    bundle_json_str = json.dumps(case_bundle, indent=2, ensure_ascii=False)

    # Przygotowanie paczki ZIP z osobnymi plikami JSON
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sprawa_zbiorcza.json", bundle_json_str)
        for r in results:
            single_str = json.dumps(r["extracted_json"], indent=2, ensure_ascii=False)
            zf.writestr(f"{r['filename']}.json", single_str)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Przetworzone pliki", len(results))
    avg_time = round(sum(r["processing_time_sec"] for r in results) / len(results), 2)
    m2.metric("Średni czas / plik", f"{avg_time} s")
    with m3:
        st.download_button(
            label="⬇️ Pobierz zbiorczy JSON",
            data=bundle_json_str,
            file_name="sprawa_kio_wynik.json",
            mime="application/json",
            use_container_width=True,
        )
    with m4:
        st.download_button(
            label="🗂️ Pobierz paczkę ZIP",
            data=zip_buffer.getvalue(),
            file_name="wyniki_ekstrakcji_kio.zip",
            mime="application/zip",
            use_container_width=True,
        )

    with st.expander("Podgląd zbiorczego JSON-a całej sprawy", expanded=False):
        st.json(case_bundle)

    st.divider()
    st.subheader("🔍 Szczegóły dokumentów i transparentność ekstrakcji")

    for idx, res in enumerate(results):
        with st.expander(
            f"📄 {res['filename']}  |  Klasa: **{res['document_type'].upper()}**"
            f"{' (wybrana ręcznie)' if res.get('manual_type') else ''}  |  Czas: {res['processing_time_sec']}s",
            expanded=True,
        ):
            col_json, col_src = st.columns([1, 1], gap="large")

            with col_json:
                st.markdown("#### Wyodrębnione dane (JSON)")
                single_json_str = json.dumps(res["extracted_json"], indent=2, ensure_ascii=False)
                st.json(res["extracted_json"])

                st.download_button(
                    label=f"⬇️ Pobierz {res['filename']}.json",
                    data=single_json_str,
                    file_name=f"{res['filename']}.json",
                    mime="application/json",
                    key=f"btn_dl_{idx}",
                )

            with col_src:
                st.markdown("#### Źródło danych w dokumencie (Transparentność)")
                sources = res.get("sources", {})
                if sources:
                    for field_key, meta in sources.items():
                        page_num = meta.get("page", "?")
                        quote_text = meta.get("quote", "Brak dokładnego cytatu")
                        st.markdown(f"**`{field_key}`** — *Strona {page_num}*")
                        st.info(f"„{quote_text}”")
                else:
                    st.warning("Brak metadanych źródłowych dla wyodrębnionych pól.")