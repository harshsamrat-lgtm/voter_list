"""
Staging Preparation Script for Inno Setup Installer.
Copies necessary project files into a clean 'dist_staging' directory:
1. Self-contained portable Python runtime from C:\\Python314 and .venv\\Lib\\site-packages.
2. Backend, frontend, models, tools/tesseract, cloudflared, scripts, icons.
3. Clean configuration and startup scripts.
"""

import os
import sys
import shutil
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = BASE_DIR / "dist_staging"
RUNTIME_DIR = DIST_DIR / "runtime"
PYTHON_BASE = Path(r"C:\Python314")
VENV_PACKAGES = BASE_DIR / ".venv" / "Lib" / "site-packages"


def copy_file(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree_filtered(src: Path, dst: Path, ignore_patterns=None):
    if ignore_patterns is None:
        ignore_patterns = shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", "*.git*", "*.tmp", "*.log",
            "voters.db-shm", "voters.db-wal"
        )
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore_patterns)


def build_staging():
    print("=" * 60)
    print("🚀 मतदाता सेवा — इंस्टॉलर स्टेजिंग डायरेक्टरी तैयार की जा रही है...")
    print("=" * 60)

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Build Portable Python Runtime
    print("\n[1/6] 🐍 पोर्टेबल पायथन रनटाइम तैयार किया जा रहा है...")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    # Copy Python executables and core DLLs
    for fn in ["python.exe", "pythonw.exe", "python3.dll", "python314.dll", "vcruntime140.dll", "vcruntime140_1.dll"]:
        src_f = PYTHON_BASE / fn
        if src_f.exists():
            copy_file(src_f, RUNTIME_DIR / fn)

    # Copy DLLs
    if (PYTHON_BASE / "DLLs").exists():
        copy_tree_filtered(PYTHON_BASE / "DLLs", RUNTIME_DIR / "DLLs")

    # Copy standard library (excluding large unused tests)
    print("   • Standard Library कॉपी हो रही है...")
    std_lib_src = PYTHON_BASE / "Lib"
    std_lib_dst = RUNTIME_DIR / "Lib"
    std_ignore = shutil.ignore_patterns("test", "tests", "idlelib", "turtledemo", "__pycache__", "*.pyc")
    copy_tree_filtered(std_lib_src, std_lib_dst, ignore_patterns=std_ignore)

    # Copy site-packages from .venv
    print("   • Installed Site-Packages कॉपी हो रहे हैं...")
    sp_dst = RUNTIME_DIR / "Lib" / "site-packages"
    sp_ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.dist-info", "*.egg-info")
    copy_tree_filtered(VENV_PACKAGES, sp_dst, ignore_patterns=sp_ignore)

    # Test portable python runtime
    test_cmd = [str(RUNTIME_DIR / "python.exe"), "-c", "import fastapi, fitz, PIL, pytesseract; print('PORTABLE PYTHON OK')"]
    import subprocess
    res = subprocess.run(test_cmd, capture_output=True, text=True)
    if "PORTABLE PYTHON OK" in res.stdout:
        print("   ✅ पोर्टेबल पायथन रनटाइम 100% सत्यापित!")
    else:
        print(f"   ⚠️ रनटाइम टेस्ट वार्निंग: {res.stderr}")

    # 2. Copy Backend
    print("\n[2/6] ⚙️ बैकएंड मॉड्यूल कॉपी किए जा रहे हैं...")
    copy_tree_filtered(BASE_DIR / "backend", DIST_DIR / "backend")

    # 3. Copy Frontend
    print("\n[3/6] 🎨 फ्रंटएंड वेब इंटरफेस कॉपी किया जा रहा है...")
    copy_tree_filtered(BASE_DIR / "frontend", DIST_DIR / "frontend")

    # 4. Copy Tools & Models
    print("\n[4/6] 🧠 OCR इंजन (Tesseract) और AI मॉडल्स कॉपी किए जा रहे हैं...")
    # Tesseract
    tess_src = BASE_DIR / "tools" / "tesseract"
    if tess_src.exists():
        tess_dst = DIST_DIR / "tools" / "tesseract"
        # Ignore non-essential uninstaller/classifier tools to save space
        tess_ignore = shutil.ignore_patterns("*.html", "*.chm", "$PLUGINSDIR", "tesseract-uninstall.exe")
        copy_tree_filtered(tess_src, tess_dst, ignore_patterns=tess_ignore)
    
    # OCR ONNX Models
    models_src = BASE_DIR / "models"
    if models_src.exists():
        copy_tree_filtered(models_src, DIST_DIR / "models")

    # Cloudflared
    cf_src = BASE_DIR / "cloudflared.exe"
    if cf_src.exists():
        copy_file(cf_src, DIST_DIR / "cloudflared.exe")

    # 5. Copy Scripts & Icons
    print("\n[5/6] 📜 स्क्रिप्ट्स व ऐप आइकन कॉपी किए जा रहे हैं...")
    copy_tree_filtered(BASE_DIR / "scripts", DIST_DIR / "scripts")

    # App icon
    icon_src = BASE_DIR / "outputs" / "app_icon.ico"
    if icon_src.exists():
        copy_file(icon_src, DIST_DIR / "outputs" / "app_icon.ico")
        copy_file(icon_src, DIST_DIR / "app_icon.ico")

    # Ensure empty data/ uploads/ outputs/ directories exist
    (DIST_DIR / "data").mkdir(exist_ok=True)
    (DIST_DIR / "uploads").mkdir(exist_ok=True)
    (DIST_DIR / "outputs").mkdir(exist_ok=True)
    (DIST_DIR / "samples").mkdir(exist_ok=True)

    # 6. Create 1-Click Launchers for Target PC
    print("\n[6/6] ⚡ वन-क्लिक लॉन्चर बैच स्क्रिप्ट्स तैयार की जा रही हैं...")
    
    # start_app.bat
    start_bat_content = """@echo off
cd /d "%~dp0"
if exist "start_silent.vbs" (
    start "" wscript.exe "start_silent.vbs"
    exit
)
start "" "runtime\\pythonw.exe" "scripts\\app_browser.py"
exit
"""
    with open(DIST_DIR / "start_app.bat", "w", encoding="utf-8") as f:
        f.write(start_bat_content)

    # start_silent.vbs (completely hidden terminal)
    silent_vbs_content = 'Set WshShell = CreateObject("WScript.Shell")\n' + \
        'strPath = WshShell.CurrentDirectory\n' + \
        'WshShell.Run chr(34) & strPath & "\\runtime\\pythonw.exe" & chr(34) & " " & chr(34) & strPath & "\\scripts\\app_browser.py" & chr(34), 0, False\n' + \
        'Set WshShell = Nothing\n'
    with open(DIST_DIR / "start_silent.vbs", "w", encoding="utf-8") as f:
        f.write(silent_vbs_content)

    print("\n" + "=" * 60)
    print("✅ स्टेजिंग डायरेक्टरी पूरी तरह तैयार: dist_staging/")
    print("=" * 60)


if __name__ == "__main__":
    build_staging()
