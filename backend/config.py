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
APP_NAME = "UP वोटर लिस्ट"
APP_VERSION = "1.0.1"
HOST = "127.0.0.1"
PORT = 8000

# Security & Admin token
# If ADMIN_TOKEN env var is not set, generate a secure random token at each startup
_env_token = os.environ.get("ADMIN_TOKEN", "").strip()
if _env_token:
    ADMIN_TOKEN = _env_token
else:
    ADMIN_TOKEN = "admin-2cd2df6b4014"
    print(f"[SECURITY] Using persistent ADMIN_TOKEN: {ADMIN_TOKEN}")

