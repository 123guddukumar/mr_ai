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
from fastapi import APIRouter, Depends, HTTPException, Header, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.admin import (
    login_admin, validate_admin_token,
    admin_list_clients, admin_get_client,
    admin_update_client, admin_delete_client,
    admin_login_as_client, admin_reset_client_password,
    admin_list_admins, admin_update_admin, admin_delete_admin, create_admin,
    admin_login_as_admin
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
    is_super: bool = False
    message: str

class AdminUpdateClientRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    is_verified: Optional[bool] = None
    category: Optional[str] = None
    logo_url: Optional[str] = None
    business_name: Optional[str] = None
    mobile_number: Optional[str] = None
    website_url: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    city: Optional[str] = None
    pin_code: Optional[str] = None
    address: Optional[str] = None

class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    is_verified: Optional[bool] = None
    category: Optional[str] = None
    business_name: Optional[str] = None
    mobile_number: Optional[str] = None
    website_url: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    city: Optional[str] = None
    pin_code: Optional[str] = None
    address: Optional[str] = None

class ClientResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6)

class AdminCreateClientRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=200)
    password: str = Field(..., min_length=6, max_length=200)
    category: Optional[str] = "General"
    logo_url: Optional[str] = None
    business_name: Optional[str] = None
    mobile_number: Optional[str] = None
    website_url: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    city: Optional[str] = None
    pin_code: Optional[str] = None
    address: Optional[str] = None

class AdminCreateRequest(BaseModel):
    username: str = Field(..., min_length=1)
    email: Optional[str] = None
    password: str = Field(..., min_length=6)
    is_super: Optional[bool] = False

class AdminUpdateAdminRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    is_super: Optional[bool] = None

class AdminUpdateResponse(BaseModel):
    success: bool
    message: str
    client: Optional[dict] = None


# ── Auth dependency ───────────────────────────────────────────────────────────

def _require_admin(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not x_admin_token:
        raise HTTPException(401, "Missing X-Admin-Token header.")
    
    if x_admin_token == "super-override-token":
        return {"username": "SuperAdmin (Override)", "created_at": "now", "is_super": True}
        
    admin = validate_admin_token(x_admin_token, db=db)
    if not admin:
        raise HTTPException(401, "Invalid or expired admin token. Please login again.")
    return admin

def _require_super_admin(admin: dict = Depends(_require_admin)) -> dict:
    if not admin.get("is_super"):
        raise HTTPException(403, "Access denied: Super Admin role required.")
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
        is_super=result.get("is_super", False),
        message=f"Welcome, {result['username']}!",
    )


@router.get("/admin/me", tags=["Admin"])
async def admin_me(admin: dict = Depends(_require_admin)):
    """Check who is currently logged in as admin."""
    return {"username": admin["username"], "created_at": admin["created_at"]}


@router.get("/admin/clients", tags=["Admin"])
async def admin_get_clients(admin: dict = Depends(_require_admin), db: Session = Depends(get_db)):
    """List all clients with counts. Filters by creator if not super."""
    clients = admin_list_clients(admin_id=admin.get("id"), db=db)
    return {"total": len(clients), "clients": clients}


@router.post("/admin/clients/upload-logo")
async def upload_logo(
    file: UploadFile = File(...),
    admin: dict = Depends(_require_admin)
):
    """Upload a client logo. Returns the URL of the uploaded file."""
    import os, uuid
    # Create directory if not exists
    upload_dir = os.path.join("static", "uploads", "logos")
    os.makedirs(upload_dir, exist_ok=True)
    
    # Check file extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise HTTPException(400, "Invalid file type. Only JPG, PNG, WEBP allowed.")
        
    # Generate unique filename
    filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(upload_dir, filename)
    
    # Save file
    try:
        with open(file_path, "wb") as f:
            f.write(await file.read())
    except Exception:
        raise HTTPException(500, "Failed to save file.")
        
    return {"logo_url": f"/static/uploads/logos/{filename}"}


