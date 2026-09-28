@echo off
cd /d "%~dp0"

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

