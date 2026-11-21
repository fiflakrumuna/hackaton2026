@echo off
REM Odwolania2json - uruchomienie na Windows
REM   start.bat        -> przetwarza PDF-y z .\input i zapisuje JSON-y do .\output
REM   start.bat gui    -> otwiera aplikacje w przegladarce (http://localhost:8501)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Tworze srodowisko .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo BLAD: nie znaleziono Pythona 3.13+. Zainstaluj go z python.org i zaznacz "Add python.exe to PATH".
        pause
        exit /b 1
    )
)

echo [2/3] Sprawdzam biblioteki ...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo BLAD: instalacja bibliotek nie powiodla sie - zobacz komunikat powyzej.
    pause
    exit /b 1
)

if not exist "input" mkdir input
if not exist "output" mkdir output

if /i "%~1"=="gui" (
    echo [3/3] Uruchamiam aplikacje: http://localhost:8501   zamkniecie: Ctrl+C
    ".venv\Scripts\python.exe" -m streamlit run app.py
) else (
    echo [3/3] Przetwarzam .\input do .\output ...
    ".venv\Scripts\python.exe" main.py -i input -o output
)
endlocal
