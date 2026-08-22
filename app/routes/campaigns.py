"""
MR AI RAG - Outbound Call Campaign Routes
Provides CRUD operations, run/pause controls, and report endpoints for call campaigns.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.models import Campaign, CampaignLead, Agent, Client
from app.services.kyc_service import run_full_kyc

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Auth Helper ───────────────────────────────────────────────────────────────

def _get_client_from_token(token: str, db: Session) -> Optional[Client]:
    """Validate X-App-Token and return the corresponding Client."""
    if not token:
        return None
    return db.query(Client).filter(Client.token == token).first()


# ── Campaign CRUD ─────────────────────────────────────────────────────────────

@router.post("/campaigns", summary="Create a new outbound call campaign")
async def create_campaign(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Create a new campaign. Expects JSON body:
    {
        "name": "Loan Recovery Q3",
        "agent_id": "agent-xxx",
        "did_number": "+919876543210",
        "goal": "Collect overdue EMI or schedule callback",
        "contacts": [
            {"phone": "9876543210", "name": "Rahul Sharma"},
            {"phone": "8765432109"}
        ]
    }
    """
    token = request.headers.get("X-App-Token", "")
    client = _get_client_from_token(token, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or missing X-App-Token")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    name = data.get("name", "").strip()
    agent_id = data.get("agent_id", "").strip()
    did_number = data.get("did_number", "").strip()
    goal = data.get("goal", "").strip()
    contacts = data.get("contacts", [])

    if not name:
        raise HTTPException(status_code=400, detail="Campaign name is required.")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required.")
    if not did_number:
        raise HTTPException(status_code=400, detail="did_number (caller ID) is required.")
    if not contacts or not isinstance(contacts, list):
        raise HTTPException(status_code=400, detail="contacts list is required and must be non-empty.")

    # Validate agent belongs to this client
    agent = db.query(Agent).filter(
        Agent.agent_id == agent_id,
        Agent.client_id == client.client_id
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or does not belong to your account.")

    # Create campaign
    campaign_id = f"camp_{uuid.uuid4().hex[:16]}"
    campaign = Campaign(
        campaign_id=campaign_id,
        client_id=client.client_id,
        agent_id=agent_id,
        name=name,
        did_number=did_number,
        goal=goal,
        status="draft",
        total_leads=len(contacts)
    )
    db.add(campaign)
    db.flush()  # Get the campaign record committed so we can FK reference it

    # Create leads
    for contact in contacts:
        phone = str(contact.get("phone", "")).strip()
        cust_name = str(contact.get("name", "")).strip()
        if not phone:
            continue
        lead = CampaignLead(
            campaign_id=campaign_id,
            phone_number=phone,
            customer_name=cust_name or None,
            status="pending"
        )
        db.add(lead)

    db.commit()
    db.refresh(campaign)
    logger.info(f"Campaign created: {campaign_id} | {name} | {len(contacts)} leads")
    return {"success": True, "campaign": campaign.to_dict()}


@router.get("/campaigns", summary="List all campaigns for this client")
async def list_campaigns(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status: draft|running|paused|completed"),
    db: Session = Depends(get_db)
):
    token = request.headers.get("X-App-Token", "")
    client = _get_client_from_token(token, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or missing X-App-Token")

    query = db.query(Campaign).filter(Campaign.client_id == client.client_id)
    if status:
        query = query.filter(Campaign.status == status)
    campaigns = query.order_by(Campaign.created_at.desc()).all()

    return {
        "success": True,
        "campaigns": [c.to_dict() for c in campaigns],
        "total": len(campaigns)
    }


@router.get("/campaigns/{campaign_id}", summary="Get a single campaign with full lead details")
async def get_campaign(
    campaign_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    token = request.headers.get("X-App-Token", "")
    client = _get_client_from_token(token, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or missing X-App-Token")

    campaign = db.query(Campaign).filter(
        Campaign.campaign_id == campaign_id,
        Campaign.client_id == client.client_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    return {
        "success": True,
        "campaign": campaign.to_dict(),
        "leads": [l.to_dict() for l in campaign.leads]
    }


@router.delete("/campaigns/{campaign_id}", summary="Delete a campaign")
async def delete_campaign(
    campaign_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    token = request.headers.get("X-App-Token", "")
    client = _get_client_from_token(token, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or missing X-App-Token")

    campaign = db.query(Campaign).filter(
        Campaign.campaign_id == campaign_id,
        Campaign.client_id == client.client_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if campaign.status == "running":
        raise HTTPException(status_code=400, detail="Cannot delete a running campaign. Pause it first.")

    db.delete(campaign)
    db.commit()
    return {"success": True, "message": "Campaign deleted."}


# ── Campaign Execution ────────────────────────────────────────────────────────

@router.post("/campaigns/{campaign_id}/run", summary="Start or resume running a campaign")
async def run_campaign(
    campaign_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Kicks off the outbound dialing process in the background.
    Calls pending leads sequentially, with a configurable delay between calls.
    """
    token = request.headers.get("X-App-Token", "")
    client = _get_client_from_token(token, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or missing X-App-Token")

    campaign = db.query(Campaign).filter(
        Campaign.campaign_id == campaign_id,
        Campaign.client_id == client.client_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if campaign.status == "running":
        raise HTTPException(status_code=400, detail="Campaign is already running.")
    if campaign.status == "completed":
        raise HTTPException(status_code=400, detail="Campaign is already completed.")

    # Derive the server base URL dynamically from the incoming request
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    scheme = request.headers.get("x-forwarded-proto") or "https"
    server_url = f"{scheme}://{host}"

    # If local, override with the domain of VOBIZ_OUTBOUND_REDIRECT_URL if it is public
    from urllib.parse import urlparse
    from app.core.config import settings
    if "localhost" in host or "127.0.0.1" in host or "0.0.0.0" in host:
        if settings.VOBIZ_OUTBOUND_REDIRECT_URL:
            try:
                parsed = urlparse(settings.VOBIZ_OUTBOUND_REDIRECT_URL)
                if parsed.scheme and parsed.netloc and "localhost" not in parsed.netloc and "127.0.0.1" not in parsed.netloc:
                    server_url = f"{parsed.scheme}://{parsed.netloc}"
                    logger.info(f"Overrode local server_url with public Vobiz redirect domain: {server_url}")
            except Exception as parse_ex:
                logger.warning(f"Failed to parse VOBIZ_OUTBOUND_REDIRECT_URL: {parse_ex}")

    # Mark campaign as running
    campaign.status = "running"
    db.commit()

    # Launch background dialing task
    background_tasks.add_task(
        _dial_all_leads,
        campaign_id=campaign_id,
        agent_id=campaign.agent_id,
        server_url=server_url
    )

    pending_count = db.query(CampaignLead).filter(
        CampaignLead.campaign_id == campaign_id,
        CampaignLead.status == "pending"
    ).count()

    logger.info(f"Campaign {campaign_id} started. {pending_count} pending leads to dial.")
    return {
        "success": True,
        "message": f"Campaign started. Dialing {pending_count} pending contacts in the background.",
        "campaign_id": campaign_id,
        "pending_leads": pending_count
    }


@router.post("/campaigns/{campaign_id}/pause", summary="Pause a running campaign")
async def pause_campaign(
    campaign_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    token = request.headers.get("X-App-Token", "")
    client = _get_client_from_token(token, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or missing X-App-Token")

    campaign = db.query(Campaign).filter(
        Campaign.campaign_id == campaign_id,
        Campaign.client_id == client.client_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    campaign.status = "paused"
    db.commit()
    return {"success": True, "message": "Campaign paused. Remaining pending leads will not be dialed."}


# ── Campaign Report ───────────────────────────────────────────────────────────

@router.get("/campaigns/{campaign_id}/report", summary="Get campaign analytics report")
async def campaign_report(
    campaign_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Returns aggregate statistics and per-lead details for a campaign.
    """
    token = request.headers.get("X-App-Token", "")
    client = _get_client_from_token(token, db)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or missing X-App-Token")

    campaign = db.query(Campaign).filter(
        Campaign.campaign_id == campaign_id,
        Campaign.client_id == client.client_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    leads = campaign.leads
    total = len(leads)
    answered = [l for l in leads if l.status == "answered"]
    no_answer = [l for l in leads if l.status == "no_answer"]
    busy = [l for l in leads if l.status == "busy"]
    failed = [l for l in leads if l.status == "failed"]
    pending = [l for l in leads if l.status == "pending"]

    avg_duration = (
        sum(l.call_duration for l in answered) // len(answered)
        if answered else 0
    )
    kyc_verified = sum(1 for l in leads if l.verification_status == "verified")
    kyc_rejected = sum(1 for l in leads if l.verification_status == "rejected")
    whatsapp_sent = sum(1 for l in leads if l.whatsapp_sent)
    email_sent = sum(1 for l in leads if l.email_sent)

    return {
        "success": True,
        "report": {
            "campaign_id":      campaign.campaign_id,
            "name":             campaign.name,
            "status":           campaign.status,
            "total_leads":      total,
            "answered":         len(answered),
            "no_answer":        len(no_answer),
            "busy":             len(busy),
            "failed":           len(failed),
            "pending":          len(pending),
            "answer_rate":      f"{(len(answered) / total * 100):.1f}%" if total > 0 else "0%",
            "avg_call_duration_sec": avg_duration,
            "kyc_verified":     kyc_verified,
            "kyc_rejected":     kyc_rejected,
            "whatsapp_sent":    whatsapp_sent,
            "email_sent":       email_sent,
        },
        "leads": [l.to_dict() for l in leads]
    }


# ── KYC Webhook (called from telephony action triggers) ───────────────────────

@router.post("/campaigns/{campaign_id}/leads/{lead_id}/verify", summary="Run KYC verification for a campaign lead")
async def verify_lead_kyc(
    campaign_id: str,
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Trigger KYC verification (Aadhar + PAN + CIBIL) for a specific lead.
    Can be called by the telephony action handler after collecting details on-call.
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    lead = db.query(CampaignLead).filter(
        CampaignLead.id == lead_id,
        CampaignLead.campaign_id == campaign_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    aadhar = data.get("aadhar")
    pan = data.get("pan")
    dob = data.get("dob")

    campaign = db.query(Campaign).filter(Campaign.campaign_id == campaign_id).first()
    cibil_threshold = 650
    if campaign:
        import json
        try:
            agent = db.query(Agent).filter(Agent.agent_id == campaign.agent_id).first()
            if agent:
                a_cfg = json.loads(agent.action_config_json or "{}")
                cibil_threshold = int(a_cfg.get("cibil_threshold", 650))
        except Exception:
            pass

    kyc_result = run_full_kyc(aadhar=aadhar, pan=pan, dob=dob, cibil_threshold=cibil_threshold)

    import json
    lead.verification_status = kyc_result.get("overall_status", "n/a")
    lead.verification_result = json.dumps(kyc_result)
    db.commit()

    return {
        "success": True,
        "lead_id": lead_id,
        "verification_status": lead.verification_status,
        "kyc_result": kyc_result
    }


# ── Background Dialing Task ───────────────────────────────────────────────────

async def _dial_all_leads(campaign_id: str, agent_id: str, server_url: str):
    """
    Background task: dials all pending leads in a campaign sequentially.
    Uses the existing trigger_outbound_call function from telephony_service.
    """
    from app.core.database import get_session_local
    from app.services.telephony_service import trigger_outbound_call

    db = get_session_local()()
    try:
        campaign = db.query(Campaign).filter(Campaign.campaign_id == campaign_id).first()
        if not campaign:
            return

        pending_leads = db.query(CampaignLead).filter(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.status == "pending"
        ).all()

        for lead in pending_leads:
            # Re-check if campaign was paused/stopped externally
            db.refresh(campaign)
            if campaign.status not in ("running",):
                logger.info(f"Campaign {campaign_id} paused/stopped. Stopping dialer.")
                break

            lead.status = "dialing"
            db.commit()

            # Build the callback URL the telephony engine will POST to when call is answered
            callback_url = (
                f"{server_url}/api/telephony/outbound-flow"
                f"?agent_id={agent_id}&campaign_id={campaign_id}&lead_id={lead.id}"
            )

            try:
                result = await trigger_outbound_call(
                    to_phone=lead.phone_number,
                    callback_url=callback_url
                )
                if result.get("status") == "success":
                    logger.info(f"[Campaign {campaign_id}] Dialing {lead.phone_number} — call triggered OK.")
                    # Status will be updated to answered/no_answer by the telephony webhook
                else:
                    lead.status = "failed"
                    lead.call_summary = result.get("message", "Dial failed.")
                    db.commit()
                    logger.warning(f"[Campaign {campaign_id}] Failed to dial {lead.phone_number}: {result}")
            except Exception as ex:
                lead.status = "failed"
                lead.call_summary = str(ex)
                db.commit()
                logger.error(f"[Campaign {campaign_id}] Exception dialing {lead.phone_number}: {ex}")

            # Wait between calls to avoid flooding the telephony provider
            await asyncio.sleep(5)

        # Check if all leads are done
        db.refresh(campaign)
        still_pending = db.query(CampaignLead).filter(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.status.in_(["pending", "dialing"])
        ).count()

        if still_pending == 0 and campaign.status == "running":
            campaign.status = "completed"
            db.commit()
            logger.info(f"Campaign {campaign_id} completed. All leads processed.")

    except Exception as e:
        logger.error(f"Campaign dialer exception for {campaign_id}: {e}", exc_info=True)
        try:
            campaign = db.query(Campaign).filter(Campaign.campaign_id == campaign_id).first()
            if campaign and campaign.status == "running":
                campaign.status = "paused"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
