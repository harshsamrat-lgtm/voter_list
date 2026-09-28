@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ====================================================================
echo     ऑटो-स्टार्ट सेटिंग हटाना (Disable Auto-Start)
echo ====================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [त्रुटि] वर्चुअल वातावरण नहीं मिला।
    pause
    exit /b 1
)

.\.venv\Scripts\python.exe scripts\setup_autostart.py --disable
echo.
echo ====================================================================
pause
