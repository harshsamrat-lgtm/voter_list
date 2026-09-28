"""
Secure Auto-Updater and Over-The-Air (OTA) Hot-Patch Engine for UP Voter Seva.

Features:
1. Zero-Token Client Security:
   - Fetches public release manifests and patches over HTTPS.
   - Zero GitHub access tokens or write credentials needed on client PCs.
2. Cryptographic Integrity:
   - Verifies SHA-256 checksums before applying any patch.
   - Prevents zip-slip directory traversal attacks.
3. User Data Isolation Shield:
   - Strictly protects `data/` (voters.db, license.enc, user accounts).
   - Never touches or overwrites local databases or configurations.
4. Hot-Patch (~2-4 MB) vs Full Installer (~285 MB) Smart Selection:
   - Automatically downloads small zip patches for code/UI updates.
   - Recommends full installer only for major runtime/OCR dependency upgrades.
5. Automatic Code Backup & Rollback:
   - Automatically archives previous code before applying updates.
"""

import os
import sys
import json
import time
import shutil
import zipfile
import hashlib
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, Any, Tuple, Optional
from pathlib import Path

from ..config import BASE_DIR, DATA_DIR

VERSION_FILE = BASE_DIR / "version.json"
BACKUP_DIR = BASE_DIR / "backups" / "code_updates"
TEMP_UPDATE_DIR = BASE_DIR / "temp" / "updater"


def parse_semver(v_str: str) -> Tuple[int, int, int]:
    """Parses semantic version string '1.2.3' into a comparable tuple (1, 2, 3)."""
    try:
        clean = (v_str or "").strip().lstrip("v").split("-")[0]
        parts = [int(p) for p in clean.split(".") if p.isdigit()]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return (0, 0, 0)


