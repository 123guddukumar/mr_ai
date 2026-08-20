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


def get_whisper_language_hint(response_language: str) -> Optional[str]:
    """Map agent response language to Whisper language code."""
    mapping = {
        "hindi": "hi",
        "english": "en",
        "hinglish": None,   # None forces Whisper to auto-detect the language
        "auto": None,      # None forces Whisper to auto-detect the language
    }
    return mapping.get(response_language.lower(), None)


# ─── STT: Groq Whisper ────────────────────────────────────────────────────────

async def transcribe_with_groq(recording_url: str, language_hint: Optional[str] = None) -> str:
    """Download Vobiz recording (with auth) and transcribe using Groq Whisper."""
    if "mock-speech-text.com" in recording_url:
        from urllib.parse import urlparse, parse_qs
        try:
            parsed = urlparse(recording_url)
            params = parse_qs(parsed.query)
            text = params.get("text", [""])[0]
            logger.info(f"Mock STT transcription: '{text}'")
            return text
        except Exception as e:
            logger.error(f"Mock STT parsing failed: {e}")
            return ""

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

async def generate_tts_audio(text: str, request: Request, voice_cfg: dict = None, bypass_r2: bool = False) -> str:
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
        scheme = request.headers.get("x-forwarded-proto") or "https"
        if "localhost" in host or "127.0.0.1" in host:
            scheme = "http"
        return f"{scheme}://{host}/static/{fn}"

    async def get_accessible_url_async(local_path: str) -> str:
        # ⚡ CLOUDFLARE R2 CDN OPTIMIZATION: Upload in background thread to avoid blocking response
        if not bypass_r2 and settings.R2_PUBLIC_URL and settings.R2_ACCESS_KEY_ID:
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

    # ── 0. System Indian Female (Sarvam AI Anushka) Voice for Welcome Menu ──
    if not voice_cfg and settings.SARVAM_API_KEY:
        try:
            url = "https://api.sarvam.ai/text-to-speech"
            headers = {
                "api-subscription-key": settings.SARVAM_API_KEY,
                "Content-Type": "application/json"
            }
            cleaned_text = clean_for_voice(text)
            data = {
                "inputs": [cleaned_text],
                "target_language_code": "hi-IN",
                "speaker": "anushka",
                "pitch": 0,
                "pace": 1.0,
                "loudness": 1.5,
                "enable_preprocessing": True
            }
            resp = await http_client.post(url, json=data, headers=headers, timeout=20.0)
            if resp.status_code == 200:
                resp_data = resp.json()
                b64 = resp_data.get("audios", [None])[0]
                if b64:
                    import base64
                    with open(filepath, "wb") as f:
                        f.write(base64.b64decode(b64))
                    url = await get_accessible_url_async(filepath)
                    logger.info("System Sarvam AI Indian Female (Anushka) welcome TTS OK")
                    return url
            else:
                logger.warning(f"System Sarvam AI TTS failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.warning(f"System Sarvam AI TTS error: {e}")

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
        default_voice_id = "pNInz6obpgHs517Ve2Rl"  # Adam (Louder and Clearer Male Voice)
        try:
            resp = await http_client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{default_voice_id}",
                headers={"xi-api-key": eleven_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": "eleven_turbo_v2_5",
                    "voice_settings": {"stability": 0.75, "similarity_boost": 0.95, "style": 0.0}
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
    silence_timeout: float = 2.0,
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

    # Convert silence_timeout to int (e.g. 2) as Plivo/Vobiz usually requires an integer for timeout
    timeout_val = int(silence_timeout) if silence_timeout >= 1 else 2
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
{play_block}
    <Record action="{record_url}" method="POST" maxLength="{max_length}" timeout="{timeout_val}" silenceTimeout="{timeout_val}" playBeep="false" finishOnKey="0"/>
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

    # Priority list of Groq models to try (most capable first, then fallbacks)
    groq_models_to_try = [
        os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile"),
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "llama3-70b-8192",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ]
    # Deduplicate while preserving order
    seen = set()
    groq_models_to_try = [m for m in groq_models_to_try if not (m in seen or seen.add(m))]

    try:
        last_error = None
        for model_name in groq_models_to_try:
            try:
                resp = await http_client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {groq_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": voice_system},
                            {"role": "user", "content": question}
                        ],
                        "max_tokens": 120,
                        "temperature": 0.4,
                        "stream": False
                    },
                    timeout=8.0
                )
                if resp.status_code == 200:
                    answer = resp.json()["choices"][0]["message"]["content"].strip()
                    logger.info(f"Groq voice LLM OK (model={model_name}): '{answer[:80]}'")
                    return answer
                elif resp.status_code in (404, 400):
                    # Model not available, try next
                    last_error = f"{resp.status_code}: {resp.text[:120]}"
                    logger.warning(f"Groq model '{model_name}' unavailable ({resp.status_code}), trying next...")
                    continue
                else:
                    last_error = f"{resp.status_code}: {resp.text[:200]}"
                    logger.error(f"Groq LLM error {resp.status_code}: {resp.text[:200]}")
                    break
            except Exception as model_ex:
                last_error = str(model_ex)
                logger.warning(f"Groq model '{model_name}' exception: {model_ex}")
                continue

        logger.error(f"All Groq models failed. Last error: {last_error}")
        return "Maafi chahta hoon, abhi jawab dene mein thodi dikkat aa rahi hai. Kripya dobara poochhen."
    except Exception as e:
        logger.error(f"Groq LLM exception: {e}", exc_info=True)
        return "Kripya ek baar aur apna sawaal poochhen."


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
    if settings.VOBIZ_OUTBOUND_REDIRECT_URL:
        # Redirect the call to Dograh's Vobiz Answer Webhook
        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">{settings.VOBIZ_OUTBOUND_REDIRECT_URL}</Redirect>
