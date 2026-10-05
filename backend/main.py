"""
FastAPI Server for UP Voter List PDF to Excel AI Converter.
Provides RESTful APIs for PDF inspection, extraction, live preview,
inline editing, and Excel export. Serves the web frontend.
"""

import os
import sys
import uuid
import shutil
import asyncio
import re
import urllib.parse
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import concurrent.futures
import subprocess

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Query, Request, Depends
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import BASE_DIR, UPLOAD_DIR, OUTPUT_DIR, SAMPLE_DIR, FRONTEND_DIR, DATA_DIR, DB_PATH, APP_NAME, APP_VERSION, ADMIN_TOKEN
from .models.voter import VoterRecord, JobStatus, VoterStats
from .modules.pdf_detector import PDFDetector
from .modules.digital_extractor import DigitalVoterExtractor
from .modules.ulb_extractor import ULBExtractor
from .modules.ocr_extractor import OCRExtractor, HEADER_FRACTION, FOOTER_FRACTION, LEFT_MARGIN_FRACTION, RIGHT_MARGIN_FRACTION, NUM_COLUMNS
from .modules.excel_builder import ExcelBuilder
from .modules.sample_generator import SamplePDFGenerator
from .modules.validator import validate_voter_record, clean_epic_no, is_valid_epic_format, is_genuine_voter, clean_hindi_text
from .modules.database import VoterDatabase
from .modules.auth_manager import AuthManager
from .modules.local_caste_ai import LocalCasteAIEngine
from .modules.db_manager import DatabaseManager
from .modules.error_corrector import DualPassErrorCorrector, LocalScanQualityAI
from .modules.tunnel_service import TunnelService
from .modules.property_survey_sync import PropertySurveySync
from .modules.license_guard import LicenseGuard
from .modules.panchayat_service import PanchayatService

# Initialize local SQLite master database and authentication system
DatabaseManager._ensure_registry()
VoterDatabase.init_db()
AuthManager.init_auth_tables()


def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    """Retrieves authenticated user from session token if provided."""
    token = None
    auth_header = request.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = (
            request.headers.get("x-auth-token")
            or request.query_params.get("auth_token")
            or request.cookies.get("voter_auth_token")
        )
    if token:
        return AuthManager.validate_session(token)
    return None


def get_current_user_required(request: Request) -> Dict[str, Any]:
    """Requires an authenticated user session."""
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="कृपया पहले लॉगिन करें। (Authentication Required)"
        )
    return user


def is_local_ip(ip: str) -> bool:
    """Checks if an IP or hostname is local to this machine or private LAN."""
    if not ip:
        return True
    ip = ip.strip().lower()
    if ip in ("127.0.0.1", "localhost", "::1", "testclient", "::ffff:127.0.0.1"):
        return True
    if ip.startswith("127.") or ip.endswith(":127.0.0.1"):
        return True
    # Private network ranges: 192.168.x.x, 10.x.x.x, 172.16-31.x.x
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        parts = ip.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def is_public_request(request: Request) -> bool:
    """
    Detects if the incoming request originates from the public internet
    (e.g., forwarded by Cloudflare Tunnel, reverse proxy, or external IP).
    """
    # Cloudflare / Tunnel headers indicate public internet tunnel access
    if request.headers.get("cf-connecting-ip") or request.headers.get("cf-ray"):
        return True
    
    # Check X-Forwarded-For
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",")]
        for ip in parts:
            if not is_local_ip(ip):
                return True
                
    # Check client host
    client_host = request.client.host if request.client else ""
    if client_host and not is_local_ip(client_host):
        return True
        
    return False


def verify_admin_access(request: Request):
    """
    Security Barrier:
    Protects administrative operations (database deletion, user management, privacy settings, backups, etc.).
    Grants access if:
    1. Request contains a valid session token of an active Admin.
    2. Request contains a valid X-Admin-Token.
    3. Request is from local machine (127.0.0.1 / localhost) AND user is not logged in as non-admin.
    """
    # 1. Check logged-in user session
    user = get_current_user_optional(request)
    if user:
        if user.get("role") == "admin":
            return user
        raise HTTPException(
            status_code=403,
            detail="पहुँच अस्वीकृत (Access Denied): यह प्रशासनिक सुविधा केवल मुख्य एडमिन के लिए सुरक्षित है।"
        )

    # 2. Check admin token
    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    if token and token == ADMIN_TOKEN:
        return {"username": "token_admin", "role": "admin"}

    # 3. Allow local machine access
    if not is_public_request(request):
        return {"username": "local_admin", "role": "admin"}

    raise HTTPException(
        status_code=403,
        detail="पहुँच अस्वीकृत (Access Denied): यह प्रशासनिक सुविधा केवल एडमिन के लिए सुरक्षित है।"
    )


def verify_superadmin_only_access(request: Request):
    """
    Security Barrier:
    Protects Nagar Panchayat Geographic Street & House Survey Audit Matching & Git OTA Publisher.
    Accessible to Super-Admin ('harshsamrat'), admin roles, and local administrators.
    """
    user = get_current_user_optional(request)
    client_host = request.client.host if request.client else ""
    is_local = client_host in ("127.0.0.1", "localhost", "::1", "testclient")

    if not user:
        if is_local:
            return {"username": "harshsamrat", "role": "admin", "is_superadmin": True}
        raise HTTPException(
            status_code=401,
            detail="सत्यापन आवश्यक है। कृपया लॉगिन करें।"
        )
    username = (user.get("username") or "").strip().lower()
    is_admin = username == "harshsamrat" or bool(user.get("is_superadmin")) or user.get("role") in ("superadmin", "admin")
    if not is_admin and not is_local:
        raise HTTPException(
            status_code=403,
            detail="पहुँच अस्वीकृत (Access Denied): '🚀 नया अपडेट पब्लिश करें' केवल एडमिनिस्ट्रेटर हेतु आरक्षित है।"
        )
    return user


def verify_operator_or_admin_access(request: Request):
    """
    Security Barrier:
    Allows both Admins and Data Operators to perform:
    - PDF Upload & OCR Conversion
    - Record editing during upload (add, edit, toggle deleted, rescan epic)
    - Batch metadata update (Part No, Assembly, Polling Station)
    - Saving upload batch into database
    - Bulk part updates in database
    """
    user = get_current_user_optional(request)
    if user:
        if user.get("role") in ("admin", "operator"):
            return user
        raise HTTPException(
            status_code=403,
            detail="पहुँच अस्वीकृत (Access Denied): यह कार्य केवल ऑपरेटर अथवा एडमिन के लिए अनुमत है।"
        )

    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    if token and token == ADMIN_TOKEN:
        return {"username": "token_admin", "role": "admin"}

    if not is_public_request(request):
        return {"username": "local_admin", "role": "admin"}

    raise HTTPException(
        status_code=403,
        detail="पहुँच अस्वीकृत (Access Denied): यह कार्य केवल ऑपरेटर अथवा एडमिन के लिए अनुमत है।"
    )


def is_admin_or_superadmin_request(request: Request) -> bool:
    """Checks if current request is from an admin or root super admin (harshsamrat)."""
    user = get_current_user_optional(request)
    if user:
        uname = (user.get("username") or "").strip().lower()
        role = (user.get("role") or "").strip().lower()
        if uname == "harshsamrat" or role in ("admin", "superadmin") or user.get("is_superadmin"):
            return True
        return False
    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    if token and token == ADMIN_TOKEN:
        return True
    return False


def is_operator_request(request: Request) -> bool:
    """
    Checks if current request is from an operator or non-admin.
    Religion and community details are strictly visible ONLY to Admin and Super Admin.
    """
    return not is_admin_or_superadmin_request(request)


def redact_caste_from_record(rec_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Redacts caste, community, and surname identification fields completely for operator users."""
    if not isinstance(rec_dict, dict):
        return rec_dict
    rec_dict["caste_key"] = None
    rec_dict["caste_source"] = None
    rec_dict["caste_reason"] = None
    rec_dict["voter_surname"] = None
    rec_dict["rel_surname"] = None
    rec_dict["is_muslim"] = 0
    rec_dict["muslim_reason"] = None
    return rec_dict


def verify_user_access(request: Request):
    """
    Security Barrier:
    Protects voter database search, statistics, recompute, and export.
    Requires an authenticated user (either regular user or admin) or local machine.
    Blocks unauthenticated public requests.
    """
    user = get_current_user_optional(request)
    if user:
        return user

    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    if token and token == ADMIN_TOKEN:
        return {"username": "token_admin", "role": "admin"}

    # Allow local machine access
    if not is_public_request(request):
        return {"username": "local_user", "role": "user"}

    raise HTTPException(
        status_code=401,
        detail="पहुँच अस्वीकृत: कृपया पहले लॉगिन करें। (Authentication Required)"
    )



app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="मतदाता सेवा मास्टर — मतदाता सूची PDF से Excel में बदलने वाला कनवर्टर"
)

# Enable CORS for local and tunnel accessibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_origin_regex=r"https://.*\.trycloudflare\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path.lower()
    if path.endswith((".js", ".css", ".html")) or path in ("/", "/admin", "/search", "/caste-analytics"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response



# =============================================================================
# SOFTWARE LICENSE & SECURITY KEY ENFORCEMENT MIDDLEWARE
# =============================================================================

@app.middleware("http")
async def license_enforcement_middleware(request: Request, call_next):
    path = request.url.path

    # Whitelist open endpoints: activation, auth, status, health, static files
    exempt_prefixes = (
        "/activate",
        "/api/license",
        "/api/auth",
        "/api/health",
        "/css",
        "/js",
        "/outputs",
        "/favicon.ico",
        "/docs",
        "/openapi.json"
    )
    if any(path.startswith(p) for p in exempt_prefixes):
        return await call_next(request)

    # Check license activation status
    lic_status = LicenseGuard.get_license_status()
    if not lic_status.get("is_activated"):
        # For API requests: return 403 Forbidden with details
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "सॉफ्टवेयर सक्रिय नहीं है। कृपया पहले लाइसेंस की दर्ज करें। (License Activation Required)",
                    "hwid": lic_status.get("hwid"),
                    "status": lic_status.get("status"),
                    "message": lic_status.get("message")
                }
            )
        # For browser navigation: redirect to /activate
        return RedirectResponse(url="/activate", status_code=303)

    return await call_next(request)


class LicenseActivationRequest(BaseModel):
    key: str


class SetupAdminRequest(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = ""
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    device_fp: Optional[str] = None


@app.get("/api/license/status")
def api_license_status():
    """Returns current machine HWID, activation status, and whether admin setup is needed."""
    st = LicenseGuard.get_license_status()
    has_local = AuthManager.has_local_admin()
    st["has_local_admin"] = has_local
    st["needs_admin_setup"] = bool(st.get("is_activated") and not has_local)
    return st


@app.post("/api/license/activate")
def api_license_activate(req: LicenseActivationRequest):
    """Activates software license on this machine using the provided key."""
    ok, msg = LicenseGuard.activate_license(req.key)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    has_local = AuthManager.has_local_admin()
    return {
        "status": "success",
        "message": msg,
        "needs_admin_setup": not has_local
    }


@app.post("/api/license/setup-admin")
def api_license_setup_admin(req: SetupAdminRequest, request: Request):
    """
    Sets up the local admin account after software activation.
    Guarantees that username != 'harshsamrat'.
    Logs in the new admin immediately and returns session.
    """
    if not LicenseGuard.is_activated():
        raise HTTPException(status_code=403, detail="सॉफ्टवेयर सक्रिय (Activated) नहीं है। पहले वैध सिक्योरिटी KEY दर्ज करें।")

    ip = request.client.host if request.client else ""
    ok, session_data, msg = AuthManager.setup_initial_admin(
        username=req.username,
        password=req.password,
        full_name=req.full_name or "",
        device_id=req.device_id,
        device_name=req.device_name,
        device_fp=req.device_fp,
        ip_address=ip
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    log_admin_action("setup_initial_admin", None, f"Local admin created: {req.username}", username=req.username)
    return {"status": "success", "message": msg, **session_data}


# =============================================================================
# AUTO-UPDATE & OTA HOT-PATCH ENDPOINTS
# =============================================================================

from .modules.updater_guard import UpdaterGuard


class ApplyUpdateRequest(BaseModel):
    download_url: str
    expected_sha256: Optional[str] = None
    target_version: Optional[str] = None


@app.get("/api/system/version")
def api_system_version():
    """Returns local software version, release date, and manifest information."""
    return UpdaterGuard.get_local_version_info()


@app.get("/api/system/check-update")
def api_system_check_update(custom_url: Optional[str] = Query(None)):
    """Queries the remote version manifest and returns update availability status."""
    return UpdaterGuard.check_for_updates(custom_url=custom_url)


@app.post("/api/system/apply-update", dependencies=[Depends(verify_admin_access)])
def api_system_apply_update(req: ApplyUpdateRequest):
    """
    Downloads and applies an OTA hot-patch.
    Guaranteed data isolation: Never touches data/voters.db or license keys.
    """
    ok, msg = UpdaterGuard.apply_patch_update(
        download_url=req.download_url,
        expected_sha256=req.expected_sha256,
        target_version=req.target_version
    )
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "success", "message": msg, "target_version": req.target_version}


# =============================================================================
# SUPER-ADMIN IN-APP GIT & OTA RELEASE PUBLISHER (HARSHSAMRAT ONLY)
# =============================================================================

from .modules.git_publisher import GitPublisher


class PublishOtaReleaseRequest(BaseModel):
    bump_type: Optional[str] = "patch"
    custom_version: Optional[str] = None
    notes: Optional[str] = ""
    changelog: Optional[List[str]] = None
    github_pat_token: Optional[str] = None
    remember_token: Optional[bool] = False


@app.get("/api/admin/git-publish-config", dependencies=[Depends(verify_superadmin_only_access)])
def api_get_git_publish_config():
    """
    Returns repository metadata and token status for Super-Admin ('harshsamrat').
    Strictly blocked for regular admins and operators.
    """
    return GitPublisher.get_git_config()


@app.post("/api/admin/publish-ota-release", dependencies=[Depends(verify_superadmin_only_access)])
def api_publish_ota_release(req: PublishOtaReleaseRequest, request: Request):
    """
    One-Click In-App OTA Publisher:
    Packages frontend/backend, computes SHA-256, updates version.json,
    commits, tags, and pushes directly to https://github.com/harshsamrat-lgtm/voter_list
    and uploads update-patch.zip to GitHub Releases.
    Accessible strictly to root Super Admin ('harshsamrat').
    """
    result = GitPublisher.publish_ota_release(
        bump_type=req.bump_type or "patch",
        custom_version=req.custom_version,
        notes=req.notes or "",
        changelog=req.changelog,
        github_pat_token=req.github_pat_token,
        remember_token=bool(req.remember_token)
    )
    log_admin_action("publish_ota_release", None, f"Published OTA release v{result.get('version')} to GitHub", username="harshsamrat")
    return result


@app.get("/activate")
def serve_activate_page():
    """Serves the license activation HTML UI."""
    activate_file = FRONTEND_DIR / "activate.html"
    if activate_file.exists():
        return FileResponse(str(activate_file))
    return Response(content="<h1>सॉफ्टवेयर एक्टिवेशन आवश्यक है</h1>", media_type="text/html")


# In-memory storage for active jobs
JOBS_DB: Dict[str, JobStatus] = {}
JOB_PDF_MAP: Dict[str, str] = {}


def reconcile_voter_serials(voters: List[VoterRecord], max_ceiling: Optional[int] = None) -> List[VoterRecord]:
    """
    Ensures every voter card in an extracted roll has a unique, continuous sequential serial number.
    Filters out non-genuine records, applies Triple-Match (EPIC + Name + Father Name) deduplication,
    and guarantees strictly continuous 1..N sequence without gaps or duplicates.
    """
    if not voters:
        return []

    # 1. Filter out non-genuine / empty records
    valid_voters = [v for v in voters if is_genuine_voter(v)]

    # 2. Triple-Key Deduplication Guard: केवल EPIC + Name + Father Name तीनों मैच हों तभी डुप्लीकेट
    seen_triples = {}
    deduped_voters: List[VoterRecord] = []
    for v in valid_voters:
        epic = clean_epic_no(v.epic_no)
        c_name = clean_hindi_text(v.name)
        c_rel = clean_hindi_text(v.relation_name)

        is_triple_valid = bool(epic and len(epic) >= 5 and c_name and len(c_name) >= 2 and c_rel and len(c_rel) >= 2)
        if is_triple_valid:
            trip_key = (epic, c_name, c_rel)
            if trip_key in seen_triples:
                # तीनों मैच हुए -> वास्तविक डुप्लीकेट
                continue
            else:
                seen_triples[trip_key] = len(deduped_voters)
                deduped_voters.append(v)
        else:
            deduped_voters.append(v)
    valid_voters = deduped_voters

    # 3. If max_ceiling is provided and valid_voters exceeds it, cap to max_ceiling
    if max_ceiling and max_ceiling > 0 and len(valid_voters) > max_ceiling:
        valid_voters = valid_voters[:max_ceiling]

    # 4. Strictly continuous 1..N serial integrity:
    # Preserve authentic printed serial numbers from the official roll!
    last_s = 0
    seen_serials = set()
    for idx, v in enumerate(valid_voters, start=1):
        if not v.serial_no or v.serial_no <= 0 or v.serial_no in seen_serials or v.serial_no < last_s:
            v.serial_no = max(last_s + 1, idx)
        seen_serials.add(v.serial_no)
        last_s = v.serial_no

    return valid_voters


def format_duration_hindi(seconds: Optional[float]) -> str:
    """Formats seconds into user-friendly Hindi representation."""
    if seconds is None or seconds < 0:
        return "0 सेकंड"
    seconds = round(seconds, 1)
    if seconds < 60:
        if seconds == int(seconds):
            return f"{int(seconds)} सेकंड"
        return f"{seconds:.1f} सेकंड"
    minutes = int(seconds // 60)
    rem_seconds = int(round(seconds % 60))
    if rem_seconds == 60:
        minutes += 1
        rem_seconds = 0
    if rem_seconds > 0:
        return f"{minutes} मिनट {rem_seconds} सेकंड"
    return f"{minutes} मिनट"


def get_optimal_scanning_workers(pages_count: int) -> int:
    """
    Dynamically detects total CPU hardware threads/cores on this computer.
    Allocates the maximum safe number of workers while preserving at least 1 core
    for the Windows operating system and other desktop software, ensuring zero system stutter:
    - 4 Cores/Threads -> 3 Workers (leaves 1 full core for Windows UI/apps)
    - 6 Cores/Threads -> 5 Workers (leaves 1 core for Windows)
    - 8 Cores/Threads -> 7 Workers (leaves 1 core for Windows)
    - 12 Cores/Threads -> 10 Workers (leaves 2 cores for Windows)
    - 16 Cores/Threads -> 14 Workers (leaves 2 cores for Windows)
    - 2 Cores/Threads -> 1 Worker (leaves 1 core for Windows)
    - 1 Core -> 1 Worker
    """
    total_cpus = os.cpu_count() or 4
    if total_cpus <= 2:
        max_workers = 1
    elif total_cpus <= 4:
        max_workers = 3
    elif total_cpus <= 8:
        max_workers = total_cpus - 1
    elif total_cpus <= 16:
        max_workers = max(1, total_cpus - 2)
    else:
        max_workers = max(1, total_cpus - 3)

    if pages_count > 0:
        return max(1, min(max_workers, pages_count))
    return max(1, max_workers)


def set_safe_process_priority():
    """
    Sets process priority to BELOW_NORMAL on Windows during heavy scanning.
    This guarantees that Windows OS UI, browser, and other user applications
    remain 100% fluid and responsive without freezing, while the scanner utilizes
    all assigned CPU cores at maximum throughput.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            # BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        except Exception:
            pass


def run_extraction_job(job_id: str, start_page: Optional[int] = None, end_page: Optional[int] = None, force_ocr: bool = False):
    """Background task executing multi-page voter extraction."""
    job = JOBS_DB.get(job_id)
    pdf_path = JOB_PDF_MAP.get(job_id)
    
    if not job or not pdf_path or not os.path.exists(pdf_path):
        if job:
            job.status = "error"
            job.error_message = "PDF फ़ाइल नहीं मिली।"
        return
        
    try:
        t_job_start = time.time()
        job.started_at_timestamp = t_job_start
        job.completed_at_timestamp = None
        job.elapsed_seconds = 0.0
        job.time_taken_formatted = None
        job.speed_seconds_per_page = None
        job.estimated_remaining_seconds = None
        
        job.status = "processing"
        inspection = PDFDetector.inspect_pdf(pdf_path)
        total_pdf_pages = inspection["total_pages"]
        is_scanned = inspection["requires_ocr"]
        is_ulb = inspection.get("is_ulb", False) or ULBExtractor.is_ulb_pdf(pdf_path)

        if is_ulb:
            has_cover = False
            actual_start = max(1, start_page if start_page else 1)
            actual_end = min(total_pdf_pages, end_page if end_page else total_pdf_pages)
            initial_serial = 1
            serial_counter = 1
            use_ocr = False
            ocr_available = False

            import pymupdf as fitz
            doc = fitz.open(pdf_path)
            cover_meta = ULBExtractor.extract_header_metadata(doc)
            doc.close()

            if not job.assembly_name and cover_meta.get("assembly"):
                job.assembly_name = cover_meta["assembly"]
            if not job.part_number and cover_meta.get("part_no"):
                job.part_number = cover_meta["part_no"]
            if not job.polling_station and cover_meta.get("polling_station"):
                job.polling_station = cover_meta["polling_station"]

            official_final_serial = None
            official_total_voters = None
        else:
            has_cover = OCRExtractor.detect_pdf_has_cover_pages(pdf_path) if total_pdf_pages > 3 else False
            
            # UP Electoral Roll Rule:
            # If has_cover is True: Page 1 = Cover/Title, Page 2 = Polling Station/Map, Page N = Modification Summary.
            # These contain administrative information, NOT voter cards.
            # If total pages > 3 and user hasn't overridden start/end, process pages 3 to (total_pages - 1).
            if has_cover and total_pdf_pages > 3:
                actual_start = start_page if (start_page is not None and start_page > 0) else 3
                actual_end = end_page if (end_page is not None and end_page > 0) else (total_pdf_pages - 1)
            else:
                actual_start = max(1, start_page if start_page else 1)
                actual_end = min(total_pdf_pages, end_page if end_page else total_pdf_pages)
                
            actual_start = max(1, min(actual_start, total_pdf_pages))
            actual_end = max(actual_start, min(actual_end, total_pdf_pages))
            
            # Calculate official initial serial based on actual_start
            if has_cover:
                initial_serial = ((actual_start - 3) * 30 + 1) if actual_start >= 3 else 1
            else:
                initial_serial = (actual_start - 1) * 30 + 1
            serial_counter = max(1, initial_serial)
            
            # Smart routing: determine extraction strategy upfront
            use_ocr = force_ocr or is_scanned
            ocr_available = OCRExtractor.is_ocr_available()
            
            # Fast-path: Reuse pre-extracted cover metadata if already populated from upload inspection or bulk scan
            if job.assembly_name and job.part_number:
                cover_meta = {
                    "assembly": job.assembly_name,
                    "part_no": job.part_number,
                    "polling_station": job.polling_station or ""
                }
                official_final_serial = None
                official_total_voters = None
            else:
                cover_meta = OCRExtractor.extract_cover_metadata(pdf_path)
                if not job.assembly_name and cover_meta.get("assembly"):
                    job.assembly_name = cover_meta["assembly"]
                if not job.part_number and cover_meta.get("part_no"):
                    job.part_number = cover_meta["part_no"]
                if not job.polling_station and cover_meta.get("polling_station"):
                    job.polling_station = cover_meta["polling_station"]

                official_final_serial = cover_meta.get("official_final_serial")
                official_total_voters = cover_meta.get("official_total_voters")

        actual_start = max(1, min(actual_start, total_pdf_pages))
        actual_end = max(actual_start, min(actual_end, total_pdf_pages))
        
        pages_to_process = list(range(actual_start - 1, actual_end))
        total_steps = len(pages_to_process)
        job.total_pages = total_steps
        
        all_voters: List[VoterRecord] = []
        
        print(f"[JOB {job_id[:8]}] Starting extraction: {total_steps} pages (start={actual_start}, end={actual_end}, initial_serial={serial_counter}), "
              f"format={'UP_NAGAR_NIKAY' if is_ulb else 'ECI_ASSEMBLY'}, strategy={'OCR' if use_ocr else 'Digital'}, "
              f"scanned={is_scanned}, ocr_available={ocr_available}, "
              f"part={job.part_number}")
        
        page_results: Dict[int, Any] = {}
        completed_pages = 0

        # Ensure Windows and foreground software stay fluid without lag
        set_safe_process_priority()

        # Dynamic core/thread allocation: uses max cores while preserving 1+ for Windows
        NUM_PAGE_WORKERS = get_optimal_scanning_workers(len(pages_to_process))
        print(f"[JOB {job_id[:8]}] CPU Core Allocation: {os.cpu_count() or 4} total hardware threads detected -> "
              f"Using {NUM_PAGE_WORKERS} parallel scanning workers (preserving system responsiveness for Windows & other software)")

        def process_single_page_worker(p_idx: int):
            t_p_start = time.time()
            page_no = p_idx + 1
            est_page_serial = max(1, initial_serial + (p_idx - (actual_start - 1)) * 30)

            p_res = None
            if is_ulb:
                if use_ocr or is_scanned:
                    from backend.modules.ulb_scanner_ai import ULBScannedPageExtractor
                    p_res = ULBScannedPageExtractor.process_scanned_page(
                        pdf_path=pdf_path,
                        page_index=p_idx,
                        metadata=cover_meta,
                        expected_page_start_serial=est_page_serial
                    )
                else:
                    p_res = ULBExtractor.process_page(pdf_path, p_idx, metadata=cover_meta)
                    if (not p_res.voters or len(p_res.voters) == 0) and ocr_available:
                        from backend.modules.ulb_scanner_ai import ULBScannedPageExtractor
                        p_res = ULBScannedPageExtractor.process_scanned_page(
                            pdf_path=pdf_path,
                            page_index=p_idx,
                            metadata=cover_meta,
                            expected_page_start_serial=est_page_serial
                        )
            elif use_ocr and ocr_available:
                p_res = OCRExtractor.process_page_ocr(
                    pdf_path, p_idx, current_serial=est_page_serial,
                    total_pages=total_pdf_pages, has_cover=has_cover,
                    parallel_columns=(NUM_PAGE_WORKERS == 1)
                )
            else:
                p_res = DigitalVoterExtractor.process_page(pdf_path, p_idx, current_serial=est_page_serial)
                if (not p_res.voters or len(p_res.voters) == 0) and ocr_available:
                    p_res = OCRExtractor.process_page_ocr(
                        pdf_path, p_idx, current_serial=est_page_serial,
                        total_pages=total_pdf_pages, has_cover=has_cover,
                        parallel_columns=(NUM_PAGE_WORKERS == 1)
                    )

            p_dur = time.time() - t_p_start
            return p_idx, p_res, p_dur

        # Execute 3 pages simultaneously across 3 CPU cores
        with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_PAGE_WORKERS) as page_executor:
            future_to_pidx = {
                page_executor.submit(process_single_page_worker, p_idx): p_idx
                for p_idx in pages_to_process
            }

            for future in concurrent.futures.as_completed(future_to_pidx):
                p_idx, p_res, p_dur = future.result()
                page_results[p_idx] = p_res
                completed_pages += 1

                job.processed_pages = completed_pages
                job.progress_percent = int((completed_pages / total_steps) * 100) if total_steps > 0 else 100

                cur_now = time.time()
                cur_elapsed = cur_now - t_job_start
                job.elapsed_seconds = round(cur_elapsed, 1)
                if completed_pages > 0:
                    speed = cur_elapsed / completed_pages
                    job.speed_seconds_per_page = round(speed, 2)
                    rem_pages = max(0, total_steps - completed_pages)
                    job.estimated_remaining_seconds = int(round(rem_pages * speed))

                cur_voters = sum(len(r.voters) for r in page_results.values() if r and r.voters)
                job.total_voters_extracted = cur_voters

                v_cnt = len(p_res.voters) if p_res and p_res.voters else 0
                print(f"[JOB {job_id[:8]}] (3-Core Parallel) Page {p_idx+1}: {v_cnt} voters in {p_dur:.1f}s "
                      f"[Progress: {completed_pages}/{total_steps}, Live Extracted: {cur_voters}]")

        # Master Serial Sequencer Buffer: Assemble voters strictly in page order
        all_voters = []

        if is_ulb:
            for p_idx in sorted(pages_to_process):
                res = page_results.get(p_idx)
                if not res or not res.voters:
                    continue
                for v in res.voters:
                    if is_genuine_voter(v):
                        if not v.polling_station and job.polling_station:
                            v.polling_station = job.polling_station
                        if not v.part_no and job.part_number:
                            v.part_no = job.part_number
                        if not v.assembly and job.assembly_name:
                            v.assembly = job.assembly_name
                        all_voters.append(v)
        else:
            # Dynamic Continuity Chain Rule: StartSerial(P_K) = EndSerial(P_{K-1}) + 1
            running_serial = max(1, initial_serial)

            for p_idx in sorted(pages_to_process):
                res = page_results.get(p_idx)
                if not res or not res.voters:
                    continue

                page_genuine_voters = [v for v in res.voters if is_genuine_voter(v)]
                if not page_genuine_voters:
                    continue

                # Assign strictly continuous 1..N serials within this page
                for i, v in enumerate(page_genuine_voters):
                    assigned_s = running_serial + i
                    v.serial_no = assigned_s
                    if not v.polling_station and job.polling_station:
                        v.polling_station = job.polling_station
                    if not v.part_no and job.part_number:
                        v.part_no = job.part_number
                    if not v.assembly and job.assembly_name:
                        v.assembly = job.assembly_name
                    all_voters.append(v)

                # Advance running_serial by exactly the number of genuine voters on this page
                running_serial += len(page_genuine_voters)

        job.total_voters_extracted = len(all_voters)
        job.records = all_voters
        
        # Ensure official cover page metadata (Assembly, Part, Polling Station) has authoritative precedence
        for v in all_voters:
            if job.polling_station:
                v.polling_station = job.polling_station
            if job.part_number:
                v.part_no = job.part_number
            if job.assembly_name:
                v.assembly = job.assembly_name
                
        # Step: Local AI Dual-Pass Error Correction (दोहरी स्कैनिंग व स्वतः त्रुटि सुधार)
        dual_summary = DualPassErrorCorrector.process_records_dual_pass(all_voters)
        all_voters = [v for v in dual_summary["records"] if is_genuine_voter(v)]
        
        # Guarantee strict 1..N continuous unique serial numbers and enforce official ceiling (ECI Assembly rolls only)
        if not is_ulb:
            ceiling = official_final_serial or official_total_voters
            all_voters = reconcile_voter_serials(all_voters, max_ceiling=ceiling)
        
        # Apply Local AI Caste Self-Determination Engine at scan time
        all_voters = LocalCasteAIEngine.infer_caste_for_records(all_voters)
        job.records = all_voters
        job.total_voters_extracted = len(all_voters)
        job.perfect_first_pass = dual_summary.get("perfect_first_pass", len(all_voters))
        job.errors_corrected = dual_summary.get("errors_corrected", 0)
        job.corrections_detail = dual_summary.get("corrections_detail", [])

        all_dbs = DatabaseManager.get_all_databases()
        active_db = next((d for d in all_dbs if d.get("is_active_target")), all_dbs[0] if all_dbs else None)
        target_db_name = active_db.get("name") if active_db else "मुख्य मतदाता डेटाबेस"
        job.target_db_name = target_db_name

        print(f"[JOB {job_id[:8]}] Local AI Dual-Pass: {job.perfect_first_pass} perfect first-pass, "
              f"{job.errors_corrected} errors corrected. Target DB: '{target_db_name}'")

        # Generate Excel workbook
        excel_filename = f"Voter_List_UP_{job_id[:8]}.xlsx"
        excel_path = str(OUTPUT_DIR / excel_filename)
        ExcelBuilder.generate_excel(
            records=all_voters,
            output_path=excel_path,
            assembly_name=job.assembly_name,
            part_no=job.part_number,
            polling_station=job.polling_station,
            filename_source=job.filename
        )
        
        t_job_end = time.time()
        total_dur = t_job_end - t_job_start
        job.completed_at_timestamp = t_job_end
        job.elapsed_seconds = round(total_dur, 1)
        job.time_taken_formatted = format_duration_hindi(total_dur)
        if total_steps > 0:
            job.speed_seconds_per_page = round(total_dur / total_steps, 2)
        job.estimated_remaining_seconds = 0

        print(f"[JOB {job_id[:8]}] Completed: {len(all_voters)} voters, "
              f"{total_steps} pages in {total_dur:.1f}s ({job.time_taken_formatted})")
        
        # Automatically persist into active target database (with zero duplication / UPSERT)
        db_res = VoterDatabase.save_voters(
            records=all_voters,
            source_file=job.filename,
            assembly=job.assembly_name,
            part_no=job.part_number,
            polling_station=job.polling_station
        )
        print(f"[DB] Auto-saved into '{target_db_name}': {db_res['inserted']} new, {db_res['updated']} updated, {db_res['total_in_db']} total in DB")

        job.excel_path = excel_path
        job.status = "completed"
        job.progress_percent = 100
        
    except Exception as e:
        job.status = "error"
        job.error_message = f"प्रोसेसिंग के दौरान त्रुटि: {str(e)}"


# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.get("/api/health")
def health_check():
    """Returns system status and OCR engine availability."""
    ocr_avail = OCRExtractor.is_ocr_available()
    tess_ready = OCRExtractor.is_tesseract_ready()
    return {
        "status": "healthy",
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "ocr_engine_available": ocr_avail,
        "tesseract_ready": tess_ready,
        "ocr_engine_type": "Tesseract (hin+eng) + ONNX" if tess_ready else "RapidOCR-Devanagari-ONNX",
        "active_jobs_count": len(JOBS_DB)
    }


@app.post("/api/upload", dependencies=[Depends(verify_operator_or_admin_access)])
async def upload_pdf(file: UploadFile = File(...)):
    """Uploads and inspects a Voter List PDF."""
    if not file or not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="कृपया केवल वैध PDF फ़ाइल (.pdf) अपलोड करें।")
        
    job_id = str(uuid.uuid4())
    # Robust filename sanitation for Windows & cross-platform
    raw_name = os.path.basename(file.filename.replace("\\", "/")).strip()
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', raw_name)
    if not safe_name.strip() or safe_name == ".pdf":
        safe_name = f"voter_list_{job_id[:8]}.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
        
    saved_filename = f"{job_id}_{safe_name}"
    file_path = UPLOAD_DIR / saved_filename
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"फ़ाइल सहेजने में विफल: {str(e)}")
        
    # Inspect PDF
    try:
        inspection = PDFDetector.inspect_pdf(str(file_path))
    except Exception as e:
        if file_path.exists():
            try:
                os.remove(file_path)
            except Exception:
                pass
        raise HTTPException(
            status_code=400,
            detail=f"PDF फ़ाइल पढ़ने में त्रुटि: {str(e)}. कृपया सुनिश्चित करें कि यह एक वैध एवं असूरक्षित PDF फ़ाइल है।"
        )

    if inspection.get("is_ulb"):
        try:
            import pymupdf as fitz
            doc = fitz.open(str(file_path))
            cover_meta = ULBExtractor.extract_header_metadata(doc)
            doc.close()
        except Exception as e:
            print(f"[WARN] ULB metadata extraction error: {e}")
            cover_meta = {}
    else:
        try:
            cover_meta = OCRExtractor.extract_cover_metadata(str(file_path))
        except Exception as e:
            print(f"[WARN] Cover metadata extraction error: {e}")
            cover_meta = {}

    inspection["assembly"] = cover_meta.get("assembly")
    inspection["part_no"] = cover_meta.get("part_no")
    inspection["polling_station"] = cover_meta.get("polling_station")
    
    job = JobStatus(
        job_id=job_id,
        filename=safe_name,
        total_pages=inspection.get("total_pages", 0),
        processed_pages=0,
        status="uploaded",
        progress_percent=0,
        assembly_name=cover_meta.get("assembly"),
        part_number=cover_meta.get("part_no"),
        polling_station=cover_meta.get("polling_station"),
        voter_list_format=inspection.get("voter_list_format", "ECI_ASSEMBLY"),
        format_label=inspection.get("format_label", "भारत निर्वाचन आयोग (ECI विधानसभा)"),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    JOBS_DB[job_id] = job
    JOB_PDF_MAP[job_id] = str(file_path)
    
    return {
        "job_id": job_id,
        "filename": safe_name,
        "inspection": inspection,
        "message": "PDF फ़ाइल सफलतापूर्वक अपलोड हो गई है।"
    }


@app.post("/api/process/{job_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def start_processing(
    job_id: str,
    background_tasks: BackgroundTasks,
    start_page: Optional[int] = Query(None, ge=1),
    end_page: Optional[int] = Query(None, ge=1),
    force_ocr: bool = Query(False),
    part_no: Optional[str] = Query(None),
    assembly: Optional[str] = Query(None),
    polling_station: Optional[str] = Query(None)
):
    """Triggers background AI extraction for the uploaded PDF."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]
    if part_no and part_no.strip():
        job.part_number = part_no.strip()
    if assembly and assembly.strip():
        job.assembly_name = assembly.strip()
    if polling_station and polling_station.strip():
        job.polling_station = polling_station.strip()

    if job.status == "processing":
        return {"message": "प्रोसेसिंग पहले से चल रही है।", "job_id": job_id}
        
    job.status = "processing"
    job.progress_percent = 0
    job.records = []
    job.started_at_timestamp = time.time()
    job.completed_at_timestamp = None
    job.elapsed_seconds = 0.0
    job.time_taken_formatted = None
    job.speed_seconds_per_page = None
    job.estimated_remaining_seconds = None
    
    background_tasks.add_task(run_extraction_job, job_id, start_page, end_page, force_ocr)
    
    return {
        "job_id": job_id,
        "status": "processing",
        "message": "मतदाता डेटा निष्कर्षण शुरू हो चुका है।"
    }


@app.get("/api/status/{job_id}")
def get_job_status(job_id: str):
    """Returns real-time processing progress and status."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]

    live_elapsed = job.elapsed_seconds
    live_speed = job.speed_seconds_per_page
    live_remaining = job.estimated_remaining_seconds
    
    if job.status == "processing" and job.started_at_timestamp:
        live_elapsed = round(time.time() - job.started_at_timestamp, 1)
        if job.processed_pages > 0:
            live_speed = round(live_elapsed / job.processed_pages, 2)
            rem_pages = max(0, job.total_pages - job.processed_pages)
            live_remaining = int(round(rem_pages * live_speed))

    return {
        "job_id": job.job_id,
        "filename": job.filename,
        "status": job.status,
        "total_pages": job.total_pages,
        "processed_pages": job.processed_pages,
        "progress_percent": job.progress_percent,
        "total_voters_extracted": job.total_voters_extracted,
        "assembly_name": job.assembly_name,
        "part_number": job.part_number,
        "polling_station": job.polling_station,
        "error_message": job.error_message,
        "excel_path": job.excel_path,
        "has_excel": job.excel_path is not None,
        "started_at_timestamp": job.started_at_timestamp,
        "completed_at_timestamp": job.completed_at_timestamp,
        "elapsed_seconds": live_elapsed,
        "elapsed_formatted": format_duration_hindi(live_elapsed),
        "time_taken_formatted": job.time_taken_formatted or (format_duration_hindi(live_elapsed) if job.status == "completed" else None),
        "speed_seconds_per_page": live_speed,
        "estimated_remaining_seconds": live_remaining,
        "estimated_remaining_formatted": format_duration_hindi(live_remaining) if live_remaining is not None else None,
        "voter_list_format": job.voter_list_format,
        "format_label": job.format_label
    }


@app.get("/api/preview/{job_id}")
def get_voter_preview(
    job_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    warnings_only: bool = Query(False),
    review_only: bool = Query(False),
    pdf_page: Optional[int] = Query(None)
):
    """Returns paginated or physical PDF page-based searchable voter records with summary analytics."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]
    records = job.records
    
    # Compute available PDF physical pages with exact counts
    page_groups = {}
    for idx, r in enumerate(records):
        p_no = getattr(r, 'page_no', 1) or 1
        if p_no not in page_groups:
            page_groups[p_no] = {
                "count": 0, 
                "deleted_count": 0, 
                "min_serial": getattr(r, 'serial_no', 0) or 0, 
                "max_serial": getattr(r, 'serial_no', 0) or 0
            }
        page_groups[p_no]["count"] += 1
        if getattr(r, 'is_deleted', False):
            page_groups[p_no]["deleted_count"] += 1
        s_val = getattr(r, 'serial_no', None)
        if isinstance(s_val, int):
            if page_groups[p_no]["min_serial"] == 0 or s_val < page_groups[p_no]["min_serial"]:
                page_groups[p_no]["min_serial"] = s_val
            if s_val > page_groups[p_no]["max_serial"]:
                page_groups[p_no]["max_serial"] = s_val

    available_pages = []
    for p_no in sorted(page_groups.keys()):
        available_pages.append({
            "page_no": p_no,
            "count": page_groups[p_no]["count"],
            "deleted_count": page_groups[p_no]["deleted_count"],
            "start_serial": page_groups[p_no]["min_serial"],
            "end_serial": page_groups[p_no]["max_serial"]
        })

    page_num = page if isinstance(page, int) else 1
    limit_num = limit if isinstance(limit, int) else 50
    search_str = None
    if isinstance(search, str) and search.strip():
        decoded_search = urllib.parse.unquote(search.strip()).strip()
        search_str = decoded_search.lower() if decoded_search else None
    gender_str = gender if isinstance(gender, str) else None
    warnings_flag = warnings_only if isinstance(warnings_only, bool) else False
    review_flag = review_only if isinstance(review_only, bool) else False
    target_pdf_page = pdf_page if isinstance(pdf_page, int) else None

    # Filter with preserved job record index
    indexed_records = list(enumerate(records))
    
    if target_pdf_page is not None:
        # Exact Physical PDF Page matching (no artificial limit slicing)
        filtered = [(i, r) for i, r in indexed_records if getattr(r, 'page_no', None) == target_pdf_page]
        if search_str:
            filtered = [
                (i, r) for i, r in filtered 
                if search_str in (r.name or '').lower() 
                or search_str in (r.epic_no or '').lower() 
                or search_str in (r.relation_name or '').lower() 
                or search_str in (r.house_no or '').lower()
            ]
        if gender_str and gender_str != "all":
            filtered = [(i, r) for i, r in filtered if r.gender == gender_str]
        if warnings_flag:
            filtered = [(i, r) for i, r in filtered if r.has_warning]
        if review_flag:
            filtered = [(i, r) for i, r in filtered if r.has_warning or (getattr(r, 'confidence_score', 1.0) or 1.0) < 0.90]
            
        total_matching = len(filtered)
        paginated_records = filtered
        total_pages = 1
        current_page = 1
    else:
        filtered = indexed_records
        if search_str:
            filtered = [
                (i, r) for i, r in filtered 
                if search_str in (r.name or '').lower() 
                or search_str in (r.epic_no or '').lower() 
                or search_str in (r.relation_name or '').lower() 
                or search_str in (r.house_no or '').lower()
            ]
            
        if gender_str and gender_str != "all":
            filtered = [(i, r) for i, r in filtered if r.gender == gender_str]
            
        if warnings_flag:
            filtered = [(i, r) for i, r in filtered if r.has_warning]
        if review_flag:
            filtered = [(i, r) for i, r in filtered if r.has_warning or (getattr(r, 'confidence_score', 1.0) or 1.0) < 0.90]
            
        total_matching = len(filtered)
        start_idx = (page_num - 1) * limit_num
        end_idx = start_idx + limit_num
        paginated_records = filtered[start_idx:end_idx]
        total_pages = (total_matching + limit_num - 1) // limit_num if limit_num > 0 else 1
        current_page = page_num
    
    stats = ExcelBuilder.calculate_stats(records)
    is_op = is_operator_request(request)
    
    out_records = []
    for orig_idx, r in paginated_records:
        r_dict = r.dict()
        r_dict["job_index"] = orig_idx
        if is_op:
            redact_caste_from_record(r_dict)
        out_records.append(r_dict)
    
    from backend.modules.ulb_scanner_ai import ULBSequenceHealer
    audit_data = ULBSequenceHealer.audit_records(records)
    audit_data["is_ulb"] = getattr(job, "is_ulb", False) or getattr(job, "template_type", "") == "ulb"

    return {
        "job_id": job_id,
        "total_records": len(records),
        "total_filtered": total_matching,
        "page": current_page,
        "limit": limit,
        "total_pages": total_pages,
        "records": out_records,
        "available_pages": available_pages,
        "stats": stats.dict(),
        "audit": audit_data
    }


class UpdateRecordRequest(BaseModel):
    record_index: int
    updated_data: Dict[str, Any]


class AddRecordRequest(BaseModel):
    serial_no: Optional[int] = None
    epic_no: Optional[str] = ""
    name: str
    relation_type: Optional[str] = "पिता"
    relation_name: Optional[str] = ""
    house_no: Optional[str] = ""
    age: Optional[int] = None
    gender: Optional[str] = "पुरुष"
    section_no: Optional[str] = ""
    page_no: Optional[int] = None
    is_deleted: Optional[bool] = False
    deleted_reason: Optional[str] = ""


@app.post("/api/add-record/{job_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def add_voter_to_job(job_id: str, req: AddRecordRequest, request: Request):
    """Allows admin/operator to manually add a voter to the scanned PDF preview list, auto-sorted by serial_no."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")

    job = JOBS_DB[job_id]

    serial = req.serial_no
    if not serial or serial <= 0:
        serials = [r.serial_no for r in job.records if isinstance(r.serial_no, int)]
        serial = (max(serials) + 1) if serials else 1

    # Determine page_no: use supplied, or infer from neighboring serials
    page_no = req.page_no
    if not page_no or page_no <= 0:
        nearest_page = None
        min_diff = 999999
        for r in job.records:
            if isinstance(r.serial_no, int) and r.page_no:
                diff = abs(r.serial_no - serial)
                if diff < min_diff:
                    min_diff = diff
                    nearest_page = r.page_no
        if nearest_page and min_diff <= 35:
            page_no = nearest_page
        else:
            page_no = job.records[-1].page_no if job.records else 1

    new_voter = VoterRecord(
        serial_no=serial,
        epic_no=req.epic_no.strip().upper() if req.epic_no else "",
        name=req.name.strip(),
        relation_type=req.relation_type or "पिता",
        relation_name=req.relation_name.strip() if req.relation_name else "",
        house_no=req.house_no.strip() if req.house_no else "",
        age=req.age,
        gender=req.gender or "पुरुष",
        assembly=job.assembly_name,
        part_no=job.part_number,
        section_no=req.section_no or (job.records[0].section_no if job.records else ""),
        polling_station=job.polling_station,
        page_no=page_no,
        source_file=job.filename,
        is_deleted=bool(req.is_deleted),
        deleted_reason=req.deleted_reason.strip() if req.deleted_reason else ("विलोपित" if req.is_deleted else None)
    )

    validated = validate_voter_record(new_voter)
    job.records.append(validated)

    # Sort records numerically by serial_no so new voter sits in exact sequence
    def _serial_sort_key(r):
        try:
            return int(r.serial_no)
        except Exception:
            return 999999
    job.records.sort(key=_serial_sort_key)

    # Re-run local caste inference
    job.records = LocalCasteAIEngine.infer_caste_for_records(job.records)

    # Regenerate Excel
    if job.excel_path:
        ExcelBuilder.generate_excel(
            records=job.records,
            output_path=job.excel_path,
            assembly_name=job.assembly_name,
            part_no=job.part_number,
            polling_station=job.polling_station,
            filename_source=job.filename
        )

    log_admin_action("add_scanned_voter", None, f"Added voter {req.name} (Serial {serial}) to job {job_id}")

    ret_dict = validated.dict()
    if is_operator_request(request):
        redact_caste_from_record(ret_dict)

    return {
        "status": "success",
        "message": f"नया मतदाता (क्रम सं० {serial}) स्कैन सूची में सही स्थान पर जोड़ा गया।",
        "total_records": len(job.records),
        "target_page": page_no,
        "serial_no": serial,
        "record": ret_dict
    }


@app.post("/api/update-record/{job_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def update_voter_record(job_id: str, req: UpdateRecordRequest, request: Request):
    """Allows user/operator to edit and correct any voter row directly from the preview table or paper view."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]
    if req.record_index < 0 or req.record_index >= len(job.records):
        raise HTTPException(status_code=400, detail="अमान्य रिकॉर्ड इंडेक्स।")
        
    record = job.records[req.record_index]
    for key, val in req.updated_data.items():
        if hasattr(record, key):
            setattr(record, key, val)
            
    # Re-validate
    validated = validate_voter_record(record)
    job.records[req.record_index] = validated

    # Re-sort if serial_no was updated
    def _serial_sort_key(r):
        try:
            return int(r.serial_no)
        except Exception:
            return 999999
    job.records.sort(key=_serial_sort_key)
    
    # Regenerate Excel
    if job.excel_path:
        ExcelBuilder.generate_excel(
            records=job.records,
            output_path=job.excel_path,
            assembly_name=job.assembly_name,
            part_no=job.part_number,
            polling_station=job.polling_station,
            filename_source=job.filename
        )
        
    ret_dict = validated.dict()
    if is_operator_request(request):
        redact_caste_from_record(ret_dict)

    return {"status": "success", "updated_record": ret_dict, "message": "रिकॉर्ड सफलतापूर्वक अपडेट हो गया।"}


@app.post("/api/toggle-deleted/{job_id}/{record_index}", dependencies=[Depends(verify_operator_or_admin_access)])
def toggle_voter_deleted_status(job_id: str, record_index: int):
    """Toggles the is_deleted status of a voter in the scanned PDF list."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
    job = JOBS_DB[job_id]
    if record_index < 0 or record_index >= len(job.records):
        raise HTTPException(status_code=400, detail="अमान्य रिकॉर्ड इंडेक्स।")
    
    r = job.records[record_index]
    r.is_deleted = not r.is_deleted
    if r.is_deleted:
        if not r.deleted_reason:
            r.deleted_reason = "विलोपित"
    else:
        r.deleted_reason = None
    
    # Regenerate Excel
    if job.excel_path:
        ExcelBuilder.generate_excel(
            records=job.records,
            output_path=job.excel_path,
            assembly_name=job.assembly_name,
            part_no=job.part_number,
            polling_station=job.polling_station,
            filename_source=job.filename
        )
    return {
        "status": "success",
        "is_deleted": r.is_deleted,
        "message": f"मतदाता को {'विलोपित (DELETED)' if r.is_deleted else 'सक्रिय (Active)'} चिह्नित किया गया।"
    }


@app.delete("/api/delete-record/{job_id}/{record_index}", dependencies=[Depends(verify_operator_or_admin_access)])
def delete_scanned_voter_record(job_id: str, record_index: int):
    """Deletes a voter record completely from the scanned PDF list."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
    job = JOBS_DB[job_id]
    if record_index < 0 or record_index >= len(job.records):
        raise HTTPException(status_code=400, detail="अमान्य रिकॉर्ड इंडेक्स।")
    
    removed = job.records.pop(record_index)
    
    # Regenerate Excel
    if job.excel_path:
        ExcelBuilder.generate_excel(
            records=job.records,
            output_path=job.excel_path,
            assembly_name=job.assembly_name,
            part_no=job.part_number,
            polling_station=job.polling_station,
            filename_source=job.filename
        )
    return {
        "status": "success",
        "message": f"मतदाता '{removed.name}' को स्कैन सूची से हटा दिया गया।",
        "total_records": len(job.records)
    }


@app.delete("/api/delete-filtered-records/{job_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def delete_filtered_records_from_job(
    job_id: str,
    search: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    warnings_only: bool = Query(False),
    review_only: bool = Query(False),
    pdf_page: Optional[int] = Query(None)
):
    """Deletes all voter records matching active filters from the job preview."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")

    job = JOBS_DB[job_id]
    records = job.records

    search_str = urllib.parse.unquote(search.strip()).lower() if search and search.strip() else None
    gender_str = gender if gender and gender != "all" else None
    warnings_flag = warnings_only
    review_flag = review_only

    def matches_filter(r):
        if pdf_page is not None and getattr(r, 'page_no', None) != pdf_page:
            return False
        if search_str:
            name = (getattr(r, 'name', '') or '').lower()
            epic = (getattr(r, 'epic_no', '') or '').lower()
            rel = (getattr(r, 'relation_name', '') or '').lower()
            house = (getattr(r, 'house_no', '') or '').lower()
            if not (search_str in name or search_str in epic or search_str in rel or search_str in house):
                return False
        if gender_str and getattr(r, 'gender', '') != gender_str:
            return False
        if warnings_flag and not getattr(r, 'has_warning', False):
            return False
        if review_flag and not (getattr(r, 'has_warning', False) or (getattr(r, 'confidence_score', 1.0) or 1.0) < 0.90):
            return False
        return True

    original_count = len(records)
    kept_records = [r for r in records if not matches_filter(r)]
    deleted_count = original_count - len(kept_records)

    job.records = kept_records
    job.total_voters_extracted = len(kept_records)

    # Regenerate Excel
    if job.excel_path:
        try:
            ExcelBuilder.generate_excel(
                records=kept_records,
                output_path=job.excel_path,
                assembly_name=job.assembly_name,
                part_no=job.part_number,
                polling_station=job.polling_station,
                filename_source=job.filename
            )
        except Exception as e:
            print(f"[WARN] Error regenerating Excel after filtered delete: {e}")

    return {
        "status": "success",
        "deleted_count": deleted_count,
        "remaining_count": len(kept_records),
        "message": f"सफलतापूर्वक {deleted_count} फ़िल्टर किए गए मतदाता हटाए गए।"
    }


@app.get("/api/download/{job_id}")
def download_excel(job_id: str):
    """Downloads the generated Excel file."""
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]
    if not job.excel_path or not os.path.exists(job.excel_path):
        # Generate on-the-fly if needed
        if job.records:
            excel_filename = f"Voter_List_UP_{job_id[:8]}.xlsx"
            excel_path = str(OUTPUT_DIR / excel_filename)
            ExcelBuilder.generate_excel(
                records=job.records,
                output_path=excel_path,
                assembly_name=job.assembly_name,
                part_no=job.part_number,
                filename_source=job.filename
            )
            job.excel_path = excel_path
        else:
            raise HTTPException(status_code=400, detail="डाउनलोड के लिए कोई डेटा उपलब्ध नहीं है।")
            
    export_name = f"UP_Voter_List_{Path(job.filename).stem}.xlsx"
    return FileResponse(
        job.excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=export_name
    )


