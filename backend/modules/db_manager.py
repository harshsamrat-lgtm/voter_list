"""
Database Manager for UP Voter System.
Handles multi-database registry, creation, naming, active save target selection,
and default search database designation.
"""

import os
import json
import sqlite3
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from ..config import DATA_DIR, DB_PATH


class DatabaseManager:
    """Manages voter SQLite database registries, active targets, and search defaults."""

    REGISTRY_FILE = DATA_DIR / "databases.json"

    @classmethod
    def _ensure_registry(cls) -> Dict[str, Any]:
        """Loads or initializes the databases.json registry file."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        default_db_file = DB_PATH.name  # voters.db

        if not cls.REGISTRY_FILE.exists():
            default_entry = {
                "id": "default",
                "name": "मुख्य मतदाता डेटाबेस (Master Database)",
                "filename": default_db_file,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "description": "प्राथमिक एकीकृत मतदाता सूची डेटाबेस",
                "is_active_target": True,
                "is_default_search": True
            }
            registry = {
                "active_save_id": "default",
                "default_search_id": "default",
                "databases": {
                    "default": default_entry
                }
            }
            cls._write_registry(registry)
            return registry

        try:
            with open(cls.REGISTRY_FILE, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            registry = {
                "active_save_id": "default",
                "default_search_id": "default",
                "databases": {}
            }

        # Auto-discover any untracked .db files in DATA_DIR
        changed = False
        if "databases" not in registry or not isinstance(registry["databases"], dict):
            registry["databases"] = {}
            changed = True

        # Ensure default exists
        if "default" not in registry["databases"]:
            registry["databases"]["default"] = {
                "id": "default",
                "name": "मुख्य मतदाता डेटाबेस (Master Database)",
                "filename": default_db_file,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "description": "प्राथमिक एकीकृत मतदाता सूची डेटाबेस",
                "is_active_target": True,
                "is_default_search": True
            }
            changed = True

        # Check all .db files in DATA_DIR
        for p in DATA_DIR.glob("*.db"):
            fname = p.name
            # Skip temp or backup files
            if fname.startswith("restore_temp_") or "pre_restore_" in fname or "backup_" in fname:
                continue
            # Check if this filename is tracked
            tracked = any(db.get("filename") == fname for db in registry["databases"].values())
            if not tracked:
                new_id = re.sub(r'[^a-zA-Z0-9_-]', '_', p.stem).lower() or f"db_{uuid.uuid4().hex[:6]}"
                if new_id in registry["databases"]:
                    new_id = f"{new_id}_{uuid.uuid4().hex[:4]}"
                clean_title = p.stem.replace("_", " ").replace("-", " ").title()
                registry["databases"][new_id] = {
                    "id": new_id,
                    "name": f"डेटाबेस ({clean_title})",
                    "filename": fname,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "description": f"स्वतः खोजा गया डेटाबेस: {fname}",
                    "is_active_target": False,
                    "is_default_search": False
                }
                changed = True

        # Ensure active_save_id and default_search_id point to valid DBs
        if registry.get("active_save_id") not in registry["databases"]:
            first_key = list(registry["databases"].keys())[0] if registry["databases"] else "default"
            registry["active_save_id"] = first_key
            changed = True

        if registry.get("default_search_id") not in registry["databases"]:
            registry["default_search_id"] = registry.get("active_save_id") or "default"
            changed = True

        # Sync flags
        for db_id, db in registry["databases"].items():
            db["is_active_target"] = (db_id == registry["active_save_id"])
            db["is_default_search"] = (db_id == registry["default_search_id"])

        if changed:
            cls._write_registry(registry)

        return registry

    @classmethod
    def _write_registry(cls, registry: Dict[str, Any]):
        """Persists registry to databases.json atomically."""
        temp_file = cls.REGISTRY_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        temp_file.replace(cls.REGISTRY_FILE)

    @classmethod
    def get_db_path(cls, db_id: Optional[str] = None) -> Path:
        """Returns the file Path for a database id. Defaults to active_save or default."""
        registry = cls._ensure_registry()
        target_id = db_id or registry.get("active_save_id") or "default"
        db_info = registry.get("databases", {}).get(target_id)
        if db_info and db_info.get("filename"):
            return DATA_DIR / db_info["filename"]
        return DB_PATH

    @classmethod
    def get_active_target_db_path(cls) -> Path:
        """Returns the file Path for the currently designated active saving database."""
        return cls.get_db_path(None)

    @classmethod
    def get_default_search_db_path(cls) -> Path:
        """Returns the file Path for the default voter search database."""
        return cls.get_search_db_path(None)

    @classmethod
    def get_search_db_path(cls, db_id: Optional[str] = None) -> Path:
        """Returns the file Path for searching voters. Defaults to default_search_id."""
        registry = cls._ensure_registry()
        target_id = db_id or registry.get("default_search_id") or "default"
        db_info = registry.get("databases", {}).get(target_id)
        if db_info and db_info.get("filename"):
            return DATA_DIR / db_info["filename"]
        return DB_PATH

    @classmethod
    def get_all_databases(cls) -> List[Dict[str, Any]]:
        """Returns list of all registered databases with stats."""
        registry = cls._ensure_registry()
        result = []

        for db_id, item in registry.get("databases", {}).items():
            file_path = DATA_DIR / item.get("filename", f"{db_id}.db")
            size_mb = round(file_path.stat().st_size / (1024 * 1024), 2) if file_path.exists() else 0.0
            
            total_voters = 0
            total_parts = 0
            parts_summary = []

            if file_path.exists():
                try:
                    conn = sqlite3.connect(str(file_path), timeout=5.0)
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    # Check if voters table exists
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voters';")
                    if cur.fetchone():
                        cur.execute("SELECT COUNT(*) FROM voters;")
                        total_voters = cur.fetchone()[0]
                        cur.execute("SELECT DISTINCT part_no, assembly, count(*) as cnt FROM voters GROUP BY part_no, assembly ORDER BY CAST(part_no AS INTEGER);")
                        parts_rows = cur.fetchall()
                        total_parts = len(parts_rows)
                        for pr in parts_rows[:10]:
                            parts_summary.append({
                                "part_no": pr["part_no"] or "",
                                "assembly": pr["assembly"] or "",
                                "count": pr["cnt"]
                            })
                    conn.close()
                except Exception:
                    pass

            result.append({
                "id": db_id,
                "name": item.get("name", db_id),
                "filename": item.get("filename", f"{db_id}.db"),
                "description": item.get("description", ""),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "is_active_target": bool(item.get("is_active_target")),
                "is_default_search": bool(item.get("is_default_search")),
                "total_voters": total_voters,
                "total_parts": total_parts,
                "size_mb": size_mb,
                "exists": file_path.exists(),
                "parts_summary": parts_summary
            })

        # Sort: Active target first, then default search, then name
        result.sort(key=lambda x: (not x["is_active_target"], not x["is_default_search"], x["name"].lower()))
        return result

    @classmethod
    def get_database_info(cls, db_id: str) -> Optional[Dict[str, Any]]:
        """Returns details of a single database."""
        all_dbs = cls.get_all_databases()
        for d in all_dbs:
            if d["id"] == db_id:
                return d
        return None

    @classmethod
    def create_database(cls, name: str, description: str = "") -> Dict[str, Any]:
        """
        Creates a new SQLite voter database, initializes its schema,
        and registers it.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("डेटाबेस का नाम रिक्त नहीं हो सकता।")

        # Generate a slug/id
        base_slug = re.sub(r'[^a-zA-Z0-9]', '_', clean_name.lower())
        base_slug = re.sub(r'_+', '_', base_slug).strip('_')
        if not base_slug or len(base_slug) < 3:
            base_slug = f"voters_{uuid.uuid4().hex[:6]}"

        registry = cls._ensure_registry()
        db_id = base_slug
        counter = 1
        while db_id in registry.get("databases", {}):
            db_id = f"{base_slug}_{counter}"
            counter += 1

        filename = f"{db_id}.db"
        file_path = DATA_DIR / filename

        # Initialize schema via sqlite3
        conn = sqlite3.connect(str(file_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        
        # Run table creation directly on this connection
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial_no INTEGER,
                name TEXT NOT NULL,
                relation_type TEXT DEFAULT 'पिता',
                relation_name TEXT,
                house_no TEXT,
                age INTEGER,
                gender TEXT DEFAULT 'पुरुष',
                epic_no TEXT,
                assembly TEXT,
                part_no TEXT,
                polling_station TEXT,
                section_no TEXT,
                page_no INTEGER,
                source_file TEXT,
                created_at TEXT,
                updated_at TEXT,
                is_deleted INTEGER DEFAULT 0,
                deleted_reason TEXT,
                is_muslim INTEGER DEFAULT 0,
                muslim_reason TEXT,
                voter_surname TEXT,
                rel_surname TEXT,
                caste_key TEXT,
                caste_source TEXT,
                caste_reason TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_name ON voters(name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_rel_name ON voters(relation_name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_epic ON voters(epic_no);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_part_no ON voters(part_no);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_assembly ON voters(assembly);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_house_no ON voters(house_no);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_gender ON voters(gender);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_assembly_part_serial ON voters(assembly, part_no, serial_no);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_is_deleted ON voters(is_deleted);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_is_muslim ON voters(is_muslim);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_caste_key ON voters(caste_key);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_part_house ON voters(part_no, house_no);")
        
        from .ai_search import ensure_ai_search_columns
        ensure_ai_search_columns(conn)
        conn.commit()
        conn.close()

        # Register in databases.json
        now_str = datetime.now().isoformat()
        db_record = {
            "id": db_id,
            "name": clean_name,
            "filename": filename,
            "created_at": now_str,
            "updated_at": now_str,
            "description": description.strip() or f"निर्मित डेटाबेस: {clean_name}",
            "is_active_target": False,
            "is_default_search": False
        }
        registry["databases"][db_id] = db_record
        cls._write_registry(registry)

        return cls.get_database_info(db_id)

    @classmethod
    def save_database_name(cls, db_id: str, new_name: str, description: Optional[str] = None) -> Dict[str, Any]:
        """
        Saves/renames the display name and description of an existing database.
        """
        clean_name = new_name.strip()
        if not clean_name:
            raise ValueError("डेटाबेस का नया नाम रिक्त नहीं हो सकता।")

        registry = cls._ensure_registry()
        if db_id not in registry.get("databases", {}):
            raise KeyError(f"डेटाबेस ID '{db_id}' नहीं मिला।")

        registry["databases"][db_id]["name"] = clean_name
        if description is not None:
            registry["databases"][db_id]["description"] = description.strip()
        registry["databases"][db_id]["updated_at"] = datetime.now().isoformat()
        cls._write_registry(registry)

        return cls.get_database_info(db_id)

    @classmethod
    def set_active_target(cls, db_id: str) -> Dict[str, Any]:
        """
        Sets the active database for saving newly scanned/converted voter records.
        """
        registry = cls._ensure_registry()
        if db_id not in registry.get("databases", {}):
            raise KeyError(f"डेटाबेस ID '{db_id}' नहीं मिला।")

        registry["active_save_id"] = db_id
        for did, d in registry["databases"].items():
            d["is_active_target"] = (did == db_id)
        cls._write_registry(registry)

        return cls.get_database_info(db_id)

    @classmethod
    def set_default_search(cls, db_id: str) -> Dict[str, Any]:
        """
        Designates the default database from which voters are searched in portal.
        """
        registry = cls._ensure_registry()
        if db_id not in registry.get("databases", {}):
            raise KeyError(f"डेटाबेस ID '{db_id}' नहीं मिला।")

        registry["default_search_id"] = db_id
        for did, d in registry["databases"].items():
            d["is_default_search"] = (did == db_id)
        cls._write_registry(registry)

        return cls.get_database_info(db_id)

    @classmethod
    def delete_database(cls, db_id: str) -> Dict[str, Any]:
        """
        Safely deletes a secondary database.
        Cannot delete active save DB or default search DB without switching first.
        """
        registry = cls._ensure_registry()
        if db_id not in registry.get("databases", {}):
            raise KeyError(f"डेटाबेस ID '{db_id}' नहीं मिला।")

        if db_id == "default":
            raise ValueError("मुख्य प्राथमिक डेटाबेस (default) को हटाया नहीं जा सकता।")

        if registry.get("active_save_id") == db_id:
            raise ValueError("यह डेटाबेस वर्तमान में डेटा सेव करने हेतु सक्रिय है। हटाने से पहले दूसरा डेटाबेस चुनें।")

        if registry.get("default_search_id") == db_id:
            raise ValueError("यह डेटाबेस वर्तमान में डिफ़ॉल्ट सर्च हेतु सक्रिय है। हटाने से पहले दूसरा सर्च डेटाबेस चुनें।")

        fname = registry["databases"][db_id].get("filename")
        file_path = DATA_DIR / fname if fname else None

        del registry["databases"][db_id]
        cls._write_registry(registry)

        # Move file to backups instead of permanent hard delete
        if file_path and file_path.exists():
            try:
                trash_dir = DATA_DIR / "backups" / "deleted_dbs"
                trash_dir.mkdir(parents=True, exist_ok=True)
                target_trash = trash_dir / f"del_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{fname}"
                file_path.rename(target_trash)
            except Exception:
                pass

        return {"status": "success", "message": f"डेटाबेस '{db_id}' सफलतापूर्वक हटा दिया गया।"}

    @classmethod
    def get_search_db_info(cls) -> Dict[str, Any]:
        """Returns details about the designated default search database."""
        all_dbs = cls.get_all_databases()
        default_db = next((d for d in all_dbs if d.get("is_default_search")), all_dbs[0] if all_dbs else None)
        return {
            "search_db_id": default_db["id"] if default_db else "default",
            "search_db_name": default_db["name"] if default_db else "मुख्य मतदाता डेटाबेस",
            "file_name": default_db["filename"] if default_db else "voters.db",
            "voter_count": default_db.get("total_voters", 0) if default_db else 0,
            "description": default_db.get("description", "") if default_db else ""
        }

