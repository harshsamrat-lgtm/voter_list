"""
Authentication and User Management Module for UP Voter Portal.
Provides:
1. SQLite storage for users and user_sessions in data/voters.db.
2. Device binding and locking for regular users (One Mobile per Account).
3. Super-admin account ('harshsamrat' / '222333') with universal device access and mobile admin privileges.
4. Self-service and admin-managed password changes.
5. User activation, deactivation, deletion, and device-lock resets.
"""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path

from ..config import DB_PATH


class AuthManager:
    """Manages user accounts, sessions, device bindings, and password hashing."""

    DEFAULT_ADMIN_USERNAME = "harshsamrat"
    DEFAULT_ADMIN_PASSWORD = "222333"

    @classmethod
    @contextmanager
    def get_connection(cls):
        """Returns SQLite connection configured with row factory, safely closed after use."""
        conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @classmethod
    def hash_password(cls, password: str, salt: Optional[str] = None) -> str:
        """Hashes password using PBKDF2-HMAC-SHA256 with 100,000 iterations."""
        if not salt:
            salt = secrets.token_hex(16)
        key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000
        )
        return f"{salt}${key.hex()}"

    @classmethod
    def verify_password(cls, password: str, stored_hash: str) -> bool:
        """Verifies a plain password against stored salt$hash."""
        try:
            if "$" not in stored_hash:
                return False
            salt, _ = stored_hash.split("$", 1)
            expected_hash = cls.hash_password(password, salt)
            return secrets.compare_digest(expected_hash, stored_hash)
        except Exception:
            return False

    @classmethod
    def init_auth_tables(cls):
        """Initializes users and user_sessions tables in voters.db."""
        with cls.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    full_name TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'active',
                    bound_device_id TEXT DEFAULT NULL,
                    bound_device_name TEXT DEFAULT NULL,
                    bound_at TEXT DEFAULT NULL,
                    last_login_at TEXT DEFAULT NULL,
                    last_login_ip TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    device_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_bound_device ON users(bound_device_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON user_sessions(user_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON user_sessions(expires_at);")

            # Auto-migrate bound_device_fp column if not present
            try:
                conn.execute("ALTER TABLE users ADD COLUMN bound_device_fp TEXT DEFAULT NULL;")
            except Exception:
                pass

        cls.seed_default_admin()

    @classmethod
    def seed_default_admin(cls):
        """Ensures the super-admin account ('harshsamrat' / '222333') exists and is active."""
        with cls.get_connection() as conn:
            row = conn.execute(
                "SELECT id, password_hash, role, status FROM users WHERE username = ?",
                (cls.DEFAULT_ADMIN_USERNAME,)
            ).fetchone()

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if not row:
                # Create default admin
                pwd_hash = cls.hash_password(cls.DEFAULT_ADMIN_PASSWORD)
                conn.execute("""
                    INSERT INTO users (username, password_hash, full_name, role, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'admin', 'active', ?, ?)
                """, (cls.DEFAULT_ADMIN_USERNAME, pwd_hash, "हर्ष सम्राट (मुख्य एडमिन)", now, now))
                conn.commit()
            else:
                # Ensure admin has role='admin' and status='active'
                if row["role"] != "admin" or row["status"] != "active":
                    conn.execute(
                        "UPDATE users SET role = 'admin', status = 'active', updated_at = ? WHERE id = ?",
                        (now, row["id"])
                    )
                    conn.commit()

    @classmethod
    def has_local_admin(cls) -> bool:
        """Returns True if at least one local admin account (other than root 'harshsamrat') exists."""
        try:
            with cls.get_connection() as conn:
                row = conn.execute(
                    "SELECT count(*) as cnt FROM users WHERE role = 'admin' AND LOWER(username) != ?",
                    (cls.DEFAULT_ADMIN_USERNAME.lower(),)
                ).fetchone()
                return bool(row and row["cnt"] > 0)
        except Exception:
            return False

    @classmethod
    def setup_initial_admin(
        cls,
        username: str,
        password: str,
        full_name: str = "",
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        device_fp: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Creates or initializes the local admin user right after license activation.
        Validates that username != 'harshsamrat' (reserved for root superadmin).
        """
        username = (username or "").strip()
        full_name = (full_name or "").strip()
        password = (password or "").strip()

        if not username:
            return False, None, "कृपया एडमिन यूजर आईडी (Username) दर्ज करें।"
        if username.lower() == cls.DEFAULT_ADMIN_USERNAME.lower():
            return False, None, f"'{cls.DEFAULT_ADMIN_USERNAME}' सुपर एडमिन के लिए आरक्षित है। कृपया अपना नया यूजरनेम चुनें।"
        if len(password) < 4:
            return False, None, "पासवर्ड कम से कम 4 अक्षरों का होना चाहिए।"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pwd_hash = cls.hash_password(password)

        with cls.get_connection() as conn:
            existing = conn.execute("SELECT id, role FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
            if existing:
                conn.execute("""
                    UPDATE users 
                    SET password_hash = ?, full_name = ?, role = 'admin', status = 'active', updated_at = ?
                    WHERE id = ?
                """, (pwd_hash, full_name or "व्यवस्थापक", now, existing["id"]))
                conn.commit()
            else:
                conn.execute("""
                    INSERT INTO users (username, password_hash, full_name, role, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'admin', 'active', ?, ?)
                """, (username, pwd_hash, full_name or "व्यवस्थापक", now, now))
                conn.commit()

        # Log in newly created admin immediately
        return cls.authenticate_user(
            username=username,
            password=password,
            device_id=device_id,
            device_name=device_name or "Admin PC",
            device_fp=device_fp,
            ip_address=ip_address
        )

    @classmethod
    def authenticate_user(
        cls,
        username: str,
        password: str,
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        device_fp: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Authenticates a user with device binding checks.
        Enforces single active session per regular user and auto-heals device ID
        via hardware fingerprint if cache was cleared on the same device.
        Returns (success: bool, session_dict: Optional[dict], message: str).
        """
        username = (username or "").strip()
        if not username or not password:
            return False, None, "कृपया यूजर आईडी और पासवर्ड दोनों दर्ज करें।"

        with cls.get_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
                (username,)
            ).fetchone()

            # Auto-heal superadmin account if missing on new database/machine
            if not user and username.lower() == cls.DEFAULT_ADMIN_USERNAME.lower():
                cls.seed_default_admin()
                user = conn.execute(
                    "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
                    (username,)
                ).fetchone()

            if not user:
                return False, None, "गलत यूजर आईडी अथवा पासवर्ड।"

            is_super = user["username"].lower() == cls.DEFAULT_ADMIN_USERNAME.lower()
            pwd_valid = cls.verify_password(password, user["password_hash"])

            if not pwd_valid:
                return False, None, "गलत यूजर आईडी अथवा पासवर्ड।"

            if user["status"] != "active":
                return False, None, "आपका खाता निष्क्रिय (Deactivated) कर दिया गया है। कृपया एडमिन से संपर्क करें।"

            role = user["role"]
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Prune old expired sessions to prevent table bloating
            try:
                conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (now,))
            except Exception:
                pass

            bound_device_id = user["bound_device_id"]
            bound_device_name = user["bound_device_name"]

            # Admin accounts bypass device binding (harshsamrat can log in from anywhere)
            if role != "admin":
                clean_device_id = (device_id or "").strip()
                clean_device_fp = (device_fp or "").strip()
                if not clean_device_id and not clean_device_fp:
                    return False, None, "मोबाइल डिवाइस पहचान (Device ID/Fingerprint) अनुपलब्ध है। कृपया ब्राउज़र कुकीज़ या स्टोरेज सक्षम करें।"

                bound_device_fp = user["bound_device_fp"] if "bound_device_fp" in user.keys() else None

                if bound_device_id is None:
                    # First login: bind this device!
                    clean_name = (device_name or "Mobile Browser").strip()
                    conn.execute("""
                        UPDATE users 
                        SET bound_device_id = ?, bound_device_name = ?, bound_device_fp = ?, bound_at = ?, updated_at = ?
                        WHERE id = ?
                    """, (clean_device_id, clean_name, clean_device_fp or None, now, now, user["id"]))
                    conn.commit()
                    bound_device_id = clean_device_id
                    bound_device_name = clean_name
                    bound_device_fp = clean_device_fp
                elif bound_device_id != clean_device_id:
                    # If device_fp matches, it is the EXACT SAME physical phone (browser cache was cleared, or user opened in another browser on the same phone)
                    if clean_device_fp and bound_device_fp and clean_device_fp == bound_device_fp:
                        # Auto-heal: update bound_device_id for this same device
                        conn.execute("""
                            UPDATE users
                            SET bound_device_id = ?, updated_at = ?
                            WHERE id = ?
                        """, (clean_device_id, now, user["id"]))
                        conn.commit()
                        bound_device_id = clean_device_id
                    else:
                        # Device mismatch: User is genuinely trying to log in from a different physical phone/device
                        dev_label = bound_device_name or "पूर्व पंजीकृत मोबाइल"
                        return False, None, (
                            f"यह आईडी केवल आपके पूर्व पंजीकृत मोबाइल/डिवाइस ({dev_label}) पर ही लॉगिन हो सकती है। "
                            "नए मोबाइल पर लॉगिन करने के लिए कृपया एडमिन से 'डिवाइस अनलॉक/रीसेट' की अनुमति लें।"
                        )
                else:
                    # bound_device_id matches. If bound_device_fp was not saved before, store it now
                    if clean_device_fp and not bound_device_fp:
                        conn.execute("UPDATE users SET bound_device_fp = ?, updated_at = ? WHERE id = ?", (clean_device_fp, now, user["id"]))
                        conn.commit()

                # SINGLE ACTIVE SESSION ENFORCEMENT:
                # Terminate ALL existing sessions for this user so only 1 session is active at a time!
                # If someone else was logged in with this account, they are kicked out immediately!
                conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user["id"],))
                conn.commit()

            # Update login info
            conn.execute("""
                UPDATE users
                SET last_login_at = ?, last_login_ip = ?, updated_at = ?
                WHERE id = ?
            """, (now, ip_address, now, user["id"]))

            # Create session token (valid for 30 days)
            token = secrets.token_hex(32)
            expires_at = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""
                INSERT INTO user_sessions (token, user_id, username, role, device_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (token, user["id"], user["username"], role, device_id, now, expires_at))
            conn.commit()

            session_data = {
                "token": token,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "full_name": user["full_name"],
                    "role": role,
                    "status": user["status"],
                    "bound_device_id": bound_device_id,
                    "bound_device_name": bound_device_name,
                    "is_admin": (role == "admin"),
                    "is_operator": (role == "operator"),
                    "is_superadmin": (user["username"].lower() == cls.DEFAULT_ADMIN_USERNAME.lower()),
                },
                "expires_at": expires_at
            }
            return True, session_data, "सफलतापूर्वक लॉगिन हो गया।"

    @classmethod
    def validate_session(cls, token: str) -> Optional[Dict[str, Any]]:
        """Validates a session token and returns active user details."""
        if not token or len(token) < 16:
            return None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with cls.get_connection() as conn:
            row = conn.execute("""
                SELECT s.token, s.user_id, s.username, s.role, s.device_id, s.expires_at,
                       u.full_name, u.status, u.bound_device_id, u.bound_device_name
                FROM user_sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ? AND s.expires_at > ?
            """, (token, now)).fetchone()

            if not row:
                return None

            if row["status"] != "active":
                return None

            return {
                "user_id": row["user_id"],
                "username": row["username"],
                "full_name": row["full_name"],
                "role": row["role"],
                "status": row["status"],
                "is_admin": (row["role"] == "admin"),
                "is_operator": (row["role"] == "operator"),
                "is_superadmin": (row["username"].lower() == cls.DEFAULT_ADMIN_USERNAME.lower()),
                "bound_device_id": row["bound_device_id"],
                "bound_device_name": row["bound_device_name"]
            }

    @classmethod
    def revoke_session(cls, token: str) -> bool:
        """Logs out a user by deleting their session token."""
        if not token:
            return False
        with cls.get_connection() as conn:
            conn.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
            conn.commit()
            return True

    @classmethod
    def change_user_password(
        cls,
        user_id: int,
        new_password: str,
        old_password: Optional[str] = None,
        is_admin_override: bool = False
    ) -> Tuple[bool, str]:
        """Changes user password. If not admin override, verifies old password."""
        if not new_password or len(new_password) < 4:
            return False, "नया पासवर्ड कम से कम 4 अक्षरों का होना चाहिए।"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with cls.get_connection() as conn:
            user = conn.execute("SELECT id, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False, "यूजर नहीं मिला।"

            if not is_admin_override:
                if not old_password:
                    return False, "वर्तमान पासवर्ड दर्ज करना अनिवार्य है।"
                if not cls.verify_password(old_password, user["password_hash"]):
                    return False, "वर्तमान पासवर्ड गलत है।"

            new_hash = cls.hash_password(new_password)
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, now, user_id)
            )
            conn.commit()
            return True, "पासवर्ड सफलतापूर्वक बदल दिया गया है।"

    @classmethod
    def create_user(
        cls,
        username: str,
        password: str,
        full_name: str = "",
        role: str = "user"
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Creates a new user account (admin only)."""
        username = (username or "").strip()
        if not username or len(username) < 3:
            return False, None, "यूजर आईडी कम से कम 3 अक्षरों की होनी चाहिए।"

        if not password or len(password) < 4:
            return False, None, "पासवर्ड कम से कम 4 अक्षरों का होना चाहिए।"

        clean_role = (role or "").strip().lower()
        if clean_role not in ("admin", "operator", "user"):
            clean_role = "user"
        role = clean_role
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with cls.get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (username,)
            ).fetchone()
            if existing:
                return False, None, f"यूजर आईडी '{username}' पहले से मौजूद है। कृपया दूसरी आईडी चुनें।"

            pwd_hash = cls.hash_password(password)
            cursor = conn.execute("""
                INSERT INTO users (username, password_hash, full_name, role, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
            """, (username, pwd_hash, full_name.strip(), role, now, now))
            conn.commit()
            user_id = cursor.lastrowid

            return True, {
                "id": user_id,
                "username": username,
                "full_name": full_name.strip(),
                "role": role,
                "status": "active"
            }, "नया यूजर सफलतापूर्वक बनाया गया।"

    @classmethod
    def list_users(cls) -> List[Dict[str, Any]]:
        """Returns all users with device binding status and metadata."""
        with cls.get_connection() as conn:
            rows = conn.execute("""
                SELECT id, username, full_name, role, status,
                       bound_device_id, bound_device_name, bound_at,
                       last_login_at, last_login_ip, created_at, updated_at
                FROM users
                ORDER BY role DESC, id ASC
            """).fetchall()

            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "username": r["username"],
                    "full_name": r["full_name"] or "",
                    "role": r["role"],
                    "status": r["status"],
                    "is_device_bound": bool(r["bound_device_id"]),
                    "bound_device_name": r["bound_device_name"] or "कोई नहीं (अनलॉक)",
                    "bound_at": r["bound_at"],
                    "last_login_at": r["last_login_at"],
                    "created_at": r["created_at"],
                    "is_superadmin": (r["username"].lower() == cls.DEFAULT_ADMIN_USERNAME.lower())
                })
            return result

    @classmethod
    def update_user_status(cls, user_id: int, status: str) -> Tuple[bool, str]:
        """Activates or deactivates a user. Prevents deactivating super-admin."""
        if status not in ("active", "inactive"):
            return False, "अवैध स्थिति (Status)।"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with cls.get_connection() as conn:
            user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False, "यूजर नहीं मिला।"

            if user["username"].lower() == cls.DEFAULT_ADMIN_USERNAME.lower() and status == "inactive":
                return False, "मुख्य एडमिन खाते को निष्क्रिय नहीं किया जा सकता।"

            conn.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, user_id)
            )
            if status == "inactive":
                # Terminate active sessions
                conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            conn.commit()

            msg = "यूजर खाता सक्रिय कर दिया गया है।" if status == "active" else "यूजर खाता निष्क्रिय कर दिया गया है।"
            return True, msg

    @classmethod
    def reset_user_device(cls, user_id: int) -> Tuple[bool, str]:
        """Resets device lock, allowing user to bind to a new mobile."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with cls.get_connection() as conn:
            user = conn.execute("SELECT id, username, bound_device_name FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False, "यूजर नहीं मिला।"

            conn.execute("""
                UPDATE users
                SET bound_device_id = NULL, bound_device_name = NULL, bound_device_fp = NULL, bound_at = NULL, updated_at = ?
                WHERE id = ?
            """, (now, user_id))
            # Also terminate existing sessions so user logs in afresh on new device
            conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            conn.commit()

            return True, f"यूजर '{user['username']}' का मोबाइल डिवाइस बंधन रीसेट कर दिया गया है। अब वे नए मोबाइल पर लॉगिन कर सकते हैं।"

    @classmethod
    def delete_user(cls, user_id: int) -> Tuple[bool, str]:
        """Deletes a user account. Prevents deleting super-admin."""
        with cls.get_connection() as conn:
            user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False, "यूजर नहीं मिला।"

            if user["username"].lower() == cls.DEFAULT_ADMIN_USERNAME.lower():
                return False, "मुख्य एडमिन 'harshsamrat' को हटाया नहीं जा सकता।"

            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            conn.commit()
            return True, f"यूजर '{user['username']}' को सफलतापूर्वक हटा दिया गया है।"