@app.post("/api/generate-sample", dependencies=[Depends(verify_operator_or_admin_access)])
def generate_sample_job(background_tasks: BackgroundTasks, pages: int = Query(2, ge=1, le=5)):
    """Generates an authentic UP voter list sample PDF and triggers immediate conversion."""
    sample_id = str(uuid.uuid4())
    sample_filename = f"UP_Voter_List_Sample_AC174_Part125_{sample_id[:6]}.pdf"
    sample_path = str(SAMPLE_DIR / sample_filename)
    
    # Generate PDF
    SamplePDFGenerator.create_sample_pdf(sample_path, pages=pages)
    
    # Register job
    inspection = PDFDetector.inspect_pdf(sample_path)
    job = JobStatus(
        job_id=sample_id,
        filename=sample_filename,
        total_pages=inspection["total_pages"],
        processed_pages=0,
        status="uploaded",
        progress_percent=0,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    JOBS_DB[sample_id] = job
    JOB_PDF_MAP[sample_id] = sample_path
    
    # Start extraction
    background_tasks.add_task(run_extraction_job, sample_id, 1, pages, False)
    
    return {
        "job_id": sample_id,
        "filename": sample_filename,
        "total_pages": pages,
        "message": "सैंपल UP वोटर लिस्ट PDF सफलतापूर्वक बनाई गई और निष्कर्षण शुरू किया गया।"
    }


# =============================================================================
# LOCAL MASTER DATABASE APIS (SEARCH, DEDUPLICATION, STATS, EXPORT)
# =============================================================================

@app.post("/api/database/save-job/{job_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def save_job_to_database(job_id: str):
    """
    Persists all records of a completed conversion job into the local SQLite database.
    Guarantees ZERO DUPLICATES (UPSERT by EPIC or Assembly+Part+Serial).
    Accessible to both Admins and Data Operators.
    """
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]
    if not job.records:
        raise HTTPException(status_code=400, detail="डेटाबेस में सेव करने के लिए कोई रिकॉर्ड उपलब्ध नहीं है।")
        
    res = VoterDatabase.save_voters(
        records=job.records,
        source_file=job.filename,
        assembly=job.assembly_name,
        part_no=job.part_number,
        polling_station=job.polling_station
    )
    return {
        "status": "success",
        "message": f"सफलतापूर्वक सुरक्षित: {res['inserted']} नए मतदाता जुड़े, {res['updated']} अपडेट हुए (0 डुप्लिकेट)।",
        "data": res
    }


class JobBulkUpdateMetadataRequest(BaseModel):
    part_no: Optional[str] = None
    part_number: Optional[str] = None
    assembly: Optional[str] = None
    assembly_name: Optional[str] = None
    polling_station: Optional[str] = None
    ward_no: Optional[str] = None
    ward_name: Optional[str] = None
    polling_booth: Optional[str] = None
    mohalla: Optional[str] = None


@app.post("/api/jobs/{job_id}/bulk-update-metadata", dependencies=[Depends(verify_operator_or_admin_access)])
def bulk_update_job_metadata(job_id: str, req: JobBulkUpdateMetadataRequest):
    """
    Bulk updates part_no, assembly, polling_station, ward_no, and polling_booth for an active upload job
    and all its extracted records in memory and regenerates Excel.
    """
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]
    target_part = req.part_number if req.part_number is not None else req.part_no
    if target_part is not None and target_part.strip():
        clean_part = target_part.strip()
        job.part_number = clean_part
        for r in job.records:
            r.part_no = clean_part
            
    target_assembly = req.assembly_name if req.assembly_name is not None else req.assembly
    if target_assembly is not None and target_assembly.strip():
        clean_ac = target_assembly.strip()
        job.assembly_name = clean_ac
        for r in job.records:
            r.assembly = clean_ac
            
    if req.polling_station is not None and req.polling_station.strip():
        clean_ps = req.polling_station.strip()
        job.polling_station = clean_ps
        for r in job.records:
            r.polling_station = clean_ps

    if req.ward_no is not None:
        clean_wn = str(req.ward_no).strip()
        for r in job.records:
            r.ward_no = clean_wn
            if req.ward_name:
                r.ward_name = str(req.ward_name).strip()

    if req.polling_booth is not None:
        clean_pb = str(req.polling_booth).strip()
        for r in job.records:
            r.polling_booth = clean_pb

    if req.mohalla is not None:
        clean_moh = str(req.mohalla).strip()
        for r in job.records:
            r.mohalla = clean_moh
            
    # Regenerate Excel if already created
    if job.excel_path and job.records:
        try:
            ExcelBuilder.generate_excel(
                records=job.records,
                output_path=job.excel_path,
                assembly_name=job.assembly_name,
                part_no=job.part_number,
                polling_station=job.polling_station,
                filename_source=job.filename
            )
        except Exception:
            pass
        
    return {
        "status": "success",
        "message": f"अपलोड सूची के सभी {len(job.records)} मतदाताओं की भाग संख्या ({job.part_number or 'अपरिवर्तित'}), विधानसभा ({job.assembly_name or 'अपरिवर्तित'}) व मतदान केंद्र सफलतापूर्वक अपडेट हो गए।",
        "job": {
            "part_number": job.part_number,
            "assembly_name": job.assembly_name,
            "polling_station": job.polling_station
        },
        "updated_metadata": {
            "part_number": job.part_number,
            "assembly_name": job.assembly_name,
            "polling_station": job.polling_station,
            "total_records": len(job.records)
        }
    }


# =============================================================================
# BULK PDF SCANNER FOR VIDHAN SABHA (विधान सभा बल्क स्कैनर)
# =============================================================================

BULK_JOBS_DB: Dict[str, Dict[str, Any]] = {}


class BulkFolderScanRequest(BaseModel):
    folder_path: str
    force_ocr: bool = False


class BulkFolderListRequest(BaseModel):
    folder_path: str


def _natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def run_bulk_folder_scan_worker(bulk_id: str, folder_path: str, force_ocr: bool = False):
    """Executes sequential background scanning for an entire folder of Electoral Roll PDF files."""
    bulk = BULK_JOBS_DB.get(bulk_id)
    if not bulk:
        return

    bulk["status"] = "processing"
    bulk["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bulk["start_time_epoch"] = time.time()
    bulk["elapsed_seconds"] = 0
    bulk["elapsed_formatted"] = "0 सेकंड"
    bulk["eta_seconds"] = None
    bulk["eta_formatted"] = "गणना की जा रही है..."
    bulk["total_estimated_formatted"] = "गणना की जा रही है..."
    bulk["avg_file_time_formatted"] = "--"

    folder = Path(folder_path.strip().strip('"').strip("'"))
    if not folder.exists() or not folder.is_dir():
        bulk["status"] = "error"
        bulk["error_message"] = f"फ़ोल्डर नहीं मिला: {folder_path}"
        return

    pdf_files = sorted(
        [p for p in folder.glob("*.pdf") if not p.name.startswith("._") and not p.name.startswith("~$")],
        key=lambda x: _natural_sort_key(x.name)
    )
    if not pdf_files:
        bulk["status"] = "completed"
        bulk["error_message"] = "उक्त फ़ोल्डर में कोई .pdf फ़ाइल नहीं मिली।"
        return

    bulk["total_files"] = len(pdf_files)
    bulk["files"] = [
        {
            "filename": p.name,
            "path": str(p),
            "status": "pending",
            "voters_count": 0,
            "assembly": None,
            "part_no": None,
            "polling_station": None,
            "start_time_epoch": None,
            "time_taken": None,
            "error": None
        }
        for p in pdf_files
    ]

    total_extracted_all = 0

    for idx, pdf_p in enumerate(pdf_files):
        if bulk.get("cancelled"):
            bulk["status"] = "cancelled"
            break

        file_start = time.time()
        bulk["current_file_index"] = idx + 1
        bulk["current_file"] = pdf_p.name
        file_item = bulk["files"][idx]
        file_item["status"] = "processing"
        file_item["start_time_epoch"] = file_start
        file_item["time_taken"] = "स्कैन जारी..."

        job_id = str(uuid.uuid4())
        file_item["job_id"] = job_id

        # Copy safely to UPLOAD_DIR for unified processing pipeline
        safe_name = re.sub(r'[\\/*?:"<>|]', '_', pdf_p.name)
        saved_filename = f"{job_id}_{safe_name}"
        dest_path = UPLOAD_DIR / saved_filename
        try:
            shutil.copyfile(str(pdf_p), str(dest_path))
        except Exception:
            dest_path = pdf_p

        try:
            inspection = PDFDetector.inspect_pdf(str(dest_path))
            cover_meta = OCRExtractor.extract_cover_metadata(str(dest_path))

            job = JobStatus(
                job_id=job_id,
                filename=safe_name,
                total_pages=inspection.get("total_pages", 0),
                processed_pages=0,
                status="processing",
                progress_percent=0,
                assembly_name=cover_meta.get("assembly"),
                part_number=cover_meta.get("part_no"),
                polling_station=cover_meta.get("polling_station"),
                voter_list_format=inspection.get("voter_list_format", "ECI_ASSEMBLY"),
                format_label=inspection.get("format_label", "भारत निर्वाचन आयोग (ECI विधानसभा)"),
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            JOBS_DB[job_id] = job
            JOB_PDF_MAP[job_id] = str(dest_path)

            # Synchronously execute extraction for this individual file in the worker loop
            run_extraction_job(job_id=job_id, force_ocr=force_ocr)

            # Inspect job outcome
            completed_job = JOBS_DB.get(job_id)
            file_dur = time.time() - file_start
            file_item["elapsed_seconds"] = round(file_dur, 1)
            file_item["time_taken"] = format_duration_hindi(file_dur)

            if completed_job and completed_job.status == "completed":
                v_cnt = len(completed_job.records)
                file_item["status"] = "completed"
                file_item["voters_count"] = v_cnt
                file_item["assembly"] = completed_job.assembly_name
                file_item["part_no"] = completed_job.part_number
                file_item["polling_station"] = completed_job.polling_station
                total_extracted_all += v_cnt
            else:
                file_item["status"] = "error"
                file_item["error"] = completed_job.error_message if completed_job else "अज्ञात त्रुटि"

        except Exception as ex:
            file_dur = time.time() - file_start
            file_item["elapsed_seconds"] = round(file_dur, 1)
            file_item["time_taken"] = format_duration_hindi(file_dur)
            file_item["status"] = "error"
            file_item["error"] = str(ex)

        proc_count = idx + 1
        bulk["processed_files"] = proc_count
        bulk["total_voters"] = total_extracted_all

        bulk_now = time.time()
        bulk_dur = bulk_now - bulk["start_time_epoch"]
        bulk["elapsed_seconds"] = round(bulk_dur, 1)
        bulk["elapsed_formatted"] = format_duration_hindi(bulk_dur)

        avg_f = bulk_dur / proc_count
        bulk["avg_file_time"] = round(avg_f, 1)
        bulk["avg_file_time_formatted"] = format_duration_hindi(avg_f)
        rem_f = bulk["total_files"] - proc_count
        if rem_f > 0:
            est_rem = avg_f * rem_f
            bulk["eta_seconds"] = round(est_rem, 1)
            bulk["eta_formatted"] = format_duration_hindi(est_rem)
            bulk["total_estimated_formatted"] = format_duration_hindi(avg_f * bulk["total_files"])
        else:
            bulk["eta_seconds"] = 0
            bulk["eta_formatted"] = "पूर्ण (0 सेकंड)"
            bulk["total_estimated_formatted"] = format_duration_hindi(bulk_dur)

    if not bulk.get("cancelled"):
        bulk["status"] = "completed"
    bulk["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pick_folder_native_windows() -> str:
    """Invokes native Windows folder browser dialog via PowerShell or Tkinter."""
    # Method 1: PowerShell FolderBrowserDialog (pure native Windows Shell)
    try:
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$f.Description = 'मतदाता सूची PDF का फ़ोल्डर चुनें'; "
            "$f.ShowNewFolderButton = $false; "
            "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=60
        )
        selected = proc.stdout.strip()
        if selected and os.path.isdir(selected):
            return selected
    except Exception as e:
        print(f"[BROWSE PS] Error: {e}")

    # Method 2: Tkinter fallback
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        folder = filedialog.askdirectory(title="मतदाता सूची PDF का फ़ोल्डर चुनें")
        root.destroy()
        if folder and os.path.isdir(folder):
            return folder
    except Exception as e:
        print(f"[BROWSE TK] Error: {e}")

    return ""


@app.post("/api/bulk/browse-folder", dependencies=[Depends(verify_operator_or_admin_access)])
def bulk_browse_folder_dialog():
    """Opens native Windows folder selection dialog and returns discovered PDF files."""
    selected_path = _pick_folder_native_windows()
    if not selected_path:
        return {"cancelled": True, "message": "फ़ोल्डर चयन रद्द किया गया या कोई फ़ोल्डर नहीं चुना गया।"}

    folder = Path(selected_path)
    pdf_files = sorted(
        [p for p in folder.glob("*.pdf") if not p.name.startswith("._") and not p.name.startswith("~$")],
        key=lambda x: _natural_sort_key(x.name)
    )
    return {
        "cancelled": False,
        "folder_path": str(folder),
        "total_files": len(pdf_files),
        "files": [{"filename": p.name, "size_bytes": p.stat().st_size} for p in pdf_files]
    }


@app.post("/api/bulk/list-folder", dependencies=[Depends(verify_operator_or_admin_access)])
def bulk_list_folder(req: BulkFolderListRequest):
    """Inspects a local folder path and returns the list of PDF files detected."""
    clean_path = req.folder_path.strip().strip('"').strip("'")
    folder = Path(clean_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"फ़ोल्डर नहीं मिला: {clean_path}")

    pdf_files = sorted(
        [p for p in folder.glob("*.pdf") if not p.name.startswith("._") and not p.name.startswith("~$")],
        key=lambda x: _natural_sort_key(x.name)
    )
    return {
        "folder_path": str(folder),
        "total_files": len(pdf_files),
        "files": [{"filename": p.name, "size_bytes": p.stat().st_size} for p in pdf_files]
    }


@app.post("/api/bulk/scan-local-folder", dependencies=[Depends(verify_operator_or_admin_access)])
def bulk_scan_local_folder(req: BulkFolderScanRequest, background_tasks: BackgroundTasks):
    """Launches a background batch scan for an entire local directory of Electoral Roll PDFs."""
    clean_path = req.folder_path.strip().strip('"').strip("'")
    folder = Path(clean_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"फ़ोल्डर नहीं मिला: {clean_path}")

    pdf_files = [p for p in folder.glob("*.pdf") if not p.name.startswith("._") and not p.name.startswith("~$")]
    if not pdf_files:
        raise HTTPException(status_code=400, detail=f"उक्त फ़ोल्डर में कोई .pdf फ़ाइल नहीं मिली: {clean_path}")

    bulk_id = str(uuid.uuid4())
    BULK_JOBS_DB[bulk_id] = {
        "bulk_id": bulk_id,
        "folder_path": str(folder),
        "status": "pending",
        "total_files": len(pdf_files),
        "processed_files": 0,
        "current_file": None,
        "current_file_index": 0,
        "total_voters": 0,
        "cancelled": False,
        "started_at": None,
        "completed_at": None,
        "files": []
    }

    background_tasks.add_task(run_bulk_folder_scan_worker, bulk_id, str(folder), req.force_ocr)

    return {
        "bulk_id": bulk_id,
        "status": "started",
        "total_files": len(pdf_files),
        "message": f"{len(pdf_files)} PDF फ़ाइलों का क्रमवार स्कैन प्रारंभ हो चुका है।"
    }


@app.get("/api/bulk/status/{bulk_id}")
def get_bulk_scan_status(bulk_id: str):
    """Returns the live status of a bulk folder scan with real-time active job metrics."""
    bulk = BULK_JOBS_DB.get(bulk_id)
    if not bulk:
        raise HTTPException(status_code=404, detail="Bulk Job ID नहीं मिला।")

    # Clone bulk dictionary to enrich with live worker progress
    res = dict(bulk)
    active_idx = (bulk.get("current_file_index", 1) or 1) - 1
    files_list = bulk.get("files", [])
    live_active_voters = 0

    # Real-time overall duration and ETA calculation
    start_ep = bulk.get("start_time_epoch")
    if start_ep:
        live_dur = time.time() - start_ep
        res["elapsed_seconds"] = round(live_dur, 1)
        res["elapsed_formatted"] = format_duration_hindi(live_dur)
        proc = bulk.get("processed_files", 0)
        tot = bulk.get("total_files", 0)
        if proc > 0 and tot > proc:
            avg_live = live_dur / proc
            rem_live = (tot - proc) * avg_live
            res["eta_seconds"] = round(rem_live, 1)
            res["eta_formatted"] = format_duration_hindi(rem_live)
            res["total_estimated_formatted"] = format_duration_hindi(avg_live * tot)
            res["avg_file_time_formatted"] = format_duration_hindi(avg_live)
        elif proc >= tot and tot > 0:
            res["eta_seconds"] = 0
            res["eta_formatted"] = "पूर्ण"
            res["total_estimated_formatted"] = format_duration_hindi(live_dur)
        else:
            res["eta_formatted"] = "गणना की जा रही है..."
            res["total_estimated_formatted"] = "गणना की जा रही है..."

    if 0 <= active_idx < len(files_list):
        active_item = files_list[active_idx]
        active_job_id = active_item.get("job_id")
        files_copy = [dict(f) for f in files_list]

        active_start = active_item.get("start_time_epoch")
        if active_start and active_item.get("status") == "processing":
            live_f_dur = time.time() - active_start
            files_copy[active_idx]["live_elapsed_seconds"] = round(live_f_dur, 1)
            files_copy[active_idx]["time_taken"] = format_duration_hindi(live_f_dur)

        if active_job_id and active_job_id in JOBS_DB:
            cur_job = JOBS_DB[active_job_id]
            live_active_voters = cur_job.total_voters_extracted or 0
            res["current_job_pages"] = cur_job.processed_pages
            res["current_job_total_pages"] = cur_job.total_pages
            res["current_job_progress_pct"] = cur_job.progress_percent
            res["current_job_live_voters"] = live_active_voters
            res["current_job_speed"] = cur_job.speed_seconds_per_page
            if cur_job.assembly_name and not active_item.get("assembly"):
                active_item["assembly"] = cur_job.assembly_name
            if cur_job.part_number and not active_item.get("part_no"):
                active_item["part_no"] = cur_job.part_number
            if cur_job.polling_station and not active_item.get("polling_station"):
                active_item["polling_station"] = cur_job.polling_station

            # Reflect live voters and pages in the active file item
            files_copy[active_idx]["live_voters"] = live_active_voters
            files_copy[active_idx]["processed_pages"] = cur_job.processed_pages
            files_copy[active_idx]["total_pages"] = cur_job.total_pages

        res["files"] = files_copy

    res["live_total_voters"] = (bulk.get("total_voters", 0) or 0) + live_active_voters
    return res


@app.post("/api/bulk/cancel/{bulk_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def cancel_bulk_scan(bulk_id: str):
    """Cancels an ongoing bulk folder scan."""
    bulk = BULK_JOBS_DB.get(bulk_id)
    if not bulk:
        raise HTTPException(status_code=404, detail="Bulk Job ID नहीं मिला।")
    bulk["cancelled"] = True
    return {"status": "cancelling", "message": "बल्क स्कैन रद्द करने का अनुरोध दर्ज किया गया।"}


@app.get("/api/database/stats", dependencies=[Depends(verify_user_access)])
def get_database_stats(request: Request, db_id: Optional[str] = Query(None)):
    """Returns total voter counts, gender ratio, parts, and assembly breakdown."""
    stats = VoterDatabase.get_stats(db_id=db_id)
    if not is_admin_or_superadmin_request(request):
        stats.pop("muslim_voters", None)
        stats.pop("muslim_percentage", None)
        stats.pop("hindu_voters", None)
        stats.pop("non_muslim_voters", None)
        stats.pop("community_breakdown", None)
        stats.pop("top_castes", None)
    return stats


@app.get("/api/database/caste-analytics", dependencies=[Depends(verify_user_access)])
def get_caste_analytics(request: Request):
    """
    Returns aggregated community & caste distribution data for interactive charts.
    Strictly blocked for non-admin users (धर्म व समुदाय विवरण केवल एडमिन और सुपर एडमिन को ही दिखे).
    """
    if not is_admin_or_superadmin_request(request):
        raise HTTPException(
            status_code=403,
            detail="पहुँच अस्वीकृत (Access Denied): धर्म एवं समुदाय विवरण केवल एडमिन और सुपर एडमिन के लिए ही उपलब्ध है।"
        )
    return VoterDatabase.get_caste_community_analytics()


@app.get("/api/database/search", dependencies=[Depends(verify_user_access)])
def search_database(
    request: Request,
    q: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    relation_name: Optional[str] = Query(None),
    epic_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    assembly: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    house_no: Optional[str] = Query(None),
    min_age: Optional[int] = Query(None, ge=1),
    max_age: Optional[int] = Query(None, ge=1),
    muslim: Optional[str] = Query(None),
    caste_key: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=5000),
    source: Optional[str] = Query(None),
    db_id: Optional[str] = Query(None)
):
    """
    Multi-criteria indexed search across all saved voters in local SQLite database.
    Supports both universal keyword (q) and field-specific filtering.
    Applies privacy restrictions if request is from the online public portal.
    Redacts all caste info for operator requests.
    """
    # Parameter normalization (handles cases where function is called directly with Query default values)
    q = q if isinstance(q, str) else None
    name = name if isinstance(name, str) else None
    relation_name = relation_name if isinstance(relation_name, str) else None
    epic_no = epic_no if isinstance(epic_no, str) else None
    part_no = part_no if isinstance(part_no, str) else None
    assembly = assembly if isinstance(assembly, str) else None
    gender = gender if isinstance(gender, str) else None
    house_no = house_no if isinstance(house_no, str) else None
    min_age = min_age if isinstance(min_age, int) else None
    max_age = max_age if isinstance(max_age, int) else None
    muslim = muslim if isinstance(muslim, str) else None
    caste_key = caste_key if isinstance(caste_key, str) else None
    status = status if isinstance(status, str) else None
    page = page if isinstance(page, int) else 1
    limit = limit if isinstance(limit, int) else 50
    source = source if isinstance(source, str) else None
    db_id = db_id if isinstance(db_id, str) and db_id.strip() else None

    # Operator / Non-Admin cannot filter by caste or religion
    is_op = is_operator_request(request)
    if is_op:
        caste_key = None
        muslim = None

    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    user = get_current_user_optional(request)
    is_admin = bool((token and token == ADMIN_TOKEN) or (user and user.get("role") == "admin"))
    
    is_public = False
    if not is_admin:
        if source == "public" or is_public_request(request):
            is_public = True

    # Rule: Without entering search criteria, public users must NOT receive any voters automatically
    # Requirement: ONLINE विस्तृत फ़िल्टर में कम से कम नाम, पिता अथवा पति का नाम, या EPIC No भरा होना अनिवार्य है
    if is_public:
        has_q = bool(q and q.strip())
        has_primary_id = bool(
            (name and name.strip()) or
            (relation_name and relation_name.strip()) or
            (epic_no and epic_no.strip())
        )

        if not has_q and not has_primary_id:
            return {
                "total_records": VoterDatabase.get_total_count(db_id=db_id),
                "total_filtered": 0,
                "page": page,
                "limit": limit,
                "total_pages": 1,
                "records": [],
                "error": "विस्तृत फ़िल्टर में कम से कम मतदाता का नाम, पिता/पति का नाम अथवा EPIC No भरना अनिवार्य है।"
            }

        has_query = any([
            has_q,
            has_primary_id,
            part_no and part_no.strip() and part_no != "all",
            assembly and assembly.strip() and assembly != "all",
            gender and gender.strip() and gender != "all",
            house_no and house_no.strip(),
            min_age is not None and min_age > 0,
            max_age is not None and max_age > 0,
            muslim and muslim.strip() and muslim != "all",
            caste_key and caste_key.strip() and caste_key != "all"
        ])
        if not has_query:
            return {
                "total_records": VoterDatabase.get_total_count(db_id=db_id),
                "total_filtered": 0,
                "page": page,
                "limit": limit,
                "total_pages": 1,
                "records": []
            }

    search_res = VoterDatabase.search_voters(
        q=q,
        name=name,
        relation_name=relation_name,
        epic_no=epic_no,
        part_no=part_no,
        assembly=assembly,
        gender=gender,
        house_no=house_no,
        min_age=min_age,
        max_age=max_age,
        muslim=muslim,
        caste_key=caste_key,
        status=status,
        page=page,
        limit=limit,
        is_public=is_public,
        db_id=db_id
    )

    if is_op and search_res.get("records"):
        try:
            from backend.modules.pending_edits_manager import PendingEditsManager
            curr_user = get_current_user_optional(request)
            op_user = curr_user.get("username") if curr_user else ""
            if op_user:
                search_res["records"] = PendingEditsManager.overlay_operator_edits(
                    search_res["records"],
                    operator_username=op_user,
                    db_id=db_id
                )
        except Exception as e:
            print(f"Pending edits overlay warning: {e}")

        for r in search_res["records"]:
            redact_caste_from_record(r)

    return search_res


@app.get("/api/database/slip/{record_id}", dependencies=[Depends(verify_user_access)])
def get_voter_slip(record_id: int, request: Request, source: Optional[str] = Query(None)):
    """
    Returns official voter information slip (मतदाता सूचना पर्ची) data
    for single-click viewing, WhatsApp sharing, or printing.
    Protects restricted voters from public viewing.
    Redacts caste info if requested by an operator.
    """
    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    is_admin = bool(token and token == ADMIN_TOKEN)
    
    is_public = False
    if not is_admin:
        if source == "public" or is_public_request(request):
            is_public = True

    voter = VoterDatabase.get_voter_by_id(record_id, is_public=is_public)
    if not voter:
        raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला अथवा सार्वजनिक खोज के लिए उपलब्ध नहीं है।")

    if is_operator_request(request):
        redact_caste_from_record(voter)

    return {
        "status": "success",
        "slip": voter
    }


# =============================================================================
# PRIVACY & ONLINE SEARCH RESTRICTION APIS
# =============================================================================

@app.get("/api/admin/privacy-settings", dependencies=[Depends(verify_admin_access)])
def get_privacy_settings():
    """Returns current privacy configuration, matched counts per caste preset, and overall impact."""
    from .modules.privacy_manager import PrivacyManager
    from .config import DB_PATH
    return PrivacyManager.get_settings_with_counts(str(DB_PATH))


class PrivacySettingsPayload(BaseModel):
    enabled: bool
    block_muslim: bool
    blocked_caste_keys: List[str]
    custom_surnames: Any = []


@app.post("/api/admin/privacy-settings", dependencies=[Depends(verify_admin_access)])
def save_privacy_settings(payload: PrivacySettingsPayload):
    """Saves admin privacy restriction configuration."""
    from .modules.privacy_manager import PrivacyManager
    updated = PrivacyManager.save_settings(payload.dict())
    return {
        "status": "success",
        "message": "ऑनलाइन गोपनीयता एवं सर्च प्रतिबंध सेटिंग्स सफलतापूर्वक सुरक्षित कर दी गईं।",
        "settings": updated
    }


# =============================================================================
# TUNNEL STATUS & QR CODE APIS (FOR MOBILE SEARCH & WHATSAPP SHARING)
# =============================================================================

@app.get("/api/admin/tunnel-status")
def get_tunnel_status(request: Request):
    """
    Returns current online tunnel status, public search URL, and QR endpoint.
    Uses TunnelService to inspect live cloudflared tunnel or local network fallback.
    """
    status = TunnelService.get_status(auto_start=False)
    # If not running and request is from local admin, auto-start if possible
    if not status.get("is_online") and not is_public_request(request):
        status = TunnelService.get_status(auto_start=True)
    return status


@app.post("/api/admin/tunnel-refresh")
def refresh_tunnel(request: Request):
    """
    Forcibly refreshes and restarts the Cloudflare Tunnel to generate a fresh,
    working public trycloudflare HTTPS link and QR code.
    """
    return TunnelService.refresh_tunnel()


@app.get("/api/portal-share-info")
def get_portal_share_info(request: Request):
    """
    Returns live voter search portal URL, WhatsApp share URL, and pre-formatted Hindi share text.
    Accessible to all users without authentication.
    """
    import urllib.parse
    status = TunnelService.get_status(auto_start=False)
    search_url = status.get("search_url") or "http://127.0.0.1:8000/search"

    whatsapp_message = (
        "🇮🇳 *मतदाता सेवा — ऑनलाइन वोटर सर्च पोर्टल* 🇮🇳\n\n"
        "निर्वाचक नामावली (वोटर लिस्ट) में अपना व अपने पूरे परिवार का नाम, भाग संख्या, व क्रम संख्या आसानी से खोजें:\n\n"
        f"🔗 *वेब लिंक:* {search_url}\n\n"
        "📱 बिना किसी ऐप के सीधे मोबाइल ब्राउज़र में खोलें और 1 सेकंड में अपनी डिजिटल मतदाता पर्ची देखें!"
    )

    whatsapp_share_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(whatsapp_message)}"

    return {
        "status": "success",
        "is_online": status.get("is_online", False),
        "search_url": search_url,
        "whatsapp_url": whatsapp_share_url,
        "whatsapp_message": whatsapp_message,
        "qr_endpoint": f"/api/portal-qr?url={search_url}"
    }


@app.get("/api/portal-qr")
@app.get("/api/admin/tunnel-qr")
def get_tunnel_qr_code(request: Request, url: Optional[str] = Query(None)):
    """
    Generates and returns high-resolution QR code PNG for the voter search portal URL.
    Can be used for direct mobile camera scanning and flyer/WhatsApp sharing.
    """
    import io
    import qrcode

    target_url = url
    if not target_url:
        link_file = OUTPUT_DIR / "latest_online_link.txt"
        if link_file.exists():
            try:
                content = link_file.read_text(encoding="utf-8").strip()
                if content.startswith("http"):
                    target_url = content if "/search" in content else f"{content.rstrip('/')}/search"
            except Exception:
                pass
                
    if not target_url:
        host = request.headers.get("host") or "127.0.0.1:8000"
        scheme = request.headers.get("x-forwarded-proto") or "http"
        target_url = f"{scheme}://{host}/search"

    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=3,
        )
        qr.add_data(target_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0F172A", back_color="#FFFFFF")
        
        # Also cache to disk
        try:
            cache_file = OUTPUT_DIR / "voter_search_qr.png"
            img.save(str(cache_file))
        except Exception:
            pass

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"QR कोड जनरेट करने में समस्या: {str(e)}")




