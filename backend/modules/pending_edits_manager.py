"""
PendingEditsManager: Handles Data Operator database modifications staging and Admin Approval workflow.

Architecture:
1. Operator modifications (UPDATE, ADD, DELETE, BULK_UPDATE_PART) are recorded in `operator_pending_edits`.
2. When the operator searches or views the database, their active pending edits are dynamically overlaid.
3. In online public search, unapproved edits are NEVER applied.
4. Admins can review, approve (commit to voters table), or reject proposed edits.
"""

import json
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from backend.config import DATA_DIR, DB_PATH
from backend.modules.database import VoterDatabase
from backend.modules.db_manager import DatabaseManager


class PendingEditsManager:
    """Manages staging, overlaying, and approving database edits made by Data Operators."""

    @classmethod
    def get_connection(cls, db_id: Optional[str] = None) -> sqlite3.Connection:
        """Returns SQLite connection to the target database and ensures table exists."""
        conn = VoterDatabase.get_connection(db_id=db_id, purpose="write")
        cls._ensure_table(conn)
        return conn

    @classmethod
    def _ensure_table(cls, conn: sqlite3.Connection):
        """Creates the operator_pending_edits table if not present."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operator_pending_edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_username TEXT NOT NULL,
                operator_name TEXT,
                action_type TEXT NOT NULL, -- 'UPDATE', 'ADD', 'DELETE', 'BULK_UPDATE_PART'
                target_id INTEGER,         -- voter record ID (null for ADD or BULK)
                part_no TEXT,
                assembly TEXT,
                voter_name TEXT,
                epic_no TEXT,
                original_data TEXT,        -- JSON string of original record
                proposed_data TEXT,        -- JSON string of proposed record
                diff_summary TEXT,         -- JSON or string summary of changed fields
                status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                review_notes TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_status ON operator_pending_edits(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_op ON operator_pending_edits(operator_username, status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_target ON operator_pending_edits(target_id);")
        conn.commit()

    @classmethod
    def create_pending_edit(
        cls,
        operator_username: str,
        operator_name: str,
        action_type: str,
        target_id: Optional[int],
        original_data: Optional[Dict[str, Any]],
        proposed_data: Dict[str, Any],
        diff_summary: Optional[Dict[str, Any]] = None,
        part_no: Optional[str] = None,
        assembly: Optional[str] = None,
        voter_name: Optional[str] = None,
        epic_no: Optional[str] = None,
        db_id: Optional[str] = None
    ) -> int:
        """Inserts a new pending edit record."""
        now = datetime.now().isoformat()

        # Extract voter info if not explicitly provided
        if proposed_data:
            if not voter_name:
                voter_name = proposed_data.get("name") or (original_data.get("name") if original_data else None)
            if not epic_no:
                epic_no = proposed_data.get("epic_no") or (original_data.get("epic_no") if original_data else None)
            if not part_no:
                part_no = proposed_data.get("part_no") or (original_data.get("part_no") if original_data else None)
            if not assembly:
                assembly = proposed_data.get("assembly") or (original_data.get("assembly") if original_data else None)

        if not diff_summary and original_data and proposed_data:
            diff_summary = {}
            for k, new_v in proposed_data.items():
                old_v = original_data.get(k)
                if old_v != new_v:
                    diff_summary[k] = {"old": old_v, "new": new_v}

        orig_json = json.dumps(original_data, ensure_ascii=False) if original_data else None
        prop_json = json.dumps(proposed_data, ensure_ascii=False) if proposed_data else None
        diff_json = json.dumps(diff_summary or {}, ensure_ascii=False)

        conn = cls.get_connection(db_id=db_id)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO operator_pending_edits (
                    operator_username, operator_name, action_type, target_id,
                    part_no, assembly, voter_name, epic_no,
                    original_data, proposed_data, diff_summary,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                operator_username, operator_name or operator_username, action_type, target_id,
                str(part_no or ""), str(assembly or ""), voter_name or "", epic_no or "",
                orig_json, prop_json, diff_json, now
            ))
            edit_id = cur.lastrowid
            return edit_id

    @classmethod
    def list_pending_edits(
        cls,
        status: Optional[str] = "pending",
        operator_username: Optional[str] = None,
        db_id: Optional[str] = None,
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Retrieves list of pending edits with parsed details."""
        conn = cls.get_connection(db_id=db_id)
        with conn:
            cur = conn.cursor()
            query = "SELECT * FROM operator_pending_edits WHERE 1=1"
            params = []
            if status and status != "all":
                query += " AND status = ?"
                params.append(status)
            if operator_username:
                query += " AND operator_username = ?"
                params.append(operator_username)

            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()

            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["original_data"] = json.loads(item["original_data"]) if item["original_data"] else None
                except Exception:
                    pass
                try:
                    item["proposed_data"] = json.loads(item["proposed_data"]) if item["proposed_data"] else None
                except Exception:
                    pass
                try:
                    item["diff_summary"] = json.loads(item["diff_summary"]) if item["diff_summary"] else {}
                except Exception:
                    pass
                results.append(item)
            return results

    @classmethod
    def get_pending_counts(cls, db_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns count of pending, approved, and rejected edits."""
        conn = cls.get_connection(db_id=db_id)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved_count,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected_count,
                    COUNT(*) as total_count
                FROM operator_pending_edits;
            """)
            row = cur.fetchone()
            return {
                "pending": row["pending_count"] or 0 if row else 0,
                "approved": row["approved_count"] or 0 if row else 0,
                "rejected": row["rejected_count"] or 0 if row else 0,
                "total": row["total_count"] or 0 if row else 0
            }

    @classmethod
    def get_operator_pending_map(cls, operator_username: str, db_id: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
        """Returns a dict mapping `voter_id -> latest pending edit proposed_data` for this operator."""
        if not operator_username:
            return {}
        conn = cls.get_connection(db_id=db_id)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, target_id, action_type, proposed_data, diff_summary, created_at
                FROM operator_pending_edits
                WHERE operator_username = ? AND status = 'pending' AND target_id IS NOT NULL
                ORDER BY id ASC;
            """, (operator_username,))
            rows = cur.fetchall()

            res = {}
            for r in rows:
                target_id = r["target_id"]
                try:
                    prop = json.loads(r["proposed_data"]) if r["proposed_data"] else {}
                except Exception:
                    prop = {}
                try:
                    diff = json.loads(r["diff_summary"]) if r["diff_summary"] else {}
                except Exception:
                    diff = {}
                res[target_id] = {
                    "pending_edit_id": r["id"],
                    "action_type": r["action_type"],
                    "proposed_data": prop,
                    "diff_summary": diff,
                    "created_at": r["created_at"]
                }
            return res

    @classmethod
    def get_operator_pending_adds(cls, operator_username: str, db_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns list of pending newly-added voters for this operator."""
        if not operator_username:
            return []
        conn = cls.get_connection(db_id=db_id)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, action_type, proposed_data, created_at
                FROM operator_pending_edits
                WHERE operator_username = ? AND status = 'pending' AND action_type = 'ADD'
                ORDER BY id DESC;
            """, (operator_username,))
            rows = cur.fetchall()

            adds = []
            for r in rows:
                try:
                    prop = json.loads(r["proposed_data"]) if r["proposed_data"] else {}
                    prop["id"] = f"pending_{r['id']}"
                    prop["is_pending_add"] = True
                    prop["pending_edit_id"] = r["id"]
                    adds.append(prop)
                except Exception:
                    pass
            return adds

    @classmethod
    def overlay_operator_edits(
        cls,
        records: List[Dict[str, Any]],
        operator_username: str,
        db_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Overlays this operator's pending changes on top of base voter records.
        Marks `is_pending_approval = True` and displays proposed values to the operator.
        """
        if not operator_username or not records:
            return records

        pending_map = cls.get_operator_pending_map(operator_username, db_id=db_id)
        if not pending_map:
            return records

        overlaid = []
        for r in records:
            vid = r.get("id")
            if vid in pending_map:
                edit_info = pending_map[vid]
                rec_copy = dict(r)
                action = edit_info["action_type"]

                if action == "DELETE":
                    rec_copy["is_deleted"] = 1
                    rec_copy["is_pending_approval"] = True
                    rec_copy["pending_action"] = "DELETE"
                    rec_copy["pending_edit_id"] = edit_info["pending_edit_id"]
                elif action in ("UPDATE", "BULK_UPDATE_PART"):
                    # Overlay proposed fields
                    for k, v in edit_info["proposed_data"].items():
                        rec_copy[k] = v
                    rec_copy["is_pending_approval"] = True
                    rec_copy["pending_action"] = action
                    rec_copy["pending_edit_id"] = edit_info["pending_edit_id"]
                    rec_copy["pending_diff"] = edit_info["diff_summary"]

                overlaid.append(rec_copy)
            else:
                overlaid.append(r)

        return overlaid

    @classmethod
    def approve_pending_edit(cls, edit_id: int, admin_username: str, db_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Approves a pending edit and permanently writes the proposed changes into SQLite `voters` table.
        """
        conn = cls.get_connection(db_id=db_id)
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM operator_pending_edits WHERE id = ?", (edit_id,))
            row = cur.fetchone()
            if not row:
                return False, "संपादन रिकॉर्ड नहीं मिला।"

            if row["status"] != "pending":
                return False, f"यह संपादन पहले से ही {row['status']} है।"

            action = row["action_type"]
            target_id = row["target_id"]
            now = datetime.now().isoformat()

            try:
                proposed = json.loads(row["proposed_data"]) if row["proposed_data"] else {}
            except Exception as e:
                return False, f"प्रस्तावित डेटा पार्स करने में विफल: {str(e)}"

            # 1. Execute database change based on action
            if action == "UPDATE":
                if not target_id:
                    return False, "अपडेट हेतु मतदाता आईडी अनुपलब्ध है।"
                # Exclude internal / non-voters fields
                allowed_fields = [
                    "serial_no", "name", "relation_type", "relation_name",
                    "house_no", "age", "gender", "epic_no", "part_no",
                    "assembly", "polling_station", "page_no", "is_deleted"
                ]
                set_parts = []
                val_parts = []
                for field in allowed_fields:
                    if field in proposed:
                        set_parts.append(f"{field} = ?")
                        val_parts.append(proposed[field])

                if set_parts:
                    val_parts.append(target_id)
                    cur.execute(f"UPDATE voters SET {', '.join(set_parts)} WHERE id = ?", val_parts)

            elif action == "ADD":
                fields = [
                    "serial_no", "name", "relation_type", "relation_name",
                    "house_no", "age", "gender", "epic_no", "part_no",
                    "assembly", "polling_station", "page_no", "is_deleted"
                ]
                cols = []
                vals = []
                qmarks = []
                for f in fields:
                    if f in proposed:
                        cols.append(f)
                        vals.append(proposed[f])
                        qmarks.append("?")
                cols.append("created_at")
                vals.append(now)
                qmarks.append("?")

                cur.execute(f"INSERT INTO voters ({', '.join(cols)}) VALUES ({', '.join(qmarks)})", vals)
                new_voter_id = cur.lastrowid
                cur.execute("UPDATE operator_pending_edits SET target_id = ? WHERE id = ?", (new_voter_id, edit_id))

            elif action == "DELETE":
                if not target_id:
                    return False, "हटाने हेतु मतदाता आईडी अनुपलब्ध है।"
                cur.execute("DELETE FROM voters WHERE id = ?", (target_id,))

            elif action == "BULK_UPDATE_PART":
                current_part = proposed.get("current_part_no")
                new_part = proposed.get("new_part_no")
                new_assembly = proposed.get("new_assembly")
                new_station = proposed.get("new_polling_station")

                if not current_part or not new_part or not new_assembly:
                    return False, "बल्क अपडेट हेतु भाग संख्या व विधानसभा आवश्यक हैं।"

                if new_station and new_station.strip():
                    cur.execute("""
                        UPDATE voters
                        SET part_no = ?, assembly = ?, polling_station = ?
                        WHERE part_no = ?
                    """, (new_part, new_assembly, new_station.strip(), current_part))
                else:
                    cur.execute("""
                        UPDATE voters
                        SET part_no = ?, assembly = ?
                        WHERE part_no = ?
                    """, (new_part, new_assembly, current_part))

            # 2. Mark pending record as approved
            cur.execute("""
                UPDATE operator_pending_edits
                SET status = 'approved', reviewed_at = ?, reviewed_by = ?
                WHERE id = ?
            """, (now, admin_username, edit_id))

            return True, "संपादन सफलतापूर्वक स्वीकृत व मुख्य डेटाबेस में लागू कर दिया गया।"

    @classmethod
    def reject_pending_edit(
        cls,
        edit_id: int,
        admin_username: str,
        reason: Optional[str] = None,
        db_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Rejects a pending edit and discards proposed changes."""
        conn = cls.get_connection(db_id=db_id)
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT status FROM operator_pending_edits WHERE id = ?", (edit_id,))
            row = cur.fetchone()
            if not row:
                return False, "संपादन रिकॉर्ड नहीं मिला।"
            if row["status"] != "pending":
                return False, f"यह संपादन पहले से ही {row['status']} है।"

            now = datetime.now().isoformat()
            cur.execute("""
                UPDATE operator_pending_edits
                SET status = 'rejected', reviewed_at = ?, reviewed_by = ?, review_notes = ?
                WHERE id = ?
            """, (now, admin_username, reason or "एडमिन द्वारा अस्वीकृत", edit_id))

            return True, "संपादन अस्वीकृत कर दिया गया है।"

    @classmethod
    def bulk_approve(cls, edit_ids: List[int], admin_username: str, db_id: Optional[str] = None) -> Dict[str, Any]:
        """Approves multiple pending edits in batch."""
        success_count = 0
        failed_count = 0
        errors = []
        for eid in edit_ids:
            ok, msg = cls.approve_pending_edit(eid, admin_username, db_id=db_id)
            if ok:
                success_count += 1
            else:
                failed_count += 1
                errors.append(f"ID {eid}: {msg}")
        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors
        }

    @classmethod
    def bulk_reject(cls, edit_ids: List[int], admin_username: str, reason: Optional[str] = None, db_id: Optional[str] = None) -> Dict[str, Any]:
        """Rejects multiple pending edits in batch."""
        success_count = 0
        failed_count = 0
        errors = []
        for eid in edit_ids:
            ok, msg = cls.reject_pending_edit(eid, admin_username, reason=reason, db_id=db_id)
            if ok:
                success_count += 1
            else:
                failed_count += 1
                errors.append(f"ID {eid}: {msg}")
        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors
        }
