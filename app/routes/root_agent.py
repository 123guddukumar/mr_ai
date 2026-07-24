"""
MR AI RAG v2 - Root Personal Assistant Agent Routes 👑
Handles Root Agent creation, Owner authentication, Personal Memory notes,
Meeting scheduling with 30-min reminders, Agent audit history (top 5 users pagination),
Media Vault (images, videos, documents), and Daily Planner.
"""

import os
import json
import logging
import secrets
import re
from datetime import datetime, timedelta, date as date_type
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Form, Request, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.database import get_db
from app.core.models import (
    Agent, AgentPublicSession, AgentPublicMessage, Client, Notification,
    RootMemory, RootMeeting, RootMedia, RootDailyPlan
)
from app.core.clients import validate_client_token
from app.services.llm import generate_answer, set_runtime_provider, get_active_api_key
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Auth Helper ───────────────────────────────────────────────────────────────

def _get_owner_client(x_app_token: Optional[str], db: Session) -> dict:
    if x_app_token:
        client = validate_client_token(x_app_token)
        if client:
            return client
    first_client = db.query(Client).first()
    if first_client:
        return {"client_id": first_client.client_id, "email": first_client.email}
    raise HTTPException(status_code=401, detail="Invalid or expired token")


# ── Request Schemas ───────────────────────────────────────────────────────────