@app.get("/api/database/export", dependencies=[Depends(verify_user_access)])
def export_database_search(
    request: Request,
    q: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    relation_name: Optional[str] = Query(None),
    epic_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    assembly: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    house_no: Optional[str] = Query(None),
    min_age: Optional[int] = Query(None, ge=1),
    max_age: Optional[int] = Query(None, ge=1),
    muslim: Optional[str] = Query(None),
    caste_key: Optional[str] = Query(None)
):
    """Exports searched database records to a formatted Excel file (.xlsx)."""
    is_admin_or_super = is_admin_or_superadmin_request(request)
    where_params = {
        "q": q, "name": name, "relation_name": relation_name,
        "epic_no": epic_no, "part_no": part_no, "assembly": assembly,
        "gender": gender, "house_no": house_no,
        "min_age": min_age, "max_age": max_age,
        "muslim": muslim if is_admin_or_super else None,
        "caste_key": caste_key if is_admin_or_super else None
    }
    export_filename = f"Voters_DB_Search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    export_path = str(OUTPUT_DIR / export_filename)
    VoterDatabase.export_to_excel(where_params, export_path, redact_caste=not is_admin_or_super)
    
    return FileResponse(
        export_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=export_filename
    )


class UpdateDatabaseVoterRequest(BaseModel):
    serial_no: Optional[int] = None
    epic_no: Optional[str] = None
    name: str
    relation_type: Optional[str] = "पिता"
    relation_name: Optional[str] = None
    house_no: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = "पुरुष"
    part_no: Optional[str] = None
    assembly: Optional[str] = None
    polling_station: Optional[str] = None
    caste_key: Optional[str] = None
    is_muslim: Optional[int] = None
    is_deleted: Optional[int] = 0


class AddDatabaseVoterRequest(BaseModel):
    serial_no: Optional[int] = None
    epic_no: Optional[str] = ""
    name: str
    relation_type: Optional[str] = "पिता"
    relation_name: Optional[str] = ""
    house_no: Optional[str] = ""
    age: Optional[int] = None
    gender: Optional[str] = "पुरुष"
    part_no: str
    section_no: Optional[str] = ""
    assembly: Optional[str] = ""
    polling_station: Optional[str] = ""
    caste_key: Optional[str] = None
    is_muslim: Optional[int] = None
    is_deleted: Optional[int] = 0


class DeleteBatchRequest(BaseModel):
    ids: List[int]


class DeleteFilterRequest(BaseModel):
    q: Optional[str] = None
    name: Optional[str] = None
    relation_name: Optional[str] = None
    epic_no: Optional[str] = None
    part_no: Optional[str] = None
    assembly: Optional[str] = None
    gender: Optional[str] = None
    house_no: Optional[str] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    muslim: Optional[str] = None


@app.post("/api/database/reindex-community", dependencies=[Depends(verify_user_access)])
def reindex_database_community(request: Request):
    """Re-analyzes and tags community for all voters in the local database."""
    if not is_admin_or_superadmin_request(request):
        raise HTTPException(
            status_code=403,
            detail="पहुँच अस्वीकृत (Access Denied): धर्म एवं समुदाय विवरण केवल एडमिन और सुपर एडमिन के लिए ही उपलब्ध है।"
        )
    res = VoterDatabase.reindex_community()
    return {
        "status": "success",
        "message": f"{res['total_analyzed']} मतदाताओं का विश्लेषण पूर्ण: {res['muslim_identified']} मुस्लिम मतदाता ({res['muslim_percentage']}%) चिह्नित।",
        "data": res
    }


@app.post("/api/database/recompute-castes", dependencies=[Depends(verify_user_access)])
def recompute_database_castes(request: Request):
    """Recomputes and propagates caste tags across households and lineages for all voters."""
    if not is_admin_or_superadmin_request(request):
        raise HTTPException(
            status_code=403,
            detail="पहुँच अस्वीकृत (Access Denied): धर्म एवं समुदाय विवरण केवल एडमिन और सुपर एडमिन के लिए ही उपलब्ध है।"
        )
    res = VoterDatabase.recompute_all_castes()
    return {
        "status": "success",
        "message": f"{res['total_analyzed']} मतदाताओं का विश्लेषण पूर्ण: {res['total_classified']} मतदाताओं की जाति सफलतापूर्वक निर्धारित की गई।",
        "data": res
    }


# ==============================================================================
# STREET AUDIT & NPPROPERTY SURVEY INTEGRATION
# Rule: "सदस्य का नाम व संबंधी का नाम से ध्वन्यात्मक मिलान से सम्पूर्ण वोटर डेटाबेस मे"
# ==============================================================================

class ExportStreetAuditRequest(BaseModel):
    street: Optional[str] = ""
    zone: Optional[str] = ""
    ward: Optional[str] = ""
    houses: List[Dict[str, Any]]
    unregisteredOnly: bool = False


@app.get("/api/survey-audit/streets", dependencies=[Depends(verify_superadmin_only_access)])
def get_survey_streets():
    """Fetches unique zones, wards and streets from NPPropertyServey."""
    res = PropertySurveySync.fetch_streets()
    if not res.get("success"):
        raise HTTPException(status_code=502, detail=res.get("error", "NPPropertyServey API से संपर्क विफल"))
    return res


@app.get("/api/survey-audit/street-voters", dependencies=[Depends(verify_superadmin_only_access)])
def audit_street_voters(
    street: Optional[str] = Query(None, description="भौगोलिक गली का नाम (ऐच्छिक/ALL)"),
    zone: Optional[str] = Query(None, description="ज़ोन संख्या (वैकल्पिक/ALL)"),
    ward: Optional[str] = Query(None, description="वार्ड का नाम (वैकल्पिक)"),
    min_age: int = Query(17, description="न्यूनतम आयु फ़िल्टर (17 या 18)")
):
    """
    Fetches houses and 17+ family members from NPPropertyServey for the given street or ENTIRE ZONE,
    and performs phonetic matching against the ENTIRE voter database to determine registration status.
    """
    clean_street = (street or "").strip()
    clean_zone = (zone or "").strip()

    # 1. Fetch street survey data from NPPropertyServey (supports single street, entire zone, or all zones)
    street_data = PropertySurveySync.fetch_street_houses_and_members(
        street=clean_street if clean_street.upper() not in ("ALL", "ANY", "-- समस्त गलियाँ --") else None,
        zone=clean_zone if clean_zone.upper() not in ("ALL", "ANY", "-- समस्त ज़ोन --") else None,
        ward=ward.strip() if ward else None,
        min_age=min_age
    )
    if not street_data.get("success"):
        raise HTTPException(status_code=502, detail=street_data.get("error", "सर्वे डेटा प्राप्त नहीं हो सका"))

    # 2. Fetch all active voters from voter database
    all_voters = VoterDatabase.get_all_voters_for_audit()

    # 3. Perform phonetic matching and audit annotation
    audit_res = PropertySurveySync.audit_street_voters(street_data, all_voters)
    return audit_res


class MapMemberVoterRequest(BaseModel):
    family_id: str = Field(..., description="NPPropertyServey family id")
    member_id: str = Field(..., description="NPPropertyServey member id")
    voter_id: int = Field(..., description="Voter DB ID")
    survey_id: Optional[str] = None
    member_name: Optional[str] = None
    notes: Optional[str] = None


@app.get("/api/voters/by-house", dependencies=[Depends(verify_superadmin_only_access)])
def get_voters_by_house(
    house_no: str = Query(..., description="मकान संख्या"),
    part_no: Optional[str] = Query(None, description="मतदान केंद्र भाग संख्या (वैकल्पिक/ALL)")
):
    """Returns all registered voters residing in a specific house_no (and optionally part_no)."""
    if not house_no or not str(house_no).strip():
        raise HTTPException(status_code=400, detail="मकान संख्या (house_no) अनिवार्य है।")
    res = VoterDatabase.get_voters_by_house(part_no=part_no, house_no=str(house_no).strip())
    return {
        "success": True,
        **res
    }


@app.get("/api/voters/distinct-parts", dependencies=[Depends(verify_superadmin_only_access)])
def get_distinct_parts():
    """Returns all distinct parts in the database with voter counts for smart dropdowns."""
    parts = VoterDatabase.get_distinct_parts()
    return {
        "success": True,
        "count": len(parts),
        "parts": parts
    }


@app.get("/api/survey-audit/search-voter-candidate", dependencies=[Depends(verify_superadmin_only_access)])
def search_voter_candidate(
    q: Optional[str] = Query(None, description="खोज हेतु नाम, EPIC, सम्बन्धी या मकान"),
    name: Optional[str] = Query(None, description="मतदाता का नाम"),
    relation_name: Optional[str] = Query(None, description="पिता/पति का नाम"),
    epic_no: Optional[str] = Query(None, description="पहचान पत्र क्रमांक (EPIC No)"),
    house_no: Optional[str] = Query(None, description="मकान संख्या"),
    part_no: Optional[str] = Query(None, description="वैकल्पिक भाग फ़िल्टर"),
    gender: Optional[str] = Query(None, description="लिंग फ़िल्टर"),
    min_age: Optional[int] = Query(None, description="न्यूनतम आयु"),
    max_age: Optional[int] = Query(None, description="अधिकतम आयु"),
    limit: int = Query(40, ge=1, le=100)
):
    """
    Searches voters across the entire database for manual mapping in the modal.
    Powered by the exact same multi-criteria AI search engine as the main page's online search.
    """
    voters = VoterDatabase.search_voter_candidate(
        q=q,
        name=name,
        relation_name=relation_name,
        epic_no=epic_no,
        house_no=house_no,
        part_no=part_no,
        gender=gender,
        min_age=min_age,
        max_age=max_age,
        limit=limit
    )
    return {
        "success": True,
        "query": q,
        "count": len(voters),
        "voters": voters
    }


class AutoMapFamilyLocalAIRequest(BaseModel):
    family_id: str = Field(..., description="NPPropertyServey family id")
    survey_id: Optional[str] = None
    overwrite_existing: bool = False


@app.post("/api/survey-audit/auto-map-family-local-ai", dependencies=[Depends(verify_superadmin_only_access)])
def auto_map_family_local_ai(req: AutoMapFamilyLocalAIRequest):
    """
    Applies Local AI Household Kinship and Phonetic Matching to auto-map all eligible
    unmapped members of the specified NPPropertyServey family to database voter records.
    """
    try:
        res = PropertySurveySync.auto_map_family_local_ai(
            family_id=req.family_id,
            survey_id=req.survey_id,
            overwrite_existing=req.overwrite_existing
        )
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("error", "लोकल AI मैपिंग विफल"))
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"लोकल AI मैपिंग में आंतरिक त्रुटि: {str(e)}")


@app.post("/api/survey-audit/map-member-voter", dependencies=[Depends(verify_superadmin_only_access)])
def map_survey_member_to_voter(req: MapMemberVoterRequest):
    """Maps an NPPropertyServey family member to a voter list record."""
    try:
        res = VoterDatabase.save_member_voter_mapping(
            family_id=req.family_id,
            member_id=req.member_id,
            voter_id=req.voter_id,
            survey_id=req.survey_id,
            member_name=req.member_name,
            notes=req.notes
        )
        # Attempt sync with NPPropertyServey
        np_sync_res = PropertySurveySync.sync_mapping_to_np_survey(res)
        return {
            "success": True,
            "message": f"सदस्य '{req.member_name or req.member_id}' को वोटर रिकॉर्ड से सफलतापूर्वक मैप कर दिया गया है।",
            "data": res,
            "np_sync": np_sync_res
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"मैपिंग सेव करने में त्रुटि: {str(e)}")


@app.delete("/api/survey-audit/map-member-voter", dependencies=[Depends(verify_superadmin_only_access)])
def delete_survey_member_voter_mapping(
    family_id: str = Query(..., description="NPPropertyServey family id"),
    member_id: str = Query(..., description="NPPropertyServey member id")
):
    """Removes a member-to-voter mapping."""
    success = VoterDatabase.delete_member_voter_mapping(family_id=family_id, member_id=member_id)
    # Attempt delete on NPPropertyServey
    np_del_res = PropertySurveySync.delete_mapping_from_np_survey(family_id=family_id, member_id=member_id)
    return {
        "success": True,
        "message": "मैपिंग सफलतापूर्वक हटा दी गई।",
        "deleted": success,
        "np_sync": np_del_res
    }


@app.get("/api/survey-audit/house-mappings", dependencies=[Depends(verify_superadmin_only_access)])
def get_survey_house_mappings(
    family_id: str = Query(..., description="NPPropertyServey family id")
):
    """Returns all active member-to-voter mappings for a given family."""
    mappings = VoterDatabase.get_mappings_by_family(family_id=family_id)
    return {
        "success": True,
        "family_id": family_id,
        "count": len(mappings),
        "mappings": mappings
    }


@app.get("/api/database/voter/{record_id}", dependencies=[Depends(verify_user_access)])
def get_database_voter(record_id: int, request: Request):
    """Retrieves full details of a voter record by ID for viewing/editing. Redacts caste for operators."""
    voter = VoterDatabase.get_voter_by_id(record_id, is_public=False)
    if not voter:
        raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")
    if is_operator_request(request):
        redact_caste_from_record(voter)
    return {"status": "success", "data": voter}


@app.post("/api/database/add", dependencies=[Depends(verify_operator_or_admin_access)])
@app.post("/api/database/voter", dependencies=[Depends(verify_operator_or_admin_access)])
def add_database_record(req: AddDatabaseVoterRequest, request: Request):
    """Adds a new voter record directly into database (Admin) or stages for approval (Operator)."""
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="मतदाता का नाम अनिवार्य है।")
    if not req.part_no or not req.part_no.strip():
        raise HTTPException(status_code=400, detail="भाग संख्या अनिवार्य है।")

    is_op = is_operator_request(request)
    curr_user = get_current_user_optional(request)
    username = curr_user.get("username", "admin") if curr_user else "admin"
    full_name = curr_user.get("full_name", username) if curr_user else username

    if is_op:
        from backend.modules.pending_edits_manager import PendingEditsManager
        prop_data = req.dict()
        edit_id = PendingEditsManager.create_pending_edit(
            operator_username=username,
            operator_name=full_name,
            action_type="ADD",
            target_id=None,
            original_data=None,
            proposed_data=prop_data,
            part_no=req.part_no,
            assembly=req.assembly,
            voter_name=req.name,
            epic_no=req.epic_no
        )
        return {
            "status": "pending",
            "pending_edit_id": edit_id,
            "message": "नया मतदाता जोड़ने का अनुरोध दर्ज कर लिया गया है। यह एडमिन के अनुमोदन के उपरांत मुख्य सूची में स्थायी रूप से जुड़ेगा।",
            "data": {**prop_data, "id": f"pending_{edit_id}", "is_pending_approval": True}
        }

    new_voter = VoterDatabase.add_voter(req.dict())
    log_admin_action("add_voter", new_voter["id"], f"Added new voter: {req.name} in part {req.part_no}")
    return {
        "status": "success",
        "message": "नया मतदाता डेटाबेस में सफलतापूर्वक जोड़ा गया।",
        "data": new_voter
    }


@app.put("/api/database/update/{record_id}", dependencies=[Depends(verify_operator_or_admin_access)])
@app.post("/api/database/update/{record_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def update_database_record(record_id: int, req: UpdateDatabaseVoterRequest, request: Request):
    """Updates a single voter record (Admin) or stages for approval (Operator)."""
    is_op = is_operator_request(request)
    curr_user = get_current_user_optional(request)
    username = curr_user.get("username", "admin") if curr_user else "admin"
    full_name = curr_user.get("full_name", username) if curr_user else username

    if is_op:
        from backend.modules.pending_edits_manager import PendingEditsManager
        orig_voter = VoterDatabase.get_voter_by_id(record_id)
        if not orig_voter:
            raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")

        prop_data = {k: v for k, v in req.dict().items() if v is not None}
        edit_id = PendingEditsManager.create_pending_edit(
            operator_username=username,
            operator_name=full_name,
            action_type="UPDATE",
            target_id=record_id,
            original_data=orig_voter,
            proposed_data=prop_data,
            part_no=prop_data.get("part_no") or orig_voter.get("part_no"),
            assembly=prop_data.get("assembly") or orig_voter.get("assembly"),
            voter_name=prop_data.get("name") or orig_voter.get("name"),
            epic_no=prop_data.get("epic_no") or orig_voter.get("epic_no")
        )
        return {
            "status": "pending",
            "pending_edit_id": edit_id,
            "message": "मतदाता विवरण में सुधार दर्ज कर लिया गया है। यह एडमिन के अनुमोदन (Approval) के उपरांत मुख्य सूची में स्थायी रूप से लागू होगा।",
            "data": {**orig_voter, **prop_data, "is_pending_approval": True}
        }

    updated = VoterDatabase.update_voter(record_id, req.dict())
    if not updated:
        raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला अथवा अपडेट असफल रहा।")
    log_admin_action("update_voter", record_id, f"Updated voter: {req.name or ''}")
    return {
        "status": "success",
        "message": "मतदाता विवरण सफलतापूर्वक अद्यतन (Update) कर दिया गया।",
        "data": updated
    }


@app.delete("/api/database/delete/{record_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def delete_database_record(record_id: int, request: Request):
    """Deletes a single voter record (Admin) or stages deletion for approval (Operator)."""
    is_op = is_operator_request(request)
    curr_user = get_current_user_optional(request)
    username = curr_user.get("username", "admin") if curr_user else "admin"
    full_name = curr_user.get("full_name", username) if curr_user else username

    if is_op:
        from backend.modules.pending_edits_manager import PendingEditsManager
        orig_voter = VoterDatabase.get_voter_by_id(record_id)
        if not orig_voter:
            raise HTTPException(status_code=404, detail="रिकॉर्ड नहीं मिला।")
        edit_id = PendingEditsManager.create_pending_edit(
            operator_username=username,
            operator_name=full_name,
            action_type="DELETE",
            target_id=record_id,
            original_data=orig_voter,
            proposed_data={"is_deleted": 1},
            part_no=orig_voter.get("part_no"),
            assembly=orig_voter.get("assembly"),
            voter_name=orig_voter.get("name"),
            epic_no=orig_voter.get("epic_no")
        )
        return {
            "status": "pending",
            "pending_edit_id": edit_id,
            "message": "मतदाता हटाने का अनुरोध दर्ज कर लिया गया है। यह एडमिन के अनुमोदन के उपरांत स्थायी रूप से लागू होगा।"
        }

    success = VoterDatabase.delete_voter(record_id)
    if not success:
        raise HTTPException(status_code=404, detail="रिकॉर्ड नहीं मिला।")
    log_admin_action("delete_voter", record_id, f"Deleted voter ID: {record_id}")
    return {"status": "success", "message": "मतदाता रिकॉर्ड सफलतापूर्वक हटा दिया गया।"}


@app.post("/api/database/delete-batch", dependencies=[Depends(verify_admin_access)])
def delete_batch_database_records(req: DeleteBatchRequest):
    """Deletes multiple selected voter records from database (Admin Only)."""
    if not req.ids:
        raise HTTPException(status_code=400, detail="कोई मतदाता आईडी चयनित नहीं की गई।")
    count = VoterDatabase.delete_selected_voters(req.ids)
    log_admin_action("delete_batch", None, f"Batch deleted {count} voters")
    return {"status": "success", "count": count, "message": f"{count} मतदाता रिकॉर्ड सफलतापूर्वक हटा दिए गए।"}


@app.post("/api/database/delete-by-filter", dependencies=[Depends(verify_admin_access)])
def delete_by_filter_database_records(req: DeleteFilterRequest):
    """Deletes all voters matching the active filter criteria (Admin Only)."""
    where_params = {k: v for k, v in req.dict().items() if v is not None}
    if not where_params:
        raise HTTPException(status_code=400, detail="फ़िल्टर मानदंड निर्दिष्ट नहीं किया गया।")
    count = VoterDatabase.delete_by_criteria(where_params)
    if count == 0:
        return {"status": "warning", "count": 0, "message": "दिए गए फ़िल्टर के अनुसार कोई रिकॉर्ड नहीं मिला।"}
    log_admin_action("delete_by_filter", None, f"Deleted {count} voters by filter")
    return {"status": "success", "count": count, "message": f"{count} मतदाता रिकॉर्ड सफलतापूर्वक हटा दिए गए।"}


@app.delete("/api/database/clear", dependencies=[Depends(verify_admin_access)])
def clear_database():
    """Clears all records from local database (Admin Only)."""
    count = VoterDatabase.clear_all()
    log_admin_action("clear_database", None, f"Cleared {count} records")
    return {"status": "success", "message": f"{count} रिकॉर्ड हटा दिए गए। डेटाबेस खाली है।"}


# =============================================================================
# DATABASE BACKUP & RESTORE ENDPOINTS
# =============================================================================

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/database/backup", dependencies=[Depends(verify_admin_access)])
def backup_database():
    """
    Creates a timestamped backup of the SQLite database and returns it as a downloadable file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"voters_backup_{timestamp}.db"
    backup_path = BACKUP_DIR / backup_filename

    try:
        import sqlite3 as _sqlite3
        source_conn = _sqlite3.connect(str(DB_PATH))
        dest_conn = _sqlite3.connect(str(backup_path))
        source_conn.backup(dest_conn)
        dest_conn.close()
        source_conn.close()
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"बैकअप बनाने में त्रुटि: {str(ex)}")

    log_admin_action("backup_database", None, f"Backup created: {backup_filename}")
    return FileResponse(
        str(backup_path),
        media_type="application/x-sqlite3",
        filename=backup_filename,
        headers={"Content-Disposition": f'attachment; filename="{backup_filename}"'}
    )


@app.post("/api/database/restore", dependencies=[Depends(verify_admin_access)])
async def restore_database(file: UploadFile = File(...)):
    """
    Restores the database from an uploaded .db backup file.
    Creates an automatic pre-restore backup first.
    """
    if not file.filename.endswith('.db'):
        raise HTTPException(status_code=400, detail="कृपया केवल .db फ़ाइल अपलोड करें।")

    # Save uploaded file to temp location
    temp_path = BACKUP_DIR / f"restore_temp_{uuid.uuid4().hex[:8]}.db"
    try:
        content = await file.read()
        with open(str(temp_path), "wb") as f:
            f.write(content)

        # Validate that it's a valid SQLite database
        import sqlite3 as _sqlite3
        try:
            test_conn = _sqlite3.connect(str(temp_path))
            test_conn.execute("SELECT count(*) FROM sqlite_master;")
            # Check if voters table exists
            cursor = test_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voters';")
            if not cursor.fetchone():
                test_conn.close()
                temp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="अमान्य बैकअप: 'voters' टेबल नहीं मिली।")
            test_conn.close()
        except _sqlite3.DatabaseError:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="अमान्य SQLite डेटाबेस फ़ाइल।")

        # Create pre-restore backup
        pre_restore_backup = BACKUP_DIR / f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        source_conn = _sqlite3.connect(str(DB_PATH))
        dest_conn = _sqlite3.connect(str(pre_restore_backup))
        source_conn.backup(dest_conn)
        dest_conn.close()
        source_conn.close()

        # Replace current database with the restored one
        shutil.copy2(str(temp_path), str(DB_PATH))
        temp_path.unlink(missing_ok=True)

        # Reinitialize database
        VoterDatabase._initialized = False
        VoterDatabase.init_db()

        log_admin_action("restore_database", None, f"Database restored from: {file.filename}")
        return {
            "status": "success",
            "message": f"डेटाबेस सफलतापूर्वक '{file.filename}' से बहाल कर दिया गया! पूर्व-बैकअप: {pre_restore_backup.name}"
        }
    except HTTPException:
        raise
    except Exception as ex:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"रिस्टोर में त्रुटि: {str(ex)}")


# =============================================================================
# PART-WISE ANALYTICS ENDPOINT
# =============================================================================

@app.get("/api/database/part-analytics", dependencies=[Depends(verify_user_access)])
def get_part_analytics(request: Request, db_id: Optional[str] = Query(None)):
    """Returns per-part voter analytics: total, male, female, muslim, top caste, etc."""
    if not VoterDatabase._initialized:
        VoterDatabase.init_db(db_id=db_id)

    is_op = is_operator_request(request)

    with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                part_no,
                polling_station,
                COUNT(*) as total,
                SUM(CASE WHEN gender = 'पुरुष' AND is_deleted = 0 THEN 1 ELSE 0 END) as male,
                SUM(CASE WHEN gender = 'महिला' AND is_deleted = 0 THEN 1 ELSE 0 END) as female,
                SUM(CASE WHEN is_muslim = 1 AND is_deleted = 0 THEN 1 ELSE 0 END) as muslim,
                SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) as deleted
            FROM voters
            GROUP BY part_no
            ORDER BY CAST(part_no AS INTEGER);
        """)
        parts_raw = cursor.fetchall()

        parts = []
        for row in parts_raw:
            part_no = row["part_no"] or "अज्ञात"
            active = row["active"] or 0

            # Find top caste for this part
            top_caste = None
            top_caste_count = 0
            if not is_op:
                cursor.execute("""
                    SELECT caste_key, COUNT(*) as cnt
                    FROM voters
                    WHERE part_no = ? AND caste_key IS NOT NULL AND caste_key != '' AND caste_key != 'muslim' AND is_deleted = 0
                    GROUP BY caste_key
                    ORDER BY cnt DESC
                    LIMIT 1;
                """, (row["part_no"],))
                top_caste_row = cursor.fetchone()
                top_caste = top_caste_row["caste_key"] if top_caste_row else None
                top_caste_count = top_caste_row["cnt"] if top_caste_row else 0

            muslim_count = row["muslim"] or 0
            muslim_pct = round((muslim_count / active * 100), 1) if active > 0 else 0.0
            hindu_count = max(0, active - muslim_count)
            hindu_pct = round((hindu_count / active * 100), 1) if active > 0 else 0.0

            part_data = {
                "part_no": part_no,
                "polling_station": row["polling_station"] or "",
                "total": row["total"] or 0,
                "active": active,
                "male": row["male"] or 0,
                "female": row["female"] or 0,
                "deleted": row["deleted"] or 0
            }
            if not is_op:
                part_data.update({
                    "hindu": hindu_count,
                    "hindu_pct": hindu_pct,
                    "muslim": muslim_count,
                    "muslim_pct": muslim_pct,
                    "top_caste": top_caste,
                    "top_caste_count": top_caste_count
                })
            parts.append(part_data)

    return {"status": "success", "parts": parts, "total_parts": len(parts)}


# =============================================================================
# ADMIN ACTIVITY AUDIT LOG
# =============================================================================

def _init_audit_log_table():
    """Creates the audit_log table if it doesn't exist."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target_id INTEGER,
            details TEXT,
            username TEXT DEFAULT 'admin',
            timestamp TEXT NOT NULL
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);")
    conn.commit()
    conn.close()

# Initialize audit log table on startup
_init_audit_log_table()


def log_admin_action(action: str, target_id: Optional[int] = None, details: str = "", username: str = "admin"):
    """Inserts an audit log entry for any admin action."""
    try:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(DB_PATH), timeout=10.0)
        conn.execute(
            "INSERT INTO audit_log (action, target_id, details, username, timestamp) VALUES (?, ?, ?, ?, ?);",
            (action, target_id, details, username, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Audit logging should never break main functionality


@app.get("/api/admin/audit-log", dependencies=[Depends(verify_admin_access)])
def get_audit_log(
    action: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200)
):
    """Returns paginated admin activity audit log."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = _sqlite3.Row

    where_clauses = []
    params = []
    if action:
        where_clauses.append("action = ?")
        params.append(action)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    offset = (page - 1) * limit

    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) as cnt FROM audit_log {where_sql};", params)
    total = cursor.fetchone()["cnt"]

    cursor.execute(f"""
        SELECT id, action, target_id, details, username, timestamp
        FROM audit_log {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?;
    """, params + [limit, offset])
    rows = [dict(r) for r in cursor.fetchall()]

    # Get summary stats
    cursor.execute("SELECT COUNT(*) as cnt FROM audit_log WHERE timestamp >= date('now');")
    today_count = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM audit_log WHERE timestamp >= date('now', '-7 days');")
    week_count = cursor.fetchone()["cnt"]

    conn.close()

    return {
        "status": "success",
        "logs": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit if limit > 0 else 1,
        "today_count": today_count,
        "week_count": week_count
    }




