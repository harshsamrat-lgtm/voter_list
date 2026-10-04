"""
Panchayat & Nikay Voter Comparison Engine (पंचायत एवं निकाय वोटर स्कैनर व तुलना).

Provides dedicated workflows for:
1. Scanning and persisting Gram Panchayat & Nagar Panchayat electoral rolls in a separate database table (`panchayat_voters`).
2. Detecting intra-list duplicates (खुद की लिस्ट में डुप्लीकेट छांटना) by EPIC, Name+Relative, and Local AI Soundex + Age Proximity.
3. Cross-comparing against Nagar Panchayat Master Data (`voters` table) to identify Dual/Duplicate Voters (दोहरी मतदाता पहचान).
4. Generating professional Excel and PDF comparison reports.
"""

import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple
import sqlite3
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .database import VoterDatabase
from .ai_search import normalize_devanagari, get_phonetic_key
from .validator import clean_house_no, clean_hindi_text, normalize_relation_type
from ..models.voter import VoterRecord

HONORIFIC_PREFIXES = {'श्री', 'श्रीमती', 'कु०', 'कु0', 'कुमारी', 'मो०', 'मो0', 'मोहम्मद', 'डॉ०', 'डा०', 'डा0', 'डॉ0', 'स्व०', 'स्व0', 'ला०', 'ला0'}


def _clean_hn(h: Any) -> str:
    """Normalizes house number by removing OCR noise symbols and converting Devanagari numerals."""
    if not h:
        return ""
    h_str = str(h).strip()
    h_str = re.sub(r'[^\w\/\-]', '', h_str)
    deva_digits = '०१२३४५६७८९'
    for i, d in enumerate(deva_digits):
        h_str = h_str.replace(d, str(i))
    return h_str.lower()