</Response>"""
        logger.info(f"Outbound call answered. Redirecting to Dograh Vobiz URL: {settings.VOBIZ_OUTBOUND_REDIRECT_URL}")
        return Response(content=xml_content, media_type="application/xml")

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
        silence_timeout=2
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


def get_ivr_mapped_agents(db: Session) -> dict:
    """Helper to map active non-personal agents to IVR digits 0-9 with name deduplication."""
    agents = db.query(Agent).filter(Agent.is_active == True).all()
    eligible_agents = []
    for a in agents:
        name_lower = (a.name or "").lower()
        cat_lower = (a.category or "").lower()
        if cat_lower == "root_assistant":
            continue
        if "personal" in name_lower or "👑" in name_lower:
            continue
        eligible_agents.append(a)

    # 1. Build initial candidates for 0 (IIP) and 1 (Vijay)
    mapped_agents = {}
    iip_agent = None
    vijay_agent = None
    for a in eligible_agents:
        if "iip" in (a.name or "").lower():
            iip_agent = a
            break
    for a in eligible_agents:
        if "vijay" in (a.name or "").lower():
            vijay_agent = a
            break

    used_ids = set()
    spoken_names = set()

    if iip_agent:
        mapped_agents["0"] = iip_agent
        used_ids.add(iip_agent.agent_id)
        norm_name = iip_agent.name.lower().replace("assistant", "").replace("ai", "").replace(" ", "").strip()
        spoken_names.add(norm_name)

    if vijay_agent:
        mapped_agents["1"] = vijay_agent
        used_ids.add(vijay_agent.agent_id)
        norm_name = vijay_agent.name.lower().replace("assistant", "").replace("ai", "").replace(" ", "").strip()
        spoken_names.add(norm_name)

    # 2. Map remaining eligible agents to digits 2 to 9, skipping duplicate/similar names
    digit_ptr = 2
    for a in eligible_agents:
        if a.agent_id in used_ids:
            continue
        
        name_clean = a.name.replace("/n", "").replace("\n", "").strip()
        norm_name = name_clean.lower().replace("assistant", "").replace("ai", "").replace(" ", "").strip()
        
        # Skip if name is too similar to already spoken names to avoid duplicate announcements
        if norm_name in spoken_names or any(spoken in norm_name or norm_name in spoken for spoken in spoken_names):
            continue
            
        if digit_ptr <= 9:
            mapped_agents[str(digit_ptr)] = a
            used_ids.add(a.agent_id)
            spoken_names.add(norm_name)
            digit_ptr += 1
            
    return mapped_agents


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
        to_phone = form_data.get("To", "").strip() or form_data.get("Called", "").strip() or ""
    except Exception:
        from_phone = ""
        to_phone = ""

    clean_phone = from_phone.replace("+", "").strip()
    agent_str = agent_id

    # If agent_id query parameter is not passed, lookup agent by dialed phone number (To/Called)
    if not agent_str and to_phone:
        clean_to_phone = to_phone.replace("+", "").strip()
        if clean_to_phone:
            # 1. Exact match or match without plus prefix
            agent = db.query(Agent).filter(
                (Agent.phone_number == to_phone) | 
                (Agent.phone_number == clean_to_phone)
            ).first()
            
            # 2. Suffix match (last 10 digits) to handle carrier formatting variations
            if not agent and len(clean_to_phone) >= 10:
                last_10 = clean_to_phone[-10:]
                agent = db.query(Agent).filter(Agent.phone_number.like(f"%{last_10}")).first()
            
            # 3. Fallback search inside customization_json (for unsynced agents)
            if not agent:
                agent = db.query(Agent).filter(
                    Agent.customization_json.like(f'%"call_number"%"%{clean_to_phone}%')
                ).first()
                if not agent and len(clean_to_phone) >= 10:
                    last_10 = clean_to_phone[-10:]
                    agent = db.query(Agent).filter(
                        Agent.customization_json.like(f'%"call_number"%"%{last_10}%')
                    ).first()

            if agent:
                agent_str = agent.agent_id
                logger.info(f"📞 Dynamic routing matched dialed number {to_phone} to Agent: {agent.name} ({agent_str})")

    # If agent_id is passed or resolved from phone mapping, bypass menu!
    if agent_str:
        agent = db.query(Agent).filter(Agent.agent_id == agent_str).first()
        agent_name = agent.name if agent else "MR AI"
        voice_cfg = get_agent_voice_config(agent) if agent else {}
        starting_msg = (agent.starting_message if agent and agent.starting_message
                        else f"Hello! Welcome to {agent_name}. How can I help you?")
        
        # Initialize or update session for this call
        if clean_phone:
            try:
                session_id = f"tel_{clean_phone}"
                session = db.query(AgentPublicSession).filter(
                    AgentPublicSession.session_id == session_id
                ).first()
                if not session:
                    session = AgentPublicSession(
                        session_id=session_id,
                        agent_id=agent_str,
                        device_id=clean_phone,
                        phone_number=clean_phone,
                        device_name="Voice Call",
                        user_name=f"Voice Caller {clean_phone[-4:] if len(clean_phone) >= 4 else clean_phone}"
                    )
                    db.add(session)
                else:
                    session.agent_id = agent_str
                    session.updated_at = datetime.utcnow()
                db.commit()
            except Exception as e:
                logger.error(f"Session save error in inbound direct route: {e}")
                db.rollback()

        record_url = get_absolute_url(request, f"/api/telephony/call/{agent_str}/transcribe")
        audio_url = await generate_tts_audio(starting_msg, request, voice_cfg)
        xml_content = build_record_xml(audio_url, starting_msg, record_url)
    else:
        # Get mapped agents from helper function (ensures exact match with select-agent)
        mapped_agents = get_ivr_mapped_agents(db)

        # Construct welcome menu text based on the mapped agents
        menu_items = []
        if "0" in mapped_agents:
            clean_name = mapped_agents["0"].name.replace("/n", "").replace("\n", "").strip()
            menu_items.append(f"{clean_name} se baat karne ke liye 0 dabaye.")
        if "1" in mapped_agents:
            clean_name = mapped_agents["1"].name.replace("/n", "").replace("\n", "").strip()
            menu_items.append(f"{clean_name} se baat karne ke liye 1 dabaye.")

        for d, a in sorted(mapped_agents.items()):
            if d in ("0", "1"):
                continue
            clean_name = a.name.replace("/n", "").replace("\n", "").strip()
            menu_items.append(f"{clean_name} se baat karne ke liye {d} dabaye.")

        menu_text = "Welcome to my assistant. " + " ".join(menu_items)

        select_url = get_absolute_url(request, "/api/telephony/inbound-call/select-agent")
        audio_url = await generate_tts_audio(menu_text, request, None, bypass_r2=True)
        
        if audio_url:
            xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather action="{select_url}" method="POST" input="dtmf" timeout="10" numDigits="1">
        <Play>{audio_url}</Play>
    </Gather>
    <Hangup/>
</Response>"""
        else:
            xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather action="{select_url}" method="POST" input="dtmf" timeout="10" numDigits="1">
        <Speak>{menu_text}</Speak>
    </Gather>
    <Hangup/>
