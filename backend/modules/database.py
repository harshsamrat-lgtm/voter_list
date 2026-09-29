"""
Local SQLite Database Manager for UP Voter Records.
Provides:
1. Persistent local storage in data/voters.db.
2. Strict deduplication (UPSERT) based on EPIC ID or (Assembly + Part + Serial No).
3. Fast indexed multi-criteria search for future voter inquiries.
4. Summary statistics and Excel export of search results.
"""

import sqlite3
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set

from ..config import DB_PATH
from ..models.voter import VoterRecord
from .ai_search import (
    normalize_devanagari,
    get_phonetic_key,
    expand_search_query,
    ensure_ai_search_columns
)
from .community_detector import identify_voter_community
from .caste_detector import extract_surname, CASTE_PRESETS, detect_direct_caste
from .local_caste_ai import LocalCasteAIEngine, clean_and_normalize_name
from .privacy_manager import PrivacyManager
from .validator import is_genuine_voter, clean_house_no


class VoterDatabase:
    """Manages SQLite storage, deduplication, and search for voter records."""

    _initialized = False

    @classmethod
    def get_connection(cls, db_id: Optional[str] = None, purpose: str = "read") -> sqlite3.Connection:
        """Returns a configured SQLite database connection with row factory."""
        from .db_manager import DatabaseManager
        if purpose in ("write", "save"):
            target_path = DatabaseManager.get_db_path(db_id)
        else:
            target_path = DatabaseManager.get_search_db_path(db_id)

        conn = sqlite3.connect(str(target_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging (WAL) for concurrency & speed
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    @classmethod
    def init_db(cls, db_id: Optional[str] = None):
        """Initializes the voters table and indexes if they do not exist."""
        with cls.get_connection(db_id=db_id, purpose="write") as conn:
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
                    updated_at TEXT
                );
            """)

            # Search performance indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_name ON voters(name);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_rel_name ON voters(relation_name);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_epic ON voters(epic_no);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_part_no ON voters(part_no);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_assembly ON voters(assembly);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_house_no ON voters(house_no);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_gender ON voters(gender);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_assembly_part_serial ON voters(assembly, part_no, serial_no);")

            # AI Phonetic & Multilingual Search columns and indexes
            ensure_ai_search_columns(conn)

            # Community & Muslim identification columns and indexes
            cls._ensure_community_columns(conn)

            # Surname & Caste identification columns and indexes
            cls._ensure_surname_columns(conn)

            # Deletion status columns and indexes (DELETED / विलोपित)
            cls._ensure_deleted_columns(conn)

            # NPPropertyServey voter mapping table
            cls._ensure_mapping_table(conn)
            
        cls._initialized = True

    @classmethod
    def _ensure_deleted_columns(cls, conn: sqlite3.Connection):
        """Ensures is_deleted and deleted_reason columns and index exist, and backfills if necessary."""
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(voters);")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if "is_deleted" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN is_deleted INTEGER DEFAULT 0;")
        if "deleted_reason" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN deleted_reason TEXT;")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_is_deleted ON voters(is_deleted);")

        # One-time backfill: If any existing rows in DB contain 'DELETED', 'DELETE', or 'विलोपित' in name, mark them
        cursor.execute("""
            UPDATE voters SET is_deleted = 1, deleted_reason = 'DELETED'
            WHERE (is_deleted = 0 OR is_deleted IS NULL) AND (
                name LIKE '%DELETED%' OR name LIKE '%DELETE%' OR name LIKE '%विलोपित%' OR name LIKE '%निरस्त%'
                OR relation_name LIKE '%DELETED%' OR relation_name LIKE '%विलोपित%'
            );
        """)
        conn.commit()

    @classmethod
    def _ensure_mapping_table(cls, conn: sqlite3.Connection):
        """Ensures survey_voter_mappings table and indexes exist for NPPropertyServey integration."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_voter_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id TEXT,
                family_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                member_name TEXT,
                voter_id INTEGER NOT NULL,
                epic_no TEXT,
                part_no TEXT,
                serial_no INTEGER,
                voter_name TEXT,
                mapped_by TEXT DEFAULT 'admin',
                mapped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                UNIQUE(family_id, member_id),
                FOREIGN KEY(voter_id) REFERENCES voters(id)
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_svm_fam_mem ON survey_voter_mappings(family_id, member_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_svm_voter_id ON survey_voter_mappings(voter_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_svm_family_id ON survey_voter_mappings(family_id);")
        conn.commit()

    @classmethod
    def _ensure_community_columns(cls, conn: sqlite3.Connection):
        """Ensures is_muslim and muslim_reason columns and index exist."""
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(voters);")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if "is_muslim" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN is_muslim INTEGER DEFAULT 0;")
        if "muslim_reason" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN muslim_reason TEXT;")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_is_muslim ON voters(is_muslim);")
        conn.commit()

    @classmethod
    def _ensure_surname_columns(cls, conn: sqlite3.Connection):
        """Ensures voter_surname, rel_surname, caste_key, and caste_source columns and indexes exist, and backfills if empty."""
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(voters);")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if "voter_surname" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN voter_surname TEXT;")
        if "rel_surname" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN rel_surname TEXT;")
        if "caste_key" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN caste_key TEXT;")
        if "caste_source" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN caste_source TEXT;")
        if "caste_reason" not in existing_cols:
            cursor.execute("ALTER TABLE voters ADD COLUMN caste_reason TEXT;")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_voter_surname ON voters(voter_surname);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_rel_surname ON voters(rel_surname);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_caste_key ON voters(caste_key);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_part_house ON voters(part_no, house_no);")
        conn.commit()

        # Check if any existing row needs surname backfilling
        cursor.execute("SELECT COUNT(*) FROM voters WHERE voter_surname IS NULL OR rel_surname IS NULL;")
        null_count = cursor.fetchone()[0]
        if null_count > 0:
            cursor.execute("SELECT id, name, relation_name FROM voters WHERE voter_surname IS NULL OR rel_surname IS NULL;")
            rows = cursor.fetchall()
            for r in rows:
                v_sur = extract_surname(r["name"])
                r_sur = extract_surname(r["relation_name"])
                cursor.execute(
                    "UPDATE voters SET voter_surname = ?, rel_surname = ? WHERE id = ?;",
                    (v_sur, r_sur, r["id"])
                )
            conn.commit()

    @staticmethod
    def _is_valid_epic(epic_no: Optional[str]) -> bool:
        """Validates if an EPIC number is a real voter card identifier and not a generic OCR artifact/placeholder."""
        if not epic_no:
            return False
        clean = epic_no.strip().upper()
        if len(clean) < 6:
            return False
        # Disallow known OCR placeholders, stamps, and error strings
        if clean in {
            "DELETED", "PHOTO", "UNKNOWN", "AVAILABLE", "NOEPIC", "PENDING",
            "ABSENT", "SHIFTED", "VILOPIT", "DUPLICATE", "NOTAVAILABLE", "NIL"
        }:
            return False
        has_digit = any(c.isdigit() for c in clean)
        has_alpha = any(c.isalpha() for c in clean)
        return has_digit and has_alpha

    @staticmethod
    def _is_voter_identity_compatible(
        db_name: str, db_rel: str, db_house: str, db_epic: str,
        rec_name: str, rec_rel_name: str = "", rec_house: str = "", rec_epic: str = "",
        rec_rel: str = ""
    ) -> bool:
        """
        Determines if an incoming voter record is compatible with an existing DB record,
        preventing accidental overwrites of different voters who share serial numbers or names.
        """
        db_n = (db_name or "").strip()
        rec_n = (rec_name or "").strip()
        rec_r_input = rec_rel_name or rec_rel

        # If existing DB record has no valid name, it can be safely updated
        if not db_n or db_n in ("?", "-", "NA", "उपलब्ध नहीं"):
            return True

        # If EPICs match and are valid, they are definitely the same voter
        if db_epic and rec_epic:
            c_db_epic = db_epic.strip().upper()
            c_rec_epic = rec_epic.strip().upper()
            if c_db_epic == c_rec_epic and len(c_db_epic) >= 6:
                return True

        # Normalized Devanagari name comparison
        db_n_norm = normalize_devanagari(db_n)
        rec_n_norm = normalize_devanagari(rec_n)
        if db_n_norm == rec_n_norm:
            return True

        # Substring name matching (e.g. "उमा" vs "उमा देवी", "राम" vs "राम प्रकाश")
        if (db_n_norm in rec_n_norm or rec_n_norm in db_n_norm) and min(len(db_n_norm), len(rec_n_norm)) >= 3:
            return True

        # Relative name comparison
        db_r = (db_rel or "").strip()
        rec_r = (rec_r_input or "").strip()
        if db_r and rec_r:
            db_r_norm = normalize_devanagari(db_r)
            rec_r_norm = normalize_devanagari(rec_r)
            if db_r_norm == rec_r_norm or (db_r_norm in rec_r_norm or rec_r_norm in db_r_norm):
                return True

        # House number matching if phonetic sound matches
        db_h = (db_house or "").strip()
        rec_h = (rec_house or "").strip()
        if db_h and rec_h and db_h == rec_h and not LocalCasteAIEngine.is_generic_house_no(db_h):
            if get_phonetic_key(db_n) == get_phonetic_key(rec_n):
                return True

        return False

    @classmethod
    def save_voters(
        cls,
        records: List[VoterRecord],
        source_file: str = "",
        assembly: Optional[str] = None,
        part_no: Optional[str] = None,
        polling_station: Optional[str] = None,
        db_id: Optional[str] = None
    ) -> Dict[str, int]:
        """
        Saves voter records into the local SQLite database.
        Implements strict deduplication (UPSERT) with zero data loss:
        - Step 1: Detects duplicate serial numbers in incoming batch and reconciles 1..N.
        - Step 2: Tracks updated IDs per batch so no row can be overwritten twice.
        - Match 1: By valid EPIC ID (exact match).
        - Match 2: By (Assembly + Part + Serial) with strict identity compatibility verification.
        - Match 3: By (Assembly + Part + Name + Relative Name) with household/serial distance check.
        Never drops or overwrites different voters!
        """
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        # Ensure only genuine voters are processed and saved
        records = [r for r in records if is_genuine_voter(r)]

        if not records:
            return {"inserted": 0, "updated": 0, "total_saved": 0, "total_in_db": cls.get_total_count(db_id=db_id)}

        # Run Local AI Caste Engine to classify direct surnames and propagate household castes
        records = LocalCasteAIEngine.infer_caste_for_records(records)

        # Pre-process: detect duplicate serial numbers within the batch for each (assembly, part)
        part_groups: Dict[Tuple[str, str], List[VoterRecord]] = defaultdict(list)
        for r in records:
            key = ((r.assembly or assembly or "").strip(), (r.part_no or part_no or "").strip())
            part_groups[key].append(r)

        for key, rec_list in part_groups.items():
            seen_serials = set()
            has_dup_serials = False
            for r in rec_list:
                s = r.serial_no
                if s and s > 0:
                    if s in seen_serials:
                        has_dup_serials = True
                        break
                    seen_serials.add(s)
                else:
                    has_dup_serials = True
                    break
            if has_dup_serials:
                for idx, r in enumerate(rec_list, start=1):
                    r.serial_no = idx

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        inserted = 0
        updated = 0
        updated_ids_in_batch: Set[int] = set()

        with cls.get_connection(db_id=db_id, purpose="write") as conn:
            cursor = conn.cursor()

            for rec in records:
                rec_assembly = rec.assembly or assembly
                rec_part = rec.part_no or part_no
                rec_ps = rec.polling_station or polling_station
                rec_epic = (rec.epic_no or "").strip().upper()
                rec_name = (rec.name or "").strip()
                rec_rel_name = (rec.relation_name or "").strip()
                rec_serial = rec.serial_no

                matched_id: Optional[int] = None

                # Rule 1: Match by valid non-generic EPIC number
                if rec_epic and cls._is_valid_epic(rec_epic):
                    cursor.execute("SELECT id, assembly, part_no, name FROM voters WHERE UPPER(epic_no) = ? LIMIT 1;", (rec_epic,))
                    row = cursor.fetchone()
                    if row and row["id"] not in updated_ids_in_batch:
                        matched_id = row["id"]

                # Rule 2: Match by (Assembly + Part No + Serial No) if no EPIC match
                if matched_id is None and rec_assembly and rec_part and rec_serial:
                    cursor.execute(
                        "SELECT id, name, relation_name, house_no, epic_no FROM voters WHERE assembly = ? AND part_no = ? AND serial_no = ? LIMIT 1;",
                        (rec_assembly, rec_part, rec_serial)
                    )
                    row = cursor.fetchone()
                    if row and row["id"] not in updated_ids_in_batch:
                        # Enforce identity compatibility so we never overwrite an existing, different person
                        if cls._is_voter_identity_compatible(
                            db_name=row["name"],
                            db_rel=row["relation_name"],
                            db_house=row["house_no"],
                            db_epic=row["epic_no"],
                            rec_name=rec_name,
                            rec_rel_name=rec_rel_name,
                            rec_house=rec.house_no,
                            rec_epic=rec_epic
                        ):
                            matched_id = row["id"]

                # Rule 2b: Match by (source_file + Serial No) if assembly/part string varied between runs
                if matched_id is None and source_file and rec_serial:
                    cursor.execute(
                        "SELECT id, name, relation_name, house_no, epic_no FROM voters WHERE source_file = ? AND serial_no = ? LIMIT 1;",
                        (source_file, rec_serial)
                    )
                    row = cursor.fetchone()
                    if row and row["id"] not in updated_ids_in_batch:
                        matched_id = row["id"]

                # Rule 3: Match by (Assembly + Part No + Name + Relative Name)
                if matched_id is None and rec_assembly and rec_part and rec_name and rec_rel_name:
                    cursor.execute(
                        "SELECT id, serial_no, house_no, epic_no FROM voters WHERE assembly = ? AND part_no = ? AND name = ? AND relation_name = ?;",
                        (rec_assembly, rec_part, rec_name, rec_rel_name)
                    )
                    candidates = cursor.fetchall()
                    for cand in candidates:
                        if cand["id"] in updated_ids_in_batch:
                            continue
                        cand_serial = cand["serial_no"]
                        cand_house = (cand["house_no"] or "").strip()
                        rec_house = (rec.house_no or "").strip()
                        # Differentiate same-name voters in different houses
                        if cand_serial and rec_serial and abs(cand_serial - rec_serial) > 5:
                            if cand_house and rec_house and cand_house != rec_house and not LocalCasteAIEngine.is_generic_house_no(cand_house):
                                continue
                        matched_id = cand["id"]
                        break

                # Pre-compute AI normalized and phonetic keys for instant searching
                name_norm = normalize_devanagari(rec_name)
                name_phon = get_phonetic_key(rec_name)
                rel_norm = normalize_devanagari(rec_rel_name)
                rel_phon = get_phonetic_key(rec_rel_name)

                # Identify Muslim community identity
                if rec.is_muslim:
                    rec_is_muslim = 1
                    rec_muslim_reason = rec.muslim_reason or ""
                else:
                    is_m, m_reason, _ = identify_voter_community(rec_name, rec_rel_name, rec.relation_type)
                    rec_is_muslim = 1 if is_m else 0
                    rec_muslim_reason = m_reason

                # Surname extraction for caste identification (voter, father, husband)
                voter_surname = extract_surname(rec_name)
                rel_surname = extract_surname(rec_rel_name)
                rec_caste_key = getattr(rec, "caste_key", None)
                rec_caste_source = getattr(rec, "caste_source", None)
                rec_caste_reason = getattr(rec, "caste_reason", None)

                # Deletion status (DELETED / विलोपित)
                rec_is_deleted = 1 if getattr(rec, "is_deleted", False) else 0
                rec_deleted_reason = getattr(rec, "deleted_reason", None) or ("DELETED" if rec_is_deleted else None)

                # Execute UPDATE or INSERT
                valid_update_serial = rec.serial_no if (rec.serial_no and rec.serial_no > 0) else None
                if matched_id is not None:
                    # Update existing record
                    cursor.execute("""
                        UPDATE voters SET
                            serial_no = COALESCE(?, serial_no),
                            name = ?,
                            relation_type = ?,
                            relation_name = ?,
                            house_no = ?,
                            age = ?,
                            gender = ?,
                            epic_no = ?,
                            assembly = ?,
                            part_no = ?,
                            polling_station = ?,
                            section_no = ?,
                            page_no = ?,
                            source_file = ?,
                            updated_at = ?,
                            name_normalized = ?,
                            name_phonetic = ?,
                            rel_normalized = ?,
                            rel_phonetic = ?,
                            is_muslim = ?,
                            muslim_reason = ?,
                            voter_surname = ?,
                            rel_surname = ?,
                            caste_key = CASE WHEN caste_source IN ('manual_admin', 'admin') THEN caste_key ELSE ? END,
                            caste_source = CASE WHEN caste_source IN ('manual_admin', 'admin') THEN caste_source ELSE ? END,
                            caste_reason = CASE WHEN caste_source IN ('manual_admin', 'admin') THEN caste_reason ELSE COALESCE(?, caste_reason) END,
                            is_deleted = CASE WHEN ? = 1 THEN 1 ELSE is_deleted END,
                            deleted_reason = COALESCE(?, deleted_reason)
                        WHERE id = ?;
                    """, (
                        valid_update_serial,
                        rec_name,
                        rec.relation_type,
                        rec_rel_name,
                        rec.house_no,
                        rec.age,
                        rec.gender,
                        rec_epic,
                        rec_assembly,
                        rec_part,
                        rec_ps,
                        rec.section_no,
                        rec.page_no,
                        source_file,
                        now_str,
                        name_norm,
                        name_phon,
                        rel_norm,
                        rel_phon,
                        rec_is_muslim,
                        rec_muslim_reason,
                        voter_surname,
                        rel_surname,
                        rec_caste_key,
                        rec_caste_source,
                        rec_caste_reason,
                        rec_is_deleted,
                        rec_deleted_reason,
                        matched_id
                    ))
                    updated += 1
                    updated_ids_in_batch.add(matched_id)
                else:
                    # Insert new record
                    cursor.execute("""
                        INSERT INTO voters (
                            serial_no, name, relation_type, relation_name,
                            house_no, age, gender, epic_no,
                            assembly, part_no, polling_station, section_no,
                            page_no, source_file, created_at, updated_at,
                            name_normalized, name_phonetic, rel_normalized, rel_phonetic,
                            is_muslim, muslim_reason, voter_surname, rel_surname,
                            caste_key, caste_source, caste_reason, is_deleted, deleted_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        rec.serial_no,
                        rec_name,
                        rec.relation_type,
                        rec_rel_name,
                        rec.house_no,
                        rec.age,
                        rec.gender,
                        rec_epic,
                        rec_assembly,
                        rec_part,
                        rec_ps,
                        rec.section_no,
                        rec.page_no,
                        source_file,
                        now_str,
                        now_str,
                        name_norm,
                        name_phon,
                        rel_norm,
                        rel_phon,
                        rec_is_muslim,
                        rec_muslim_reason,
                        voter_surname,
                        rel_surname,
                        rec_caste_key,
                        rec_caste_source,
                        rec_caste_reason,
                        rec_is_deleted,
                        rec_deleted_reason
                    ))
                    inserted += 1
                    if cursor.lastrowid:
                        updated_ids_in_batch.add(cursor.lastrowid)


            # Stale / Phantom Row Cleanup for this source file
            if source_file:
                # 1. Purge any blank/empty records
                cursor.execute("""
                    DELETE FROM voters 
                    WHERE source_file = ? 
                      AND (name IS NULL OR TRIM(name) = '' OR TRIM(name) = '?' OR LENGTH(TRIM(name)) < 2);
                """, (source_file,))

                # 2. If this file was re-scanned, purge any stale serials beyond the new batch count
                if records:
                    active_part = records[0].part_no or part_no
                    if active_part:
                        cursor.execute("""
                            DELETE FROM voters 
                            WHERE source_file = ? AND part_no = ? AND serial_no > ?;
                        """, (source_file, active_part, len(records)))
                        # Also purge old rows from misread part numbers of the exact same file
                        cursor.execute("""
                            DELETE FROM voters 
                            WHERE source_file = ? AND part_no != ?;
                        """, (source_file, active_part))

            conn.commit()

        total_in_db = cls.get_total_count(active_only=False, db_id=db_id)
        active_in_db = cls.get_total_count(active_only=True, db_id=db_id)
        return {
            "inserted": inserted,
            "updated": updated,
            "total_saved": len(records),
            "total_in_db": total_in_db,
            "active_in_db": active_in_db
        }

    @classmethod
    def get_total_count(cls, active_only: bool = False, db_id: Optional[str] = None) -> int:
        """Returns the total number of voters in the database (defaults to all saved records)."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)
        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cursor = conn.cursor()
            if active_only:
                cursor.execute("SELECT COUNT(*) FROM voters WHERE is_deleted = 0;")
            else:
                cursor.execute("SELECT COUNT(*) FROM voters;")
            return cursor.fetchone()[0]

    @classmethod
    def search_voters(
        cls,
        q: Optional[str] = None,
        name: Optional[str] = None,
        relation_name: Optional[str] = None,
        epic_no: Optional[str] = None,
        part_no: Optional[str] = None,
        assembly: Optional[str] = None,
        gender: Optional[str] = None,
        house_no: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        muslim: Optional[str] = None,
        caste_key: Optional[str] = None,
        status: Optional[str] = None,
        is_deleted: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        is_public: bool = False,
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs multi-criteria indexed search across all saved voters.
        Supports both universal keyword search (q) and targeted field filters.
        """
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        page = max(1, page)
        limit = max(1, min(limit, 5000))
        offset = (page - 1) * limit

        where_clauses = ["1=1"]
        params: List[Any] = []

        # Universal search across name, relative, epic, house_no, polling station with AI tolerance
        if q and q.strip():
            q_clean = q.strip()
            q_exp = expand_search_query(q_clean)
            q_conds = []

            # 1. Exact variants across name and relation_name
            for v in q_exp["exact_variants"]:
                q_conds.append("name LIKE ?")
                params.append(f"%{v}%")
                q_conds.append("relation_name LIKE ?")
                params.append(f"%{v}%")

            # 2. Normalized Devanagari variants
            for v in q_exp["normalized_variants"]:
                q_conds.append("name_normalized LIKE ?")
                params.append(f"%{v}%")
                q_conds.append("rel_normalized LIKE ?")
                params.append(f"%{v}%")

            # 3. Phonetic Soundex keys
            if q_exp["phonetic_key"]:
                q_conds.append("name_phonetic LIKE ?")
                params.append(f"%{q_exp['phonetic_key']}%")
                q_conds.append("rel_phonetic LIKE ?")
                params.append(f"%{q_exp['phonetic_key']}%")

            # 4. Standard fields: epic_no, house_no, polling_station, part_no
            q_conds.append("UPPER(epic_no) LIKE ?")
            params.append(f"%{q_clean.upper()}%")
            q_conds.append("house_no LIKE ?")
            params.append(f"%{q_clean}%")
            q_conds.append("polling_station LIKE ?")
            params.append(f"%{q_clean}%")
            q_conds.append("part_no LIKE ?")
            params.append(f"%{q_clean}%")

            where_clauses.append(f"({' OR '.join(q_conds)})")

        # Specific field filters with AI tolerance
        if name and name.strip():
            name_clean = name.strip()
            name_exp = expand_search_query(name_clean)
            name_conds = []
            for v in name_exp["exact_variants"]:
                name_conds.append("name LIKE ?")
                params.append(f"%{v}%")
            for v in name_exp["normalized_variants"]:
                name_conds.append("name_normalized LIKE ?")
                params.append(f"%{v}%")
            if name_exp["phonetic_key"]:
                name_conds.append("name_phonetic LIKE ?")
                params.append(f"%{name_exp['phonetic_key']}%")
            if name_conds:
                where_clauses.append(f"({' OR '.join(name_conds)})")

        if relation_name and relation_name.strip():
            rel_clean = relation_name.strip()
            rel_exp = expand_search_query(rel_clean)
            rel_conds = []
            for v in rel_exp["exact_variants"]:
                rel_conds.append("relation_name LIKE ?")
                params.append(f"%{v}%")
            for v in rel_exp["normalized_variants"]:
                rel_conds.append("rel_normalized LIKE ?")
                params.append(f"%{v}%")
            if rel_exp["phonetic_key"]:
                rel_conds.append("rel_phonetic LIKE ?")
                params.append(f"%{rel_exp['phonetic_key']}%")
            if rel_conds:
                where_clauses.append(f"({' OR '.join(rel_conds)})")

        if epic_no and epic_no.strip():
            where_clauses.append("UPPER(epic_no) LIKE ?")
            params.append(f"%{epic_no.strip().upper()}%")

        if part_no and part_no.strip() and part_no != "all":
            where_clauses.append("part_no = ?")
            params.append(part_no.strip())

        if assembly and assembly.strip() and assembly != "all":
            where_clauses.append("assembly = ?")
            params.append(assembly.strip())

        if gender and gender.strip() and gender != "all":
            where_clauses.append("gender = ?")
            params.append(gender.strip())

        if house_no and house_no.strip():
            where_clauses.append("house_no LIKE ?")
            params.append(f"%{house_no.strip()}%")

        if min_age is not None and min_age > 0:
            where_clauses.append("age >= ?")
            params.append(min_age)

        if max_age is not None and max_age > 0:
            where_clauses.append("age <= ?")
            params.append(max_age)

        # Community / Muslim filter
        if muslim and muslim.strip() and muslim != "all":
            m_clean = muslim.strip().lower()
            if m_clean in ("yes", "true", "1", "muslim"):
                where_clauses.append("is_muslim = 1")
            elif m_clean in ("no", "false", "0", "non_muslim", "hindu", "other"):
                where_clauses.append("(is_muslim = 0 OR is_muslim IS NULL)")

        # Caste filter (by caste_key OR voter surname or relative surname matching CASTE_PRESETS)
        if caste_key and caste_key.strip() and caste_key != "all":
            ck = caste_key.strip().lower()
            if ck == "muslim":
                target_surnames = CASTE_PRESETS.get("muslim", {}).get("surnames", [])
                if target_surnames:
                    placeholders = ",".join("?" for _ in target_surnames)
                    where_clauses.append(f"(caste_key = 'muslim' OR is_muslim = 1 OR voter_surname IN ({placeholders}) OR rel_surname IN ({placeholders}))")
                    params.extend(target_surnames)
                    params.extend(target_surnames)
                else:
                    where_clauses.append("(caste_key = 'muslim' OR is_muslim = 1)")
            elif ck in CASTE_PRESETS:
                target_surnames = CASTE_PRESETS[ck]["surnames"]
                if target_surnames:
                    placeholders = ",".join("?" for _ in target_surnames)
                    where_clauses.append(f"((caste_key = ? OR voter_surname IN ({placeholders}) OR rel_surname IN ({placeholders})) AND (is_muslim = 0 OR is_muslim IS NULL))")
                    params.append(ck)
                    params.extend(target_surnames)
                    params.extend(target_surnames)
                else:
                    where_clauses.append("caste_key = ?")
                    params.append(ck)
            elif ck in ("other", "others", "अन्य"):
                all_known: Set[str] = set()
                for k, p in CASTE_PRESETS.items():
                    if k != "muslim":
                        all_known.update(p["surnames"])
                if all_known:
                    placeholders = ",".join("?" for _ in all_known)
                    where_clauses.append(f"((caste_key IS NULL OR caste_key = 'other' OR caste_key = '') AND is_muslim = 0 AND voter_surname NOT IN ({placeholders}) AND rel_surname NOT IN ({placeholders}))")
                    params.extend(list(all_known))
                    params.extend(list(all_known))

        # Voter status filter (All / Active / Deleted)
        stat_filter = status or is_deleted
        if stat_filter and stat_filter.strip() and stat_filter != "all":
            st = stat_filter.strip().lower()
            if st in ("deleted", "1", "true", "विलोपित", "निरस्त"):
                where_clauses.append("is_deleted = 1")
            elif st in ("active", "0", "false", "सक्रिय"):
                where_clauses.append("(is_deleted = 0 OR is_deleted IS NULL)")

        # Online Public Search Restrictions (Caste & Community hiding)
        if is_public:
            restrictions = PrivacyManager.get_active_restrictions()
            if restrictions.get("enabled"):
                if restrictions.get("block_muslim"):
                    where_clauses.append("is_muslim = 0")
                blocked_caste_keys = restrictions.get("blocked_caste_keys", [])
                if blocked_caste_keys:
                    placeholders = ",".join("?" for _ in blocked_caste_keys)
                    where_clauses.append(f"(caste_key IS NULL OR caste_key NOT IN ({placeholders}))")
                    params.extend(blocked_caste_keys)
                blocked_surnames = restrictions.get("blocked_surnames", [])
                if blocked_surnames:
                    placeholders = ",".join("?" for _ in blocked_surnames)
                    where_clauses.append(f"(voter_surname NOT IN ({placeholders}) AND rel_surname NOT IN ({placeholders}))")
                    params.extend(blocked_surnames)
                    params.extend(blocked_surnames)

        where_sql = " AND ".join(where_clauses)

        # Smart relevance ordering: exact match first, then normalized/transliterated, then phonetic
        order_clause = "assembly ASC, CAST(part_no AS INTEGER) ASC, serial_no ASC"
        order_params: List[Any] = []
        sort_term = q.strip() if (q and q.strip()) else (name.strip() if (name and name.strip()) else None)
        if sort_term:
            sort_norm = normalize_devanagari(sort_term)
            sort_phon = get_phonetic_key(sort_term)
            order_clause = f"""
                CASE
                    WHEN name LIKE ? THEN 1
                    WHEN name_normalized LIKE ? THEN 2
                    WHEN name_phonetic LIKE ? THEN 3
                    ELSE 4
                END ASC, assembly ASC, CAST(part_no AS INTEGER) ASC, serial_no ASC
            """
            order_params = [f"%{sort_term}%", f"%{sort_norm}%", f"%{sort_phon}%"]

        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cursor = conn.cursor()

            # Count total matching
            count_query = f"SELECT COUNT(*) FROM voters WHERE {where_sql};"
            cursor.execute(count_query, params)
            total_matching = cursor.fetchone()[0]

            # Count active vs deleted matching
            cursor.execute(f"SELECT COUNT(*) FROM voters WHERE {where_sql} AND is_deleted = 0;", params)
            active_matching = cursor.fetchone()[0]
            cursor.execute(f"SELECT COUNT(*) FROM voters WHERE {where_sql} AND is_deleted = 1;", params)
            deleted_matching = cursor.fetchone()[0]

            # Fetch paginated rows ordered by relevance and hierarchy
            select_query = f"""
                SELECT 
                    id, serial_no, name, relation_type, relation_name,
                    house_no, age, gender, epic_no,
                    assembly, part_no, polling_station, section_no,
                    page_no, source_file, created_at, updated_at,
                    is_muslim, muslim_reason, voter_surname, rel_surname,
                    caste_key, caste_source, caste_reason, is_deleted, deleted_reason
                FROM voters
                WHERE {where_sql}
                ORDER BY {order_clause}
                LIMIT ? OFFSET ?;
            """
            cursor.execute(select_query, params + order_params + [limit, offset])
            rows = cursor.fetchall()

            records = []
            for row in rows:
                r_dict = dict(row)
                r_dict["is_deleted"] = bool(r_dict.get("is_deleted", 0))
                records.append(r_dict)

        total_pages = (total_matching + limit - 1) // limit if limit > 0 else 1

        return {
            "total_records": cls.get_total_count(active_only=True),
            "total_filtered": total_matching,
            "active_filtered": active_matching,
            "deleted_filtered": deleted_matching,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "records": records
        }

    @classmethod
    def get_stats(cls, db_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns overall database metrics (active voters, gender ratio, parts, assemblies, deleted voters)."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cursor = conn.cursor()

            # Active voters count (सामान्य गिनती में केवल सक्रिय मतदाता)
            cursor.execute("SELECT COUNT(*) FROM voters WHERE is_deleted = 0;")
            active_voters = cursor.fetchone()[0]

            # Deleted voters count (अलग से गिनती)
            cursor.execute("SELECT COUNT(*) FROM voters WHERE is_deleted = 1;")
            deleted_voters = cursor.fetchone()[0]

            # Total records in table (including deleted)
            cursor.execute("SELECT COUNT(*) FROM voters;")
            total_records = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM voters WHERE gender = 'पुरुष' AND is_deleted = 0;")
            male_voters = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM voters WHERE gender = 'महिला' AND is_deleted = 0;")
            female_voters = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM voters WHERE gender NOT IN ('पुरुष', 'महिला') AND is_deleted = 0;")
            other_voters = cursor.fetchone()[0]

            cursor.execute("SELECT DISTINCT assembly FROM voters WHERE assembly IS NOT NULL AND assembly != '' AND is_deleted = 0 ORDER BY assembly;")
            assemblies = [r[0] for r in cursor.fetchall()]

            cursor.execute("SELECT DISTINCT part_no FROM voters WHERE part_no IS NOT NULL AND part_no != '' AND is_deleted = 0 ORDER BY CAST(part_no AS INTEGER);")
            parts = [r[0] for r in cursor.fetchall()]

            # Average age of active voters
            cursor.execute("SELECT AVG(age) FROM voters WHERE age IS NOT NULL AND age > 0 AND is_deleted = 0;")
            avg_age_row = cursor.fetchone()[0]
            avg_age = round(avg_age_row, 1) if avg_age_row else 0

            # Community (Muslim) Metrics for active voters
            cursor.execute("SELECT COUNT(*) FROM voters WHERE is_muslim = 1 AND is_deleted = 0;")
            muslim_voters = cursor.fetchone()[0]
            non_muslim_voters = active_voters - muslim_voters
            hindu_voters = non_muslim_voters
            muslim_pct = round((muslim_voters / active_voters * 100), 1) if active_voters > 0 else 0.0
            hindu_pct = round((hindu_voters / active_voters * 100), 1) if active_voters > 0 else 0.0

        gender_ratio = round((female_voters / male_voters * 1000), 1) if male_voters > 0 else 0

        return {
            "total_voters": total_records,
            "active_voters": active_voters,
            "deleted_voters": deleted_voters,
            "total_records": total_records,
            "male_voters": male_voters,
            "female_voters": female_voters,
            "other_voters": other_voters,
            "gender_ratio": gender_ratio,
            "average_age": avg_age,
            "hindu_voters": hindu_voters,
            "hindu_percentage": hindu_pct,
            "muslim_voters": muslim_voters,
            "muslim_percentage": muslim_pct,
            "non_muslim_voters": hindu_voters,
            "total_assemblies": len(assemblies),
            "total_parts": len(parts),
            "assemblies": assemblies,
            "parts": parts
        }

    @classmethod
    def get_caste_community_analytics(cls) -> Dict[str, Any]:
        """
        Calculates demographic distributions for interactive charts:
        1. Community: Hindu (Non-Muslim) vs Muslim counts & percentages
        2. Caste: Breakdown by caste presets based on surnames
        """
        if not cls._initialized:
            cls.init_db()

        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM voters WHERE is_deleted = 0;")
            total_voters = cursor.fetchone()[0]

            if total_voters == 0:
                return {
                    "total_voters": 0,
                    "hindu_count": 0,
                    "muslim_count": 0,
                    "non_muslim_count": 0,
                    "community": [],
                    "castes": []
                }

            cursor.execute("SELECT COUNT(*) FROM voters WHERE is_muslim = 1 AND is_deleted = 0;")
            muslim_count = cursor.fetchone()[0]
            hindu_count = total_voters - muslim_count
            non_muslim_count = hindu_count

            community_data = [
                {
                    "key": "hindu",
                    "label": "🚩 हिन्दू मतदाता",
                    "count": hindu_count,
                    "percentage": round((hindu_count / total_voters) * 100, 1),
                    "color": "#EA580C"
                },
                {
                    "key": "muslim",
                    "label": "☪️ मुस्लिम मतदाता",
                    "count": muslim_count,
                    "percentage": round((muslim_count / total_voters) * 100, 1),
                    "color": "#059669"
                }
            ]

            cursor.execute("SELECT caste_key, caste_source, voter_surname, rel_surname, is_muslim FROM voters WHERE is_deleted = 0;")
            rows = cursor.fetchall()

            # Rich distinct color palette for all recognized castes
            caste_colors = [
                "#059669", "#EA580C", "#2563EB", "#7C3AED", "#DB2777",
                "#D97706", "#0D9488", "#4F46E5", "#65A30D", "#0891B2",
                "#C026D3", "#DC2626", "#9333EA", "#0284C7", "#16A34A",
                "#CA8A04", "#E11D48", "#475569", "#B45309", "#4338CA",
                "#BE185D", "#047857", "#0E7490", "#7E22CE", "#1D4ED8",
                "#A16207", "#BE123C", "#334155"
            ]

            caste_data = []
            idx = 0
            for key, preset in CASTE_PRESETS.items():
                p_surnames = set(preset.get("surnames", []))
                if key == "muslim":
                    cnt = sum(1 for r in rows if r["caste_key"] == "muslim" or r["is_muslim"] == 1)
                else:
                    cnt = sum(
                        1 for r in rows
                        if (r["is_muslim"] == 0 or r["is_muslim"] is None) and (
                            r["caste_key"] == key or (
                                not r["caste_key"] and (r["voter_surname"] in p_surnames or r["rel_surname"] in p_surnames)
                            )
                        )
                    )

                caste_data.append({
                    "key": key,
                    "label": preset["label"].split("/")[0].strip(),
                    "full_label": preset["label"],
                    "count": cnt,
                    "percentage": round((cnt / total_voters) * 100, 1) if total_voters > 0 else 0,
                    "color": caste_colors[idx % len(caste_colors)]
                })
                idx += 1

            # Other / Unclassified surnames
            classified_total = sum(item["count"] for item in caste_data)
            other_count = max(0, total_voters - classified_total)
            if other_count > 0:
                caste_data.append({
                    "key": "other",
                    "label": "अन्य / सामान्य",
                    "full_label": "अन्य / सामान्य उपनाम",
                    "count": other_count,
                    "percentage": round((other_count / total_voters) * 100, 1) if total_voters > 0 else 0,
                    "color": "#94A3B8"
                })

            # Sort castes: Populated castes first (descending), followed by 0-count castes
            caste_data.sort(key=lambda x: (x["count"] > 0, x["count"]), reverse=True)

            return {
                "total_voters": total_voters,
                "hindu_count": hindu_count,
                "muslim_count": muslim_count,
                "non_muslim_count": non_muslim_count,
                "community": community_data,
                "castes": caste_data,
                "household_ai_count": sum(1 for r in rows if r["caste_source"] == "household_ai"),
                "direct_count": sum(1 for r in rows if r["caste_source"] in ("direct_surname", "direct_community")),
                "lineage_count": sum(1 for r in rows if r["caste_source"] == "family_lineage_ai")
            }

    @classmethod
    def get_voter_by_id(cls, voter_id: int, is_public: bool = False, db_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetches a single voter record by primary key ID, applying privacy restrictions for public requests."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)
        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    id, serial_no, name, relation_type, relation_name,
                    house_no, age, gender, epic_no,
                    assembly, part_no, polling_station, section_no,
                    page_no, source_file, created_at, updated_at,
                    is_muslim, muslim_reason, voter_surname, rel_surname,
                    caste_key, caste_source, caste_reason, is_deleted, deleted_reason
                FROM voters
                WHERE id = ?;
            """, (voter_id,))
            row = cursor.fetchone()
            if not row:
                return None
                
            rec = dict(row)
            rec["is_deleted"] = bool(rec.get("is_deleted", 0))
            if is_public:
                restrictions = PrivacyManager.get_active_restrictions()
                if restrictions.get("enabled"):
                    if restrictions.get("block_muslim") and rec.get("is_muslim") == 1:
                        return None
                    blocked_caste_keys = set(restrictions.get("blocked_caste_keys", []))
                    if rec.get("caste_key") in blocked_caste_keys:
                        return None
                    blocked_surnames = set(restrictions.get("blocked_surnames", []))
                    if rec.get("voter_surname") in blocked_surnames or rec.get("rel_surname") in blocked_surnames:
                        return None
            return rec

    @classmethod
    def update_voter(cls, voter_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Updates an existing voter record by ID from the Admin Panel.
        Recomputes phonetic indexes, surnames, and community/caste tags as appropriate.
        """
        if not cls._initialized:
            cls.init_db()

        existing = cls.get_voter_by_id(voter_id, is_public=False)
        if not existing:
            return None

        # Extract values or keep existing
        name = (data.get("name") if data.get("name") is not None else existing.get("name") or "").strip()
        rel_type = (data.get("relation_type") if data.get("relation_type") is not None else existing.get("relation_type") or "पिता").strip()
        rel_name = (data.get("relation_name") if data.get("relation_name") is not None else existing.get("relation_name") or "").strip()
        serial_no = data.get("serial_no") if data.get("serial_no") is not None else existing.get("serial_no")
        epic_no = (data.get("epic_no") if data.get("epic_no") is not None else existing.get("epic_no") or "").strip().upper()
        house_no = (data.get("house_no") if data.get("house_no") is not None else existing.get("house_no") or "").strip()
        age = data.get("age") if data.get("age") is not None else existing.get("age")
        gender = (data.get("gender") if data.get("gender") is not None else existing.get("gender") or "पुरुष").strip()
        part_no = (data.get("part_no") if data.get("part_no") is not None else existing.get("part_no") or "").strip()
        assembly = (data.get("assembly") if data.get("assembly") is not None else existing.get("assembly") or "").strip()
        polling_station = (data.get("polling_station") if data.get("polling_station") is not None else existing.get("polling_station") or "").strip()
        is_deleted = 1 if data.get("is_deleted") else 0
        deleted_reason = "DELETED" if is_deleted else None

        # Recompute AI search phonetic tokens
        name_norm = normalize_devanagari(name)
        name_phon = get_phonetic_key(name)
        rel_norm = normalize_devanagari(rel_name)
        rel_phon = get_phonetic_key(rel_name)

        # Community determination
        if "is_muslim" in data and data["is_muslim"] is not None:
            is_m = 1 if data["is_muslim"] else 0
            m_reason = "एडमिन द्वारा निर्धारित" if is_m else ""
        else:
            is_m_det, m_reason, _ = identify_voter_community(name, rel_name, rel_type)
            is_m = 1 if is_m_det else 0

        # Caste determination
        caste_key = data.get("caste_key")
        caste_source = existing.get("caste_source")
        caste_reason = existing.get("caste_reason")

        if is_m:
            # Rule: If voter is Muslim, they never come under a caste based on house number or otherwise
            caste_key = "muslim"
            caste_source = "direct_community"
            caste_reason = "प्रत्यक्ष समुदाय पहचान (मुस्लिम)"
        elif caste_key is not None:
            caste_key = str(caste_key).strip().lower()
            if caste_key in ("", "all", "none", "null"):
                caste_key = None
                caste_source = None
                caste_reason = None
            elif caste_key in CASTE_PRESETS:
                caste_source = "manual_admin"
                caste_reason = "एडमिन द्वारा प्रविष्टि (Manual Override)"
            else:
                caste_source = "manual_admin"
                caste_reason = "एडमिन द्वारा प्रविष्टि (Manual Override)"
        else:
            # Auto-detect if name changed
            c_det, m_sur = detect_direct_caste(name, rel_name, rel_type, False)
            if c_det and c_det != "muslim":
                caste_key = c_det
                caste_source = "direct_surname"
                caste_reason = f"प्रत्यक्ष उपनाम मिलान ({m_sur})"

        voter_surname = extract_surname(name)
        rel_surname = extract_surname(rel_name)
        updated_at = datetime.now().isoformat()

        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE voters SET
                    serial_no = ?,
                    name = ?,
                    relation_type = ?,
                    relation_name = ?,
                    house_no = ?,
                    age = ?,
                    gender = ?,
                    epic_no = ?,
                    assembly = ?,
                    part_no = ?,
                    polling_station = ?,
                    updated_at = ?,
                    name_normalized = ?,
                    name_phonetic = ?,
                    rel_normalized = ?,
                    rel_phonetic = ?,
                    is_muslim = ?,
                    muslim_reason = ?,
                    voter_surname = ?,
                    rel_surname = ?,
                    caste_key = ?,
                    caste_source = ?,
                    caste_reason = ?,
                    is_deleted = ?,
                    deleted_reason = ?
                WHERE id = ?;
            """, (
                serial_no, name, rel_type, rel_name,
                house_no, age, gender, epic_no,
                assembly, part_no, polling_station,
                updated_at,
                name_norm, name_phon, rel_norm, rel_phon,
                is_m, m_reason,
                voter_surname, rel_surname,
                caste_key, caste_source, caste_reason,
                is_deleted, deleted_reason,
                voter_id
            ))
            conn.commit()

        return cls.get_voter_by_id(voter_id, is_public=False)

    @classmethod
    def add_voter(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inserts a new voter record into the database from the Admin Panel.
        Precomputes phonetic indexes, surnames, community/caste tags, and auto-assigns serial_no if missing.
        """
        if not cls._initialized:
            cls.init_db()

        name = (data.get("name") or "").strip()
        rel_type = (data.get("relation_type") or "पिता").strip()
        rel_name = (data.get("relation_name") or "").strip()
        part_no = (str(data.get("part_no") or "")).strip()
        section_no = (str(data.get("section_no") or "")).strip()
        assembly = (data.get("assembly") or "").strip()
        polling_station = (data.get("polling_station") or "").strip()
        epic_no = (data.get("epic_no") or "").strip().upper()
        house_no = (data.get("house_no") or "").strip()
        
        age = data.get("age")
        try:
            age = int(age) if age is not None and str(age).strip() != "" else None
        except Exception:
            age = None
            
        gender = (data.get("gender") or "पुरुष").strip()
        is_deleted = 1 if data.get("is_deleted") else 0
        deleted_reason = "DELETED" if is_deleted else None

        # Determine serial_no if missing or 0
        serial_no = data.get("serial_no")
        try:
            serial_no = int(serial_no) if serial_no is not None and str(serial_no).strip() != "" else None
        except Exception:
            serial_no = None

        with cls.get_connection() as conn:
            cursor = conn.cursor()
            if not serial_no or serial_no <= 0:
                cursor.execute("SELECT MAX(serial_no) as max_s FROM voters WHERE part_no = ?;", (part_no,))
                row = cursor.fetchone()
                serial_no = (row["max_s"] or 0) + 1 if (row and row["max_s"]) else 1

            # AI search phonetic tokens
            name_norm = normalize_devanagari(name)
            name_phon = get_phonetic_key(name)
            rel_norm = normalize_devanagari(rel_name)
            rel_phon = get_phonetic_key(rel_name)

            # Community determination
            if "is_muslim" in data and data["is_muslim"] is not None:
                is_m = 1 if data["is_muslim"] else 0
                m_reason = "एडमिन द्वारा निर्धारित" if is_m else ""
            else:
                is_m_det, m_reason, _ = identify_voter_community(name, rel_name, rel_type)
                is_m = 1 if is_m_det else 0

            # Caste determination
            caste_key = data.get("caste_key")
            caste_source = None
            caste_reason = None

            if is_m:
                caste_key = "muslim"
                caste_source = "direct_community"
                caste_reason = "प्रत्यक्ष समुदाय पहचान (मुस्लिम)"
            elif caste_key and str(caste_key).strip().lower() not in ("", "all", "none", "null"):
                caste_key = str(caste_key).strip().lower()
                caste_source = "manual_admin"
                caste_reason = "एडमिन द्वारा प्रविष्टि (Manual Override)"
            else:
                # Auto-detect using direct surname first
                c_det, m_sur = detect_direct_caste(name, rel_name, rel_type, False)
                if c_det and c_det != "muslim":
                    caste_key = c_det
                    caste_source = "direct_surname"
                    caste_reason = f"प्रत्यक्ष उपनाम मिलान ({m_sur})"
                elif house_no and part_no:
                    # Check household dominant caste in database
                    cursor.execute("""
                        SELECT caste_key, COUNT(*) as cnt 
                        FROM voters 
                        WHERE part_no = ? AND house_no = ? AND caste_key IS NOT NULL AND caste_key != 'muslim'
                        GROUP BY caste_key ORDER BY cnt DESC LIMIT 1;
                    """, (part_no, house_no))
                    h_row = cursor.fetchone()
                    if h_row and h_row["caste_key"]:
                        caste_key = h_row["caste_key"]
                        caste_source = "household_ai"
                        caste_reason = f"मकान AI: मकान नं० {house_no} में पारिवारिक रिश्ता/क्लस्टर से जाति निर्धारित"

            voter_surname = extract_surname(name)
            rel_surname = extract_surname(rel_name)
            now_iso = datetime.now().isoformat()

            cursor.execute("""
                INSERT INTO voters (
                    serial_no, name, relation_type, relation_name,
                    house_no, age, gender, epic_no,
                    assembly, part_no, section_no, polling_station,
                    created_at, updated_at,
                    name_normalized, name_phonetic, rel_normalized, rel_phonetic,
                    is_muslim, muslim_reason,
                    voter_surname, rel_surname,
                    caste_key, caste_source, caste_reason,
                    is_deleted, deleted_reason,
                    source_file
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    'admin_direct_entry'
                );
            """, (
                serial_no, name, rel_type, rel_name,
                house_no, age, gender, epic_no,
                assembly, part_no, section_no, polling_station,
                now_iso, now_iso,
                name_norm, name_phon, rel_norm, rel_phon,
                is_m, m_reason,
                voter_surname, rel_surname,
                caste_key, caste_source, caste_reason,
                is_deleted, deleted_reason
            ))
            new_id = cursor.lastrowid
            conn.commit()

        return cls.get_voter_by_id(new_id, is_public=False)

    @classmethod
    def delete_voter(cls, voter_id: int) -> bool:
        """Deletes a single voter by ID."""
        if not cls._initialized:
            cls.init_db()
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM voters WHERE id = ?;", (voter_id,))
            conn.commit()
            return cursor.rowcount > 0

    @classmethod
    def delete_selected_voters(cls, voter_ids: List[int]) -> int:
        """Deletes multiple selected voter records by primary key IDs."""
        if not voter_ids:
            return 0
        if not cls._initialized:
            cls.init_db()
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            total_deleted = 0
            chunk_size = 500
            for i in range(0, len(voter_ids), chunk_size):
                chunk = voter_ids[i:i + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                cursor.execute(f"DELETE FROM voters WHERE id IN ({placeholders});", chunk)
                total_deleted += cursor.rowcount
            conn.commit()
            return total_deleted

    @classmethod
    def delete_by_criteria(cls, where_params: Dict[str, Any]) -> int:
        """
        Deletes all voter records matching the given search filters
        (e.g. by part_no, assembly, name, or universal q).
        """
        if not cls._initialized:
            cls.init_db()

        q = where_params.get("q")
        name = where_params.get("name")
        relation_name = where_params.get("relation_name")
        epic_no = where_params.get("epic_no")
        part_no = where_params.get("part_no")
        assembly = where_params.get("assembly")
        gender = where_params.get("gender")
        house_no = where_params.get("house_no")
        min_age = where_params.get("min_age")
        max_age = where_params.get("max_age")
        muslim = where_params.get("muslim")

        where_clauses = ["1=1"]
        params: List[Any] = []

        if q and q.strip():
            q_clean = q.strip()
            q_exp = expand_search_query(q_clean)
            q_conds = []
            for v in q_exp["exact_variants"]:
                q_conds.append("name LIKE ?")
                params.append(f"%{v}%")
                q_conds.append("relation_name LIKE ?")
                params.append(f"%{v}%")
            for v in q_exp["normalized_variants"]:
                q_conds.append("name_normalized LIKE ?")
                params.append(f"%{v}%")
                q_conds.append("rel_normalized LIKE ?")
                params.append(f"%{v}%")
            if q_exp["phonetic_key"]:
                q_conds.append("name_phonetic LIKE ?")
                params.append(f"%{q_exp['phonetic_key']}%")
                q_conds.append("rel_phonetic LIKE ?")
                params.append(f"%{q_exp['phonetic_key']}%")
            q_conds.append("UPPER(epic_no) LIKE ?")
            params.append(f"%{q_clean.upper()}%")
            q_conds.append("house_no LIKE ?")
            params.append(f"%{q_clean}%")
            q_conds.append("polling_station LIKE ?")
            params.append(f"%{q_clean}%")
            q_conds.append("part_no LIKE ?")
            params.append(f"%{q_clean}%")
            where_clauses.append(f"({' OR '.join(q_conds)})")

        if name and name.strip():
            name_clean = name.strip()
            name_exp = expand_search_query(name_clean)
            name_conds = []
            for v in name_exp["exact_variants"]:
                name_conds.append("name LIKE ?")
                params.append(f"%{v}%")
            for v in name_exp["normalized_variants"]:
                name_conds.append("name_normalized LIKE ?")
                params.append(f"%{v}%")
            if name_exp["phonetic_key"]:
                name_conds.append("name_phonetic LIKE ?")
                params.append(f"%{name_exp['phonetic_key']}%")
            if name_conds:
                where_clauses.append(f"({' OR '.join(name_conds)})")

        if relation_name and relation_name.strip():
            rel_clean = relation_name.strip()
            rel_exp = expand_search_query(rel_clean)
            rel_conds = []
            for v in rel_exp["exact_variants"]:
                rel_conds.append("relation_name LIKE ?")
                params.append(f"%{v}%")
            for v in rel_exp["normalized_variants"]:
                rel_conds.append("rel_normalized LIKE ?")
                params.append(f"%{v}%")
            if rel_exp["phonetic_key"]:
                rel_conds.append("rel_phonetic LIKE ?")
                params.append(f"%{rel_exp['phonetic_key']}%")
            if rel_conds:
                where_clauses.append(f"({' OR '.join(rel_conds)})")

        if epic_no and epic_no.strip():
            where_clauses.append("UPPER(epic_no) LIKE ?")
            params.append(f"%{epic_no.strip().upper()}%")

        if part_no and part_no.strip() and part_no != "all":
            where_clauses.append("part_no = ?")
            params.append(part_no.strip())

        if assembly and assembly.strip() and assembly != "all":
            where_clauses.append("assembly = ?")
            params.append(assembly.strip())

        if gender and gender.strip() and gender != "all":
            where_clauses.append("gender = ?")
            params.append(gender.strip())

        if house_no and house_no.strip():
            where_clauses.append("house_no LIKE ?")
            params.append(f"%{house_no.strip()}%")

        if min_age is not None and min_age > 0:
            where_clauses.append("age >= ?")
            params.append(min_age)

        if max_age is not None and max_age > 0:
            where_clauses.append("age <= ?")
            params.append(max_age)

        if muslim and muslim.strip() and muslim != "all":
            m_clean = muslim.strip().lower()
            if m_clean in ("yes", "true", "1", "muslim"):
                where_clauses.append("is_muslim = 1")
            elif m_clean in ("no", "false", "0", "non_muslim", "other"):
                where_clauses.append("is_muslim = 0")

        where_sql = " AND ".join(where_clauses)
        if where_sql == "1=1":
            return 0  # Safety guard

        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM voters WHERE {where_sql};", params)
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    @classmethod
    def bulk_update_assembly_part(
        cls,
        current_part_no: str,
        new_assembly: Optional[str] = None,
        new_part_no: str = "",
        current_assembly: Optional[str] = None,
        new_polling_station: Optional[str] = None,
        db_id: Optional[str] = None,
        new_assembly_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Bulk updates Assembly Constituency and Part Number for all voter records
        belonging to a specific part in a single atomic transaction.
        """
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        clean_curr_part = str(current_part_no).strip() if current_part_no is not None else ""
        clean_new_part = str(new_part_no).strip() if new_part_no is not None else ""
        clean_new_assembly = str(new_assembly or new_assembly_name or "").strip()
        clean_new_station = str(new_polling_station).strip() if new_polling_station is not None else ""

        if not clean_curr_part:
            raise ValueError("वर्तमान भाग संख्या देना अनिवार्य है।")

        # If user didn't enter new part no, retain current part
        if not clean_new_part:
            clean_new_part = clean_curr_part

        if clean_new_part == clean_curr_part and not clean_new_assembly and not clean_new_station:
            raise ValueError("कृपया कम से कम नई भाग संख्या या नई विधान सभा या नया मतदान केंद्र दर्ज करें।")

        with cls.get_connection(db_id=db_id, purpose="write") as conn:
            cursor = conn.cursor()

            where_sql = "WHERE part_no = ?"
            where_params = [clean_curr_part]
            if current_assembly and current_assembly.strip() and current_assembly.strip() != "all":
                where_sql += " AND assembly = ?"
                where_params.append(current_assembly.strip())

            cursor.execute(f"SELECT COUNT(*) FROM voters {where_sql};", where_params)
            matching_count = cursor.fetchone()[0]

            if matching_count == 0:
                return {
                    "status": "error",
                    "updated_count": 0,
                    "affected_voters": 0,
                    "message": f"भाग संख्या '{clean_curr_part}' में कोई मतदाता रिकॉर्ड नहीं मिला।"
                }

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            set_clauses = []
            update_params = []

            if clean_new_assembly:
                set_clauses.append("assembly = ?")
                update_params.append(clean_new_assembly)
            if clean_new_part:
                set_clauses.append("part_no = ?")
                update_params.append(clean_new_part)
            if clean_new_station:
                set_clauses.append("polling_station = ?")
                update_params.append(clean_new_station)

            set_clauses.append("updated_at = ?")
            update_params.append(now_str)

            set_sql = ", ".join(set_clauses)
            update_sql = f"UPDATE voters SET {set_sql} {where_sql};"

            cursor.execute(update_sql, update_params + where_params)
            affected = cursor.rowcount
            conn.commit()

            msg_parts = []
            if clean_new_assembly:
                msg_parts.append(f"विधान सभा: '{clean_new_assembly}'")
            if clean_new_part != clean_curr_part:
                msg_parts.append(f"भाग संख्या: '{clean_new_part}'")
            if clean_new_station:
                msg_parts.append(f"मतदान केंद्र: '{clean_new_station}'")
            details_str = ", ".join(msg_parts) if msg_parts else f"भाग संख्या {clean_new_part}"

            return {
                "status": "success",
                "updated_count": affected,
                "affected_voters": affected,
                "current_part_no": clean_curr_part,
                "new_part_no": clean_new_part,
                "new_assembly": clean_new_assembly or current_assembly,
                "new_polling_station": clean_new_station or None,
                "message": f"सफलतापूर्वक भाग {clean_curr_part} के {affected} मतदाताओं का विवरण ({details_str}) अपडेट हो गया।"
            }

    @classmethod
    def clear_all(cls, db_id: Optional[str] = None) -> int:
        """Clears all voter records from database."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)
        with cls.get_connection(db_id=db_id, purpose="write") as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM voters;")
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    @classmethod
    def reindex_community(cls) -> Dict[str, Any]:
        """
        Re-evaluates community (Muslim) identification for all voters in the database.
        Applies the core rule:
        - If voter name is Muslim OR relative name is Muslim -> is_muslim = 1
        """
        if not cls._initialized:
            cls.init_db()

        with cls.get_connection() as conn:
            cls._ensure_community_columns(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, relation_name, relation_type FROM voters;")
            rows = cursor.fetchall()
            
            analyzed = 0
            muslim_count = 0
            for r in rows:
                is_m, reason, _ = identify_voter_community(
                    r["name"],
                    r["relation_name"],
                    r["relation_type"] or "पिता"
                )
                is_m_val = 1 if is_m else 0
                cursor.execute(
                    "UPDATE voters SET is_muslim = ?, muslim_reason = ? WHERE id = ?;",
                    (is_m_val, reason, r["id"])
                )
                analyzed += 1
                if is_m:
                    muslim_count += 1

            conn.commit()
            
            pct = round((muslim_count / analyzed * 100), 1) if analyzed > 0 else 0.0
            return {
                "total_analyzed": analyzed,
                "muslim_identified": muslim_count,
                "non_muslim_count": analyzed - muslim_count,
                "muslim_percentage": pct
            }

    @classmethod
    def export_to_excel(cls, where_params: Dict[str, Any], output_path: str, redact_caste: bool = False) -> str:
        """
        Exports searched voter records matching the given criteria directly to a styled Excel file.
        If redact_caste is True, hides caste and surname fields (for Operator users).
        """
        if redact_caste:
            where_params["caste_key"] = None

        search_res = cls.search_voters(
            q=where_params.get("q"),
            name=where_params.get("name"),
            relation_name=where_params.get("relation_name"),
            epic_no=where_params.get("epic_no"),
            part_no=where_params.get("part_no"),
            assembly=where_params.get("assembly"),
            gender=where_params.get("gender"),
            house_no=where_params.get("house_no"),
            min_age=where_params.get("min_age"),
            max_age=where_params.get("max_age"),
            muslim=where_params.get("muslim"),
            caste_key=None if redact_caste else where_params.get("caste_key"),
            status=where_params.get("status"),
            page=1,
            limit=50000  # Export up to 50,000 matches
        )
        records = search_res["records"]

        # Convert dict rows to VoterRecord objects for ExcelBuilder
        voter_records: List[VoterRecord] = []
        for r in records:
            vr = VoterRecord(
                serial_no=r.get("serial_no") or 1,
                name=r.get("name") or "",
                relation_type=r.get("relation_type") or "पिता",
                relation_name=r.get("relation_name") or "",
                house_no=r.get("house_no") or "",
                age=r.get("age"),
                gender=r.get("gender") or "पुरुष",
                epic_no=r.get("epic_no") or "",
                assembly=r.get("assembly"),
                part_no=r.get("part_no"),
                polling_station=r.get("polling_station"),
                page_no=r.get("page_no") or 1,
                is_muslim=False if redact_caste else bool(r.get("is_muslim")),
                muslim_reason=None if redact_caste else r.get("muslim_reason"),
                caste_key=None if redact_caste else r.get("caste_key"),
                caste_source=None if redact_caste else r.get("caste_source"),
                voter_surname=None if redact_caste else r.get("voter_surname"),
                rel_surname=None if redact_caste else r.get("rel_surname"),
                is_deleted=bool(r.get("is_deleted")),
                deleted_reason=r.get("deleted_reason")
            )
            voter_records.append(vr)

        from .excel_builder import ExcelBuilder
        ExcelBuilder.generate_excel(
            records=voter_records,
            output_path=output_path,
            assembly_name=where_params.get("assembly") or "मास्टर डेटाबेस (Master DB)",
            part_no=where_params.get("part_no") or "सभी भाग",
            polling_station="स्थानीय डेटाबेस खोज परिणाम",
            filename_source="voters_master_db.sqlite"
        )
        return output_path

    @classmethod
    def reindex_surnames(cls) -> Dict[str, Any]:
        """
        Re-extracts and saves voter and relative surnames for all records in the database.
        Ensures 100% accurate caste recognition indexing.
        """
        if not cls._initialized:
            cls.init_db()

        with cls.get_connection() as conn:
            cls._ensure_surname_columns(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, relation_name FROM voters;")
            rows = cursor.fetchall()
            count = 0
            for r in rows:
                v_sur = extract_surname(r["name"])
                r_sur = extract_surname(r["relation_name"])
                cursor.execute(
                    "UPDATE voters SET voter_surname = ?, rel_surname = ? WHERE id = ?;",
                    (v_sur, r_sur, r["id"])
                )
                count += 1
            conn.commit()
            return {"status": "success", "total_indexed": count}

    @classmethod
    def recompute_all_castes(cls) -> Dict[str, Any]:
        """
        Recomputes caste identification for all voters in the database
        using the LocalCasteAIEngine (Direct surname + Household Part/House clustering + Lineage).
        """
        if not cls._initialized:
            cls.init_db()

        with cls.get_connection() as conn:
            cls._ensure_surname_columns(conn)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, serial_no, name, relation_name, relation_type, house_no, part_no, is_muslim, caste_key, caste_source, caste_reason
                FROM voters
                ORDER BY CAST(part_no AS INTEGER), house_no, CAST(serial_no AS INTEGER);
            """)
            rows = cursor.fetchall()
            if not rows:
                return {
                    "status": "success",
                    "total_analyzed": 0,
                    "total_classified": 0,
                    "source_breakdown": {},
                    "caste_breakdown": {}
                }

            records_dict = []
            for r in rows:
                d = dict(r)
                d["house_no"] = clean_house_no(d.get("house_no") or "")
                records_dict.append(d)

            inferred = LocalCasteAIEngine.infer_caste_for_records(records_dict)

            update_data = []
            caste_counts: Dict[str, int] = defaultdict(int)
            source_counts: Dict[str, int] = defaultdict(int)

            for rec in inferred:
                v_sur = extract_surname(rec.get("name") or "")
                r_sur = extract_surname(rec.get("relation_name") or "")
                c_key = rec.get("caste_key")
                c_src = rec.get("caste_source")
                c_reason = rec.get("caste_reason")
                is_m = rec.get("is_muslim")

                # Strict Rule: When voter is Muslim, they never receive any caste based on house number!
                if is_m or c_key == "muslim":
                    c_key = "muslim"
                    c_src = "direct_community"
                    c_reason = c_reason or "प्रत्यक्ष समुदाय पहचान (मुस्लिम)"
                    is_m = 1
                elif c_src in ("manual_admin", "admin"):
                    c_src = "manual_admin"

                if c_key:
                    caste_counts[c_key] += 1
                if c_src:
                    source_counts[c_src] += 1

                update_data.append((c_key, c_src, c_reason, v_sur, r_sur, is_m, rec.get("house_no"), rec["id"]))

            cursor.executemany("""
                UPDATE voters
                SET caste_key = ?, caste_source = ?, caste_reason = ?, voter_surname = ?, rel_surname = ?,
                    is_muslim = ?, house_no = ?
                WHERE id = ?;
            """, update_data)
            conn.commit()

            return {
                "status": "success",
                "total_analyzed": len(inferred),
                "total_classified": sum(caste_counts.values()),
                "source_breakdown": dict(source_counts),
                "caste_breakdown": dict(caste_counts)
            }

    @classmethod
    def get_all_voters_for_audit(cls, db_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all active voter records in memory for high-speed phonetic audit matching."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)
        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, serial_no, part_no, name, relation_name, relation_type,
                       house_no, age, gender, epic_no, caste_key, caste_reason
                FROM voters
                WHERE is_deleted = 0;
            """)
            return [dict(r) for r in cursor.fetchall()]

    @classmethod
    def get_voters_by_house(cls, part_no: Optional[str], house_no: str, db_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns all registered voters residing in a specific part_no and house_no,
        or across all parts if part_no is 'ALL', empty, or None.
        Annotated with any active mapping to an NPPropertyServey family member.
        """
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        clean_h = clean_house_no(house_no or "")
        norm_part = str(part_no or "").strip()
        is_all_parts = not norm_part or norm_part.upper() in ("ALL", "ANY", "-- समस्त भाग --")

        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cls._ensure_mapping_table(conn)
            cursor = conn.cursor()

            if is_all_parts:
                cursor.execute("""
                    SELECT v.id, v.serial_no, v.part_no, v.name, v.relation_type, v.relation_name,
                           v.house_no, v.age, v.gender, v.epic_no, v.caste_key, v.caste_source, v.caste_reason,
                           v.is_muslim, v.is_deleted,
                           m.id as mapping_id, m.family_id, m.member_id, m.member_name as mapped_member_name, m.mapped_at
                    FROM voters v
                    LEFT JOIN survey_voter_mappings m ON v.id = m.voter_id
                    WHERE (v.house_no = ? OR v.house_no = ?) AND (v.is_deleted = 0 OR v.is_deleted IS NULL)
                    ORDER BY CAST(v.part_no AS INTEGER), CAST(v.serial_no AS INTEGER);
                """, (house_no, clean_h))
                rows = cursor.fetchall()

                if not rows:
                    cursor.execute("""
                        SELECT v.id, v.serial_no, v.part_no, v.name, v.relation_type, v.relation_name,
                               v.house_no, v.age, v.gender, v.epic_no, v.caste_key, v.caste_source, v.caste_reason,
                               v.is_muslim, v.is_deleted,
                               m.id as mapping_id, m.family_id, m.member_id, m.member_name as mapped_member_name, m.mapped_at
                        FROM voters v
                        LEFT JOIN survey_voter_mappings m ON v.id = m.voter_id
                        WHERE (v.is_deleted = 0 OR v.is_deleted IS NULL)
                        ORDER BY CAST(v.part_no AS INTEGER), CAST(v.serial_no AS INTEGER);
                    """)
                    all_rows = cursor.fetchall()
                    target_clean = clean_house_no(house_no)
                    rows = [r for r in all_rows if clean_house_no(r["house_no"] or "") == target_clean]
            else:
                cursor.execute("""
                    SELECT v.id, v.serial_no, v.part_no, v.name, v.relation_type, v.relation_name,
                           v.house_no, v.age, v.gender, v.epic_no, v.caste_key, v.caste_source, v.caste_reason,
                           v.is_muslim, v.is_deleted,
                           m.id as mapping_id, m.family_id, m.member_id, m.member_name as mapped_member_name, m.mapped_at
                    FROM voters v
                    LEFT JOIN survey_voter_mappings m ON v.id = m.voter_id
                    WHERE v.part_no = ? AND (v.house_no = ? OR v.house_no = ?) AND (v.is_deleted = 0 OR v.is_deleted IS NULL)
                    ORDER BY CAST(v.serial_no AS INTEGER);
                """, (norm_part, house_no, clean_h))
                rows = cursor.fetchall()

                # If no rows found with exact house_no, query all voters in part and filter with clean_house_no
                if not rows and norm_part:
                    cursor.execute("""
                        SELECT v.id, v.serial_no, v.part_no, v.name, v.relation_type, v.relation_name,
                               v.house_no, v.age, v.gender, v.epic_no, v.caste_key, v.caste_source, v.caste_reason,
                               v.is_muslim, v.is_deleted,
                               m.id as mapping_id, m.family_id, m.member_id, m.member_name as mapped_member_name, m.mapped_at
                        FROM voters v
                        LEFT JOIN survey_voter_mappings m ON v.id = m.voter_id
                        WHERE v.part_no = ? AND (v.is_deleted = 0 OR v.is_deleted IS NULL)
                        ORDER BY CAST(v.serial_no AS INTEGER);
                    """, (norm_part,))
                    all_part_rows = cursor.fetchall()
                    target_clean = clean_house_no(house_no)
                    rows = [r for r in all_part_rows if clean_house_no(r["house_no"] or "") == target_clean]

            parts_found = {}
            results = []
            for r in rows:
                d = dict(r)
                d["is_mapped"] = bool(d.get("mapping_id"))
                results.append(d)
                p = str(d.get("part_no") or "").strip()
                if p:
                    parts_found[p] = parts_found.get(p, 0) + 1

            # Format parts_found list
            parts_summary = [{"part_no": p, "count": c} for p, c in sorted(parts_found.items(), key=lambda x: (int(x[0]) if x[0].isdigit() else 9999))]
            primary_part = norm_part if (not is_all_parts and norm_part) else (parts_summary[0]["part_no"] if parts_summary else "")

            return {
                "voters": results,
                "count": len(results),
                "parts_found": parts_summary,
                "active_part": primary_part,
                "house_no": house_no
            }

    @classmethod
    def get_distinct_parts(cls, db_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all distinct parts in the voter database with voter counts."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)
        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT part_no, polling_station, COUNT(*) as voter_count
                FROM voters
                WHERE (is_deleted = 0 OR is_deleted IS NULL) AND part_no IS NOT NULL AND part_no != ''
                GROUP BY part_no
                ORDER BY CAST(part_no AS INTEGER);
            """)
            return [dict(r) for r in cursor.fetchall()]

    @classmethod
    def search_voter_candidate(
        cls,
        q: str,
        part_no: Optional[str] = None,
        limit: int = 30,
        db_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        High-speed candidate search across the voter database for manual mapping.
        Matches against EPIC, Name, Relative Name, or House No.
        """
        if not cls._initialized:
            cls.init_db(db_id=db_id)
        clean_q = (q or "").strip()
        if not clean_q:
            return []

        # Detect Latin/English characters and transliterate
        if re.search(r'[a-zA-Z]', clean_q):
            from .ai_search import transliterate_latin_to_hindi
            trans_q = transliterate_latin_to_hindi(clean_q)
        else:
            trans_q = clean_q

        norm_q = clean_and_normalize_name(trans_q)
        like_pat = f"%{norm_q}%"
        raw_like = f"%{clean_q}%"

        norm_part = str(part_no or "").strip()
        has_part = bool(norm_part and norm_part.upper() not in ("ALL", "ANY", "-- समस्त भाग --"))

        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cls._ensure_mapping_table(conn)
            cursor = conn.cursor()

            sql = """
                SELECT v.id, v.serial_no, v.part_no, v.name, v.relation_type, v.relation_name,
                       v.house_no, v.age, v.gender, v.epic_no, v.caste_key, v.caste_source, v.caste_reason,
                       v.is_muslim,
                       m.id as mapping_id, m.family_id, m.member_id, m.member_name as mapped_member_name
                FROM voters v
                LEFT JOIN survey_voter_mappings m ON v.id = m.voter_id
                WHERE (v.is_deleted = 0 OR v.is_deleted IS NULL)
                  AND (
                      v.name LIKE ?
                      OR v.relation_name LIKE ?
                      OR v.epic_no LIKE ?
                      OR v.house_no = ?
                      OR v.serial_no = ?
                  )
            """
            params: List[Any] = [like_pat, like_pat, raw_like, clean_q, clean_q if clean_q.isdigit() else -1]
            if has_part:
                sql += " AND v.part_no = ?"
                params.append(norm_part)

            sql += """
                ORDER BY
                  CASE WHEN v.epic_no = ? THEN 1
                       WHEN v.name = ? THEN 2
                       WHEN v.name LIKE ? THEN 3
                       ELSE 4
                  END,
                  CAST(v.part_no AS INTEGER),
                  CAST(v.serial_no AS INTEGER)
                LIMIT ?;
            """
            params.extend([clean_q.upper(), norm_q, like_pat, limit])

            cursor.execute(sql, params)
            res = []
            for r in cursor.fetchall():
                d = dict(r)
                d["is_mapped"] = bool(d.get("mapping_id"))
                res.append(d)
            return res

    @classmethod
    def save_member_voter_mapping(
        cls,
        family_id: str,
        member_id: str,
        voter_id: int,
        survey_id: Optional[str] = None,
        member_name: Optional[str] = None,
        notes: Optional[str] = None,
        mapped_by: str = "admin",
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Saves or updates a mapping between an NPPropertyServey family member and a Voter record.
        """
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        with cls.get_connection(db_id=db_id, purpose="write") as conn:
            cls._ensure_mapping_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT id, serial_no, part_no, name, epic_no, house_no FROM voters WHERE id = ?;", (voter_id,))
            voter = cursor.fetchone()
            if not voter:
                raise ValueError(f"मतदाता आईडी {voter_id} डेटाबेस में नहीं मिला।")

            cursor.execute("""
                INSERT INTO survey_voter_mappings (
                    survey_id, family_id, member_id, member_name, voter_id,
                    epic_no, part_no, serial_no, voter_name, mapped_by, notes, mapped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(family_id, member_id) DO UPDATE SET
                    survey_id = excluded.survey_id,
                    voter_id = excluded.voter_id,
                    member_name = excluded.member_name,
                    epic_no = excluded.epic_no,
                    part_no = excluded.part_no,
                    serial_no = excluded.serial_no,
                    voter_name = excluded.voter_name,
                    mapped_by = excluded.mapped_by,
                    notes = excluded.notes,
                    mapped_at = CURRENT_TIMESTAMP;
            """, (
                survey_id, family_id, member_id, member_name, voter["id"],
                voter["epic_no"], voter["part_no"], voter["serial_no"], voter["name"],
                mapped_by, notes
            ))
            conn.commit()

            return {
                "success": True,
                "family_id": family_id,
                "member_id": member_id,
                "voter_id": voter["id"],
                "voter_name": voter["name"],
                "epic_no": voter["epic_no"],
                "part_no": voter["part_no"],
                "serial_no": voter["serial_no"]
            }

    @classmethod
    def delete_member_voter_mapping(
        cls,
        family_id: str,
        member_id: str,
        db_id: Optional[str] = None
    ) -> bool:
        """Deletes a member-to-voter mapping."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        with cls.get_connection(db_id=db_id, purpose="write") as conn:
            cls._ensure_mapping_table(conn)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM survey_voter_mappings WHERE family_id = ? AND member_id = ?;", (family_id, member_id))
            conn.commit()
            return cursor.rowcount > 0

    @classmethod
    def get_mappings_by_family(cls, family_id: str, db_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all active mappings for a given NPPropertyServey family."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cls._ensure_mapping_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM survey_voter_mappings WHERE family_id = ?;", (family_id,))
            return [dict(r) for r in cursor.fetchall()]

    @classmethod
    def get_all_survey_mappings_dict(cls, db_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Returns all survey voter mappings indexed by f'{family_id}_{member_id}'."""
        if not cls._initialized:
            cls.init_db(db_id=db_id)

        with cls.get_connection(db_id=db_id, purpose="read") as conn:
            cls._ensure_mapping_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM survey_voter_mappings;")
            mappings = {}
            for r in cursor.fetchall():
                d = dict(r)
                key = f"{d.get('family_id')}_{d.get('member_id')}"
                mappings[key] = d
            return mappings