def _clean_ocr_text(text: Any) -> str:
    """Collapses OCR split spaces around Hindi matras (e.g. 'आयुि श' -> 'आयुिश')."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", str(text).strip())
    t = t.replace('\u093c', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')
    t = re.sub(r'\s+([\u093e-\u094f])', r'\1', t)
    t = re.sub(r'([\u093e-\u094f])\s+', r'\1', t)
    return re.sub(r'\s+', ' ', t).strip()


def _extract_core_first_name(name_str: str) -> str:
    """Extracts the primary given first name, skipping honorifics."""
    if not name_str:
        return ""
    tokens = [t for t in name_str.strip().split() if t not in HONORIFIC_PREFIXES]
    return tokens[0] if tokens else ""


def _are_first_names_matching(n1_raw: str, n2_raw: str, norm1: str, norm2: str, phon1: str, phon2: str) -> Tuple[bool, str]:
    """
    Validates first-name integrity to strictly avoid false duplicate positives
    between brothers, sisters, or parents sharing the same father, house and surname
    (e.g., Rajiv vs Sudhir, Archana vs Hema, Pramod vs Praveen).
    """
    fn1 = _extract_core_first_name(norm1)
    fn2 = _extract_core_first_name(norm2)
    if not fn1 or not fn2:
        return False, "रिक्त प्रथम नाम"

    if fn1 == fn2:
        return True, "सटीक प्रथम नाम"

    cfn1 = _clean_ocr_text(fn1).replace(' ', '')
    cfn2 = _clean_ocr_text(fn2).replace(' ', '')
    if cfn1 == cfn2:
        return True, "समान प्रथम नाम"

    sim_fn = SequenceMatcher(None, cfn1, cfn2).ratio()
    if sim_fn >= 0.80 and abs(len(cfn1) - len(cfn2)) <= 2:
        return True, f"प्रथम नाम साम्य ({int(sim_fn * 100)}%)"

    p_fn1 = get_phonetic_key(fn1)
    p_fn2 = get_phonetic_key(fn2)
    if p_fn1 and p_fn2 and p_fn1 == p_fn2 and len(p_fn1) >= 2:
        return True, "ध्वनि प्रथम नाम"

    return False, "भिन्न प्रथम नाम"


def _are_rel_names_matching(r1_raw: str, r2_raw: str, r1_norm: str, r2_norm: str, r1_phon: str, r2_phon: str) -> Tuple[bool, str]:
    """Matches relation names considering OCR typos, missing surnames, and prefix tokens."""
    if r1_norm and r2_norm and r1_norm == r2_norm:
        return True, "सटीक सम्बन्धी"

    t1_list = (r1_norm or "").split()
    t2_list = (r2_norm or "").split()
    tokens1 = set(t1_list)
    tokens2 = set(t2_list)

    if tokens1 and tokens2:
        if tokens1.issubset(tokens2) or tokens2.issubset(tokens1):
            return True, "सम्बन्धी नाम उपसमूह (Subset Token)"
        if t1_list[0] == t2_list[0]:
            return True, "सम्बन्धी प्रथम नाम मिलान"
        sim_first = SequenceMatcher(None, t1_list[0], t2_list[0]).ratio()
        if sim_first >= 0.75:
            return True, f"सम्बन्धी प्रथम नाम साम्य ({int(sim_first * 100)}%)"

    p_tokens1 = (r1_phon or "").split()
    p_tokens2 = (r2_phon or "").split()
    if p_tokens1 and p_tokens2:
        if set(p_tokens1).issubset(set(p_tokens2)) or set(p_tokens2).issubset(set(p_tokens1)):
            return True, "ध्वन्यात्मक उपसमूह"
        if p_tokens1[0] == p_tokens2[0] and len(p_tokens1[0]) >= 2:
            return True, "प्रथम ध्वन्यात्मक टोकन मिलान"

    sim = SequenceMatcher(None, r1_norm or "", r2_norm or "").ratio()
    if sim >= 0.70:
        return True, f"सम्बन्धी नाम साम्य ({int(sim * 100)}%)"

    p_sim = SequenceMatcher(None, r1_phon or "", r2_phon or "").ratio()
    if p_sim >= 0.70:
        return True, f"सम्बन्धी ध्वनि साम्य ({int(p_sim * 100)}%)"

    return False, "none"


def _are_voter_names_matching(n1_raw: str, n2_raw: str, n1_norm: str, n2_norm: str, n1_phon: str, n2_phon: str) -> Tuple[bool, str]:
    """Validates full voter names with first-name preservation and OCR error tolerance."""
    fn_match, fn_reason = _are_first_names_matching(n1_raw, n2_raw, n1_norm, n2_norm, n1_phon, n2_phon)
    if not fn_match:
        return False, fn_reason

    if n1_norm and n2_norm and n1_norm == n2_norm:
        return True, "सटीक नाम"

    c1 = _clean_ocr_text(n1_norm).replace(' ', '')
    c2 = _clean_ocr_text(n2_norm).replace(' ', '')
    sim_c = SequenceMatcher(None, c1, c2).ratio()
    if sim_c >= 0.75:
        return True, f"नाम साम्य ({int(sim_c * 100)}%)"

    p_sim = SequenceMatcher(None, n1_phon or "", n2_phon or "").ratio()
    if p_sim >= 0.75:
        return True, f"ध्वनि साम्य ({int(p_sim * 100)}%)"

    return False, "none"


class _DisjointSetUnion:
    """Disjoint Set Union (Union-Find) for cohesive transitive duplicate clustering."""
    def __init__(self):
        self.parent = {}

    def find(self, i: Any) -> Any:
        if self.parent.setdefault(i, i) != i:
            self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i: Any, j: Any):
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j


class PanchayatService:
    """Handles storage, internal deduplication, and cross-body comparison for Panchayat & Nikay voters."""

    @classmethod
    def ensure_table(cls, conn: sqlite3.Connection):
        """Ensures the panchayat_voters table and performance indexes exist."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS panchayat_voters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body_type TEXT DEFAULT 'gram_panchayat',
                body_name TEXT,
                ward_no TEXT,
                ward_name TEXT,
                part_no TEXT,
                polling_booth TEXT,
                mohalla TEXT,
                serial_no INTEGER,
                name TEXT NOT NULL,
                relation_type TEXT DEFAULT 'पिता',
                relation_name TEXT,
                house_no TEXT,
                age INTEGER,
                gender TEXT DEFAULT 'पुरुष',
                epic_no TEXT,
                polling_station TEXT,
                section_no TEXT,
                page_no INTEGER,
                source_file TEXT,
                created_at TEXT,
                updated_at TEXT,
                name_normalized TEXT,
                name_phonetic TEXT,
                rel_normalized TEXT,
                rel_phonetic TEXT,
                is_deleted INTEGER DEFAULT 0,
                deleted_reason TEXT
            );
        """)

        # Migration columns if table already existed without them
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(panchayat_voters);")
            cols = {row[1] for row in cursor.fetchall()}
            if "part_no" not in cols:
                cursor.execute("ALTER TABLE panchayat_voters ADD COLUMN part_no TEXT;")
            if "polling_booth" not in cols:
                cursor.execute("ALTER TABLE panchayat_voters ADD COLUMN polling_booth TEXT;")
            if "mohalla" not in cols:
                cursor.execute("ALTER TABLE panchayat_voters ADD COLUMN mohalla TEXT;")
        except Exception:
            pass

        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_name ON panchayat_voters(name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_rel_name ON panchayat_voters(relation_name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_epic ON panchayat_voters(epic_no);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_body_ward ON panchayat_voters(body_name, ward_no);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_part_no ON panchayat_voters(part_no);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_name_norm ON panchayat_voters(name_normalized);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_name_phon ON panchayat_voters(name_phonetic);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_rel_norm ON panchayat_voters(rel_normalized);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_rel_phon ON panchayat_voters(rel_phonetic);")
        conn.commit()

    @classmethod
    def save_voters(
        cls,
        records: List[Any],
        body_type: str = "gram_panchayat",
        body_name: str = "",
        ward_no: str = "",
        ward_name: str = "",
        part_no: str = "",
        polling_station: str = "",
        polling_booth: str = "",
        mohalla: str = "",
        source_file: str = "",
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Saves voter records into the dedicated `panchayat_voters` table.
        Computes AI Devanagari normalized variants and phonetic Soundex keys.
        """
        if not records:
            return {"inserted": 0, "total_in_panchayat_db": cls.get_total_count(db_id=db_id)}

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        inserted = 0

        clean_body_type = "gram_panchayat" if "gram" in str(body_type).lower() or "ग्राम" in str(body_type) else "nagar_panchayat"
        clean_body_name = (body_name or "").strip() or ("ग्राम पंचायत" if clean_body_type == "gram_panchayat" else "नगर पंचायत")
        clean_ward_no = str(ward_no or "").strip()
        clean_ward_name = str(ward_name or "").strip()
        clean_part_no = str(part_no or "").strip()
        # Enforce that every voter list must have a part_no (fallback to ward_no if not specified)
        if not clean_part_no:
            clean_part_no = clean_ward_no or "1"
        clean_ps = str(polling_station or "").strip()
        clean_pb = str(polling_booth or "").strip()
        clean_moh = str(mohalla or "").strip()

        with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            for r in records:
                if isinstance(r, dict):
                    r_serial = r.get("serial_no")
                    r_name = str(r.get("name") or "").strip()
                    r_rel_type = str(r.get("relation_type") or "पिता").strip()
                    r_rel_name = str(r.get("relation_name") or r.get("father_name") or "").strip()
                    r_house = str(r.get("house_no") or "").strip()
                    r_age = r.get("age")
                    r_gender = str(r.get("gender") or "पुरुष").strip()
                    r_epic = str(r.get("epic_no") or "").strip().upper()
                    r_ps = str(r.get("polling_station") or clean_ps).strip()
                    r_pb = str(r.get("polling_booth") or clean_pb).strip()
                    r_part = str(r.get("part_no") or clean_part_no).strip()
                    r_moh = str(r.get("mohalla") or clean_moh).strip()
                    r_ward_no = str(r.get("ward_no") or clean_ward_no).strip()
                    r_ward_name = str(r.get("ward_name") or clean_ward_name).strip()
                    r_sec = str(r.get("section_no") or "").strip()
                    r_page = r.get("page_no") or 1
                else:
                    r_serial = getattr(r, "serial_no", None)
                    r_name = str(getattr(r, "name", "") or "").strip()
                    r_rel_type = str(getattr(r, "relation_type", "पिता") or "पिता").strip()
                    r_rel_name = str(getattr(r, "relation_name", "") or "").strip()
                    r_house = str(getattr(r, "house_no", "") or "").strip()
                    r_age = getattr(r, "age", None)
                    r_gender = str(getattr(r, "gender", "पुरुष") or "पुरुष").strip()
                    r_epic = str(getattr(r, "epic_no", "") or "").strip().upper()
                    r_ps = str(getattr(r, "polling_station", "") or clean_ps).strip()
                    r_pb = str(getattr(r, "polling_booth", "") or clean_pb).strip()
                    r_part = str(getattr(r, "part_no", "") or clean_part_no).strip()
                    r_moh = str(getattr(r, "mohalla", "") or clean_moh).strip()
                    r_ward_no = str(getattr(r, "ward_no", "") or clean_ward_no).strip()
                    r_ward_name = str(getattr(r, "ward_name", "") or clean_ward_name).strip()
                    r_sec = str(getattr(r, "section_no", "") or "").strip()
                    r_page = getattr(r, "page_no", 1) or 1

                if not r_name:
                    continue

                try:
                    r_age_val = int(r_age) if r_age else None
                except (ValueError, TypeError):
                    r_age_val = None

                try:
                    r_serial_val = int(r_serial) if r_serial else None
                except (ValueError, TypeError):
                    r_serial_val = None

                norm_m = normalize_devanagari(r_name)
                phon_m = get_phonetic_key(r_name)
                norm_r = normalize_devanagari(r_rel_name)
                phon_r = get_phonetic_key(r_rel_name)

                cursor.execute("""
                    INSERT INTO panchayat_voters (
                        body_type, body_name, ward_no, ward_name, part_no, polling_booth, mohalla, serial_no,
                        name, relation_type, relation_name, house_no, age, gender, epic_no,
                        polling_station, section_no, page_no, source_file,
                        created_at, updated_at,
                        name_normalized, name_phonetic, rel_normalized, rel_phonetic
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    clean_body_type,
                    clean_body_name,
                    r_ward_no or clean_ward_no,
                    r_ward_name or clean_ward_name,
                    r_part or clean_part_no,
                    r_pb or clean_pb,
                    r_moh or clean_moh,
                    r_serial_val,
                    r_name,
                    r_rel_type,
                    r_rel_name,
                    r_house,
                    r_age_val,
                    r_gender,
                    r_epic,
                    r_ps or clean_ps,
                    r_sec,
                    r_page,
                    source_file,
                    now_str,
                    now_str,
                    norm_m,
                    phon_m,
                    norm_r,
                    phon_r
                ))
                inserted += 1

            conn.commit()

        return {
            "success": True,
            "inserted": inserted,
            "body_type": clean_body_type,
            "body_name": clean_body_name,
            "ward_no": clean_ward_no,
            "total_in_panchayat_db": cls.get_total_count(db_id=db_id)
        }

    @classmethod
    def get_total_count(cls, db_id: Optional[str] = None) -> int:
        """Returns total records in panchayat_voters."""
        try:
            with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
                cls.ensure_table(conn)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM panchayat_voters WHERE is_deleted = 0 OR is_deleted IS NULL;")
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    @classmethod
    def get_voters(
        cls,
        search: Optional[str] = None,
        body_type: Optional[str] = None,
        body_name: Optional[str] = None,
        ward_no: Optional[str] = None,
        part_no: Optional[str] = None,
        gender: Optional[str] = None,
        sort_by: Optional[str] = "part_serial",
        sort_order: Optional[str] = "asc",
        page: int = 1,
        limit: int = 50,
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Returns paginated voters from `panchayat_voters` sorted and filtered by part_no and serial_no."""
        page_val = max(1, page)
        limit_val = max(1, min(limit, 500))
        offset = (page_val - 1) * limit_val

        where = ["(is_deleted = 0 OR is_deleted IS NULL)"]
        params: List[Any] = []

        if search and search.strip():
            s = search.strip()
            where.append("(name LIKE ? OR relation_name LIKE ? OR UPPER(epic_no) LIKE ? OR house_no LIKE ? OR part_no LIKE ?)")
            params.extend([f"%{s}%", f"%{s}%", f"%{s.upper()}%", f"%{s}%", f"%{s}%"])

        if body_type and body_type.strip() and body_type.lower() != "all":
            where.append("body_type = ?")
            params.append(body_type.strip())

        if body_name and body_name.strip() and body_name.lower() != "all":
            where.append("body_name = ?")
            params.append(body_name.strip())

        if ward_no and ward_no.strip() and ward_no.lower() != "all":
            where.append("ward_no = ?")
            params.append(ward_no.strip())

        if part_no and part_no.strip() and part_no.lower() != "all":
            where.append("part_no = ?")
            params.append(part_no.strip())

        if gender and gender.strip() and gender.lower() != "all":
            where.append("gender = ?")
            params.append(gender.strip())

        where_str = " AND ".join(where)

        # Dynamic sorting supporting part_no and serial_no
        order_dir = "DESC" if str(sort_order or "").lower() == "desc" else "ASC"
        sort_key = str(sort_by or "part_serial").lower()
        if sort_key == "part_serial":
            order_clause = f"CAST(part_no AS INTEGER) {order_dir}, CAST(serial_no AS INTEGER) {order_dir}, id ASC"
        elif sort_key == "serial_no":
            order_clause = f"CAST(serial_no AS INTEGER) {order_dir}, id ASC"
        elif sort_key == "name":
            order_clause = f"name_normalized {order_dir}, CAST(serial_no AS INTEGER) ASC"
        elif sort_key == "house_no":
            order_clause = f"CAST(house_no AS INTEGER) {order_dir}, house_no {order_dir}, CAST(serial_no AS INTEGER) ASC"
        elif sort_key == "age":
            order_clause = f"age {order_dir}, CAST(serial_no AS INTEGER) ASC"
        else:
            order_clause = f"body_name ASC, CAST(ward_no AS INTEGER) ASC, CAST(part_no AS INTEGER) {order_dir}, CAST(serial_no AS INTEGER) {order_dir}, id ASC"

        with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            # Total matching count
            cursor.execute(f"SELECT COUNT(*) FROM panchayat_voters WHERE {where_str};", params)
            total = cursor.fetchone()[0]

            # Fetch rows
            sql = f"""
                SELECT id, body_type, body_name, ward_no, ward_name, part_no, polling_booth, mohalla, serial_no,
                       name, relation_type, relation_name, house_no, age, gender, epic_no,
                       polling_station, page_no, source_file, created_at
                FROM panchayat_voters
                WHERE {where_str}
                ORDER BY {order_clause}
                LIMIT ? OFFSET ?;
            """
            cursor.execute(sql, params + [limit_val, offset])
            voters = [dict(r) for r in cursor.fetchall()]

        total_pages = (total + limit_val - 1) // limit_val if limit_val > 0 else 1

        return {
            "success": True,
            "total": total,
            "page": page_val,
            "limit": limit_val,
            "total_pages": total_pages,
            "voters": voters
        }

    @classmethod
    def get_stats(cls, db_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns demographic and geographical statistics for panchayat_voters including distinct parts."""
        with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM panchayat_voters WHERE is_deleted = 0 OR is_deleted IS NULL;")
            total_voters = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM panchayat_voters WHERE gender = 'पुरुष' AND (is_deleted = 0 OR is_deleted IS NULL);")
            male_voters = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM panchayat_voters WHERE gender = 'महिला' AND (is_deleted = 0 OR is_deleted IS NULL);")
            female_voters = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM panchayat_voters WHERE epic_no IS NOT NULL AND epic_no != '' AND (is_deleted = 0 OR is_deleted IS NULL);")
            with_epic = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT part_no) FROM panchayat_voters WHERE part_no IS NOT NULL AND part_no != '' AND (is_deleted = 0 OR is_deleted IS NULL);")
            total_parts = cursor.fetchone()[0]

            # Distinct bodies with counts
            cursor.execute("""
                SELECT body_name, body_type, COUNT(*) as voter_count
                FROM panchayat_voters
                WHERE is_deleted = 0 OR is_deleted IS NULL
                GROUP BY body_name, body_type
                ORDER BY voter_count DESC;
            """)
            bodies = [dict(r) for r in cursor.fetchall()]

            # Distinct wards with counts
            cursor.execute("""
                SELECT ward_no, COUNT(*) as voter_count
                FROM panchayat_voters
                WHERE (is_deleted = 0 OR is_deleted IS NULL) AND ward_no IS NOT NULL AND ward_no != ''
                GROUP BY ward_no
                ORDER BY CAST(ward_no AS INTEGER) ASC;
            """)
            wards = [dict(r) for r in cursor.fetchall()]

            # Distinct parts with counts
            cursor.execute("""
                SELECT part_no, COUNT(*) as voter_count
                FROM panchayat_voters
                WHERE (is_deleted = 0 OR is_deleted IS NULL) AND part_no IS NOT NULL AND part_no != ''
                GROUP BY part_no
                ORDER BY CAST(part_no AS INTEGER) ASC;
            """)
            parts = [dict(r) for r in cursor.fetchall()]
            distinct_parts = [str(p["part_no"]) for p in parts if p.get("part_no")]

            # Source files
            cursor.execute("""
                SELECT source_file, COUNT(*) as voter_count
                FROM panchayat_voters
                WHERE is_deleted = 0 OR is_deleted IS NULL
                GROUP BY source_file
                ORDER BY created_at DESC;
            """)
            source_files = [dict(r) for r in cursor.fetchall()]

        return {
            "success": True,
            "total_voters": total_voters,
            "male_voters": male_voters,
            "female_voters": female_voters,
            "total_parts": total_parts,
            "with_epic": with_epic,
            "missing_epic": total_voters - with_epic,
            "bodies": bodies,
            "wards": wards,
            "parts": parts,
            "distinct_parts": distinct_parts,
            "source_files": source_files
        }

    # =========================================================================
    # INTRA-LIST DUPLICATE DETECTION (खुद की लिस्ट में डुप्लीकेट छांटना)
    # =========================================================================

    @classmethod
    def find_internal_duplicates(
        cls,
        body_name: Optional[str] = None,
        ward_no: Optional[str] = None,
        part_no: Optional[str] = None,
        match_tier: Optional[str] = "all",
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        High-Precision Multi-Tier Local AI Intra-List Deduplication Engine.
        Identifies duplicate voters within the same part, ward, or entire body:
        Tier 1: 100% Exact EPIC Identification Card Duplicate.
        Tier 2: Same House (मकान सं०) + Local AI Name & Relation Fuzzy/Subset Matching.
                (With strict first-name integrity to prevent sibling false positives).
        Tier 3: Exact Normalized Name + Relative Match across houses/parts.
        Tier 4: Local AI Phonetic Soundex + Age Proximity (±3 yrs).
        Groups all connected voter records into cohesive clusters using Disjoint Set Union (DSU).
        """
        with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            where_clauses = ["(p.is_deleted = 0 OR p.is_deleted IS NULL)"]
            params: List[Any] = []

            if body_name and body_name.strip() and body_name.lower() != "all":
                where_clauses.append("p.body_name = ?")
                params.append(body_name.strip())

            if ward_no and ward_no.strip() and ward_no.lower() != "all":
                where_clauses.append("p.ward_no = ?")
                params.append(ward_no.strip())

            if part_no and part_no.strip() and part_no.lower() != "all":
                where_clauses.append("p.part_no = ?")
                params.append(part_no.strip())

            w_sql = " AND ".join(where_clauses)

            query = f"""
                SELECT p.id, p.body_type, p.body_name, p.ward_no, p.ward_name, p.part_no, p.polling_booth, p.mohalla, p.serial_no,
                       p.name, p.relation_type, p.relation_name, p.house_no, p.age, p.gender, p.epic_no,
                       p.polling_station, p.source_file, p.created_at,
                       p.name_normalized, p.rel_normalized, p.name_phonetic, p.rel_phonetic
                FROM panchayat_voters p
                WHERE {w_sql}
                ORDER BY CAST(p.part_no AS INTEGER) ASC, CAST(p.serial_no AS INTEGER) ASC, p.id ASC;
            """
            cursor.execute(query, params)
            voters = [dict(r) for r in cursor.fetchall()]

        if not voters or len(voters) < 2:
            return {
                "success": True,
                "total_clusters": 0,
                "total_duplicate_voters": 0,
                "epic_duplicates": 0,
                "house_ai_duplicates": 0,
                "name_rel_duplicates": 0,
                "ai_phonetic_duplicates": 0,
                "clusters": []
            }

        dsu = _DisjointSetUnion()
        edge_info: Dict[Tuple[int, int], Dict[str, Any]] = {}

        # -------------------------------------------------------------
        # Tier 1: Exact EPIC No Match (100% Certainty)
        # -------------------------------------------------------------
        epic_groups = defaultdict(list)
        for v in voters:
            ep = (v.get("epic_no") or "").strip().upper()
            if ep and len(ep) >= 5:
                epic_groups[ep].append(v)

        for ep, members in epic_groups.items():
            if len(members) > 1:
                for i in range(len(members) - 1):
                    dsu.union(members[i]["id"], members[i + 1]["id"])
                    edge_info[(members[i]["id"], members[i + 1]["id"])] = {
                        "tier": "epic",
                        "reason": f"🎯 समान पहचान पत्र (EPIC: {ep})",
                        "confidence": 100
                    }

        # -------------------------------------------------------------
        # Tier 2: Same House No + Local AI Name & Relation Matching (98% Certainty)
        # -------------------------------------------------------------
        house_groups = defaultdict(list)
        for v in voters:
            hn = _clean_hn(v.get("house_no"))
            if hn and hn not in ("0", "00", "-", "na"):
                b_name = v.get("body_name") or ""
                part_key = str(v.get("part_no") or v.get("ward_no") or "")
                house_groups[(b_name, part_key, hn)].append(v)

        for (b_name, p_key, hn), h_voters in house_groups.items():
            if len(h_voters) < 2:
                continue
            n = len(h_voters)
            for i in range(n):
                for j in range(i + 1, n):
                    v1, v2 = h_voters[i], h_voters[j]

                    # Gender match
                    g1 = (v1.get("gender") or "").strip()
                    g2 = (v2.get("gender") or "").strip()
                    if g1 and g2 and g1 != g2:
                        continue

                    # Age proximity (within ±4 years)
                    a1 = v1.get("age") or 0
                    a2 = v2.get("age") or 0
                    if a1 > 0 and a2 > 0 and abs(a1 - a2) > 4:
                        continue

                    nm_ok, nm_msg = _are_voter_names_matching(
                        v1["name"], v2["name"],
                        v1.get("name_normalized") or "", v2.get("name_normalized") or "",
                        v1.get("name_phonetic") or "", v2.get("name_phonetic") or ""
                    )
                    if not nm_ok:
                        continue

                    rl_ok, rl_msg = _are_rel_names_matching(
                        v1["relation_name"], v2["relation_name"],
                        v1.get("rel_normalized") or "", v2.get("rel_normalized") or "",
                        v1.get("rel_phonetic") or "", v2.get("rel_phonetic") or ""
                    )
                    if not rl_ok:
                        continue

                    dsu.union(v1["id"], v2["id"])
                    edge_info[(v1["id"], v2["id"])] = {
                        "tier": "house_ai",
                        "reason": f"🏠 समान मकान ({hn}) + {nm_msg} + {rl_msg}",
                        "confidence": 98
                    }

        # -------------------------------------------------------------
        # Tier 3: Exact Normalized Name + Relative Match Across Houses/Parts (95% Certainty)
        # -------------------------------------------------------------
        name_rel_groups = defaultdict(list)
        for v in voters:
            nm = v.get("name_normalized") or ""
            rl = v.get("rel_normalized") or ""
            if nm and rl and len(rl) >= 2:
                name_rel_groups[(v.get("body_name") or "", nm, rl)].append(v)

        for (b_name, nm, rl), nr_voters in name_rel_groups.items():
            if len(nr_voters) < 2:
                continue
            n = len(nr_voters)
            for i in range(n):
                for j in range(i + 1, n):
                    v1, v2 = nr_voters[i], nr_voters[j]
                    g1 = (v1.get("gender") or "").strip()
                    g2 = (v2.get("gender") or "").strip()
                    if g1 and g2 and g1 != g2:
                        continue
                    a1 = v1.get("age") or 0
                    a2 = v2.get("age") or 0
                    if a1 > 0 and a2 > 0 and abs(a1 - a2) > 4:
                        continue

                    fn_ok, _ = _are_first_names_matching(v1["name"], v2["name"], nm, nm, "", "")
                    if not fn_ok:
                        continue

                    dsu.union(v1["id"], v2["id"])
                    edge_info[(v1["id"], v2["id"])] = {
                        "tier": "name_rel",
                        "reason": f"✨ सटीक नाम व सम्बन्धी ({v1['name']} / {v1['relation_name']})",
                        "confidence": 95
                    }

        # -------------------------------------------------------------
        # Tier 4: Phonetic Soundex + Proximity Across Houses (88% Certainty)
        # -------------------------------------------------------------
        phon_groups = defaultdict(list)
        for v in voters:
            np = v.get("name_phonetic") or ""
            rp = v.get("rel_phonetic") or ""
            if np and rp and len(rp) >= 2:
                phon_groups[(v.get("body_name") or "", np, rp)].append(v)

        for (b_name, np, rp), p_voters in phon_groups.items():
            if len(p_voters) < 2:
                continue
            n = len(p_voters)
            for i in range(n):
                for j in range(i + 1, n):
                    v1, v2 = p_voters[i], p_voters[j]
                    g1 = (v1.get("gender") or "").strip()
                    g2 = (v2.get("gender") or "").strip()
                    if g1 and g2 and g1 != g2:
                        continue
                    a1 = v1.get("age") or 0
                    a2 = v2.get("age") or 0
                    if a1 > 0 and a2 > 0 and abs(a1 - a2) > 3:
                        continue

                    fn_ok, _ = _are_first_names_matching(
                        v1["name"], v2["name"],
                        v1.get("name_normalized") or "", v2.get("name_normalized") or "",
                        np, np
                    )
                    if not fn_ok:
                        continue

                    dsu.union(v1["id"], v2["id"])
                    edge_info[(v1["id"], v2["id"])] = {
                        "tier": "ai_phonetic",
                        "reason": f"🤖 AI ध्वन्यात्मक (Soundex) + आयु साम्य ({v1['name']})",
                        "confidence": 88
                    }

        # Assemble and sort clusters
        clusters_map = defaultdict(list)
        for v in voters:
            root = dsu.find(v["id"])
            clusters_map[root].append(v)

        raw_clusters = []
        epic_cnt = 0
        house_cnt = 0
        name_cnt = 0
        ai_cnt = 0

        tier_priority = {"epic": 4, "house_ai": 3, "name_rel": 2, "ai_phonetic": 1}

        for root, members in clusters_map.items():
            if len(members) > 1:
                # Sort members by part_no ASC, then serial_no ASC, then id ASC
                members.sort(key=lambda x: (
                    int(x["part_no"]) if str(x.get("part_no") or "").isdigit() else 0,
                    int(x["serial_no"]) if str(x.get("serial_no") or "").isdigit() else 0,
                    x.get("id") or 0
                ))

                best_tier = "ai_phonetic"
                best_reason = "डुप्लीकेट मतदाता"
                best_conf = 85
                max_p = 0

                for (id1, id2), info in edge_info.items():
                    if dsu.find(id1) == root:
                        p = tier_priority.get(info["tier"], 0)
                        if p > max_p:
                            max_p = p
                            best_tier = info["tier"]
                            best_reason = info["reason"]
                            best_conf = info["confidence"]

                if best_tier == "epic":
                    epic_cnt += 1
                    b_color, b_bg, b_icon = "#DC2626", "#FEE2E2", "🔴"
                elif best_tier == "house_ai":
                    house_cnt += 1
                    b_color, b_bg, b_icon = "#059669", "#D1FAE5", "🏠"
                elif best_tier == "name_rel":
                    name_cnt += 1
                    b_color, b_bg, b_icon = "#EA580C", "#FFEDD5", "🟠"
                else:
                    ai_cnt += 1
                    b_color, b_bg, b_icon = "#D97706", "#FEF3C7", "🟡"

                raw_clusters.append({
                    "cluster_id": f"cluster_{root}",
                    "match_tier": best_tier,
                    "match_type": best_reason,
                    "match_label": best_reason,
                    "confidence": best_conf,
                    "badge_color": b_color,
                    "badge_bg": b_bg,
                    "badge_icon": b_icon,
                    "match_criterion": f"{members[0]['name']} (सम्बन्धी: {members[0]['relation_name']})",
                    "match_key": f"{members[0]['name']} (भाग {members[0].get('part_no') or '-'} | मकान {members[0].get('house_no') or '-'})",
                    "voters_count": len(members),
                    "voters": members
                })

        raw_clusters.sort(key=lambda c: (-c["voters_count"], int(c["voters"][0].get("part_no") or 0) if str(c["voters"][0].get("part_no") or "").isdigit() else 0, int(c["voters"][0].get("serial_no") or 0) if str(c["voters"][0].get("serial_no") or "").isdigit() else 0))

        filtered_clusters = raw_clusters
        if match_tier and match_tier != "all":
            filtered_clusters = [c for c in raw_clusters if c["match_tier"] == match_tier]

        total_dups = sum(c["voters_count"] for c in filtered_clusters)

        return {
            "success": True,
            "duplicate_clusters_count": len(filtered_clusters),
            "total_clusters": len(filtered_clusters),
            "total_duplicate_voters": total_dups,
            "epic_duplicates": epic_cnt,
            "house_ai_duplicates": house_cnt,
            "name_rel_duplicates": name_cnt,
            "ai_phonetic_duplicates": ai_cnt,
            "clusters": filtered_clusters
        }

    # =========================================================================
    # CROSS-BODY COMPARISON (नगर पंचायत मास्टर डेटा से तुलना व दोहरी मतदाता पहचान)
    # =========================================================================

    @classmethod
    def compare_with_nagar_panchayat(
        cls,
        body_name: Optional[str] = None,
        ward_no: Optional[str] = None,
        rule_filter: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cross-compares `panchayat_voters` against the Nagar Panchayat Master database (`voters` table).
        Identifies Dual/Duplicate Voters (दोहरी मतदाता) across 3 tiers:
        1. 🎯 सटीक EPIC No मिलान (Exact EPIC ID Match)
        2. ✨ सटीक नाम + पिता/पति का नाम मिलान (Exact Name + Relative)
        3. 🤖 लोकल AI ध्वन्यात्मक (Soundex) + आयु समानता (±4 वर्ष)
        """
        with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            # Check counts
            cursor.execute("SELECT COUNT(*) FROM panchayat_voters WHERE is_deleted = 0 OR is_deleted IS NULL;")
            total_panchayat = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM voters WHERE is_deleted = 0 OR is_deleted IS NULL;")
            total_nagar = cursor.fetchone()[0]

            if total_panchayat == 0 or total_nagar == 0:
                return {
                    "success": True,
                    "total_panchayat_voters": total_panchayat,
                    "total_nagar_voters": total_nagar,
                    "total_dual_voters": 0,
                    "dual_percentage": 0.0,
                    "matches": [],
                    "page": page,
                    "limit": limit,
                    "total_pages": 1,
                    "message": "पंचायत अथवा नगर पंचायत डेटाबेस रिक्त है।"
                }

            p_where = ["(p.is_deleted = 0 OR p.is_deleted IS NULL)"]
            p_params: List[Any] = []
            if body_name and body_name.strip() and body_name.lower() != "all":
                p_where.append("p.body_name = ?")
                p_params.append(body_name.strip())
            if ward_no and ward_no.strip() and ward_no.lower() != "all":
                p_where.append("p.ward_no = ?")
                p_params.append(ward_no.strip())
            p_where_str = " AND ".join(p_where)

            matched_panchayat_ids = set()
            all_matches: List[Dict[str, Any]] = []

            # -------------------------------------------------------------
            # PASS 1: Exact EPIC Match
            # -------------------------------------------------------------
            sql_epic = f"""
                SELECT
                    p.id as p_id, p.body_type as p_body_type, p.body_name as p_body_name,
                    p.ward_no as p_ward_no, p.serial_no as p_serial_no, p.name as p_name,
                    p.relation_type as p_rel_type, p.relation_name as p_rel_name,
                    p.house_no as p_house_no, p.age as p_age, p.gender as p_gender,
                    p.epic_no as p_epic_no, p.source_file as p_source_file,
                    v.id as v_id, v.part_no as v_part_no, v.serial_no as v_serial_no,
                    v.name as v_name, v.relation_type as v_rel_type, v.relation_name as v_rel_name,
                    v.house_no as v_house_no, v.age as v_age, v.gender as v_gender,
                    v.epic_no as v_epic_no, v.polling_station as v_ps
                FROM panchayat_voters p
                JOIN voters v ON UPPER(p.epic_no) = UPPER(v.epic_no)
                WHERE {p_where_str}
                  AND (v.is_deleted = 0 OR v.is_deleted IS NULL)
                  AND p.epic_no IS NOT NULL AND TRIM(p.epic_no) != ''
                  AND LENGTH(TRIM(p.epic_no)) >= 5
                ORDER BY p.body_name, CAST(p.ward_no AS INTEGER), CAST(p.serial_no AS INTEGER);
            """
            cursor.execute(sql_epic, p_params)
            for r in cursor.fetchall():
                row = dict(r)
                p_id = row["p_id"]
                if p_id in matched_panchayat_ids:
                    continue
                matched_panchayat_ids.add(p_id)
                all_matches.append({
                    "panchayat": {
                        "id": row["p_id"],
                        "body_type": row["p_body_type"],
                        "body_name": row["p_body_name"],
                        "ward_no": row["p_ward_no"],
                        "serial_no": row["p_serial_no"],
                        "name": row["p_name"],
                        "relation_type": row["p_rel_type"],
                        "relation_name": row["p_rel_name"],
                        "house_no": row["p_house_no"],
                        "age": row["p_age"],
                        "gender": row["p_gender"],
                        "epic_no": row["p_epic_no"],
                        "source_file": row["p_source_file"]
                    },
                    "nagar_panchayat": {
                        "id": row["v_id"],
                        "part_no": row["v_part_no"],
                        "serial_no": row["v_serial_no"],
                        "name": row["v_name"],
                        "relation_type": row["v_rel_type"],
                        "relation_name": row["v_rel_name"],
                        "house_no": row["v_house_no"],
                        "age": row["v_age"],
                        "gender": row["v_gender"],
                        "epic_no": row["v_epic_no"],
                        "polling_station": row["v_ps"]
                    },
                    "match_type": "🎯 सटीक EPIC मिलान (Exact EPIC Match)",
                    "match_tag_short": "🎯 सटीक EPIC",
                    "badge_color": "#065F46",
                    "badge_bg": "#D1FAE5",
                    "match_score": 100,
                    "confidence": "सटीक (100%)"
                })

            # -------------------------------------------------------------
            # PASS 2: Exact Normalized Name + Relative Match
            # -------------------------------------------------------------
            sql_name = f"""
                SELECT
                    p.id as p_id, p.body_type as p_body_type, p.body_name as p_body_name,
                    p.ward_no as p_ward_no, p.serial_no as p_serial_no, p.name as p_name,
                    p.relation_type as p_rel_type, p.relation_name as p_rel_name,
                    p.house_no as p_house_no, p.age as p_age, p.gender as p_gender,
                    p.epic_no as p_epic_no, p.source_file as p_source_file,
                    v.id as v_id, v.part_no as v_part_no, v.serial_no as v_serial_no,
                    v.name as v_name, v.relation_type as v_rel_type, v.relation_name as v_rel_name,
                    v.house_no as v_house_no, v.age as v_age, v.gender as v_gender,
                    v.epic_no as v_epic_no, v.polling_station as v_ps
                FROM panchayat_voters p
                JOIN voters v ON p.name_normalized = v.name_normalized
                             AND p.rel_normalized = v.rel_normalized
                WHERE {p_where_str}
                  AND (v.is_deleted = 0 OR v.is_deleted IS NULL)
                  AND p.name_normalized IS NOT NULL AND p.rel_normalized IS NOT NULL
                  AND LENGTH(TRIM(p.rel_normalized)) >= 3
                ORDER BY p.body_name, CAST(p.ward_no AS INTEGER), CAST(p.serial_no AS INTEGER);
            """
            cursor.execute(sql_name, p_params)
            for r in cursor.fetchall():
                row = dict(r)
                p_id = row["p_id"]
                if p_id in matched_panchayat_ids:
                    continue
                matched_panchayat_ids.add(p_id)
                all_matches.append({
                    "panchayat": {
                        "id": row["p_id"],
                        "body_type": row["p_body_type"],
                        "body_name": row["p_body_name"],
                        "ward_no": row["p_ward_no"],
                        "serial_no": row["p_serial_no"],
                        "name": row["p_name"],
                        "relation_type": row["p_rel_type"],
                        "relation_name": row["p_rel_name"],
                        "house_no": row["p_house_no"],
                        "age": row["p_age"],
                        "gender": row["p_gender"],
                        "epic_no": row["p_epic_no"],
                        "source_file": row["p_source_file"]
                    },
                    "nagar_panchayat": {
                        "id": row["v_id"],
                        "part_no": row["v_part_no"],
                        "serial_no": row["v_serial_no"],
                        "name": row["v_name"],
                        "relation_type": row["v_rel_type"],
                        "relation_name": row["v_rel_name"],
                        "house_no": row["v_house_no"],
                        "age": row["v_age"],
                        "gender": row["v_gender"],
                        "epic_no": row["v_epic_no"],
                        "polling_station": row["v_ps"]
                    },
                    "match_type": "✨ सटीक नाम व सम्बन्धी मिलान (Exact Name + Relation)",
                    "match_tag_short": "✨ सटीक नाम व सम्बन्धी",
                    "badge_color": "#1E40AF",
                    "badge_bg": "#DBEAFE",
                    "match_score": 95,
                    "confidence": "अति उच्च (95%)"
                })

            # -------------------------------------------------------------
            # PASS 3: Local AI Phonetic (Soundex) + Age Proximity (±4 years)
            # -------------------------------------------------------------
            sql_phon = f"""
                SELECT
                    p.id as p_id, p.body_type as p_body_type, p.body_name as p_body_name,
                    p.ward_no as p_ward_no, p.serial_no as p_serial_no, p.name as p_name,
                    p.relation_type as p_rel_type, p.relation_name as p_rel_name,
                    p.house_no as p_house_no, p.age as p_age, p.gender as p_gender,
                    p.epic_no as p_epic_no, p.source_file as p_source_file,
                    v.id as v_id, v.part_no as v_part_no, v.serial_no as v_serial_no,
                    v.name as v_name, v.relation_type as v_rel_type, v.relation_name as v_rel_name,
                    v.house_no as v_house_no, v.age as v_age, v.gender as v_gender,
                    v.epic_no as v_epic_no, v.polling_station as v_ps
                FROM panchayat_voters p
                JOIN voters v ON p.name_phonetic = v.name_phonetic
                             AND p.rel_phonetic = v.rel_phonetic
                WHERE {p_where_str}
                  AND (v.is_deleted = 0 OR v.is_deleted IS NULL)
                  AND p.name_phonetic IS NOT NULL AND p.rel_phonetic IS NOT NULL
                  AND LENGTH(TRIM(p.rel_phonetic)) >= 3
                  AND p.age > 0 AND v.age > 0
                  AND ABS(p.age - v.age) <= 4
                ORDER BY p.body_name, CAST(p.ward_no AS INTEGER), CAST(p.serial_no AS INTEGER);
            """
            cursor.execute(sql_phon, p_params)
            for r in cursor.fetchall():
                row = dict(r)
                p_id = row["p_id"]
                if p_id in matched_panchayat_ids:
                    continue
                matched_panchayat_ids.add(p_id)
                all_matches.append({
                    "panchayat": {
                        "id": row["p_id"],
                        "body_type": row["p_body_type"],
                        "body_name": row["p_body_name"],
                        "ward_no": row["p_ward_no"],
                        "serial_no": row["p_serial_no"],
                        "name": row["p_name"],
                        "relation_type": row["p_rel_type"],
                        "relation_name": row["p_rel_name"],
                        "house_no": row["p_house_no"],
                        "age": row["p_age"],
                        "gender": row["p_gender"],
                        "epic_no": row["p_epic_no"],
                        "source_file": row["p_source_file"]
                    },
                    "nagar_panchayat": {
                        "id": row["v_id"],
                        "part_no": row["v_part_no"],
                        "serial_no": row["v_serial_no"],
                        "name": row["v_name"],
                        "relation_type": row["v_rel_type"],
                        "relation_name": row["v_rel_name"],
                        "house_no": row["v_house_no"],
                        "age": row["v_age"],
                        "gender": row["v_gender"],
                        "epic_no": row["v_epic_no"],
                        "polling_station": row["v_ps"]
                    },
                    "match_type": "🤖 लोकल AI ध्वन्यात्मक (Soundex) + आयु समानता (±4 वर्ष)",
                    "match_tag_short": "🤖 AI Soundex + आयु",
                    "badge_color": "#9A3412",
                    "badge_bg": "#FFEDD5",
                    "match_score": 85,
                    "confidence": "AI संभावित (85%)"
                })

        # Apply rule filter if specified
        if rule_filter and rule_filter != "all":
            if rule_filter == "epic":
                filtered_matches = [m for m in all_matches if "EPIC" in m["match_type"]]
            elif rule_filter == "name":
                filtered_matches = [m for m in all_matches if "सटीक नाम" in m["match_type"]]
            elif rule_filter == "ai_soundex":
                filtered_matches = [m for m in all_matches if "Soundex" in m["match_type"]]
            else:
                filtered_matches = all_matches
        else:
            filtered_matches = all_matches

        total_dual = len(filtered_matches)
        dual_pct = round((total_dual / total_panchayat * 100), 1) if total_panchayat > 0 else 0.0

        # Pagination
        page_val = max(1, page)
        limit_val = max(1, min(limit, 200))
        start_idx = (page_val - 1) * limit_val
        end_idx = start_idx + limit_val
        paginated_matches = filtered_matches[start_idx:end_idx]
        total_pages = (total_dual + limit_val - 1) // limit_val if limit_val > 0 else 1

        epic_cnt = sum(1 for m in all_matches if "EPIC" in m["match_type"])
        name_cnt = sum(1 for m in all_matches if "सटीक नाम" in m["match_type"])
        ai_cnt = sum(1 for m in all_matches if "Soundex" in m["match_type"])

        return {
            "success": True,
            "total_panchayat_voters": total_panchayat,
            "total_nagar_voters": total_nagar,
            "total_dual_voters": total_dual,
            "dual_percentage": dual_pct,
            "epic_matches_count": epic_cnt,
            "name_matches_count": name_cnt,
            "ai_matches_count": ai_cnt,
            "page": page_val,
            "limit": limit_val,
            "total_pages": total_pages,
            "matches": paginated_matches
        }

    # =========================================================================
    # EXCEL EXPORT GENERATORS
    # =========================================================================

    @classmethod
    def generate_dual_voters_excel(
        cls,
        output_path: str,
        body_name: Optional[str] = None,
        ward_no: Optional[str] = None,
        db_id: Optional[str] = None
    ) -> str:
        """
        Generates a professionally styled Excel report comparing Panchayat vs Nagar Panchayat Dual Voters.
        Side-by-side presentation of matched voter credentials.
        """
        res = cls.compare_with_nagar_panchayat(body_name=body_name, ward_no=ward_no, page=1, limit=50000, db_id=db_id)
        matches = res.get("matches", [])

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "दोहरा मतदाता तुलना"
        ws.views.sheetView[0].showGridLines = True

        # Styles
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        panchayat_hdr_fill = PatternFill(start_color="065F46", end_color="065F46", fill_type="solid")
        nagar_hdr_fill = PatternFill(start_color="1D4ED8", end_color="1D4ED8", fill_type="solid")
        summary_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
        zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        epic_tag_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
        name_tag_fill = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")
        ai_tag_fill = PatternFill(start_color="FFEDD5", end_color="FFEDD5", fill_type="solid")

        white_font_bold = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        title_font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        sub_font = Font(name="Calibri", size=9, italic=True, color="E2E8F0")
        cell_font = Font(name="Calibri", size=10)
        bold_cell_font = Font(name="Calibri", size=10, bold=True)
        center_align = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")
        thin_border = Border(
            left=Side(style='thin', color="CBD5E1"),
            right=Side(style='thin', color="CBD5E1"),
            top=Side(style='thin', color="CBD5E1"),
            bottom=Side(style='thin', color="CBD5E1")
        )

        # Title Banner (Row 1-2)
        ws.merge_cells("A1:Q1")
        top_cell = ws["A1"]
        top_cell.value = "⚖️ पंचायत एवं नगर पंचायत दोहरी मतदाता मिलान रिपोर्ट (Dual Voters Comparison Register)"
        top_cell.font = title_font
        top_cell.fill = header_fill
        top_cell.alignment = center_align
        ws.row_dimensions[1].height = 36

        ws.merge_cells("A2:Q2")
        meta_cell = ws["A2"]
        date_str = datetime.now().strftime("%d-%m-%Y %H:%M")
        meta_cell.value = f"विश्लेषण तिथि: {date_str} | कुल पंचायत मतदाता: {res.get('total_panchayat_voters', 0)} | दोहरी मतदाता: {len(matches)} ({res.get('dual_percentage', 0)}%) | ECI व राज्य निर्वाचन आयोग डेटा संरेखण"
        meta_cell.font = sub_font
        meta_cell.fill = header_fill
        meta_cell.alignment = center_align
        ws.row_dimensions[2].height = 20

        # Sub-header Grouping Banner (Row 3)
        ws["A3"].value = ""
        ws["A3"].fill = header_fill

        ws.merge_cells("B3:I3")
        ws["B3"].value = "🏛️ ग्राम / नगर पंचायत रिकॉर्ड (Panchayat Electoral Roll)"
        ws["B3"].fill = panchayat_hdr_fill
        ws["B3"].font = white_font_bold
        ws["B3"].alignment = center_align

        ws.merge_cells("J3:P3")
        ws["J3"].value = "🏢 नगर पंचायत मास्टर रिकॉर्ड (Nagar Panchayat ECI Master Database)"
        ws["J3"].fill = nagar_hdr_fill
        ws["J3"].font = white_font_bold
        ws["J3"].alignment = center_align

        ws["Q3"].value = ""
        ws["Q3"].fill = header_fill
        ws.row_dimensions[3].height = 26

        # Detailed Column Headers (Row 4)
        col_headers = [
            ("A", "क्र०"),
            ("B", "पंचायत / निकाय"),
            ("C", "वार्ड नं०"),
            ("D", "क्र० #"),
            ("E", "मतदाता का नाम"),
            ("F", "पिता / पति का नाम"),
            ("G", "आयु/लिंग"),
            ("H", "मकान नं०"),
            ("I", "पहचान पत्र (EPIC)"),
            ("J", "भाग नं०"),
            ("K", "क्र० #"),
            ("L", "मतदाता का नाम"),
            ("M", "संबंधी का नाम"),
            ("N", "आयु/लिंग"),
            ("O", "मकान नं०"),
            ("P", "पहचान पत्र (EPIC)"),
            ("Q", "मिलान प्रकार")
        ]

        ws.row_dimensions[4].height = 24
        for col_letter, header_txt in col_headers:
            cell = ws[f"{col_letter}4"]
            cell.value = header_txt
            cell.font = white_font_bold
            cell.alignment = center_align
            if col_letter in ("B", "C", "D", "E", "F", "G", "H", "I"):
                cell.fill = panchayat_hdr_fill
            elif col_letter in ("J", "K", "L", "M", "N", "O", "P"):
                cell.fill = nagar_hdr_fill
            else:
                cell.fill = header_fill

        # Data Rows
        row_idx = 5
        for idx, m in enumerate(matches, start=1):
            p = m.get("panchayat", {})
            v = m.get("nagar_panchayat", {})
            is_zebra = (idx % 2 == 0)

            ws.row_dimensions[row_idx].height = 22

            row_data = [
                (idx, center_align, bold_cell_font),
                (p.get("body_name") or "-", left_align, cell_font),
                (p.get("ward_no") or "-", center_align, cell_font),
                (p.get("serial_no") or "-", center_align, bold_cell_font),
                (p.get("name") or "", left_align, bold_cell_font),
                (f"({p.get('relation_type') or 'पिता'}) {p.get('relation_name') or '-'}", left_align, cell_font),
                (f"{p.get('age') or '-'} ({p.get('gender') or '-'})", center_align, cell_font),
                (p.get("house_no") or "-", center_align, cell_font),
                (p.get("epic_no") or "N/A", center_align, bold_cell_font),
                (f"भाग {v.get('part_no') or '-'}", center_align, cell_font),
                (f"#{v.get('serial_no') or '-'}", center_align, bold_cell_font),
                (v.get("name") or "", left_align, bold_cell_font),
                (f"({v.get('relation_type') or 'पिता'}) {v.get('relation_name') or '-'}", left_align, cell_font),
                (f"{v.get('age') or '-'} ({v.get('gender') or '-'})", center_align, cell_font),
                (v.get("house_no") or "-", center_align, cell_font),
                (v.get("epic_no") or "N/A", center_align, bold_cell_font),
                (m.get("match_tag_short") or m.get("match_type") or "-", center_align, bold_cell_font)
            ]

            for c_idx, (val, align, font_style) in enumerate(row_data, start=1):
                col_chr = get_column_letter(c_idx)
                c = ws[f"{col_chr}{row_idx}"]
                c.value = val
                c.alignment = align
                c.font = font_style
                c.border = thin_border
                if c_idx == 17:
                    # Tag Fill
                    if "EPIC" in str(val):
                        c.fill = epic_tag_fill
                    elif "सटीक" in str(val):
                        c.fill = name_tag_fill
                    else:
                        c.fill = ai_tag_fill
                elif is_zebra:
                    c.fill = zebra_fill

            row_idx += 1

        # Auto-fit columns
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                if cell.row in (1, 2, 3):
                    continue
                max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 11)

        ws.column_dimensions["A"].width = 6
        ws.column_dimensions["B"].width = 18
        ws.column_dimensions["E"].width = 20
        ws.column_dimensions["F"].width = 22
        ws.column_dimensions["L"].width = 20
        ws.column_dimensions["M"].width = 22
        ws.column_dimensions["Q"].width = 22

        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        wb.save(output_path)
        return output_path

    @classmethod
    def delete_voter(cls, voter_id: int, db_id: Optional[str] = None) -> bool:
        """Deletes a specific voter from panchayat_voters."""
        try:
            with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
                cls.ensure_table(conn)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM panchayat_voters WHERE id = ?;", (voter_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception:
            return False

    @classmethod
    def delete_body(cls, body_name: str, db_id: Optional[str] = None) -> int:
        """Deletes all voters belonging to a specific body name."""
        try:
            with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
                cls.ensure_table(conn)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM panchayat_voters WHERE body_name = ?;", (body_name,))
                conn.commit()
                return cursor.rowcount
        except Exception:
            return 0

    @classmethod
    def delete_by_source_file(cls, source_file: str, db_id: Optional[str] = None) -> int:
        """Deletes all voters belonging to a specific source file."""
        try:
            with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
                cls.ensure_table(conn)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM panchayat_voters WHERE source_file = ?;", (source_file,))
                conn.commit()
                return cursor.rowcount
        except Exception:
            return 0

    @classmethod
    def delete_all_voters(cls, db_id: Optional[str] = None) -> int:
        """Deletes all records from panchayat_voters table."""
        try:
            with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
                cls.ensure_table(conn)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM panchayat_voters;")
                conn.commit()
                return cursor.rowcount
        except Exception:
            return 0

    @classmethod
    def delete_filtered_voters(
        cls,
        search: Optional[str] = None,
        body_type: Optional[str] = None,
        body_name: Optional[str] = None,
        ward_no: Optional[str] = None,
        part_no: Optional[str] = None,
        gender: Optional[str] = None,
        db_id: Optional[str] = None
    ) -> int:
        """Deletes all voters in panchayat_voters matching the specified filter criteria."""
        where = ["(is_deleted = 0 OR is_deleted IS NULL)"]
        params: List[Any] = []

        if search and search.strip():
            s = search.strip()
            where.append("(name LIKE ? OR relation_name LIKE ? OR UPPER(epic_no) LIKE ? OR house_no LIKE ? OR part_no LIKE ?)")
            params.extend([f"%{s}%", f"%{s}%", f"%{s.upper()}%", f"%{s}%", f"%{s}%"])

        if body_type and body_type.strip() and body_type.lower() != "all":
            where.append("body_type = ?")
            params.append(body_type.strip())

        if body_name and body_name.strip() and body_name.lower() != "all":
            where.append("body_name = ?")
            params.append(body_name.strip())

        if ward_no and ward_no.strip() and ward_no.lower() != "all":
            where.append("ward_no = ?")
            params.append(ward_no.strip())

        if part_no and part_no.strip() and part_no.lower() != "all":
            where.append("part_no = ?")
            params.append(part_no.strip())

        if gender and gender.strip() and gender.lower() != "all":
            where.append("gender = ?")
            params.append(gender.strip())

        where_str = " AND ".join(where)

        try:
            with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
                cls.ensure_table(conn)
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM panchayat_voters WHERE {where_str};", params)
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            print(f"[ERROR] delete_filtered_voters failed: {e}")
            return 0

    @classmethod
    def search_voters(
        cls,
        query: Optional[str] = None,
        name: Optional[str] = None,
        relation_name: Optional[str] = None,
        epic_no: Optional[str] = None,
        house_no: Optional[str] = None,
        body_name: Optional[str] = None,
        ward_no: Optional[str] = None,
        part_no: Optional[str] = None,
        mohalla: Optional[str] = None,
        polling_station: Optional[str] = None,
        gender: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        page: int = 1,
        limit: int = 30,
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fast online public search across Panchayat / Nikay voters table.
        Supports universal query string and multi-field advanced filters.
        """
        page_val = max(1, page)
        limit_val = max(1, min(limit, 200))
        offset = (page_val - 1) * limit_val

        where = ["(is_deleted = 0 OR is_deleted IS NULL)"]
        params: List[Any] = []

        if query and query.strip():
            q = query.strip()
            where.append("""(
                name LIKE ? OR relation_name LIKE ? OR UPPER(epic_no) LIKE ? OR house_no LIKE ?
                OR ward_name LIKE ? OR polling_station LIKE ? OR polling_booth LIKE ? OR mohalla LIKE ?
            )""")
            like_q = f"%{q}%"
            params.extend([like_q, like_q, f"%{q.upper()}%", like_q, like_q, like_q, like_q, like_q])

        if name and name.strip():
            where.append("name LIKE ?")
            params.append(f"%{name.strip()}%")

        if relation_name and relation_name.strip():
            where.append("relation_name LIKE ?")
            params.append(f"%{relation_name.strip()}%")

        if epic_no and epic_no.strip():
            where.append("UPPER(epic_no) LIKE ?")
            params.append(f"%{epic_no.strip().upper()}%")

        if house_no and house_no.strip():
            where.append("house_no LIKE ?")
            params.append(f"%{house_no.strip()}%")

        if body_name and body_name.strip() and body_name.lower() != "all":
            where.append("body_name = ?")
            params.append(body_name.strip())

        if ward_no and ward_no.strip() and ward_no.lower() != "all":
            where.append("ward_no = ?")
            params.append(ward_no.strip())

        if part_no and part_no.strip() and part_no.lower() != "all":
            where.append("part_no = ?")
            params.append(part_no.strip())

        if mohalla and mohalla.strip():
            where.append("mohalla LIKE ?")
            params.append(f"%{mohalla.strip()}%")

        if polling_station and polling_station.strip() and polling_station.lower() != "all":
            where.append("polling_station LIKE ?")
            params.append(f"%{polling_station.strip()}%")

        if gender and gender.strip() and gender.lower() != "all":
            clean_g = "पुरुष" if "पु" in gender or gender == "M" else ("महिला" if "म" in gender or gender == "F" else gender)
            where.append("(gender = ? OR gender = ?)")
            params.extend([clean_g, "M" if clean_g == "पुरुष" else "F"])

        if min_age is not None:
            where.append("age >= ?")
            params.append(int(min_age))

        if max_age is not None:
            where.append("age <= ?")
            params.append(int(max_age))

        where_str = " AND ".join(where)

        with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            cursor.execute(f"SELECT COUNT(*) FROM panchayat_voters WHERE {where_str};", params)
            total = cursor.fetchone()[0]

            sql = f"""
                SELECT id, body_type, body_name, ward_no, ward_name, part_no, polling_booth, mohalla, serial_no,
                       name, relation_type, relation_name, house_no, age, gender, epic_no,
                       polling_station, source_file, created_at
                FROM panchayat_voters
                WHERE {where_str}
                ORDER BY body_name ASC, CAST(ward_no AS INTEGER) ASC, CAST(part_no AS INTEGER) ASC, CAST(serial_no AS INTEGER) ASC, id ASC
                LIMIT ? OFFSET ?;
            """
            cursor.execute(sql, params + [limit_val, offset])
            voters = [dict(r) for r in cursor.fetchall()]

        total_pages = (total + limit_val - 1) // limit_val if limit_val > 0 else 1

        return {
            "success": True,
            "total": total,
            "page": page_val,
            "limit": limit_val,
            "total_pages": total_pages,
            "records": voters,
            "voters": voters
        }

    @classmethod
    def get_search_filter_options(cls, db_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns distinct filter options for the Panchayat online search portal."""
        with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            cursor.execute("SELECT DISTINCT body_name FROM panchayat_voters WHERE (is_deleted = 0 OR is_deleted IS NULL) AND body_name IS NOT NULL AND body_name != '' ORDER BY body_name ASC;")
            bodies = [r[0] for r in cursor.fetchall()]

            cursor.execute("SELECT DISTINCT ward_no, ward_name, body_name FROM panchayat_voters WHERE (is_deleted = 0 OR is_deleted IS NULL) AND ward_no IS NOT NULL AND ward_no != '' ORDER BY CAST(ward_no AS INTEGER) ASC;")
            wards = [{"ward_no": r[0], "ward_name": r[1] or "", "body_name": r[2] or ""} for r in cursor.fetchall()]

            cursor.execute("SELECT DISTINCT part_no FROM panchayat_voters WHERE (is_deleted = 0 OR is_deleted IS NULL) AND part_no IS NOT NULL AND part_no != '' ORDER BY CAST(part_no AS INTEGER) ASC;")
            parts = [r[0] for r in cursor.fetchall()]

            cursor.execute("SELECT DISTINCT polling_station FROM panchayat_voters WHERE (is_deleted = 0 OR is_deleted IS NULL) AND polling_station IS NOT NULL AND polling_station != '' ORDER BY polling_station ASC;")
            stations = [r[0] for r in cursor.fetchall()]

            cursor.execute("SELECT COUNT(*) FROM panchayat_voters WHERE is_deleted = 0 OR is_deleted IS NULL;")
            total_voters = cursor.fetchone()[0]

            return {
                "success": True,
                "total_voters": total_voters,
                "bodies": bodies,
                "wards": wards,
                "parts": parts,
                "polling_stations": stations
            }

    @classmethod
    def generate_search_excel(
        cls,
        output_path: str,
        query: Optional[str] = None,
        body_name: Optional[str] = None,
        ward_no: Optional[str] = None,
        part_no: Optional[str] = None,
        db_id: Optional[str] = None
    ) -> str:
        """Exports matching panchayat voters to styled Excel file."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        search_res = cls.search_voters(
            query=query,
            body_name=body_name,
            ward_no=ward_no,
            part_no=part_no,
            page=1,
            limit=5000,
            db_id=db_id
        )
        voters = search_res.get("voters", [])

        wb = Workbook()
        ws = wb.active
        ws.title = "पंचायत मतदाता सूची"
        ws.views.sheetView[0].showGridLines = True

        title_font = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
        title_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="3B82F6", end_color="3B82F6", fill_type="solid")
        data_font = Font(name="Calibri", size=10)
        zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        thin_side = Side(style="thin", color="CBD5E1")
        border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        ws.merge_cells("A1:M1")
        t_cell = ws["A1"]
        t_cell.value = f"पंचायत एवं नगरीय निकाय मतदाता खोज रिपोर्ट (कुल {len(voters)} मतदाता)"
        t_cell.font = title_font
        t_cell.fill = title_fill
        t_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 36

        headers = [
            "क्र०सं०", "निकाय / पंचायत", "वार्ड सं०", "वार्ड का नाम", "भाग संख्या", "सीरियल",
            "मतदाता का नाम", "संबंध", "संबंधी का नाम", "मकान सं०", "आयु / लिंग",
            "मतदान केंद्र व स्थल", "संबद्ध मोहल्ला"
        ]

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=2, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border
        ws.row_dimensions[2].height = 24

        for row_idx, v in enumerate(voters, 3):
            is_zebra = (row_idx % 2 == 0)
            g_str = f"{v.get('age') or '-'} / {v.get('gender') or '-'}"
            loc_str = v.get("polling_booth") or v.get("polling_station") or ""
            row_data = [
                row_idx - 2,
                v.get("body_name") or "",
                v.get("ward_no") or "",
                v.get("ward_name") or "",
                v.get("part_no") or "",
                v.get("serial_no") or "",
                v.get("name") or "",
                v.get("relation_type") or "पिता",
                v.get("relation_name") or "",
                v.get("house_no") or "",
                g_str,
                loc_str,
                v.get("mohalla") or ""
            ]
            for c_idx, val in enumerate(row_data, 1):
                c = ws.cell(row=row_idx, column=c_idx, value=val)
                c.font = data_font
                c.border = border
                if is_zebra:
                    c.fill = zebra_fill
                if c_idx in (1, 3, 5, 6, 11):
                    c.alignment = Alignment(horizontal="center")

        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col if cell.row > 1)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 11)

        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        wb.save(output_path)
        return output_path

    @classmethod
    def enhance_with_local_ai(
        cls,
        body_name: Optional[str] = None,
        uploads_dir: str = "uploads",
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Deep Local AI cleaning and enhancement across `panchayat_voters`.
        1. If original uploaded PDF files exist on disk in `uploads_dir`, re-processes them with
           ULBExtractor's Devanagari font decoder and Local AI correction, replacing corrupted records
           with 100% accurate Hindi text, clean house numbers, and correct gender/relations.
        2. If PDF is not found, applies ULB font decoding, compound name splitting, house sanitization,
           and DualPassErrorCorrector directly to all existing records in database.
        3. Recalculates Devanagari normalized forms and Soundex phonetic keys for accurate deduplication.
        """
        from .ulb_extractor import ULBExtractor
        from .error_corrector import DualPassErrorCorrector, LocalScanQualityAI
        from .ai_search import normalize_devanagari, get_phonetic_key

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_enhanced = 0
        sources_processed = []

        with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            query = """
                SELECT DISTINCT source_file, body_type, body_name, ward_no, ward_name
                FROM panchayat_voters
                WHERE (is_deleted = 0 OR is_deleted IS NULL)
            """
            params = []
            if body_name and body_name.strip() and body_name.lower() != "all":
                query += " AND body_name = ?"
                params.append(body_name.strip())

            cursor.execute(query, params)
            groups = cursor.fetchall()

            for g in groups:
                src_file = g[0]
                b_type = g[1] or "gram_panchayat"
                b_name = g[2] or ""
                w_no = g[3] or ""
                w_name = g[4] or ""

                pdf_full_path = os.path.join(uploads_dir, src_file) if src_file else None
                if pdf_full_path and not os.path.exists(pdf_full_path):
                    alt_path = os.path.join(os.getcwd(), uploads_dir, src_file)
                    if os.path.exists(alt_path):
                        pdf_full_path = alt_path

                if pdf_full_path and os.path.exists(pdf_full_path):
                    voters = ULBExtractor.process_pdf(pdf_full_path)
                    if voters:
                        cursor.execute("DELETE FROM panchayat_voters WHERE source_file = ?;", (src_file,))
                        clean_records = []
                        for v in voters:
                            clean_records.append({
                                "serial_no": v.serial_no,
                                "name": v.name,
                                "relation_type": v.relation_type,
                                "relation_name": v.relation_name,
                                "house_no": v.house_no,
                                "age": v.age,
                                "gender": v.gender,
                                "epic_no": v.epic_no or "",
                                "polling_station": v.polling_station or "",
                                "section_no": v.section_no or "",
                                "page_no": v.page_no or 1
                            })
                        conn.commit()
                        save_res = cls.save_voters(
                            records=clean_records,
                            body_type=b_type,
                            body_name=b_name,
                            ward_no=w_no,
                            ward_name=w_name,
                            source_file=src_file,
                            db_id=db_id
                        )
                        cnt = save_res.get("inserted", len(clean_records))
                        total_enhanced += cnt
                        sources_processed.append(f"{src_file} ({cnt} मतदाता, PDF से शुद्ध पुनर्निष्कासन)")
                        continue

                cursor.execute("""
                    SELECT id, name, relation_type, relation_name, house_no, age, gender, epic_no
                    FROM panchayat_voters
                    WHERE source_file = ? AND (is_deleted = 0 OR is_deleted IS NULL);
                """, (src_file,))
                rows = cursor.fetchall()

                for row in rows:
                    v_id, r_name, r_rel_type, r_rel_name, r_house, r_age, r_gender, r_epic = row

                    c_name = ULBExtractor.decode_sec_up_font(r_name or "")
                    c_name = ULBExtractor.clean_compound_hindi_name(c_name)

                    c_rel = ULBExtractor.decode_sec_up_font(r_rel_name or "")
                    c_rel = ULBExtractor.clean_compound_hindi_name(c_rel)

                    c_house = clean_house_no(r_house or "")

                    rec_dict = {
                        "name": c_name,
                        "relation_type": r_rel_type,
                        "relation_name": c_rel,
                        "house_no": c_house,
                        "age": r_age,
                        "gender": r_gender,
                        "epic_no": r_epic or ""
                    }
                    eval_res = LocalScanQualityAI.evaluate_record_quality(rec_dict)
                    corrected_rec, _ = DualPassErrorCorrector.apply_intelligent_corrections(rec_dict, eval_res)

                    norm_m = normalize_devanagari(corrected_rec["name"])
                    phon_m = get_phonetic_key(corrected_rec["name"])
                    norm_r = normalize_devanagari(corrected_rec["relation_name"])
                    phon_r = get_phonetic_key(corrected_rec["relation_name"])

                    cursor.execute("""
                        UPDATE panchayat_voters
                        SET name = ?, relation_type = ?, relation_name = ?, house_no = ?,
                            age = ?, gender = ?, epic_no = ?, updated_at = ?,
                            name_normalized = ?, name_phonetic = ?, rel_normalized = ?, rel_phonetic = ?
                        WHERE id = ?;
                    """, (
                        corrected_rec["name"],
                        corrected_rec["relation_type"],
                        corrected_rec["relation_name"],
                        corrected_rec["house_no"],
                        corrected_rec["age"],
                        corrected_rec["gender"],
                        corrected_rec["epic_no"],
                        now_str,
                        norm_m,
                        phon_m,
                        norm_r,
                        phon_r,
                        v_id
                    ))
                    total_enhanced += 1

                conn.commit()
                sources_processed.append(f"{src_file} ({len(rows)} मतदाता, डेटाबेस इन-प्लेस शुद्धि)")

        return {
            "success": True,
            "enhanced_count": total_enhanced,
            "sources_processed": sources_processed,
            "message": f"लोकल AI द्वारा कुल {total_enhanced} पंचायत मतदाता सफलतापूर्वक शुद्ध व मानकीकृत किए गए।"
        }

    @classmethod
    def bulk_update_voters(
        cls,
        filter_params: Dict[str, Any],
        update_values: Dict[str, Any],
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Bulk updates administrative fields (ward_no, ward_name, part_no, body_name,
        polling_station, polling_booth, mohalla) across voters matching the filter.
        """
        allowed_updates = {
            "ward_no", "ward_name", "part_no", "body_name", "body_type",
            "polling_station", "polling_booth", "mohalla"
        }
        updates = {k: str(v).strip() for k, v in update_values.items() if k in allowed_updates and v is not None}
        if not updates:
            return {"success": False, "updated_count": 0, "message": "कोई अपडेट मान प्रदान नहीं किया गया।"}

        # Enforce that part_no is never blank if part_no is being updated
        if "part_no" in updates and not updates["part_no"]:
            updates["part_no"] = updates.get("ward_no") or "1"

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates["updated_at"] = now_str

        with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()

            where_clauses = ["is_deleted = 0"]
            where_vals = []

            if filter_params.get("voter_ids"):
                ids = filter_params["voter_ids"]
                placeholders = ",".join("?" for _ in ids)
                where_clauses.append(f"id IN ({placeholders})")
                where_vals.extend(ids)
            else:
                if filter_params.get("body_name"):
                    where_clauses.append("body_name = ?")
                    where_vals.append(filter_params["body_name"].strip())
                if filter_params.get("ward_no"):
                    where_clauses.append("ward_no = ?")
                    where_vals.append(str(filter_params["ward_no"]).strip())
                if filter_params.get("part_no"):
                    where_clauses.append("part_no = ?")
                    where_vals.append(str(filter_params["part_no"]).strip())
                if filter_params.get("source_file"):
                    where_clauses.append("source_file = ?")
                    where_vals.append(filter_params["source_file"].strip())

            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            query = f"UPDATE panchayat_voters SET {set_clause} WHERE {' AND '.join(where_clauses)};"
            params = list(updates.values()) + where_vals

            cursor.execute(query, params)
            updated_count = cursor.rowcount
            conn.commit()

        return {
            "success": True,
            "updated_count": updated_count,
            "message": f"सफलतापूर्वक {updated_count} मतदाताओं के विवरण अपडेट किए गए।"
        }

    @classmethod
    def get_voter(cls, voter_id: int, db_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a single voter from `panchayat_voters` table."""
        with VoterDatabase.get_connection(db_id=db_id, purpose="read") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM panchayat_voters WHERE id = ?;", (voter_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    @classmethod
    def update_voter(
        cls,
        voter_id: int,
        updates: Dict[str, Any],
        db_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Updates an individual voter's attributes in `panchayat_voters`.
        Re-computes Devanagari normalized variants and Soundex keys if name or relation is updated.
        """
        allowed_fields = {
            "name", "relation_type", "relation_name", "house_no", "age", "gender",
            "serial_no", "ward_no", "ward_name", "part_no", "polling_booth",
            "polling_station", "mohalla", "section_no"
        }
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered_updates:
            return cls.get_voter(voter_id, db_id=db_id)

        # Name normalization & phonetics if name modified
        if "name" in filtered_updates and filtered_updates["name"]:
            norm_m = normalize_devanagari(filtered_updates["name"])
            phon_m = get_phonetic_key(filtered_updates["name"])
            filtered_updates["name_normalized"] = norm_m
            filtered_updates["name_phonetic"] = phon_m

        if "relation_name" in filtered_updates:
            rel_val = filtered_updates["relation_name"] or ""
            norm_r = normalize_devanagari(rel_val)
            phon_r = get_phonetic_key(rel_val)
            filtered_updates["rel_normalized"] = norm_r
            filtered_updates["rel_phonetic"] = phon_r

        if "age" in filtered_updates and filtered_updates["age"] is not None:
            try:
                filtered_updates["age"] = int(filtered_updates["age"])
            except (ValueError, TypeError):
                filtered_updates["age"] = None

        if "serial_no" in filtered_updates and filtered_updates["serial_no"] is not None:
            try:
                filtered_updates["serial_no"] = int(filtered_updates["serial_no"])
            except (ValueError, TypeError):
                pass

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filtered_updates["updated_at"] = now_str

        with VoterDatabase.get_connection(db_id=db_id, purpose="write") as conn:
            cls.ensure_table(conn)
            cursor = conn.cursor()
            set_clauses = [f"{k} = ?" for k in filtered_updates.keys()]
            vals = list(filtered_updates.values()) + [voter_id]
            cursor.execute(f"UPDATE panchayat_voters SET {', '.join(set_clauses)} WHERE id = ?;", vals)
            conn.commit()

        return cls.get_voter(voter_id, db_id=db_id)

    @classmethod
    def _locate_pdf(cls, source_file: str, uploads_dir: Optional[str] = None) -> Optional[str]:
        """Locates the source PDF file across potential upload and data directories."""
        if not source_file:
            return None
        from pathlib import Path
        candidate_dirs = []
        if uploads_dir:
            candidate_dirs.append(Path(uploads_dir))
        candidate_dirs.extend([
            Path("uploads"),
            Path("backend/uploads"),
            Path("data"),
            Path(".")
        ])
        
        target_name = os.path.basename(source_file).strip()
        for cdir in candidate_dirs:
            if not cdir.exists():
                continue
            exact = cdir / target_name
            if exact.exists():
                return str(exact)
            for f in cdir.glob("*.pdf"):
                if f.name == target_name or target_name in f.name:
                    return str(f)
        return None

    @staticmethod
    def _convert_devanagari_digits(text: str) -> str:
        """Converts Devanagari numerals to standard ASCII digits."""
        dev_map = {
            '०': '0', '१': '1', '२': '2', '३': '3', '४': '4',
            '५': '5', '६': '6', '७': '7', '८': '8', '९': '9'
        }
        return "".join(dev_map.get(ch, ch) for ch in text)

    @classmethod
    def get_voter_crop_and_rescan(
        cls,
        voter_id: int,
        rescan: bool = False,
        uploads_dir: Optional[str] = None,
        db_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Extracts high-resolution crop of the target voter from the original PDF electoral roll.
        The crop includes context of 2 voters above and 2 voters below, with the target voter highlighted.
        If rescan is True, performs a high-precision targeted OCR / font extraction on that card and returns structured fields.
        """
        import io
        import base64
        import pymupdf as fitz
        from PIL import Image, ImageDraw, ImageOps, ImageEnhance
        from .ocr_extractor import OCRExtractor
        from .ulb_extractor import ULBExtractor

        voter = cls.get_voter(voter_id, db_id=db_id)
        if not voter:
            return {"success": False, "error": "मतदाता रिकॉर्ड नहीं मिला।"}

        source_file = voter.get("source_file") or ""
        pdf_path = cls._locate_pdf(source_file, uploads_dir=uploads_dir)

        if not pdf_path or not os.path.exists(pdf_path):
            return {
                "success": True,
                "voter": voter,
                "has_pdf": False,
                "crop_image_base64": None,
                "rescanned": None,
                "source_file": source_file,
                "page_no": voter.get("page_no") or 1,
                "message": "मूल पीडीएफ फाइल सर्वर पर नहीं मिली। केवल मैनुअल संपादन उपलब्ध है।"
            }

        try:
            doc = fitz.open(pdf_path)
            page_no = voter.get("page_no") or 1
            page_idx = max(0, min(page_no - 1, len(doc) - 1))
            page = doc[page_idx]

            # High-fidelity 300 DPI rendering for crystal clarity
            zoom = 300.0 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            words = page.get_text("words")
            page_rect_w = page.rect.width
            page_rect_h = page.rect.height
            doc.close()

            target_serial = voter.get("serial_no")
            target_row_info = None
            target_col_info = None

            # Layout Analysis: SEC UP 2-Column rolls vs 30-card grid
            table_words = [w for w in words if w[1] >= 115]
            col_ranges = [
                (1, 0, 295, 30, 75, 120, 250, 275, 295),
                (2, 295, 595, 325, 370, 413, 540, 565, 595)
            ]

            is_two_col = len(words) >= 10 and any(w[0] < 295 and w[1] >= 115 for w in words) and any(w[0] >= 295 and w[1] >= 115 for w in words)

            if is_two_col:
                for col_idx, x_min, x_max, s_lim, h_lim, n_lim, r_lim, g_lim, a_lim in col_ranges:
                    col_w = [w for w in table_words if x_min <= w[0] < x_max]
                    if not col_w:
                        continue
                    sorted_w = sorted(col_w, key=lambda w: (w[1], w[0]))
                    rows = []
                    curr_row = []
                    curr_y = -1.0
                    for w in sorted_w:
                        if curr_y < 0 or abs(w[1] - curr_y) < 5.0:
                            curr_row.append(w)
                            curr_y = (curr_y + w[1]) / 2.0 if curr_y >= 0 else w[1]
                        else:
                            rows.append(curr_row)
                            curr_row = [w]
                            curr_y = w[1]
                    if curr_row:
                        rows.append(curr_row)

                    for r_i, r in enumerate(rows):
                        by_x = sorted(r, key=lambda w: w[0])
                        s_tokens = [w[4] for w in by_x if x_min <= w[0] < s_lim]
                        if s_tokens and s_tokens[0].isdigit() and target_serial is not None and int(s_tokens[0]) == int(target_serial):
                            target_row_info = (r_i, rows, x_min, x_max, s_lim, h_lim, n_lim, r_lim, g_lim, a_lim)
                            target_col_info = (col_idx, x_min, x_max)
                            break
                    if target_row_info:
                        break

            digital_row_tokens = None
            if target_row_info:
                r_i, rows, x_min, x_max, s_lim, h_lim, n_lim, r_lim, g_lim, a_lim = target_row_info
                digital_row_tokens = rows[r_i]
                above_idx = max(0, r_i - 2)
                below_idx = min(len(rows) - 1, r_i + 2)

                y0_pt = max(0, min(w[1] for w in rows[above_idx]) - 4)
                y1_pt = min(page_rect_h, max(w[3] for w in rows[below_idx]) + 4)
                target_y0_pt = min(w[1] for w in rows[r_i]) - 1
                target_y1_pt = max(w[3] for w in rows[r_i]) + 1

                # If at top of column, show header context
                if r_i <= 1:
                    y0_pt = max(0, y0_pt - 18)
            elif is_two_col:
                # Proportional row in 2-column
                col_idx = 1 if (target_serial or 1) <= 49 else 2
                x_min, x_max = (0, 295) if col_idx == 1 else (295, 595)
                rel_row = ((target_serial or 1) - 1) % 49
                h_row = (page_rect_h * 0.85) / 49.0
                t_y = 115.0 + rel_row * h_row
                target_y0_pt = t_y
                target_y1_pt = t_y + h_row
                y0_pt = max(0, t_y - 2.2 * h_row)
                y1_pt = min(page_rect_h, t_y + 3.2 * h_row)
            else:
                # 3-Column x 10-Row Grid (Standard ECI / Gram Panchayat format)
                col_w = (page_rect_w * 0.94) / 3.0
                card_h = (page_rect_h * 0.88) / 10.0
                card_idx = ((target_serial or 1) - 1) % 30
                grid_row = card_idx // 3
                grid_col = card_idx % 3
                
                left_margin = page_rect_w * 0.03
                top_margin = page_rect_h * 0.08
                
                x_min = left_margin + grid_col * col_w
                x_max = left_margin + (grid_col + 1) * col_w
                
                above_row = max(0, grid_row - 2)
                below_row = min(9, grid_row + 2)
                
                y0_pt = top_margin + above_row * card_h
                y1_pt = top_margin + (below_row + 1) * card_h
                target_y0_pt = top_margin + grid_row * card_h
                target_y1_pt = top_margin + (grid_row + 1) * card_h

            px_x0 = max(0, int(x_min * zoom))
            px_x1 = min(img.width, int(x_max * zoom))
            px_y0 = max(0, int(y0_pt * zoom))
            px_y1 = min(img.height, int(y1_pt * zoom))

            crop = img.crop((px_x0, px_y0, px_x1, px_y1))

            # Highlight target voter with red outline
            t_y0 = max(2, int((target_y0_pt - y0_pt) * zoom))
            t_y1 = min(crop.height - 3, int((target_y1_pt - y0_pt) * zoom))

            draw = ImageDraw.Draw(crop)
            draw.rectangle([2, t_y0, crop.width - 3, t_y1], outline=(239, 68, 68), width=3)

            buf = io.BytesIO()
            crop.save(buf, format="PNG", optimize=True)
            crop_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            rescanned = None
            if rescan:
                # Target card crop at 300 DPI
                t_card_y0 = max(0, int(target_y0_pt * zoom))
                t_card_y1 = min(img.height, int(target_y1_pt * zoom))
                target_card_crop = img.crop((px_x0, t_card_y0, px_x1, t_card_y1))
                cw, ch = target_card_crop.size

                if is_two_col:
                    # SEC UP 2-Column: Slice micro-cells
                    name_crop = target_card_crop.crop((int(0.24 * cw), 0, int(0.51 * cw), ch))
                    rel_crop = target_card_crop.crop((int(0.50 * cw), 0, int(0.81 * cw), ch))
                    house_crop = target_card_crop.crop((int(0.10 * cw), 0, int(0.24 * cw), ch))
                    gender_crop = target_card_crop.crop((int(0.80 * cw), 0, int(0.89 * cw), ch))
                    age_crop = target_card_crop.crop((int(0.88 * cw), 0, cw, ch))

                    ocr_name = OCRExtractor._ocr_image(name_crop, lang="hin", psm=7).strip()
                    ocr_rel = OCRExtractor._ocr_image(rel_crop, lang="hin", psm=7).strip()
                    ocr_house = OCRExtractor._ocr_image(house_crop, lang="hin+eng", psm=7).strip()
                    ocr_gender = OCRExtractor._ocr_image(gender_crop, lang="hin", psm=7).strip()
                    ocr_age = OCRExtractor._ocr_image(age_crop, lang="eng", psm=7, whitelist="0123456789").strip()

                    clean_name = clean_hindi_text(ocr_name) or voter.get("name", "")
                    clean_rel = clean_hindi_text(ocr_rel) or voter.get("relation_name", "")

                    # House number: convert Devanagari numerals
                    h_dev = cls._convert_devanagari_digits(ocr_house)
                    clean_h = clean_house_no(h_dev) or voter.get("house_no", "")

                    # If digital tokens exist, compare and supplement
                    if digital_row_tokens:
                        by_x = sorted(digital_row_tokens, key=lambda w: w[0])
                        d_h_tokens = [w[4] for w in by_x if 30 <= (w[0] - x_min) < 75]
                        if d_h_tokens:
                            d_h = ULBExtractor.decode_sec_up_font("".join(d_h_tokens)).strip()
                            d_h_clean = clean_house_no(cls._convert_devanagari_digits(d_h))
                            if d_h_clean:
                                clean_h = d_h_clean

                    # Gender
                    if "म" in ocr_gender or "स्त्री" in ocr_gender:
                        clean_g = "महिला"
                    elif "पु" in ocr_gender or "प" in ocr_gender:
                        clean_g = "पुरुष"
                    else:
                        clean_g = voter.get("gender", "पुरुष")

                    # Age
                    try:
                        clean_a = int(ocr_age) if ocr_age else voter.get("age")
                    except Exception:
                        clean_a = voter.get("age")

                    # Relation Type
                    r_type = voter.get("relation_type", "पिता")
                    if clean_g == "महिला" and ("देवी" in clean_name or "श्रीमती" in clean_name):
                        if r_type in ["पति", "पिता"]:
                            r_type = "पति"

                    rescanned = {
                        "name": clean_name,
                        "relation_type": r_type,
                        "relation_name": clean_rel,
                        "house_no": clean_h,
                        "age": clean_a,
                        "gender": clean_g
                    }
                else:
                    # 30-Card Standard Box Extractor
                    text_crop = target_card_crop.crop((0, 0, int(cw * 0.74), ch))
                    rescan_hin = OCRExtractor._ocr_image(text_crop, lang="hin", psm=6)
                    rescan_eng = OCRExtractor._ocr_image(text_crop, lang="eng", psm=6)
                    from .field_parser import UPFieldParser
                    parsed = UPFieldParser.parse_single_voter_box(
                        box_text=f"{rescan_hin}\n{rescan_eng}",
                        default_serial=target_serial or 1,
                        page_no=page_no,
                        box_eng=rescan_eng
                    )
                    if parsed:
                        rescanned = {
                            "name": clean_hindi_text(parsed.name) or voter.get("name", ""),
                            "relation_type": parsed.relation_type or voter.get("relation_type", "पिता"),
                            "relation_name": clean_hindi_text(parsed.relation_name) or voter.get("relation_name", ""),
                            "house_no": clean_house_no(parsed.house_no) or voter.get("house_no", ""),
                            "age": parsed.age or voter.get("age"),
                            "gender": parsed.gender or voter.get("gender", "पुरुष")
                        }
                    else:
                        rescanned = {
                            "name": voter.get("name", ""),
                            "relation_type": voter.get("relation_type", "पिता"),
                            "relation_name": voter.get("relation_name", ""),
                            "house_no": voter.get("house_no", ""),
                            "age": voter.get("age"),
                            "gender": voter.get("gender", "पुरुष")
                        }

            return {
                "success": True,
                "voter": voter,
                "has_pdf": True,
                "source_file": source_file,
                "page_no": page_no,
                "crop_image_base64": f"data:image/png;base64,{crop_b64}",
                "rescanned": rescanned,
                "message": "उच्च क्वालिटी से पुनः स्कैन संपन्न हुआ।" if rescan else "क्रॉप सफलतापूर्वक लोड हुआ।"
            }

        except Exception as e:
            return {
                "success": True,
                "voter": voter,
                "has_pdf": False,
                "crop_image_base64": None,
                "rescanned": None,
                "source_file": source_file,
                "page_no": voter.get("page_no") or 1,
                "message": f"क्रॉप लोड करने में त्रुटि: {str(e)}"
            }

