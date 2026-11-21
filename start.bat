@echo off
REM Odwolania2json - uruchomienie na Windows
REM   start.bat        -> (dwuklik) otwiera aplikacje w przegladarce (http://localhost:8501)
REM   start.bat batch  -> przetwarza PDF-y z .\input i zapisuje JSON-y do .\output
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

if /i "%~1"=="batch" (
    echo [3/3] Przetwarzam .\input do .\output ...
    ".venv\Scripts\python.exe" main.py -i input -o output
) else (
    echo [3/3] Uruchamiam aplikacje: http://localhost:8501   zamkniecie: zamknij to okno
    REM przegladarka otwiera sie po kilku sekundach, gdy serwer juz wystartuje
    start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
    ".venv\Scripts\python.exe" -m streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
)
endlocal