class RootChatReq(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []
    session_id: Optional[str] = None
    target_agent_id: Optional[str] = None
    offset: Optional[int] = 0

class SaveMemoryReq(BaseModel):
    title: str
    content: str
    category: Optional[str] = "note"
    tags: Optional[List[str]] = []

class ScheduleMeetingReq(BaseModel):
    title: str
    description: Optional[str] = ""
    meeting_time: str
    duration_mins: Optional[int] = 30


# ── Ensure Root Agent Endpoint ────────────────────────────────────────────────

@router.post("/root-agent/ensure")
@router.get("/root-agent/agent")
async def get_or_create_root_agent(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Retrieves or automatically initializes the Root Personal Assistant Agent for the owner."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    agent = db.query(Agent).filter(
        Agent.client_id == client_id,
        Agent.is_root == True
    ).first()

    if not agent:
        root_id = secrets.token_hex(8)
        agent = Agent(
            agent_id=root_id,
            client_id=client_id,
            name="Personal Assistant 👑",
            description="Root Personal AI Assistant with full system control, memory, meeting scheduler, and media vault.",
            category="root_assistant",
            personality="Authoritative, deeply loyal, highly efficient executive assistant. Responds respectfully with 'Sir'.",
            starting_message="Namaste Sir! Main aapka Root Personal Assistant hoon 👑. Main aapke sabhi Agents, Visitor Histories, Meetings, Notes, aur Media Vault ka full access aur management rakhta hoon. Aaj main aapki kya sewa karoon?",
            voice_config_json=json.dumps({"provider": "elevenlabs", "voice_name": "Adam"}),
            system_config_json=json.dumps({
                "provider": settings.LLM_PROVIDER,
                "model": settings.GEMINI_MODEL if settings.LLM_PROVIDER == "gemini" else "default",
                "system_prompt": "You are the Root Personal Assistant for the owner. You have supreme access to all sub-agents, system databases, notes, and media vault. Address the user as Sir."
            }),
            customization_json=json.dumps({
                "badge": "👑 ROOT AGENT",
                "king_icon": True,
                "color": "#eab308"
            }),
            datastores_json="[]",
            is_root=True,
            is_active=True,
            created_at=datetime.utcnow()
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        logger.info(f"👑 Root Personal Assistant Agent created for client {client_id}")

    return agent.to_dict()


# ── Helper: Parse Date & Title for Meetings ───────────────────────────────────

def _parse_meeting_details(msg_raw: str) -> tuple[str, datetime]:
    msg_lower = msg_raw.lower()
    
    # 1. Date Calculation
    now = datetime.utcnow()
    meeting_dt = now + timedelta(days=1)  # Default tomorrow
    
    if any(k in msg_lower for k in ["aaj", "today"]):
        meeting_dt = now + timedelta(hours=3)
    elif any(k in msg_lower for k in ["kal", "kaal", "tomorrow"]):
        meeting_dt = now + timedelta(days=1)
        meeting_dt = meeting_dt.replace(hour=10, minute=0, second=0, microsecond=0)
    
    # Try parsing time e.g. "4 pm", "5 baje", "11:30"
    time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?', msg_lower)
    if time_match:
        try:
            hr = int(time_match.group(1))
            mn = int(time_match.group(2)) if time_match.group(2) else 0
            ampm = time_match.group(3) or ''
            if 'pm' in ampm and hr < 12: hr += 12
            elif 'am' in ampm and hr == 12: hr = 0
            meeting_dt = meeting_dt.replace(hour=hr, minute=mn, second=0, microsecond=0)
        except Exception:
            pass

    # 2. Clean Title Extraction
    title = "Business Meeting"
    if "ke sath" in msg_lower or "ke saath" in msg_lower or "with" in msg_lower or "se" in msg_lower:
        match = re.search(r'(?:h|hai|h|par|bhi)?\s*(.*?)\s*(?:ke s|ke sa|with|se)\s*(.*?)(?:save|schedule|set|kar|$)', msg_raw, re.IGNORECASE)
        if match:
            target = match.group(2).strip() or match.group(1).strip()
            # Clean keywords
            target = re.sub(r'^(h|hai|meeting|mera|meri|aaj|kal|kaal)\s+', '', target, flags=re.IGNORECASE).strip()
            if target:
                title = f"Meeting with {target}"
    
    if title == "Business Meeting" and len(msg_raw) > 5:
        # Clean string as title
        clean = re.sub(r'^(mera|meri|kaal|kal|aaj|h|hai|meeting|save|kr|kar|do|set)\s+', '', msg_raw, flags=re.IGNORECASE).strip()
        if clean:
            title = f"Meeting: {clean[:40]}"

    return title, meeting_dt


# ── Root Agent Interactive Chat Engine ────────────────────────────────────────

@router.post("/root-agent/chat")
async def root_agent_chat(
    req: RootChatReq,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]
    msg_raw = req.message.strip()
    msg_lower = msg_raw.lower()

    root_agent = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == True).first()
    if not root_agent:
        raise HTTPException(status_code=404, detail="Root Agent not initialized")

    media_payload = None

    # Save user message to Root Agent Public Session
    session_obj = None
    try:
        session_obj = db.query(AgentPublicSession).filter(
            AgentPublicSession.agent_id == root_agent.agent_id,
            AgentPublicSession.client_id == client_id
        ).first()

        if not session_obj:
            session_obj = AgentPublicSession(
                session_id=f"root_sess_{client_id}",
                agent_id=root_agent.agent_id,
                client_id=client_id,
                user_name="Owner",
                device_name="Owner Workspace",
                created_at=datetime.utcnow()
            )
            db.add(session_obj)
            db.commit()
            db.refresh(session_obj)

        user_msg_db = AgentPublicMessage(
            session_id=session_obj.session_id,
            role="user",
            content=msg_raw,
            created_at=datetime.utcnow()
        )
        db.add(user_msg_db)
        db.commit()
    except Exception as db_e:
        logger.warning(f"Root session msg save warning: {db_e}")
        db.rollback()

    def _save_asst_msg(text_content):
        try:
            if session_obj:
                asst_msg_db = AgentPublicMessage(
                    session_id=session_obj.session_id,
                    role="assistant",
                    content=text_content,
                    created_at=datetime.utcnow()
                )
                db.add(asst_msg_db)
                db.commit()
        except Exception as db_e:
            logger.warning(f"Root asst msg save warning: {db_e}")
            db.rollback()

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 1: Meeting Creation / Schedule (HIGHEST PRIORITY if meeting word present)
    # ──────────────────────────────────────────────────────────────────────────
    is_meeting_word = any(w in msg_lower for w in ["meeting", "metting", "appointment", "calendar entry"])
    is_save_action = any(w in msg_lower for w in ["save", "schedule", "set", "kar do", "kr do", "store", "add"])
    is_inquiry_word = any(w in msg_lower for w in ["kab", "konsi", "dikhao", "batao", "show", "list", "kya", "status", "hai kya"])

    if is_meeting_word and is_save_action and not (is_inquiry_word and "save" not in msg_lower):
        title, meeting_dt = _parse_meeting_details(msg_raw)
        
        meeting_obj = RootMeeting(
            meeting_id=secrets.token_hex(8),
            client_id=client_id,
            owner_id=client_id,
            title=title,
            description=msg_raw,
            meeting_time=meeting_dt,
            duration_mins=30,
            status="scheduled",
            reminder_sent=False,
            notification_sent=False,
            created_at=datetime.utcnow()
        )
        db.add(meeting_obj)
        db.commit()
        db.refresh(meeting_obj)

        time_formatted = meeting_dt.strftime("%d %b %Y, %I:%M %p")
        reminder_time = (meeting_dt - timedelta(minutes=30)).strftime("%I:%M %p")
        
        resp = (
            f"🗓️ **Sir, aapka Meeting successful Save & Schedule ho gaya hai!**\n\n"
            f"📌 **Title**: {title}\n"
            f"⏰ **Timing**: {time_formatted}\n"
            f"📍 **Status**: Scheduled\n\n"
            f"🔔 **Notification Alert**: Main meeting start hone se 30 minute pehle (`{reminder_time}`) aapko advance reminder notification bhej doonga ki aapki meeting hai, attend kar lijiye!"
        )
        return {
            "role": "assistant",
            "content": resp,
            "answer": resp,
            "media": None,
            "agent_id": root_agent.agent_id
        }

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 2: Query Saved Meetings
    # ──────────────────────────────────────────────────────────────────────────
    is_list_all_meetings = any(kw in msg_lower for kw in ["sab meeting", "aane wali meeting", "meri meeting", "meetings dikhao", "all meetings", "show meeting", "list meeting", "kon si meeting", "konsi meeting"])
    if (is_meeting_word and is_inquiry_word) or is_list_all_meetings:
        try:
            meetings = db.query(RootMeeting).filter(RootMeeting.client_id == client_id).order_by(RootMeeting.meeting_time.asc()).all()
            if not meetings:
                resp = "Sir, Root Personal Database me abhi tak koi scheduled meeting nahi hai."
            else:
                m_list = []
                for idx, m in enumerate(meetings, 1):
                    dt_str = m.meeting_time.strftime("%d %b %Y, %I:%M %p")
                    m_list.append(f"**{idx}. {m.title}**\n   ⏰ Timing: {dt_str}\n   📍 Status: {m.status.capitalize()}")
                
                resp = "🗓️ **Sir, aapke Root Database me saved Upcoming Meetings ki complete list:**\n\n" + "\n\n".join(m_list)
        except Exception as db_e:
            db.rollback()
            logger.error(f"Error querying meetings: {db_e}")
            resp = "Sir, meetings query karte waqt technical error aaya."

        return {
            "role": "assistant",
            "content": resp,
            "answer": resp,
            "media": None,
            "agent_id": root_agent.agent_id
        }

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 3: Save Personal Note / Data (Without Meeting)
    # ──────────────────────────────────────────────────────────────────────────
    if any(kw in msg_lower for kw in ["save kr do", "save kar do", "store kr do", "store kar do", "is text ko save", "note kar lo", "yis store kr", "yaad rakho", "yisko save", "save in db", "database m save"]):
        note_title = msg_raw[:60]
        note_content = msg_raw

        if ":" in msg_raw:
            parts = msg_raw.split(":", 1)
            note_title = parts[0].strip()
            note_content = parts[1].strip()
        elif "save" in msg_raw.lower():
            clean_t = re.sub(r'(yisko|isiko|is text ko|save|kr do|kar do|database|m|in db).*$', '', msg_raw, flags=re.IGNORECASE).strip()
            if clean_t: note_title = f"Note: {clean_t[:40]}"

        try:
            memory_obj = RootMemory(
                memory_id=secrets.token_hex(8),
                client_id=client_id,
                owner_id=client_id,
                category="note",
                title=note_title or "Saved Personal Note",
                content=note_content,
                tags_json=json.dumps(["auto_saved"]),
                created_at=datetime.utcnow()
            )
            db.add(memory_obj)
            db.commit()
            db.refresh(memory_obj)

            resp = f"✅ **Sir, maine aapke Personal Data ko Database me format karke save kar liya hai:**\n\n📌 **Title**: {memory_obj.title}\n📄 **Content**: {memory_obj.content}"
        except Exception as db_e:
            db.rollback()
            logger.error(f"Error saving root memory: {db_e}")
            resp = f"✅ **Sir, aapka personal note record receive ho gaya hai:**\n\n📄 {msg_raw}"

        return {
            "role": "assistant",
            "content": resp,
            "answer": resp,
            "media": None,
            "agent_id": root_agent.agent_id
        }

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 4: Query Saved Notes / Personal Data
    # ──────────────────────────────────────────────────────────────────────────
    if any(kw in msg_lower for kw in ["kya save h", "kya save hai", "saved notes", "saved data", "meri notes", "kya data h", "yaad h", "memory dikhao"]):
        try:
            memories = db.query(RootMemory).filter(RootMemory.client_id == client_id).order_by(RootMemory.created_at.desc()).all()
            if not memories:
                resp = "Sir, Root Database me abhi tak koi personal notes ya data saved nahi hai."
            else:
                n_list = []
                for idx, m in enumerate(memories, 1):
                    dt_str = m.created_at.strftime("%d %b %Y")
                    n_list.append(f"**{idx}. {m.title}** (`{dt_str}`)\n   📄 Content: {m.content[:150]}")
                resp = "🧠 **Sir, aapke Root Database me saved Personal Notes & Data:**\n\n" + "\n\n".join(n_list)
        except Exception as db_e:
            db.rollback()
            resp = "Sir, saved notes fetch karte waqt error aaya."

        return {
            "role": "assistant",
            "content": resp,
            "answer": resp,
            "media": None,
            "agent_id": root_agent.agent_id
        }

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 5: System Agents List & Audit History
    # ──────────────────────────────────────────────────────────────────────────
    if any(phrase in msg_lower for phrase in ["agents sab ka history", "agent history", "agents history", "agent ka history", "agents list", "agents status"]):
        agents = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == False).all()
        if not agents:
            resp = "Sir, aapke paas abhi koi sub-agents nahi hain. Naye agents create karne ke baad main unki complete chat history monitor kar doonga."
        else:
            names_list = "\n".join([f"• **{a.name}** (Category: {a.category}, ID: `{a.agent_id}`)" for a in agents])
            resp = f"Sir, aapke system me nimnlikhit agents active hain:\n\n{names_list}\n\nAap kis agent ki history aur top visitors ke baare me jaan-na chahte hain? Kripya us agent ka naam ya ID bataiye."
        return {
            "role": "assistant",
            "content": resp,
            "answer": resp,
            "media": None,
            "agent_id": root_agent.agent_id
        }

    # Top 5 Visitors Audit & "Aur batao" Pagination
    target_agent = None
    all_sub_agents = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == False).all()
    for sa in all_sub_agents:
        if sa.name.lower() in msg_lower or sa.agent_id in msg_lower or (req.target_agent_id and sa.agent_id == req.target_agent_id):
            target_agent = sa
            break

    is_pagination_request = any(p in msg_lower for p in ["aur batao", "next 5", "more history", "aur users", "aur history"])

    if target_agent or is_pagination_request:
        if not target_agent and req.target_agent_id:
            target_agent = db.query(Agent).filter(Agent.agent_id == req.target_agent_id).first()

        if not target_agent and len(all_sub_agents) > 0:
            target_agent = all_sub_agents[0]

        if target_agent:
            current_offset = req.offset or 0
            if is_pagination_request:
                current_offset += 5

            sessions = db.query(AgentPublicSession).filter(
                AgentPublicSession.agent_id == target_agent.agent_id
            ).order_by(AgentPublicSession.updated_at.desc()).offset(current_offset).limit(5).all()

            if not sessions:
                if current_offset > 0:
                    resp = f"Sorry Sir, **{target_agent.name}** ke liye ab aur history nahi hai."
                else:
                    resp = f"Sir, **{target_agent.name}** par abhi tak koi public user interactions record nahi hue hain."
            else:
                user_summaries = []
                for idx, sess in enumerate(sessions, start=current_offset + 1):
                    msg_count = db.query(AgentPublicMessage).filter(AgentPublicMessage.session_id == sess.session_id).count()
                    last_msg = db.query(AgentPublicMessage).filter(
                        AgentPublicMessage.session_id == sess.session_id,
                        AgentPublicMessage.role == "user"
                    ).order_by(AgentPublicMessage.created_at.desc()).first()

                    last_query = last_msg.content if last_msg else "General inquiry"
                    user_name = sess.user_name or f"Visitor #{sess.id}"
                    phone = f" (Contact: {sess.phone_number})" if sess.phone_number else ""
                    
                    user_summaries.append(
                        f"**{idx}. {user_name}**{phone}\n"
                        f"   • Device: {sess.device_name}\n"
                        f"   • Messages Exchanged: {msg_count}\n"
                        f"   • Main Intent: \"{last_query[:120]}\"\n"
                    )

                summary_str = "\n".join(user_summaries)
                resp = (
                    f"Sir, **{target_agent.name}** ki history se Top Visitors ({current_offset + 1} se {current_offset + len(sessions)}):\n\n"
                    f"{summary_str}\n"
                    f"Agar aur users ki detail dekhni ho to **'Aur batao'** boliye."
                )

            return {
                "role": "assistant",
                "content": resp,
                "answer": resp,
                "media": None,
                "target_agent_id": target_agent.agent_id,
                "offset": current_offset,
                "agent_id": root_agent.agent_id
            }

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 6: Media Vault (Images, Videos, Documents)
    # ──────────────────────────────────────────────────────────────────────────
    if any(kw in msg_lower for kw in ["image", "photo", "picture", "video", "document", "pdf", "file", "media", "dikhao", "mange"]):
        media_type = "image" if any(k in msg_lower for k in ["image", "photo", "picture"]) else ("video" if "video" in msg_lower else "document")

        media_item = db.query(RootMedia).filter(
            RootMedia.client_id == client_id,
            RootMedia.media_type == media_type
        ).order_by(RootMedia.created_at.desc()).first()

        if media_item:
            media_payload = media_item.to_dict()
            resp = f"Sir, aapke request ke anusar **{media_item.name}** ({media_type.upper()}) hazir hai:"
        else:
            any_media = db.query(RootMedia).filter(RootMedia.client_id == client_id).order_by(RootMedia.created_at.desc()).first()
            if any_media:
                media_payload = any_media.to_dict()
                resp = f"Sir, aapke vault se requested file **{any_media.name}** hazir hai:"
            else:
                resp = f"Sir, Media Vault me abhi tak koi {media_type} upload nahi hua hai."

        return {
            "role": "assistant",
            "content": resp,
            "answer": resp,
            "media": media_payload,
            "agent_id": root_agent.agent_id
        }

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 7: Conversational AI & RAG Answer
    # ──────────────────────────────────────────────────────────────────────────
    mem_context = "None"
    meet_context = "None"
    try:
        saved_memories = db.query(RootMemory).filter(RootMemory.client_id == client_id).order_by(RootMemory.created_at.desc()).limit(15).all()
        saved_meetings = db.query(RootMeeting).filter(RootMeeting.client_id == client_id).order_by(RootMeeting.meeting_time.asc()).limit(10).all()
        mem_context = "\n".join([f"• Note [{m.created_at.strftime('%d %b %Y')}]: {m.title} -> {m.content}" for m in saved_memories]) or "None"
        meet_context = "\n".join([f"• Meeting [{m.meeting_time.strftime('%d %b %Y, %I:%M %p')}]: {m.title} ({m.status})" for m in saved_meetings]) or "None"
    except Exception as db_e:
        logger.warning(f"Root memory query fallback: {db_e}")
        db.rollback()

    full_context = f"OWNER SAVED MEMORIES & NOTES:\n{mem_context}\n\nOWNER SCHEDULED MEETINGS:\n{meet_context}"
    
    system_prompt = (
        "You are the Root Personal Assistant Agent for the owner. You have supreme authority over all sub-agents, system notes, and scheduled meetings.\n"
        "Address the user respectfully as 'Sir'.\n"
        "CORE DIRECTIVES:\n"
        "1. Always check the OWNER SAVED MEMORIES & MEETINGS context below to answer any question about saved data, owner's profile, software developer identity, company name, or upcoming meetings.\n"
        "2. Never cite document page numbers or say 'according to provided documents'. Respond in a natural, executive conversational assistant style in Hinglish/Hindi or English (matching user's language).\n"
        "3. If asked 'guddu kon h' or 'main kon hoon' or 'diintech', answer clearly based on saved notes: Guddu Kumar is a Software Developer working at Diintech company!\n\n"
        f"--- ROOT PERSONAL DATABASE CONTEXT ---\n{full_context}\n--- END CONTEXT ---"
    )

    try:
        from app.services.llm import llm_with_history
        hist_list = req.history or []
        ans = await llm_with_history(
            question=msg_raw,
            system=system_prompt,
            history=hist_list,
            provider=settings.LLM_PROVIDER,
            model=settings.GEMINI_MODEL if settings.LLM_PROVIDER == "gemini" else "default",
            api_key=get_active_api_key(settings.LLM_PROVIDER)
        )
        resp = ans or f"Sir, main aapke order par kaam kar raha hoon."
    except Exception as e:
        logger.error(f"Root agent LLM generation error: {e}")
        resp = f"Sir, main aapke order par kaam kar raha hoon."

    _save_asst_msg(resp)
    return {
        "role": "assistant",
        "content": resp,
        "answer": resp,
        "media": media_payload,
        "agent_id": root_agent.agent_id
    }


