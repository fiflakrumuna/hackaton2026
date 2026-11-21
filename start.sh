#!/usr/bin/env bash
# Odwołania2json – uruchomienie na macOS / Linux
#   ./start.sh       -> przetwarza PDF-y z ./input i zapisuje JSON-y do ./output
#   ./start.sh gui   -> otwiera aplikację w przeglądarce (http://localhost:8501)
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] Tworzę środowisko .venv ..."
  python3 -m venv .venv || { echo "BŁĄD: brak Pythona 3.13+"; exit 1; }
fi

echo "[2/3] Sprawdzam biblioteki ..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt

mkdir -p input output

if [ "$1" = "gui" ]; then
  echo "[3/3] Uruchamiam aplikację: http://localhost:8501  (zamknięcie: Ctrl+C)"
  exec .venv/bin/python -m streamlit run app.py
else
  echo "[3/3] Przetwarzam ./input -> ./output ..."
  exec .venv/bin/python main.py -i input -o output
fi
