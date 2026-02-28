"""
MR AI RAG - Admin Routes
POST /api/admin/login         → Admin login
GET  /api/admin/clients       → List all clients
GET  /api/admin/clients/{id}  → Get single client
PUT  /api/admin/clients/{id}  → Update client
DELETE /api/admin/clients/{id} → Delete client
POST /api/admin/login-as/{id} → Login as client (impersonation)
POST /api/admin/clients/{id}/reset-password → Reset client password
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.admin import (
    login_admin, validate_admin_token,
    admin_list_clients, admin_get_client,
    admin_update_client, admin_delete_client,
    admin_login_as_client, admin_reset_client_password,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class AdminLoginResponse(BaseModel):
    token: str
    username: str
    message: str

class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    is_verified: Optional[bool] = None

class ClientResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6)

class AdminCreateClientRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=200)
    password: str = Field(..., min_length=6, max_length=200)


# ── Auth dependency ───────────────────────────────────────────────────────────

def _require_admin(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not x_admin_token:
        raise HTTPException(401, "Missing X-Admin-Token header.")
    admin = validate_admin_token(x_admin_token, db=db)
    if not admin:
        raise HTTPException(401, "Invalid or expired admin token. Please login again.")
    return admin


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/admin/login", response_model=AdminLoginResponse, tags=["Admin"])
async def admin_login(req: AdminLoginRequest, db: Session = Depends(get_db)):
    """Admin login — returns admin session token."""
    result = login_admin(req.username, req.password, db=db)
    if not result:
        raise HTTPException(401, "Invalid username or password.")
    return AdminLoginResponse(
        token=result["token"],
        username=result["username"],
        message=f"Welcome, {result['username']}!",
    )


@router.get("/admin/me", tags=["Admin"])
async def admin_me(admin: dict = Depends(_require_admin)):
    """Check who is currently logged in as admin."""
    return {"username": admin["username"], "created_at": admin["created_at"]}


@router.get("/admin/clients", tags=["Admin"])
async def admin_get_clients(admin: dict = Depends(_require_admin), db: Session = Depends(get_db)):
    """List all clients with counts."""
    clients = admin_list_clients(db=db)
    return {"total": len(clients), "clients": clients}


@router.post("/admin/clients/create-direct", tags=["Admin"])
async def admin_create_client_direct(
    req: AdminCreateClientRequest,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Directly create a verified client without OTP — admin only."""
    from app.core.clients import register_client, mark_verified
    result = register_client(name=req.name, email=req.email, password=req.password, db=db)
    if result is None:
        raise HTTPException(409, f"Email '{req.email}' is already registered.")
    mark_verified(email=req.email, db=db)
    result["is_verified"] = True
    return {"success": True, "message": f"Client '{req.name}' created and verified.", "client": result}


@router.get("/admin/clients/{client_id}", tags=["Admin"])
async def admin_get_single_client(client_id: str, admin: dict = Depends(_require_admin), db: Session = Depends(get_db)):
    c = admin_get_client(client_id, db=db)
    if not c:
        raise HTTPException(404, f"Client '{client_id}' not found.")
    return c


@router.put("/admin/clients/{client_id}", tags=["Admin"])
async def admin_edit_client(
    client_id: str,
    req: ClientUpdateRequest,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Update client name, email, or verified status."""
    result = admin_update_client(
        client_id,
        name=req.name, email=req.email, is_verified=req.is_verified,
        db=db,
    )
    if not result:
        raise HTTPException(404, f"Client '{client_id}' not found.")
    return {"success": True, "client": result}


@router.delete("/admin/clients/{client_id}", tags=["Admin"])
async def admin_remove_client(
    client_id: str,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Delete a client and all their data (cascades)."""
    ok = admin_delete_client(client_id, db=db)
    if not ok:
        raise HTTPException(404, f"Client '{client_id}' not found.")
    return {"success": True, "message": f"Client {client_id} deleted."}


@router.post("/admin/login-as/{client_id}", tags=["Admin"])
async def admin_impersonate(
    client_id: str,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Generate a fresh session token for a client. Admin can then login as them."""
    result = admin_login_as_client(client_id, db=db)
    if not result:
        raise HTTPException(404, f"Client '{client_id}' not found.")
    logger.warning(f"Admin '{admin['username']}' is impersonating client {client_id}")
    return {
        "success": True,
        "client_id": result["client_id"],
        "token": result["token"],
        "name": result["name"],
        "email": result["email"],
        "message": f"Logged in as {result['name']}",
    }


@router.post("/admin/clients/{client_id}/reset-password", tags=["Admin"])
async def admin_reset_password(
    client_id: str,
    req: ClientResetPasswordRequest,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    ok = admin_reset_client_password(client_id, req.new_password, db=db)
    if not ok:
        raise HTTPException(404, f"Client '{client_id}' not found.")
    return {"success": True, "message": "Password reset successfully."}