# ── Root Agent Chat History Retrieval Endpoint ───────────────────────────────

@router.get("/root-agent/history")
def get_root_agent_history(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    root_agent = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == True).first()
    if not root_agent:
        return []

    session_obj = db.query(AgentPublicSession).filter(
        AgentPublicSession.agent_id == root_agent.agent_id,
        AgentPublicSession.client_id == client_id
    ).first()

    if not session_obj:
        return []

    messages = db.query(AgentPublicMessage).filter(
        AgentPublicMessage.session_id == session_obj.session_id
    ).order_by(AgentPublicMessage.created_at.asc()).all()

    return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else ""} for m in messages]


# ── Media Vault Upload Endpoint ───────────────────────────────────────────────

@router.post("/root-agent/media/upload")
async def upload_root_media(
    file: UploadFile = File(...),
    media_type: str = Form("image"),
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(""),
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    upload_dir = os.path.join("uploads", "root_media")
    os.makedirs(upload_dir, exist_ok=True)

    filename = f"{secrets.token_hex(6)}_{file.filename}"
    file_path = os.path.join(upload_dir, filename)

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    file_url = f"/uploads/root_media/{filename}"
    extracted_text = ""

    if media_type == "document" and filename.endswith(".txt"):
        try: extracted_text = content.decode("utf-8", errors="ignore")
        except Exception: extracted_text = ""

    media_obj = RootMedia(
        media_id=secrets.token_hex(8),
        client_id=client_id,
        owner_id=client_id,
        media_type=media_type.lower(),
        name=name or file.filename,
        description=description or "",
        file_url=file_url,
        file_path=file_path,
        raw_text=extracted_text,
        created_at=datetime.utcnow()
    )
    db.add(media_obj)
    db.commit()
    db.refresh(media_obj)

    return {
        "success": True,
        "media": media_obj.to_dict()
    }


# ── 30-Minute Pre-Meeting Reminder Background Service ─────────────────────────

@router.get("/root-agent/check-reminders")
async def trigger_meeting_reminders_check(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    now = datetime.utcnow()
    threshold = now + timedelta(minutes=30)

    upcoming_meetings = db.query(RootMeeting).filter(
        RootMeeting.client_id == client_id,
        RootMeeting.status == "scheduled",
        RootMeeting.reminder_sent == False,
        RootMeeting.meeting_time <= threshold,
        RootMeeting.meeting_time >= now - timedelta(minutes=5)
    ).all()

    triggered_count = 0
    notifications_created = []

    for m in upcoming_meetings:
        notif = Notification(
            client_id=client_id,
            type="meeting_reminder",
            title="⏰ Upcoming Meeting Alert (30 min remaining)",
            message=f"Sir, aapki meeting '{m.title}' 30 minute me hone wali hai ({m.meeting_time.strftime('%I:%M %p')}). Kindly attend!",
            is_read=False,
            created_at=datetime.utcnow()
        )
        db.add(notif)
        m.reminder_sent = True
        triggered_count += 1
        notifications_created.append(notif.to_dict())

    db.commit()
    return {
        "triggered_reminders": triggered_count,
        "notifications": notifications_created
    }


# ── Meetings & Memories Listing ───────────────────────────────────────────────

@router.get("/root-agent/meetings")
async def list_root_meetings(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    meetings = db.query(RootMeeting).filter(RootMeeting.client_id == client["client_id"]).order_by(RootMeeting.meeting_time.asc()).all()
    return [m.to_dict() for m in meetings]


@router.get("/root-agent/memories")
async def list_root_memories(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    memories = db.query(RootMemory).filter(RootMemory.client_id == client["client_id"]).order_by(RootMemory.created_at.desc()).all()
    return [m.to_dict() for m in memories]


@router.get("/root-agent/media")
async def list_root_media(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    media = db.query(RootMedia).filter(RootMedia.client_id == client["client_id"]).order_by(RootMedia.created_at.desc()).all()
    return [m.to_dict() for m in media]


@router.get("/root-agent/overview")
async def get_root_system_overview(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Returns complete Executive Overview for Root Assistant:
    - Meetings list & count
    - Saved memories/notes list & count
    - Media vault files count & list
    - Sub-agents list with visitor session counts & message counts
    """
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    meetings = db.query(RootMeeting).filter(RootMeeting.client_id == client_id).order_by(RootMeeting.meeting_time.asc()).all()
    memories = db.query(RootMemory).filter(RootMemory.client_id == client_id).order_by(RootMemory.created_at.desc()).all()
    media_items = db.query(RootMedia).filter(RootMedia.client_id == client_id).order_by(RootMedia.created_at.desc()).all()
    sub_agents = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == False).all()

    agents_overview = []
    for sa in sub_agents:
        total_visitors = db.query(AgentPublicSession).filter(AgentPublicSession.agent_id == sa.agent_id).count()
        agents_overview.append({
            "agent_id": sa.agent_id,
            "name": sa.name,
            "category": sa.category,
            "is_active": sa.is_active,
            "total_visitors": total_visitors
        })

    return {
        "total_meetings": len(meetings),
        "meetings": [m.to_dict() for m in meetings],
        "total_notes": len(memories),
        "memories": [m.to_dict() for m in memories],
        "total_media": len(media_items),
        "media": [m.to_dict() for m in media_items],
        "total_agents": len(sub_agents),
        "agents_overview": agents_overview
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DAILY PLANNER ENDPOINTS — Only for Root Personal Assistant Agent 👑
# ═══════════════════════════════════════════════════════════════════════════════

class CreatePlanReq(BaseModel):
    title: str
    description: Optional[str] = ""
    category: Optional[str] = "work"   # work | personal | health | meeting | other
    plan_date: str   # YYYY-MM-DD
    plan_time: str   # HH:MM

class MeetingToPlanReq(BaseModel):
    title: str
    description: Optional[str] = ""
    plan_date: str   # YYYY-MM-DD
    plan_time: str   # HH:MM
    source_agent_id: Optional[str] = None


def _compute_plan_status(plan_date: str, plan_time: str, is_completed: bool) -> str:
    """Compute plan status based on date/time vs now."""
    if is_completed:
        return "completed"
    try:
        plan_dt = datetime.strptime(f"{plan_date} {plan_time}", "%Y-%m-%d %H:%M")
        now = datetime.utcnow() + timedelta(hours=5, minutes=30)  # IST offset
        if plan_dt < now:
            return "completed"  # Auto-complete if past
        today_str = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        if plan_date == today_str:
            return "pending"
        return "upcoming"
    except Exception:
        return "pending"


@router.get("/root-agent/plans/today")
async def get_today_plans(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get all plans for today for the sliding carousel."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]
    today_str = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")

    plans = db.query(RootDailyPlan).filter(
        RootDailyPlan.client_id == client_id,
        RootDailyPlan.plan_date == today_str
    ).order_by(RootDailyPlan.plan_time.asc()).all()

    result = []
    for p in plans:
        d = p.to_dict()
        d["status"] = _compute_plan_status(p.plan_date, p.plan_time, p.is_completed)
        result.append(d)
    return result


@router.get("/root-agent/plans")
async def list_daily_plans(
    filter: Optional[str] = Query(None),   # pending | completed | upcoming | all
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """List all daily plans sorted by nearest date-time."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    query = db.query(RootDailyPlan).filter(RootDailyPlan.client_id == client_id)
    plans = query.all()

    result = []
    for p in plans:
        d = p.to_dict()
        computed_status = _compute_plan_status(p.plan_date, p.plan_time, p.is_completed)
        d["status"] = computed_status
        result.append(d)

    # Filter
    if filter and filter != "all":
        result = [p for p in result if p["status"] == filter]
    else:
        result = [p for p in result if p["status"] != "completed"]

    # Sort by nearest date-time
    def sort_key(p):
        try:
            return datetime.strptime(f"{p['plan_date']} {p['plan_time']}", "%Y-%m-%d %H:%M")
        except Exception:
            return datetime.max

    result.sort(key=sort_key)
    return result


@router.post("/root-agent/plans")
async def create_daily_plan(
    req: CreatePlanReq,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Create a new daily plan."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    # Validate date is not in past
    try:
        plan_dt = datetime.strptime(f"{req.plan_date} {req.plan_time}", "%Y-%m-%d %H:%M")
        now_local = datetime.now()
        # Generous 12-hour buffer to handle system clock drift, timezone variations, and user delay
        if plan_dt < now_local - timedelta(hours=12):
            raise HTTPException(status_code=400, detail="Cannot create plan for a past date/time.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format. Use YYYY-MM-DD and HH:MM.")

    plan = RootDailyPlan(
        plan_id=secrets.token_hex(8),
        client_id=client_id,
        owner_id=client_id,
        title=req.title.strip(),
        description=req.description or "",
        category=req.category or "work",
        plan_date=req.plan_date,
        plan_time=req.plan_time,
        status="pending",
        is_completed=False,
        from_meeting=False,
        created_at=datetime.utcnow()
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    d = plan.to_dict()
    d["status"] = _compute_plan_status(plan.plan_date, plan.plan_time, plan.is_completed)
    return {"success": True, "plan": d}


@router.patch("/root-agent/plans/{plan_id}/complete")
async def complete_daily_plan(
    plan_id: str,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Mark a plan as completed (toggle)."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    plan = db.query(RootDailyPlan).filter(
        RootDailyPlan.plan_id == plan_id,
        RootDailyPlan.client_id == client_id
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    plan.is_completed = not plan.is_completed
    plan.completed_at = datetime.utcnow() if plan.is_completed else None
    plan.status = "completed" if plan.is_completed else _compute_plan_status(plan.plan_date, plan.plan_time, False)
    db.commit()
    db.refresh(plan)

    d = plan.to_dict()
    d["status"] = plan.status
    return {"success": True, "plan": d}


@router.post("/root-agent/plans/auto-complete")
async def auto_complete_past_plans(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Auto-complete all plans whose date-time has passed."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%Y-%m-%d")
    current_time_str = now_ist.strftime("%H:%M")

    plans = db.query(RootDailyPlan).filter(
        RootDailyPlan.client_id == client_id,
        RootDailyPlan.is_completed == False
    ).all()

    updated_count = 0
    for p in plans:
        try:
            plan_dt = datetime.strptime(f"{p.plan_date} {p.plan_time}", "%Y-%m-%d %H:%M")
            if plan_dt < now_ist:
                p.is_completed = True
                p.status = "completed"
                p.completed_at = datetime.utcnow()
                updated_count += 1
        except Exception:
            pass

    db.commit()
    return {"auto_completed": updated_count}


@router.get("/root-agent/plans/check-conflict")
async def check_plan_conflict(
    plan_date: str = Query(...),
    plan_time: str = Query(...),
    exclude_plan_id: Optional[str] = Query(None),
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Check if there's already a plan at the given date and time (within 30-min window)."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    try:
        target_dt = datetime.strptime(f"{plan_date} {plan_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format.")

    # Check for plans within ±30 min window on same date
    plans_on_date = db.query(RootDailyPlan).filter(
        RootDailyPlan.client_id == client_id,
        RootDailyPlan.plan_date == plan_date,
        RootDailyPlan.is_completed == False
    ).all()

    conflicts = []
    for p in plans_on_date:
        if exclude_plan_id and p.plan_id == exclude_plan_id:
            continue
        try:
            p_dt = datetime.strptime(f"{p.plan_date} {p.plan_time}", "%Y-%m-%d %H:%M")
            diff = abs((p_dt - target_dt).total_seconds() / 60)
            if diff < 30:  # Within 30 minutes
                conflicts.append({
                    "plan_id": p.plan_id,
                    "title": p.title,
                    "plan_time": p.plan_time,
                    "category": p.category,
                    "diff_minutes": round(diff)
                })
        except Exception:
            pass

    return {
        "has_conflict": len(conflicts) > 0,
        "conflicts": conflicts
    }


@router.post("/root-agent/plans/from-meeting")
async def add_plan_from_meeting(
    req: MeetingToPlanReq,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Called by sub-agent when a meeting is scheduled in chat.
    Saves to Root Planner with category='meeting' and checks conflicts.
    """
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    # Check conflict first
    conflict_resp = {"has_conflict": False, "conflicts": []}
    try:
        target_dt = datetime.strptime(f"{req.plan_date} {req.plan_time}", "%Y-%m-%d %H:%M")
        plans_on_date = db.query(RootDailyPlan).filter(
            RootDailyPlan.client_id == client_id,
            RootDailyPlan.plan_date == req.plan_date,
            RootDailyPlan.is_completed == False
        ).all()
        for p in plans_on_date:
            p_dt = datetime.strptime(f"{p.plan_date} {p.plan_time}", "%Y-%m-%d %H:%M")
            diff = abs((p_dt - target_dt).total_seconds() / 60)
            if diff < 30:
                conflict_resp["has_conflict"] = True
                conflict_resp["conflicts"].append({
                    "plan_id": p.plan_id,
                    "title": p.title,
                    "plan_time": p.plan_time,
                    "category": p.category,
                    "diff_minutes": round(diff)
                })
    except Exception:
        pass

    # Create the plan regardless (caller decides to show warning)
    plan = RootDailyPlan(
        plan_id=secrets.token_hex(8),
        client_id=client_id,
        owner_id=client_id,
        title=req.title.strip(),
        description=req.description or "",
        category="meeting",
        plan_date=req.plan_date,
        plan_time=req.plan_time,
        status="pending",
        is_completed=False,
        from_meeting=True,
        created_at=datetime.utcnow()
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    d = plan.to_dict()
    d["status"] = _compute_plan_status(plan.plan_date, plan.plan_time, plan.is_completed)
    return {
        "success": True,
        "plan": d,
        "conflict": conflict_resp
    }