</Response>"""

    return Response(content=xml_content, media_type="application/xml")


# ─── Agent Selection DTMF Handler ────────────────────────────────────────────

@router.post("/telephony/inbound-call/select-agent", summary="Select agent from IVR digit")
async def api_select_agent(
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

    mapped_agents = get_ivr_mapped_agents(db)

    selected_agent = mapped_agents.get(digits)
    if selected_agent:
        agent_id = selected_agent.agent_id
        # Update session
        try:
            session_id = f"tel_{clean_phone}"
            session = db.query(AgentPublicSession).filter(
                AgentPublicSession.session_id == session_id
            ).first()
            if not session:
                session = AgentPublicSession(
                    session_id=session_id,
                    agent_id=agent_id,
                    device_id=clean_phone,
                    phone_number=clean_phone,
                    device_name="Voice Call",
                    user_name=f"Voice Caller {clean_phone[-4:]}"
                )
                db.add(session)
            else:
                session.agent_id = agent_id
                session.updated_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            logger.error(f"Session save error: {e}")

        if settings.VOBIZ_OUTBOUND_REDIRECT_URL:
            # Redirect the call to Dograh's Vobiz Answer Webhook
            xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">{settings.VOBIZ_OUTBOUND_REDIRECT_URL}</Redirect>
</Response>"""
            logger.info(f"Agent selected: {agent_id}. Redirecting call to Dograh Vobiz URL: {settings.VOBIZ_OUTBOUND_REDIRECT_URL}")
        else:
            voice_cfg = get_agent_voice_config(selected_agent)
            starting_msg = (selected_agent.starting_message if selected_agent.starting_message
                            else f"Hello! Welcome to {selected_agent.name}. How can I help you?")
            record_url = get_absolute_url(request, f"/api/telephony/call/{agent_id}/transcribe")
            audio_url = await generate_tts_audio(starting_msg, request, voice_cfg)
            xml_content = build_record_xml(audio_url, starting_msg, record_url)
    else:
        inbound_url = get_absolute_url(request, "/api/telephony/inbound-call")
        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>Invalid selection. Let's try again.</Speak>
    <Redirect method="POST">{inbound_url}</Redirect>