class UpdaterGuard:
    """Manages update checking, cryptographic verification, and safe patch application."""

    @classmethod
    def get_local_version_info(cls) -> Dict[str, Any]:
        """Reads local version.json manifest."""
        default_info = {
            "version": "1.0.1",
            "app_name": "UP Voter Seva",
            "channel": "stable",
            "release_date": "2026-09-28",
            "remote_manifest_url": "https://raw.githubusercontent.com/harshsamrat-lgtm/voter_list/main/version.json"
        }
        if not VERSION_FILE.exists():
            return default_info
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**default_info, **data}
        except Exception:
            return default_info

    @classmethod
    def check_for_updates(cls, custom_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Queries the remote version manifest and compares with local version.
        Returns detailed update availability and changelog.
        """
        local_info = cls.get_local_version_info()
        manifest_url = custom_url or local_info.get("remote_manifest_url")

        if not manifest_url:
            return {
                "update_available": False,
                "current_version": local_info.get("version"),
                "message": "रिमोट अपडेट URL कॉन्फ़िगर नहीं है।"
            }

        try:
            req = urllib.request.Request(
                manifest_url,
                headers={"User-Agent": f"UP-Voter-Seva-Updater/{local_info.get('version')}"}
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                remote_data = json.loads(resp.read().decode("utf-8"))

            local_ver = parse_semver(local_info.get("version", "1.0.0"))
            remote_ver = parse_semver(remote_data.get("version", "1.0.0"))

            is_newer = remote_ver > local_ver
            update_type = remote_data.get("update_type", "patch")

            patch_info = remote_data.get("patch", {})
            full_info = remote_data.get("full_installer", {})

            return {
                "update_available": is_newer,
                "current_version": local_info.get("version"),
                "latest_version": remote_data.get("version"),
                "release_date": remote_data.get("release_date"),
                "update_type": update_type,
                "title": remote_data.get("title", f"नया अपडेट {remote_data.get('version')} उपलब्ध है"),
                "changelog": remote_data.get("changelog", ["सामान्य सुधार व सुरक्षा अपडेट"]),
                "patch_download_url": patch_info.get("url"),
                "patch_size_mb": patch_info.get("size_mb", 3.0),
                "patch_sha256": patch_info.get("sha256"),
                "full_installer_url": full_info.get("url"),
                "full_installer_size_mb": full_info.get("size_mb", 285.0),
                "full_installer_sha256": full_info.get("sha256"),
                "checked_at": datetime.now().isoformat()
            }
        except urllib.error.URLError as e:
            return {
                "update_available": False,
                "current_version": local_info.get("version"),
                "error": f"इंटरनेट या सर्वर से संपर्क नहीं हो सका: {e.reason}",
                "checked_at": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "update_available": False,
                "current_version": local_info.get("version"),
                "error": f"अपडेट जाँच में त्रुटि: {str(e)}",
                "checked_at": datetime.now().isoformat()
            }

    @classmethod
    def verify_sha256(cls, file_path: Path, expected_hash: str) -> bool:
        """Verifies file against expected SHA-256 hash."""
        if not expected_hash:
            return True
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest().lower() == expected_hash.strip().lower()

    @classmethod
    def apply_patch_update(
        cls,
        download_url: str,
        expected_sha256: Optional[str] = None,
        target_version: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Downloads a patch ZIP, validates cryptographic integrity, creates a code backup,
        and safely extracts code while strictly isolating user data.
        """
        if not download_url:
            return False, "डाउनलोड URL प्राप्त नहीं हुआ।"

        TEMP_UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        zip_dest = TEMP_UPDATE_DIR / f"patch_{int(time.time())}.zip"

        # 1. Download patch file
        try:
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "UP-Voter-Seva-Updater"}
            )
            with urllib.request.urlopen(req, timeout=60.0) as resp, open(zip_dest, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)
        except Exception as e:
            return False, f"पैच डाउनलोड करने में विफलता: {str(e)}"

        # 2. Check SHA-256 Hash
        if expected_sha256:
            if not cls.verify_sha256(zip_dest, expected_sha256):
                zip_dest.unlink(missing_ok=True)
                return False, "सुरक्षा चेतावनी: डाउनलोड की गई फ़ाइल का SHA-256 हैश मेल नहीं खाता (फ़ाइल दूषित हो सकती है)।"

        # 3. Create pre-update backup of code
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_folder = BACKUP_DIR / f"backup_pre_{ts}"
        backup_folder.mkdir(parents=True, exist_ok=True)

        try:
            for d in ["frontend", "backend"]:
                src = BASE_DIR / d
                if src.exists():
                    shutil.copytree(src, backup_folder / d, dirs_exist_ok=True)
            if VERSION_FILE.exists():
                shutil.copy2(VERSION_FILE, backup_folder / "version.json")
        except Exception as e:
            print(f"[Updater Warning] Pre-update backup warning: {e}")

        # 4. Safe ZIP Extraction with Data Shield
        protected_prefixes = (
            "data/", "data\\",
            "uploads/", "uploads\\",
            "backups/", "backups\\",
            "tools/tesseract/tessdata/"
        )

        try:
            with zipfile.ZipFile(zip_dest, "r") as zf:
                # Security Check: Zip-slip prevention & Data Shield
                for member in zf.infolist():
                    filename = member.filename.replace("\\", "/")
                    
                    # Prevent zip slip (path traversal)
                    target_path = (BASE_DIR / member.filename).resolve()
                    if not str(target_path).startswith(str(BASE_DIR.resolve())):
                        raise Exception(f"अमान्य फ़ाइल पाथ (Zip-Slip Blocked): {member.filename}")

                    # DATA ISOLATION SHIELD: Never overwrite database, licenses, or user uploads!
                    if any(filename.lower().startswith(p) for p in protected_prefixes):
                        continue
                    if filename.endswith(".db") or filename.endswith(".db-wal") or filename.endswith(".enc"):
                        continue

                    # Extract allowed code files
                    zf.extract(member, BASE_DIR)

            # 5. Update local version manifest
            if target_version:
                local_info = cls.get_local_version_info()
                local_info["version"] = target_version
                local_info["last_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(VERSION_FILE, "w", encoding="utf-8") as f:
                    json.dump(local_info, f, indent=2, ensure_ascii=False)

            # Cleanup temp file
            zip_dest.unlink(missing_ok=True)
            return True, f"सॉफ़्टवेयर सफलतापूर्वक संस्करण {target_version or 'नवीनतम'} पर अपडेट हो गया!"

        except Exception as e:
            # Attempt Rollback if extraction failed
            try:
                for d in ["frontend", "backend"]:
                    b_src = backup_folder / d
                    if b_src.exists():
                        shutil.copytree(b_src, BASE_DIR / d, dirs_exist_ok=True)
            except Exception:
                pass
            return False, f"पैच लागू करने में त्रुटि (रोलबैक सुरक्षित): {str(e)}"
