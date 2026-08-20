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
    RootMemory, RootMeeting, RootMedia, RootDailyPlan, RootDailyPlanAnalysis
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
    client_id: Optional[str] = None

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


class AnalyzePlansReq(BaseModel):
    plan_date: str


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


# ── Unified Planner Extractor and Handler (Voice/Chat) ─────────────────────────

async def extract_planner_intent_with_llm(
    question: str,
    history: list,
    db: Session
) -> dict:
    import json
    from datetime import datetime, timedelta
    from app.services.llm import llm_with_history
    from app.core.config import settings

    now_local = datetime.utcnow() + timedelta(hours=5, minutes=30)
    current_date = now_local.strftime("%Y-%m-%d")
    current_time = now_local.strftime("%H:%M")
    current_day = now_local.strftime("%A")

    system_prompt = f"""
You are a precise JSON extractor helper for a Root Personal Assistant Agent.
The owner of the system is talking to you. You need to analyze the conversation history and the new message to detect if the user is trying to:
1. QUERY (view/list/check) their plans, schedule, meetings, or reminders for a specific date (or today/tomorrow).
2. SET (create/schedule/add) a new plan, meeting, or reminder.
3. COMPLETE (mark as completed/done) an existing plan, meeting, or reminder.
4. EDIT (modify/change/reschedule) an existing plan, meeting, or reminder.

Current local date: {current_date}
Current local time: {current_time}
Current day of week: {current_day}

Intents:
- "query": User wants to see their schedule/plans/meetings/reminders. (E.g., "aaj ka kya plan h", "meri meetings batao", "schedule check karo", "what is my schedule tomorrow?")
- "set": User wants to schedule or create a plan, meeting, or reminder. (E.g., "meeting set kar do", "gym ka plan set kar do 5 pm", "reminder lagao", "aaj 4 baje team standup add karo")
- "complete": User wants to mark a plan/meeting/reminder as completed or done. (E.g., "06:05 PM wala plan complete kr do", "gym ka reminder done mark karo", "meeting complete ho gayi h", "6:05 baje wala task complete mark kro", "complete mark 6:05 PM plan")
- "edit": User wants to modify/change/reschedule a plan, meeting, or reminder. (E.g., "6:05 pm wala plan edit karke time 7:00 pm kar do", "gym ke plan ka name change kar do", "meeting 4 baje ke badle 5 baje shift kar do")
- "none": Not related to querying, setting, completing, or editing plans/meetings/reminders.

If intent is "query", extract:
- "target_type": "plan" (daily plans only) | "meeting" (meetings only) | "reminder" (reminders only) | "all" (everything, default)
- "date": "YYYY-MM-DD" (calculate based on relative terms: "aaj/today" -> "{current_date}", "kal/tomorrow" -> next day, etc. Default is "{current_date}").

If intent is "set", extract:
- "target_type": "plan" | "meeting" | "reminder" (e.g. if they say "reminder set kro" -> "reminder", "meeting schedule karo" -> "meeting", otherwise default to "plan". Be highly tolerant of typos, e.g. "meetiang", "meting", "meating", "meet" must map to "meeting". If they schedule something with a person, e.g. "guddu ke sath add kar do", target_type is "meeting").
- "title": Clean title/description of the item (e.g. "Meeting with Ramesh", "Gym cardio session", "Drink water"). If it is a meeting with a person but title is not explicitly specified, construct it like "Meeting with Guddu". Set to null if not provided.
- "date": "YYYY-MM-DD". Calculate based on relative terms. Default to today's date "{current_date}" if not specified.
- "time": "HH:MM" (24-hour format). Parse relative times like "sham ko 6 baje" -> "18:00", "subah 10 baje" -> "10:00", "5:30 pm" -> "17:30". Set to null if not specified.
- "category": Choose from "work", "personal", "health", "meeting", "reminder", "other". If not specified, map "reminder" to "reminder", "meeting" to "meeting", and others default to "work".
- "duration_mins": Integer. The duration in minutes if specified (e.g., "1 hour" -> 60, "2 hours" -> 120, "30 minutes" -> 30, "45 mins" -> 45, "aadha ghanta" -> 30, "ek ghanta" -> 60). Set to 30 by default if not specified or not clear.

If intent is "complete", extract:
- "title": Title or description keyword of the item to complete (e.g. "Gym", "Meeting with Ramesh"). Set to null if not specified.
- "time": "HH:MM" (24-hour format). Parse the time mentioned, e.g. "06:05 PM" -> "18:05", "6:05 baje" -> "18:05". Set to null if not specified.
- "date": "YYYY-MM-DD". Calculate relative dates. Default to today's date "{current_date}" if not specified.
- "target_type": "plan" | "meeting" | "reminder" | "all" (default "all").

If intent is "edit", extract:
- "search_parameters":
  - "title": Clean keyword to match current title (e.g. "Gym", "Meeting with Ramesh"). Set to null if not specified.
  - "time": "HH:MM" (24-hour format). Parse the current scheduled time to match, e.g. "06:05 PM" -> "18:05". Set to null if not specified.
  - "date": "YYYY-MM-DD". Parse the current scheduled date, default to "{current_date}" if not specified.
- "new_parameters":
  - "title": The new title to assign. Set to null if not specified.
  - "time": "HH:MM" (24-hour format). The new time to assign. Set to null if not specified.
  - "date": "YYYY-MM-DD". The new date to assign. Set to null if not specified.
  - "category": Choose from "work", "personal", "health", "meeting", "reminder", "other". Set to null if not specified.
  - "duration_mins": Integer. The new duration in minutes to assign (e.g. "1 hour" -> 60). Set to null if not specified.

Rules for "set":
- To create a plan, meeting, or reminder, we MUST have a "title" and a "time".
- If either "title" or "time" is null, set "status" to "incomplete" and list the missing fields in "missing_fields".
- If "status" is "incomplete", generate a natural, short, polite question in Hindi/Hinglish in "ask_clarification" asking the user for the missing fields. (E.g., "Sir, plan kis chiz ka set karna hai aur kitne baje?")
- If all required fields are present, set "status" to "complete" and "missing_fields" to [].

Response format: ONLY return a raw JSON object. No explanation, no markdown backticks, no code block wrapper. Just raw JSON.
Example incomplete set response:
{{
  "intent": "set",
  "target_type": "reminder",
  "status": "incomplete",
  "extracted_parameters": {{
    "title": null,
    "date": "{current_date}",
    "time": "18:00",
    "category": "reminder",
    "duration_mins": 30
  }},
  "missing_fields": ["title"],
  "ask_clarification": "Sir, sham 6 baje kis chiz ka reminder lagana hai?"
}}

Example complete set response:
{{
  "intent": "set",
  "target_type": "plan",
  "status": "complete",
  "extracted_parameters": {{
    "title": "Gym cardio session",
    "date": "{current_date}",
    "time": "18:00",
    "category": "health",
    "duration_mins": 30
  }},
  "missing_fields": [],
  "ask_clarification": null
}}

Example complete complete response:
{{
  "intent": "complete",
  "target_type": "plan",
  "extracted_parameters": {{
    "title": null,
    "date": "{current_date}",
    "time": "18:05"
  }}
}}
"""
    try:
        ans = await llm_with_history(
            question=question,
            system=system_prompt,
            history=history,
            provider=settings.LLM_PROVIDER,
            model=settings.GEMINI_MODEL if settings.LLM_PROVIDER == "gemini" else "default",
            api_key=get_active_api_key(settings.LLM_PROVIDER)
        )
        ans_clean = ans.strip()
        if ans_clean.startswith("```"):
            ans_clean = re.sub(r'^```(?:json)?\n', '', ans_clean)
            ans_clean = re.sub(r'\n```$', '', ans_clean)
            ans_clean = ans_clean.strip()
        
        # Sometimes there's stray text, try to find the first '{' and last '}'
        start_idx = ans_clean.find('{')
        end_idx = ans_clean.rfind('}')
        if start_idx != -1 and end_idx != -1:
            ans_clean = ans_clean[start_idx:end_idx+1]

        data = json.loads(ans_clean)
        return data
    except Exception as e:
        logger.warning(f"Error parsing intent with LLM: {e}")
        return {"intent": "none"}

