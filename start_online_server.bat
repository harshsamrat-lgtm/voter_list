@echo off
chcp 65001 > nul
cd /d "%~dp0"
title UP Voter Search - Online Public Server
color 0A

echo ====================================================================
echo        मतदाता सेवा एवं ऑनलाइन सर्वर (Voter Service & Online Server)
echo ====================================================================
echo.
echo 1. पायथन परिवेश की जांच की जा रही है...

if not exist ".venv\Scripts\python.exe" (
    echo [त्रुटि] .venv\Scripts\python.exe नहीं मिला!
    echo कृपया सुनिश्चित करें कि वर्चुअल वातावरण तैयार है।
    pause
    exit /b 1
)

echo 2. सर्वर एवं सुरक्षित Cloudflare टनल शुरू किया जा रहा है...
echo.

.\.venv\Scripts\python.exe scripts\tunnel_manager.py

pause
