@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Virtual environment create kiya ja raha hai...
    python -m venv .venv
    echo [INFO] Dependencies install ho rahi hain...
    .venv\Scripts\pip install -r requirements.txt
)

if exist "start_silent.vbs" (
    start "" wscript.exe "start_silent.vbs"
    exit
)

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "scripts\app_browser.py"
    exit
)

start "" pythonw "scripts\app_browser.py"
exit

