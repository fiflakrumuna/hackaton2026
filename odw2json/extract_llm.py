"""Ekstraktor LLM – dowolne API zgodne z OpenAI (/chat/completions).

Prompt i schemat JSON są generowane z YAML, więc nowy typ dokumentu / nowe pole
nie wymaga zmian w tym pliku. Adres, klucz i model pochodzą z .env
(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL); włączenie w settings.yaml:

    extractors:
      - {class: odw2json.extract_rules:RuleExtractor}
      - {class: odw2json.extract_llm:LLMExtractor}

Wynik przechodzi potem przez tę samą normalizację co reguły (postprocess.py),
więc LLM nie musi idealnie trzymać formatu dat/kwot/NIP-ów.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request

from .config import AppConfig, DocumentType
from .document import ExtractedDocument, ExtractionResult
from .schema_builder import empty_instance, field_guide, json_schema

log = logging.getLogger("odw2json.llm")

SYSTEM = (
    "Jesteś precyzyjnym systemem ekstrakcji danych z polskich dokumentów zamówień publicznych. "
    "Zwracasz WYŁĄCZNIE poprawny JSON. Nie zgaduj: jeśli informacji nie ma w tekście, wpisz null. "
    "Wartości przepisuj dosłownie z dokumentu (nazwy firm z formą prawną, np. 'sp. z o.o.')."
)


class LLMExtractor:
    name = "llm"

    def __init__(self, cfg: AppConfig, max_chars: int = 12000, with_evidence: bool = True, use_json_schema: bool = False, **opts):
        llm = {**(cfg.get("llm", {}) or {}), **opts}
        self.base_url = (llm.get("base_url") or "").rstrip("/")
        self.api_key = llm.get("api_key") or ""
        self.model = llm.get("model") or ""
        self.temperature = float(llm.get("temperature", 0))
        self.timeout = float(llm.get("timeout_s", 60))
        self.max_chars = max_chars
        self.with_evidence = with_evidence
        self.use_json_schema = use_json_schema
        self.max_retries = int(llm.get("max_retries", 4))     # ponowienia przy 429 / 5xx
        self.max_wait_s = float(llm.get("max_wait_s", 60))    # najdłuższe pojedyncze czekanie

    def build_messages(self, doc: ExtractedDocument, dt: DocumentType) -> list[dict]:
        skeleton = json.dumps(empty_instance(dt), ensure_ascii=False, indent=1)
        hint = (dt.llm or {}).get("system_hint", "")
        ev = ('\nDodatkowo zwróć klucz "zrodla": {"ścieżka.pola": "dosłowny cytat z dokumentu"} dla każdego '
              'wypełnionego pola. Format odpowiedzi: {"dane": <JSON wg szablonu>, "zrodla": {...}}') if self.with_evidence else ""
        user = (f"Typ dokumentu: {dt.title}. {dt.description}\n{hint}\n\nPola do wypełnienia:\n{field_guide(dt)}\n\n"
                f"Szablon JSON (zachowaj dokładnie te klucze):\n{skeleton}{ev}\n\n"
                f"DOKUMENT:\n<<<\n{doc.text[: self.max_chars]}\n>>>")
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    def extract(self, doc: ExtractedDocument, dt: DocumentType) -> ExtractionResult:
        if not (self.base_url and self.model):
            return ExtractionResult(data={}, method=self.name, error="LLM nie skonfigurowany (llm.base_url / llm.model)")
        body = {"model": self.model, "temperature": self.temperature, "messages": self.build_messages(doc, dt)}
        if self.use_json_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": dt.id, "schema": json_schema(dt, with_evidence=self.with_evidence)}}
        else:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                                              # domyślny "Python-urllib" jest blokowany przez Cloudflare (403)
                                              "User-Agent": "odw2json/1.0"})
        content = self._post(req)
        parsed = _parse_json(content)
        data = parsed.get("dane", parsed) if self.with_evidence else parsed
        data = _reshape(dt.schema, data)
        evidence = {}
        for path, quote in (parsed.get("zrodla") or {}).items():
            if isinstance(quote, str) and quote.strip():
                # weryfikacja cytatu: czy naprawdę występuje w dokumencie (ochrona przed halucynacją)
                found = _norm(quote)[:80] in _norm(doc.text)
                evidence[path] = {"quote": quote[:300], "page": doc.find_page(quote), "method": "llm", "verified": found}
        return ExtractionResult(data=data, evidence=evidence, method=self.name)

    def _post(self, req: urllib.request.Request) -> str:
        """Wywołanie API z ponawianiem przy limicie (429) i chwilowych błędach serwera (5xx)."""
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                if attempt >= self.max_retries or not (e.code == 429 or e.code >= 500):
                    raise
                try:
                    wait = float(e.headers.get("retry-after") or 0)
                except ValueError:
                    wait = 0
                wait = min(max(wait, 2 ** attempt * 5), self.max_wait_s)
                log.warning("LLM HTTP %s – ponawiam za %.0f s (próba %d/%d)", e.code, wait, attempt + 1, self.max_retries)
                time.sleep(wait)
        raise RuntimeError("LLM: wyczerpano próby")


def _reshape(fs, value):
    """Dopasowuje odpowiedź LLM do struktury schematu z YAML.

    LLM-y lubią „spłaszczać” zagnieżdżone obiekty, np. zamiast
    mocodawca[].data.{krs, nazwa} zwracają mocodawca[].{krs, nazwa}.
    Jeśli brakuje obiektu-pola, a jego pola leżą poziom wyżej, przenosimy je na miejsce.
    Działa dla każdego typu dokumentu (sterowane schematem, bez nazw pól w kodzie).
    """
    if fs.type == "list" and fs.items is not None:
        if isinstance(value, dict):          # pojedynczy obiekt zamiast listy
            value = [value]
        return [_reshape(fs.items, v) for v in value] if isinstance(value, list) else value
    if fs.type != "object" or not isinstance(value, dict):
        return value
    out = dict(value)
    for key, sub in fs.fields.items():
        if sub.type == "object" and not isinstance(out.get(key), dict):
            lifted = {k: out.pop(k) for k in list(out) if k in sub.fields and k not in fs.fields}
            if lifted:
                out[key] = lifted
        if key in out:
            out[key] = _reshape(sub, out[key])
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _parse_json(s: str) -> dict:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        return json.loads(m.group(0)) if m else {}