class LoginRequest(BaseModel):
    username: str
    password: str
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    device_fp: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: Optional[str] = None
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = ""
    role: Optional[str] = "user"


class UpdateUserStatusRequest(BaseModel):
    status: str  # 'active' | 'inactive'


class ResetPasswordAdminRequest(BaseModel):
    new_password: str


@app.post("/api/auth/login")
def api_login(req: LoginRequest, request: Request):
    """
    Authenticates user. Regular users are locked to their first mobile device.
    Admin 'harshsamrat' can log in from any mobile or device without restriction.
    """
    ip = request.client.host if request.client else ""
    ok, session_data, msg = AuthManager.authenticate_user(
        username=req.username,
        password=req.password,
        device_id=req.device_id,
        device_name=req.device_name,
        device_fp=req.device_fp,
        ip_address=ip
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg, **session_data}


@app.post("/api/auth/logout")
def api_logout(request: Request):
    """Revokes session token and logs out the user."""
    auth_header = request.headers.get("authorization") or ""
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = (
            request.headers.get("x-auth-token")
            or request.query_params.get("auth_token")
            or request.cookies.get("voter_auth_token")
        )
    if token:
        AuthManager.revoke_session(token)
    return {"status": "success", "message": "सफलतापूर्वक लॉगआउट हो गया।"}


@app.get("/api/auth/me")
def api_me(request: Request):
    """Returns currently authenticated user session details."""
    user = get_current_user_optional(request)
    if not user:
        if not is_public_request(request):
            return {
                "authenticated": True,
                "user": {
                    "user_id": "local_admin",
                    "username": "local_admin",
                    "role": "admin",
                    "full_name": "स्थानीय व्यवस्थापक (Local Admin)",
                    "active": True,
                    "is_admin": True,
                    "is_superadmin": False
                }
            }
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": user}


