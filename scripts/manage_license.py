"""
Admin License Manager Tool for UP Voter Seva.
Usage:
  python scripts/manage_license.py hwid
  python scripts/manage_license.py generate "Client Name" --days 365 [--hwid HWID-XXXX-XXXX-XXXX]
  python scripts/manage_license.py verify "UPVOTER-..."
  python scripts/manage_license.py status
"""

import sys
import os
import argparse

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.modules.license_guard import LicenseGuard


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="UP Voter Seva - License & Security Key Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command: hwid
    subparsers.add_parser("hwid", help="Show this machine's Hardware ID (HWID)")

    # Command: generate
    gen_parser = subparsers.add_parser("generate", help="Generate a new security/license key")
    gen_parser.add_argument("client", type=str, help="Client or Operator name")
    gen_parser.add_argument("--days", type=int, default=365, help="Validity in days (default: 365)")
    gen_parser.add_argument("--hwid", type=str, default=None, help="Lock key to specific Machine HWID (optional)")
    gen_parser.add_argument("--notes", type=str, default="", help="Optional notes or district/city")

    # Command: verify
    verify_parser = subparsers.add_parser("verify", help="Verify and decode a license key")
    verify_parser.add_argument("key", type=str, help="The UPVOTER-... license key string")

    # Command: status
    subparsers.add_parser("status", help="Check current local machine license status")

    # Command: activate
    act_parser = subparsers.add_parser("activate", help="Activate this machine with a license key")
    act_parser.add_argument("key", type=str, help="The license key to activate")

    args = parser.parse_args()

    if args.command == "hwid":
        hwid = LicenseGuard.get_machine_hwid()
        print("\n" + "=" * 50)
        print(f"🖥️  इस कंप्यूटर की मशीन पहचान (Machine HWID):")
        print(f"👉  {hwid}")
        print("=" * 50 + "\n")

    elif args.command == "generate":
        key = LicenseGuard.generate_key(
            client_name=args.client,
            expiry_days=args.days,
            hwid=args.hwid,
            notes=args.notes
        )
        print("\n" + "=" * 60)
        print("🔑  सफलतापूर्वक नई लाइसेंस/सिक्योरिटी की तैयार हो गई:")
        print("=" * 60)
        print(f"📌  ग्राहक/ऑपरेटर : {args.client}")
        print(f"⏳  वैधता अवधि   : {args.days} दिन")
        print(f"🔒  HWID लॉक     : {args.hwid or 'ANY (पहले एक्टिवेट करने वाले PC पर स्वतः लॉक)'}")
        print("-" * 60)
        print(f"👉  लाइसेंस की   : {key}")
        print("=" * 60 + "\n")

    elif args.command == "verify":
        ok, payload, msg = LicenseGuard.decode_key(args.key)
        if ok and payload:
            print("\n✅  लाइसेंस की मान्य (Valid) है:")
            print(f"• ग्राहक: {payload.get('c')}")
            print(f"• समाप्ति तिथि: {payload.get('e')}")
            print(f"• मशीन लॉक: {payload.get('h')}")
            print(f"• नोट्स: {payload.get('n', '')}\n")
        else:
            print(f"\n❌  लाइसेंस अमान्य: {msg}\n")

    elif args.command == "status":
        st = LicenseGuard.get_license_status()
        print("\n" + "=" * 50)
        print("📋  स्थानीय कंप्यूटर लाइसेंस स्थिति (Status):")
        print("=" * 50)
        print(f"• सक्रिय (Activated) : {'हाँ (YES)' if st['is_activated'] else 'नहीं (NO)'}")
        print(f"• स्थिति (Status)    : {st['status']}")
        print(f"• ग्राहक का नाम      : {st.get('client_name') or 'N/A'}")
        print(f"• समाप्ति तिथि       : {st.get('expiry_date') or 'N/A'}")
        print(f"• शेष दिन            : {st.get('days_remaining')} दिन")
        print(f"• इस PC का HWID      : {st['hwid']}")
        print(f"• संदेश              : {st['message']}")
        print("=" * 50 + "\n")

    elif args.command == "activate":
        ok, msg = LicenseGuard.activate_license(args.key)
        if ok:
            print(f"\n✅ {msg}\n")
        else:
            print(f"\n❌ एक्टिवेशन विफल: {msg}\n")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
