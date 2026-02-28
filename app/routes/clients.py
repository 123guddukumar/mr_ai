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
