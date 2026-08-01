"""
MR AI RAG - Vobiz Cloud Telephony Routes
Natural voice conversation using Record + Groq Whisper STT + Agent TTS (ElevenLabs/OpenAI)
"""

import logging
import httpx
import asyncio
import os
import uuid
import json
import re
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.routes.agents import api_agent_public_ask, AgentPublicAskReq
from app.services.telephony_service import trigger_outbound_call
from app.core.models import Agent, AgentPublicSession, AgentPublicMessage

logger = logging.getLogger(__name__)
router = APIRouter()

# Reusable HTTP client to keep connections alive and avoid DNS/TCP/SSL handshake latency
http_client = httpx.AsyncClient(timeout=60.0)

@router.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()



# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_absolute_url(request: Request, path: str) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    scheme = request.headers.get("x-forwarded-proto") or "https"
    if "ngrok" in host:
        scheme = "https"
    return f"{scheme}://{host}{path}"


def get_agent_voice_config(agent: Agent) -> dict:
    """Extract TTS voice config from Agent model."""
    try:
        return json.loads(agent.voice_config_json or "{}")
    except Exception:
        return {}


def get_agent_response_language(agent: Agent) -> str:
    """Get the agent's configured response language."""
    try:
        sys_cfg = json.loads(agent.system_config_json or "{}")
        return sys_cfg.get("response_language", "english").lower()
    except Exception:
        return "english"


def clean_for_voice(text: str) -> str:
    """Strip markdown/special chars so TTS reads naturally."""
    text = re.sub(r"[*_`#>|~\[\]]", "", text)
    text = re.sub(r"\n+", ". ", text)
    text = re.sub(r"\s+", " ", text)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    return text.strip()


def get_whisper_language_hint(response_language: str) -> str:
    """Map agent response language to Whisper language code."""
    mapping = {
        "hindi": "hi",
        "english": "en",
        "hinglish": "hi",   # Map Hinglish to Hindi to force Devanagari script instead of auto-detecting Urdu script
        "auto": "hi",
    }
    return mapping.get(response_language.lower(), "hi")


# ─── STT: Groq Whisper ────────────────────────────────────────────────────────

async def transcribe_with_groq(recording_url: str, language_hint: Optional[str] = None) -> str:
    """Download Vobiz recording (with auth) and transcribe using Groq Whisper."""
    groq_api_key = settings.GROQ_API_KEY
    if not groq_api_key:
        logger.error("GROQ_API_KEY not set")
        return ""

    try:
        auth_headers = {
            "X-Auth-ID": settings.VOBIZ_AUTH_ID,
            "X-Auth-Token": settings.VOBIZ_AUTH_TOKEN,
        }
        # Use global http_client to reuse connection keep-alive
        audio_resp = await http_client.get(recording_url, headers=auth_headers)
        if audio_resp.status_code != 200:
            logger.error(f"Recording download failed: HTTP {audio_resp.status_code}")
            return ""
        audio_bytes = audio_resp.content
        logger.info(f"Downloaded {len(audio_bytes)} bytes")

        files = {"file": ("recording.mp3", audio_bytes, "audio/mpeg")}
        data = {
            "model": "whisper-large-v3",
            "prompt": "The caller is speaking in Hindi or English (Hinglish). Transcribe ONLY in Devanagari Hindi or English characters. DO NOT use Arabic or Urdu script."
        }
        if language_hint:
            data["language"] = language_hint
        
        resp = await http_client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {groq_api_key}"},
            files=files,
            data=data
        )
        if resp.status_code == 200:
            text = resp.json().get("text", "").strip()
            logger.info(f"Transcription: '{text}'")
            print(f"DEBUG TRANSCRIPTION: '{text}'", flush=True)
            return text
        else:
            logger.error(f"Groq Whisper error {resp.status_code}: {resp.text}")
            return ""
    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        return ""


