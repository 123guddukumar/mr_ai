"""
MR AI RAG - Client Auth Routes (PostgreSQL + OTP)
POST /api/clients/send-otp       → Send OTP to email
POST /api/clients/register       → Register (requires OTP)
POST /api/clients/login          → Login
GET  /api/clients/me             → Profile
GET  /api/clients/me/history     → Chat history
POST /api/clients/me/history     → Save message
DELETE /api/clients/me/history   → Clear history
GET  /api/clients/me/notifications       → Get notifications
POST /api/clients/me/notifications/read  → Mark all read
GET  /api/clients/me/keys        → List client's API keys
POST /api/clients/forgot-password → Send reset OTP
POST /api/clients/reset-password  → Verify OTP + set new password
"""

import logging
from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.clients import (
    register_client, login_client, validate_client_token,
    mark_verified, update_password,
    save_chat_message, get_chat_history, clear_chat_history,
    save_notification, get_notifications, mark_notifications_read, get_unread_count,
)
from app.core.email_service import generate_otp, store_otp, verify_otp, send_otp_email
from app.core.api_keys import list_api_keys

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────────────

class SendOTPRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=200)
    name: str = Field(default="", max_length=100)
    purpose: str = Field(default="register", pattern="^(register|reset)$")

class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=200)
    password: str = Field(..., min_length=6, max_length=200)
    otp: str = Field(..., min_length=4, max_length=8)

class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5)
    password: str = Field(..., min_length=1)

class ClientAuthResponse(BaseModel):
    client_id: str
    token: str
    name: str
    email: str
    created_at: str
    message: str

class ClientProfile(BaseModel):
    client_id: str
    name: str
    email: str
    created_at: str
    last_login: str

class ChatMessageIn(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)
    sources: List[str] = []
    category: str = Field(default="home")    # "home" | "playground"
    source_type: str = Field(default="")    # "pdf" | "yt" | "web" | "vid"

class ChatMessageOut(BaseModel):
    role: str
    content: str
    sources: List[str]
    category: str = "home"
    source_type: str = ""
    timestamp: str

class HistoryResponse(BaseModel):
    client_id: str
    total: int
    messages: List[ChatMessageOut]

class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    message: str
    is_read: bool
    created_at: str

class NotificationsResponse(BaseModel):
    total: int
    unread: int
    notifications: List[NotificationOut]

class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5)

class ResetPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5)
    otp: str = Field(..., min_length=4)
    new_password: str = Field(..., min_length=6)


# ── Auth dependency ───────────────────────────────────────────────────────────

