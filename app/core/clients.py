"""
MR AI RAG - Client Management (PostgreSQL)
Uses SQLAlchemy instead of JSON files.
Same public API as before, plus notification helpers.
"""

import hashlib
import logging
import secrets
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def _generate_client_id() -> str:
    return "client-" + secrets.token_hex(5)

def _generate_token() -> str:
    return "clt-" + secrets.token_hex(24)


def _get_db():
    """Get a DB session (used outside of FastAPI request context)."""
    from app.core.database import get_session_local
    SessionLocal = get_session_local()
    return SessionLocal()


# ── Public API ────────────────────────────────────────────────────────────────

def register_client(name: str, email: str, password: str, db=None) -> Optional[dict]:
    """Register a new client. Returns client dict or None if email taken."""
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        existing = db.query(Client).filter(Client.email == email.lower()).first()
        if existing:
            return None

        client_id = _generate_client_id()
        while db.query(Client).filter(Client.client_id == client_id).first():
            client_id = _generate_client_id()

        token = _generate_token()
        now = datetime.utcnow()
        client = Client(
            client_id=client_id, name=name, email=email.lower(),
            password_hash=_hash_password(password), token=token,
            is_verified=False, created_at=now, last_login=now,
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        logger.info(f"Client registered: {client_id} ({email})")
        return client.to_dict()
    finally:
        if close_db:
            db.close()


def login_client(email: str, password: str, db=None) -> Optional[dict]:
    """Validate credentials, refresh token, return record or None."""
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        client = db.query(Client).filter(Client.email == email.lower()).first()
        if not client:
            return None
        if client.password_hash != _hash_password(password):
            return None
        if not client.is_verified:
            return {"error": "not_verified"}
        new_token = _generate_token()
        client.token = new_token
        client.last_login = datetime.utcnow()
        db.commit()
        db.refresh(client)
        return client.to_dict()
    finally:
        if close_db:
            db.close()


def mark_verified(email: str, db=None) -> bool:
    """Mark a client's email as verified."""
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        client = db.query(Client).filter(Client.email == email.lower()).first()
        if not client:
            return False
        client.is_verified = True
        db.commit()
        return True
    finally:
        if close_db:
            db.close()


def update_password(email: str, new_password: str, db=None) -> bool:
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        client = db.query(Client).filter(Client.email == email.lower()).first()
        if not client:
            return False
        client.password_hash = _hash_password(new_password)
        client.token = _generate_token()  # Invalidate old sessions
        db.commit()
        return True
    finally:
        if close_db:
            db.close()


def validate_client_token(token: str, db=None) -> Optional[dict]:
    """Return client record if token is valid, else None."""
    if not token:
        return None
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        client = db.query(Client).filter(Client.token == token).first()
        return client.to_dict() if client else None
    finally:
        if close_db:
            db.close()


def get_client_by_id(client_id: str, db=None) -> Optional[dict]:
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        client = db.query(Client).filter(Client.client_id == client_id).first()
        return client.to_dict() if client else None
    finally:
        if close_db:
            db.close()


# ── Chat History ──────────────────────────────────────────────────────────────

def save_chat_message(client_id: str, role: str, content: str, sources: List[str] = None, category: str = "home", source_type: str = "", db=None) -> bool:
    import json
    from app.core.models import ChatMessage
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        msg = ChatMessage(
            client_id=client_id, role=role, content=content,
            sources_json=json.dumps(sources or []),
            category=category,
            source_type=source_type,
            timestamp=datetime.utcnow(),
        )
        db.add(msg)
        db.commit()
        return True
    except Exception as e:
        logger.error(f"save_chat_message error: {e}")
        return False
    finally:
        if close_db:
            db.close()


def get_chat_history(client_id: str, limit: int = 100, db=None) -> List[dict]:
    from app.core.models import ChatMessage
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        msgs = db.query(ChatMessage).filter(
            ChatMessage.client_id == client_id
        ).order_by(ChatMessage.timestamp.asc()).limit(limit).all()
        return [m.to_dict() for m in msgs]
    finally:
        if close_db:
            db.close()


def clear_chat_history(client_id: str, db=None) -> bool:
    from app.core.models import ChatMessage
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        db.query(ChatMessage).filter(ChatMessage.client_id == client_id).delete()
        db.commit()
        return True
    finally:
        if close_db:
            db.close()


# ── Notifications ─────────────────────────────────────────────────────────────

def save_notification(client_id: str, type: str, title: str, message: str, db=None) -> bool:
    from app.core.models import Notification
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        notif = Notification(
            client_id=client_id, type=type, title=title, message=message,
            is_read=False, created_at=datetime.utcnow(),
        )
        db.add(notif)
        db.commit()
        return True
    except Exception as e:
        logger.error(f"save_notification error: {e}")
        return False
    finally:
        if close_db:
            db.close()


def get_notifications(client_id: str, limit: int = 50, db=None) -> List[dict]:
    from app.core.models import Notification
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        notifs = db.query(Notification).filter(
            Notification.client_id == client_id
        ).order_by(Notification.created_at.desc()).limit(limit).all()
        return [n.to_dict() for n in notifs]
    finally:
        if close_db:
            db.close()


def mark_notifications_read(client_id: str, db=None) -> None:
    from app.core.models import Notification
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        db.query(Notification).filter(
            Notification.client_id == client_id,
            Notification.is_read == False,
        ).update({"is_read": True})
        db.commit()
    finally:
        if close_db:
            db.close()


def get_unread_count(client_id: str, db=None) -> int:
    from app.core.models import Notification
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        return db.query(Notification).filter(
            Notification.client_id == client_id,
            Notification.is_read == False,
        ).count()
    finally:
        if close_db:
            db.close()


def get_client_by_id(client_id: str, db=None) -> Optional[dict]:
    """Fetch a client record by client_id. Returns dict or None."""
    from app.core.models import Client
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        client = db.query(Client).filter(Client.client_id == client_id).first()
        return client.to_dict() if client else None
    finally:
        if close_db:
            db.close()