@app.post("/api/auth/change-password")
def api_change_password(req: ChangePasswordRequest, request: Request):
    """Allows authenticated user to change their own password."""
    user = get_current_user_required(request)
    ok, msg = AuthManager.change_user_password(
        user_id=user["user_id"],
        new_password=req.new_password,
        old_password=req.old_password,
        is_admin_override=False
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


@app.get("/api/admin/users", dependencies=[Depends(verify_admin_access)])
def api_admin_list_users():
    """Lists all registered users and their device binding status (Admin Only)."""
    users = AuthManager.list_users()
    return {"status": "success", "users": users, "total": len(users)}


@app.post("/api/admin/users", dependencies=[Depends(verify_admin_access)])
def api_admin_create_user(req: CreateUserRequest):
    """Creates a new user account (Admin Only)."""
    ok, new_user, msg = AuthManager.create_user(
        username=req.username,
        password=req.password,
        full_name=req.full_name or "",
        role=req.role or "user"
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg, "user": new_user}


@app.post("/api/admin/users/{user_id}/status", dependencies=[Depends(verify_admin_access)])
def api_admin_update_status(user_id: int, req: UpdateUserStatusRequest):
    """Activates or deactivates a user account (Admin Only)."""
    ok, msg = AuthManager.update_user_status(user_id, req.status)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


@app.post("/api/admin/users/{user_id}/reset-device", dependencies=[Depends(verify_admin_access)])
def api_admin_reset_device(user_id: int):
    """Resets user mobile device binding so they can bind a new phone (Admin Only)."""
    ok, msg = AuthManager.reset_user_device(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


@app.post("/api/admin/users/{user_id}/reset-password", dependencies=[Depends(verify_admin_access)])
def api_admin_reset_password(user_id: int, req: ResetPasswordAdminRequest):
    """Resets any user's password without needing their old password (Admin Only)."""
    ok, msg = AuthManager.change_user_password(
        user_id=user_id,
        new_password=req.new_password,
        old_password=None,
        is_admin_override=True
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


@app.delete("/api/admin/users/{user_id}", dependencies=[Depends(verify_admin_access)])
def api_admin_delete_user(user_id: int):
    """Deletes a user account. Superadmin cannot be deleted (Admin Only)."""
    ok, msg = AuthManager.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


# =============================================================================
# MULTI-DATABASE MANAGEMENT & BULK UPDATE & ERROR CORRECTION APIS
# =============================================================================

class CreateDatabaseRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class RenameDatabaseRequest(BaseModel):
    name: str
    description: Optional[str] = None


class BulkUpdatePartRequest(BaseModel):
    current_part_no: Optional[str] = None
    new_assembly: Optional[str] = None
    new_assembly_name: Optional[str] = None
    new_part_no: Optional[str] = None
    current_assembly: Optional[str] = None
    new_polling_station: Optional[str] = None
    db_id: Optional[str] = None
    source_file: Optional[str] = None
    job_id: Optional[str] = None


class RescanPartRequest(BaseModel):
    part_no: str
    assembly: Optional[str] = None
    db_id: Optional[str] = None


@app.get("/api/admin/databases", dependencies=[Depends(verify_admin_access)])
def api_get_all_databases():
    """Returns list of all registered SQLite databases with stats and active/default flags."""
    dbs = DatabaseManager.get_all_databases()
    return {
        "status": "success",
        "databases": dbs,
        "total_count": len(dbs)
    }


@app.post("/api/admin/databases/create", dependencies=[Depends(verify_admin_access)])
def api_create_database(req: CreateDatabaseRequest):
    """Creates a new database and initializes schema."""
    try:
        db_info = DatabaseManager.create_database(req.name, req.description or "")
        log_admin_action("create_database", None, f"Created new database: '{req.name}' ({db_info['filename']})")
        return {
            "status": "success",
            "message": f"नया डेटाबेस '{req.name}' सफलतापूर्वक निर्मित कर दिया गया!",
            "database": db_info
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/admin/databases/{db_id}/rename", dependencies=[Depends(verify_admin_access)])
def api_rename_database(db_id: str, req: RenameDatabaseRequest):
    """Saves/renames the database name and description."""
    try:
        db_info = DatabaseManager.save_database_name(db_id, req.name, req.description)
        log_admin_action("rename_database", None, f"Renamed database ID {db_id} to '{req.name}'")
        return {
            "status": "success",
            "message": f"डेटाबेस का नाम सफलतापूर्वक '{req.name}' सुरक्षित कर दिया गया!",
            "database": db_info
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/databases/{db_id}/set-active-target", dependencies=[Depends(verify_admin_access)])
def api_set_active_target_database(db_id: str):
    """Designates which database newly scanned data will be saved into."""
    try:
        db_info = DatabaseManager.set_active_target(db_id)
        log_admin_action("set_active_target_db", None, f"Set active target DB: '{db_info['name']}' ({db_id})")
        return {
            "status": "success",
            "message": f"डेटा सेव करने के लिए सक्रिय डेटाबेस '{db_info['name']}' निर्धारित कर दिया गया।",
            "database": db_info
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/databases/{db_id}/set-default-search", dependencies=[Depends(verify_admin_access)])
def api_set_default_search_database(db_id: str):
    """Designates which database is used as the default for voter search."""
    try:
        db_info = DatabaseManager.set_default_search(db_id)
        log_admin_action("set_default_search_db", None, f"Set default search DB: '{db_info['name']}' ({db_id})")
        return {
            "status": "success",
            "message": f"मतदाता सर्च के लिए डिफ़ॉल्ट डेटाबेस '{db_info['name']}' निर्धारित कर दिया गया।",
            "database": db_info
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/databases/{db_id}", dependencies=[Depends(verify_admin_access)])
def api_delete_database(db_id: str):
    """Deletes a secondary database."""
    try:
        res = DatabaseManager.delete_database(db_id)
        log_admin_action("delete_database", None, f"Deleted database ID: {db_id}")
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/database/search-db-info")
def api_get_search_db_info():
    """Returns info about the current default search database for public/worker search portal."""
    all_dbs = DatabaseManager.get_all_databases()
    default_db = next((d for d in all_dbs if d.get("is_default_search")), all_dbs[0] if all_dbs else None)
    return {
        "status": "success",
        "search_db_id": default_db["id"] if default_db else "default",
        "search_db_name": default_db["name"] if default_db else "मुख्य मतदाता डेटाबेस",
        "default_db": {
            "id": default_db["id"] if default_db else "default",
            "name": default_db["name"] if default_db else "मुख्य मतदाता डेटाबेस",
            "total_voters": default_db.get("total_voters", 0) if default_db else 0,
            "total_parts": default_db.get("total_parts", 0) if default_db else 0
        },
        "available_databases": [
            {"id": d["id"], "name": d["name"], "total_voters": d.get("total_voters", 0)}
            for d in all_dbs
        ]
    }


@app.post("/api/database/bulk-update-part", dependencies=[Depends(verify_operator_or_admin_access)])
def api_bulk_update_part(req: BulkUpdatePartRequest, request: Request):
    """
    Bulk updates Assembly Constituency and Part Number for all voter records
    belonging to a specific part in a single operation.
    If called by an operator, stages the bulk update for Admin approval.
    """
    is_op = is_operator_request(request)
    curr_user = get_current_user_optional(request)
    username = curr_user.get("username", "admin") if curr_user else "admin"
    full_name = curr_user.get("full_name", username) if curr_user else username
    target_assembly = req.new_assembly or req.new_assembly_name

    if is_op:
        from backend.modules.pending_edits_manager import PendingEditsManager
        prop_data = {
            "current_part_no": req.current_part_no,
            "new_part_no": req.new_part_no,
            "current_assembly": req.current_assembly,
            "new_assembly": target_assembly,
            "new_polling_station": req.new_polling_station,
            "db_id": req.db_id
        }
        edit_id = PendingEditsManager.create_pending_edit(
            operator_username=username,
            operator_name=full_name,
            action_type="BULK_UPDATE_PART",
            target_id=None,
            original_data={"part_no": req.current_part_no, "assembly": req.current_assembly},
            proposed_data=prop_data,
            part_no=req.current_part_no,
            assembly=target_assembly,
            voter_name=f"भाग {req.current_part_no} के सभी मतदाता",
            db_id=req.db_id
        )
        return {
            "status": "pending",
            "pending_edit_id": edit_id,
            "message": f"भाग {req.current_part_no} की भाग संख्या व विधानसभा बदलने का अनुरोध दर्ज हो गया है। एडमिन के अनुमोदन के उपरांत मुख्य डेटाबेस में लागू होगा।"
        }

    try:
        # In-memory sync if job_id is provided and currently active in JOBS_DB
        if req.job_id and req.job_id in JOBS_DB:
            job = JOBS_DB[req.job_id]
            if req.new_part_no:
                job.part_number = req.new_part_no
            if target_assembly:
                job.assembly_name = target_assembly
            if req.new_polling_station:
                job.polling_station = req.new_polling_station
            if job.records:
                for r in job.records:
                    if req.new_part_no:
                        r["part_no"] = req.new_part_no
                    if target_assembly:
                        r["assembly"] = target_assembly
                        r["assembly_name"] = target_assembly
                    if req.new_polling_station:
                        r["polling_station"] = req.new_polling_station
            if job.excel_path and os.path.exists(job.excel_path):
                try:
                    os.remove(job.excel_path)
                except Exception:
                    pass
                job.excel_path = None

        res = VoterDatabase.bulk_update_assembly_part(
            current_part_no=req.current_part_no or "",
            new_assembly=target_assembly,
            new_part_no=req.new_part_no or "",
            current_assembly=req.current_assembly,
            new_polling_station=req.new_polling_station,
            db_id=req.db_id,
            source_file=req.source_file
        )
        if res.get("status") == "error":
            raise HTTPException(status_code=404, detail=res.get("message"))

        log_admin_action(
            "bulk_update_part",
            None,
            f"File/Part '{req.source_file or req.current_part_no}' updated: AC='{target_assembly}', Part='{req.new_part_no}', Records={res.get('updated_count')}"
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"अपडेट विफल: {str(e)}")


# =============================================================================
# OPERATOR PENDING EDITS & ADMIN APPROVAL ENDPOINTS
# =============================================================================

@app.get("/api/admin/pending-edits", dependencies=[Depends(verify_admin_access)])
def get_pending_edits(
    status: Optional[str] = Query("pending"),
    operator: Optional[str] = Query(None),
    db_id: Optional[str] = Query(None)
):
    """Lists operator pending edits for admin review and approval."""
    from backend.modules.pending_edits_manager import PendingEditsManager
    edits = PendingEditsManager.list_pending_edits(status=status, operator_username=operator, db_id=db_id)
    counts = PendingEditsManager.get_pending_counts(db_id=db_id)
    return {
        "status": "success",
        "edits": edits,
        "records": edits,
        "counts": counts
    }


@app.get("/api/admin/pending-edits/counts", dependencies=[Depends(verify_user_access)])
def get_pending_edits_counts(db_id: Optional[str] = Query(None)):
    """Returns pending edits count badge data."""
    from backend.modules.pending_edits_manager import PendingEditsManager
    counts = PendingEditsManager.get_pending_counts(db_id=db_id)
    return {"status": "success", "counts": counts}


@app.post("/api/admin/pending-edits/{edit_id}/approve", dependencies=[Depends(verify_admin_access)])
def approve_pending_edit_endpoint(edit_id: int, request: Request, db_id: Optional[str] = Query(None)):
    """Approves a single operator pending edit and commits changes to voters table."""
    from backend.modules.pending_edits_manager import PendingEditsManager
    curr_user = get_current_user_optional(request)
    admin_name = curr_user.get("username", "admin") if curr_user else "admin"
    ok, msg = PendingEditsManager.approve_pending_edit(edit_id, admin_name, db_id=db_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    log_admin_action("approve_pending_edit", edit_id, f"Approved pending edit #{edit_id} by {admin_name}")
    return {"status": "success", "message": msg}


class RejectEditRequest(BaseModel):
    reason: Optional[str] = None
    db_id: Optional[str] = None


@app.post("/api/admin/pending-edits/{edit_id}/reject", dependencies=[Depends(verify_admin_access)])
def reject_pending_edit_endpoint(
    edit_id: int,
    req: RejectEditRequest,
    request: Request
):
    """Rejects a single operator pending edit and discards changes."""
    from backend.modules.pending_edits_manager import PendingEditsManager
    curr_user = get_current_user_optional(request)
    admin_name = curr_user.get("username", "admin") if curr_user else "admin"
    ok, msg = PendingEditsManager.reject_pending_edit(edit_id, admin_name, reason=req.reason, db_id=req.db_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    log_admin_action("reject_pending_edit", edit_id, f"Rejected pending edit #{edit_id} by {admin_name}")
    return {"status": "success", "message": msg}


class BulkPendingActionRequest(BaseModel):
    edit_ids: List[int]
    reason: Optional[str] = None
    db_id: Optional[str] = None


@app.post("/api/admin/pending-edits/bulk-approve", dependencies=[Depends(verify_admin_access)])
def bulk_approve_pending_edits_endpoint(req: BulkPendingActionRequest, request: Request):
    """Approves multiple operator pending edits in bulk."""
    from backend.modules.pending_edits_manager import PendingEditsManager
    curr_user = get_current_user_optional(request)
    admin_name = curr_user.get("username", "admin") if curr_user else "admin"
    res = PendingEditsManager.bulk_approve(req.edit_ids, admin_name, db_id=req.db_id)
    log_admin_action("bulk_approve_pending", None, f"Bulk approved {res['success_count']} edits by {admin_name}")
    return {"status": "success", "result": res, "message": f"{res['success_count']} संपादन स्वीकृत व लागू कर दिए गए।"}


@app.post("/api/admin/pending-edits/bulk-reject", dependencies=[Depends(verify_admin_access)])
def bulk_reject_pending_edits_endpoint(req: BulkPendingActionRequest, request: Request):
    """Rejects multiple operator pending edits in bulk."""
    from backend.modules.pending_edits_manager import PendingEditsManager
    curr_user = get_current_user_optional(request)
    admin_name = curr_user.get("username", "admin") if curr_user else "admin"
    res = PendingEditsManager.bulk_reject(req.edit_ids, admin_name, reason=req.reason, db_id=req.db_id)
    log_admin_action("bulk_reject_pending", None, f"Bulk rejected {res['success_count']} edits by {admin_name}")
    return {"status": "success", "result": res, "message": f"{res['success_count']} संपादन अस्वीकृत कर दिए गए।"}


@app.post("/api/jobs/{job_id}/rescan-correct", dependencies=[Depends(verify_operator_or_admin_access)])
def api_job_rescan_correct(job_id: str):
    """
    Runs Local AI Quality Inspection and Second-Pass Error Correction on an active job's records.
    """
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")

    job = JOBS_DB[job_id]
    if not job.records:
        raise HTTPException(status_code=400, detail="सुधारने के लिए कोई रिकॉर्ड उपलब्ध नहीं है।")

    res = DualPassErrorCorrector.process_records_dual_pass(job.records)
    job.records = res["records"]
    job.perfect_first_pass = res.get("perfect_first_pass", 0)
    job.errors_corrected = res.get("errors_corrected", 0)
    job.corrections_detail = res.get("corrections_detail", [])

    # Re-generate Excel with corrected records
    if job.excel_path:
        try:
            ExcelBuilder.generate_excel(
                records=job.records,
                output_path=job.excel_path,
                assembly_name=job.assembly_name,
                part_no=job.part_number,
                polling_station=job.polling_station,
                filename_source=job.filename
            )
        except Exception:
            pass

    # Save to active target database
    try:
        db_res = VoterDatabase.save_voters(
            records=job.records,
            source_file=job.filename,
            assembly=job.assembly_name,
            part_no=job.part_number,
            polling_station=job.polling_station
        )
    except Exception:
        pass

    log_admin_action(
        "rescan_correct_job",
        None,
        f"Job {job_id[:8]}: {res['errors_corrected']} errors corrected by Local AI"
    )

    return {
        "status": "success",
        "job_id": job_id,
        "perfect_first_pass": res.get("perfect_first_pass", 0),
        "ai_evaluated_defective": res.get("ai_evaluated_defective", 0),
        "errors_corrected": res.get("errors_corrected", 0),
        "corrections_detail": res.get("corrections_detail", []),
        "summary_message": res.get("summary_message", ""),
        "records": job.records
    }


# =============================================================================
# OCR गहन सत्यापन प्रणाली (Deep Spot-Check Verification System)
# =============================================================================

class SpotCheckRequest(BaseModel):
    serial_numbers: List[int] = Field(..., description="जांचने के लिए क्रमांक संख्याओं की सूची (e.g. [660, 826])")


class DbSpotCheckRequest(BaseModel):
    part_no: str = Field(..., description="भाग संख्या (e.g. '20')")
    serial_numbers: List[int] = Field(..., description="जांचने के लिए क्रमांक संख्याओं की सूची")
    db_id: Optional[str] = Field(None, description="डेटाबेस ID (optional)")


@app.post("/api/spot-check/{job_id}", dependencies=[Depends(verify_operator_or_admin_access)])
def api_spot_check_job_voters(job_id: str, req: SpotCheckRequest, request: Request):
    """
    गहन OCR सत्यापन प्रणाली (Deep Spot-Check Verification):
    1. मूल PDF से कार्ड इमेज निकालता है (High-DPI Render)
    2. ताज़ा स्वतंत्र OCR री-स्कैन करता है (Independent Fresh Re-Scan)
    3. मूल निकाले गए डेटा बनाम ताज़ा री-स्कैन की तुलना (Field-by-Field Comparison)
    4. प्रति-फ़ील्ड मिलान विश्वास स्कोर (Per-Field Match Confidence)
    5. विसंगतियों को हाइलाइट करता है (Discrepancy Highlighting)
    6. कार्ड इमेज Base64 के रूप में वापस करता है (Visual Verification)
    """
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")

    job = JOBS_DB[job_id]
    if not job.records:
        raise HTTPException(status_code=400, detail="कोई रिकॉर्ड उपलब्ध नहीं है।")

    pdf_path = JOB_PDF_MAP.get(job_id) or getattr(job, 'filepath', None)
    has_pdf = bool(pdf_path and os.path.exists(pdf_path))

    results = []
    for target_serial in req.serial_numbers:
        # Find record with matching serial_no
        found_rec = None
        found_idx = None
        for idx, r in enumerate(job.records):
            if r.serial_no == target_serial:
                found_rec = r
                found_idx = idx
                break

        if not found_rec:
            results.append({
                "serial_no": target_serial,
                "status": "not_found",
                "message": f"क्रमांक {target_serial} इस PDF बैच में नहीं मिला।",
                "original_data": None,
                "rescan_data": None,
                "comparison": None,
                "card_image_base64": None,
                "overall_confidence": 0.0
            })
            continue

        # Build original data dict
        rec_dict = found_rec.dict() if hasattr(found_rec, "dict") else dict(found_rec)
        if is_operator_request(request):
            redact_caste_from_record(rec_dict)

        original_data = {
            "serial_no": found_rec.serial_no,
            "name": found_rec.name or "",
            "relation_type": found_rec.relation_type or "",
            "relation_name": found_rec.relation_name or "",
            "house_no": found_rec.house_no or "",
            "age": found_rec.age,
            "gender": found_rec.gender or "",
            "epic_no": found_rec.epic_no or "",
            "page_no": found_rec.page_no or 1,
            "is_deleted": found_rec.is_deleted,
        }

        # Perform independent re-scan if PDF is available
        rescan_data = None
        card_image_b64 = None
        comparison = []
        overall_confidence = 0.0

        if has_pdf:
            try:
                import io
                import base64
                import pymupdf as fitz
                from PIL import Image, ImageEnhance

                page_no = found_rec.page_no or 1
                page_idx = max(0, page_no - 1)

                doc = fitz.open(pdf_path)
                if page_idx < len(doc):
                    page = doc[page_idx]
                    pix = page.get_pixmap(dpi=300)  # Higher DPI for verification
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    w, h = img.size
                    doc.close()

                    # Calculate card position on page
                    top_margin = int(h * HEADER_FRACTION)
                    bottom_margin = int(h * FOOTER_FRACTION)
                    left_margin = int(w * LEFT_MARGIN_FRACTION)
                    right_margin = int(w * RIGHT_MARGIN_FRACTION)
                    usable_width = w - left_margin - right_margin
                    col_width_px = usable_width / float(NUM_COLUMNS)
                    card_height_px = (h - top_margin - bottom_margin) / 10.0

                    # Determine card's grid position from page records
                    page_records = [r for r in job.records if (r.page_no or 1) == page_no]
                    card_idx_on_page = 0
                    for ip, pr in enumerate(page_records):
                        if pr.serial_no == found_rec.serial_no:
                            card_idx_on_page = ip
                            break

                    row = card_idx_on_page // NUM_COLUMNS
                    col = card_idx_on_page % NUM_COLUMNS

                    col_x0 = int(left_margin + col * col_width_px)
                    col_x1 = int(left_margin + (col + 1) * col_width_px)
                    card_y0 = max(0, int(top_margin + row * card_height_px) - 3)
                    card_y1 = min(h, int(top_margin + (row + 1) * card_height_px) + 3)

                    # Crop the individual voter card
                    card_crop = img.crop((col_x0, card_y0, col_x1, card_y1))

                    # Convert card image to base64 for frontend display
                    buf = io.BytesIO()
                    card_crop.save(buf, format="PNG", optimize=True)
                    card_image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

                    # Perform independent fresh OCR re-scan on this card
                    cw, ch = card_crop.size
                    text_crop = card_crop.crop((0, 0, int(cw * 0.74), ch))
                    rescan_hin = OCRExtractor._ocr_image(text_crop, lang="hin", psm=6)
                    rescan_eng = OCRExtractor._ocr_image(text_crop, lang="eng", psm=6)
                    combined_rescan = f"{rescan_hin}\n{rescan_eng}"

                    # Parse fresh re-scan results
                    rescan_voter = UPFieldParser.parse_single_voter_box(
                        box_text=combined_rescan,
                        default_serial=found_rec.serial_no,
                        page_no=page_no,
                        metadata=None,
                        box_eng=rescan_eng
                    )

                    # Also re-scan EPIC with high-precision targeted scan
                    epic_x0 = max(0, int(cw * 0.25))
                    epic_y1 = min(ch, int(ch * 0.32))
                    epic_crop = card_crop.crop((epic_x0, 0, cw, epic_y1))
                    epic_gray = epic_crop.convert("L")
                    epic_enh = ImageEnhance.Contrast(epic_gray).enhance(2.0)
                    rescan_epic_txt = OCRExtractor._ocr_image(
                        epic_enh, lang="eng", psm=7,
                        whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/"
                    ).strip()
                    rescan_epic_clean = clean_epic_no(rescan_epic_txt)

                    if rescan_voter:
                        rescan_data = {
                            "name": rescan_voter.name or "",
                            "relation_type": rescan_voter.relation_type or "",
                            "relation_name": rescan_voter.relation_name or "",
                            "house_no": rescan_voter.house_no or "",
                            "age": rescan_voter.age,
                            "gender": rescan_voter.gender or "",
                            "epic_no": rescan_epic_clean or rescan_voter.epic_no or "",
                        }
                    else:
                        rescan_data = {
                            "name": "",
                            "relation_type": "",
                            "relation_name": "",
                            "house_no": "",
                            "age": None,
                            "gender": "",
                            "epic_no": rescan_epic_clean or "",
                        }

                    # Field-by-field comparison with confidence scoring
                    def _field_similarity(orig, rescan, field_name):
                        """Computes match confidence between original and re-scanned value."""
                        if field_name == "age":
                            if orig is None and rescan is None:
                                return {"match": True, "confidence": 1.0, "status": "✅ दोनों में आयु अनुपलब्ध"}
                            if orig is None or rescan is None:
                                return {"match": False, "confidence": 0.3, "status": "⚠️ एक में आयु मिली, दूसरे में नहीं"}
                            if orig == rescan:
                                return {"match": True, "confidence": 1.0, "status": "✅ आयु बिल्कुल मेल खाती है"}
                            diff = abs(orig - rescan)
                            if diff <= 1:
                                return {"match": True, "confidence": 0.90, "status": f"✅ ~मेल (अंतर: {diff} वर्ष)"}
                            return {"match": False, "confidence": max(0.1, 1.0 - diff * 0.05), "status": f"❌ विसंगति (मूल: {orig}, री-स्कैन: {rescan})"}

                        orig_str = str(orig or "").strip()
                        rescan_str = str(rescan or "").strip()

                        if not orig_str and not rescan_str:
                            return {"match": True, "confidence": 1.0, "status": "✅ दोनों रिक्त"}
                        if not orig_str or not rescan_str:
                            return {"match": False, "confidence": 0.3, "status": "⚠️ एक में डेटा मिला, दूसरे में नहीं"}
                        if orig_str == rescan_str:
                            return {"match": True, "confidence": 1.0, "status": "✅ शत-प्रतिशत मेल"}

                        # Partial match check (for Hindi names that may differ slightly)
                        common_chars = sum(1 for c in orig_str if c in rescan_str)
                        max_len = max(len(orig_str), len(rescan_str))
                        char_sim = common_chars / max_len if max_len > 0 else 0

                        if char_sim >= 0.85:
                            return {"match": True, "confidence": round(char_sim, 2), "status": f"✅ लगभग मेल ({int(char_sim*100)}% समानता)"}
                        elif char_sim >= 0.60:
                            return {"match": False, "confidence": round(char_sim, 2), "status": f"⚠️ आंशिक मेल ({int(char_sim*100)}% समानता)"}
                        else:
                            return {"match": False, "confidence": round(char_sim, 2), "status": f"❌ विसंगति ({int(char_sim*100)}% समानता)"}

                    # Compare each field
                    fields_to_compare = [
                        ("name", "मतदाता का नाम", original_data["name"], rescan_data["name"]),
                        ("relation_name", "पिता/पति का नाम", original_data["relation_name"], rescan_data["relation_name"]),
                        ("epic_no", "EPIC नंबर", original_data["epic_no"], rescan_data["epic_no"]),
                        ("house_no", "मकान संख्या", original_data["house_no"], rescan_data["house_no"]),
                        ("age", "आयु", original_data["age"], rescan_data["age"]),
                        ("gender", "लिंग", original_data["gender"], rescan_data["gender"]),
                    ]

                    total_confidence = 0.0
                    field_weights = {"name": 0.25, "relation_name": 0.20, "epic_no": 0.25, "house_no": 0.10, "age": 0.10, "gender": 0.10}

                    for f_key, f_label, orig_val, rescan_val in fields_to_compare:
                        sim = _field_similarity(orig_val, rescan_val, f_key)
                        comparison.append({
                            "field_key": f_key,
                            "field_label": f_label,
                            "original_value": str(orig_val) if orig_val is not None else "",
                            "rescan_value": str(rescan_val) if rescan_val is not None else "",
                            "match": sim["match"],
                            "confidence": sim["confidence"],
                            "status": sim["status"]
                        })
                        total_confidence += sim["confidence"] * field_weights.get(f_key, 0.1)

                    overall_confidence = round(min(1.0, total_confidence / sum(field_weights.values())), 3)

                    # EPIC format validation
                    epic_valid_orig, epic_defect_orig, _ = is_valid_epic_format(original_data["epic_no"])
                    epic_valid_rescan, epic_defect_rescan, _ = is_valid_epic_format(rescan_data.get("epic_no", ""))

                    # AI Quality Assessment
                    eval_result = LocalScanQualityAI.evaluate_record_quality(rec_dict)

                else:
                    doc.close()

            except Exception as ex:
                print(f"[SPOT-CHECK] Error processing serial {target_serial}: {ex}")

        # If no PDF re-scan was possible, still provide AI quality assessment
        if not rescan_data:
            eval_result = LocalScanQualityAI.evaluate_record_quality(rec_dict)
            overall_confidence = eval_result["score"] / 100.0

        results.append({
            "serial_no": target_serial,
            "status": "verified",
            "message": f"क्रमांक {target_serial} का गहन सत्यापन सम्पन्न।",
            "original_data": original_data,
            "rescan_data": rescan_data,
            "comparison": comparison,
            "card_image_base64": card_image_b64,
            "overall_confidence": overall_confidence,
            "ai_quality_score": eval_result.get("score", 0) if 'eval_result' in dir() or 'eval_result' in locals() else None,
            "ai_defects": eval_result.get("defects", []) if 'eval_result' in locals() else [],
            "record": rec_dict,
            "pdf_available": has_pdf
        })

    # Overall summary
    verified_count = sum(1 for r in results if r["status"] == "verified")
    not_found_count = sum(1 for r in results if r["status"] == "not_found")
    avg_confidence = sum(r["overall_confidence"] for r in results if r["status"] == "verified") / max(1, verified_count)
    high_confidence = sum(1 for r in results if r["status"] == "verified" and r["overall_confidence"] >= 0.85)
    low_confidence = sum(1 for r in results if r["status"] == "verified" and r["overall_confidence"] < 0.60)

    return {
        "status": "success",
        "job_id": job_id,
        "part_no": job.part_number,
        "total_requested": len(req.serial_numbers),
        "verified_count": verified_count,
        "not_found_count": not_found_count,
        "avg_confidence": round(avg_confidence, 3),
        "high_confidence_count": high_confidence,
        "low_confidence_count": low_confidence,
        "results": results,
        "summary_message": (
            f"गहन सत्यापन: {verified_count} मतदाताओं का सत्यापन पूर्ण। "
            f"औसत विश्वसनीयता: {int(avg_confidence * 100)}% | "
            f"उच्च विश्वसनीयता: {high_confidence} | "
            f"निम्न विश्वसनीयता: {low_confidence}"
            + (f" | {not_found_count} नहीं मिले" if not_found_count > 0 else "")
        )
    }


@app.get("/api/spot-check/card-image/{job_id}/{serial_no}", dependencies=[Depends(verify_operator_or_admin_access)])
def api_spot_check_card_image(job_id: str, serial_no: int):
    """
    मूल PDF से विशिष्ट मतदाता कार्ड की उच्च-गुणवत्ता इमेज निकालकर भेजता है।
    Renders high-quality voter card image from original PDF for visual verification.
    """
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")

    job = JOBS_DB[job_id]
    pdf_path = JOB_PDF_MAP.get(job_id) or getattr(job, 'filepath', None)
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=400, detail="मूल PDF फाइल उपलब्ध नहीं है।")

    found_rec = None
    for r in job.records:
        if r.serial_no == serial_no:
            found_rec = r
            break

    if not found_rec:
        raise HTTPException(status_code=404, detail=f"क्रमांक {serial_no} नहीं मिला।")

    try:
        import io
        import pymupdf as fitz

        page_no = found_rec.page_no or 1
        page_idx = max(0, page_no - 1)

        doc = fitz.open(pdf_path)
        if page_idx >= len(doc):
            doc.close()
            raise HTTPException(status_code=400, detail=f"पृष्ठ {page_no} उपलब्ध नहीं है।")

        page = doc[page_idx]
        pix = page.get_pixmap(dpi=350)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        w, h = img.size
        doc.close()

        top_margin = int(h * HEADER_FRACTION)
        bottom_margin = int(h * FOOTER_FRACTION)
        left_margin = int(w * LEFT_MARGIN_FRACTION)
        right_margin = int(w * RIGHT_MARGIN_FRACTION)
        usable_width = w - left_margin - right_margin
        col_width_px = usable_width / float(NUM_COLUMNS)
        card_height_px = (h - top_margin - bottom_margin) / 10.0

        page_records = [r for r in job.records if (r.page_no or 1) == page_no]
        card_idx_on_page = 0
        for ip, pr in enumerate(page_records):
            if pr.serial_no == found_rec.serial_no:
                card_idx_on_page = ip
                break

        row = card_idx_on_page // NUM_COLUMNS
        col = card_idx_on_page % NUM_COLUMNS

        col_x0 = int(left_margin + col * col_width_px)
        col_x1 = int(left_margin + (col + 1) * col_width_px)
        card_y0 = max(0, int(top_margin + row * card_height_px) - 2)
        card_y1 = min(h, int(top_margin + (row + 1) * card_height_px) + 2)

        card_crop = img.crop((col_x0, card_y0, col_x1, card_y1))
        buf = io.BytesIO()
        card_crop.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        return Response(content=buf.getvalue(), media_type="image/png")

    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"कार्ड इमेज निकालने में त्रुटि: {str(ex)}")


@app.post("/api/database/spot-check", dependencies=[Depends(verify_operator_or_admin_access)])
def api_database_spot_check(req: DbSpotCheckRequest, request: Request):
    """
    डेटाबेस में सहेजे गए मतदाताओं का भाग संख्या + क्रमांक से गहन सत्यापन।
    Deep spot-check verification for voters saved in database by Part No + Serial No.
    Performs AI quality assessment and returns detailed inspection results.
    """
    clean_part = req.part_no.strip()
    if not clean_part:
        raise HTTPException(status_code=400, detail="भाग संख्या आवश्यक है।")

    is_op = is_operator_request(request)
    results = []

    with VoterDatabase.get_connection(db_id=req.db_id, purpose="read") as conn:
        cursor = conn.cursor()

        for target_serial in req.serial_numbers:
            cursor.execute(
                "SELECT * FROM voters WHERE part_no = ? AND serial_no = ?",
                (clean_part, target_serial)
            )
            row = cursor.fetchone()
            is_panchayat = False

            if not row:
                # Also check panchayat_voters table by ward_no, part_no, or body_name
                try:
                    cursor.execute(
                        "SELECT * FROM panchayat_voters WHERE (ward_no = ? OR part_no = ? OR body_name = ?) AND serial_no = ?",
                        (clean_part, clean_part, clean_part, target_serial)
                    )
                    row = cursor.fetchone()
                    if row:
                        is_panchayat = True
                except Exception:
                    pass

            if not row:
                results.append({
                    "serial_no": target_serial,
                    "part_no": clean_part,
                    "status": "not_found",
                    "message": f"भाग/वार्ड {clean_part} में क्रमांक {target_serial} नहीं मिला।",
                    "original_data": None,
                    "ai_quality": None,
                    "overall_confidence": 0.0
                })
                continue

            rec_dict = dict(row)

            # Check if PDF exists to render visual card image
            card_b64 = None
            pdf_fname = rec_dict.get("pdf_filename") or rec_dict.get("source_file")
            if pdf_fname:
                for candidate_dir in [UPLOAD_DIR, DATA_DIR, Path("uploads")]:
                    cand = candidate_dir / pdf_fname
                    if cand.exists():
                        try:
                            import pymupdf as fitz
                            doc = fitz.open(str(cand))
                            p_idx = max(0, (rec_dict.get("page_no") or 1) - 1)
                            if p_idx < len(doc):
                                page = doc[p_idx]
                                rects = page.search_for(str(target_serial))
                                if rects:
                                    r = rects[0]
                                    card_rect = fitz.Rect(max(0, r.x0 - 8), max(0, r.y0 - 4), min(page.rect.width, r.x0 + 200), min(page.rect.height, r.y0 + 72))
                                    pix = page.get_pixmap(clip=card_rect, dpi=250)
                                    import io, base64
                                    card_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                            doc.close()
                        except Exception:
                            pass
                        break

            # Build original data
            original_data = {
                "id": rec_dict.get("id"),
                "serial_no": rec_dict.get("serial_no"),
                "name": rec_dict.get("name", ""),
                "relation_type": rec_dict.get("relation_type", ""),
                "relation_name": rec_dict.get("relation_name", ""),
                "house_no": rec_dict.get("house_no", ""),
                "age": rec_dict.get("age"),
                "gender": rec_dict.get("gender", ""),
                "epic_no": rec_dict.get("epic_no", ""),
                "page_no": rec_dict.get("page_no"),
                "assembly": rec_dict.get("assembly", ""),
                "part_no": rec_dict.get("part_no", ""),
                "section_no": rec_dict.get("section_no", ""),
            }

            if is_op:
                redact_caste_from_record(original_data)

            # Run AI Quality Assessment
            eval_result = LocalScanQualityAI.evaluate_record_quality(rec_dict)

            # EPIC validation
            epic_valid, epic_defect, epic_meta = is_valid_epic_format(rec_dict.get("epic_no", ""))

            # Detailed field-level checks
            field_checks = []

            # Name check
            name = rec_dict.get("name", "")
            name_ok = bool(name and len(name) >= 2 and len(re.findall(r'[\u0900-\u097F]', name)) >= 2)
            field_checks.append({
                "field": "name", "label": "मतदाता का नाम", "value": name,
                "ok": name_ok,
                "status": "✅ वैध हिन्दी नाम" if name_ok else "❌ नाम अपूर्ण या अमान्य"
            })

            # Relation name check
            rel_name = rec_dict.get("relation_name", "")
            rel_ok = bool(rel_name and len(rel_name) >= 2)
            field_checks.append({
                "field": "relation_name", "label": "पिता/पति का नाम", "value": rel_name,
                "ok": rel_ok,
                "status": "✅ वैध" if rel_ok else "⚠️ अपूर्ण"
            })

            # EPIC check
            field_checks.append({
                "field": "epic_no", "label": "EPIC नंबर", "value": rec_dict.get("epic_no", ""),
                "ok": epic_valid,
                "status": f"✅ {epic_defect}" if epic_valid else f"❌ {epic_defect}"
            })

            # Age check
            age = rec_dict.get("age")
            age_ok = age is not None and 18 <= age <= 120
            field_checks.append({
                "field": "age", "label": "आयु", "value": str(age) if age else "",
                "ok": age_ok,
                "status": f"✅ वैध ({age} वर्ष)" if age_ok else f"⚠️ अमान्य ({age})" if age else "⚠️ आयु अनुपलब्ध"
            })

            # Gender check
            gender = rec_dict.get("gender", "")
            gender_ok = gender in ("पुरुष", "महिला", "अन्य")
            field_checks.append({
                "field": "gender", "label": "लिंग", "value": gender,
                "ok": gender_ok,
                "status": f"✅ {gender}" if gender_ok else "⚠️ लिंग अस्पष्ट"
            })

            # House check
            house = rec_dict.get("house_no", "")
            field_checks.append({
                "field": "house_no", "label": "मकान संख्या", "value": house,
                "ok": bool(house),
                "status": f"✅ {house}" if house else "⚠️ मकान नंबर रिक्त"
            })

            overall_conf = eval_result["score"] / 100.0

            results.append({
                "serial_no": target_serial,
                "part_no": clean_part,
                "status": "verified",
                "message": f"भाग {clean_part} क्रमांक {target_serial} का गहन सत्यापन सम्पन्न।",
                "original_data": original_data,
                "ai_quality": {
                    "score": eval_result["score"],
                    "is_perfect": eval_result["is_perfect"],
                    "defects_count": eval_result["defects_count"],
                    "defects": eval_result["defects"],
                    "recommendation": eval_result["recommendation"]
                },
                "field_checks": field_checks,
                "epic_validation": {
                    "is_valid": epic_valid,
                    "defect": epic_defect,
                    "format_type": epic_meta.get("type", "UNKNOWN")
                },
                "card_image_base64": card_b64,
                "is_panchayat": is_panchayat,
                "overall_confidence": round(overall_conf, 3)
            })

    # Summary
    verified_count = sum(1 for r in results if r["status"] == "verified")
    not_found_count = sum(1 for r in results if r["status"] == "not_found")
    avg_conf = sum(r["overall_confidence"] for r in results if r["status"] == "verified") / max(1, verified_count)

    return {
        "status": "success",
        "part_no": clean_part,
        "total_requested": len(req.serial_numbers),
        "verified_count": verified_count,
        "not_found_count": not_found_count,
        "avg_confidence": round(avg_conf, 3),
        "results": results,
        "summary_message": (
            f"भाग {clean_part} गहन सत्यापन: {verified_count} मतदाताओं की जांच पूर्ण। "
            f"औसत गुणवत्ता: {int(avg_conf * 100)}%"
            + (f" | {not_found_count} नहीं मिले" if not_found_count > 0 else "")
        )
    }


@app.post("/api/jobs/{job_id}/rescan-voter-epic/{record_index}", dependencies=[Depends(verify_operator_or_admin_access)])
def api_rescan_job_voter_epic(job_id: str, record_index: int, request: Request):
    """
    Executes a targeted, single-pass micro-OCR re-scan of the EPIC number for a specific voter card.
    AI evaluates the result: updates the record if valid, or flags with an admin warning if unresolvable.
    """
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
        
    job = JOBS_DB[job_id]
    if not job.records or record_index < 0 or record_index >= len(job.records):
        raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")
        
    rec = job.records[record_index]
    pdf_path = JOB_PDF_MAP.get(job_id) or getattr(job, 'filepath', None)
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=400, detail="मूल पीडीएफ फाइल डिस्क पर उपलब्ध नहीं है।")

    # Determine card index on physical page
    page_no = rec.page_no or 1
    page_records = [r for r in job.records if (r.page_no or 1) == page_no]
    card_idx = 0
    for idx_p, pr in enumerate(page_records):
        if pr.serial_no == rec.serial_no:
            card_idx = idx_p
            break
            
    old_epic = rec.epic_no or ""
    success, new_epic, msg = OCRExtractor.rescan_single_voter_epic(
        pdf_path=pdf_path,
        page_no=page_no,
        card_index=card_idx,
        current_epic=old_epic
    )
    
    if new_epic and new_epic != old_epic:
        rec.epic_no = new_epic
        
    # Re-validate with AI
    is_valid, defect_reason, _ = is_valid_epic_format(rec.epic_no)
    if is_valid:
        # Clear EPIC defect warnings if any
        if rec.warning_message and "EPIC" in rec.warning_message:
            non_epic = [w for w in rec.warning_message.split(" | ") if "EPIC" not in w]
            rec.warning_message = " | ".join(non_epic) if non_epic else None
            if not rec.warning_message:
                rec.has_warning = False
    else:
        rec.has_warning = True
        epic_warn = f"चेतावनी: EPIC अमान्य प्रारूप ({defect_reason}) - एडमिन मैन्युअल जांच अपेक्षित"
        if rec.warning_message:
            if "EPIC" not in rec.warning_message:
                rec.warning_message = f"{rec.warning_message} | {epic_warn}"
        else:
            rec.warning_message = epic_warn

    log_admin_action(
        "rescan_voter_epic",
        None,
        f"Job {job_id[:8]} Serial {rec.serial_no}: EPIC '{old_epic}' -> '{rec.epic_no}' (Valid={is_valid})"
    )

    rec_dict = rec.dict() if hasattr(rec, "dict") else dict(rec)
    if is_operator_request(request):
        redact_caste_from_record(rec_dict)

    return {
        "status": "success",
        "record_index": record_index,
        "serial_no": rec.serial_no,
        "old_epic": old_epic,
        "new_epic": rec.epic_no,
        "is_valid": is_valid,
        "defect_reason": defect_reason if not is_valid else None,
        "message": msg,
        "record": rec_dict
    }


@app.post("/api/database/voter/{record_id}/rescan-epic", dependencies=[Depends(verify_admin_access)])
def api_database_voter_rescan_epic(record_id: int):
    """
    Executes a targeted, single-pass micro-OCR re-scan of the EPIC number for a specific voter in database.
    """
    with VoterDatabase.get_connection(purpose="write") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM voters WHERE id = ?;", (record_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")

        source_file = row["source_file"]
        page_no = row["page_no"] or 1
        serial_no = row["serial_no"]
        old_epic = row["epic_no"] or ""

        # Locate PDF file in uploads
        pdf_path = None
        if source_file:
            cand1 = UPLOAD_DIR / source_file
            if cand1.exists():
                pdf_path = str(cand1)
            else:
                for f in UPLOAD_DIR.glob("*.pdf"):
                    if f.name == os.path.basename(source_file):
                        pdf_path = str(f)
                        break

        if not pdf_path:
            raise HTTPException(status_code=400, detail=f"मूल पीडीएफ फाइल '{source_file}' सर्वर पर नहीं मिली।")

        cursor.execute("SELECT id, serial_no FROM voters WHERE page_no = ? AND part_no = ? ORDER BY serial_no ASC;", 
                       (page_no, row["part_no"]))
        page_rows = cursor.fetchall()
        card_idx = 0
        for ip, pr in enumerate(page_rows):
            if pr["id"] == record_id:
                card_idx = ip
                break

        success, new_epic, msg = OCRExtractor.rescan_single_voter_epic(
            pdf_path=pdf_path,
            page_no=page_no,
            card_index=card_idx,
            current_epic=old_epic
        )

        is_valid = False
        defect_reason = None
        if new_epic:
            is_valid, defect_reason, _ = is_valid_epic_format(new_epic)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("UPDATE voters SET epic_no = ?, updated_at = ? WHERE id = ?;", 
                           (new_epic, now_str, record_id))

        log_admin_action(
            "rescan_db_voter_epic",
            None,
            f"Voter ID {record_id} Serial {serial_no}: EPIC '{old_epic}' -> '{new_epic}' (Valid={is_valid})"
        )

        return {
            "status": "success",
            "record_id": record_id,
            "serial_no": serial_no,
            "old_epic": old_epic,
            "new_epic": new_epic or old_epic,
            "is_valid": is_valid,
            "defect_reason": defect_reason,
            "message": msg
        }


def find_uploaded_pdf(source_file: str) -> Optional[str]:
    """
    Locates the original uploaded PDF on disk, handling UUID prefixes
    (e.g. {UUID}_{source_file} or {UUID}_{UUID}_{source_file}).
    Never falls back to dummy sample files.
    """
    if not source_file:
        return None
    base_src = os.path.basename(source_file).strip()

    search_dirs = [UPLOAD_DIR, DATA_DIR / "uploads", BASE_DIR / "uploads", BASE_DIR]

    # 1. Direct path check
    for d in search_dirs:
        if not d.exists():
            continue
        direct = d / base_src
        if direct.exists() and direct.is_file():
            return str(direct)

    # 2. Match in directories with UUID prefix or substring
    for d in search_dirs:
        if not d.exists():
            continue
        try:
            pdf_files = sorted(list(d.glob("*.pdf")), key=lambda p: p.stat().st_mtime, reverse=True)
            for f in pdf_files:
                if f.name == base_src:
                    return str(f)
                if f.name.endswith("_" + base_src) or f.name.endswith("-" + base_src):
                    return str(f)
            # Match stem substring
            stem = Path(base_src).stem
            if stem and len(stem) > 4:
                for f in pdf_files:
                    if stem in f.name:
                        return str(f)
        except Exception:
            pass

    return None


class VoterContextCropRequest(BaseModel):
    job_id: Optional[str] = None
    record_index: Optional[int] = None
    voter_id: Optional[int] = None
    rescan: bool = False
    page_no: Optional[int] = None
    serial_no: Optional[int] = None


@app.get("/api/voter-context-crop")
@app.post("/api/voter-context-crop")
def api_voter_context_crop(
    job_id: Optional[str] = Query(None),
    record_index: Optional[int] = Query(None),
    voter_id: Optional[int] = Query(None),
    rescan: bool = Query(False),
    page_no: Optional[int] = Query(None),
    serial_no: Optional[int] = Query(None),
    payload: Optional[VoterContextCropRequest] = None,
    request: Request = None
):
    """
    Extracts the cropped real PDF context photo (with target voter card highlighted and surrounding cards)
    and optionally executes high-quality AI re-scan and field auto-fill for the edit modal.
    Supports both active scan jobs (job_id + record_index) and persistent database voters (voter_id).
    """
    if payload:
        if payload.job_id: job_id = payload.job_id
        if payload.record_index is not None: record_index = payload.record_index
        if payload.voter_id is not None: voter_id = payload.voter_id
        if payload.rescan: rescan = payload.rescan
        if payload.page_no is not None: page_no = payload.page_no
        if payload.serial_no is not None: serial_no = payload.serial_no

    # Case 1: Database Voter ID
    if voter_id is not None:
        # Check standard voters table first
        with VoterDatabase.get_connection(purpose="read") as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM voters WHERE id = ?;", (voter_id,))
            row = cursor.fetchone()

        if row:
            source_file = row["source_file"] or ""
            target_page = row["page_no"] or 1
            target_serial = row["serial_no"]
            
            # Locate real uploaded PDF file (NO mock/sample fallback)
            pdf_path = find_uploaded_pdf(source_file)

            if not pdf_path or not os.path.exists(pdf_path):
                return {
                    "success": False,
                    "crop_image_base64": None,
                    "rescanned": None,
                    "message": f"मूल पीडीएफ फाइल '{source_file}' सर्वर पर उपलब्ध नहीं है। कृपया पीडीएफ पुनः अपलोड या स्कैन करें।"
                }

            # In ECI rolls, 30 voters per page in row-major order:
            card_idx = 0
            if target_serial is not None and target_serial > 0:
                card_idx = (target_serial - 1) % 30
            else:
                with VoterDatabase.get_connection(purpose="read") as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT id FROM voters WHERE page_no = ? AND part_no = ? ORDER BY serial_no ASC;",
                        (target_page, row["part_no"])
                    )
                    page_rows = cursor.fetchall()
                for ip, pr in enumerate(page_rows):
                    if pr["id"] == voter_id:
                        card_idx = ip
                        break

            res = OCRExtractor.get_voter_card_context_crop(
                pdf_path=pdf_path,
                page_no=target_page,
                card_index=card_idx,
                serial_no=target_serial,
                rescan=rescan
            )
            return res

        # Check panchayat voters table fallback
        try:
            from .modules.panchayat_service import PanchayatService
            p_voter = PanchayatService.get_voter(voter_id)
            if p_voter:
                p_res = PanchayatService.get_voter_crop_and_rescan(
                    voter_id=voter_id,
                    rescan=rescan,
                    uploads_dir=str(UPLOAD_DIR)
                )
                return p_res
        except Exception:
            pass

        return {
            "success": False,
            "crop_image_base64": None,
            "rescanned": None,
            "message": f"मतदाता रिकॉर्ड ID {voter_id} डेटाबेस में नहीं मिला।"
        }

    # Case 2: Active Job Scan Record
    if job_id:
        job = JOBS_DB.get(job_id)
        pdf_path = JOB_PDF_MAP.get(job_id)
        if not pdf_path or not os.path.exists(pdf_path):
            if job and job.filename:
                pdf_path = find_uploaded_pdf(job.filename)

        if not pdf_path or not os.path.exists(pdf_path):
            return {
                "success": False,
                "crop_image_base64": None,
                "rescanned": None,
                "message": "सक्रिय स्कैन की मूल पीडीएफ फाइल उपलब्ध नहीं है।"
            }

        rec = None
        card_idx = 0
        target_page = page_no or 1
        target_serial = serial_no

        if job and job.records:
            if record_index is not None and 0 <= record_index < len(job.records):
                rec = job.records[record_index]
            else:
                for r in job.records:
                    if getattr(r, "job_index", None) == record_index or getattr(r, "serial_no", None) == record_index:
                        rec = r
                        break

            if rec:
                target_page = rec.page_no or target_page
                target_serial = rec.serial_no
                if rec.serial_no and rec.serial_no > 0:
                    card_idx = (rec.serial_no - 1) % 30
                else:
                    page_voters = [r for r in job.records if r.page_no == target_page]
                    if rec in page_voters:
                        card_idx = page_voters.index(rec)
                    elif record_index is not None:
                        card_idx = record_index % 30
        elif target_serial and target_serial > 0:
            card_idx = (target_serial - 1) % 30
        elif record_index is not None:
            card_idx = record_index % 30

        res = OCRExtractor.get_voter_card_context_crop(
            pdf_path=pdf_path,
            page_no=target_page,
            card_index=card_idx,
            serial_no=target_serial,
            rescan=rescan
        )
        return res

    return {
        "success": False,
        "crop_image_base64": None,
        "rescanned": None,
        "message": "अनुरोध में job_id या voter_id प्रदान करना आवश्यक है।"
    }


@app.post("/api/database/rescan-correct-part", dependencies=[Depends(verify_operator_or_admin_access)])
def api_database_rescan_correct_part(req: RescanPartRequest, request: Request):
    """
    Runs Local AI Quality Inspection and Second-Pass Error Correction on all voters of a part in database.
    Allows both Admins and Data Operators to clean and standardize voter records of a part.
    """
    clean_part = req.part_no.strip()
    if not clean_part:
        raise HTTPException(status_code=400, detail="भाग संख्या आवश्यक है।")

    curr_user = get_current_user_optional(request)
    username = curr_user.get("username", "admin") if curr_user else "admin"

    with VoterDatabase.get_connection(db_id=req.db_id, purpose="write") as conn:
        cursor = conn.cursor()
        where_sql = "WHERE part_no = ?"
        params = [clean_part]
        if req.assembly and req.assembly.strip() and req.assembly.strip() != "all":
            where_sql += " AND assembly = ?"
            params.append(req.assembly.strip())

        cursor.execute(f"SELECT * FROM voters {where_sql} ORDER BY serial_no ASC;", params)
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"भाग {clean_part} में कोई मतदाता रिकॉर्ड नहीं मिला।")

        records: List[VoterRecord] = []
        for row in rows:
            r = dict(row)
            rec = VoterRecord(
                id=r.get("id"),
                serial_no=r.get("serial_no"),
                name=r.get("name") or "",
                relation_type=r.get("relation_type") or "पिता",
                relation_name=r.get("relation_name") or "",
                house_no=r.get("house_no") or "",
                age=r.get("age"),
                gender=r.get("gender") or "पुरुष",
                epic_no=r.get("epic_no") or "",
                assembly=r.get("assembly") or "",
                part_no=r.get("part_no") or "",
                polling_station=r.get("polling_station") or "",
                section_no=r.get("section_no") or "",
                page_no=r.get("page_no"),
                source_file=r.get("source_file") or "",
                is_deleted=bool(r.get("is_deleted", 0)),
                deleted_reason=r.get("deleted_reason")
            )
            records.append(rec)

        # Run Local AI dual-pass correction
        res = DualPassErrorCorrector.process_records_dual_pass(records)
        corrected_records = res["records"]

        # Persist corrections back to database
        updated_count = 0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for cr in corrected_records:
            rec_id = getattr(cr, "id", None) or (cr.get("id") if isinstance(cr, dict) else None)
            if rec_id:
                name_val = cr.name if hasattr(cr, "name") else cr.get("name")
                rel_type_val = cr.relation_type if hasattr(cr, "relation_type") else cr.get("relation_type")
                rel_name_val = cr.relation_name if hasattr(cr, "relation_name") else cr.get("relation_name")
                house_val = cr.house_no if hasattr(cr, "house_no") else cr.get("house_no")
                age_val = cr.age if hasattr(cr, "age") else cr.get("age")
                gender_val = cr.gender if hasattr(cr, "gender") else cr.get("gender")
                epic_val = cr.epic_no if hasattr(cr, "epic_no") else cr.get("epic_no")

                cursor.execute("""
                    UPDATE voters SET
                        name = ?, relation_type = ?, relation_name = ?,
                        house_no = ?, age = ?, gender = ?, epic_no = ?,
                        updated_at = ?
                    WHERE id = ?;
                """, (
                    name_val, rel_type_val, rel_name_val,
                    house_val, age_val, gender_val, epic_val,
                    now_str, rec_id
                ))
                updated_count += 1
        conn.commit()

    log_admin_action(
        "rescan_correct_part",
        None,
        f"Part {clean_part}: {res['errors_corrected']} errors corrected in DB",
        username=username
    )

    return {
        "status": "success",
        "part_no": clean_part,
        "total_records": len(records),
        "perfect_first_pass": res.get("perfect_first_pass", 0),
        "errors_corrected": res.get("errors_corrected", 0),
        "corrections_detail": res.get("corrections_detail", []),
        "summary_message": res.get("summary_message", ""),
        "message": f"भाग {clean_part}: कुल {len(records)} मतदाताओं में से {res.get('errors_corrected', 0)} में स्वचालित सुधार किया गया।"
    }


# =============================================================================
# GEOGRAPHIC STREET SURVEY AUDIT EXPORT
# 3-Sheet Comprehensive Register (Survey + Form 6 Action List + Statistics)
# =============================================================================

class SurveyExportRequest(BaseModel):
    street: Optional[str] = ""
    zone: Optional[str] = None
    ward: Optional[str] = None
    min_age: int = 17
    filter_mode: str = "all"  # 'all', 'unregistered_only', 'form6_18', 'form6_17'


@app.post("/api/survey-audit/export-excel", dependencies=[Depends(verify_superadmin_only_access)])
def export_survey_audit_excel(payload: SurveyExportRequest):
    """
    Exports comprehensive 3-sheet street voter audit & Form 6 field register (.xlsx):
    - Sheet 1: सम्पूर्ण गली सर्वे (All surveyed houses & members with full voter match status)
    - Sheet 2: फॉर्म 6 फील्ड कार्य सूची (Targeted unregistered 18+ and 17+ list with BLO verification & signature fields)
    - Sheet 3: सांख्यिकी सारांश (Summary KPIs, registration rates, and age demographics)
    """
    clean_street = (payload.street or "").strip()
    street = clean_street if clean_street.upper() not in ("ALL", "ANY", "-- समस्त गलियाँ --") else None
    zone = payload.zone.strip() if payload.zone else None
    ward = payload.ward.strip() if payload.ward else None
    min_age = payload.min_age

    survey_res = PropertySurveySync.fetch_street_houses_and_members(
        street=street,
        zone=zone,
        ward=ward,
        min_age=min_age
    )
    if not survey_res.get("success"):
        raise HTTPException(status_code=502, detail=survey_res.get("error", "डेटा प्राप्त नहीं हो सका।"))

    all_voters = VoterDatabase.get_all_voters_for_audit()
    audit_res = PropertySurveySync.audit_street_voters(survey_res, all_voters)

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    # Styling presets
    font_family = "Arial"
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name=font_family, size=11, bold=True, color="FFFFFF")
    
    green_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    green_font = Font(name=font_family, size=10, bold=True, color="166534")
    
    red_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    red_font = Font(name=font_family, size=10, bold=True, color="991B1B")
    
    amber_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    amber_font = Font(name=font_family, size=10, bold=True, color="92400E")

    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1")
    )
    thick_bottom = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="medium", color="1E293B")
    )

    street_label = street or "समस्त गलियाँ"
    zone_label = f" (ज़ोन: {zone})" if zone else ""
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # -------------------------------------------------------------
    # SHEET 1: सम्पूर्ण गली सर्वे (Master Survey & Voter Audit)
    # -------------------------------------------------------------
    ws1 = wb.active
    ws1.title = "सम्पूर्ण गली सर्वे"
    ws1.views.sheetView[0].showGridLines = True

    # Title & Subtitle
    ws1.merge_cells("A1:M1")
    t1 = ws1["A1"]
    t1.value = f"डोर-टू-डोर मतदाता सत्यापन एवं सर्वे रजिस्टर - क्षेत्र: {street_label}{zone_label}"
    t1.font = Font(name=font_family, size=14, bold=True, color="1E3A8A")
    t1.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[1].height = 32

    ws1.merge_cells("A2:M2")
    s1 = ws1["A2"]
    s1.value = (
        f"कुल मकान: {audit_res['totalHouses']} | कुल 17+ सदस्य: {audit_res['totalEligibleMembers']} | "
        f"पंजीकृत मतदाता: {audit_res['totalRegistered']} | "
        f"फॉर्म 6 पात्र (18+): {audit_res['form6_18plusCount']} | "
        f"अग्रिम फॉर्म 6 (17+): {audit_res['form6_17plusCount']} | "
        f"रिपोर्ट जनरेट: {now_str}"
    )
    s1.font = Font(name=font_family, size=10, italic=True, color="475569")
    s1.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 22

    headers1 = [
        "क्र०", "मकान नं० (सर्वे)", "प्रॉपर्टी ID", "मुखिया/स्वामी",
        "सदस्य का नाम", "संबंधी (पिता/पति) का नाम", "आयु", "लिंग",
        "सम्बन्ध", "मोबाइल", "वोटर स्थिति", "वोटर लिस्ट विवरण (EPIC/भाग/क्रम/मकान)", "आवश्यक कार्यवाही"
    ]
    ws1.append([])
    ws1.append(headers1)
    ws1.row_dimensions[4].height = 28

    for col_idx in range(1, len(headers1) + 1):
        c = ws1.cell(row=4, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = thick_bottom

    row_num = 5
    counter = 1
    filter_mode = payload.filter_mode

    for h in audit_res.get("houses", []):
        h_no = h.get("houseNumber") or ""
        prop_id = h.get("propertyId") or ""
        owner = h.get("ownerName") or ""

        for m in h.get("eligibleMembers", []):
            is_reg = m.get("isRegistered", False)
            age = int(m.get("age") or 0)

            if filter_mode == "unregistered_only" and is_reg:
                continue
            if filter_mode == "form6_18" and (is_reg or age < 18):
                continue
            if filter_mode == "form6_17" and (is_reg or age != 17):
                continue

            v_info = m.get("voterRecord")
            if is_reg and v_info:
                v_str = f"EPIC: {v_info.get('epic_no')} | भाग: {v_info.get('part_no')} | क्रम: {v_info.get('serial_no')} | मकान: {v_info.get('voter_house')}"
            else:
                v_str = "वोटर लिस्ट में उपलब्ध नहीं"

            row_data = [
                counter,
                h_no,
                prop_id,
                owner,
                m.get("name") or "",
                m.get("fatherHusbandName") or "",
                age,
                m.get("gender") or "",
                m.get("relationship") or "",
                m.get("mobile") or "",
                m.get("statusLabel") or "",
                v_str,
                m.get("actionNeeded") or ""
            ]
            ws1.append(row_data)

            for c_idx in range(1, len(headers1) + 1):
                cell = ws1.cell(row=row_num, column=c_idx)
                cell.border = thin_border
                cell.font = Font(name=font_family, size=10)
                if c_idx in (1, 2, 7, 8, 10):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

                # Badge styles for status & action
                if c_idx in (11, 13):
                    if is_reg:
                        cell.fill = green_fill
                        cell.font = green_font
                    elif age >= 18:
                        cell.fill = red_fill
                        cell.font = red_font
                    else:
                        cell.fill = amber_fill
                        cell.font = amber_font

            row_num += 1
            counter += 1

    # Auto-fit columns for sheet 1
    for col in ws1.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in (1, 2, 3):
                continue
            val_str = str(cell.value or "")
            max_len = max(max_len, len(val_str))
        ws1.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # -------------------------------------------------------------
    # SHEET 2: फॉर्म 6 फील्ड कार्य सूची (Targeted Unregistered Field Verification Register)
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="फॉर्म 6 फील्ड कार्य सूची")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells("A1:M1")
    t2 = ws2["A1"]
    t2.value = f"डोर-टू-डोर नवीन मतदाता पंजीकरण (फॉर्म 6) फील्ड कार्य सूची - क्षेत्र: {street_label}{zone_label}"
    t2.font = Font(name=font_family, size=14, bold=True, color="991B1B")
    t2.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[1].height = 32

    ws2.merge_cells("A2:M2")
    s2 = ws2["A2"]
    s2.value = (
        f"केवल अपंजीकृत 18+ एवं 17+ सदस्य | कुल लक्ष्य: {audit_res['form6_18plusCount'] + audit_res['form6_17plusCount']} नागरिक | "
        f"BLO / सर्वेयर द्वारा भौतिक सत्यापन एवं हस्ताक्षर हेतु अधिकृत प्रपत्र | दिनांक: {now_str}"
    )
    s2.font = Font(name=font_family, size=10, italic=True, color="475569")
    s2.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[2].height = 22

    headers2 = [
        "क्र०", "मकान नं० (सर्वे)", "प्रॉपर्टी ID", "मुखिया/स्वामी",
        "सदस्य का नाम", "संबंधी (पिता/पति) का नाम", "आयु", "लिंग",
        "मोबाइल नं०", "पात्रता श्रेणी", "BLO भौतिक सत्यापन स्थिति", "BLO टिप्पणी", "नागरिक/मुखिया हस्ताक्षर"
    ]
    ws2.append([])
    ws2.append(headers2)
    ws2.row_dimensions[4].height = 30

    header2_fill = PatternFill(start_color="991B1B", end_color="991B1B", fill_type="solid")
    for col_idx in range(1, len(headers2) + 1):
        c = ws2.cell(row=4, column=col_idx)
        c.fill = header2_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = thick_bottom

    row2_num = 5
    counter2 = 1

    for h in audit_res.get("houses", []):
        h_no = h.get("houseNumber") or ""
        prop_id = h.get("propertyId") or ""
        owner = h.get("ownerName") or ""

        for m in h.get("eligibleMembers", []):
            is_reg = m.get("isRegistered", False)
            if is_reg:
                continue  # Only unregistered members on Sheet 2

            age = int(m.get("age") or 0)
            category = "18+ नवीन मतदाता (फॉर्म 6)" if age >= 18 else "17+ अग्रिम आवेदन (फॉर्म 6)"

            row_data2 = [
                counter2,
                h_no,
                prop_id,
                owner,
                m.get("name") or "",
                m.get("fatherHusbandName") or "",
                age,
                m.get("gender") or "",
                m.get("mobile") or "",
                category,
                "",  # BLO भौतिक सत्यापन स्थिति (रिक्त)
                "",  # BLO टिप्पणी (रिक्त)
                ""   # हस्ताक्षर (रिक्त)
            ]
            ws2.append(row_data2)
            ws2.row_dimensions[row2_num].height = 26

            for c_idx in range(1, len(headers2) + 1):
                cell = ws2.cell(row=row2_num, column=c_idx)
                cell.border = thin_border
                cell.font = Font(name=font_family, size=10)
                if c_idx in (1, 2, 7, 8, 9):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif c_idx == 10:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    if age >= 18:
                        cell.fill = red_fill
                        cell.font = red_font
                    else:
                        cell.fill = amber_fill
                        cell.font = amber_font
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

            row2_num += 1
            counter2 += 1

    # Auto-fit columns for sheet 2
    for col in ws2.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in (1, 2, 3):
                continue
            val_str = str(cell.value or "")
            max_len = max(max_len, len(val_str))
        ws2.column_dimensions[col_letter].width = max(max_len + 4, 15)

    # -------------------------------------------------------------
    # SHEET 3: सांख्यिकी सारांश (Summary & Demographic Analytics)
    # -------------------------------------------------------------
    ws3 = wb.create_sheet(title="सांख्यिकी सारांश")
    ws3.views.sheetView[0].showGridLines = True

    ws3.merge_cells("A1:G1")
    t3 = ws3["A1"]
    t3.value = f"डोर-टू-डोर सर्वे एवं मतदाता ऑडिट - सांख्यिकी सारांश"
    t3.font = Font(name=font_family, size=14, bold=True, color="1E3A8A")
    t3.alignment = Alignment(horizontal="center", vertical="center")
    ws3.row_dimensions[1].height = 32

    ws3.merge_cells("A2:G2")
    s3 = ws3["A2"]
    s3.value = f"क्षेत्र: {street_label}{zone_label} | रिपोर्ट जनरेट तिथि: {now_str}"
    s3.font = Font(name=font_family, size=10, italic=True, color="475569")
    s3.alignment = Alignment(horizontal="center", vertical="center")
    ws3.row_dimensions[2].height = 22

    # Section 1: Key Metrics
    ws3.cell(row=4, column=1, value="1. मुख्य सांख्यिकी सूचकांक (Key Audit Metrics)").font = Font(name=font_family, size=11, bold=True, color="1E293B")
    metric_headers = ["क्र०", "सूचक विवरण (Metric)", "संख्या (Count)", "प्रतिशत (%)"]
    ws3.append([])
    ws3.append(metric_headers)
    ws3.row_dimensions[6].height = 24

    for c_i in range(1, 5):
        c = ws3.cell(row=6, column=c_i)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = thick_bottom

    total_m = audit_res['totalEligibleMembers'] or 1
    total_unreg = audit_res['totalEligibleMembers'] - audit_res['totalRegistered']
    reg_pct = f"{(audit_res['totalRegistered'] / total_m * 100):.1f}%"
    unreg_pct = f"{(total_unreg / total_m * 100):.1f}%"
    f6_18_pct = f"{(audit_res['form6_18plusCount'] / total_m * 100):.1f}%"
    f6_17_pct = f"{(audit_res['form6_17plusCount'] / total_m * 100):.1f}%"

    metric_rows = [
        (1, "कुल सर्वेक्षित मकान", audit_res['totalHouses'], "-"),
        (2, "कुल 17+ पात्र परिवार सदस्य", audit_res['totalEligibleMembers'], "100.0%"),
        (3, "कुल पंजीकृत मतदाता (वोटर लिस्ट में उपलब्ध)", audit_res['totalRegistered'], reg_pct),
        (4, "कुल अपंजीकृत सदस्य (वोटर लिस्ट में अनुपस्थित)", total_unreg, unreg_pct),
        (5, "फॉर्म 6 पात्र नागरिक (आयु 18+ वर्ष)", audit_res['form6_18plusCount'], f6_18_pct),
        (6, "अग्रिम फॉर्म 6 पात्र (आयु 17 वर्ष)", audit_res['form6_17plusCount'], f6_17_pct),
    ]

    r_idx = 7
    for row in metric_rows:
        ws3.append(list(row))
        for col_idx in range(1, 5):
            cell = ws3.cell(row=r_idx, column=col_idx)
            cell.border = thin_border
            cell.font = Font(name=font_family, size=10)
            if col_idx in (1, 3, 4):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
            if row[0] == 3 and col_idx == 3:
                cell.font = green_font
            elif row[0] == 5 and col_idx == 3:
                cell.font = red_font
        r_idx += 1

    # Section 2: Gender Breakdown
    ws3.cell(row=r_idx + 2, column=1, value="2. लिंग आधारित पंजीकरण विवरण (Gender Distribution)").font = Font(name=font_family, size=11, bold=True, color="1E293B")
    gender_headers = ["क्र०", "लिंग", "कुल सदस्य", "पंजीकृत मतदाता", "अपंजीकृत (फॉर्म 6)"]
    ws3.cell(row=r_idx + 3, column=1)
    ws3.row_dimensions[r_idx + 4].height = 24

    for c_i, h_title in enumerate(gender_headers, 1):
        c = ws3.cell(row=r_idx + 4, column=c_i, value=h_title)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = thick_bottom

    gender_stats = {"पुरुष": {"total": 0, "reg": 0, "unreg": 0}, "महिला": {"total": 0, "reg": 0, "unreg": 0}, "अन्य": {"total": 0, "reg": 0, "unreg": 0}}
    for h in audit_res.get("houses", []):
        for m in h.get("eligibleMembers", []):
            g = (m.get("gender") or "").strip()
            key = "पुरुष" if "पु" in g or "M" in g.upper() else ("महिला" if "म" in g or "F" in g.upper() else "अन्य")
            gender_stats[key]["total"] += 1
            if m.get("isRegistered"):
                gender_stats[key]["reg"] += 1
            else:
                gender_stats[key]["unreg"] += 1

    gr_start = r_idx + 5
    for idx, (g_name, g_data) in enumerate(gender_stats.items(), 1):
        g_row = [idx, g_name, g_data["total"], g_data["reg"], g_data["unreg"]]
        ws3.append(g_row)
        for c_i in range(1, 6):
            cell = ws3.cell(row=gr_start, column=c_i)
            cell.border = thin_border
            cell.font = Font(name=font_family, size=10)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        gr_start += 1

    for col in ws3.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in (1, 2):
                continue
            val_str = str(cell.value or "")
            max_len = max(max_len, len(val_str))
        ws3.column_dimensions[col_letter].width = max(max_len + 4, 15)

    export_dir = OUTPUT_DIR / "survey_audits"
    export_dir.mkdir(parents=True, exist_ok=True)
    raw_name = street or (f"Zone_{zone}" if zone else "All_Streets")
    clean_street_name = re.sub(r'[\\/*?:"<>|]', '_', str(raw_name))
    filename = f"Gali_Survey_{clean_street_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = export_dir / filename
    wb.save(str(filepath))

    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