def _require_client(
    x_client_token: Optional[str] = Header(None, alias="X-Client-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not x_client_token:
        raise HTTPException(401, "Missing X-Client-Token header.")
    record = validate_client_token(x_client_token, db=db)
    if not record:
        raise HTTPException(401, "Invalid or expired client token. Please login again.")
    return record


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/clients/send-otp", tags=["Clients"])
async def send_otp(req: SendOTPRequest, db: Session = Depends(get_db)):
    """Step 1 of registration: send OTP email."""
    otp = generate_otp()
    store_otp(db=db, email=req.email, otp=otp, purpose=req.purpose)
    sent = send_otp_email(to_email=req.email, otp=otp, purpose=req.purpose, name=req.name)
    if not sent:
        raise HTTPException(500, "Failed to send OTP email. Check SMTP settings.")
    return {"success": True, "message": f"OTP sent to {req.email}. Valid for 10 minutes."}


@router.post("/clients/register", response_model=ClientAuthResponse, tags=["Clients"])
async def api_register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Step 2: verify OTP then create account."""
    if not verify_otp(db=db, email=req.email, otp=req.otp, purpose="register"):
        raise HTTPException(400, "Invalid or expired OTP. Please request a new code.")
    result = register_client(name=req.name, email=req.email, password=req.password, db=db)
    if result is None:
        raise HTTPException(409, f"Email '{req.email}' is already registered.")
    # Mark verified immediately (OTP proved email)
    mark_verified(email=req.email, db=db)
    result["is_verified"] = True
    # Welcome notification
    save_notification(
        client_id=result["client_id"], type="system",
        title="Welcome to MR AI RAG!",
        message=f"Your account has been created. Client ID: {result['client_id']}",
        db=db,
    )
    return ClientAuthResponse(
        client_id=result["client_id"], token=result["token"],
        name=result["name"], email=result["email"],
        created_at=result["created_at"],
        message=f"Welcome, {result['name']}! Account verified and ready.",
    )


@router.post("/clients/login", response_model=ClientAuthResponse, tags=["Clients"])
async def api_login(req: LoginRequest, db: Session = Depends(get_db)):
    result = login_client(email=req.email, password=req.password, db=db)
    if result is None:
        raise HTTPException(401, "Invalid email or password.")
    if isinstance(result, dict) and result.get("error") == "not_verified":
        raise HTTPException(403, "Email not verified. Please complete OTP verification.")
    return ClientAuthResponse(
        client_id=result["client_id"], token=result["token"],
        name=result["name"], email=result["email"],
        created_at=result["created_at"],
        message=f"Welcome back, {result['name']}!",
    )


@router.post("/clients/forgot-password", tags=["Clients"])
async def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    from app.core.models import Client
    client = db.query(Client).filter(Client.email == req.email.lower()).first()
    if not client:
        # Don't reveal if email exists
        return {"success": True, "message": "If that email exists, a reset code has been sent."}
    otp = generate_otp()
    store_otp(db=db, email=req.email, otp=otp, purpose="reset")
    send_otp_email(to_email=req.email, otp=otp, purpose="reset", name=client.name)
    return {"success": True, "message": "If that email exists, a reset code has been sent."}


@router.post("/clients/reset-password", tags=["Clients"])
async def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    if not verify_otp(db=db, email=req.email, otp=req.otp, purpose="reset"):
        raise HTTPException(400, "Invalid or expired OTP.")
    ok = update_password(email=req.email, new_password=req.new_password, db=db)
    if not ok:
        raise HTTPException(404, "Email not found.")
    return {"success": True, "message": "Password updated. Please login with your new password."}


@router.get("/clients/me", response_model=ClientProfile, tags=["Clients"])
async def api_get_me(client: dict = Depends(_require_client)):
    return ClientProfile(
        client_id=client["client_id"], name=client["name"], email=client["email"],
        created_at=client.get("created_at", ""), last_login=client.get("last_login", ""),
    )


@router.get("/clients/me/history", response_model=HistoryResponse, tags=["Clients"])
async def api_get_history(client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    msgs = get_chat_history(client["client_id"], db=db)
    return HistoryResponse(
        client_id=client["client_id"], total=len(msgs),
        messages=[ChatMessageOut(**m) for m in msgs],
    )


@router.post("/clients/me/history", tags=["Clients"])
async def api_add_history(
    msg: ChatMessageIn,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    ok = save_chat_message(
        client["client_id"], msg.role, msg.content, msg.sources,
        category=msg.category, source_type=msg.source_type, db=db,
    )
    if not ok:
        raise HTTPException(500, "Failed to save message.")
    return {"success": True}


@router.delete("/clients/me/history", tags=["Clients"])
async def api_clear_history(client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    clear_chat_history(client["client_id"], db=db)
    return {"success": True, "message": "Chat history cleared."}


@router.get("/clients/me/notifications", response_model=NotificationsResponse, tags=["Clients"])
async def api_get_notifications(client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    notifs = get_notifications(client["client_id"], db=db)
    unread = get_unread_count(client["client_id"], db=db)
    return NotificationsResponse(
        total=len(notifs), unread=unread,
        notifications=[NotificationOut(**n) for n in notifs],
    )


@router.post("/clients/me/notifications/read", tags=["Clients"])
async def api_mark_notifications_read(client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    mark_notifications_read(client["client_id"], db=db)
    return {"success": True}


@router.get("/clients/me/keys", tags=["Clients"])
async def api_get_my_keys(client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    """Return all API keys created by this client."""
    keys = list_api_keys(client_id=client["client_id"], db=db)
    return {"client_id": client["client_id"], "total": len(keys), "keys": keys}


# ── Google OAuth Login ────────────────────────────────────────────────────────

class GoogleLoginRequest(BaseModel):
    id_token: str = Field(..., min_length=10)
    name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None

@router.post("/clients/google-login", response_model=ClientAuthResponse, tags=["Clients"])
async def api_google_login(req: GoogleLoginRequest, db: Session = Depends(get_db)):
    """Login or register with a Google id_token from the frontend."""
    import os
    from app.core.models import Client
    from app.core.clients import _generate_token, _generate_client_id

    client_id_env = os.environ.get("GOOGLE_CLIENT_ID", "")

    # Verify token with Google
    name = req.name or "Google User"
    email = req.email or ""
    avatar = req.avatar_url or ""

    if client_id_env:
        try:
            import urllib.request, json as _json
            url = f"https://oauth2.googleapis.com/tokeninfo?id_token={req.id_token}"
            with urllib.request.urlopen(url, timeout=5) as resp:
                payload = _json.loads(resp.read())
            if payload.get("aud") != client_id_env:
                raise HTTPException(401, "Invalid Google token audience.")
            email = payload.get("email", email)
            name = payload.get("name", name)
            avatar = payload.get("picture", avatar)
            if not payload.get("email_verified"):
                raise HTTPException(400, "Google email not verified.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Google token verification failed: {e}")
    else:
        # No GOOGLE_CLIENT_ID set — accept passed email/name (dev/demo mode)
        if not email:
            raise HTTPException(400, "GOOGLE_CLIENT_ID not configured. Pass email directly.")

    # Upsert client
    client = db.query(Client).filter(Client.email == email.lower()).first()
    if client:
        client.token = _generate_token()
        client.last_login = datetime.utcnow()
        client.login_method = "google"
        if avatar:
            client.avatar_url = avatar
        if not client.is_verified:
            client.is_verified = True
    else:
        import secrets
        cid = _generate_client_id()
        while db.query(Client).filter(Client.client_id == cid).first():
            cid = _generate_client_id()
        client = Client(
            client_id=cid, name=name, email=email.lower(),
            password_hash=secrets.token_hex(32),  # random unusable password
            token=_generate_token(), is_verified=True,
            login_method="google", avatar_url=avatar,
            created_at=datetime.utcnow(), last_login=datetime.utcnow(),
        )
        db.add(client)
    db.commit()
    db.refresh(client)
    return ClientAuthResponse(
        client_id=client.client_id, token=client.token,
        name=client.name, email=client.email,
        created_at=client.created_at.isoformat() if client.created_at else "",
        message=f"Welcome, {client.name}! (Google Login)",
    )


# ── QR Code Login ─────────────────────────────────────────────────────────────

@router.get("/clients/me/qr-token", tags=["Clients"])
async def api_get_qr_token(client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    """Generate a short-lived QR login token for the current user. Returns a QR image URL."""
    import secrets
    from app.core.models import QRToken

    token_val = "qr-" + secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(minutes=15)

    qt = QRToken(
        token=token_val,
        client_id=client["client_id"],
        expires_at=expires,
        used=False,
    )
    db.add(qt)
    db.commit()

    login_url = f"/login?qr={token_val}"
    return {
        "token": token_val,
        "login_url": login_url,
        "expires_at": expires.isoformat(),
        "qr_data": login_url,  # frontend generates QR image from this
    }


class QRLoginRequest(BaseModel):
    token: str = Field(..., min_length=5)

@router.post("/clients/qr-login", response_model=ClientAuthResponse, tags=["Clients"])
async def api_qr_login(req: QRLoginRequest, db: Session = Depends(get_db)):
    """Authenticate using a QR token. Returns a fresh session."""
    from app.core.models import QRToken, Client
    from app.core.clients import _generate_token

    qt = db.query(QRToken).filter(QRToken.token == req.token).first()
    if not qt:
        raise HTTPException(404, "QR token not found.")
    if qt.used:
        raise HTTPException(400, "QR token already used.")
    if qt.expires_at < datetime.utcnow():
        raise HTTPException(400, "QR token has expired. Please generate a new one.")

    client = db.query(Client).filter(Client.client_id == qt.client_id).first()
    if not client:
        raise HTTPException(404, "User not found.")

    # Mark used + update login
    qt.used = True
    client.token = _generate_token()
    client.last_login = datetime.utcnow()
    client.login_method = "qr"
    db.commit()
    db.refresh(client)

    return ClientAuthResponse(
        client_id=client.client_id, token=client.token,
        name=client.name, email=client.email,
        created_at=client.created_at.isoformat() if client.created_at else "",
        message=f"Welcome, {client.name}! (QR Login)",
    )
