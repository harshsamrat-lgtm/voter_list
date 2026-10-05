import os
import uuid
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
SAMPLE_DIR = BASE_DIR / "samples"
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH = DATA_DIR / "voters.db"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Tesseract executable candidate paths for Windows
TESSERACT_CANDIDATE_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    "tesseract"
]

# App configuration
APP_NAME = "मतदाता सेवा मास्टर"
APP_VERSION = "1.0.1"
HOST = "127.0.0.1"
PORT = 8000

import secrets

# Security & Admin token
# Priority:
# 1. ADMIN_TOKEN environment variable (if explicitly set)
# 2. Persisted unique token in data/.admin_token (keeps token stable across desktop restarts)
# 3. Cryptographically secure random token generated on first run
_token_file = DATA_DIR / ".admin_token"
_env_token = os.environ.get("ADMIN_TOKEN", "").strip()

if _env_token:
    ADMIN_TOKEN = _env_token
elif _token_file.exists():
    try:
        ADMIN_TOKEN = _token_file.read_text(encoding="utf-8").strip()
    except Exception:
        ADMIN_TOKEN = ""
    if not ADMIN_TOKEN:
        ADMIN_TOKEN = f"admin-{secrets.token_hex(16)}"
        try:
            _token_file.write_text(ADMIN_TOKEN, encoding="utf-8")
        except Exception:
            pass
else:
    ADMIN_TOKEN = f"admin-{secrets.token_hex(16)}"
    try:
        _token_file.write_text(ADMIN_TOKEN, encoding="utf-8")
    except Exception:
        pass

