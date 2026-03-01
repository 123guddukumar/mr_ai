"""
MR AI RAG - API Key Management Routes
POST   /api/keys/generate   → Create a new API key (admin only)
GET    /api/keys             → List all keys (masked, admin only)
DELETE /api/keys/{key_id}    → Revoke a key (admin only)
POST   /api/keys/validate    → Check if a key is valid (public)
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.api_keys import (
    generate_api_key,
    validate_api_key,
    list_api_keys,
    revoke_api_key,
    get_total_keys,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic Models ───────────────────────────────────────────────────────────

class GenerateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="A friendly label for this API key")
    created_by: str = Field(default="admin", max_length=100)


class GenerateKeyResponse(BaseModel):
    id: str
    key: str                # shown ONCE only
    key_preview: str
    name: str
    created_at: str
    is_active: bool
    message: str


class ApiKeyRecord(BaseModel):
    id: str
    name: str
    created_by: str
    created_at: str
    is_active: bool
    last_used_at: Optional[str] = None
    request_count: int
    key_preview: str


class ListKeysResponse(BaseModel):
    total: int
    keys: List[ApiKeyRecord]


class ValidateKeyRequest(BaseModel):
    key: str = Field(..., min_length=10)


class ValidateKeyResponse(BaseModel):
    valid: bool
    key_id: Optional[str] = None
    name: Optional[str] = None
    message: str


class RevokeResponse(BaseModel):
    success: bool
    key_id: str
    message: str


# ── Admin Secret Guard ────────────────────────────────────────────────────────

def _check_admin(x_admin_secret: Optional[str]) -> Optional[str]:
    """
    Validates X-Admin-Secret. Accepts either:
      1. The global API_KEY_ADMIN_SECRET from settings
      2. A valid client token (clt-...) — returns that client's client_id

    Returns client_id string if a client token was used, else None.
    Raises 403 if neither matches.
    """
    from app.core.config import settings
    if not x_admin_secret:
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing admin secret. Pass X-Admin-Secret header.",
        )
    # Check global admin secret first
    if x_admin_secret == settings.API_KEY_ADMIN_SECRET:
        return None  # Global admin — no client_id context

    # Check client token (starts with "clt-")
    if x_admin_secret.startswith("clt-"):
        from app.core.clients import validate_client_token
        client = validate_client_token(x_admin_secret)
        if client:
            return client["client_id"]  # Return client_id for attribution

    raise HTTPException(
        status_code=403,
        detail="Invalid admin secret. Use your global admin secret or your client token.",
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/keys/generate",
    response_model=GenerateKeyResponse,
    summary="Generate a new API key",
    tags=["API Keys"],
)
async def create_api_key(
    req: GenerateKeyRequest,
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
    db: Session = Depends(get_db),
):
    """
    Generate a new API key.  
    ⚠️ The full key is returned **only once** in this response — store it safely.  
    Requires `X-Admin-Secret` header (global admin secret OR your client token).
    """
    client_id = _check_admin(x_admin_secret)
    result = generate_api_key(
        name=req.name,
        created_by=client_id or req.created_by,
        client_id=client_id,
        db=db,
    )
    # Fire notification if created by a client
    if client_id:
        from app.core.clients import save_notification, get_client_by_id
        save_notification(
            client_id=client_id, type="api_key",
            title="API Key Created",
            message=f"New API key '{req.name}' created. Preview: {result['key_preview']}",
            db=db,
        )
        # ── Send API key to client's email (non-blocking background thread) ──────
        try:
            import threading
            from app.core.email_service import send_api_key_email
            client = get_client_by_id(client_id, db)
            if client and client.get("email"):
                def _send():
                    send_api_key_email(
                        to_email=client["email"],
                        key_name=req.name,
                        full_key=result["key"],
                        name=client.get("name", ""),
                    )
                threading.Thread(target=_send, daemon=True).start()
                logger.info(f"API key email queued for {client['email']}")
        except Exception as email_err:
            logger.warning(f"Could not send API key email: {email_err}")

    return GenerateKeyResponse(
        **result,
        message=(
            f"API key '{req.name}' created successfully. "
            "⚠️ Copy your key now — it will not be shown again!"
        ),
    )


@router.get(
    "/keys",
    response_model=ListKeysResponse,
    summary="List all API keys (masked)",
    tags=["API Keys"],
)
async def get_api_keys(
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
):
    """
    List all created API keys with masked values.  
    Requires `X-Admin-Secret` header.
    """
    _check_admin(x_admin_secret)
    keys = list_api_keys()
    return ListKeysResponse(total=len(keys), keys=keys)


# ⚠️  IMPORTANT: static routes must come BEFORE wildcard /{key_id} routes
@router.get(
    "/keys/status",
    summary="Get API key system status",
    tags=["API Keys"],
)
async def api_key_status():
    """Public endpoint: returns whether API key enforcement is enabled and total key count."""
    from app.core.config import settings
    return {
        "api_keys_enabled": settings.API_KEYS_ENABLED,
        "total_keys": get_total_keys(),
        "message": (
            "API key authentication is active. Include X-API-Key header in requests."
            if settings.API_KEYS_ENABLED
            else "API key authentication is disabled. All requests are allowed."
        ),
    }


@router.post(
    "/keys/validate",
    response_model=ValidateKeyResponse,
    summary="Validate an API key",
    tags=["API Keys"],
)
async def validate_key(req: ValidateKeyRequest):
    """
    Check whether an API key is valid and active.  
    Does **not** require admin secret — intended for client-side key checks.
    """
    record = validate_api_key(req.key)
    if record is None:
        return ValidateKeyResponse(
            valid=False,
            message="Invalid or revoked API key.",
        )
    return ValidateKeyResponse(
        valid=True,
        key_id=record["id"],
        name=record["name"],
        message=f"Key is valid. Name: '{record['name']}'",
    )


@router.delete(
    "/keys/{key_id}",
    response_model=RevokeResponse,
    summary="Revoke an API key",
    tags=["API Keys"],
)
async def revoke_key(
    key_id: str,
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
):
    """
    Revoke (deactivate) an API key by its ID.  
    Revoked keys cannot be used to authenticate requests.  
    Requires `X-Admin-Secret` header.
    """
    _check_admin(x_admin_secret)
    ok = revoke_api_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Key ID '{key_id}' not found.")
    return RevokeResponse(
        success=True,
        key_id=key_id,
        message=f"Key '{key_id}' has been revoked successfully.",
    )
