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

from .config import UPLOAD_DIR, OUTPUT_DIR, SAMPLE_DIR, FRONTEND_DIR, DATA_DIR, DB_PATH, APP_NAME, APP_VERSION, ADMIN_TOKEN
from .models.voter import VoterRecord, JobStatus, VoterStats
from .modules.pdf_detector import PDFDetector
from .modules.digital_extractor import DigitalVoterExtractor
from .modules.ulb_extractor import ULBExtractor
from .modules.ocr_extractor import OCRExtractor
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
    Strict Security Barrier:
    Protects Nagar Panchayat Geographic Street & House Survey Audit Matching & Git OTA Publisher.
    Strictly restricted to the super user 'harshsamrat'.
    """
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="सत्यापन आवश्यक है। कृपया 'harshsamrat' सुपर एडमिन के रूप में लॉगिन करें।"
        )
    username = (user.get("username") or "").strip().lower()
    if username != "harshsamrat":
        raise HTTPException(
            status_code=403,
            detail="पहुँच अस्वीकृत (Access Denied): '🚀 नया अपडेट पब्लिश करें' एवं प्रशासनिक ऑडिट केवल मुख्य सुपर एडमिन (harshsamrat) हेतु आरक्षित है।"
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
            
            # Extract cover page (Page 1) metadata: Polling Station, Part No, Assembly, Official Counts
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
                p_res = ULBExtractor.process_page(pdf_path, p_idx, metadata=cover_meta)
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
        
        # Determine assembly / part / polling station metadata from records or job
        for v in all_voters:
            if not v.polling_station and job.polling_station:
                v.polling_station = job.polling_station
            if not v.part_no and job.part_number:
                v.part_no = job.part_number
            if not v.assembly and job.assembly_name:
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
    
    return {
        "job_id": job_id,
        "total_records": len(records),
        "total_filtered": total_matching,
        "page": current_page,
        "limit": limit,
        "total_pages": total_pages,
        "records": out_records,
        "available_pages": available_pages,
        "stats": stats.dict()
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


@app.post("/api/jobs/{job_id}/bulk-update-metadata", dependencies=[Depends(verify_operator_or_admin_access)])
def bulk_update_job_metadata(job_id: str, req: JobBulkUpdateMetadataRequest):
    """
    Bulk updates part_no, assembly, and polling_station for an active upload job
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
    q: str = Query(..., min_length=1, description="खोज हेतु नाम, EPIC, सम्बन्धी या मकान"),
    part_no: Optional[str] = Query(None, description="वैकल्पिक भाग फ़िल्टर"),
    limit: int = Query(30, ge=1, le=100)
):
    """Searches voters across the entire database for manual mapping in the modal."""
    voters = VoterDatabase.search_voter_candidate(q=q, part_no=part_no, limit=limit)
    return {
        "success": True,
        "query": q,
        "count": len(voters),
        "voters": voters
    }


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
    current_part_no: str
    new_assembly: Optional[str] = None
    new_assembly_name: Optional[str] = None
    new_part_no: Optional[str] = None
    current_assembly: Optional[str] = None
    new_polling_station: Optional[str] = None
    db_id: Optional[str] = None


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
        res = VoterDatabase.bulk_update_assembly_part(
            current_part_no=req.current_part_no,
            new_assembly=target_assembly,
            new_part_no=req.new_part_no,
            current_assembly=req.current_assembly,
            new_polling_station=req.new_polling_station,
            db_id=req.db_id
        )
        if res.get("status") == "error":
            raise HTTPException(status_code=404, detail=res.get("message"))

        log_admin_action(
            "bulk_update_part",
            None,
            f"Part {req.current_part_no} updated: AC='{target_assembly}', Part='{req.new_part_no}', Records={res.get('updated_count')}"
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


# Mount Frontend static files for assets, css, js
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