# ─── TTS: ElevenLabs + OpenAI fallback ───────────────────────────────────────

async def generate_tts_audio(text: str, request: Request, voice_cfg: dict = None) -> str:
    """
    Generate TTS audio using agent's configured voice.
    Priority: Agent's ElevenLabs voice → System ElevenLabs → OpenAI nova
    Returns a public URL for Vobiz <Play> tag, or "" to fall back to <Speak>.
    """
    text = clean_for_voice(text)
    if not text:
        return ""

    # Save to /static dir (publicly accessible via ngrok)
    static_dir = os.path.join(os.path.dirname(__file__), "..", "..", "static")
    os.makedirs(static_dir, exist_ok=True)
    filename = f"voice_{uuid.uuid4().hex[:12]}.mp3"
    filepath = os.path.join(static_dir, filename)

    def make_public_url(fn: str) -> str:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
        scheme = "https" if "ngrok" in host else (request.headers.get("x-forwarded-proto") or "http")
        return f"{scheme}://{host}/static/{fn}"

    async def get_accessible_url_async(local_path: str) -> str:
        # ⚡ CLOUDFLARE R2 CDN OPTIMIZATION: Upload in background thread to avoid blocking response
        if settings.R2_PUBLIC_URL and settings.R2_ACCESS_KEY_ID:
            try:
                from app.services.r2_storage import upload_to_r2
                fn = os.path.basename(local_path)
                r2_key = f"telephony/{fn}"
                # Run sync upload in executor to keep event loop free (removes ~600ms block)
                r2_url = await asyncio.to_thread(upload_to_r2, local_path, r2_key, "audio/mpeg")
                if r2_url:
                    logger.info(f"Uploaded voice file to Cloudflare R2: {r2_url}")
                    return r2_url
            except Exception as e:
                logger.error(f"Failed to upload voice to R2: {e}")
        return make_public_url(os.path.basename(local_path))

    # ── 1. Agent's own ElevenLabs voice (from voice_config_json) ──
    if voice_cfg and voice_cfg.get("provider") == "elevenlabs":
        voice_id = voice_cfg.get("voice_name") or voice_cfg.get("voice_id") or "21m00Tcm4TlvDq8ikWAM"
        api_key = voice_cfg.get("api_key") or settings.ELEVENLABS_API_KEY
        if api_key:
            try:
                resp = await http_client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                    json={
                        "text": text,
                        "model_id": "eleven_turbo_v2_5",
                        "voice_settings": {"stability": 0.4, "similarity_boost": 0.8}
                    }
                )
                if resp.status_code == 200:
                    with open(filepath, "wb") as f:
                        f.write(resp.content)
                    url = await get_accessible_url_async(filepath)
                    logger.info(f"Agent ElevenLabs TTS OK: voice={voice_id}")
                    print(f"DEBUG TTS (Agent ElevenLabs voice={voice_id}): {url}", flush=True)
                    return url
                else:
                    logger.warning(f"Agent ElevenLabs TTS failed: {resp.status_code}")
            except Exception as e:
                logger.warning(f"Agent ElevenLabs TTS error: {e}")

    # ── 2. System ElevenLabs key ──
    eleven_key = settings.ELEVENLABS_API_KEY
    if eleven_key:
        default_voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel
        try:
            resp = await http_client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{default_voice_id}",
                headers={"xi-api-key": eleven_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": "eleven_turbo_v2_5",
                    "voice_settings": {"stability": 0.4, "similarity_boost": 0.8}
                }
            )
            if resp.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                url = await get_accessible_url_async(filepath)
                logger.info("System ElevenLabs TTS OK")
                return url
            else:
                logger.warning(f"System ElevenLabs TTS failed: {resp.status_code}")
        except Exception as e:
            logger.warning(f"System ElevenLabs TTS error: {e}")

    # ── 3. OpenAI TTS fallback ──
    openai_key = settings.OPENAI_API_KEY
    if openai_key:
        try:
            resp = await http_client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={"model": "tts-1", "input": text, "voice": "nova", "response_format": "mp3"}
            )
            if resp.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                url = await get_accessible_url_async(filepath)
                logger.info("OpenAI TTS fallback OK")
                return url
        except Exception as e:
            logger.warning(f"OpenAI TTS error: {e}")

    return ""   # All failed — Vobiz <Speak> will be used


