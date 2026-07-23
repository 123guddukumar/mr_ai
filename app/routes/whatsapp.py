"""
MR AI RAG - WhatsApp Webhook & Messaging Router
"""

import logging
import httpx
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.routes.agents import api_agent_public_ask, AgentPublicAskReq

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/whatsapp/webhook", response_class=PlainTextResponse, summary="Verify WhatsApp webhook callback")
async def verify_whatsapp_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    challenge: Optional[str] = Query(None, alias="hub.challenge"),
    verify_token: Optional[str] = Query(None, alias="hub.verify_token")
):
    """
    Handles the Meta verification handshake.
    """
    logger.info(f"Received WhatsApp verification request: mode={mode}, challenge={challenge}, token={verify_token}")
    
    if mode == "subscribe" and verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp Webhook verified successfully!")
        return challenge
        
    logger.warning("WhatsApp Webhook verification failed due to token mismatch.")
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@router.post("/whatsapp/webhook", summary="Receive WhatsApp messages and status updates")
async def receive_whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives incoming WhatsApp messages, delegates to the configured AI agent, and sends replies back.
    """
    try:
        payload = await request.json()
        logger.debug(f"Received WhatsApp webhook payload: {payload}")
        
        # Verify if payload is a status update (delivered, read, etc.) or a user message
        entry = payload.get("entry", [])
        if not entry:
            return {"status": "ignored", "reason": "empty entry"}
            
        changes = entry[0].get("changes", [])
        if not changes:
            return {"status": "ignored", "reason": "empty changes"}
            
        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages:
            # Most likely a delivery or read receipt status event; ignore to avoid loops
            return {"status": "ignored", "reason": "no messages in payload"}
            
        message = messages[0]
        msg_type = message.get("type")
        
        # Only process text messages currently
        if msg_type != "text":
            logger.info(f"Ignored non-text WhatsApp message of type: {msg_type}")
            return {"status": "ignored", "reason": "unsupported message type"}
            
        from_phone = message.get("from")  # User phone number (e.g. "919876543210")
        text_body = message.get("text", {}).get("body", "").strip()
        
        if not from_phone or not text_body:
            return {"status": "ignored", "reason": "missing from_phone or text body"}
            
        profile_name = "WhatsApp User"
        contacts = value.get("contacts", [])
        if contacts:
            profile_name = contacts[0].get("profile", {}).get("name", "WhatsApp User")
            
        # Call the existing public ask API logic of our agent.
        # Use prefix "wa_" with phone number as session_id to maintain distinct session history in database.
        req_ask = AgentPublicAskReq(
            question=text_body,
            session_id=f"wa_{from_phone}",
            device_id=from_phone,
            device_name=f"WhatsApp ({profile_name})"
        )
        
        agent_id = settings.WHATSAPP_DEFAULT_AGENT_ID
        logger.info(f"Processing WhatsApp msg from {from_phone} using Agent {agent_id}: '{text_body}'")
        
        # Delegate conversation to the agent endpoint (RAG, database logs, meeting schedules, lead captures run automatically)
        response = await api_agent_public_ask(agent_id=agent_id, req=req_ask, db=db)
        ai_answer = response.get("answer", "")
        
        if ai_answer:
            # Send response back to the sender's WhatsApp
            success = await send_whatsapp_message(from_phone, ai_answer)
            if success:
                logger.info(f"Successfully replied to {from_phone} on WhatsApp.")
            else:
                logger.error(f"Failed to deliver WhatsApp reply to {from_phone}.")
        else:
            logger.warning(f"Agent did not return a response for message: '{text_body}'")
            
        return {"status": "success"}
        
    except Exception as e:
        logger.error(f"Error processing WhatsApp webhook: {e}", exc_info=True)
        # Always return 200/OK to WhatsApp to prevent Meta from retrying failed request
        return {"status": "error", "detail": str(e)}


async def send_whatsapp_message(to_phone: str, text: str) -> bool:
    """
    Sends a text message reply to a WhatsApp user via Meta Cloud API.
    """
    if not settings.WHATSAPP_PHONE_NUMBER_ID or not settings.WHATSAPP_ACCESS_TOKEN:
        logger.error("WhatsApp Integration credentials (Phone Number ID or Access Token) are missing in settings.")
        return False
        
    url = f"https://graph.facebook.com/v18.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=15.0)
            if resp.status_code in (200, 201):
                return True
            else:
                logger.error(f"Meta WhatsApp API returned error code {resp.status_code}: {resp.text}")
                return False
        except Exception as err:
            logger.error(f"HTTP exception during WhatsApp send: {err}")
            return False
