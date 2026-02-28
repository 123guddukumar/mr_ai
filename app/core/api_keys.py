"""
MR AI RAG - API Key Management (PostgreSQL)
Rewrites the JSON-file-based api_keys.py to use SQLAlchemy.
Same public API preserved.
"""

import hashlib
import logging
import secrets
import string
from datetime import datetime
from typing import Optional, List

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def _mask_key(raw_key: str) -> str:
    if len(raw_key) <= 12:
        return raw_key[:4] + "****"
    return raw_key[:12] + "..." + raw_key[-4:]

def _generate_raw_key() -> str:
    alphabet = string.ascii_letters + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(40))
    return f"mrairag-{suffix}"


def _get_db():
    from app.core.database import get_session_local
    SessionLocal = get_session_local()
    return SessionLocal()


# ── Public API ────────────────────────────────────────────────────────────────

def generate_api_key(name: str, created_by: str = "admin", client_id: str = None, db=None) -> dict:
    """Create a new API key. Returns full key ONCE."""
    from app.core.models import ApiKey
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        raw = _generate_raw_key()
        key_id = secrets.token_hex(8)
        # Ensure unique key_id
        while db.query(ApiKey).filter(ApiKey.key_id == key_id).first():
            key_id = secrets.token_hex(8)

        now = datetime.utcnow()
        api_key = ApiKey(
            key_id=key_id,
            client_id=client_id,
            name=name,
            created_by=created_by or (client_id or "admin"),
            key_hash=_hash_key(raw),
            key_preview=_mask_key(raw),
            is_active=True,
            created_at=now,
            request_count=0,
        )
        db.add(api_key)
        db.commit()
        logger.info(f"API key generated: id={key_id!r} name={name!r}")
        return {
            "id": key_id,
            "key": raw,
            "key_preview": _mask_key(raw),
            "name": name,
            "created_at": now.isoformat(),
            "is_active": True,
        }
    finally:
        if close_db:
            db.close()


def validate_api_key(raw: str, db=None) -> Optional[dict]:
    """Match raw key against stored hashes. Returns record or None."""
    from app.core.models import ApiKey
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        target = _hash_key(raw)
        record = db.query(ApiKey).filter(
            ApiKey.key_hash == target,
            ApiKey.is_active == True,
        ).first()
        if not record:
            return None
        record.last_used_at = datetime.utcnow()
        record.request_count = (record.request_count or 0) + 1
        db.commit()
        return record.to_dict()
    finally:
        if close_db:
            db.close()


def list_api_keys(client_id: str = None, db=None) -> List[dict]:
    """Return all keys (or keys for a specific client) — no hashes."""
    from app.core.models import ApiKey
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        q = db.query(ApiKey)
        if client_id:
            q = q.filter(ApiKey.client_id == client_id)
        records = q.order_by(ApiKey.created_at.desc()).all()
        return [
            {k: v for k, v in r.to_dict().items() if k != "key_hash"}
            for r in records
        ]
    finally:
        if close_db:
            db.close()


def revoke_api_key(key_id: str, db=None) -> bool:
    """Revoke by key_id. Returns True if found."""
    from app.core.models import ApiKey
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        record = db.query(ApiKey).filter(ApiKey.key_id == key_id).first()
        if not record:
            return False
        record.is_active = False
        db.commit()
        logger.info(f"API key revoked: {key_id!r}")
        return True
    finally:
        if close_db:
            db.close()


def get_total_keys(db=None) -> int:
    from app.core.models import ApiKey
    close_db = db is None
    if db is None:
        db = _get_db()
    try:
        return db.query(ApiKey).count()
    finally:
        if close_db:
            db.close()


# ── FastAPI Dependencies ───────────────────────────────────────────────────────

async def require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> dict:
    from app.core.config import settings
    if not settings.API_KEYS_ENABLED:
        return {"id": "bypass", "name": "no-auth", "is_active": True}
    record = validate_api_key(x_api_key)
    if record is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key. Pass your key in the X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return record


async def optional_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> Optional[dict]:
    from app.core.config import settings
    if not settings.API_KEYS_ENABLED:
        return {"id": "bypass", "name": "no-auth", "is_active": True}
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Pass your key in the X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return await require_api_key(x_api_key=x_api_key)