def build_record_xml(
    intro_audio_url: str,
    intro_text: str,
    record_url: str,
    max_length: int = 25,
    silence_timeout: float = 0.5,
    use_speak_for_response: bool = False
) -> str:
    """
    Build XML that plays intro then immediately records.
    - If intro_audio_url is set: use <Play> (for pre-generated greetings)
    - If use_speak_for_response: use <Speak> directly (ZERO latency for real-time responses)
    maxSilence auto-stops recording when user stops talking.
    """
    if use_speak_for_response or not intro_audio_url:
        safe = clean_for_voice(intro_text)
        play_block = f"    <Speak>{safe}</Speak>"
    else:
        play_block = f"    <Play>{intro_audio_url}</Play>"

    # Convert silence_timeout to int (e.g. 1) as Plivo/Vobiz usually requires an integer for timeout
    timeout_val = int(silence_timeout) if silence_timeout >= 1 else 1
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
{play_block}
    <Record action="{record_url}" method="POST" maxLength="{max_length}" timeout="{timeout_val}" silenceTimeout="{timeout_val}" playBeep="false"/>
    <Hangup/>
</Response>"""



# ─── RAG Context Retrieval (fast, top-3 chunks) ──────────────────────────────

async def get_rag_context(question: str, agent_id: str, datastore_ids: list) -> str:
    """Fetch relevant knowledge base context for the agent's question (async, non-blocking)."""
    try:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        # Run CPU-bound embedding in executor to keep event loop free
        query_emb = await asyncio.to_thread(embed_texts, [question])
        if query_emb is None or len(query_emb) == 0:
            return ""
        emb_2d = query_emb[0].reshape(1, -1)
        results = get_vector_store().search_combined(
            emb_2d, agent_id=agent_id, datastore_ids=datastore_ids, top_k=3
        )
        texts = []
        for res in results:
            if isinstance(res, (list, tuple)) and len(res) > 0:
                texts.append(res[0].text)
            elif hasattr(res, 'text'):
                texts.append(res.text)
        return "\n---\n".join(texts)
    except Exception as e:
        logger.warning(f"RAG context retrieval failed: {e}")
        return ""


# ─── Fast Voice LLM (Groq — ultra low latency) ───────────────────────────────