# =============================================================================
# FRONTEND ROUTING (PUBLIC SEARCH PORTAL vs. ADMIN CONVERTER)
# =============================================================================

@app.get("/")
def serve_root(request: Request):
    """
    Serves the public search portal if accessed via internet tunnel,
    or allows local admin view based on origin or query.
    """
    search_file = FRONTEND_DIR / "search.html"
    index_file = FRONTEND_DIR / "index.html"
    
    # If explicitly requested admin view
    if request.query_params.get("view") == "admin":
        return FileResponse(str(index_file))
        
    # Public internet traffic always defaults to the search portal
    if is_public_request(request) and search_file.exists():
        return FileResponse(str(search_file))
        
    # Local server default: serve search portal if available, or admin converter
    if search_file.exists():
        return FileResponse(str(search_file))
    return FileResponse(str(index_file))


@app.get("/search")
@app.get("/voter-search")
def serve_search_page():
    """Direct URL for dedicated Public Voter Search Portal."""
    search_file = FRONTEND_DIR / "search.html"
    if search_file.exists():
        return FileResponse(str(search_file))
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/admin")
def serve_admin_page(request: Request):
    """Direct URL for Administrator PDF Converter & DB Management."""
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/caste-analytics")
@app.get("/caste-analytics.html")
@app.get("/analytics")
def serve_caste_analytics_page():
    """Direct URL for dedicated Standalone Caste & Demographic Analytics Page."""
    caste_file = FRONTEND_DIR / "caste-analytics.html"
    if caste_file.exists():
        return FileResponse(str(caste_file))
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# ==============================================================================
# 🏛️ PANCHAYAT & LOCAL BODY DEDICATED SCANNER & CROSS-COMPARISON APIS
# ==============================================================================

PANCHAYAT_JOBS_DB: Dict[str, Dict[str, Any]] = {}
PANCHAYAT_JOB_PDF_MAP: Dict[str, str] = {}


def run_panchayat_extraction_job(
    job_id: str,
    body_type: str,
    body_name: str,
    ward_no: str,
    ward_name: Optional[str] = "",
    part_no: Optional[str] = "",
    polling_station: Optional[str] = "",
    polling_booth: Optional[str] = "",
    mohalla: Optional[str] = "",
    district: Optional[str] = "",
    engine: str = "auto",
    parts_config: Optional[List[Dict[str, Any]]] = None
):
    """
    Background worker for Panchayat / Local Body PDF Extraction.
    Supports SEC UP 2-Column rolls, Digital Text, or OCR fallback.
    Saves extracted voters into dedicated `panchayat_voters` table.
    Enforces that every voter list must have a valid part_no (भाग संख्या).
    Supports multi-part lists where different page ranges have different part numbers/polling stations.
    """
    job = PANCHAYAT_JOBS_DB.get(job_id)
    pdf_path = PANCHAYAT_JOB_PDF_MAP.get(job_id)

    if not job or not pdf_path or not os.path.exists(pdf_path):
        if job:
            job["status"] = "failed"
            job["error"] = "PDF फ़ाइल नहीं मिली।"
        return

    try:
        job["status"] = "processing"
        job["progress_pct"] = 5
        job["message"] = "PDF संरचना का विश्लेषण हो रहा है..."
        job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] PDF फाइल का विश्लेषण शुरू...")

        inspection = PDFDetector.inspect_pdf(pdf_path)
        total_pages = inspection.get("total_pages", 1)
        job["pages_total"] = total_pages

        is_ulb = inspection.get("is_ulb", False) or ULBExtractor.is_ulb_pdf(pdf_path)
        requires_ocr = inspection.get("requires_ocr", False)
        ocr_available = OCRExtractor.is_ocr_available()

        job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] कुल पृष्ठ: {total_pages}, प्रारूप: {'राज्य निर्वाचन आयोग (SEC UP 2-Column)' if is_ulb else 'साधारण/डिजिटल'}")

        cover_meta = {}
        try:
            import pymupdf as fitz
            doc = fitz.open(pdf_path)
            if len(doc) > 0:
                cover_meta = ULBExtractor.extract_page_header_metadata(page=doc[0])
                if (not cover_meta.get("nikay_name") or not cover_meta.get("ward_no") or not cover_meta.get("part_no")) and len(doc) > 1:
                    cover_meta = ULBExtractor.extract_page_header_metadata(page=doc[1], fallback_meta=cover_meta)
            doc.close()
        except Exception:
            pass

        # Operator/Admin bulk overrides take highest priority; fallback to auto-extracted header
        final_body_type = body_type or cover_meta.get("body_type") or "gram_panchayat"
        final_body_name = body_name or cover_meta.get("nikay_name") or ("ग्राम पंचायत" if "gram" in str(final_body_type) else "नगर पंचायत")
        final_ward_no = ward_no or cover_meta.get("ward_no") or ""
        final_ward_name = ward_name or cover_meta.get("ward_name") or ""
        # Enforce that every Nikay voter list must have a part_no (भाग संख्या)
        final_part_no = part_no or cover_meta.get("part_no") or final_ward_no or "1"
        final_ps = polling_station or cover_meta.get("polling_station") or ""
        final_pb = polling_booth or cover_meta.get("polling_booth") or ""
        final_moh = mohalla or cover_meta.get("mohalla") or ""

        if parts_config and len(parts_config) > 0:
            job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 📑 मल्टी-पार्ट विन्यास सक्रिय: कुल {len(parts_config)} भाग विन्यास परिभाषित हैं।")
            for pc in parts_config:
                job["logs"].append(f"   • पृष्ठ {pc.get('page_start')}-{pc.get('page_end')}: भाग सं० {pc.get('part_no')} (वार्ड {pc.get('ward_no')}) | {pc.get('polling_booth') or pc.get('polling_station')}")
        else:
            job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 📋 लागू प्रशासनिक विवरण: निकाय: {final_body_name} | वार्ड: {final_ward_no} ({final_ward_name or '-'}) | भाग सं०: {final_part_no} | मतदान स्थल: {final_pb or final_ps or '-'}")

        if ocr_available:
            job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ उच्च क्वालिटी AI ऑटो-स्कैन (300 DPI Micro-OCR) सक्रिय है - मूल इमेज से शत-प्रतिशत शुद्ध टेक्स्ट निष्कर्षण।")

        all_voters_list = []
        serial_counter = 1
        running_meta = dict(cover_meta)
        if part_no:
            running_meta["part_no"] = part_no
        if ward_no:
            running_meta["ward_no"] = ward_no

        for p_idx in range(total_pages):
            page_no = p_idx + 1
            job["pages_done"] = page_no
            pct = 5 + int((page_no / total_pages) * 85)
            job["progress_pct"] = pct
            job["message"] = f"पृष्ठ {page_no} / {total_pages} प्रोसेस हो रहा है..."

            # Resolve page-specific part configuration if multi-part config provided
            matching_part = None
            if parts_config:
                for b in parts_config:
                    try:
                        p_s = int(b.get("page_start", 1))
                        p_e = int(b.get("page_end", total_pages))
                        if p_s <= page_no <= p_e:
                            matching_part = b
                            break
                    except (ValueError, TypeError):
                        continue

            if matching_part:
                page_part = str(matching_part.get("part_no") or "").strip() or final_part_no
                page_ward = str(matching_part.get("ward_no") or "").strip() or final_ward_no
                page_ward_name = str(matching_part.get("ward_name") or "").strip() or final_ward_name
                page_ps = str(matching_part.get("polling_station") or "").strip() or final_ps
                page_pb = str(matching_part.get("polling_booth") or "").strip() or final_pb
                page_moh = str(matching_part.get("mohalla") or "").strip() or final_moh
            else:
                page_part = part_no or running_meta.get("part_no") or final_part_no
                page_ward = ward_no or running_meta.get("ward_no") or final_ward_no
                page_ward_name = ward_name or running_meta.get("ward_name") or final_ward_name
                page_ps = polling_station or running_meta.get("polling_station") or final_ps
                page_pb = polling_booth or running_meta.get("polling_booth") or final_pb
                page_moh = mohalla or running_meta.get("mohalla") or final_moh

            page_meta_for_extract = dict(running_meta)
            page_meta_for_extract["part_no"] = page_part
            page_meta_for_extract["ward_no"] = page_ward
            page_meta_for_extract["ward_name"] = page_ward_name
            page_meta_for_extract["polling_station"] = page_ps
            page_meta_for_extract["polling_booth"] = page_pb
            page_meta_for_extract["mohalla"] = page_moh

            p_res = None
            if engine == "ocr" and ocr_available:
                p_res = OCRExtractor.process_page_ocr(
                    pdf_path, p_idx, current_serial=serial_counter, total_pages=total_pages
                )
            elif is_ulb or engine == "ulb":
                p_res = ULBExtractor.process_page(pdf_path, p_idx, metadata=page_meta_for_extract)
            else:
                p_res = DigitalVoterExtractor.process_page(pdf_path, p_idx, current_serial=serial_counter)
                if (not p_res or not p_res.voters) and ocr_available and requires_ocr:
                    p_res = OCRExtractor.process_page_ocr(
                        pdf_path, p_idx, current_serial=serial_counter, total_pages=total_pages
                    )

            if p_res and p_res.voters:
                page_voter_count = len(p_res.voters)

                # Check if this page introduced a new part number or ward from header if not in explicit parts_config
                first_v = p_res.voters[0]
                new_part = getattr(first_v, "part_no", None)
                if not parts_config and not part_no and new_part and new_part != running_meta.get("part_no"):
                    running_meta["part_no"] = new_part
                    if getattr(first_v, "polling_booth", None):
                        running_meta["polling_booth"] = getattr(first_v, "polling_booth")
                    if getattr(first_v, "polling_station", None):
                        running_meta["polling_station"] = getattr(first_v, "polling_station")
                    if getattr(first_v, "ward_no", None):
                        running_meta["ward_no"] = getattr(first_v, "ward_no")
                    if getattr(first_v, "ward_name", None):
                        running_meta["ward_name"] = getattr(first_v, "ward_name")
                    if getattr(first_v, "mohalla", None):
                        running_meta["mohalla"] = getattr(first_v, "mohalla")
                    job["logs"].append(
                        f"[{datetime.now().strftime('%H:%M:%S')}] 📍 नया भाग/अनुभाग पहचाना गया (पृष्ठ {page_no}): भाग सं० {new_part} | वार्ड {running_meta.get('ward_no', '')} | मतदान स्थल: {running_meta.get('polling_booth') or running_meta.get('polling_station')}"
                    )

                for v in p_res.voters:
                    v_dict = {
                        "serial_no": v.serial_no or serial_counter,
                        "name": v.name,
                        "relation_type": v.relation_type or "पिता",
                        "relation_name": getattr(v, "relation_name", None) or getattr(v, "father_name", ""),
                        "house_no": v.house_no,
                        "age": v.age,
                        "gender": v.gender,
                        "epic_no": "",  # SEC UP / Nagar Nikay / Panchayat lists do not have EPIC numbers
                        "polling_station": page_ps or getattr(v, "polling_station", "") or final_ps,
                        "polling_booth": page_pb or getattr(v, "polling_booth", "") or final_pb,
                        "part_no": page_part,
                        "ward_no": page_ward,
                        "ward_name": page_ward_name,
                        "mohalla": page_moh or getattr(v, "mohalla", "") or getattr(v, "section_no", "") or final_moh,
                        "section_no": page_moh or getattr(v, "mohalla", "") or getattr(v, "section_no", "") or final_moh,
                        "page_no": page_no
                    }
                    all_voters_list.append(v_dict)
                    serial_counter += 1

                job["voters_extracted"] = len(all_voters_list)
                job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] पृष्ठ {page_no}: {page_voter_count} मतदाता निकाले गए (भाग सं०: {page_part}, कुल: {len(all_voters_list)})")

        job["progress_pct"] = 92
        job["message"] = "मतदाताओं को पंचायत डेटाबेस (panchayat_voters) में सहेजा जा रहा है..."
        job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] कुल {len(all_voters_list)} मतदाताओं को अलग डेटाबेस टेबल में इंसर्ट व इंडेक्स किया जा रहा है...")

        source_filename = os.path.basename(pdf_path)
        # Clear previous records for this source file to prevent duplicate serials
        PanchayatService.delete_by_source_file(source_filename)
        save_result = PanchayatService.save_voters(
            records=all_voters_list,
            body_type=final_body_type,
            body_name=final_body_name,
            ward_no=final_ward_no,
            ward_name=final_ward_name,
            part_no=final_part_no,
            polling_station=final_ps,
            polling_booth=final_pb,
            source_file=source_filename
        )

        job["progress_pct"] = 100
        job["status"] = "completed"
        job["voters_saved"] = save_result.get("inserted", len(all_voters_list))
        job["message"] = f"सफलता! {job['voters_saved']} पंचायत मतदाता सफलतापूर्वक सेव हुए।"
        job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 प्रक्रिया पूर्ण! कुल {job['voters_saved']} मतदाता अलग पंचायत टेबल में सेव हुए।")

    except Exception as e:
        import traceback
        err_msg = str(e)
        traceback.print_exc()
        job["status"] = "failed"
        job["error"] = err_msg
        job["message"] = f"त्रुटि: {err_msg}"
        job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ त्रुटि: {err_msg}")