async def handle_planner_voice_and_chat(
    message: str,
    history: list,
    client_id: str,
    db: Session,
    voice_mode: bool = False
) -> Optional[str]:
    data = await extract_planner_intent_with_llm(message, history, db)
    intent = data.get("intent", "none")

    msg_lower = message.lower()
    now_local = datetime.utcnow() + timedelta(hours=5, minutes=30)
    today_str = now_local.strftime("%Y-%m-%d")

    # Rule-based fallback if LLM failed/returned none (e.g. rate limit error)
    if intent == "none":
        # Check if query intent
        if any(kw in msg_lower for kw in ["aaj ka", "aaj ke", "plan", "plane", "reminder", "meeting", "checklist", "schedule", "kon kon"]):
            if any(kw in msg_lower for kw in ["batao", "dikhao", "show", "list", "check", "h", "hai"]):
                intent = "query"
                data = {
                    "intent": "query",
                    "target_type": "all",
                    "extracted_parameters": {
                        "date": today_str
                    }
                }
        
        # Check if complete intent
        if any(kw in msg_lower for kw in ["complete", "done", "mark"]):
            time_match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)?', msg_lower)
            extracted_time = None
            if time_match:
                try:
                    hr = int(time_match.group(1))
                    mn = int(time_match.group(2))
                    ampm = time_match.group(3) or ""
                    if "pm" in ampm.lower() and hr < 12:
                        hr += 12
                    elif "am" in ampm.lower() and hr == 12:
                        hr = 0
                    extracted_time = f"{hr:02d}:{mn:02d}"
                except:
                    pass
            
            intent = "complete"
            data = {
                "intent": "complete",
                "extracted_parameters": {
                    "time": extracted_time,
                    "date": today_str,
                    "title": None,
                    "target_type": "all"
                }
            }

        # Check if set intent
        last_assistant_msg = None
        if history:
            for h in reversed(history):
                if h.get("role") == "assistant":
                    last_assistant_msg = h.get("content", "").lower()
                    break

        is_set_followup = False
        if last_assistant_msg:
            is_set_followup = any(kw in last_assistant_msg for kw in ["schedule karni hai", "reminder lagana hai", "plan set karna hai", "kiske sath", "kitne baje", "kis chiz ka", "set karna hai"])

        if any(kw in msg_lower for kw in ["set", "add", "schedule", "lagao", "lagado", "kr do", "kar do"]) or is_set_followup:
            extracted_time = None
            
            time_match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm|baje)?', msg_lower)
            if time_match:
                try:
                    hr = int(time_match.group(1))
                    mn = int(time_match.group(2))
                    ampm = time_match.group(3) or ""
                    if "pm" in ampm.lower() and hr < 12:
                        hr += 12
                    elif "am" in ampm.lower() and hr == 12:
                        hr = 0
                    extracted_time = f"{hr:02d}:{mn:02d}"
                except:
                    pass
            
            if not extracted_time:
                time_match2 = re.search(r'(\d{1,2})\s*(am|pm|baje)', msg_lower)
                if time_match2:
                    try:
                        hr = int(time_match2.group(1))
                        mn = 0
                        ampm = time_match2.group(2) or ""
                        if "pm" in ampm.lower() and hr < 12:
                            hr += 12
                        elif "am" in ampm.lower() and hr == 12:
                            hr = 0
                        extracted_time = f"{hr:02d}:{mn:02d}"
                    except:
                        pass

            target_type = "plan"
            is_meet_indicator = any(w in msg_lower for w in ["meeting", "meetiang", "metting", "meting", "meating", "meetin", "meet", "appointment", "call", "consultation", "session"])
            is_with_person = any(w in msg_lower for w in ["ke sath", "ke saath", "with", "se milna", "guddu"])
            
            if is_meet_indicator or is_with_person or (last_assistant_msg and "meeting" in last_assistant_msg):
                target_type = "meeting"
            elif "reminder" in msg_lower or "remind" in msg_lower or (last_assistant_msg and "reminder" in last_assistant_msg):
                target_type = "reminder"
                
            title = None
            person_name = None
            
            if target_type == "meeting":
                hindi_match = re.search(r'([a-zA-Z0-9\u0900-\u097F]+)\s+(?:ke\s+saath|ke\s+sath)', message, re.IGNORECASE)
                if hindi_match:
                    candidate = hindi_match.group(1).strip()
                    if candidate.lower() not in ["add", "set", "kar", "kr", "do", "m", "me", "pe", "aaj", "kal", "time", "baje", "pm", "am", "meeting", "meetiang", "metting", "meetiang"]:
                        person_name = candidate
                
                if not person_name:
                    english_match = re.search(r'(?:with|se|milna)\s+([a-zA-Z0-9\u0900-\u097F]+)', message, re.IGNORECASE)
                    if english_match:
                        candidate = english_match.group(1).strip()
                        if candidate.lower() not in ["add", "set", "kar", "kr", "do", "me", "aaj", "kal", "time"]:
                            person_name = candidate

                if person_name:
                    title = f"Meeting with {person_name}"
                else:
                    title = "Meeting"
            elif target_type == "reminder":
                title = "Reminder"
            else:
                title = "Daily Plan"

            missing_fields = []
            if not extracted_time:
                missing_fields.append("time")
            if title in ["Meeting", "Reminder", "Daily Plan"]:
                missing_fields.append("title")

            ask_clarification = None
            if missing_fields:
                if target_type == "meeting":
                    if "title" in missing_fields and "time" in missing_fields:
                        ask_clarification = "Sir, meeting kiske sath aur kitne baje schedule karni hai?"
                    elif "title" in missing_fields:
                        ask_clarification = "Sir, meeting kiske sath schedule karni hai?"
                    else:
                        ask_clarification = "Sir, meeting kitne baje schedule karni hai?"
                elif target_type == "reminder":
                    if "title" in missing_fields and "time" in missing_fields:
                        ask_clarification = "Sir, reminder kis chiz ka aur kitne baje lagana hai?"
                    elif "title" in missing_fields:
                        ask_clarification = "Sir, kis chiz ka reminder lagana hai?"
                    else:
                        ask_clarification = "Sir, reminder kitne baje lagana hai?"
                else:
                    if "title" in missing_fields and "time" in missing_fields:
                        ask_clarification = "Sir, plan kis chiz ka aur kitne baje set karna hai?"
                    elif "title" in missing_fields:
                        ask_clarification = "Sir, plan kis chiz ka set karna hai?"
                    else:
                        ask_clarification = "Sir, plan kitne baje set karna hai?"

            data = {
                "intent": "set",
                "target_type": target_type,
                "status": "incomplete" if missing_fields else "complete",
                "extracted_parameters": {
                    "title": title if title not in ["Meeting", "Reminder", "Daily Plan"] else None,
                    "date": today_str,
                    "time": extracted_time,
                    "category": target_type if target_type in ["meeting", "reminder"] else "work"
                },
                "missing_fields": missing_fields,
                "ask_clarification": ask_clarification
            }
            intent = "set"

    if intent == "none":
        return None

    # Handle Query
    if intent == "query":
        params = data.get("extracted_parameters", {})
        target_date = params.get("date") or data.get("date")
        target_type = data.get("target_type") or params.get("target_type") or "all"
        if not target_date:
            now_local = datetime.utcnow() + timedelta(hours=5, minutes=30)
            target_date = now_local.strftime("%Y-%m-%d")

        # Query RootDailyPlan
        plans_q = db.query(RootDailyPlan).filter(
            RootDailyPlan.client_id == client_id,
            RootDailyPlan.plan_date == target_date
        )
        if target_type == "reminder":
            plans_q = plans_q.filter(RootDailyPlan.category == "reminder")
        elif target_type == "meeting":
            plans_q = plans_q.filter(RootDailyPlan.category == "meeting")
        elif target_type == "plan":
            plans_q = plans_q.filter(RootDailyPlan.category.notin_(["meeting", "reminder"]))
        
        plans = plans_q.all()

        # Query RootMeeting
        meetings = []
        if target_type in ["meeting", "all"]:
            try:
                start_dt = datetime.strptime(f"{target_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
                end_dt = datetime.strptime(f"{target_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
                meetings = db.query(RootMeeting).filter(
                    RootMeeting.client_id == client_id,
                    RootMeeting.meeting_time >= start_dt,
                    RootMeeting.meeting_time <= end_dt
                ).all()
            except Exception as e:
                logger.warning(f"Error querying meetings: {e}")

        # Format Display Date
        now_local = datetime.utcnow() + timedelta(hours=5, minutes=30)
        today_str = now_local.strftime("%Y-%m-%d")
        tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")

        if target_date == today_str:
            display_date = "aaj"
        elif target_date == tomorrow_str:
            display_date = "kal"
        else:
            try:
                dt = datetime.strptime(target_date, "%Y-%m-%d")
                display_date = dt.strftime("%d %b %Y")
            except:
                display_date = target_date

        events = []
        seen_meetings = set()

        for p in plans:
            t_type = "Plan"
            if p.category == "reminder":
                t_type = "Reminder"
            elif p.category == "meeting":
                t_type = "Meeting"
                seen_meetings.add((p.title.lower(), p.plan_time))
            
            events.append({
                "time": p.plan_time,
                "title": p.title,
                "type": t_type,
                "status": "completed" if p.is_completed else "pending"
            })

        for m in meetings:
            m_time_str = m.meeting_time.strftime("%H:%M")
            if (m.title.lower(), m_time_str) not in seen_meetings:
                events.append({
                    "time": m_time_str,
                    "title": m.title,
                    "type": "Meeting",
                    "status": m.status
                })

        # Sort events by time
        events.sort(key=lambda x: x["time"])

        if not events:
            type_hindi = {
                "plan": "plan",
                "meeting": "meeting",
                "reminder": "reminder",
                "all": "plan, meeting ya reminder"
            }.get(target_type, "plan")
            return f"Sir, {display_date} ke liye koi {type_hindi} scheduled nahi hai."

        # Build response string
        response_parts = [f"Sir, {display_date} ka schedule is prakar hai:"]
        for ev in events:
            time_12h = ev["time"]
            try:
                time_obj = datetime.strptime(ev["time"], "%H:%M")
                time_12h = time_obj.strftime("%I:%M %p")
            except:
                pass
            status_str = " (completed)" if ev["status"] == "completed" else ""
            response_parts.append(f"- {time_12h} par: [{ev['type']}] {ev['title']}{status_str}")
            
        return "\n".join(response_parts)

    # Handle Complete
    if intent == "complete":
        params = data.get("extracted_parameters", {})
        title = params.get("title") or data.get("title")
        time = params.get("time") or data.get("time")
        date = params.get("date") or data.get("date")
        target_type = data.get("target_type") or params.get("target_type") or "all"

        if not date:
            now_local = datetime.utcnow() + timedelta(hours=5, minutes=30)
            date = now_local.strftime("%Y-%m-%d")

        query = db.query(RootDailyPlan).filter(
            RootDailyPlan.client_id == client_id,
            RootDailyPlan.plan_date == date
        )
        if time:
            query = query.filter(RootDailyPlan.plan_time == time)
        if target_type == "reminder":
            query = query.filter(RootDailyPlan.category == "reminder")
        elif target_type == "meeting":
            query = query.filter(RootDailyPlan.category == "meeting")
        elif target_type == "plan":
            query = query.filter(RootDailyPlan.category.notin_(["meeting", "reminder"]))

        plans = query.all()

        # Fallback: if we filtered specifically but found nothing, query without category filter
        if not plans and target_type != "all":
            fallback_query = db.query(RootDailyPlan).filter(
                RootDailyPlan.client_id == client_id,
                RootDailyPlan.plan_date == date
            )
            if time:
                fallback_query = fallback_query.filter(RootDailyPlan.plan_time == time)
            plans = fallback_query.all()

        if title and plans:
            plans_filtered = []
            for p in plans:
                if title.lower() in p.title.lower() or p.title.lower() in title.lower():
                    plans_filtered.append(p)
            if plans_filtered:
                plans = plans_filtered

        if not plans:
            time_part = f" jo {time} baje tha" if time else ""
            title_part = f" '{title}'" if title else ""
            return f"Sir, {date}{time_part}{title_part} ka koi plan scheduled nahi mila."

        completed_titles = []
        for p in plans:
            p.is_completed = True
            p.status = "completed"
            p.completed_at = datetime.utcnow()
            completed_titles.append(f"[{p.category.upper()}] {p.title}")

            if p.category == "meeting" or p.from_meeting:
                try:
                    start_dt = datetime.strptime(f"{date} 00:00:00", "%Y-%m-%d %H:%M:%S")
                    end_dt = datetime.strptime(f"{date} 23:59:59", "%Y-%m-%d %H:%M:%S")
                    meeting = db.query(RootMeeting).filter(
                        RootMeeting.client_id == client_id,
                        RootMeeting.meeting_time >= start_dt,
                        RootMeeting.meeting_time <= end_dt,
                        RootMeeting.title.like(f"%{p.title}%")
                    ).first()
                    if meeting:
                        meeting.status = "completed"
                except Exception as me:
                    logger.warning(f"Error marking matching RootMeeting complete: {me}")

        db.commit()
        titles_str = ", ".join(completed_titles)
        time_formatted = time
        try:
            time_obj = datetime.strptime(time, "%H:%M")
            time_formatted = time_obj.strftime("%I:%M %p")
        except:
            pass

        time_part = f" jo {time_formatted} baje tha" if time else ""
        return f"✅ Sir, maine {titles_str}{time_part} ko complete mark kar diya hai."

    # Handle Edit
    if intent == "edit":
        search = data.get("search_parameters", {})
        new_params = data.get("new_parameters", {})

        s_title = search.get("title")
        s_time = search.get("time")
        s_date = search.get("date")
        if not s_date:
            now_local = datetime.utcnow() + timedelta(hours=5, minutes=30)
            s_date = now_local.strftime("%Y-%m-%d")

        query = db.query(RootDailyPlan).filter(
            RootDailyPlan.client_id == client_id,
            RootDailyPlan.plan_date == s_date
        )
        if s_time:
            query = query.filter(RootDailyPlan.plan_time == s_time)

        plans = query.all()

        if s_title and plans:
            plans_filtered = []
            for p in plans:
                if s_title.lower() in p.title.lower() or p.title.lower() in s_title.lower():
                    plans_filtered.append(p)
            if plans_filtered:
                plans = plans_filtered

        if not plans:
            time_part = f" jo {s_time} baje tha" if s_time else ""
            title_part = f" '{s_title}'" if s_title else ""
            return f"Sir, {s_date}{time_part}{title_part} ka koi plan scheduled nahi mila."

        edited_plans = []
        for p in plans:
            old_title = p.title
            old_date = p.plan_date
            old_time = p.plan_time

            n_title = new_params.get("title")
            n_time = new_params.get("time")
            n_date = new_params.get("date")
            n_category = new_params.get("category")

            updates = []
            if n_title:
                p.title = n_title
                updates.append(f"Title to '{n_title}'")
            if n_time:
                p.plan_time = n_time
                updates.append(f"Time to '{n_time}'")
            if n_date:
                p.plan_date = n_date
                updates.append(f"Date to '{n_date}'")
            if n_category:
                p.category = n_category
                updates.append(f"Category to '{n_category}'")

            if not updates:
                return "Sir, kya edit karna hai (jaise title, date, ya time) kripya batayein."

            if p.category == "meeting" or p.from_meeting:
                try:
                    start_dt = datetime.strptime(f"{old_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
                    end_dt = datetime.strptime(f"{old_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
                    meeting = db.query(RootMeeting).filter(
                        RootMeeting.client_id == client_id,
                        RootMeeting.meeting_time >= start_dt,
                        RootMeeting.meeting_time <= end_dt,
                        RootMeeting.title == old_title
                    ).first()
                    if meeting:
                        if n_title:
                            meeting.title = n_title
                        if n_date or n_time:
                            final_date = n_date if n_date else old_date
                            final_time = n_time if n_time else old_time
                            meeting.meeting_time = datetime.strptime(f"{final_date} {final_time}", "%Y-%m-%d %H:%M")
                except Exception as me:
                    logger.warning(f"Error updating meeting during edit: {me}")

            edited_plans.append(f"{old_title} ({', '.join(updates)})")

        db.commit()
        return f"📝 Sir, maine plan successfully edit kar diya hai:\n" + "\n".join([f"- {ep}" for ep in edited_plans])

    # Handle Set
    if intent == "set":
        status = data.get("status", "incomplete")
        if status == "incomplete":
            return data.get("ask_clarification") or "Sir, schedule karne ke liye detail adhuri hai. Kripya puri detail batayein."

        # Complete - Save to Database
        params = data.get("extracted_parameters", {})
        title = params.get("title")
        date = params.get("date")
        time = params.get("time")
        category = params.get("category") or "work"
        target_type = data.get("target_type", "plan")

        if not title or not time:
            return "Sir, plan set karne ke liye title aur time hona jaruri hai."

        if not date:
            now_local = datetime.utcnow() + timedelta(hours=5, minutes=30)
            date = now_local.strftime("%Y-%m-%d")

        if target_type == "meeting":
            try:
                meeting_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
                dur_mins = params.get("duration_mins") or 30
                
                # 1. Create RootMeeting
                meeting_obj = RootMeeting(
                    meeting_id=secrets.token_hex(8),
                    client_id=client_id,
                    owner_id=client_id,
                    title=title,
                    description=f"Scheduled via Root Agent Voice/Chat: {title}",
                    meeting_time=meeting_dt,
                    duration_mins=dur_mins,
                    status="scheduled",
                    reminder_sent=False,
                    notification_sent=False,
                    created_at=datetime.utcnow()
                )
                db.add(meeting_obj)

                # 2. Sync to RootDailyPlan
                plan_obj = RootDailyPlan(
                    plan_id=secrets.token_hex(8),
                    client_id=client_id,
                    owner_id=client_id,
                    title=title,
                    description=f"Meeting: {title}",
                    category="meeting",
                    plan_date=date,
                    plan_time=time,
                    status="pending",
                    is_completed=False,
                    from_meeting=True,
                    duration_mins=dur_mins,
                    created_at=datetime.utcnow()
                )
                db.add(plan_obj)
                db.commit()

                time_formatted = meeting_dt.strftime("%d %b %Y, %I:%M %p")
                return f"🗓️ Sir, aapka Meeting successful set ho gaya hai!\n\n📌 Title: {title}\n⏰ Timing: {time_formatted}\n📍 Status: Scheduled"
            except Exception as e:
                db.rollback()
                logger.error(f"Error setting meeting: {e}")
                return "Sir, meeting set karte waqt technical error aaya."

        elif target_type == "reminder":
            try:
                plan_obj = RootDailyPlan(
                    plan_id=secrets.token_hex(8),
                    client_id=client_id,
                    owner_id=client_id,
                    title=title,
                    description=f"Reminder: {title}",
                    category="reminder",
                    plan_date=date,
                    plan_time=time,
                    status="pending",
                    is_completed=False,
                    from_meeting=False,
                    created_at=datetime.utcnow()
                )
                db.add(plan_obj)
                db.commit()

                time_formatted = time
                try:
                    time_obj = datetime.strptime(time, "%H:%M")
                    time_formatted = time_obj.strftime("%I:%M %p")
                except:
                    pass

                return f"🔔 Sir, aapka Reminder set ho gaya hai!\n\n📌 Title: {title}\n⏰ Timing: {date} at {time_formatted}"
            except Exception as e:
                db.rollback()
                logger.error(f"Error setting reminder: {e}")
                return "Sir, reminder set karte waqt technical error aaya."

        else:  # plan
            try:
                plan_obj = RootDailyPlan(
                    plan_id=secrets.token_hex(8),
                    client_id=client_id,
                    owner_id=client_id,
                    title=title,
                    description=f"Plan: {title}",
                    category=category,
                    plan_date=date,
                    plan_time=time,
                    status="pending",
                    is_completed=False,
                    from_meeting=False,
                    created_at=datetime.utcnow()
                )
                db.add(plan_obj)
                db.commit()

                time_formatted = time
                try:
                    time_obj = datetime.strptime(time, "%H:%M")
                    time_formatted = time_obj.strftime("%I:%M %p")
                except:
                    pass

                return f"✅ Sir, aapka Daily Plan set ho gaya hai!\n\n📌 Title: {title}\n⏰ Timing: {date} at {time_formatted}\n📁 Category: {category}"
            except Exception as e:
                db.rollback()
                logger.error(f"Error setting daily plan: {e}")
                return "Sir, plan set karte waqt technical error aaya."

    return None


# ── Root Agent Interactive Chat Engine ────────────────────────────────────────

@router.post("/root-agent/chat")
async def root_agent_chat(
    req: RootChatReq,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if req.client_id:
        client_id = req.client_id
    else:
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
        req_sess_id = req.session_id
        if req_sess_id:
            session_obj = db.query(AgentPublicSession).filter(
                AgentPublicSession.session_id == req_sess_id,
                AgentPublicSession.agent_id == root_agent.agent_id
            ).first()

        if not session_obj:
            if not req_sess_id:
                req_sess_id = f"root_sess_{client_id}_{secrets.token_hex(4)}"

            session_obj = AgentPublicSession(
                session_id=req_sess_id,
                agent_id=root_agent.agent_id,
                device_id="root_chat",
                user_name="Owner",
                device_name=msg_raw[:40] + ("..." if len(msg_raw) > 40 else ""),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(session_obj)
            db.commit()
            db.refresh(session_obj)
        else:
            if session_obj.device_name in ["Owner Workspace", "Unknown Device", "New Chat", ""]:
                session_obj.device_name = msg_raw[:40] + ("..." if len(msg_raw) > 40 else "")
            session_obj.updated_at = datetime.utcnow()
            db.commit()

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

    # ── Intercept Daily Planner / Reminders / Meetings Intents ──
    parser_history = []
    if session_obj:
        try:
            msgs = db.query(AgentPublicMessage).filter(
                AgentPublicMessage.session_id == session_obj.session_id
            ).order_by(AgentPublicMessage.created_at.desc()).limit(6).all()
            msgs.reverse()
            for m in msgs:
                if m.content.strip() != msg_raw:
                    parser_history.append({"role": m.role, "content": m.content})
        except Exception as he:
            logger.warning(f"Error fetching message history: {he}")

    planner_resp = await handle_planner_voice_and_chat(
        message=msg_raw,
        history=parser_history,
        client_id=client_id,
        db=db,
        voice_mode=False
    )
    if planner_resp:
        _save_asst_msg(planner_resp)
        return {
            "role": "assistant",
            "content": planner_resp,
            "answer": planner_resp,
            "media": None,
            "agent_id": root_agent.agent_id,
            "session_id": session_obj.session_id
        }

    # ──────────────────────────────────────────────────────────────────────────
    # INTENT 1: Meeting Creation / Schedule (HIGHEST PRIORITY if meeting word present)
    # ──────────────────────────────────────────────────────────────────────────
    is_meeting_word = any(w in msg_lower for w in ["meeting", "metting", "appointment", "calendar entry"])
    is_save_action = any(w in msg_lower for w in ["save", "schedule", "set", "kar do", "kr do", "store", "add"])
    is_inquiry_word = any(w in msg_lower for w in ["kab", "konsi", "dikhao", "batao", "show", "list", "kya", "status", "hai kya"])

    if is_meeting_word and is_save_action and not (is_inquiry_word and "save" not in msg_lower):
        title, meeting_dt = _parse_meeting_details(msg_raw)
        
        dur_mins = 30
        dur_match = re.search(r'(\d+)\s*(hour|hr|ghanta|ghante|minute|min|m)', msg_lower)
        if dur_match:
            try:
                val = int(dur_match.group(1))
                unit = dur_match.group(2)
                if any(x in unit for x in ['hour', 'hr', 'ghant']):
                    dur_mins = val * 60
                else:
                    dur_mins = val
            except Exception:
                pass

        meeting_obj = RootMeeting(
            meeting_id=secrets.token_hex(8),
            client_id=client_id,
            owner_id=client_id,
            title=title,
            description=msg_raw,
            meeting_time=meeting_dt,
            duration_mins=dur_mins,
            status="scheduled",
            reminder_sent=False,
            notification_sent=False,
            created_at=datetime.utcnow()
        )
        db.add(meeting_obj)
        db.commit()
        db.refresh(meeting_obj)

        # Sync to RootDailyPlan so it displays in the daily planner checklist/sidebar
        try:
            plan_date = meeting_dt.strftime("%Y-%m-%d")
            plan_time = meeting_dt.strftime("%H:%M")
            plan_obj = RootDailyPlan(
                plan_id=secrets.token_hex(8),
                client_id=client_id,
                owner_id=client_id,
                title=title,
                description=f"Meeting: {title}",
                category="meeting",
                plan_date=plan_date,
                plan_time=plan_time,
                status="pending",
                is_completed=False,
                from_meeting=True,
                duration_mins=dur_mins,
                created_at=datetime.utcnow()
            )
            db.add(plan_obj)
            db.commit()
        except Exception as sync_err:
            logger.warning(f"Error syncing INTENT 1 meeting to RootDailyPlan: {sync_err}")
            db.rollback()

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
        "agent_id": root_agent.agent_id,
        "session_id": session_obj.session_id
    }


# ── Root Agent Chat History Retrieval Endpoint ───────────────────────────────

@router.get("/root-agent/history")
def get_root_agent_history(
    session_id: Optional[str] = Query(None),
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    root_agent = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == True).first()
    if not root_agent:
        return {"session_id": None, "messages": []}

    session_obj = None
    if session_id:
        session_obj = db.query(AgentPublicSession).filter(
            AgentPublicSession.session_id == session_id,
            AgentPublicSession.agent_id == root_agent.agent_id
        ).first()
    else:
        session_obj = db.query(AgentPublicSession).filter(
            AgentPublicSession.agent_id == root_agent.agent_id
        ).order_by(AgentPublicSession.updated_at.desc()).first()

    if not session_obj:
        return {"session_id": None, "messages": []}

    messages = db.query(AgentPublicMessage).filter(
        AgentPublicMessage.session_id == session_obj.session_id
    ).order_by(AgentPublicMessage.created_at.asc()).all()

    return {
        "session_id": session_obj.session_id,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else "",
                "file_url": m.file_url or "",
                "file_name": m.file_name or "",
                "file_type": m.file_type or ""
            } for m in messages
        ]
    }


@router.get("/root-agent/sessions")
def get_root_agent_sessions(
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    root_agent = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == True).first()
    if not root_agent:
        return []

    sessions = db.query(AgentPublicSession).filter(
        AgentPublicSession.agent_id == root_agent.agent_id
    ).order_by(AgentPublicSession.updated_at.desc()).all()

    return [s.to_dict() for s in sessions]


@router.delete("/root-agent/sessions/{session_id}")
def delete_root_agent_session(
    session_id: str,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    root_agent = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == True).first()
    if not root_agent:
        raise HTTPException(status_code=404, detail="Root Agent not found")

    session_obj = db.query(AgentPublicSession).filter(
        AgentPublicSession.session_id == session_id,
        AgentPublicSession.agent_id == root_agent.agent_id
    ).first()

    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    db.delete(session_obj)
    db.commit()
    return {"status": "success", "message": "Session deleted successfully"}


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
    duration_mins: Optional[int] = 30
    is_recurring: Optional[bool] = False

class EditPlanReq(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    plan_date: Optional[str] = None
    plan_time: Optional[str] = None
    status: Optional[str] = None
    duration_mins: Optional[int] = None
    is_recurring: Optional[bool] = None

class MeetingToPlanReq(BaseModel):
    title: str
    description: Optional[str] = ""
    plan_date: str   # YYYY-MM-DD
    plan_time: str   # HH:MM
    source_agent_id: Optional[str] = None
    duration_mins: Optional[int] = 30
    is_recurring: Optional[bool] = False


def _create_next_recurring_occurrence(plan: RootDailyPlan, db: Session):
    if not plan.is_recurring or plan.category != "meeting":
        return

    try:
        # Calculate next day's date string YYYY-MM-DD
        current_date_dt = datetime.strptime(plan.plan_date, "%Y-%m-%d")
        next_date_dt = current_date_dt + timedelta(days=1)
        next_date_str = next_date_dt.strftime("%Y-%m-%d")

        # Check if already exists for tomorrow to avoid duplicates
        existing = db.query(RootDailyPlan).filter(
            RootDailyPlan.client_id == plan.client_id,
            RootDailyPlan.title == plan.title,
            RootDailyPlan.plan_date == next_date_str,
            RootDailyPlan.plan_time == plan.plan_time
        ).first()

        if existing:
            logger.info(f"Recurring plan already exists for tomorrow: {next_date_str}")
            return

        # Create next day daily plan
        next_plan = RootDailyPlan(
            plan_id=secrets.token_hex(8),
            client_id=plan.client_id,
            owner_id=plan.owner_id,
            title=plan.title,
            description=plan.description or "",
            category="meeting",
            plan_date=next_date_str,
            plan_time=plan.plan_time,
            status="pending",
            is_completed=False,
            from_meeting=True,
            duration_mins=plan.duration_mins or 30,
            is_recurring=True,
            created_at=datetime.utcnow()
        )
        db.add(next_plan)

        # Sync next day RootMeeting record
        meeting_dt = datetime.strptime(f"{next_date_str} {plan.plan_time}", "%Y-%m-%d %H:%M")
        meeting_obj = RootMeeting(
            meeting_id=secrets.token_hex(8),
            client_id=plan.client_id,
            owner_id=plan.owner_id,
            title=plan.title,
            description=plan.description or "",
            meeting_time=meeting_dt,
            duration_mins=plan.duration_mins or 30,
            status="scheduled",
            reminder_sent=False,
            notification_sent=False,
            created_at=datetime.utcnow()
        )
        db.add(meeting_obj)
        db.flush()
        logger.info(f"Created next recurring meeting for {next_date_str} {plan.plan_time}")
    except Exception as e:
        logger.error(f"Error creating next recurring occurrence: {e}")


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

    dur_mins = req.duration_mins if req.duration_mins is not None else 30

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
        from_meeting=(req.category == "meeting"),
        duration_mins=dur_mins,
        is_recurring=bool(req.is_recurring),
        created_at=datetime.utcnow()
    )
    db.add(plan)

    if req.category == "meeting":
        try:
            meeting_dt = datetime.strptime(f"{req.plan_date} {req.plan_time}", "%Y-%m-%d %H:%M")
            meeting_obj = RootMeeting(
                meeting_id=secrets.token_hex(8),
                client_id=client_id,
                owner_id=client_id,
                title=req.title.strip(),
                description=req.description or "",
                meeting_time=meeting_dt,
                duration_mins=dur_mins,
                status="scheduled",
                reminder_sent=False,
                notification_sent=False,
                created_at=datetime.utcnow()
            )
            db.add(meeting_obj)
        except Exception as me:
            logger.error(f"Error creating matching RootMeeting: {me}")

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
    
    if plan.is_completed:
        _create_next_recurring_occurrence(plan, db)

    db.commit()
    db.refresh(plan)

    d = plan.to_dict()
    d["status"] = plan.status
    return {"success": True, "plan": d}


@router.put("/root-agent/plans/{plan_id}")
async def edit_daily_plan(
    plan_id: str,
    req: EditPlanReq,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Edit/update an existing daily plan."""
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    plan = db.query(RootDailyPlan).filter(
        RootDailyPlan.plan_id == plan_id,
        RootDailyPlan.client_id == client_id
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    old_title = plan.title
    old_date = plan.plan_date
    old_time = plan.plan_time
    was_completed = plan.is_completed

    if req.title is not None:
        plan.title = req.title
    if req.description is not None:
        plan.description = req.description
    if req.category is not None:
        plan.category = req.category
    if req.plan_date is not None:
        plan.plan_date = req.plan_date
    if req.plan_time is not None:
        plan.plan_time = req.plan_time
    if req.duration_mins is not None:
        plan.duration_mins = req.duration_mins
    if req.is_recurring is not None:
        plan.is_recurring = req.is_recurring
    if req.status is not None:
        plan.status = req.status
        if req.status == "completed":
            plan.is_completed = True
            plan.completed_at = datetime.utcnow()
        else:
            plan.is_completed = False
            plan.completed_at = None

    if plan.is_completed and not was_completed:
        _create_next_recurring_occurrence(plan, db)

    if plan.category == "meeting" or plan.from_meeting:
        try:
            start_dt = datetime.strptime(f"{old_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(f"{old_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
            meeting = db.query(RootMeeting).filter(
                RootMeeting.client_id == client_id,
                RootMeeting.meeting_time >= start_dt,
                RootMeeting.meeting_time <= end_dt,
                RootMeeting.title == old_title
            ).first()
            if meeting:
                if req.title is not None:
                    meeting.title = req.title
                if req.description is not None:
                    meeting.description = req.description
                if req.plan_date is not None or req.plan_time is not None:
                    final_date = req.plan_date if req.plan_date is not None else old_date
                    final_time = req.plan_time if req.plan_time is not None else old_time
                    meeting.meeting_time = datetime.strptime(f"{final_date} {final_time}", "%Y-%m-%d %H:%M")
                if req.status is not None:
                    meeting.status = req.status
                if req.duration_mins is not None:
                    meeting.duration_mins = req.duration_mins
        except Exception as me:
            logger.warning(f"Error syncing edit to RootMeeting: {me}")

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
                
                # Setup next day occurrence if recurring
                _create_next_recurring_occurrence(p, db)
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
        duration_mins=req.duration_mins if req.duration_mins is not None else 30,
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


@router.post("/root-agent/plans/analyze")
async def analyze_daily_plans(
    req: AnalyzePlansReq,
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]
    plan_date = req.plan_date
    
    # 1. Fetch plans for that date
    plans = db.query(RootDailyPlan).filter(
        RootDailyPlan.client_id == client_id,
        RootDailyPlan.plan_date == plan_date
    ).all()
    
    # Compute counts
    total_count = len(plans)
    completed_count = 0
    pending_count = 0
    
    plans_text = []
    for idx, p in enumerate(plans, 1):
        status_str = _compute_plan_status(p.plan_date, p.plan_time, p.is_completed)
        if status_str == "completed":
            completed_count += 1
        else:
            pending_count += 1
        
        category_str = p.category.upper()
        plans_text.append(
            f"{idx}. [{category_str}] {p.title} at {p.plan_time} - Status: {status_str.capitalize()}\n"
            f"   Description: {p.description or 'No description'}"
        )
    
    plans_summary_input = "\n\n".join(plans_text) if plans_text else "No plans scheduled today."
        
    # 2. Get Root Agent configuration
    root_agent = db.query(Agent).filter(Agent.client_id == client_id, Agent.is_root == True).first()
    provider = "gemini"
    model = "gemini-3.5-flash"
    api_key = ""
    if root_agent:
        try:
            s_cfg = json.loads(root_agent.system_config_json or "{}")
            provider = s_cfg.get('provider', 'gemini')
            model = s_cfg.get('model', 'gemini-3.5-flash')
            api_key = s_cfg.get('api_key', '')
        except Exception:
            pass

    if provider == 'gemini' and not api_key:
        import os
        api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

    # 4. Build prompt
    system_prompt = (
        "You are a Personal AI Productivity Advisor utilizing the PACE Framework (Priorities, Allocation, Control, Efficiency).\n"
        "You analyze the owner's daily schedule and plans to assess how intentionally their time was allocated, structured, and how it can be improved. Your job is not to measure busyness, but to answer: 'Did the user invest their available time in the right things, with the right structure, and what should change next?'\n\n"
        "You MUST return a JSON response object matching this exact schema:\n"
        "{\n"
        "  \"summary\": \"Must start with 'PACE — [Date]'. Provide a brief analytical summary of the entire day's plans. End with the Day Insight as the last sentence (e.g. 'Day Insight: Your calendar protected time for your core priorities, but fragmented meetings reduced execution blocks.'). Written in Hinglish/Hindi or English.\",\n"
        "  \"analysis\": \"P — Priorities:\\nExplain whether the calendar appears aligned with the user's priorities, highlighting the strongest alignment and most important apparent gap.\\n\\nA — Allocation:\\nSummarise where scheduled time went, focusing on patterns, durations, or proportions of scheduled work, meetings, personal time, etc. Written in Hinglish/Hindi or English.\",\n"
        "  \"feedback\": \"C — Control:\\nExplain how intentionally the day was structured. Identify fragmentation, back-to-back meetings, focus blocks, buffers, context switching, and whether structure was intentional or reactive. Written in Hinglish/Hindi or English.\",\n"
        "  \"key_points\": [\n"
        "    \"Improvement 1 (e.g., 'Schedule a 2-hour morning focus block for your core tasks.')\",\n"
        "    \"Improvement 2 (e.g., 'Batch small admin tasks together.')\",\n"
        "    \"Improvement 3 (e.g., 'Reduce meeting fragmentation by protecting open slot.')\"\n"
        "  ],\n"
        "  \"pace_structure\": {\n"
        "    \"priorities\": {\n"
        "      \"status\": \"Aligned\" or \"Partially Aligned\" or \"Misaligned\",\n"
        "      \"strong_alignment\": \"What went well regarding priorities alignment (1 short sentence)\",\n"
        "      \"biggest_gap\": \"The main priority gap or missing item (1 short sentence)\"\n"
        "    },\n"
        "    \"allocation\": {\n"
        "      \"total_scheduled\": \"Total scheduled time estimated (e.g., '9h 30m')\",\n"
        "      \"categories\": [\n"
        "        {\n"
        "          \"name\": \"Client / Customer\" or \"Focused Work\" or \"Team / Leadership\" or \"Operations\" or \"Strategy / Planning\" or \"Open Time\",\n"
        "          \"duration\": \"Estimated time (e.g., '3h 00m')\",\n"
        "          \"percentage\": Percentage value as integer (e.g., 32),\n"
        "          \"color\": \"Hex code (e.g. #818cf8 for Client / Customer, #3b82f6 for Focused Work, #10b981 for Team / Leadership, #f59e0b for Operations, #ec4899 for Strategy / Planning, #94a3b8 for Open Time)\"\n"
        "        }\n"
        "      ]\n"
        "    },\n"
        "    \"control\": {\n"
        "      \"status\": \"High\" or \"Moderate\" or \"Low\",\n"
        "      \"status_color\": \"#10b981\" or \"#f59e0b\" or \"#ef4444\",\n"
        "      \"good\": \"Positive aspect of daily structure (1 short phrase, e.g., '2 focus blocks protected')\",\n"
        "      \"watch_out\": \"Warning sign or risk (1 short phrase, e.g., 'Back-to-back meetings in afternoon')\",\n"
        "      \"needs_more\": \"Where support is needed (1 short phrase, e.g., 'Short gaps causing context switching')\"\n"
        "    },\n"
        "    \"efficiency\": {\n"
        "      \"status\": \"High Impact\" or \"Medium Impact\" or \"Low Impact\",\n"
        "      \"actions\": [\n"
        "        {\n"
        "          \"icon\": \"users\" or \"clock\" or \"target\" or \"calendar\" or \"zap\",\n"
        "          \"title\": \"Title of concrete action (e.g., 'Batch 3 internal meetings into one block')\",\n"
        "          \"description\": \"Subtext explaining value (e.g., 'Save ~45 mins and reduce context switching.')\"\n"
        "        }\n"
        "      ]\n"
        "    },\n"
        "    \"day_insight\": \"Brief, punchy final insight sentence matching the last sentence of the summary.\"\n"
        "  }\n"
        "}\n\n"
        "EVIDENCE & TONE RULES:\n"
        "1. Tone: Executive advisor (concise, analytical, neutral, practical, action-oriented). No lecturing or excessive praise. Reading time should be ~1 minute.\n"
        "2. Evidence: Base conclusions on visible calendar evidence. For Allocation category durations, sum/estimate reasonable times for tasks (e.g. meetings = 30-60 mins, focus blocks = 1-2 hours) to sum up to total_scheduled.\n"
        "3. Language: Hinglish/Hindi or English matching the user.\n"
        "4. If no plans exist: Suggest the user rest or enjoy their free time, total_scheduled '0h 00m' with 'Open Time' at 100%.\n"
        "5. The 'key_points' array MUST contain exactly 3 concrete, actionable improvements for the user's daily plan based on the analysis. Written in Hinglish/Hindi or English.\n"
        "Return ONLY the raw JSON string matching the structure above."
    )


    question = (
        f"Daily plans analysis for date: {plan_date}\n"
        f"Metrics:\n"
        f"- Total plans in a day: {total_count}\n"
        f"- Completed plans today: {completed_count}\n"
        f"- Pending/not completed plans today: {pending_count}\n\n"
        f"Scheduled Plans details:\n"
        f"{plans_summary_input}\n\n"
        f"Please analyze this day's completed and not completed plans and return the JSON object."
    )

    from app.services.llm import llm_with_history
    try:
        raw_result = await llm_with_history(
            question=question,
            system=system_prompt,
            history=[],
            provider=provider,
            model=model,
            api_key=api_key,
            ollama_url="http://localhost:11434"
        )
    except Exception as e:
        logger.error(f"Daily plans analysis LLM call failed: {e}")
        try:
            import os
            fallback_api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
            raw_result = await llm_with_history(
                question=question,
                system=system_prompt,
                history=[],
                provider="gemini",
                model="gemini-3.5-flash",
                api_key=fallback_api_key,
                ollama_url="http://localhost:11434"
            )
        except Exception as fallback_err:
            raise HTTPException(status_code=502, detail=f"Analysis failed: {fallback_err}")

    # Extract JSON
    import re
    match = re.search(r"```json\s*(.*?)\s*```", raw_result, re.DOTALL | re.IGNORECASE)
    if match:
        json_content = match.group(1).strip()
    else:
        match_simple = re.search(r"```\s*(.*?)\s*```", raw_result, re.DOTALL)
        if match_simple:
            json_content = match_simple.group(1).strip()
        else:
            json_content = raw_result.strip()

    try:
        from app.routes.agents import repair_json
        try:
            analyzed_data = json.loads(json_content)
        except Exception:
            repaired = repair_json(json_content)
            analyzed_data = json.loads(repaired)
        
        # Validate keys
        for key in ["summary", "feedback", "analysis"]:
            if key not in analyzed_data:
                analyzed_data[key] = "Not specified"
        if "key_points" not in analyzed_data or not isinstance(analyzed_data["key_points"], list):
            analyzed_data["key_points"] = []
            
        # Ensure pace_structure contains day_insight/daily_insight compatibility
        if "pace_structure" in analyzed_data and isinstance(analyzed_data["pace_structure"], dict):
            ps = analyzed_data["pace_structure"]
            if "day_insight" in ps:
                ps["daily_insight"] = ps["day_insight"]
            elif "daily_insight" in ps:
                ps["day_insight"] = ps["daily_insight"]
    except Exception as parse_err:
        logger.error(f"Failed to parse LLM analysis: {parse_err}. Raw content: {raw_result}")
        analyzed_data = {
            "summary": "Failed to parse analysis from LLM.",
            "feedback": raw_result,
            "analysis": "Please check raw logs.",
            "key_points": []
        }

    # Save/Update in DB
    analysis_record = db.query(RootDailyPlanAnalysis).filter(
        RootDailyPlanAnalysis.client_id == client_id,
        RootDailyPlanAnalysis.plan_date == plan_date
    ).first()

    pace_json_str = json.dumps(analyzed_data.get("pace_structure", {}))

    if not analysis_record:
        analysis_record = RootDailyPlanAnalysis(
            analysis_id=secrets.token_hex(8),
            client_id=client_id,
            plan_date=plan_date,
            summary=analyzed_data["summary"],
            feedback=analyzed_data["feedback"],
            analysis=analyzed_data["analysis"],
            key_points=json.dumps(analyzed_data["key_points"]),
            pace_json=pace_json_str,
            created_at=datetime.utcnow()
        )
        db.add(analysis_record)
    else:
        analysis_record.summary = analyzed_data["summary"]
        analysis_record.feedback = analyzed_data["feedback"]
        analysis_record.analysis = analyzed_data["analysis"]
        analysis_record.key_points = json.dumps(analyzed_data["key_points"])
        analysis_record.pace_json = pace_json_str
        analysis_record.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(analysis_record)

    return analysis_record.to_dict()


@router.get("/root-agent/plans/analyze")
async def get_daily_plans_analysis(
    plan_date: str = Query(...),
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]
    
    analysis_record = db.query(RootDailyPlanAnalysis).filter(
        RootDailyPlanAnalysis.client_id == client_id,
        RootDailyPlanAnalysis.plan_date == plan_date
    ).first()

    if not analysis_record:
        return {}

    return analysis_record.to_dict()


@router.get("/root-agent/pings/stats")
async def get_root_pings_stats(
    period: str = Query("today"), # today | yesterday | this_week | all
    start_date: Optional[str] = Query(None), # YYYY-MM-DD
    end_date: Optional[str] = Query(None), # YYYY-MM-DD
    x_app_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Computes comparative stats for pings, sources, outcomes and sub-agents.
    """
    from app.core.models import AgentFeedback
    
    client = _get_owner_client(x_app_token, db)
    client_id = client["client_id"]

    # Fetch client's agents
    agents = db.query(Agent).filter(Agent.client_id == client_id).all()
    agent_ids = [a.agent_id for a in agents]
    
    if not agent_ids:
        return {
            "total_pings": {"count": 0, "growth": "0%", "growth_text": "0% vs Previous", "is_positive": True},
            "conversations": {"count": 0, "growth": "0%", "growth_text": "0% vs Previous", "is_positive": True},
            "sources": {
                "whatsapp": {"count": 0, "growth": "0% ↑", "is_positive": True},
                "chats": {"count": 0, "growth": "0% ↑", "is_positive": True},
                "calls": {"count": 0, "growth": "0% ↑", "is_positive": True},
                "widgets": {"count": 0, "growth": "0% ↑", "is_positive": True}
            },
            "outcomes": {
                "meetings": {"count": 0, "growth": "0% ↑", "is_positive": True},
                "enquiry": {"count": 0, "growth": "0% ↑", "is_positive": True},
                "support": {"count": 0, "growth": "0% ↑", "is_positive": True},
                "feedback": {"count": 0, "growth": "0% ↑", "is_positive": True},
                "others": {"count": 0, "growth": "0% ↑", "is_positive": True}
            },
            "agents": [],
            "clients": []
        }

    now = datetime.utcnow()
    prev_label = "Previous"
    if start_date and end_date:
        try:
            curr_start = datetime.strptime(start_date, "%Y-%m-%d")
            curr_end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            duration = curr_end - curr_start
            prev_start = curr_start - duration
            prev_end = curr_start
            prev_label = "Prev Period"
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    elif period == "yesterday":
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        curr_start = today_midnight - timedelta(days=1)
        curr_end = today_midnight
        prev_start = today_midnight - timedelta(days=2)
        prev_end = today_midnight - timedelta(days=1)
        prev_label = "Day Before"
    elif period == "this_week":
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        curr_start = today_midnight - timedelta(days=now.weekday())
        curr_end = now
        prev_start = curr_start - timedelta(days=7)
        prev_end = curr_start
        prev_label = "Prev Week"
    elif period == "all":
        curr_start = None
        curr_end = None
        prev_start = None
        prev_end = None
        prev_label = "Previous"
    else: # Default is "today"
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        curr_start = today_midnight
        curr_end = now
        prev_start = today_midnight - timedelta(days=1)
        prev_end = today_midnight
        prev_label = "Yesterday"

    # Query current sessions
    curr_sess_q = db.query(AgentPublicSession).filter(AgentPublicSession.agent_id.in_(agent_ids))
    if curr_start:
        curr_sess_q = curr_sess_q.filter(AgentPublicSession.created_at >= curr_start)
    if curr_end:
        curr_sess_q = curr_sess_q.filter(AgentPublicSession.created_at < curr_end)
    curr_sessions = curr_sess_q.all()

    # Query previous sessions
    prev_sessions = []
    if prev_start and prev_end:
        prev_sess_q = db.query(AgentPublicSession).filter(AgentPublicSession.agent_id.in_(agent_ids))
        prev_sess_q = prev_sess_q.filter(AgentPublicSession.created_at >= prev_start)
        prev_sess_q = prev_sess_q.filter(AgentPublicSession.created_at < prev_end)
        prev_sessions = prev_sess_q.all()

    # Query current messages (Total Pings where role == 'user')
    curr_msgs_q = db.query(AgentPublicMessage).join(AgentPublicSession).filter(
        AgentPublicSession.agent_id.in_(agent_ids),
        AgentPublicMessage.role == "user"
    )
    if curr_start:
        curr_msgs_q = curr_msgs_q.filter(AgentPublicMessage.created_at >= curr_start)
    if curr_end:
        curr_msgs_q = curr_msgs_q.filter(AgentPublicMessage.created_at < curr_end)
    curr_msg_count = curr_msgs_q.count()

    # Query previous messages
    prev_msg_count = 0
    if prev_start and prev_end:
        prev_msgs_q = db.query(AgentPublicMessage).join(AgentPublicSession).filter(
            AgentPublicSession.agent_id.in_(agent_ids),
            AgentPublicMessage.role == "user"
        )
        prev_msgs_q = prev_msgs_q.filter(AgentPublicMessage.created_at >= prev_start)
        prev_msgs_q = prev_msgs_q.filter(AgentPublicMessage.created_at < prev_end)
        prev_msg_count = prev_msgs_q.count()

    # Query meetings scheduled
    curr_meetings_q = db.query(RootMeeting).filter(RootMeeting.client_id == client_id)
    if curr_start:
        curr_meetings_q = curr_meetings_q.filter(RootMeeting.created_at >= curr_start)
    if curr_end:
        curr_meetings_q = curr_meetings_q.filter(RootMeeting.created_at < curr_end)
    curr_meeting_count = curr_meetings_q.count()

    prev_meeting_count = 0
    if prev_start and prev_end:
        prev_meetings_q = db.query(RootMeeting).filter(RootMeeting.client_id == client_id)
        prev_meetings_q = prev_meetings_q.filter(RootMeeting.created_at >= prev_start)
        prev_meetings_q = prev_meetings_q.filter(RootMeeting.created_at < prev_end)
        prev_meeting_count = prev_meetings_q.count()

    # Query feedback submitted
    curr_feedback_q = db.query(AgentFeedback).filter(AgentFeedback.agent_id.in_(agent_ids))
    if curr_start:
        curr_feedback_q = curr_feedback_q.filter(AgentFeedback.created_at >= curr_start)
    if curr_end:
        curr_feedback_q = curr_feedback_q.filter(AgentFeedback.created_at < curr_end)
    curr_feedback_count = curr_feedback_q.count()

    prev_feedback_count = 0
    if prev_start and prev_end:
        prev_feedback_q = db.query(AgentFeedback).filter(AgentFeedback.agent_id.in_(agent_ids))
        prev_feedback_q = prev_feedback_q.filter(AgentFeedback.created_at >= prev_start)
        prev_feedback_q = prev_feedback_q.filter(AgentFeedback.created_at < prev_end)
        prev_feedback_count = prev_feedback_q.count()

    # Source & Outcome Classifier Helper
    def get_source_and_category(session):
        sid = (session.session_id or "")
        dname = (session.device_name or "").lower()
        
        # Source Classification
        if sid.startswith("wa_") or "whatsapp" in dname:
            source = "whatsapp"
        elif sid.startswith("tel_") or "voice call" in dname or "call" in dname:
            source = "calls"
        elif sid.startswith("wid_") or "widget" in dname:
            source = "widgets"
        else:
            source = "chats"
            
        # Outcome Category Classification
        category = "others"
        if session.analysis_json:
            try:
                js = json.loads(session.analysis_json)
                cat = (js.get("category") or "").lower()
                if "meeting" in cat:
                    category = "meetings"
                elif cat in ["marketing", "calling", "enquiry"]:
                    category = "enquiry"
                elif cat in ["support", "help"]:
                    category = "support"
                elif cat in ["feedback", "report"]:
                    category = "feedback"
            except:
                pass
        return source, category

    # Aggregate current stats
    curr_sources = {"whatsapp": 0, "chats": 0, "calls": 0, "widgets": 0}
    curr_categories = {"meetings": 0, "enquiry": 0, "support": 0, "feedback": 0, "others": 0}
    for s in curr_sessions:
        src, cat = get_source_and_category(s)
        curr_sources[src] += 1
        curr_categories[cat] += 1

    # Aggregate previous stats
    prev_sources = {"whatsapp": 0, "chats": 0, "calls": 0, "widgets": 0}
    prev_categories = {"meetings": 0, "enquiry": 0, "support": 0, "feedback": 0, "others": 0}
    for s in prev_sessions:
        src, cat = get_source_and_category(s)
        prev_sources[src] += 1
        prev_categories[cat] += 1

    # Adjust meetings and feedback with direct table count aggregates
    curr_categories["meetings"] = max(curr_categories["meetings"], curr_meeting_count)
    prev_categories["meetings"] = max(prev_categories["meetings"], prev_meeting_count)
    curr_categories["feedback"] = max(curr_categories["feedback"], curr_feedback_count)
    prev_categories["feedback"] = max(prev_categories["feedback"], prev_feedback_count)

    # Growth helper logic
    def calc_growth_card(curr, prev):
        if prev > 0:
            val = round(((curr - prev) / prev) * 100)
            is_pos = val >= 0
            sign = "↑" if is_pos else "↓"
            return {
                "count": curr,
                "growth": f"{sign} {abs(val)}%",
                "growth_text": f"vs {prev_label}",
                "is_positive": is_pos
            }
        else:
            return {
                "count": curr,
                "growth": "↑ 100%" if curr > 0 else "0%",
                "growth_text": f"vs {prev_label}",
                "is_positive": True
            }

    def calc_growth_simple(curr, prev):
        if prev > 0:
            val = round(((curr - prev) / prev) * 100)
            is_pos = val >= 0
            sign = "↑" if is_pos else "↓"
            return {
                "count": curr,
                "growth": f"{sign} {abs(val)}%",
                "is_positive": is_pos
            }
        else:
            return {
                "count": curr,
                "growth": "↑ 100%" if curr > 0 else "0%",
                "is_positive": True
            }

    # Build response data structure
    res_data = {
        "total_pings": calc_growth_card(curr_msg_count, prev_msg_count),
        "conversations": calc_growth_card(len(curr_sessions), len(prev_sessions)),
        "sources": {
            k: calc_growth_simple(curr_sources[k], prev_sources[k]) for k in curr_sources
        },
        "outcomes": {
            k: calc_growth_simple(curr_categories[k], prev_categories[k]) for k in curr_categories
        },
        "agents": [],
        "clients": []
    }

    # Aggregate Monitored Sub-Agents
    for a in agents:
        sa_sess_q = db.query(AgentPublicSession).filter(AgentPublicSession.agent_id == a.agent_id)
        if curr_start:
            sa_sess_q = sa_sess_q.filter(AgentPublicSession.created_at >= curr_start)
        if curr_end:
            sa_sess_q = sa_sess_q.filter(AgentPublicSession.created_at < curr_end)
        sa_sessions = sa_sess_q.all()
        
        web_chat = 0
        web_call = 0
        meeting_req = 0
        enquiry_cnt = 0
        other_cnt = 0
        
        for s in sa_sessions:
            src, cat = get_source_and_category(s)
            if src == "chats":
                web_chat += 1
            elif src == "calls":
                web_call += 1
                
            if cat == "meetings":
                meeting_req += 1
            elif cat == "enquiry":
                enquiry_cnt += 1
            else:
                other_cnt += 1
        
        res_data["agents"].append({
            "agent_id": a.agent_id,
            "name": a.name,
            "category": a.category,
            "is_active": a.is_active,
            "is_root": a.is_root,
            "total_visitors": len(sa_sessions),
            "totalChats": len(sa_sessions),
            "webChat": web_chat,
            "webCall": web_call,
            "meetingRequest": meeting_req,
            "enquiry": enquiry_cnt,
            "other": other_cnt
        })

    # Aggregate Client-Wise Breakdown (Owner + Sub-clients)
    clients_stats = []
    parent_client_obj = db.query(Client).filter(Client.client_id == client_id).first()
    if parent_client_obj:
        clients_to_query = [parent_client_obj]
        sub_clients = db.query(Client).filter(Client.created_by_client_id == client_id).all()
        clients_to_query.extend(sub_clients)
    else:
        clients_to_query = []

    for c in clients_to_query:
        c_agents = db.query(Agent).filter(Agent.client_id == c.client_id).all()
        c_agent_ids = [a.agent_id for a in c_agents]
        
        # Count meetings
        c_meetings_q = db.query(RootMeeting).filter(RootMeeting.client_id == c.client_id)
        if curr_start:
            c_meetings_q = c_meetings_q.filter(RootMeeting.created_at >= curr_start)
        if curr_end:
            c_meetings_q = c_meetings_q.filter(RootMeeting.created_at < curr_end)
        c_meetings_count = c_meetings_q.count()
        
        # Count pings
        c_pings_count = 0
        if c_agent_ids:
            c_msgs_q = db.query(AgentPublicMessage).join(AgentPublicSession).filter(
                AgentPublicSession.agent_id.in_(c_agent_ids),
                AgentPublicMessage.role == "user"
            )
            if curr_start:
                c_msgs_q = c_msgs_q.filter(AgentPublicMessage.created_at >= curr_start)
            if curr_end:
                c_msgs_q = c_msgs_q.filter(AgentPublicMessage.created_at < curr_end)
            c_pings_count = c_msgs_q.count()
            
        clients_stats.append({
            "client_id": c.client_id,
            "name": c.name or c.business_name or "Unnamed Client",
            "business_name": c.business_name or "",
            "meetings_count": c_meetings_count,
            "total_pings": c_pings_count
        })
        
    res_data["clients"] = clients_stats

    return {
        "success": True,
        "summary": res_data
    }