async def voice_groq_response(
    question: str,
    agent_system_prompt: str,
    response_language: str = "english",
    caller_phone: str = "",
    agent_id: str = "",
    datastore_ids: list = None
) -> str:
    """
    Uses Groq's llama-3.3-70b for ~5-10x faster responses than OpenAI.
    RAG context is fetched from the agent's knowledge base before LLM call.
    Critical for phone calls where every second of delay feels long.
    """
    groq_key = settings.GROQ_API_KEY
    if not groq_key:
        return "I apologize, I am unable to respond right now. Please try again."

    lang_instruction = {
        "hindi": "ONLY respond in Hindi (Devanagari or Roman Hindi).",
        "english": "ONLY respond in English.",
        "hinglish": "Respond in Hinglish (mix of Hindi and English, whichever feels natural).",
    }.get(response_language.lower(), "Respond in English.")

    # ── Fetch RAG context from knowledge base ──
    rag_context = ""
    if agent_id:
        rag_context = await get_rag_context(question, agent_id, datastore_ids or [])

    context_block = f"""

=== KNOWLEDGE BASE CONTEXT (use this to answer) ===
{rag_context[:1500]}
====================================================""" if rag_context else ""

    voice_system = f"""{agent_system_prompt}{context_block}

=== PHONE CALL MODE — STRICT RULES ===
You are on a LIVE PHONE CALL. CRITICAL rules:
- Maximum 2 sentences, 25 words. Do not exceed this.
- Answer ONLY from the Knowledge Base context above when available.
- No "Great question!", no "Sure!", no filler words.
- NO bullet points, NO markdown, NO asterisks.
- NO asking for phone number, email, or personal details.
- Answer directly and stop immediately.
- {lang_instruction}"""

    try:
        resp = await http_client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": voice_system},
                    {"role": "user", "content": question}
                ],
                "max_tokens": 100,        # 2 short sentences
                "temperature": 0.3,       # Lower temp = more factual, less hallucination
                "stream": False
            }
        )
        if resp.status_code == 200:
            answer = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info(f"Groq voice LLM OK: '{answer[:80]}'")
            return answer
        else:
            logger.error(f"Groq LLM error {resp.status_code}: {resp.text[:200]}")
            return "I'm sorry, I couldn't process your question. Please try again."
    except Exception as e:
        logger.error(f"Groq LLM exception: {e}", exc_info=True)
        return "I apologize for the delay. Please ask your question again."


# ─── Outbound Call Trigger ────────────────────────────────────────────────────

@router.post("/telephony/outbound-call", summary="Trigger an outbound call to a lead")
async def api_trigger_outbound_call(
    request: Request,
    db: Session = Depends(get_db)
):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    to_phone = data.get("to_phone")
    agent_id = data.get("agent_id") or settings.VOBIZ_DEFAULT_AGENT_ID
    server_url = data.get("server_url")

    if not to_phone:
        raise HTTPException(status_code=400, detail="Missing parameter: to_phone")
    if not server_url:
        raise HTTPException(status_code=400, detail="Missing parameter: server_url")

    server_url = server_url.rstrip("/")
    callback_url = f"{server_url}/api/telephony/outbound-flow?agent_id={agent_id}&to={to_phone}"
    result = await trigger_outbound_call(to_phone=to_phone, callback_url=callback_url)
    return result


# ─── Outbound Flow (call answered) ───────────────────────────────────────────

@router.post("/telephony/outbound-flow", summary="XML flow when outbound call is answered")
async def api_outbound_flow(
    request: Request,
    agent_id: Optional[str] = Query(None),
    to: Optional[str] = Query(None)
):
    """Called by Vobiz when the outbound call is answered."""
    agent_str = agent_id or settings.VOBIZ_DEFAULT_AGENT_ID
    record_url = get_absolute_url(request, f"/api/telephony/call/{agent_str}/transcribe")

    # Get agent config for voice and starting message
    voice_cfg = {}
    starting_msg = "Hello! I am your MR AI Voice Assistant. How can I help you today?"
    try:
        agent = db_get_agent(agent_str)
        if agent:
            voice_cfg = get_agent_voice_config(agent)
            if agent.starting_message:
                starting_msg = agent.starting_message
    except Exception:
        pass

    # Generate natural TTS for the greeting
    audio_url = await generate_tts_audio(starting_msg, request, voice_cfg)
    xml_content = build_record_xml(
        intro_audio_url=audio_url,
        intro_text=starting_msg,
        record_url=record_url,
        max_length=25,
        silence_timeout=1
    )
    logger.info(f"Outbound flow sent, record_url={record_url}")
    return Response(content=xml_content, media_type="application/xml")


def db_get_agent(agent_id: str) -> Optional[Agent]:
    """Quick helper to get agent from DB."""
    try:
        from app.core.database import get_session_local
        db = get_session_local()()
        agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
        db.close()
        return agent
    except Exception:
        return None


# ─── Inbound Call Handler ────────────────────────────────────────────────────

