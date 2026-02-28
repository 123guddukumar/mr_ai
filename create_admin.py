#!/usr/bin/env python3
"""
MR AI RAG - Create Admin CLI Script
Usage:
    python create_admin.py
    python create_admin.py --username admin --password secret123
    python create_admin.py --list          (list existing admins)

Run from the project root directory (where .env is located).
"""

import sys
import os
import argparse
import getpass

# ─────────────────────────────────────────────────────────────────────────────
# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional

# ─────────────────────────────────────────────────────────────────────────────

def _setup_db():
    """Initialize database connection and ensure tables exist."""
    try:
        from app.core.database import init_db
        init_db()
    except Exception as e:
        print(f"❌ Database error: {e}")
        print("   Make sure DATABASE_URL is set in your .env file.")
        sys.exit(1)


def cmd_create(username: str, password: str):
    """Create a new admin account."""
    _setup_db()
    from app.core.admin import create_admin

    result = create_admin(username.strip(), password)
    if result is None:
        print(f"❌ Admin username '{username}' already exists.")
        print("   Use --list to see existing admins.")
        sys.exit(1)
    print(f"\n✅ Admin created successfully!")
    print(f"   Username : {result['username']}")
    print(f"   Created  : {result['created_at']}")
    print(f"\n   Login at : /admin-login")
    print(f"   Swagger  : /api/docs#/Admin\n")


def cmd_list():
    """List all admin accounts."""
    _setup_db()
    try:
        from app.core.database import get_session_local
        from app.core.models import Admin
        SessionLocal = get_session_local()
        db = SessionLocal()
        admins = db.query(Admin).all()
        db.close()
    except Exception as e:
        print(f"❌ Error listing admins: {e}")
        sys.exit(1)

    if not admins:
        print("ℹ️  No admin accounts exist yet.")
        print("   Run: python create_admin.py to create one.")
        return

    print(f"\n{'─'*55}")
    print(f"  {'ID':<5} {'Username':<25} {'Last Login'}")
    print(f"{'─'*55}")
    for a in admins:
        last = a.last_login.strftime('%Y-%m-%d %H:%M') if a.last_login else 'Never'
        print(f"  {a.id:<5} {a.username:<25} {last}")
    print(f"{'─'*55}")
    print(f"  Total: {len(admins)} admin(s)\n")


def cmd_delete(username: str):
    """Delete an admin account."""
    _setup_db()
    try:
        from app.core.database import get_session_local
        from app.core.models import Admin
        SessionLocal = get_session_local()
        db = SessionLocal()
        admin = db.query(Admin).filter(Admin.username == username.lower()).first()
        if not admin:
            print(f"❌ Admin '{username}' not found.")
            db.close()
            sys.exit(1)
        db.delete(admin)
        db.commit()
        db.close()
        print(f"✅ Admin '{username}' deleted.")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="MR AI RAG — Admin Account Manager",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Examples:
  python create_admin.py                         # interactive mode
  python create_admin.py --username admin        # prompts for password
  python create_admin.py -u admin -p secret123  # fully non-interactive
  python create_admin.py --list                  # list all admins
  python create_admin.py --delete admin          # delete an admin
"""
    )
    parser.add_argument("-u", "--username", help="Admin username")
    parser.add_argument("-p", "--password", help="Admin password (min 6 chars)")
    parser.add_argument("--list", "-l", action="store_true", help="List all admin accounts")
    parser.add_argument("--delete", "-d", metavar="USERNAME", help="Delete an admin account")

    args = parser.parse_args()

    # ── List mode ───────────────────────────────────────────────────────────
    if args.list:
        cmd_list()
        return

    # ── Delete mode ──────────────────────────────────────────────────────────
    if args.delete:
        confirm = input(f"⚠  Delete admin '{args.delete}'? Type 'yes' to confirm: ").strip()
        if confirm != "yes":
            print("Cancelled.")
            return
        cmd_delete(args.delete)
        return

    # ── Create mode ──────────────────────────────────────────────────────────
    print("\n┌─────────────────────────────────────────────┐")
    print("│      MR AI RAG — Create Admin Account       │")
    print("└─────────────────────────────────────────────┘\n")

    # Get username
    username = args.username
    if not username:
        username = input("  Username : ").strip()
    if not username:
        print("❌ Username cannot be empty.")
        sys.exit(1)

    # Get password (hidden input if not provided via arg)
    password = args.password
    if not password:
        password = getpass.getpass("  Password : ")
        confirm  = getpass.getpass("  Confirm  : ")
        if password != confirm:
            print("❌ Passwords do not match.")
            sys.exit(1)

    if len(password) < 6:
        print("❌ Password must be at least 6 characters.")
        sys.exit(1)

    cmd_create(username, password)


if __name__ == "__main__":
    main()
