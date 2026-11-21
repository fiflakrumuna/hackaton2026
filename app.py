import json
import zipfile
from io import BytesIO
import streamlit as st
from pipeline import process_document, get_available_schemas, USE_MOCK

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

    st.subheader("📂 Konfiguracja klas dokumentów")
    st.caption("Schematy ładowane dynamicznie z katalogu `schemas/` bez zmiany kodu aplikacji:")

    schemas = get_available_schemas()
    if schemas:
        selected_schema = st.selectbox("Podgląd schematu:", list(schemas.keys()))
        with st.expander("Pokaż definicję JSON", expanded=False):
            st.json(schemas[selected_schema])
    else:
        st.error("Brak plików .json w folderze schemas/")

    if st.button("🔄 Odśwież schematy"):
        st.rerun()

    st.divider()
    st.markdown(
        "**Środowisko:**\n"
        "- Przetwarzanie lokalne (CPU)\n"
        "- Brak zależności od GPU\n"
        "- Komponenty 100% Open Source"
    )

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
        st.info(f"Wybrano plików: **{len(uploaded_files)}**")

    if run_btn:
        results = []
        errors = []

        for file in uploaded_files:
            with st.status(f"Przetwarzanie: **{file.name}**...", expanded=True) as status:
                try:
                    file_bytes = file.read()
                    res = process_document(file_bytes, file.name)
                    results.append(res)
                    status.update(
                        label=f"✅ **{file.name}** → Typ: `{res['document_type']}` ({res['processing_time_sec']}s)",
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
            f"📄 {res['filename']}  |  Klasa: **{res['document_type'].upper()}**  |  Czas: {res['processing_time_sec']}s",
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