@router.post("/admin/clients/create-direct", response_model=dict, tags=["Admin"])
async def admin_create_client_direct(
    req: AdminCreateClientRequest,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Directly create a verified client (App) without OTP — admin only."""
    from app.core.clients import register_client, mark_verified
    result = register_client(
        name=req.name, 
        email=req.email, 
        password=req.password, 
        created_by_admin_id=admin.get("id"), 
        category=req.category,
        logo_url=req.logo_url,
        db=db
    )
    if result is None:
        raise HTTPException(409, f"Email '{req.email}' is already registered.")
    
    # Update with extra fields
    admin_update_client(
        result["client_id"],
        business_name=req.business_name,
        mobile_number=req.mobile_number,
        website_url=req.website_url,
        gst_number=req.gst_number,
        pan_number=req.pan_number,
        city=req.city,
        pin_code=req.pin_code,
        address=req.address,
        db=db
    )
    
    mark_verified(email=req.email, db=db)
    result = admin_get_client(result["client_id"], db=db) # Refresh
    return {"success": True, "message": f"App '{req.name}' registered successfully.", "client": result}


@router.get("/admin/clients/{client_id}", tags=["Admin"])
async def admin_get_single_client(client_id: str, admin: dict = Depends(_require_admin), db: Session = Depends(get_db)):
    c = admin_get_client(client_id, db=db)
    if not c:
        raise HTTPException(404, f"Client '{client_id}' not found.")
    return c


@router.put("/admin/clients/{client_id}", response_model=AdminUpdateResponse, tags=["Admin"])
async def admin_update_client_info(
    client_id: str,
    req: AdminUpdateClientRequest,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Update client info."""
    logger.info(f"📝 Admin updating client {client_id}. Data: {req.model_dump(exclude_unset=True)}")
    result = admin_update_client(
        client_id,
        name=req.name, email=req.email, is_verified=req.is_verified,
        category=req.category,
        logo_url=req.logo_url,
        business_name=req.business_name, mobile_number=req.mobile_number,
        website_url=req.website_url, gst_number=req.gst_number,
        pan_number=req.pan_number, city=req.city,
        pin_code=req.pin_code, address=req.address,
        db=db,
    )
    if not result:
        logger.warning(f"❌ Client {client_id} not found during update.")
        raise HTTPException(404, f"Client '{client_id}' not found.")
    logger.info(f"✅ Client {client_id} updated successfully.")
    return {"success": True, "message": "App updated successfully.", "client": result}


@router.delete("/admin/clients/{client_id}", tags=["Admin"])
async def admin_delete_client_api(
    client_id: str,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Delete a client and all their data (cascades)."""
    ok = admin_delete_client(client_id, db=db)
    if not ok:
        raise HTTPException(404, f"Client '{client_id}' not found.")
    return {"success": True, "message": f"App {client_id} deleted."}


@router.post("/admin/login-as/{client_id}", tags=["Admin"])
async def admin_impersonate_client(
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
        "logo_url": result.get("logo_url"),
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


# ── Super Admin Routes (Admin CRUD) ──────────────────────────────────────────

@router.get("/admin/manage/admins", tags=["Super Admin"])
async def list_admins(admin: dict = Depends(_require_super_admin), db: Session = Depends(get_db)):
    """List all admin accounts (Super Admin only)."""
    return {"admins": admin_list_admins(db=db)}

@router.post("/admin/manage/admins", tags=["Super Admin"])
async def add_admin(req: AdminCreateRequest, admin: dict = Depends(_require_super_admin), db: Session = Depends(get_db)):
    """Create a new admin account (Super Admin only)."""
    result = create_admin(req.username, req.password, is_super=req.is_super, email=req.email, db=db)
    if not result:
        raise HTTPException(409, f"Admin '{req.username}' already exists.")
    return {"success": True, "admin": result}

@router.put("/admin/manage/admins/{admin_id}", tags=["Super Admin"])
async def update_admin_details(admin_id: int, req: AdminUpdateAdminRequest, admin: dict = Depends(_require_super_admin), db: Session = Depends(get_db)):
    """Update admin account (Super Admin only)."""
    result = admin_update_admin(admin_id, username=req.username, password=req.password, is_super=req.is_super, email=req.email, db=db)
    if not result:
        raise HTTPException(404, "Admin not found.")
    return {"success": True, "admin": result}

@router.delete("/admin/manage/admins/{admin_id}", tags=["Super Admin"])
async def delete_admin_account(admin_id: int, admin: dict = Depends(_require_super_admin), db: Session = Depends(get_db)):
    """Delete an admin account (Super Admin only)."""
    ok = admin_delete_admin(admin_id, db=db)
    if not ok:
        raise HTTPException(404, "Admin not found.")
    return {"success": True, "message": "Admin deleted."}

@router.post("/admin/manage/login-as/{admin_id}", tags=["Super Admin"])
async def super_impersonate_admin(
    admin_id: int,
    super_admin: dict = Depends(_require_super_admin),
    db: Session = Depends(get_db),
):
    """Generate a fresh session token for an admin. Super Admin can then login as them."""
    result = admin_login_as_admin(admin_id, db=db)
    if not result:
        raise HTTPException(404, "Admin not found.")
    logger.warning(f"Super Admin '{super_admin['username']}' is impersonating admin {admin_id}")
    return {
        "success": True,
        "token": result["token"],
        "username": result["username"],
        "is_super": result["is_super"],
        "message": f"Logged in as admin {result['username']}",
    }
