@echo off
cd /d "%~dp0"
echo ========================================================
echo        UP Voter Portal - Admin Password Reset
echo ========================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\reset_admin_password.py" 222333
) else (
    python "scripts\reset_admin_password.py" 222333
)

echo.
pause
