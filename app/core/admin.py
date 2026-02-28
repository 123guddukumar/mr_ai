"""
MR AI RAG - Admin Core Functions
Handles admin creation, login, and client management operations.
"""

import hashlib
import logging
import secrets
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)


def _hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def _generate_admin_token() -> str:
    return "adm-" + secrets.token_hex(32)

def _get_db():
    from app.core.database import get_session_local
    SessionLocal = get_session_local()
    return SessionLocal()


# ── Admin Account Management ──────────────────────────────────────────────────

def create_admin(username: str, password: str, db=None) -> Optional[dict]:
    """Create a new admin account. Returns None if username already exists."""
    from app.core.models import Admin
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        existing = db.query(Admin).filter(Admin.username == username.lower()).first()
        if existing:
            return None
        admin = Admin(
            username=username.lower(),
            password_hash=_hash_password(password),
            created_at=datetime.utcnow(),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        logger.info(f"Admin created: {username}")
        return admin.to_dict()
    finally:
        if close_db:
            db.close()


def login_admin(username: str, password: str, db=None) -> Optional[dict]:
    """Validate admin credentials, return record with token on success."""
    from app.core.models import Admin
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        admin = db.query(Admin).filter(Admin.username == username.lower()).first()
        if not admin:
            return None
        if admin.password_hash != _hash_password(password):
            return None
        admin.admin_token = _generate_admin_token()
        admin.last_login = datetime.utcnow()
        db.commit()
        db.refresh(admin)
        return {**admin.to_dict(), "token": admin.admin_token}
    finally:
        if close_db:
            db.close()


def validate_admin_token(token: str, db=None) -> Optional[dict]:
    """Return admin record if token is valid."""
    if not token:
        return None
    from app.core.models import Admin
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        admin = db.query(Admin).filter(Admin.admin_token == token).first()
        if not admin:
            return None
        return {**admin.to_dict(), "token": admin.admin_token}
    finally:
        if close_db:
            db.close()


# ── Client Management (admin operations) ─────────────────────────────────────

def admin_list_clients(db=None) -> List[dict]:
    """List all registered clients."""
    from app.core.models import Client, ApiKey, ChatMessage
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        clients = db.query(Client).order_by(Client.created_at.desc()).all()
        result = []
        for c in clients:
            key_count = db.query(ApiKey).filter(ApiKey.client_id == c.client_id).count()
            msg_count = db.query(ChatMessage).filter(ChatMessage.client_id == c.client_id).count()
            d = c.to_dict()
            d["api_key_count"] = key_count
            d["chat_message_count"] = msg_count
            result.append(d)
        return result
    finally:
        if close_db:
            db.close()


def admin_get_client(client_id: str, db=None) -> Optional[dict]:
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        c = db.query(Client).filter(Client.client_id == client_id).first()
        return c.to_dict() if c else None
    finally:
        if close_db:
            db.close()


def admin_update_client(client_id: str, name: str = None, email: str = None, is_verified: bool = None, db=None) -> Optional[dict]:
    """Update client fields. Only provided fields are updated."""
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        c = db.query(Client).filter(Client.client_id == client_id).first()
        if not c:
            return None
        if name is not None:
            c.name = name
        if email is not None:
            c.email = email.lower()
        if is_verified is not None:
            c.is_verified = is_verified
        db.commit()
        db.refresh(c)
        return c.to_dict()
    finally:
        if close_db:
            db.close()


def admin_delete_client(client_id: str, db=None) -> bool:
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        c = db.query(Client).filter(Client.client_id == client_id).first()
        if not c:
            return False
        db.delete(c)
        db.commit()
        logger.info(f"Admin deleted client: {client_id}")
        return True
    finally:
        if close_db:
            db.close()


def admin_login_as_client(client_id: str, db=None) -> Optional[dict]:
    """Generate a fresh token for a client — allows admin to impersonate."""
    from app.core.models import Client
    import secrets
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        c = db.query(Client).filter(Client.client_id == client_id).first()
        if not c:
            return None
        # Generate fresh token
        c.token = "clt-" + secrets.token_hex(24)
        c.last_login = datetime.utcnow()
        db.commit()
        db.refresh(c)
        return c.to_dict()
    finally:
        if close_db:
            db.close()


def admin_reset_client_password(client_id: str, new_password: str, db=None) -> bool:
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        c = db.query(Client).filter(Client.client_id == client_id).first()
        if not c:
            return False
        c.password_hash = _hash_password(new_password)
        db.commit()
        return True
    finally:
        if close_db:
            db.close()


def admin_count() -> int:
    """Return total number of admin accounts (used to prompt first-run setup)."""
    try:
        db = _get_db()
        from app.core.models import Admin
        count = db.query(Admin).count()
        db.close()
        return count
    except Exception:
        return 0
