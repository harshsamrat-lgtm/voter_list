#!/usr/bin/env python3
"""
OTA Hot-Patch & Release Packaging Tool for UP Voter Seva.

Usage:
    python scripts/publish_update.py --bump patch --notes "Bugfixes and performance"
    python scripts/publish_update.py --version 1.0.2 --repo harshsamrat/voter-list-converter

What this tool does:
1. Calculates or bumps the semantic version.
2. Packages frontend/ and backend/ into dist_output/update-patch.zip.
   (Strictly isolates and ignores user data, databases, and licenses).
3. Computes the cryptographic SHA-256 hash and size of update-patch.zip.
4. Updates version.json with the patch manifest.
5. Displays ready-to-run Git push commands for zero-token deployment.
"""

import os
import sys
import json
import zipfile
import hashlib
import argparse
from datetime import datetime
from pathlib import Path

# Force UTF-8 on Windows terminal to prevent cp1252 charmap errors
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT_DIR / "version.json"
DIST_DIR = ROOT_DIR / "dist_output"


def parse_semver(v_str: str):
    clean = (v_str or "").strip().lstrip("v").split("-")[0]
    parts = [int(p) for p in clean.split(".") if p.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return parts[:3]


def bump_version(current_ver: str, bump_type: str = "patch") -> str:
    maj, min_, pat = parse_semver(current_ver)
    if bump_type == "major":
        return f"{maj + 1}.0.0"
    elif bump_type == "minor":
        return f"{maj}.{min_ + 1}.0"
    else:
        return f"{maj}.{min_}.{pat + 1}"


def compute_sha256(file_path: Path) -> str:
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest().lower()


def build_patch_zip(output_zip: Path):
    """Packages frontend and backend files into a lightweight OTA patch zip."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    # Directories and files to include
    include_roots = ["frontend", "backend"]
    include_files = ["version.json", "requirements.txt"]

    # Patterns and prefixes to strictly exclude
    excluded_dirs = {
        "__pycache__", ".venv", "dist_staging", "dist_output", "build",
        ".git", ".github", "tools", "backups", "uploads", "data", "temp"
    }
    excluded_extensions = {".pyc", ".pyo", ".pyd", ".db", ".db-wal", ".db-shm", ".enc", ".log"}

    total_files = 0
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # Include directories
        for root_name in include_roots:
            root_path = ROOT_DIR / root_name
            if not root_path.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root_path):
                # Filter out excluded directories in-place
                dirnames[:] = [d for d in dirnames if d not in excluded_dirs and not d.startswith(".")]

                for fn in filenames:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in excluded_extensions or fn.startswith("."):
                        continue
                    full_p = Path(dirpath) / fn
                    rel_p = full_p.relative_to(ROOT_DIR)
                    zf.write(full_p, str(rel_p).replace("\\", "/"))
                    total_files += 1

        # Include single root files
        for fn in include_files:
            p = ROOT_DIR / fn
            if p.exists():
                zf.write(p, fn)
                total_files += 1

    return total_files


def main():
    parser = argparse.ArgumentParser(description="OTA Hot-Patch Packaging Tool for UP Voter Seva")
    parser.add_argument("--version", type=str, help="Explicit target version (e.g. 1.0.2)")
    parser.add_argument("--bump", choices=["patch", "minor", "major"], default="patch", help="Semantic bump type")
    parser.add_argument("--repo", type=str, default="harshsamrat-lgtm/voter_list", help="GitHub repo (owner/repo)")
    parser.add_argument("--notes", type=str, default="", help="Short release note")
    parser.add_argument("--changelog", action="append", help="Changelog bullet point (can use multiple times)")
    args = parser.parse_args()

    # Read current version
    current_data = {}
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                current_data = json.load(f)
        except Exception:
            pass

    current_ver = current_data.get("version", "1.0.1")

    # Determine target version
    target_ver = args.version.strip() if args.version else bump_version(current_ver, args.bump)

    print("=" * 65)
    print("🚀 UP Voter Seva - OTA Patch Packaging Tool")
    print("=" * 65)
    print(f"📌 Current Version : v{current_ver}")
    print(f"🎯 Target Version  : v{target_ver}")
    print(f"📦 GitHub Repo     : {args.repo}")
    print("-" * 65)

    # 1. Temporarily prepare version.json with target version
    current_data["version"] = target_ver
    current_data["release_date"] = datetime.now().strftime("%Y-%m-%d")
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(current_data, f, indent=2, ensure_ascii=False)

    # 2. Package update-patch.zip
    patch_zip = DIST_DIR / "update-patch.zip"
    print("⚙️  Creating update-patch.zip...")
    count = build_patch_zip(patch_zip)
    size_bytes = patch_zip.stat().st_size
    size_mb = round(size_bytes / (1024 * 1024), 2)
    sha256_hash = compute_sha256(patch_zip)

    print(f"✅ Patch built: {count} files packaged ({size_mb} MB)")
    print(f"🔐 SHA-256   : {sha256_hash}")

    # 3. Build Changelog
    changelog = args.changelog if args.changelog else [
        f"आधिकारिक OTA अपडेट v{target_ver}",
        args.notes or "सुरक्षा संवर्द्धन एवं प्रदर्शन में सुधार",
        "डेटा सुरक्षा शील्ड: स्थानीय डेटाबेस सुरक्षित"
    ]

    # 4. Generate final version.json
    manifest = {
        "version": target_ver,
        "app_name": current_data.get("app_name", "UP Voter Seva"),
        "channel": "stable",
        "release_date": datetime.now().strftime("%Y-%m-%d"),
        "min_required_version": "1.0.0",
        "remote_manifest_url": f"https://raw.githubusercontent.com/{args.repo}/main/version.json",
        "update_type": "patch",
        "title": f"UP वोटर सेवा v{target_ver} अपडेट",
        "notes": args.notes or "आधिकारिक स्थिर रिलीज",
        "changelog": changelog,
        "patch": {
            "url": f"https://github.com/{args.repo}/releases/download/v{target_ver}/update-patch.zip",
            "size_mb": size_mb,
            "sha256": sha256_hash
        },
        "full_installer": {
            "url": f"https://github.com/{args.repo}/releases/download/v{target_ver}/UP_Voter_Service_Setup_v1.0.exe",
            "size_mb": 285.0,
            "sha256": ""
        }
    }

    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print("📄 version.json updated successfully.")
    print("-" * 65)
    print("🎉 अगला कदम (Next Steps for Developer):")
    print(f"1. Git पर कमिट करें और v{target_ver} टैग बनाएं:")
    print("   git add version.json")
    print(f"   git commit -m \"Release v{target_ver}: {args.notes or 'OTA Update'}\"")
    print(f"   git tag v{target_ver}")
    print("   git push origin main --tags")
    print("")
    print(f"2. GitHub Releases पेज पर v{target_ver} रिलीज़ बनाकर '{patch_zip.name}' फ़ाइल अपलोड करें:")
    print(f"   फ़ाइल लोकेशन: {patch_zip}")
    print("=" * 65)


if __name__ == "__main__":
    main()
