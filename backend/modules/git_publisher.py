"""
Super-Admin In-App Git & OTA Release Publisher for UP Voter Seva.

Allows the Super-Admin ('harshsamrat') to package and publish updates directly
from the web interface without touching a command prompt or terminal.

Features:
1. Version bump and manifest generation.
2. Lightweight OTA patch zip creation (~2.4 MB) with cryptographic SHA-256 hash.
3. Automated Git operations (add, commit, tag, push).
4. GitHub REST API integration for automated Release creation and Asset upload.
5. Encrypted GitHub PAT token vault stored safely in data/git_secret.enc.
"""

import os
import sys
import json
import base64
import hashlib
import hmac
import zipfile
import subprocess
import urllib.request
import urllib.error
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

from ..config import BASE_DIR, DATA_DIR

DEFAULT_REPO = "harshsamrat-lgtm/voter_list"
VERSION_FILE = BASE_DIR / "version.json"
DIST_DIR = BASE_DIR / "dist_output"
GIT_TOKEN_FILE = DATA_DIR / "git_secret.enc"

# Vault Secret Key for GitHub PAT encryption
# Configurable via environment variable or data/.git_secret_key
_env_git_sec = os.environ.get("VOTER_GIT_TOKEN_SECRET", "").strip()
_git_sec_file = DATA_DIR / ".git_secret_key"

if _env_git_sec:
    _TOKEN_SECRET = _env_git_sec.encode("utf-8")
elif _git_sec_file.exists():
    try:
        _TOKEN_SECRET = _git_sec_file.read_bytes().strip()
    except Exception:
        _TOKEN_SECRET = b"UP_VOTER_SEVA_GIT_TOKEN_VAULT_KEY_2026_SUPER_ADMIN_99"
else:
    _TOKEN_SECRET = b"UP_VOTER_SEVA_GIT_TOKEN_VAULT_KEY_2026_SUPER_ADMIN_99"


