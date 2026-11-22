#!/usr/bin/env bash
# Odwołania2json – uruchomienie na macOS / Linux
#   ./start.sh         -> otwiera aplikację w przeglądarce (http://localhost:8501)
#   ./start.sh batch   -> przetwarza PDF-y z ./input i zapisuje JSON-y do ./output
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] Tworzę środowisko .venv ..."
  python3 -m venv .venv || { echo "BŁĄD: nie znaleziono Pythona 3.13+ (python.org albo: brew install python@3.13)"; exit 1; }
fi

echo "[2/3] Sprawdzam biblioteki ..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt \
  || { echo "BŁĄD: instalacja bibliotek nie powiodła się – zobacz komunikat powyżej."; exit 1; }

mkdir -p input output

if [ "$1" = "batch" ]; then
  echo "[3/3] Przetwarzam ./input -> ./output ..."
  exec .venv/bin/python main.py -i input -o output
else
  echo "[3/3] Uruchamiam aplikację: http://localhost:8501   zamknięcie: Ctrl+C"
  # przeglądarka otwiera się po kilku sekundach, gdy serwer już wystartuje
  ( sleep 5; if command -v open >/dev/null; then open http://localhost:8501; \
             elif command -v xdg-open >/dev/null; then xdg-open http://localhost:8501; fi ) >/dev/null 2>&1 &
  exec .venv/bin/python -m streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
fi
