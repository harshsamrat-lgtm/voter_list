"""
Privacy and Online Search Restriction Manager.
Persists and manages admin configuration to restrict community/caste voters
from appearing in online public searches, while preserving full database counts.
"""

import os
import json
import sqlite3
from typing import Dict, List, Set, Any
from datetime import datetime

from .caste_detector import CASTE_PRESETS, extract_surname


DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "block_muslim": False,
    "blocked_caste_keys": [],
    "custom_surnames": [],
    "updated_at": ""
}


class PrivacyManager:
    _cached_settings: Dict[str, Any] = None
    _settings_file: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "privacy_settings.json")

    @classmethod
    def _get_file_path(cls) -> str:
        os.makedirs(os.path.dirname(cls._settings_file), exist_ok=True)
        return cls._settings_file

    @classmethod
    def load_settings(cls) -> Dict[str, Any]:
        """Loads settings from disk or returns defaults."""
        if cls._cached_settings is not None:
            return cls._cached_settings

        path = cls._get_file_path()
        if not os.path.exists(path):
            cls._cached_settings = dict(DEFAULT_SETTINGS)
            return cls._cached_settings

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge with defaults
                settings = dict(DEFAULT_SETTINGS)
                settings.update(data)
                cls._cached_settings = settings
                return settings
        except Exception as e:
            print(f"[PrivacyManager] Error reading settings: {e}")
            cls._cached_settings = dict(DEFAULT_SETTINGS)
            return cls._cached_settings

    @classmethod
    def save_settings(cls, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validates and persists privacy settings."""
        settings = cls.load_settings()
        
        if "enabled" in new_settings:
            settings["enabled"] = bool(new_settings["enabled"])
        if "block_muslim" in new_settings:
            settings["block_muslim"] = bool(new_settings["block_muslim"])
        if "blocked_caste_keys" in new_settings:
            settings["blocked_caste_keys"] = [k for k in new_settings["blocked_caste_keys"] if k in CASTE_PRESETS]
            
        if "custom_surnames" in new_settings:
            raw_surnames = new_settings["custom_surnames"]
            if isinstance(raw_surnames, str):
                tokens = [extract_surname(s.strip()) for s in raw_surnames.replace(",", " ").split()]
            elif isinstance(raw_surnames, list):
                tokens = [extract_surname(str(s).strip()) for s in raw_surnames]
            else:
                tokens = []
            settings["custom_surnames"] = sorted(list(set(filter(None, tokens))))

        settings["updated_at"] = datetime.now().isoformat()
        
        path = cls._get_file_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            
        cls._cached_settings = settings
        return settings

    @classmethod
    def get_active_restrictions(cls) -> Dict[str, Any]:
        """
        Returns active restriction flags and the combined set of blocked surnames.
        """
        settings = cls.load_settings()
        
        if not settings.get("enabled", False):
            return {
                "enabled": False,
                "block_muslim": False,
                "blocked_surnames": []
            }
            
        blocked_surnames: Set[str] = set()
        
        # Add surnames from preset caste groups
        for key in settings.get("blocked_caste_keys", []):
            if key in CASTE_PRESETS:
                for s in CASTE_PRESETS[key]["surnames"]:
                    blocked_surnames.add(s)
                    
        # Add custom surnames
        for s in settings.get("custom_surnames", []):
            clean = extract_surname(s)
            if clean:
                blocked_surnames.add(clean)
                
        return {
            "enabled": True,
            "block_muslim": bool(settings.get("block_muslim", False)),
            "blocked_caste_keys": settings.get("blocked_caste_keys", []),
            "blocked_surnames": sorted(list(blocked_surnames))
        }

    @classmethod
    def get_settings_with_counts(cls, db_path: str) -> Dict[str, Any]:
        """
        Calculates live impact and matched counts for each caste preset
        directly from the database for the Admin UI.
        """
        settings = cls.load_settings()
        active = cls.get_active_restrictions()
        
        preset_info = []
        total_voters = 0
        muslim_count = 0
        
        if not os.path.exists(db_path):
            return {
                "settings": settings,
                "total_voters": 0,
                "blocked_voters": 0,
                "searchable_voters": 0,
                "muslim_count": 0,
                "presets": []
            }
            
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        try:
            c.execute("SELECT COUNT(*) FROM voters")
            total_voters = c.fetchone()[0]
            
            c.execute("SELECT COUNT(*) FROM voters WHERE is_muslim = 1")
            muslim_count = c.fetchone()[0]
            
            # Fetch all rows to compute preset counts and blocked counts
            # (or query by voter_surname/rel_surname)
            c.execute("SELECT voter_surname, rel_surname, is_muslim FROM voters")
            rows = c.fetchall()
            
            # Count for each preset
            for key, preset in CASTE_PRESETS.items():
                p_surnames = set(preset["surnames"])
                cnt = sum(1 for r in rows if (r["voter_surname"] in p_surnames or r["rel_surname"] in p_surnames))
                preset_info.append({
                    "key": key,
                    "label": preset["label"],
                    "description": preset["description"],
                    "count": cnt,
                    "is_blocked": key in settings.get("blocked_caste_keys", [])
                })
                
            # Count total blocked under current settings
            blocked_surnames_set = set(active["blocked_surnames"])
            block_muslim = active["block_muslim"]
            is_enabled = active["enabled"]
            
            blocked_count = 0
            if is_enabled:
                for r in rows:
                    is_m = r["is_muslim"] == 1
                    s_match = (r["voter_surname"] in blocked_surnames_set or r["rel_surname"] in blocked_surnames_set)
                    if (block_muslim and is_m) or s_match:
                        blocked_count += 1
                        
            searchable_count = total_voters - blocked_count
            
        finally:
            conn.close()
            
        return {
            "settings": settings,
            "total_voters": total_voters,
            "blocked_voters": blocked_count if active["enabled"] else 0,
            "searchable_voters": searchable_count if active["enabled"] else total_voters,
            "muslim_count": muslim_count,
            "presets": preset_info
        }