@router.post("/telephony/inbound-call", summary="Inbound call webhook")
async def api_inbound_call(
    request: Request,
    agent_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        form_data = await request.form()
        from_phone = form_data.get("From", "").strip() or form_data.get("Caller", "").strip() or ""
    except Exception:
        from_phone = ""

    clean_phone = from_phone.replace("+", "").strip()
    agent_str = agent_id

    if not agent_str and clean_phone:
        try:
            session = db.query(AgentPublicSession).filter(
                AgentPublicSession.phone_number.like(f"%{clean_phone}%")
            ).order_by(AgentPublicSession.updated_at.desc()).first()
            if session:
                agent_str = session.agent_id
        except Exception as e:
            logger.error(f"Session lookup error: {e}")

    if agent_str:
        agent = db.query(Agent).filter(Agent.agent_id == agent_str).first()
        agent_name = agent.name if agent else "MR AI"
        voice_cfg = get_agent_voice_config(agent) if agent else {}
        starting_msg = (agent.starting_message if agent and agent.starting_message
                        else f"Hello! Welcome to {agent_name}. How can I help you?")
        record_url = get_absolute_url(request, f"/api/telephony/call/{agent_str}/transcribe")
        audio_url = await generate_tts_audio(starting_msg, request, voice_cfg)
        xml_content = build_record_xml(audio_url, starting_msg, record_url)
    else:
        verify_url = get_absolute_url(request, "/api/telephony/inbound-call/verify-agent")
        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather action="{verify_url}" method="POST" input="dtmf" timeout="15" numDigits="8">
        <Speak>Welcome to MR AI Voice Portal. Please enter the agent ID using your keypad.</Speak>
    </Gather>
    <Hangup/>
</Response>"""

    return Response(content=xml_content, media_type="application/xml")


# ─── Agent Verify ────────────────────────────────────────────────────────────

@router.post("/telephony/inbound-call/verify-agent", summary="Verify Agent ID from DTMF")
async def api_verify_agent(
    request: Request,
    db: Session = Depends(get_db)
):
    try:
        form_data = await request.form()
        digits = form_data.get("Digits", "").strip()
        from_phone = form_data.get("From", "").strip() or "UnknownCaller"
    except Exception:
        digits = ""
        from_phone = "UnknownCaller"

    clean_phone = from_phone.replace("+", "").strip()
    agent = None

    if digits:
        agent = db.query(Agent).filter(
            (Agent.agent_id.like(f"%{digits}%")) |
            (Agent.custom_slug.like(f"%{digits}%"))
        ).first()

    if agent:
        try:
            session_id = f"tel_{clean_phone}"
            session = db.query(AgentPublicSession).filter(
                AgentPublicSession.session_id == session_id
            ).first()
            if not session:
                session = AgentPublicSession(
                    session_id=f"tel_{clean_phone}",
                    agent_id=agent.agent_id,
                    device_id=clean_phone,
                    phone_number=clean_phone,
                    device_name="Voice Call",
                    user_name=f"Voice Caller {clean_phone[-4:]}"
                )
                db.add(session)
            else:
                session.agent_id = agent.agent_id
                session.updated_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            logger.error(f"Session save error: {e}")

        voice_cfg = get_agent_voice_config(agent)
        intro = f"Connected with {agent.name}. How can I help you?"
        record_url = get_absolute_url(request, f"/api/telephony/call/{agent.agent_id}/transcribe")
        audio_url = await generate_tts_audio(intro, request, voice_cfg)
        xml_content = build_record_xml(audio_url, intro, record_url)
    else:
        verify_url = get_absolute_url(request, "/api/telephony/inbound-call/verify-agent")
        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather action="{verify_url}" method="POST" input="dtmf" timeout="15" numDigits="8">
        <Speak>Invalid agent ID. Please enter the correct agent ID.</Speak>
    </Gather>
    <Hangup/>
</Response>"""

    return Response(content=xml_content, media_type="application/xml")


# ─── CORE: Transcribe + AI + TTS loop ────────────────────────────────────────

@router.post("/telephony/call/{agent_id}/transcribe", summary="STT → RAG → TTS voice loop")
async def api_transcribe_and_respond(
    request: Request,
    agent_id: str,
    silence_count: int = Query(0),
    db: Session = Depends(get_db)
):
    """
    Core voice AI loop:
    1. Receive Vobiz recording
    2. Transcribe with Groq Whisper (auto language)
    3. Query RAG agent
    4. Generate TTS using agent's configured voice
    5. Return <Play> response + next <Record> (no # needed — silence auto-stops)
    """
    form_data = await request.form()
    all_fields = dict(form_data)
    logger.info(f"TRANSCRIBE: agent={agent_id}, fields={list(all_fields.keys())}, silence_count={silence_count}")

    recording_url = (
        form_data.get("RecordUrl") or
        form_data.get("RecordFile") or
        form_data.get("RecordingUrl") or ""
    ).strip()
    recording_duration = int(form_data.get("RecordingDuration", "0") or "0")
    from_phone = form_data.get("From", "").strip() or "UnknownCaller"
    clean_phone = from_phone.replace("+", "").strip()

    # Get agent config
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    voice_cfg = get_agent_voice_config(agent) if agent else {}
    response_lang = get_agent_response_language(agent) if agent else "english"
    sys_cfg = json.loads(agent.system_config_json or "{}") if agent else {}
    agent_system_prompt = sys_cfg.get("system_prompt", "")

    # Helper to handle silence or transcription failures (completely silent retries)
    async def handle_silence() -> Response:
        next_count = silence_count + 1
        if next_count >= 3:
            goodbye_msg = "Thank you for calling. Goodbye."
            # Generate goodbye in agent's actual voice
            audio_url = await generate_tts_audio(goodbye_msg, request, voice_cfg)
            if audio_url:
                xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{audio_url}</Play>
    <Hangup/>
</Response>"""
            else:
                xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>{goodbye_msg}</Speak>
    <Hangup/>
</Response>"""
            logger.info("Silence limit reached. Hanging up call.")
            return Response(content=xml, media_type="application/xml")
        else:
            # Silent retry - no audio prompts at all, just record again to keep it natural
            silent_record_url = get_absolute_url(request, f"/api/telephony/call/{agent_id}/transcribe?silence_count={next_count}")
            xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Record action="{silent_record_url}" method="POST" maxLength="25" timeout="1" silenceTimeout="1" playBeep="false"/>
    <Hangup/>
</Response>"""
            logger.info(f"Silence detected. Retrying silently (count={next_count})")
            return Response(content=xml, media_type="application/xml")

    if not recording_url or recording_duration < 0:
        return await handle_silence()

    # Transcribe (use language hint based on agent's response language)
    lang_hint = get_whisper_language_hint(response_lang)
    speech_text = await transcribe_with_groq(recording_url, lang_hint)

    if not speech_text:
        return await handle_silence()

    logger.info(f"Transcribed: '{speech_text}'")
    print(f"DEBUG SPEECH: '{speech_text}'", flush=True)

    # ── Call Session History & Root Agent Interception ──
    is_root_agent = agent and (agent.is_root or agent.category == "root_assistant")
    session_obj = None
    parser_history = []

    if is_root_agent:
        call_sid = (
            form_data.get("CallUUID") or
            form_data.get("CallSid") or
            form_data.get("call_uuid") or
            form_data.get("call_id") or
            clean_phone
        )
        try:
            session_obj = db.query(AgentPublicSession).filter(
                AgentPublicSession.session_id == call_sid
            ).first()
            if not session_obj:
                session_obj = AgentPublicSession(
                    session_id=call_sid,
                    agent_id=agent_id,
                    device_id="voice_call",
                    phone_number=clean_phone,
                    user_name=f"Caller {clean_phone}",
                    device_name="Voice Call",
                    created_at=datetime.utcnow()
                )
                db.add(session_obj)
                db.commit()
                db.refresh(session_obj)

            # Log user question
            user_msg_db = AgentPublicMessage(
                session_id=session_obj.session_id,
                role="user",
                content=speech_text,
                created_at=datetime.utcnow()
            )
            db.add(user_msg_db)
            db.commit()

            # Retrieve last 6 messages (excluding the current user message we just saved)
            msgs = db.query(AgentPublicMessage).filter(
                AgentPublicMessage.session_id == session_obj.session_id
            ).order_by(AgentPublicMessage.created_at.desc()).limit(6).all()
            msgs.reverse()
            for m in msgs:
                if m.id != user_msg_db.id:
                    parser_history.append({"role": m.role, "content": m.content})
        except Exception as se:
            logger.warning(f"Error handling telephony session history: {se}")
            db.rollback()

    ai_answer = None
    if is_root_agent:
        from app.routes.root_agent import handle_planner_voice_and_chat
        try:
            ai_answer = await handle_planner_voice_and_chat(
                message=speech_text,
                history=parser_history,
                client_id=agent.client_id,
                db=db,
                voice_mode=True
            )
        except Exception as pe:
            logger.warning(f"Planner handler exception in telephony: {pe}")

    if ai_answer is None:
        # Get agent's datastore IDs for RAG lookup
        agent_datastore_ids = []
        if agent:
            try:
                agent_datastore_ids = json.loads(agent.datastores_json or "[]")
            except Exception:
                pass

        # Fallback to default voice_groq_response
        ai_answer = await voice_groq_response(
            question=speech_text,
            agent_system_prompt=agent_system_prompt,
            response_language=response_lang,
            caller_phone=clean_phone,
            agent_id=agent_id,
            datastore_ids=agent_datastore_ids
        )

    # Save assistant response to session history
    if is_root_agent and session_obj:
        try:
            asst_msg_db = AgentPublicMessage(
                session_id=session_obj.session_id,
                role="assistant",
                content=ai_answer,
                created_at=datetime.utcnow()
            )
            db.add(asst_msg_db)
            db.commit()
        except Exception as se:
            logger.warning(f"Error saving assistant response to session history: {se}")
            db.rollback()

    logger.info(f"AI Answer: '{ai_answer[:120]}'")
    print(f"DEBUG AI ANSWER: '{ai_answer[:120]}'", flush=True)

    # Generate TTS audio with agent's configured ElevenLabs voice (with fallback)
    audio_url = await generate_tts_audio(ai_answer, request, voice_cfg)
    
    # Reset silence_count to 0 on a successful response
    success_record_url = get_absolute_url(request, f"/api/telephony/call/{agent_id}/transcribe?silence_count=0")
    xml_content = build_record_xml(
        intro_audio_url=audio_url,
        intro_text=ai_answer,
        record_url=success_record_url,
        max_length=25,
        silence_timeout=1.0,
        use_speak_for_response=False
    )
    return Response(content=xml_content, media_type="application/xml")


# ─── Legacy respond route ─────────────────────────────────────────────────────

@router.post("/telephony/call/{agent_id}/respond", summary="Legacy respond endpoint")
async def api_call_respond(
    request: Request,
    agent_id: str,
    db: Session = Depends(get_db)
):
    """Routes to transcribe flow."""
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    voice_cfg = get_agent_voice_config(agent) if agent else {}
    starting_msg = (agent.starting_message if agent and agent.starting_message
                    else "Hello! How can I help you?")
    record_url = get_absolute_url(request, f"/api/telephony/call/{agent_id}/transcribe")
    audio_url = await generate_tts_audio(starting_msg, request, voice_cfg)
    xml_content = build_record_xml(audio_url, starting_msg, record_url)
    return Response(content=xml_content, media_type="application/xml")
