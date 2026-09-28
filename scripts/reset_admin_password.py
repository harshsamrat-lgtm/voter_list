"""
Quick Utility to Reset Admin Password and Unlock Device Bindings for UP Voter Portal.
Usage: python scripts/reset_admin_password.py [optional_new_password]
Default password if not supplied: 222333
"""

import sys
import os
import sqlite3

# Ensure UTF-8 output on Windows console
if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr is not None and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Add parent directory to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from backend.config import DB_PATH
from backend.modules.auth_manager import AuthManager

def reset_admin(new_pwd: str = "222333"):
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at: {DB_PATH}")
        return False

    AuthManager.init_auth_tables()
    pwd_hash = AuthManager.hash_password(new_pwd)

    with sqlite3.connect(str(DB_PATH)) as conn:
        # Update superadmin
        conn.execute(
            "UPDATE users SET password_hash = ?, role = 'admin', status = 'active' WHERE username = 'harshsamrat'",
            (pwd_hash,)
        )
        # Unlock device bindings for operator accounts
        conn.execute("UPDATE users SET bound_device_id = NULL, bound_device_name = NULL, bound_at = NULL")
        conn.commit()

    print("=" * 60)
    print("✅ एडमिन पासवर्ड सफलतापूर्वक रीसेट कर दिया गया है!")
    print("=" * 60)
    print(f"👤 यूजर आईडी: harshsamrat")
    print(f"🔑 नया पासवर्ड: {new_pwd}")
    print("📱 सभी ऑपरेटर/यूजर खातों के डिवाइस बंधन अनलॉक कर दिए गए हैं।")
    print("=" * 60)
    return True

if __name__ == "__main__":
    pwd = sys.argv[1] if len(sys.argv) > 1 else "222333"
    reset_admin(pwd)