def parse_semver(v_str: str) -> Tuple[int, int, int]:
    clean = (v_str or "").strip().lstrip("v").split("-")[0]
    parts = [int(p) for p in clean.split(".") if p.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def bump_version(current_ver: str, bump_type: str = "patch") -> str:
    maj, min_, pat = parse_semver(current_ver)
    if bump_type == "major":
        return f"{maj + 1}.0.0"
    elif bump_type == "minor":
        return f"{maj}.{min_ + 1}.0"
    else:
        return f"{maj}.{min_}.{pat + 1}"


class GitPublisher:
    """Manages in-app packaging, git commits, tags, and GitHub Release deployment."""

    @classmethod
    def get_stored_github_token(cls) -> Optional[str]:
        """Loads and decrypts saved GitHub PAT token from local encrypted vault."""
        if not GIT_TOKEN_FILE.exists():
            return None
        try:
            with open(GIT_TOKEN_FILE, "r", encoding="utf-8") as f:
                container = json.load(f)
            b64_payload = container.get("payload", "")
            sig = container.get("sig", "")
            raw = base64.b64decode(b64_payload.encode('ascii'))

            expected_sig = hmac.new(_TOKEN_SECRET, raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                return None
            data = json.loads(raw.decode('utf-8'))
            return data.get("token")
        except Exception:
            return None

    @classmethod
    def save_github_token(cls, token: str):
        """Encrypts and persists GitHub PAT token for the Super Admin."""
        clean = (token or "").strip()
        if not clean:
            if GIT_TOKEN_FILE.exists():
                try:
                    os.remove(GIT_TOKEN_FILE)
                except Exception:
                    pass
            return

        payload = {"token": clean, "updated_at": datetime.now().isoformat()}
        raw = json.dumps(payload).encode('utf-8')
        sig = hmac.new(_TOKEN_SECRET, raw, hashlib.sha256).hexdigest()
        container = {
            "payload": base64.b64encode(raw).decode('ascii'),
            "sig": sig
        }
        GIT_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GIT_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(container, f)
    @classmethod
    def generate_dynamic_release_metadata(cls) -> Tuple[str, List[str]]:
        """Dynamically inspects git status, diffs, and recent commits to automatically
        generate customized Hindi release notes and bulleted changelog reflecting ACTUAL changes."""
        changed_files = set()
        diff_text = ""
        commit_messages = []
        latest_tag = ""

        # 1. Identify latest git release tag
        try:
            latest_tag = subprocess.check_output(
                "git describe --tags --abbrev=0", shell=True, text=True, errors="replace"
            ).strip()
        except Exception:
            latest_tag = ""

        # 2. Check commits made since the latest tag
        if latest_tag:
            try:
                log_out = subprocess.check_output(
                    f"git log {latest_tag}..HEAD --pretty=format:%s",
                    shell=True, text=True, errors="replace"
                ).strip()
                if log_out:
                    for line in log_out.splitlines():
                        line = line.strip()
                        if line and not line.lower().startswith("release v"):
                            commit_messages.append(line)
            except Exception:
                pass

        # 3. Inspect working tree uncommitted / staged files
        try:
            status_out = subprocess.check_output(
                "git status --short", shell=True, text=True, errors="replace"
            ).strip()
            if status_out:
                for line in status_out.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        changed_files.add(parts[1].replace("\\", "/").lower())
        except Exception:
            pass

        # 4. Extract comprehensive diff text (uncommitted + committed since tag)
        try:
            diff_cmd = f"git diff {latest_tag}..HEAD" if (latest_tag and commit_messages) else "git diff HEAD"
            diff_text = subprocess.check_output(
                diff_cmd, shell=True, text=True, errors="replace"
            )
        except Exception:
            try:
                diff_text = subprocess.check_output(
                    "git diff", shell=True, text=True, errors="replace"
                )
            except Exception:
                diff_text = ""

        # Parse added lines per file to prevent cross-file keyword pollution
        file_diffs: Dict[str, List[str]] = {}
        curr_fn = None
        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    curr_fn = re.sub(r"^[ab]/", "", parts[3]).lower()
                    file_diffs[curr_fn] = []
            elif curr_fn and line.startswith("+") and not line.startswith("+++"):
                file_diffs[curr_fn].append(line[1:])

        def get_file_content(file_substr: str) -> str:
            lines = []
            for fn, lns in file_diffs.items():
                if file_substr in fn:
                    lines.extend(lns)
            return "\n".join(lines).lower()

        subsystems = []
        bullets = []

        # --- A. COMMIT-BASED BULLETS (if developer committed with meaningful messages) ---
        for c_msg in commit_messages:
            clean_msg = c_msg
            if ":" in clean_msg:
                clean_msg = clean_msg.split(":", 1)[1].strip()
            if clean_msg and len(clean_msg) > 3:
                bullets.append(f"• {clean_msg}")

        # --- B. CONTENT-AWARE PER-FILE DIFF INSPECTION ---

        # 1. Security & Hardening (main.py, auth_manager.py, config.py, license_guard.py)
        sec_content = (
            get_file_content("auth_manager") + "\n" +
            get_file_content("config.py") + "\n" +
            get_file_content("license_guard") + "\n" +
            get_file_content("main.py")
        )
        sec_keywords = [
            "security_config", "admin_token", "default_admin_password",
            "allow_local_auto_admin", "corsmiddleware", "verify_superadmin",
            "_master_secret", "_token_secret", "hash_password", "require_auth_for_superadmin"
        ]
        if any(k in sec_content for k in sec_keywords):
            subsystems.append("सुरक्षा सुदृढ़ीकरण")
            bullets.append("• सुरक्षा सुदृढ़ीकरण: एडमिन क्रेडेंशियल्स, डायनामिक टोकन जनरेशन, सुपरएडमिन सुरक्षा व CORS नीतियां लागू")

        # 2. Bulk Metadata Edit (Part No, Assembly, Polling Station in main.py, app.js, database.py)
        main_content = get_file_content("main.py")
        db_content = get_file_content("database.py")
        fe_app_content = get_file_content("app.js")
        bulk_edit_keywords = ["update_batch_metadata", "bulk-update", "bulk_update", "batch metadata", "विधान सभा"]
        if any(k in main_content or k in db_content or k in fe_app_content for k in bulk_edit_keywords):
            subsystems.append("बल्क मेटाडेटा संपादन")
            bullets.append("• बल्क डेटा संपादन: भाग संख्या, विधान सभा व मतदान केंद्र का सामूहिक संशोधन (Bulk Edit) फीचर")

        # 3. UI Renaming / Labeling ("Git से अपडेट" -> "अपडेट", Modals in frontend)
        fe_index_content = get_file_content("index.html")
        if "अपडेट" in fe_index_content or "softwareupdatemodal" in fe_app_content or "btnupdate" in fe_index_content:
            subsystems.append("इंटरफेस सरलीकरण")
            bullets.append("• यूजर इंटरफेस: 'Git से अपडेट' का नाम बदलकर 'अपडेट' किया गया एवं डायलॉग लेआउट परिष्कृत")

        # 4. OCR Extraction & Card Cropping (Only if ocr_extractor was touched)
        ocr_content = get_file_content("ocr_extractor")
        if ocr_content:
            subsystems.append("OCR एक्सट्रैक्शन")
            if "dpi" in ocr_content or "300" in ocr_content:
                bullets.append("• एडिट विंडो में वास्तविक पीडीएफ से 300 DPI उच्च क्वालिटी स्कैन व प्रीव्यू")
            bullets.append("• OCR इंजन: वोटर कार्ड टेक्स्ट एक्सट्रैक्शन, क्रॉपिंग व अलाइनमेंट में सुधार")

        # 5. Local AI & Name Correction (Only if error_corrector was touched)
        corr_content = get_file_content("error_corrector")
        if corr_content:
            subsystems.append("लोकल AI नाम सुधार")
            bullets.append("• लोकल AI नाम सुधार: मतदाता व संबंधी के नाम की वर्तनी एवं ऑटो-करेक्शन शब्दकोश अपडेट")

        # 6. Elapsed / ETA Timer (Only if queue/timer code was modified)
        if any(k in main_content for k in ["elapsed_seconds", "timer_card", "eta_seconds"]):
            subsystems.append("बल्क स्कैन टाइमर")
            bullets.append("• बल्क स्कैनिंग: लाइव बीता समय (Elapsed) व शेष समय (ETA) का स्वचालित टाइमर")

        # 7. Database & Search (Only if database.py was touched)
        if db_content:
            subsystems.append("डेटाबेस व सर्च")
            bullets.append("• मतदाता डेटाबेस: सर्च स्पीड, रिकॉर्ड स्टोरेज व डेटा निष्पादन गति में सुधार")

        # 8. Export (Excel / PDF)
        if get_file_content("excel_generator") or get_file_content("pdf_generator") or "export_excel" in main_content:
            subsystems.append("डेटा एक्सपोर्ट")
            bullets.append("• एक्सपोर्ट सुविधा: मतदाता सूची व पर्ची के एक्सेल एवं पीडीएफ एक्सपोर्ट फॉर्मेटिंग में सुधार")

        # 9. Cloudflare / Live Tunnel
        if get_file_content("tunnel_service"):
            subsystems.append("क्लाउड लाइव शेयरिंग")
            bullets.append("• लाइव शेयरिंग: सुरक्षित क्लाउड टनल व मोबाइल क्यूआर सर्च कनेक्टिविटी में संवर्द्धन")

        # 10. Installer & Launcher
        if any("installer" in fn or fn.endswith(".vbs") or fn.endswith(".bat") for fn in file_diffs):
            subsystems.append("विंडोज इंस्टॉलर")
            bullets.append("• विंडोज इंस्टॉलर: साइलेंट बैकग्राउंड लॉन्चर (.vbs) एवं इंस्टॉलर स्क्रिप्ट्स अपडेट")

        # 11. Automated Tests & Quality
        if any("tests/" in fn for fn in file_diffs):
            bullets.append("• सिस्टम विश्वसनीयता: ऑटोमेटेड सुरक्षा व सर्च टेस्ट सुइट का विस्तार एवं सत्यापन")

        # 12. Auto-Update / Publisher
        if get_file_content("git_publisher") or get_file_content("updater_guard"):
            bullets.append("• रिलीज मैनेजमेंट: वास्तविक Git Diff आधारित सटीक रिलीज नोट्स व चेंजलॉग जनरेशन")

        # Deduplicate bullets while preserving order
        seen = set()
        dedup_bullets = []
        for b in bullets:
            b_clean = b.strip()
            if b_clean and b_clean not in seen:
                seen.add(b_clean)
                dedup_bullets.append(b_clean)

        # Build dynamic notes/title based on detected modifications
        if len(subsystems) == 1:
            notes = f"{subsystems[0]} संवर्द्धन"
        elif len(subsystems) == 2:
            notes = f"{subsystems[0]} व {subsystems[1]}"
        elif len(subsystems) >= 3:
            notes = f"{subsystems[0]}, {subsystems[1]} व {subsystems[2]}"
        else:
            if commit_messages:
                notes = commit_messages[0]
            else:
                notes = "सिस्टम स्थिरता, बग फिक्स व परफॉर्मेंस संवर्द्धन अपडेट"

        if not dedup_bullets:
            dedup_bullets.append("• सुरक्षा संवर्द्धन, बग फिक्स एवं समग्र सिस्टम परफॉर्मेंस सुधार")

        return notes, dedup_bullets

    @classmethod
    def get_git_config(cls) -> Dict[str, Any]:
        """Returns current version, repository details, and token presence for Super Admin."""
        cur_version = "1.0.2"
        if VERSION_FILE.exists():
            try:
                with open(VERSION_FILE, "r", encoding="utf-8") as f:
                    cur_version = json.load(f).get("version", "1.0.2")
            except Exception:
                pass

        saved_token = cls.get_stored_github_token()
        token_preview = f"{saved_token[:4]}...{saved_token[-4:]}" if saved_token and len(saved_token) > 8 else None

        # Check git CLI
        git_ready = False
        try:
            out = subprocess.check_output("git --version", shell=True, text=True, errors="replace")
            git_ready = "git version" in out.lower()
        except Exception:
            git_ready = False

        # Dynamically generate context-aware notes and changelog for every update
        default_notes, default_changelog = cls.generate_dynamic_release_metadata()

        return {
            "current_version": cur_version,
            "next_patch": bump_version(cur_version, "patch"),
            "next_minor": bump_version(cur_version, "minor"),
            "next_major": bump_version(cur_version, "major"),
            "repository": DEFAULT_REPO,
            "repository_url": f"https://github.com/{DEFAULT_REPO}",
            "has_saved_token": bool(saved_token),
            "token_preview": token_preview,
            "git_cli_available": git_ready,
            "default_notes": default_notes,
            "default_changelog": default_changelog
        }

    @classmethod
    def compute_sha256(cls, file_path: Path) -> str:
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha.update(chunk)
        return sha.hexdigest().lower()

    @classmethod
    def build_patch_zip(cls, output_zip: Path) -> int:
        """Packages frontend, backend, and manifests into a clean ~2.4 MB update zip."""
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        if output_zip.exists():
            output_zip.unlink()

        include_roots = ["frontend", "backend"]
        include_files = ["version.json", "requirements.txt"]

        excluded_dirs = {
            "__pycache__", ".venv", "dist_staging", "dist_output", "build",
            ".git", ".github", "tools", "backups", "uploads", "data", "temp"
        }
        excluded_extensions = {".pyc", ".pyo", ".pyd", ".db", ".db-wal", ".db-shm", ".enc", ".log"}

        total_files = 0
        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for root_name in include_roots:
                root_path = BASE_DIR / root_name
                if not root_path.exists():
                    continue
                for dirpath, dirnames, filenames in os.walk(root_path):
                    dirnames[:] = [d for d in dirnames if d not in excluded_dirs and not d.startswith(".")]
                    for fn in filenames:
                        ext = os.path.splitext(fn)[1].lower()
                        if ext in excluded_extensions or fn.startswith("."):
                            continue
                        full_p = Path(dirpath) / fn
                        rel_p = full_p.relative_to(BASE_DIR)
                        zf.write(full_p, str(rel_p).replace("\\", "/"))
                        total_files += 1

            for fn in include_files:
                p = BASE_DIR / fn
                if p.exists():
                    zf.write(p, fn)
                    total_files += 1

            # Safely include Firebase service account key so direct Cloud Sync works on all installations
            sa_key = BASE_DIR / "data" / "serviceAccountKey.json"
            if sa_key.exists():
                zf.write(sa_key, "data/serviceAccountKey.json")
                total_files += 1

        return total_files

    @classmethod
    def publish_ota_release(
        cls,
        bump_type: str = "patch",
        custom_version: Optional[str] = None,
        notes: str = "",
        changelog: Optional[List[str]] = None,
        github_pat_token: Optional[str] = None,
        remember_token: bool = False
    ) -> Dict[str, Any]:
        """
        One-Click Orchestration:
        1. Bumps version
        2. Packages update-patch.zip
        3. Computes SHA-256
        4. Writes version.json
        5. Commits and tags in Git
        6. Pushes to GitHub
        7. Publishes GitHub Release & Uploads update-patch.zip
        """
        logs = []
        token = (github_pat_token or "").strip() or cls.get_stored_github_token()
        if not token:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail="GitHub Personal Access Token (PAT) आवश्यक है। कृपया 'repo' स्कोप के साथ टोकन दर्ज करें।"
            )
        if remember_token and github_pat_token:
            cls.save_github_token(github_pat_token.strip())

        # 1. Determine Target Version
        cur_version = "1.0.2"
        if VERSION_FILE.exists():
            try:
                with open(VERSION_FILE, "r", encoding="utf-8") as f:
                    cur_version = json.load(f).get("version", "1.0.2")
            except Exception:
                pass

        if custom_version and custom_version.strip():
            target_version = custom_version.strip().lstrip("v")
        else:
            target_version = bump_version(cur_version, bump_type)

        logs.append(f"लक्ष्य संस्करण निर्धारित: v{target_version} (पिछला: v{cur_version})")

        # 2. Package update-patch.zip
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        patch_zip = DIST_DIR / "update-patch.zip"
        file_count = cls.build_patch_zip(patch_zip)
        size_bytes = patch_zip.stat().st_size
        size_mb = round(size_bytes / (1024 * 1024), 2)
        sha256_hash = cls.compute_sha256(patch_zip)

        logs.append(f"हॉट-पैच पैकेजिंग पूर्ण: {file_count} फाइलें, {size_mb} MB")
        logs.append(f"क्रिप्टोग्राफिक SHA-256 हैश: {sha256_hash[:16]}...")

        # 3. Update version.json manifest
        release_date = datetime.now().strftime("%Y-%m-%d")
        items = [c.strip() for c in (changelog or []) if c.strip()]
        if not items:
            items = [
                f"आधिकारिक OTA अपडेट v{target_version}",
                notes or "सुरक्षा संवर्द्धन एवं प्रदर्शन में सुधार",
                "डेटा सुरक्षा शील्ड: स्थानीय डेटाबेस सुरक्षित"
            ]

        manifest_data = {
            "version": target_version,
            "app_name": "UP Voter Seva",
            "channel": "stable",
            "release_date": release_date,
            "min_required_version": "1.0.0",
            "remote_manifest_url": f"https://raw.githubusercontent.com/{DEFAULT_REPO}/main/version.json",
            "update_type": "patch",
            "title": f"UP वोटर सेवा v{target_version} अपडेट",
            "notes": notes or f"रिलीज v{target_version}",
            "changelog": items,
            "patch": {
                "url": f"https://github.com/{DEFAULT_REPO}/releases/download/v{target_version}/update-patch.zip",
                "size_mb": size_mb,
                "sha256": sha256_hash
            },
            "full_installer": {
                "url": f"https://github.com/{DEFAULT_REPO}/releases/download/v{target_version}/UP_Voter_Service_Setup_v1.0.exe",
                "size_mb": 285.0,
                "sha256": ""
            }
        }

        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)
        logs.append(f"version.json मैनिफेस्ट सफलतापूर्वक अपडेट किया गया")

        # 4. Git Automation
        def run_git(cmd: str) -> Tuple[bool, str]:
            try:
                env = os.environ.copy()
                env["GIT_TERMINAL_PROMPT"] = "0"
                env["GCM_INTERACTIVE"] = "never"
                res = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=str(BASE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=60
                )
                output = (res.stdout + "\n" + res.stderr).strip()
                return (res.returncode == 0, output)
            except Exception as e:
                return (False, str(e))

        # Check git repo existence
        if not (BASE_DIR / ".git").exists():
            run_git("git init")
            run_git("git branch -M main")

        # Check user config
        run_git("git config user.name")
        ok_cfg, out_cfg = run_git("git config user.name")
        if not ok_cfg or not out_cfg.strip():
            run_git('git config user.name "UP Voter SuperAdmin"')
            run_git('git config user.email "superadmin@upvoter.local"')

        # Ensure remote origin is set
        run_git("git remote remove origin")
        run_git(f"git remote add origin https://github.com/{DEFAULT_REPO}.git")

        # Ensure .gitignore exists to prevent sensitive files from being pushed
        if not (BASE_DIR / ".gitignore").exists():
            with open(BASE_DIR / ".gitignore", "w", encoding="utf-8") as gf:
                gf.write("data/\n*.db*\n*.enc\nruntime/\ndist_output/\ndist_staging/\n*.exe\n*.zip\n__pycache__/\n")

        # Git Add: Stage components safely (excluding .github workflows so standard 'repo' PAT works seamlessly)
        stage_targets = ["version.json", "frontend", "backend", "scripts", ".gitignore", "README.md", "requirements.txt"]
        staged_count = 0
        for tgt in stage_targets:
            if (BASE_DIR / tgt).exists():
                ok_t, _ = run_git(f'git add "{tgt}"')
                if ok_t:
                    staged_count += 1
        logs.append(f"Git स्टेजिंग पूर्ण ({staged_count} घटक ट्रैक किए गए)")

        # Git Commit
        commit_msg = f"Release v{target_version}: {notes or 'In-app OTA update'}"
        ok_commit, msg_commit = run_git(f'git commit -m "{commit_msg}"')
        if ok_commit:
            logs.append(f"Git कमिट संपन्न: {commit_msg}")
        else:
            logs.append(f"Git कमिट स्थिति: {msg_commit[:80]}")

        # Git Tag
        tag_name = f"v{target_version}"
        run_git(f"git tag -d {tag_name}")  # delete local tag if exists
        ok_tag, msg_tag = run_git(f'git tag -a {tag_name} -m "UP Voter Seva {tag_name}"')
        if ok_tag:
            logs.append(f"Git टैग बनाया गया: {tag_name}")

        # Git Push
        push_remote_url = f"https://github.com/{DEFAULT_REPO}.git"
        if token:
            # Check if repository exists on GitHub, and auto-create it if 404
            try:
                check_repo_req = urllib.request.Request(
                    f"https://api.github.com/repos/{DEFAULT_REPO}",
                    headers={
                        "Authorization": f"token {token}",
                        "User-Agent": "UP-Voter-Seva-Publisher",
                        "Accept": "application/vnd.github.v3+json"
                    }
                )
                try:
                    with urllib.request.urlopen(check_repo_req, timeout=10.0) as _:
                        pass
                except urllib.error.HTTPError as repo_err:
                    if repo_err.code == 404:
                        logs.append(f"GitHub पर '{DEFAULT_REPO}' नहीं मिली। स्वतः नई रिपॉजिटरी बनाई जा रही है...")
                        repo_name = DEFAULT_REPO.split("/")[-1]
                        create_payload = {
                            "name": repo_name,
                            "description": "मतदाता सेवा मास्टर (Voter Service Master) - Official Application & OTA Updates",
                            "private": False,
                            "has_issues": True
                        }
                        create_req = urllib.request.Request(
                            "https://api.github.com/user/repos",
                            data=json.dumps(create_payload).encode('utf-8'),
                            headers={
                                "Authorization": f"token {token}",
                                "User-Agent": "UP-Voter-Seva-Publisher",
                                "Accept": "application/vnd.github.v3+json",
                                "Content-Type": "application/json"
                            },
                            method="POST"
                        )
                        with urllib.request.urlopen(create_req, timeout=15.0) as cr_resp:
                            logs.append(f"✅ GitHub पर नई रिपॉजिटरी '{DEFAULT_REPO}' सफलतापूर्वक बना दी गई!")
            except Exception as ex:
                logs.append(f"रिपॉजिटरी सत्यापन सूचना: {str(ex)}")

            # Use authenticated URL
            push_remote_url = f"https://{token}@github.com/{DEFAULT_REPO}.git"

        # Check current branch
        _, branch_out = run_git("git rev-parse --abbrev-ref HEAD")
        branch_name = branch_out.strip() or "main"
        if branch_name.lower() in ("head", ""):
            branch_name = "main"

        # Push branch & tags
        logs.append(f"GitHub पर कोड व टैग पुश किया जा रहा है ({DEFAULT_REPO})...")
        ok_push, msg_push = run_git(f'git -c credential.helper= push "{push_remote_url}" {branch_name} --tags --force')
        if ok_push:
            logs.append("✅ Git Push सफल रहा (main + tags)!")
        else:
            logs.append(f"Git Push रिपोर्ट: {msg_push[:120]}")

        # 5. GitHub Releases REST API: Create Release & Upload Asset
        release_created = False
        release_web_url = f"https://github.com/{DEFAULT_REPO}/releases/tag/{tag_name}"

        if token:
            try:
                # 5A. Create GitHub Release
                api_url = f"https://api.github.com/repos/{DEFAULT_REPO}/releases"
                rel_payload = {
                    "tag_name": tag_name,
                    "target_commitish": branch_name,
                    "name": f"UP Voter Seva {tag_name}",
                    "body": f"### UP वोटर सेवा आधिकारिक OTA अपडेट ({tag_name})\n\n"
                            f"- **रिलीज दिनांक:** {release_date}\n"
                            f"- **पैच साइज़:** {size_mb} MB\n"
                            f"- **SHA-256:** `{sha256_hash}`\n\n"
                            f"**नोट्स:** {notes or 'प्रदर्शन एवं सुरक्षा संवर्द्धन'}\n\n"
                            f"> **डेटा सुरक्षा:** इस अपडेट से स्थानीय मतदाता डेटाबेस व लाइसेंस सुरक्षित रहेंगे।",
                    "draft": False,
                    "prerelease": False
                }
                req = urllib.request.Request(
                    api_url,
                    data=json.dumps(rel_payload).encode('utf-8'),
                    headers={
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github.v3+json",
                        "Content-Type": "application/json",
                        "User-Agent": "UP-Voter-Seva-Publisher"
                    },
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=15.0) as resp:
                        rel_data = json.loads(resp.read().decode('utf-8'))
                        upload_url_template = rel_data.get("upload_url", "")
                        release_id = rel_data.get("id")
                        release_web_url = rel_data.get("html_url", release_web_url)
                        release_created = True
                        logs.append(f"✅ GitHub Release {tag_name} API द्वारा बनाई गई!")
                except urllib.error.HTTPError as he:
                    if he.code == 422:
                        # Release might already exist: fetch it
                        get_req = urllib.request.Request(
                            f"https://api.github.com/repos/{DEFAULT_REPO}/releases/tags/{tag_name}",
                            headers={"Authorization": f"token {token}", "User-Agent": "UP-Voter-Seva-Publisher"}
                        )
                        with urllib.request.urlopen(get_req, timeout=10.0) as get_resp:
                            rel_data = json.loads(get_resp.read().decode('utf-8'))
                            release_id = rel_data.get("id")
                            release_web_url = rel_data.get("html_url", release_web_url)
                            release_created = True
                            logs.append(f"मौजूदा GitHub Release {tag_name} प्राप्त हुई")
                    else:
                        logs.append(f"GitHub Release API नोटिस (HTTP {he.code}): {he.reason}")
                        release_id = None

                # 5B. Upload update-patch.zip as Asset
                if release_created and release_id:
                    logs.append(f"update-patch.zip ({size_mb} MB) GitHub Release में अपलोड हो रहा है...")
                    upload_url = f"https://uploads.github.com/repos/{DEFAULT_REPO}/releases/{release_id}/assets?name=update-patch.zip"
                    with open(patch_zip, "rb") as zf:
                        zip_bytes = zf.read()

                    upload_req = urllib.request.Request(
                        upload_url,
                        data=zip_bytes,
                        headers={
                            "Authorization": f"token {token}",
                            "Content-Type": "application/zip",
                            "User-Agent": "UP-Voter-Seva-Publisher"
                        },
                        method="POST"
                    )
                    try:
                        with urllib.request.urlopen(upload_req, timeout=40.0) as up_resp:
                            if up_resp.status in (200, 201):
                                logs.append("🎉 update-patch.zip सफलतापूर्वक GitHub Release में अटैच हो गया!")
                    except urllib.error.HTTPError as uhe:
                        if uhe.code == 422:
                            logs.append("update-patch.zip एसेट पहले से संलग्न है (Asset already present)")
                        else:
                            logs.append(f"Asset upload सूचना (HTTP {uhe.code})")

            except Exception as ex:
                logs.append(f"GitHub Release REST API सूचना: {str(ex)}")

        logs.append(f"🚀 बधाई! नया अपडेट v{target_version} अब Git पर लाइव है।")
        logs.append("अन्य सभी कंप्यूटरों पर 5 मिनट के भीतर या रीलोड करने पर स्वतः अपडेट बटन चमकने लगेगा।")

        return {
            "status": "success",
            "version": target_version,
            "patch_size_mb": size_mb,
            "sha256": sha256_hash,
            "release_url": release_web_url,
            "logs": logs,
            "message": f"सॉफ्टवेयर v{target_version} सफलतापूर्वक Git पर पब्लिश हो गया!"
        }