class PanchayatProcessRequest(BaseModel):
    body_type: Optional[str] = "gram_panchayat"
    body_name: Optional[str] = ""
    ward_no: Optional[str] = ""
    ward_name: Optional[str] = ""
    part_no: Optional[str] = ""
    polling_station: Optional[str] = ""
    polling_booth: Optional[str] = ""
    mohalla: Optional[str] = ""
    district: Optional[str] = ""
    engine: Optional[str] = "auto"
    parts_config: Optional[List[Dict[str, Any]]] = None


@app.post("/api/panchayat/inspect-header")
async def inspect_panchayat_header(
    request: Request,
    file: UploadFile = File(...)
):
    """
    Rapid multi-part inspection endpoint:
    Pre-scans headers across ALL pages of the PDF to identify distinct sections,
    part numbers, ward numbers, booths, and page ranges so user can review and bulk edit.
    Accessible to Data Operators and Admins.
    """
    verify_operator_or_admin_access(request)
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="केवल PDF फाइलें ही समर्थित हैं।")

    content = await file.read()
    detected_parts = []
    detected_meta = {}
    total_pages = 1
    nikay_name = ""
    body_type = "nagar_panchayat"
    district = ""

    try:
        import pymupdf as fitz
        doc = fitz.open(stream=content, filetype="pdf")
        total_pages = len(doc)
        insp_res = ULBExtractor.inspect_pdf_all_parts(doc)
        detected_parts = insp_res.get("detected_parts", [])
        nikay_name = insp_res.get("nikay_name", "")
        body_type = insp_res.get("body_type", "nagar_panchayat")
        district = insp_res.get("district", "")

        if detected_parts:
            first_p = detected_parts[0]
            detected_meta = {
                "body_type": body_type,
                "nikay_name": nikay_name,
                "district": district,
                "ward_no": first_p.get("ward_no", ""),
                "ward_name": first_p.get("ward_name", ""),
                "part_no": first_p.get("part_no", "1"),
                "polling_station": first_p.get("polling_station", ""),
                "polling_booth": first_p.get("polling_booth", ""),
                "mohalla": first_p.get("mohalla", "")
            }
        doc.close()
    except Exception as e:
        print(f"[WARN] Failed multi-part inspect PDF header: {e}")

    return {
        "success": True,
        "filename": file.filename,
        "total_pages": total_pages,
        "nikay_name": nikay_name,
        "body_type": body_type,
        "district": district,
        "detected_parts": detected_parts,
        "detected_meta": detected_meta
    }


@app.post("/api/panchayat/upload")
async def upload_panchayat_pdf(
    request: Request,
    file: UploadFile = File(...),
    body_type: str = Form("gram_panchayat"),
    body_name: Optional[str] = Form(""),
    ward_no: Optional[str] = Form(""),
    ward_name: Optional[str] = Form(""),
    part_no: Optional[str] = Form(""),
    polling_station: Optional[str] = Form(""),
    polling_booth: Optional[str] = Form(""),
    mohalla: Optional[str] = Form(""),
    district: Optional[str] = Form("")
):
    """
    Uploads a Gram/Nagar Panchayat PDF and initializes background job.
    Auto-detects header metadata from the PDF and accepts user bulk overrides.
    Accessible to Data Operators and Admins.
    """
    verify_operator_or_admin_access(request)
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="केवल PDF फाइलें ही समर्थित हैं।")

    job_id = str(uuid.uuid4())
    safe_filename = f"panchayat_{job_id[:8]}_{file.filename}"
    saved_path = UPLOAD_DIR / safe_filename

    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        inspection = PDFDetector.inspect_pdf(str(saved_path))
        total_pages = inspection.get("total_pages", 1)
    except Exception:
        total_pages = 1

    # Auto-extract header metadata from page 0 / 1
    detected_meta = {}
    try:
        import pymupdf as fitz
        doc = fitz.open(str(saved_path))
        if len(doc) > 0:
            detected_meta = ULBExtractor.extract_page_header_metadata(page=doc[0])
            if (not detected_meta.get("nikay_name") or not detected_meta.get("ward_no") or not detected_meta.get("part_no")) and len(doc) > 1:
                detected_meta = ULBExtractor.extract_page_header_metadata(page=doc[1], fallback_meta=detected_meta)
        if not detected_meta.get("part_no"):
            detected_meta["part_no"] = detected_meta.get("ward_no") or "1"
        doc.close()
    except Exception as e:
        print(f"[WARN] Header extraction during upload: {e}")

    # Apply any explicit user overrides passed during upload
    if body_type: detected_meta["body_type"] = body_type
    if body_name: detected_meta["nikay_name"] = body_name
    if ward_no: detected_meta["ward_no"] = ward_no
    if ward_name: detected_meta["ward_name"] = ward_name
    if part_no: detected_meta["part_no"] = part_no
    if polling_station: detected_meta["polling_station"] = polling_station
    if polling_booth: detected_meta["polling_booth"] = polling_booth
    if mohalla: detected_meta["mohalla"] = mohalla
    if district: detected_meta["district"] = district

    PANCHAYAT_JOBS_DB[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress_pct": 0,
        "pages_done": 0,
        "pages_total": total_pages,
        "voters_extracted": 0,
        "voters_saved": 0,
        "detected_meta": detected_meta,
        "message": "फाइल अपलोड हुई, स्कैनिंग के लिए तैयार।",
        "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] फ़ाइल '{file.filename}' अपलोड हुई (कुल पृष्ठ: {total_pages})"],
        "error": None
    }
    PANCHAYAT_JOB_PDF_MAP[job_id] = str(saved_path)

    return {
        "success": True,
        "job_id": job_id,
        "total_pages": total_pages,
        "filename": file.filename,
        "detected_meta": detected_meta
    }


@app.post("/api/panchayat/process/{job_id}")
async def start_panchayat_processing(
    job_id: str,
    req: PanchayatProcessRequest,
    background_tasks: BackgroundTasks,
    request: Request
):
    """Triggers background extraction and ingestion into panchayat_voters table with bulk metadata overrides."""
    verify_operator_or_admin_access(request)
    if job_id not in PANCHAYAT_JOBS_DB:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")

    job_data = PANCHAYAT_JOBS_DB.get(job_id, {})
    detected = job_data.get("detected_meta", {})

    b_type = req.body_type or detected.get("body_type") or "gram_panchayat"
    b_name = req.body_name or detected.get("nikay_name") or ""
    w_no = req.ward_no or detected.get("ward_no") or ""
    w_name = req.ward_name or detected.get("ward_name") or ""
    p_no = req.part_no or detected.get("part_no") or ""
    ps = req.polling_station or detected.get("polling_station") or ""
    pb = req.polling_booth or detected.get("polling_booth") or ""
    moh = req.mohalla or detected.get("mohalla") or ""
    dist = req.district or detected.get("district") or ""

    background_tasks.add_task(
        run_panchayat_extraction_job,
        job_id=job_id,
        body_type=b_type,
        body_name=b_name,
        ward_no=w_no,
        ward_name=w_name,
        part_no=p_no,
        polling_station=ps,
        polling_booth=pb,
        mohalla=moh,
        district=dist,
        engine=req.engine or "auto",
        parts_config=req.parts_config
    )

    return {
        "success": True,
        "message": "पंचायत स्कैनर प्रक्रिया प्रारंभ की गई।",
        "job_id": job_id
    }


class PanchayatBulkUpdateRequest(BaseModel):
    filter_params: Dict[str, Any] = Field(default_factory=dict)
    update_values: Dict[str, Any] = Field(default_factory=dict)


@app.post("/api/panchayat/bulk-update", dependencies=[Depends(verify_admin_access)])
def api_panchayat_bulk_update(req: PanchayatBulkUpdateRequest):
    """
    Bulk updates administrative fields (ward_no, ward_name, part_no, body_name,
    polling_station, polling_booth, mohalla) across voters matching the filter in panchayat_voters table.
    """
    res = PanchayatService.bulk_update_voters(req.filter_params, req.update_values)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "अपडेट विफल रहा।"))
    return res


@app.get("/api/panchayat/status/{job_id}")
async def get_panchayat_job_status(job_id: str, request: Request):
    """Returns real-time status and logs for panchayat scan job."""
    verify_operator_or_admin_access(request)
    job = PANCHAYAT_JOBS_DB.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ID नहीं मिला।")
    return job


@app.get("/api/panchayat/stats")
async def get_panchayat_stats(request: Request):
    """Returns overall summary statistics for panchayat_voters table."""
    verify_operator_or_admin_access(request)
    try:
        stats = PanchayatService.get_stats()
        stats["distinct_bodies"] = [b.get("body_name") for b in stats.get("bodies", []) if b.get("body_name")]
        stats["distinct_wards"] = [w.get("ward_no") for w in stats.get("wards", []) if w.get("ward_no")]
        stats["distinct_parts"] = stats.get("distinct_parts") or [str(p.get("part_no")) for p in stats.get("parts", []) if p.get("part_no")]
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/panchayat/voters")
async def get_panchayat_voters_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    body_name: Optional[str] = Query(None),
    ward_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("part_serial"),
    sort_order: Optional[str] = Query("asc"),
    query: Optional[str] = Query(None)
):
    """Returns paginated voter records from panchayat_voters table with part_no and sorting support."""
    verify_admin_access(request)
    try:
        res = PanchayatService.get_voters(
            search=query,
            body_name=body_name,
            ward_no=ward_no,
            part_no=part_no,
            gender=gender,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            limit=page_size
        )
        return {
            "success": True,
            "records": res.get("voters", []),
            "total": res.get("total", 0),
            "page": page,
            "page_size": page_size
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/panchayat/export/voters")
async def export_panchayat_voters(
    request: Request,
    body_name: Optional[str] = Query(None),
    ward_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    query: Optional[str] = Query(None)
):
    """Exports filtered panchayat voters to styled Excel file with Part No columns."""
    verify_admin_access(request)
    try:
        import urllib.parse
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        part_tag = f"_Part_{part_no}" if part_no else ""
        ward_tag = f"_Ward_{ward_no}" if ward_no else ""
        body_tag = f"_{body_name.replace(' ', '_')}" if body_name else ""
        out_filename = f"Panchayat_Voters{body_tag}{ward_tag}{part_tag}_{timestamp}.xlsx"
        output_file = OUTPUT_DIR / out_filename

        PanchayatService.generate_search_excel(
            output_path=str(output_file),
            query=query,
            body_name=body_name,
            ward_no=ward_no,
            part_no=part_no
        )

        encoded_filename = urllib.parse.quote(out_filename)
        return FileResponse(
            path=str(output_file),
            filename=out_filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PanchayatEnhanceRequest(BaseModel):
    body_name: Optional[str] = None


@app.post("/api/panchayat/enhance/local-ai")
async def enhance_panchayat_with_local_ai(request: Request, req: Optional[PanchayatEnhanceRequest] = None):
    """
    Applies Local AI Devanagari font decoding, compound name splitting, house sanitization,
    and DualPassErrorCorrector to all panchayat_voters records.
    """
    verify_admin_access(request)
    try:
        body_name = req.body_name if req else None
        res = PanchayatService.enhance_with_local_ai(body_name=body_name, uploads_dir=str(UPLOAD_DIR))
        return res
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/panchayat/duplicates/internal")
async def get_panchayat_internal_duplicates(
    request: Request,
    body_name: Optional[str] = Query(None),
    ward_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    match_tier: Optional[str] = Query("all")
):
    """Returns intra-list duplicate clusters within panchayat_voters table using Local AI deduplication engine."""
    verify_admin_access(request)
    try:
        res = PanchayatService.find_internal_duplicates(
            body_name=body_name,
            ward_no=ward_no,
            part_no=part_no,
            match_tier=match_tier
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/panchayat/compare/dual-voters")
async def compare_panchayat_dual_voters(
    request: Request,
    body_name: Optional[str] = Query(None),
    ward_no: Optional[str] = Query(None),
    rule_filter: Optional[str] = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500)
):
    """Cross-compares panchayat_voters against main Nagar Panchayat voters table."""
    verify_admin_access(request)
    try:
        res = PanchayatService.compare_with_nagar_panchayat(
            body_name=body_name,
            ward_no=ward_no,
            rule_filter=rule_filter,
            page=page,
            limit=page_size
        )
        matches = res.get("matches", [])
        for m in matches:
            m_type_raw = m.get("match_type", "")
            if "EPIC" in m_type_raw:
                m["match_type"] = "exact_epic"
                m["match_label"] = "100% सटीक EPIC मिलान"
                m["confidence"] = 100
            elif "सटीक नाम" in m_type_raw:
                m["match_type"] = "exact_name_rel"
                m["match_label"] = "सटीक नाम व सम्बन्धी मिलान"
                m["confidence"] = 95
            else:
                m["match_type"] = "ai_phonetic_age"
                m["match_label"] = "AI ध्वन्यात्मक (Soundex) + आयु साम्य"
                m["confidence"] = 85

            m["panchayat_voter"] = m.get("panchayat")
            m["nagar_voter"] = m.get("nagar_panchayat")
            pv = m.get("panchayat") or {}
            nv = m.get("nagar_panchayat") or {}
            if pv.get("age") and nv.get("age"):
                try:
                    m["age_diff"] = abs(int(pv["age"]) - int(nv["age"]))
                except Exception:
                    m["age_diff"] = None
            else:
                m["age_diff"] = None

        return {
            "success": True,
            "total_matches": res.get("total_dual_voters", len(matches)),
            "exact_epic_matches": res.get("epic_matches_count", 0),
            "exact_name_rel_matches": res.get("name_matches_count", 0),
            "ai_phonetic_matches": res.get("ai_matches_count", 0),
            "matches": matches
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExportDualVotersRequest(BaseModel):
    body_name: Optional[str] = None
    ward_no: Optional[str] = None


@app.post("/api/panchayat/export/dual-voters")
async def export_panchayat_dual_voters(req: ExportDualVotersRequest, request: Request):
    """Exports dual-voter cross-comparison report to styled Excel file."""
    verify_admin_access(request)
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        body_tag = f"_{req.body_name.replace(' ', '_')}" if req.body_name else ""
        out_filename = f"Dual_Voters_Report{body_tag}_{timestamp}.xlsx"
        output_file = OUTPUT_DIR / out_filename

        PanchayatService.generate_dual_voters_excel(
            output_path=str(output_file),
            body_name=req.body_name,
            ward_no=req.ward_no
        )

        encoded_filename = urllib.parse.quote(out_filename)
        return FileResponse(
            path=str(output_file),
            filename=out_filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PanchayatBulkUpdateRequest(BaseModel):
    # Scope filters
    body_name: Optional[str] = None
    ward_no: Optional[str] = None
    part_no: Optional[str] = None
    source_file: Optional[str] = None
    voter_ids: Optional[List[int]] = None
    # New values
    new_body_type: Optional[str] = None
    new_body_name: Optional[str] = None
    new_ward_no: Optional[str] = None
    new_ward_name: Optional[str] = None
    new_part_no: Optional[str] = None
    new_polling_station: Optional[str] = None
    new_polling_booth: Optional[str] = None
    new_mohalla: Optional[str] = None


@app.post("/api/panchayat/bulk-update")
async def bulk_update_panchayat_voters(req: PanchayatBulkUpdateRequest, request: Request):
    """
    Bulk updates administrative attributes (ward_no, ward_name, part_no, body_name,
    polling_station, polling_booth, mohalla) across voters matching the filter or voter_ids.
    Accessible to Data Operators and Admins.
    """
    verify_admin_access(request)
    filter_params = {}
    if req.voter_ids:
        filter_params["voter_ids"] = req.voter_ids
    if req.body_name:
        filter_params["body_name"] = req.body_name
    if req.ward_no:
        filter_params["ward_no"] = req.ward_no
    if req.part_no:
        filter_params["part_no"] = req.part_no
    if req.source_file:
        filter_params["source_file"] = req.source_file

    update_values = {}
    if req.new_body_type:
        update_values["body_type"] = req.new_body_type
    if req.new_body_name:
        update_values["body_name"] = req.new_body_name
    if req.new_ward_no:
        update_values["ward_no"] = req.new_ward_no
    if req.new_ward_name:
        update_values["ward_name"] = req.new_ward_name
    if req.new_part_no:
        update_values["part_no"] = req.new_part_no
    if req.new_polling_station:
        update_values["polling_station"] = req.new_polling_station
    if req.new_polling_booth:
        update_values["polling_booth"] = req.new_polling_booth
    if req.new_mohalla:
        update_values["mohalla"] = req.new_mohalla

    if not update_values:
        raise HTTPException(status_code=400, detail="कम से कम एक फ़ील्ड (वार्ड, भाग, निकाय, मतदान स्थल) का नया मान प्रदान करना आवश्यक है।")

    res = PanchayatService.bulk_update_voters(
        filter_params=filter_params,
        update_values=update_values
    )
    return res


class PanchayatVoterUpdateRequest(BaseModel):
    name: Optional[str] = None
    relation_type: Optional[str] = None
    relation_name: Optional[str] = None
    house_no: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    serial_no: Optional[int] = None
    ward_no: Optional[str] = None
    ward_name: Optional[str] = None
    part_no: Optional[str] = None
    polling_booth: Optional[str] = None
    polling_station: Optional[str] = None
    mohalla: Optional[str] = None
    section_no: Optional[str] = None


@app.get("/api/panchayat/voter/{voter_id}")
async def get_panchayat_voter_with_crop(voter_id: int, request: Request):
    """
    Returns single voter details along with high-resolution crop of the original electoral roll.
    The crop contains 2 voters above and 2 voters below, with the target voter highlighted.
    """
    verify_admin_access(request)
    try:
        res = PanchayatService.get_voter_crop_and_rescan(
            voter_id=voter_id,
            rescan=False,
            uploads_dir=str(UPLOAD_DIR)
        )
        if not res.get("success") and "नहीं मिला" in str(res.get("error", "")):
            raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/panchayat/voter/{voter_id}/rescan")
async def rescan_panchayat_voter(voter_id: int, request: Request):
    """
    Performs high-precision AI / micro-OCR re-scan of the specific voter card from the original PDF roll.
    Returns structured extracted fields for automatically populating the edit form.
    """
    verify_admin_access(request)
    try:
        res = PanchayatService.get_voter_crop_and_rescan(
            voter_id=voter_id,
            rescan=True,
            uploads_dir=str(UPLOAD_DIR)
        )
        if not res.get("success") and "नहीं मिला" in str(res.get("error", "")):
            raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/panchayat/voter/{voter_id}")
async def update_panchayat_voter(voter_id: int, req: PanchayatVoterUpdateRequest, request: Request):
    """
    Updates voter details in panchayat_voters table and refreshes phonetic Soundex indices.
    """
    verify_admin_access(request)
    try:
        updates = {k: v for k, v in req.dict().items() if v is not None}
        if not updates:
            raise HTTPException(status_code=400, detail="कोई अपडेट फ़ील्ड नहीं दी गई है।")
        
        updated = PanchayatService.update_voter(voter_id=voter_id, updates=updates)
        if not updated:
            raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")
        
        return {
            "success": True,
            "message": "मतदाता विवरण सफलतापूर्वक अपडेट किया गया।",
            "voter": updated
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/panchayat/voter/{voter_id}")
async def delete_panchayat_voter(voter_id: int, request: Request):
    """Deletes a single voter record from panchayat_voters table."""
    verify_operator_or_admin_access(request)
    try:
        success = PanchayatService.delete_voter(voter_id)
        if not success:
            raise HTTPException(status_code=404, detail="मतदाता रिकॉर्ड नहीं मिला।")
        return {"success": True, "deleted_id": voter_id, "message": "मतदाता सफलतापूर्वक हटा दिया गया।"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/panchayat/body")
async def delete_panchayat_body(request: Request, body_name: str = Query(...)):
    """Deletes all voters belonging to a specified body from panchayat_voters (Admin/SuperUser only)."""
    verify_admin_access(request)
    try:
        del_count = PanchayatService.delete_body(body_name=body_name)
        return {"success": True, "deleted_count": del_count, "body_name": body_name, "message": f"'{body_name}' के सभी {del_count} रिकॉर्ड्स हटा दिए गए।"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/panchayat/all")
async def delete_all_panchayat_records(request: Request):
    """Deletes all records from panchayat_voters table (Admin/SuperUser only)."""
    verify_admin_access(request)
    try:
        del_count = PanchayatService.delete_all_voters()
        return {
            "success": True,
            "deleted_count": del_count,
            "message": f"सफलतापूर्वक सम्पूर्ण पंचायत डेटाबेस ({del_count} रिकॉर्ड्स) को खाली कर दिया गया।"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/panchayat/voters/delete-filtered")
async def delete_panchayat_filtered_voters(
    request: Request,
    search: Optional[str] = Query(None),
    body_type: Optional[str] = Query(None),
    body_name: Optional[str] = Query(None),
    ward_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    gender: Optional[str] = Query(None)
):
    """Deletes all voters matching active filters from panchayat_voters table (Admin/Operator access)."""
    verify_operator_or_admin_access(request)
    try:
        del_count = PanchayatService.delete_filtered_voters(
            search=search,
            body_type=body_type,
            body_name=body_name,
            ward_no=ward_no,
            part_no=part_no,
            gender=gender
        )
        return {
            "success": True,
            "deleted_count": del_count,
            "message": f"सफलतापूर्वक वर्तमान फ़िल्टर के अनुसार {del_count} मतदाता हटा दिए गए।"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# PANCHAYAT & NIKAY DEDICATED ONLINE SEARCH PORTAL ROUTES & APIS
# =============================================================================

@app.get("/panchayat-search")
@app.get("/panchayat-portal")
@app.get("/panchayat")
async def serve_panchayat_search_page(request: Request):
    """Serves the dedicated online search portal for Panchayat & Urban Local Body voters."""
    search_file = FRONTEND_DIR / "panchayat-search.html"
    if search_file.exists():
        return FileResponse(str(search_file))
    raise HTTPException(status_code=404, detail="Panchayat search page not found.")


@app.get("/api/panchayat/search")
async def api_panchayat_search(
    query: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    relation_name: Optional[str] = Query(None),
    epic_no: Optional[str] = Query(None),
    house_no: Optional[str] = Query(None),
    body_name: Optional[str] = Query(None),
    ward_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    mohalla: Optional[str] = Query(None),
    polling_station: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    min_age: Optional[int] = Query(None),
    max_age: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100)
):
    """Dedicated online public search API for Panchayat & Nikay voters."""
    try:
        return PanchayatService.search_voters(
            query=query,
            name=name,
            relation_name=relation_name,
            epic_no=epic_no,
            house_no=house_no,
            body_name=body_name,
            ward_no=ward_no,
            part_no=part_no,
            mohalla=mohalla,
            polling_station=polling_station,
            gender=gender,
            min_age=min_age,
            max_age=max_age,
            page=page,
            limit=page_size
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/panchayat/search/filter-options")
@app.get("/api/panchayat/search/stats")
async def api_panchayat_search_filter_options():
    """Returns filter options and summary counts for Panchayat online search portal."""
    try:
        return PanchayatService.get_search_filter_options()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PanchayatSearchExportRequest(BaseModel):
    query: Optional[str] = None
    body_name: Optional[str] = None
    ward_no: Optional[str] = None
    part_no: Optional[str] = None


@app.post("/api/panchayat/search/export-excel")
async def export_panchayat_search_excel(req: PanchayatSearchExportRequest):
    """Exports matching panchayat search results to a styled Excel spreadsheet."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        body_tag = f"_{req.body_name.replace(' ', '_')}" if req.body_name else ""
        out_filename = f"Panchayat_Voters_Search{body_tag}_{timestamp}.xlsx"
        output_file = OUTPUT_DIR / out_filename

        PanchayatService.generate_search_excel(
            output_path=str(output_file),
            query=req.query,
            body_name=req.body_name,
            ward_no=req.ward_no,
            part_no=req.part_no
        )

        encoded_filename = urllib.parse.quote(out_filename)
        return FileResponse(
            path=str(output_file),
            filename=out_filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Mount Frontend static files for assets, css, js
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