</Response>"""

    return Response(content=xml_content, media_type="application/xml")


@router.post("/telephony/hangup", summary="Graceful hangup handler")
async def api_telephony_hangup(request: Request):
    logger.info("Telephony call hangup webhook received.")
    return {"status": "hangup_logged"}


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

    # Check if user pressed '0' on their keypad to go back to the menu
    digits = (form_data.get("Digits") or form_data.get("digits") or "").strip()
    if digits == "0":
        inbound_url = get_absolute_url(request, "/api/telephony/inbound-call")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">{inbound_url}</Redirect>
</Response>"""
        logger.info("Digit 0 pressed. Redirecting back to the main menu.")
        return Response(content=xml, media_type="application/xml")

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
    <Record action="{silent_record_url}" method="POST" maxLength="25" timeout="2" silenceTimeout="2" playBeep="false" finishOnKey="0"/>
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

    # Check if speech indicates going back to the main menu
    clean_speech = speech_text.strip().lower().replace(".", "").replace(",", "")
    if clean_speech in ("0", "zero", "menu", "go back", "back to menu", "main menu", "starting menu"):
        inbound_url = get_absolute_url(request, "/api/telephony/inbound-call")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">{inbound_url}</Redirect>
</Response>"""
        logger.info(f"Speech '{speech_text}' requested menu. Redirecting back to the main menu.")
        return Response(content=xml, media_type="application/xml")

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
        silence_timeout=2.0,
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
