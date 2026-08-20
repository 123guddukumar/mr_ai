import json
import logging
import secrets
import os
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.agents import (
    create_datastore, get_datastores, delete_datastore,
    create_agent, update_agent, get_agents, delete_agent
)
from app.core.clients import validate_client_token

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Auth helper ──────────────────────────────────────────────────────────────

def _get_client(x_app_token: Optional[str], db: Session) -> dict:
    if not x_app_token:
        raise HTTPException(401, "Missing X-App-Token header")
    client = validate_client_token(x_app_token)
    if not client:
        raise HTTPException(401, "Invalid or expired token")
    return client

# ── Models ────────────────────────────────────────────────────────────────────

class CreateDataStoreReq(BaseModel):
    name: str

class CreateAgentReq(BaseModel):
    name: str
    description: Optional[str] = ""
    category: Optional[str] = "General"
    personality: Optional[str] = ""
    starting_message: Optional[str] = "Hello! How can I help you today?"
    voice_config: Optional[dict] = {}
    system_config: Optional[dict] = {}
    customization: Optional[dict] = {}
    datastores: Optional[List[str]] = []
    custom_slug: Optional[str] = None
    phone_number: Optional[str] = None

class UpdateAgentReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    personality: Optional[str] = None
    starting_message: Optional[str] = None
    voice_config: Optional[dict] = None
    system_config: Optional[dict] = None
    customization: Optional[dict] = None
    datastores: Optional[List[str]] = None
    is_active: Optional[bool] = None
    custom_slug: Optional[str] = None
    phone_number: Optional[str] = None

class IngestUrlReq(BaseModel):
    url: str

class IngestYouTubeReq(BaseModel):
    url: str

class IngestJsonReq(BaseModel):
    json_data: Optional[dict] = None
    json_text: Optional[str] = None
    title: str = "JSON Data"

class SuggestPromptReq(BaseModel):
    name: str
    description: str
    category: Optional[str] = ""

class AgentFeedbackCreate(BaseModel):
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    feedback_type: str = "feedback"
    rating: Optional[int] = None
    comment: str
    device_id: Optional[str] = None
    session_id: Optional[str] = None

# ── Chunking helper ────────────────────────────────────────────────────────────

def _make_agent_chunks(text: str, source_name: str, owner_id: str, is_ds: bool = True, chunk_size: int = 500):
    from app.models.schemas import ChunkMetadata
    import uuid
    chunks = []
    texts = []
    text = text.strip()
    if not text: return [], []
    
    for i in range(0, len(text), chunk_size):
        piece = text[i : i + chunk_size].strip()
        if len(piece) < 30: continue
        
        meta = ChunkMetadata(
            chunk_id=str(uuid.uuid4()),
            source_file=source_name,
            page_number=1, # Default for web/yt
            chunk_index=i // chunk_size,
            text=piece,
            datastore_id=owner_id if is_ds else None,
            agent_id=None if is_ds else owner_id
        )
        chunks.append(meta)
        texts.append(piece)
    return chunks, texts

# ── DataStore Routes ─────────────────────────────────────────────────────────

@router.post("/datastores", tags=["Agents & DataStores"])
async def api_create_datastore(req: CreateDataStoreReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    ds = create_datastore(client["client_id"], req.name, db)
    return ds.to_dict()

@router.get("/datastores", tags=["Agents & DataStores"])
async def api_list_datastores(x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    dss = get_datastores(client["client_id"], db)
    return [ds.to_dict() for ds in dss]

from app.core.models import DataStore, DataStoreSource, Agent, AgentKnowledgeSource

@router.get("/datastores/{ds_id}", tags=["Agents & DataStores"])
async def api_get_datastore(ds_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    logger.info(f"🔍 Fetching DataStore details for ID: {ds_id}")
    client = _get_client(x_app_token, db)
    ds = db.query(DataStore).filter(DataStore.datastore_id == ds_id, DataStore.client_id == client["client_id"]).first()
    if not ds: 
        logger.warning(f"❌ DataStore not found: {ds_id}")
        raise HTTPException(404, "DataStore not found")
    
    d = ds.to_dict()
    # Include sources
    srcs = db.query(DataStoreSource).filter(DataStoreSource.datastore_id == ds_id).all()
    d["sources"] = [
        {"id": s.id, "source_type": s.source_type, "source_name": s.source_name, "chunk_count": s.chunk_count}
        for s in srcs
    ]
    logger.info(f"✅ Found {len(srcs)} sources for {ds_id}")
    return d

@router.delete("/datastores/{ds_id}", tags=["Agents & DataStores"])
async def api_delete_datastore(ds_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    ok = delete_datastore(ds_id, client["client_id"], db)
    if not ok: raise HTTPException(404, "DataStore not found")
    return {"success": True}

# ── Agent Routes ─────────────────────────────────────────────────────────────

def validate_custom_slug(slug: Optional[str], agent_id: Optional[str], db: Session) -> Optional[str]:
    if not slug:
        return None
    slug = slug.strip().lower()
    if not slug:
        return None
    
    import re
    if not re.match(r"^[a-z0-9-]+$", slug):
        raise HTTPException(400, "Custom URL must contain only lowercase letters, numbers, and hyphens.")
        
    reserved_keywords = {
        "login", "user", "dashboard", "help", "admin", "super-admin", "memory", 
        "memory-chat", "agent-chat", "memory-chat-public", "reels", "api-docs", 
        "aws-deploy-guide", "developer-guide", "api-document", "ugc-api-docs",
        "assets", "static", "uploads", "api", "docs", "redoc", "redirect"
    }
    if slug in reserved_keywords:
        raise HTTPException(400, "This URL path is reserved and cannot be used.")
        
    from app.core.models import Agent
    query = db.query(Agent).filter(Agent.custom_slug == slug)
    if agent_id:
        query = query.filter(Agent.agent_id != agent_id)
    existing = query.first()
    if existing:
        raise HTTPException(400, "This custom URL is already in use by another agent.")
        
    return slug

@router.post("/agents", tags=["Agents & DataStores"])
async def api_create_agent(req: CreateAgentReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    # Extract all fields for creation
    params = req.dict()
    
    # Validate custom slug
    if 'custom_slug' in params and params['custom_slug']:
        params['custom_slug'] = validate_custom_slug(params['custom_slug'], None, db)
        
    # Map complex fields to JSON strings
    if not params.get('phone_number') and 'customization' in params and isinstance(params['customization'], dict):
        call_num = params['customization'].get('call_number')
        if call_num:
            params['phone_number'] = str(call_num).strip()

    if 'voice_config' in params: params['voice_config_json'] = json.dumps(params.pop('voice_config'))
    if 'system_config' in params: params['system_config_json'] = json.dumps(params.pop('system_config'))
    if 'customization' in params: params['customization_json'] = json.dumps(params.pop('customization'))
    if 'datastores' in params: params['datastores_json'] = json.dumps(params.pop('datastores'))
    
    from app.core.models import Agent
    import secrets
    new_id = secrets.token_hex(8)
    agent = Agent(agent_id=new_id, client_id=client["client_id"], **params)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    
    # Auto-provision Dograh voice agent in background (fire-and-forget)
    try:
        import asyncio
        from app.services.dograh_service import provision_dograh_agent
        agent_name = params.get('name', new_id)
        system_prompt = params.get('system_prompt', '') or ''
        # Schedule background provisioning (non-blocking)
        asyncio.create_task(
            asyncio.to_thread(provision_dograh_agent, new_id, agent_name, system_prompt)
        )
        logger.info(f"Dograh voice agent provisioning scheduled for agent {new_id}")
    except Exception as e:
        logger.warning(f"Dograh auto-provision skipped for {new_id}: {e}")
    
    return agent.to_dict()


# ── OpenAI-Compatible Public Routes (no auth needed, for Dograh/3rd party validation) ──

@router.get("/agents/models", tags=["Agents & DataStores"])
async def api_agents_models_public():
    """Public models list - required for Dograh base URL validation."""
    return {
        "object": "list",
        "data": [
            {"id": "default", "object": "model", "created": 1686935002, "owned_by": "custom"},
            {"id": "gpt-4.1", "object": "model", "created": 1686935002, "owned_by": "custom"}
        ]
    }

@router.get("/agents", tags=["Agents & DataStores"])
async def api_list_agents(
    request: Request,
    x_app_token: Optional[str] = Header(None, alias="X-App-Token"),
    db: Session = Depends(get_db)
):
    auth_header = request.headers.get("authorization", "")
    if not x_app_token and auth_header.lower().startswith("bearer "):
        return {"message": "OpenAI-compatible RAG server is running."}
        
    client = _get_client(x_app_token, db)
    agents = get_agents(client["client_id"], db)
    return [a.to_dict() for a in agents]

@router.patch("/agents/{agent_id}", tags=["Agents & DataStores"])
async def api_update_agent(agent_id: str, req: UpdateAgentReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    updates = req.dict(exclude_unset=True)
    
    # Validate custom slug if provided
    if 'custom_slug' in updates:
        updates['custom_slug'] = validate_custom_slug(updates['custom_slug'], agent_id, db)
    
    # Convert dict fields to JSON strings for core logic
    if 'customization' in updates and isinstance(updates['customization'], dict):
        call_num = updates['customization'].get('call_number')
        if call_num:
            updates['phone_number'] = str(call_num).strip()

    if 'voice_config' in updates: updates['voice_config_json'] = json.dumps(updates.pop('voice_config'))
    if 'system_config' in updates: updates['system_config_json'] = json.dumps(updates.pop('system_config'))
    if 'customization' in updates: updates['customization_json'] = json.dumps(updates.pop('customization'))
    if 'datastores' in updates: updates['datastores_json'] = json.dumps(updates.pop('datastores'))
    
    agent = update_agent(agent_id, client["client_id"], db, **updates)
    if not agent: raise HTTPException(404, "Agent not found")
    return agent.to_dict()

@router.get("/agents/{agent_id}", tags=["Agents & DataStores"])
async def api_get_agent(agent_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    logger.info(f"👤 Client {client['client_id']} requesting Agent {agent_id}")
    from app.core.models import Agent, AgentKnowledgeSource
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client["client_id"]).first()
    if not agent: 
        logger.warning(f"❌ Agent {agent_id} not found for Client {client['client_id']}")
        raise HTTPException(404, "Agent not found")
    
    d = agent.to_dict()
    # Include sources
    srcs = db.query(AgentKnowledgeSource).filter(AgentKnowledgeSource.agent_id == agent_id).all()
    serialized_sources = [s.to_dict() for s in srcs]
    d["sources"] = serialized_sources
    d["kb_sources"] = serialized_sources
    return d

@router.delete("/agents/{agent_id}", tags=["Agents & DataStores"])
async def api_delete_agent(agent_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    ok = delete_agent(agent_id, client["client_id"], db)
    if not ok: raise HTTPException(404, "Agent not found")
    return {"success": True}

@router.post("/agents/suggest-prompt", tags=["Agents & DataStores"])
async def api_suggest_prompt(req: SuggestPromptReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    _get_client(x_app_token, db)
    
    import httpx
    try:
        # Using the specific API Key and Model requested by the user
        API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyBt32PpStf7-QfIw56RkR9gEdWWSPvPls8")
        model_name = "gemini-3.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
        
        logger.info(f"Generating detailed prompt suggestion via {model_name}...")
        
        detailed_instruction = (
            f"You are a master AI prompt engineer. Your task is to generate a COMPREHENSIVE, high-quality system prompt for an AI agent.\n\n"
            f"AGENT DETAILS:\n"
            f"Name: {req.name}\n"
            f"Category: {req.category}\n"
            f"Description: {req.description}\n\n"
            f"THE SYSTEM PROMPT MUST INCLUDE:\n"
            f"1. IDENTITY & MISSION: Define who the agent is and their core purpose.\n"
            f"2. TONE & PERSONALITY: Describe the specific voice (e.g., professional, witty, empathetic) and speech patterns.\n"
            f"3. BEHAVIORAL GUIDELINES: List clear 'Dos and Don'ts'. How should they handle uncertainty?\n"
            f"4. EXPERTISE & BOUNDARIES: Detail what topics they are experts in and where they should politely decline to answer.\n"
            f"5. INTERACTION STYLE: How should they structure their responses? (e.g., use of markdown, bullet points, concise vs detailed).\n\n"
            f"Return ONLY the final system prompt text. No introductory or concluding remarks. Start immediately with the prompt content."
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": detailed_instruction
                        }
                    ]
                }
            ]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            
            if response.status_code != 200:
                logger.error(f"Gemini API Error: {response.status_code} - {response.text}")
                if response.status_code == 429:
                    raise HTTPException(429, "AI Quota Limit Full. Please try again later.")
                elif response.status_code == 403:
                    raise HTTPException(403, "API key invalid or permission issue")
                else:
                    raise HTTPException(response.status_code, f"AI service error: {response.text}")

            data = response.json()
            suggestion = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            
        return {"suggestion": suggestion}
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Prompt suggestion failed: {err_msg}")
        raise HTTPException(500, f"Suggestion failed: {err_msg}")

# ── Knowledge Ingestion (DataStore) ───────────────────────────────────────────

@router.post("/datastores/{ds_id}/upload-pdf", tags=["Agents & DataStores"])
async def ds_upload_pdf(ds_id: str, file: UploadFile = File(...), x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    # Validate DS ownership
    from app.core.models import DataStore, DataStoreSource
    ds = db.query(DataStore).filter(DataStore.datastore_id == ds_id, DataStore.client_id == client["client_id"]).first()
    if not ds: raise HTTPException(404, "DataStore not found")

    fname = file.filename or "doc.pdf"
    content = await file.read()
    
    # Logic similar to memory_upload_pdf but using datastore_id
    from app.services.embedder import embed_texts
    from app.services.vector_store import get_vector_store
    from app.models.schemas import ChunkMetadata
    import PyPDF2, io, uuid

    reader = PyPDF2.PdfReader(io.BytesIO(content))
    chunks, texts = [], []
    for page_num, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if not text: continue
        for i in range(0, len(text), 500):
            piece = text[i:i+500].strip()
            if len(piece) < 30: continue
            cm = ChunkMetadata(
                chunk_id=str(uuid.uuid4()), source_file=fname,
                page_number=page_num+1, chunk_index=i//500,
                text=piece, datastore_id=ds_id, # Link to datastore
            )
            chunks.append(cm); texts.append(piece)

    if chunks:
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(DataStoreSource(datastore_id=ds_id, source_type="pdf", source_name=fname, chunk_count=len(chunks)))
        db.commit()

    return {"success": True, "total_chunks": len(chunks)}

# ── URL/YT Ingestion (DataStore) ──────────────────────────────────────────────

@router.post("/datastores/{ds_id}/ingest-url", tags=["Agents & DataStores"])
async def api_ds_ingest_url(ds_id: str, req: IngestUrlReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import DataStore, DataStoreSource
    ds = db.query(DataStore).filter(DataStore.datastore_id == ds_id, DataStore.client_id == client["client_id"]).first()
    if not ds: raise HTTPException(404, "DataStore not found")

    from urllib.parse import urljoin, urlparse
    import httpx
    from bs4 import BeautifulSoup
    
    base_url = req.url
    domain = urlparse(base_url).netloc
    visited = set()
    to_visit = [(base_url, 0)] # (url, depth)
    all_text = ""
    max_pages = 10
    max_depth = 2
    
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as hc:
        while to_visit and len(visited) < max_pages:
            curr_url, depth = to_visit.pop(0)
            if curr_url in visited: continue
            visited.add(curr_url)
            
            try:
                resp = await hc.get(curr_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200: continue
                
                content_type = resp.headers.get("Content-Type", "").lower()
                is_pdf = "application/pdf" in content_type or curr_url.lower().split('?')[0].endswith(".pdf")
                
                if is_pdf:
                    import PyPDF2, io
                    reader = PyPDF2.PdfReader(io.BytesIO(resp.content))
                    page_text = ""
                    for page in reader.pages:
                        page_text += (page.extract_text() or "")
                    page_text = page_text.replace('\x00', '')
                else:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for tag in soup(["script","style","nav","footer","header"]): tag.decompose()
                    page_text = " ".join(soup.get_text(" ", strip=True).split())
                
                all_text += f"\n--- Source: {curr_url} ---\n{page_text}\n"
                
                if not is_pdf and depth < max_depth:
                    for a in soup.find_all("a", href=True):
                        full_url = urljoin(curr_url, a["href"]).split("#")[0]
                        if urlparse(full_url).netloc == domain and full_url not in visited:
                            to_visit.append((full_url, depth + 1))
                            
            except Exception as e:
                logger.error(f"Failed to scrape {curr_url}: {e}")

    if not all_text: raise HTTPException(502, "Could not extract any content from the website")
    
    title = urlparse(base_url).netloc
    chunks, texts = _make_agent_chunks(all_text[:100000], title, ds_id, is_ds=True)
    if chunks:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(DataStoreSource(datastore_id=ds_id, source_type="url", source_name=title, chunk_count=len(chunks), raw_text=all_text))
        db.commit()
    return {"success": True, "total_chunks": len(chunks), "pages_scraped": len(visited)}

@router.post("/datastores/{ds_id}/ingest-yt", tags=["Agents & DataStores"])
async def api_ds_ingest_yt(ds_id: str, req: IngestYouTubeReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import DataStore, DataStoreSource
    ds = db.query(DataStore).filter(DataStore.datastore_id == ds_id, DataStore.client_id == client["client_id"]).first()
    if not ds: raise HTTPException(404, "DataStore not found")

    from app.services.youtube_service import get_youtube_transcript
    try:
        text, title = await get_youtube_transcript(req.url)
    except Exception as e:
        logger.error(f"YouTube ingestion failed: {e}")
        raise HTTPException(502, f"YouTube Ingestion Error: {str(e)}")

    if not text: 
        raise HTTPException(422, "Could not extract transcript from YouTube video. Please ensure the video is public and has audio.")

    chunks, texts = _make_agent_chunks(text, title, ds_id, is_ds=True)
    if chunks:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(DataStoreSource(datastore_id=ds_id, source_type="youtube", source_name=title, chunk_count=len(chunks), raw_text=text))
        db.commit()
    return {"success": True, "total_chunks": len(chunks), "title": title}

@router.post("/datastores/{ds_id}/ingest-json", tags=["Agents & DataStores"])
async def api_ds_ingest_json(ds_id: str, req: IngestJsonReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import DataStore, DataStoreSource
    ds = db.query(DataStore).filter(DataStore.datastore_id == ds_id, DataStore.client_id == client["client_id"]).first()
    if not ds: raise HTTPException(404, "DataStore not found")

    text = req.json_text or json.dumps(req.json_data)
    if not text: raise HTTPException(400, "No data provided")

    chunks, texts = _make_agent_chunks(text, req.title, ds_id, is_ds=True)
    if chunks:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(DataStoreSource(datastore_id=ds_id, source_type="json", source_name=req.title, chunk_count=len(chunks)))
        db.commit()
    return {"success": True, "total_chunks": len(chunks)}

@router.post("/agents/scrape-url", tags=["Agents & DataStores"])
async def api_scrape_url(req: IngestUrlReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    _get_client(x_app_token, db)
    import httpx
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as hc:
            resp = await hc.get(req.url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200: raise HTTPException(resp.status_code, "Failed to load website")
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script","style"]): tag.decompose()
            text = " ".join(soup.get_text(" ", strip=True).split())
            return {"text": text[:10000], "title": urlparse(req.url).netloc}
    except Exception as e:
        raise HTTPException(502, f"Scraping failed: {str(e)}")

class WebsiteUpgradeReq(BaseModel):
    url: Optional[str] = None
    scraped_text: Optional[str] = None
    existing_code: Optional[str] = None
    prompt: Optional[str] = ""

@router.post("/agents/upgrade-website", tags=["Agents & DataStores"])
async def api_upgrade_website(req: WebsiteUpgradeReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.services.llm import get_active_provider, ask_llm
    from app.core.config import settings
    
    provider = get_active_provider()
    
    system_prompt = (
        "You are a world-class Web Designer. Create ultra-modern, premium landing pages using HTML5 and Vanilla CSS. "
        "Use Inter font, beautiful gradients, and clean layouts (Stripe/Apple style). "
        "Return ONLY the full HTML/CSS code. No conversational text."
    )
    
    if req.existing_code:
        user_prompt = f"Current Code:\n{req.existing_code}\n\nUpdate Instructions: {req.prompt}\n\nReturn complete updated HTML."
    else:
        user_prompt = (
            f"Generate a premium landing page for: {req.url or ''}\n"
            f"Content: {req.scraped_text or 'Modern Business'}\n"
            f"Style: {req.prompt or 'Premium, minimal, high-conversion'}"
        )
    
    try:
        # Use Groq for speed if available, else current provider
        html = await ask_llm(provider, user_prompt, max_t=4000)
        
        # Clean markdown
        if "```html" in html: html = html.split("```html")[1].split("```")[0].strip()
        elif "```" in html: html = html.split("```")[1].split("```")[0].strip()
            
        # Inject Chatbot Widget
        chatbot_script = f"""
        <div id="mrai-chat" style="position:fixed; bottom:20px; right:20px; z-index:9999;">
            <div id="mrai-bubble" style="width:60px; height:60px; background:#ff7a00; border-radius:50%; display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:0 8px 20px rgba(0,0,0,0.2);">
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            </div>
            <div id="mrai-window" style="display:none; position:fixed; bottom:90px; right:20px; width:350px; height:450px; background:white; border-radius:15px; box-shadow:0 10px 40px rgba(0,0,0,0.15); flex-direction:column; border:1px solid #eee; overflow:hidden;">
                <div style="background:#ff7a00; padding:15px; color:white; font-weight:bold; display:flex; justify-content:space-between;">
                    <span>AI Assistant</span>
                    <span onclick="document.getElementById('mrai-window').style.display='none'" style="cursor:pointer">&times;</span>
                </div>
                <div id="mrai-messages" style="flex:1; padding:15px; overflow-y:auto; font-size:13px; display:flex; flex-direction:column; gap:10px;">
                    <div style="background:#f4f4f4; padding:8px 12px; border-radius:10px; align-self:flex-start;">Hello! Ask me anything about this website.</div>
                </div>
                <div style="padding:10px; border-top:1px solid #eee; display:flex; gap:5px;">
                    <input type="text" id="mrai-input" placeholder="Type a message..." style="flex:1; border:1px solid #ddd; padding:8px; border-radius:5px; outline:none;">
                    <button onclick="mraiSend()" style="background:#ff7a00; color:white; border:none; padding:8px 15px; border-radius:5px; cursor:pointer;">Send</button>
                </div>
            </div>
        </div>
        <script>
            const bubble = document.getElementById('mrai-bubble');
            const win = document.getElementById('mrai-window');
            bubble.onclick = () => {{ win.style.display = win.style.display === 'none' ? 'flex' : 'none'; }};
            
            async function mraiSend() {{
                const input = document.getElementById('mrai-input');
                const box = document.getElementById('mrai-messages');
                const msg = input.value.trim();
                if(!msg) return;
                
                const uMsg = document.createElement('div');
                uMsg.style = "background:#ff7a00; color:white; padding:8px 12px; border-radius:10px; align-self:flex-end;";
                uMsg.textContent = msg;
                box.appendChild(uMsg);
                input.value = "";
                
                const typing = document.createElement('div');
                typing.style = "background:#eee; padding:8px 12px; border-radius:10px; align-self:flex-start;";
                typing.textContent = "...";
                box.appendChild(typing);
                box.scrollTop = box.scrollHeight;

                try {{
                    const r = await fetch('/api/agents/chat-website', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json', 'X-App-Token': '{x_app_token}' }},
                        body: JSON.stringify({{ message: msg, url: "{req.url or ''}" }})
                    }});
                    const res = await r.json();
                    typing.textContent = res.answer;
                }} catch(e) {{ typing.textContent = "Error connecting to AI."; }}
                box.scrollTop = box.scrollHeight;
            }}
        </script>
        """
        if "</body>" in html: html = html.replace("</body>", f"{chatbot_script}</body>")
        else: html += chatbot_script
        
        return {"html": html}
    except Exception as e:
        raise HTTPException(502, f"AI generation failed: {str(e)}")

class WebsiteChatReq(BaseModel):
    message: str
    url: Optional[str] = ""

@router.post("/agents/chat-website", tags=["Agents & DataStores"])
async def api_chat_website(req: WebsiteChatReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    _get_client(x_app_token, db)
    from app.services.llm import generate_answer
    try:
        context = f"This is a chat about the website: {req.url}. Provide helpful answers based on the context of this website."
        answer = await generate_answer(req.message, context)
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(500, f"Chat failed: {str(e)}")

@router.get("/agents/website-projects", tags=["Agents & DataStores"])
async def api_list_web_projects(x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import WebsiteProject
    projs = db.query(WebsiteProject).filter(WebsiteProject.client_id == client["client_id"]).order_by(WebsiteProject.created_at.desc()).all()
    return [p.to_dict() for p in projs]

@router.post("/agents/website-projects", tags=["Agents & DataStores"])
async def api_save_web_project(req: WebsiteUpgradeReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import WebsiteProject
    import uuid
    
    pid = str(uuid.uuid4())[:8]
    new_proj = WebsiteProject(
        project_id=pid,
        client_id=client["client_id"],
        name=req.url or "Untitled Project",
        url=req.url,
        html_code=req.existing_code,
        scraped_text=req.scraped_text
    )
    db.add(new_proj)
    db.commit()
    return new_proj.to_dict()

@router.delete("/agents/website-projects/{project_id}", tags=["Agents & DataStores"])
async def api_delete_web_project(project_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import WebsiteProject
    proj = db.query(WebsiteProject).filter(WebsiteProject.project_id == project_id, WebsiteProject.client_id == client["client_id"]).first()
    if not proj: raise HTTPException(404, "Project not found")
    db.delete(proj)
    db.commit()
    return {"success": True}

# ── LMS / Training Routes ─────────────────────────────────────────────────────

@router.get("/training/courses", tags=["LMS"])
async def api_list_courses(x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import Course
    courses = db.query(Course).filter(Course.client_id == client["client_id"]).all()
    return [c.to_dict() for c in courses]

@router.get("/training/courses/{course_id}", tags=["LMS"])
async def api_get_course_detail(course_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    _get_client(x_app_token, db)
    from app.core.models import Course, Chapter, Topic, Question
    import json
    course = db.query(Course).filter(Course.course_id == course_id).first()
    if not course: raise HTTPException(404, "Course not found")
    
    data = course.to_dict()
    chaps = []
    for ch in course.chapters:
        c_data = {"id": ch.id, "title": ch.title, "topics": []}
        for t in ch.topics:
            t_data = {"id": t.id, "title": t.title, "content": t.content, "questions": []}
            for q in t.questions:
                t_data["questions"].append({
                    "id": q.id,
                    "question": q.question,
                    "options": json.loads(q.options_json),
                    "correct_idx": q.correct_idx
                })
            c_data["topics"].append(t_data)
        chaps.append(c_data)
    
    test_qs = db.query(Question).filter(Question.course_id == course_id, Question.is_test == True).all()
    data["final_test"] = [{
        "id": q.id,
        "question": q.question,
        "options": json.loads(q.options_json),
        "correct_idx": q.correct_idx
    } for q in test_qs]
    
    data["chapters"] = chaps
    return data

@router.delete("/training/courses/{course_id}", tags=["LMS"])
async def api_delete_course(course_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import Course
    course = db.query(Course).filter(Course.course_id == course_id, Course.client_id == client["client_id"]).first()
    if not course: raise HTTPException(404, "Course not found")
    db.delete(course)
    db.commit()
    return {"success": True, "message": "Course deleted successfully"}

class CourseGenReq(BaseModel):
    topic: str
    datastore_id: Optional[str] = None

@router.post("/training/generate-courses", tags=["LMS"])
async def api_generate_courses(req: CourseGenReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.services.llm import generate_answer, get_active_provider
    from app.core.models import Course, Chapter, Topic, Question
    from app.services.vector_store import get_vector_store
    from app.services.embedder import embed_texts
    import uuid, json, random
    
    # 1. Retrieve Context from DataStore (RAG)
    context_text = ""
    if req.datastore_id:
        try:
            # Ensure query_emb is 2D for FAISS
            query_emb = embed_texts([req.topic])
            if query_emb is not None and len(query_emb) > 0:
                emb_2d = query_emb[0].reshape(1, -1)
                results = get_vector_store().search_combined(emb_2d, agent_id=None, datastore_ids=[req.datastore_id], top_k=10)
                # Safer extraction
                texts = []
                for res in results:
                    if isinstance(res, (list, tuple)) and len(res) > 0:
                        texts.append(res[0].text)
                    elif hasattr(res, 'text'):
                        texts.append(res.text)
                context_text = "\n---\n".join(texts)
        except Exception as e:
            print(f"RAG Retrieval failed: {e}")

    # 2. Generate Course Outlines
    outline_prompt = (
        f"Based on the following knowledge base context, generate 3 distinct course outlines for the topic: {req.topic}.\n"
        f"Context: {context_text[:4000]}\n\n"
        "Each course MUST have 3 chapters. Each chapter MUST have exactly 3 topic titles.\n"
        "Return ONLY a valid JSON object. No conversational text.\n"
        "Format: "
        '{"courses": [{"title": "...", "description": "...", "chapters": [{"title": "...", "topics": ["T1", "T2", "T3"]}]}]}'
    )
    
    from app.core.config import settings
    orig_sp = settings.SYSTEM_PROMPT
    settings.SYSTEM_PROMPT = "You are a professional curriculum designer. Output ONLY valid JSON structure with 3 courses, each having 3 chapters and 3 topics."
    
    try:
        provider = get_active_provider()
        from app.services.llm import _call_openai, _call_gemini, _call_groq
        
        async def ask_llm(p, prompt, max_t=settings.OPENAI_MAX_TOKENS):
            # Groq/OpenAI compatible call with custom tokens
            if p == "gemini": return await _call_gemini(prompt, "")
            if p == "groq": 
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=settings.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
                resp = await client.chat.completions.create(
                    model=settings.GROQ_MODEL,
                    messages=[{"role":"system","content":settings.SYSTEM_PROMPT},{"role":"user","content":prompt}],
                    max_tokens=max_t,
                    temperature=0.2
                )
                return resp.choices[0].message.content
            return await _call_openai(prompt, "")

        # Robust JSON cleaning and parsing
        def extract_json(text):
            if not text: return None
            try:
                # Find the first '{'
                start = text.find("{")
                if start == -1: return None
                
                content = text[start:]
                
                # Use JSONDecoder.raw_decode to find the first valid JSON object
                import json
                decoder = json.JSONDecoder(strict=False)
                try:
                    obj, index = decoder.raw_decode(content)
                    return obj
                except Exception as e:
                    # Fallback: aggressive cleaning if raw_decode fails
                    # Extract everything between FIRST { and LAST }
                    last_brace = content.rfind("}")
                    if last_brace == -1: return None
                    text_slice = content[:last_brace+1]
                    # Remove non-printable chars but keep newlines/tabs
                    sanitized = "".join(ch for ch in text_slice if ord(ch) >= 32 or ch in "\n\r\t")
                    return json.loads(sanitized, strict=False)
            except Exception as e:
                print(f"JSON Extraction failed: {e}")
                return None

        # Enhanced ask_llm with retry for Groq 429s
        async def ask_llm_retry(p, prompt, max_t=settings.OPENAI_MAX_TOKENS, retries=3):
            import asyncio
            for i in range(retries):
                try:
                    return await ask_llm(p, prompt, max_t)
                except Exception as e:
                    if "429" in str(e) and i < retries - 1:
                        print(f"Rate limited (429). Retrying in 3s... (Attempt {i+1}/{retries})")
                        await asyncio.sleep(3)
                        continue
                    raise e

        # 2. Generate Course Outlines
        resp_text = await ask_llm_retry(provider, outline_prompt, max_t=2500)
        data = extract_json(resp_text)
        if not data:
            raise HTTPException(502, "LLM returned malformed structure. Please try again.")
        if not data:
            raise HTTPException(502, "LLM returned malformed JSON structure.")
        
        created_courses = []
        courses_data = data.get("courses", [])
        if not isinstance(courses_data, list):
            courses_data = []

        for c_data in courses_data:
            if not isinstance(c_data, dict): continue
            cid = str(uuid.uuid4())[:8]
            c_title = c_data.get("title", f"Course on {req.topic}")
            c_desc = c_data.get("description", "Comprehensive learning module.")
            
            course = Course(course_id=cid, client_id=client["client_id"], title=c_title, description=c_desc)
            db.add(course)
            db.flush()
            
            chapters_data = c_data.get("chapters", [])
            if not isinstance(chapters_data, list): chapters_data = []
            
            for idx, ch_data in enumerate(chapters_data):
                if not isinstance(ch_data, dict): continue
                ch_title = ch_data.get("title", f"Chapter {idx + 1}")
                
                chapter = Chapter(course_id=cid, title=ch_title, order=idx)
                db.add(chapter)
                db.flush()
                
                topics_data = ch_data.get("topics", [])
                if not isinstance(topics_data, list): topics_data = []
                
                for tidx, t_title in enumerate(topics_data):
                    if not isinstance(t_title, str): t_title = str(t_title)
                    topic = Topic(chapter_id=chapter.id, title=t_title, order=tidx)
                    
                    # Generate REAL Topic Content and 5 MCQs
                    content_prompt = (
                        f"Context: {context_text[:2500]}\n"
                        f"Topic: {t_title}\n"
                        "Act as a professional teacher. Write a VERY DETAILED and COMPREHENSIVE lesson (at least 150-200 words) about this topic. "
                        "Explain key concepts clearly. Also generate 5 MCQ questions.\n"
                        "Return ONLY JSON: {\"content\": \"...\", \"questions\": [{\"q\": \"...\", \"opts\": [\"...\"], \"correct\": 0}]}"
                    )
                    
                    try:
                        content_resp = await ask_llm_retry(provider, content_prompt, max_t=1200)
                        c_json = extract_json(content_resp)
                        if not c_json: raise ValueError("Invalid topic JSON")
                        
                        topic.content = c_json.get("content", f"Detailed lesson on {t_title}.")
                        db.add(topic)
                        db.flush()
                        
                        qs = c_json.get("questions", [])
                        if isinstance(qs, list):
                            for q_data in qs[:5]:
                                if not isinstance(q_data, dict): continue
                                q = Question(
                                    topic_id=topic.id,
                                    question=q_data.get("q", "Question about the topic?"),
                                    options_json=json.dumps(q_data.get("opts", ["A", "B", "C", "D"])),
                                    correct_idx=q_data.get("correct", 0)
                                )
                                db.add(q)
                    except:
                        topic.content = f"Learning module for {t_title} based on your knowledge base."
                        db.add(topic)
                        db.flush()
            
            # 3. Final Test Questions
            test_prompt = f"Generate 10 final exam MCQs for '{c_title}' based on: {context_text[:2000]}\nReturn JSON: {{\"test\": [{{ \"q\": \"...\", \"opts\": [\"...\"], \"correct\": 0 }}]}}"
            try:
                test_resp = await ask_llm(provider, test_prompt, max_t=1500)
                t_json = extract_json(test_resp)
                if not t_json: raise ValueError("Invalid test JSON")
                
                test_qs = t_json.get("test", [])
                if isinstance(test_qs, list):
                    for q_data in test_qs[:10]:
                        q = Question(
                            course_id=cid,
                            question=q_data.get("q", "Final Exam Question?"),
                            options_json=json.dumps(q_data.get("opts", ["Option 1", "Option 2", "Option 3", "Option 4"])),
                            correct_idx=q_data.get("correct", 0),
                            is_test=True
                        )
                        db.add(q)
            except: pass
            
            created_courses.append(course.to_dict())
            
        db.commit()
        return {"courses": created_courses}
        
    except Exception as e:
        db.rollback()
        settings.SYSTEM_PROMPT = orig_sp
        raise HTTPException(502, f"LMS Generation failed: {str(e)}")
    finally:
        settings.SYSTEM_PROMPT = orig_sp

@router.post("/training/submit-test", tags=["LMS"])
async def api_submit_test(req: dict, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import UserCourseProgress, Question
    
    cid = req.get("course_id")
    answers = req.get("answers", {}) 
    
    test_qs = db.query(Question).filter(Question.course_id == cid, Question.is_test == True).all()
    correct_count = 0
    total = len(test_qs)
    
    for q in test_qs:
        if str(q.id) in answers and answers[str(q.id)] == q.correct_idx:
            correct_count += 1
            
    score = int((correct_count / total) * 100) if total > 0 else 0
    passed = score >= 60
    
    progress = UserCourseProgress(
        client_id=client["client_id"],
        course_id=cid,
        score=score,
        passed=passed
    )
    db.add(progress)
    db.commit()
    
    return {"score": score, "passed": passed, "correct": correct_count, "total": total}

# ── Knowledge Ingestion (Agent) ───────────────────────────────────────────────

@router.post("/agents/{agent_id}/upload-pdf", tags=["Agents & DataStores"])
async def api_agent_upload_pdf(agent_id: str, file: UploadFile = File(...), x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import Agent, AgentKnowledgeSource
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client["client_id"]).first()
    if not agent: raise HTTPException(404, "Agent not found")

    fname = file.filename or "doc.pdf"
    content = await file.read()
    
    import PyPDF2, io
    reader = PyPDF2.PdfReader(io.BytesIO(content))
    text = ""
    for page in reader.pages: text += (page.extract_text() or "")
    text = text.replace('\x00', '')
    
    chunks, texts = _make_agent_chunks(text, fname, agent_id, is_ds=False)
    if chunks:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(AgentKnowledgeSource(agent_id=agent_id, source_type="pdf", source_name=fname, chunk_count=len(chunks), raw_text=text))
        db.commit()
    return {"success": True, "total_chunks": len(chunks)}

@router.post("/agents/{agent_id}/ingest-url", tags=["Agents & DataStores"])
async def api_agent_ingest_url(agent_id: str, req: IngestUrlReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import Agent, AgentKnowledgeSource
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client["client_id"]).first()
    if not agent: raise HTTPException(404, "Agent not found")

    try:
        import httpx
        from bs4 import BeautifulSoup
        async with httpx.AsyncClient(timeout=30) as hc:
            resp = await hc.get(req.url, headers={"User-Agent": "Mozilla/5.0"})
        
        content_type = resp.headers.get("Content-Type", "").lower()
        is_pdf = "application/pdf" in content_type or req.url.lower().split('?')[0].endswith(".pdf")
        
        if is_pdf:
            import PyPDF2, io
            reader = PyPDF2.PdfReader(io.BytesIO(resp.content))
            text = ""
            for page in reader.pages:
                text += (page.extract_text() or "")
            text = text.replace('\x00', '')[:30000]
            title = req.url.split('/')[-1] or "PDF Document"
        else:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script","style","nav","footer","header"]): tag.decompose()
            text = " ".join(soup.get_text(" ", strip=True).split())[:30000].replace('\x00', '')
            title = soup.title.string.strip() if soup.title else req.url
    except Exception as e: raise HTTPException(502, f"Scrape failed: {e}")

    chunks, texts = _make_agent_chunks(text, title, agent_id, is_ds=False)
    if chunks:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(AgentKnowledgeSource(agent_id=agent_id, source_type="url", source_name=title, chunk_count=len(chunks), raw_text=text))
        db.commit()
    return {"success": True, "total_chunks": len(chunks)}

@router.post("/agents/{agent_id}/ingest-yt", tags=["Agents & DataStores"])
async def api_agent_ingest_yt(agent_id: str, req: IngestYouTubeReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import Agent, AgentKnowledgeSource
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client["client_id"]).first()
    if not agent: raise HTTPException(404, "Agent not found")

    from app.services.youtube_service import get_youtube_transcript
    try:
        text, title = await get_youtube_transcript(req.url)
        text = text.replace('\x00', '')
    except Exception as e:
        logger.error(f"Agent YT ingestion failed: {e}")
        raise HTTPException(502, f"YouTube Ingestion Error: {str(e)}")

    if not text:
        raise HTTPException(422, "Could not extract transcript from YouTube video.")

    chunks, texts = _make_agent_chunks(text, title, agent_id, is_ds=False)
    if chunks:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(AgentKnowledgeSource(agent_id=agent_id, source_type="youtube", source_name=title, chunk_count=len(chunks), raw_text=text))
        db.commit()
    return {"success": True, "total_chunks": len(chunks), "title": title}
    
@router.delete("/datastores/{ds_id}/sources/{source_id}", tags=["Agents & DataStores"])
async def api_delete_ds_source(ds_id: str, source_id: int, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.agents import delete_datastore_source
    ok = delete_datastore_source(ds_id, source_id, client["client_id"], db)
    if not ok: raise HTTPException(404, "Source not found")
    return {"success": True}

@router.delete("/agents/{agent_id}/sources/{source_id}", tags=["Agents & DataStores"])
async def api_delete_agent_source(agent_id: str, source_id: int, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.agents import delete_agent_source
    ok = delete_agent_source(agent_id, source_id, client["client_id"], db)
    if not ok: raise HTTPException(404, "Source not found")
    return {"success": True}

# ── Agent Chat (RAG) ──────────────────────────────────────────────────────────

class BookMeetingReq(BaseModel):
    name: str
    meeting_time: datetime
    session_id: Optional[str] = None
    device_id: Optional[str] = None

@router.get("/agents/{agent_id}/booked-dates", tags=["Agents & DataStores"])
async def get_booked_dates(agent_id: str, db: Session = Depends(get_db)):
    from app.core.models import Agent, RootMeeting, RootDailyPlan
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    
    meetings = db.query(RootMeeting).filter(
        RootMeeting.client_id == agent.client_id,
        RootMeeting.status == "scheduled",
        RootMeeting.meeting_time >= datetime.utcnow()
    ).all()
    
    booked = [m.meeting_time.isoformat() for m in meetings]

    # Fetch pending plans from RootDailyPlan to block those slots too
    plans = db.query(RootDailyPlan).filter(
        RootDailyPlan.client_id == agent.client_id,
        RootDailyPlan.is_completed == False
    ).all()

    for p in plans:
        try:
            dt = datetime.strptime(f"{p.plan_date} {p.plan_time}", "%Y-%m-%d %H:%M")
            booked.append(dt.isoformat())
        except Exception:
            pass

    return {
        "booked_dates": list(set(booked))
    }

async def analyze_meeting_details(conversation_text: str, agent) -> dict:
    import json, os, logging
    from app.services.llm import llm_with_history
    
    logger = logging.getLogger(__name__)
    
    try: s_cfg = json.loads(agent.system_config_json or "{}")
    except: s_cfg = {}
    
    provider = s_cfg.get('provider', 'gemini')
    model = s_cfg.get('model', 'gemini-3.5-flash')
    api_key = s_cfg.get('api_key', '')
    
    if provider == 'gemini' and not api_key:
        api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

    system_prompt = (
        "You are an AI assistant analyzing a chat history between a user and an agent.\n"
        "Your task is to generate a concise and relevant title and description for a meeting scheduled during this chat.\n"
        "The title should be brief (e.g., 'Project Consultation' or 'Support Discussion').\n"
        "The description should summarize what the user discussed or wants to achieve.\n"
        "You MUST respond ONLY with a raw JSON object matching this exact schema:\n"
        "{\n"
        "  \"title\": \"Generated Title\",\n"
        "  \"description\": \"Generated summary/description\"\n"
        "}\n"
        "Do not explain, do not output any markdown code blocks. Just raw JSON."
    )
    
    prompt = f"Here is the chat history:\n\n{conversation_text}\n\nAnalyze and return JSON."
    
    try:
        raw_result = await llm_with_history(
            question=prompt,
            system=system_prompt,
            history=[],
            provider=provider,
            model=model,
            api_key=api_key,
            ollama_url="http://localhost:11434"
        )
    except Exception as e:
        logger.error(f"Meeting details analysis failed: {e}. Falling back to default Gemini.")
        try:
            fallback_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
            raw_result = await llm_with_history(
                question=prompt,
                system=system_prompt,
                history=[],
                provider="gemini",
                model="gemini-3.5-flash",
                api_key=fallback_key,
                ollama_url="http://localhost:11434"
            )
        except Exception as fe:
            logger.error(f"Fallback meeting analysis failed: {fe}")
            return {"title": "Scheduled Meeting", "description": "Scheduled via Agent Chat"}

    try:
        import re
        match = re.search(r"({.*})", raw_result, re.DOTALL)
        if match:
            data = json.loads(match.group(1).strip())
            if 'title' not in data: data['title'] = "Scheduled Meeting"
            if 'description' not in data: data['description'] = "Scheduled via Agent Chat"
            return data
    except Exception as parse_err:
        logger.error(f"JSON parse error for meeting details: {parse_err}")
        
    return {"title": "Scheduled Meeting", "description": "Scheduled via Agent Chat"}

@router.post("/agents/{agent_id}/book-meeting", tags=["Agents & DataStores"])
async def book_agent_meeting(agent_id: str, req: BookMeetingReq, db: Session = Depends(get_db)):
    from app.core.models import Agent, RootMeeting, RootDailyPlan, AgentPublicMessage
    import secrets
    import logging
    logger = logging.getLogger(__name__)

    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")

    meeting_title = f"Meeting with {req.name}"
    meeting_desc = f"Scheduled via Agent Chat (Session: {req.session_id})"

    if req.session_id:
        chat_msgs = db.query(AgentPublicMessage).filter(
            AgentPublicMessage.session_id == req.session_id
        ).order_by(AgentPublicMessage.created_at.asc()).all()
        
        if chat_msgs:
            conversation_text = ""
            for m in chat_msgs:
                role_lbl = "User" if m.role == "user" else "Assistant"
                conversation_text += f"{role_lbl}: {m.content}\n"
            
            try:
                ai_details = await analyze_meeting_details(conversation_text, agent)
                meeting_title = ai_details.get("title", meeting_title)
                meeting_desc = ai_details.get("description", meeting_desc)
            except Exception as ae:
                logger.error(f"Failed to analyze meeting details via AI: {ae}")

    meeting_obj = RootMeeting(
        meeting_id=secrets.token_hex(8),
        client_id=agent.client_id,
        owner_id=agent.client_id,
        title=meeting_title,
        description=meeting_desc,
        meeting_time=req.meeting_time,
        duration_mins=30,
        status="scheduled",
        reminder_sent=False,
        notification_sent=False,
        created_at=datetime.utcnow()
    )
    db.add(meeting_obj)

    # 1. Create a Daily Plan in RootDailyPlan
    plan_date_str = req.meeting_time.strftime("%Y-%m-%d")
    plan_time_str = req.meeting_time.strftime("%H:%M")
    
    desc_parts = [f"Attendee: {req.name}"]
    if meeting_desc:
        desc_parts.append(f"Description: {meeting_desc}")
    if req.device_id:
        desc_parts.append(f"Device ID: {req.device_id}")
    if req.session_id:
        desc_parts.append(f"Session ID: {req.session_id}")
    desc_str = "\n".join(desc_parts)

    plan = RootDailyPlan(
        plan_id=secrets.token_hex(8),
        client_id=agent.client_id,
        owner_id=agent.client_id,
        title=meeting_title,
        description=desc_str,
        category="meeting",
        plan_date=plan_date_str,
        plan_time=plan_time_str,
        status="upcoming",
        is_completed=False,
        from_meeting=True,
        created_at=datetime.utcnow()
    )
    db.add(plan)

    # 2. Append User and Assistant messages to public session chat log
    if req.session_id:
        user_msg = AgentPublicMessage(
            session_id=req.session_id,
            role="user",
            content=f"Please book a meeting for {req.name} at {req.meeting_time.strftime('%Y-%m-%d %I:%M %p')}."
        )
        db.add(user_msg)
        
        asst_msg = AgentPublicMessage(
            session_id=req.session_id,
            role="assistant",
            content=f"🎉 **Congratulations!** Your meeting schedule slot is booked successfully for **{req.meeting_time.strftime('%Y-%m-%d')}** at **{req.meeting_time.strftime('%I:%M %p')}**."
        )
        db.add(asst_msg)

    db.commit()
    db.refresh(meeting_obj)
    return {"success": True, "meeting_id": meeting_obj.meeting_id}

async def analyze_chat_for_suggestions_and_actions(conversation_text: str, agent) -> dict:
    import json, os, logging
    from app.services.llm import llm_with_history
    
    logger = logging.getLogger(__name__)
    
    try: s_cfg = json.loads(agent.system_config_json or "{}")
    except: s_cfg = {}
    
    provider = s_cfg.get('provider', 'gemini')
    model = s_cfg.get('model', 'gemini-3.5-flash')
    api_key = s_cfg.get('api_key', '')
    
    if provider == 'gemini' and not api_key:
        api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

    system_prompt = (
        "You are an expert conversation analyzer.\n"
        "Analyze the following chat history between a User and an AI Assistant.\n"
        "Determine if the user intends to schedule a meeting/appointment/booking OR if they want to call/speak on the phone.\n"
        "Also generate exactly 2 relevant, natural follow-up questions the user is likely to ask next.\n"
        "You MUST respond ONLY with a raw JSON object matching this exact schema:\n"
        "{\n"
        "  \"meeting_intent\": true/false,\n"
        "  \"call_intent\": true/false,\n"
        "  \"suggested_questions\": [\"Question 1\", \"Question 2\"]\n"
        "}\n"
        "Do not explain, do not output any markdown code blocks. Just raw JSON."
    )
    
    prompt = f"Here is the chat history:\n\n{conversation_text}\n\nAnalyze and return JSON."
    
    try:
        raw_result = await llm_with_history(
            question=prompt,
            system=system_prompt,
            history=[],
            provider=provider,
            model=model,
            api_key=api_key,
            ollama_url="http://localhost:11434"
        )
    except Exception as e:
        logger.error(f"Analysis failed: {e}. Falling back to default Gemini.")
        try:
            fallback_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
            raw_result = await llm_with_history(
                question=prompt,
                system=system_prompt,
                history=[],
                provider="gemini",
                model="gemini-3.5-flash",
                api_key=fallback_key,
                ollama_url="http://localhost:11434"
            )
        except Exception as fe:
            logger.error(f"Fallback analysis failed: {fe}")
            return {"meeting_intent": False, "call_intent": False, "suggested_questions": []}

    try:
        import re
        match = re.search(r"({.*})", raw_result, re.DOTALL)
        if match:
            return json.loads(match.group(1).strip())
        return json.loads(raw_result.strip())
    except Exception as e:
        logger.error(f"Failed to parse analysis JSON: {e}. Raw response: {raw_result}")
        return {"meeting_intent": False, "call_intent": False, "suggested_questions": []}

class AgentAskReq(BaseModel):
    question: str
    history: list = []
    is_voice: Optional[bool] = False

@router.post("/agents/{agent_id}/ask", tags=["Agents & DataStores"])
async def agent_ask(agent_id: str, req: AgentAskReq, db: Session = Depends(get_db)):
    from app.core.models import Agent, DataStore
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.is_active == True).first()
    if not agent: raise HTTPException(404, "Agent not found")

    # 1. Root Agent Delegation if agent is_root or category is root_assistant
    if agent.is_root or agent.category == 'root_assistant':
        from app.routes.root_agent import root_agent_chat, RootChatReq
        root_req = RootChatReq(
            message=req.question,
            session_id=f"root_sess_{agent.client_id}",
            client_id=agent.client_id
        )
        root_res = await root_agent_chat(req=root_req, x_app_token=None, db=db)
        ans_text = root_res.get("content") or root_res.get("answer") or "Sir, main aapke order par kaam kar raha hoon."
        return {
            "answer": ans_text,
            "content": ans_text,
            "sources": [],
            "is_rag": False,
            "session_id": f"root_sess_{agent.client_id}",
            "media": root_res.get("media")
        }

    # Get configured Q&A training pairs
    try:
        custom_cfg = json.loads(agent.customization_json or "{}")
        qa_pairs = custom_cfg.get("qa_pairs", [])
    except:
        qa_pairs = []

    # Quick Q&A Match (Instant Sub-second response)
    def clean_match_string(s: str) -> str:
        import re
        if not s: return ""
        s_clean = re.sub(r'[^\w\s\u0900-\u097F]', '', s).lower().strip()
        return " ".join(s_clean.split())

    q_clean = clean_match_string(req.question)
    matched_pair = None
    for pair in qa_pairs:
        pair_q = clean_match_string(pair.get("q", ""))
        if pair_q and (pair_q == q_clean or pair_q in q_clean or q_clean in pair_q):
            matched_pair = pair
            break

    answer = ""
    sources_data = []
    is_rag = False

    if matched_pair:
        raw_answer = matched_pair.get("a")
        sources_data = [{"source_file": "Training Q&A Pairs", "page_number": 1}]
        is_rag = True
        
        # Check if we need to translate/reformulate based on response_lang
        try: s_cfg = json.loads(agent.system_config_json or "{}")
        except: s_cfg = {}
        response_lang = s_cfg.get("response_language", "default")
        
        import re
        is_user_hindi = bool(re.search(r'[\u0900-\u097F]', req.question))
        is_answer_hindi = bool(re.search(r'[\u0900-\u097F]', raw_answer))
        
        needs_translation = False
        target_lang = None
        
        if response_lang == "hindi":
            needs_translation = not is_answer_hindi
            target_lang = "Hindi (using Devanagari script हिंदी लिपि only)"
        elif response_lang == "english":
            try:
                raw_answer.encode('ascii')
                is_answer_english = True
            except UnicodeEncodeError:
                is_answer_english = False
            needs_translation = not is_answer_english
            target_lang = "English"
        elif response_lang == "hinglish":
            needs_translation = True
            target_lang = "Hinglish (Hindi language written using the English/Latin alphabet, e.g. 'Aap kaise hain?', 'Main help karta hoon')"
        elif response_lang == "default":
            if is_user_hindi and not is_answer_hindi:
                needs_translation = True
                target_lang = "Hindi (using Devanagari script हिंदी लिपि only)"
            elif not is_user_hindi and is_answer_hindi:
                needs_translation = True
                target_lang = "English"
                
        if needs_translation:
            try:
                provider = s_cfg.get('provider', 'gemini')
                model = s_cfg.get('model', 'gemini-3.5-flash')
                api_key = s_cfg.get('api_key', '')
                if provider == 'gemini' and not api_key:
                    import os
                    api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

                from app.services.llm import llm_with_history
                translation_prompt = (
                    f"You are a professional translator. Translate the following text into {target_lang}.\n"
                    f"Maintain the exact meaning, structure, facts, formatting, and markdown links (do NOT change or translate URLs in links, e.g. [text](url) -> keep url same).\n"
                    f"Text to translate:\n{raw_answer}"
                )
                translated_answer = await llm_with_history(
                    question=translation_prompt,
                    system=f"You are a precise translator translating to {target_lang}. Return ONLY the translated text without any conversational preamble, notes, or extra comments.",
                    history=[],
                    provider=provider,
                    model=model,
                    api_key=api_key
                )
                answer = translated_answer.strip()
            except Exception as e:
                logger.error(f"Failed to translate matched Q&A pair with agent's LLM: {e}")
                # FALLBACK: Try translating using system's global Gemini / Google API Key!
                try:
                    import os
                    from app.core.config import settings
                    fallback_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "") or settings.GEMINI_API_KEY
                    if fallback_key:
                        logger.info("Attempting translation fallback using global Gemini key...")
                        translated_answer = await llm_with_history(
                            question=translation_prompt,
                            system=f"You are a precise translator translating to {target_lang}. Return ONLY the translated text without any conversational preamble, notes, or extra comments.",
                            history=[],
                            provider="gemini",
                            model="gemini-3.5-flash",
                            api_key=fallback_key
                        )
                        answer = translated_answer.strip()
                    else:
                        raise ValueError("No global Gemini API key available for fallback")
                except Exception as fallback_err:
                    logger.error(f"Failed translation fallback: {fallback_err}")
                    answer = raw_answer
        else:
            answer = raw_answer
    else:
        # Get linked datastores
        try: ds_ids = json.loads(agent.datastores_json or "[]")
        except: ds_ids = []

        from app.services.embedder import embed_query
        from app.services.vector_store import get_vector_store
        from app.services.llm import build_context_and_sources

        query_emb = embed_query(req.question)
        
        # Search in agent's own knowledge base AND linked datastores
        results = get_vector_store().search_combined(query_emb, agent_id=agent_id, datastore_ids=ds_ids, top_k=5)

        # Filter results by relevance (similarity threshold)
        relevant_results = [res for res in results if res[1] > 0.35] # Score threshold for RAG
        
        context, sources_data = build_context_and_sources(relevant_results)
        is_rag = bool(context)
        
        # Add Q&A pairs to context as high-priority training context
        if qa_pairs:
            qa_context_parts = [f"Q: {p.get('q')}\nA: {p.get('a')}" for p in qa_pairs]
            qa_context = "--- CONFIGURED TRAINING Q&A PAIRS ---\n" + "\n\n".join(qa_context_parts) + "\n--- END OF TRAINING Q&A PAIRS ---\n\n"
            context = qa_context + (context or "")

        # System Instruction
        try: s_cfg = json.loads(agent.system_config_json or "{}")
        except: s_cfg = {}
        
        # Greeting logic: Improved detection
        q_low = req.question.lower().strip()
        greetings = ["hi", "hello", "hey", "hii", "hiihii", "namaste", "how are you", "who are you", "good morning", "good evening"]
        is_greeting = any(g in q_low for g in greetings)
        
        response_lang = s_cfg.get("response_language", "default")
        if response_lang == "hindi":
            lang_rule = "LANGUAGE RULE: You MUST always respond in Hindi (हिंदी) only, using the Devanagari script (हिंदी लिपि). Regardless of what language the user writes in (even if they ask in English), you MUST reply in Hindi using Devanagari script. Do NOT use Romanized Hinglish (Latin alphabet) for your responses."
            context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate it and respond in Hindi (Devanagari script) only."
            greeting_rule = "GREETING RULE: Always reply to greetings in Hindi (Devanagari script) only."
        elif response_lang == "english":
            lang_rule = "LANGUAGE RULE: You MUST always respond in English only. Regardless of what language the user writes in (even if they ask in Hindi), you MUST reply in English."
            context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate it and respond in English only."
            greeting_rule = "GREETING RULE: Always reply to greetings in English only."
        elif response_lang == "hinglish":
            lang_rule = "LANGUAGE RULE: You MUST always respond in Hinglish only (Hindi language written using the English/Latin alphabet, e.g. 'Aap kaise hain?', 'Main aapki kya madad kar sakta hoon?'). Regardless of what language the user writes in, you MUST reply in Hinglish. Do NOT use Hindi Devanagari script."
            context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate/transliterate it and respond in Hinglish only."
            greeting_rule = "GREETING RULE: Always reply to greetings in Hinglish only."
        else:
            lang_rule = "LANGUAGE RULE: Respond ONLY in the same language the user uses. If asked in English, reply in English. If asked in Hindi, reply in Hindi using Devanagari script (हिंदी लिपि) only. Do NOT use Romanized Hinglish (Latin alphabet) for Hindi responses. Do not translate unless asked."
            context_lang_rule = "CONTEXT LANGUAGE RULE: The context files might be in a different language (e.g. Hindi) than the user's question (e.g. English). You MUST always translate the context information and respond in the same language as the user's question. If the user asks in English, you MUST answer in English, even if the knowledge base context is in Hindi."
            greeting_rule = "GREETING RULE: Reply to greetings (Hi, Hello, Namaste) in the SAME language the user used."

        identity = (
            f"You are {agent.name}. {agent.personality}\n"
            f"{lang_rule}\n"
            f"{context_lang_rule}\n"
            f"{greeting_rule}\n"
            f"CORE INSTRUCTIONS: {s_cfg.get('system_prompt', '')}\n"
            f"RESPONSE STYLE: Be natural, conversational and helpful. Stop being robotic. Share knowledge from context naturally if found.\n"
        )

        if context:
            system = (
                f"{identity}\n\n"
                f"--- KNOWLEDGE BASE CONTEXT ---\n"
                f"{context}\n"
                f"--- END OF CONTEXT ---\n\n"
                f"CRITICAL INSTRUCTIONS:\n"
                f"1. Prioritize answering based on the provided context if it contains the answer.\n"
                f"2. IMPORTANT: If the context does not contain the answer, or if the user is asking a general question unrelated to the context, you MUST use your general AI knowledge to provide a helpful, correct, and complete answer. Do NOT say 'information not found in documents' if you can answer it using your general knowledge."
            )
        else:
            system = (
                f"{identity}\n\n"
                f"NOTE: No specific information was found in the internal knowledge base for this query.\n"
                f"INSTRUCTION: Since no direct context is available, please use your general AI knowledge to provide a helpful and accurate answer to the user's question."
            )

        # Final Override
        system += "\n\nFINAL DIRECTIVE: Always be helpful. If context is provided, use it. If not, use your general knowledge. Stop being robotic."

        # For voice queries, override instructions to ensure extreme brevity (under 1-2 sentences) and force fast low-latency models
        if req.is_voice:
            system += "\n\nCRITICAL VOICE DIRECTIVE: Keep your answer extremely short, conversational, and limited to 1-2 sentences maximum. Do NOT use any bullet points, lists, asterisks, or markdown formatting. Speak naturally."
            orig_provider = s_cfg.get('provider', 'gemini')
            if orig_provider == "openai":
                voice_provider = "openai"
                voice_model = "gpt-4o-mini"
                voice_api_key = s_cfg.get('api_key', '')
            elif orig_provider == "groq":
                voice_provider = "groq"
                voice_model = "llama-3.1-8b-instant"
                voice_api_key = s_cfg.get('api_key', '')
            else:
                voice_provider = "gemini"
                voice_model = "gemini-3.5-flash"
                import os
                voice_api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
        else:
            voice_provider = s_cfg.get('provider', 'gemini')
            voice_model = s_cfg.get('model', 'gemini-3.5-flash')
            voice_api_key = s_cfg.get('api_key', '')

        # Call LLM
        from app.services.llm import llm_with_history
        try:
            if response_lang == "hindi":
                system += "\n\nCRITICAL LANGUAGE DIRECTIVE: Regardless of user question or context, you MUST respond in Hindi (Devanagari script हिंदी लिपि only). Do NOT use English or Hinglish."
            elif response_lang == "english":
                system += "\n\nCRITICAL LANGUAGE DIRECTIVE: Regardless of user question or context, you MUST respond in English only."
            elif response_lang == "hinglish":
                system += "\n\nCRITICAL LANGUAGE DIRECTIVE: Regardless of user question or context, you MUST respond in Hinglish only (Hindi written using the English/Latin alphabet, e.g. 'Aap kaise hain?', 'Main help karta hoon'). Do NOT use Devanagari script."

            answer = await llm_with_history(
                question=req.question, system=system, history=req.history[-6:],
                provider=voice_provider,
                model=voice_model,
                api_key=voice_api_key,
                ollama_url="http://localhost:11434",
            )
        except Exception as e:
            orig_provider = s_cfg.get('provider', 'gemini')
            if voice_provider == 'gemini' and orig_provider != 'gemini':
                logger.warning(f"Voice Gemini failed: {e}. Falling back to agent's configured provider {orig_provider}...")
                try:
                    answer = await llm_with_history(
                        question=req.question, system=system, history=req.history[-6:],
                        provider=orig_provider,
                        model=s_cfg.get('model', 'gemini-3.5-flash'),
                        api_key=s_cfg.get('api_key', ''),
                        ollama_url="http://localhost:11434",
                    )
                except Exception as fallback_err:
                    raise HTTPException(502, f"LLM error (Primary and Fallback failed): {fallback_err}")
            elif voice_provider != 'gemini':
                logger.warning(f"Primary LLM provider {voice_provider} failed: {e}. Falling back to Gemini...")
                try:
                    import os
                    fallback_api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
                    answer = await llm_with_history(
                        question=req.question, system=system, history=req.history[-6:],
                        provider='gemini',
                        model='gemini-3.5-flash',
                        api_key=fallback_api_key,
                        ollama_url="http://localhost:11434",
                    )
                except Exception as fallback_err:
                    raise HTTPException(502, f"LLM error (Primary and Fallback failed): {fallback_err}")
            else:
                raise HTTPException(502, f"LLM error: {e}")

    # --- Recommendations / Dynamic Actions Analysis ---
    suggested_questions = []
    action_button = None

    user_msgs_count = len([m for m in req.history if m.get("role") == "user"]) + 1
    if user_msgs_count >= 2 and not req.is_voice:
        chat_lines = []
        for m in req.history:
            role = "User" if m.get("role") == "user" else "Assistant"
            chat_lines.append(f"{role}: {m.get('content', '')}")
        chat_lines.append(f"User: {req.question}")
        chat_lines.append(f"Assistant: {answer}")
        history_text = "\n".join(chat_lines)
        
        analysis = await analyze_chat_for_suggestions_and_actions(history_text, agent)
        suggested_questions = analysis.get("suggested_questions", [])
        
        try: c_cfg = json.loads(agent.customization_json or "{}")
        except: c_cfg = {}
        whatsapp_number = c_cfg.get("whatsapp_number", "")
        call_number = c_cfg.get("call_number", "")
        if not whatsapp_number or not call_number:
            from app.core.models import Client
            client_obj = db.query(Client).filter(Client.client_id == agent.client_id).first()
            if client_obj:
                if not whatsapp_number: whatsapp_number = client_obj.mobile_number or ""
                if not call_number: call_number = client_obj.mobile_number or ""
                
        if analysis.get("meeting_intent") and whatsapp_number:
            action_button = {
                "action_type": "whatsapp",
                "phone_number": whatsapp_number,
                "message": "Schedule a meeting with us",
                "created_at": datetime.utcnow().isoformat()
            }
        elif analysis.get("call_intent") and call_number:
            action_button = {
                "action_type": "call",
                "phone_number": call_number,
                "message": "Give us a call",
                "created_at": datetime.utcnow().isoformat()
            }

    if not suggested_questions:
        try:
            qa_pairs = json.loads(agent.customization_json or "{}").get("qa_pairs", [])
            suggested_questions = [p.get("q") for p in qa_pairs if p.get("q")][:5]
        except:
            suggested_questions = []

    return {
        "answer": answer,
        "sources": [s.__dict__ if hasattr(s, '__dict__') else dict(s) for s in sources_data],
        "is_rag": is_rag,
        "suggested_questions": suggested_questions,
        "action_button": action_button
    }


class TestVoiceReq(BaseModel):
    provider: str
    voice_id: str
    api_key: str
    text: str

@router.post("/agents/test-voice", tags=["Agents & DataStores"])
async def api_test_voice(req: TestVoiceReq):
    import httpx
    from app.core.config import settings
    if req.provider == "elevenlabs":
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{req.voice_id}"
        api_key_val = req.api_key.strip() if req.api_key else os.getenv("ELEVENLABS_API_KEY", "") or settings.ELEVENLABS_API_KEY
        headers = {"xi-api-key": api_key_val, "Content-Type": "application/json"}
        payload = {"text": req.text, "model_id": "eleven_multilingual_v2"}
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if not r.is_success: raise HTTPException(r.status_code, f"ElevenLabs error: {r.text}")
            return Response(content=r.content, media_type="audio/mpeg")
    elif req.provider == "sarvam":
        url = "https://api.sarvam.ai/text-to-speech"
        api_key_val = req.api_key.strip() if req.api_key else os.getenv("SARVAM_API_KEY", "") or settings.SARVAM_API_KEY
        headers = {"api-subscription-key": api_key_val, "Content-Type": "application/json"}
        spk = req.voice_id
        if spk == "hi-IN-Neural-A": spk = "shubh"
        elif spk == "hi-IN-Neural-B": spk = "ritu"
        
        payload = {
            "inputs": [req.text],
            "target_language_code": "hi-IN",
            "speaker": spk,
            "model": "bulbul:v3"
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if not r.is_success: raise HTTPException(r.status_code, f"Sarvam error: {r.text}")
            import base64
            audio_base64 = r.json()["audios"][0]
            return Response(content=base64.b64decode(audio_base64), media_type="audio/wav")
    elif req.provider == "smallestai":
        url = "https://api.smallest.ai/waves/v1/tts"
        api_key_val = req.api_key.strip() if req.api_key else os.getenv("SMALLEST_API_KEY", "") or settings.SMALLEST_API_KEY
        headers = {
            "Authorization": f"Bearer {api_key_val}",
            "Content-Type": "application/json"
        }
        payload = {
            "text": req.text,
            "voice_id": req.voice_id or "meher",
            "model": "lightning_v3.1_pro",
            "sample_rate": 24000,
            "output_format": "wav"
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if not r.is_success: raise HTTPException(r.status_code, f"Smallest AI error: {r.text}")
            return Response(content=r.content, media_type="audio/wav")
    else:
        raise HTTPException(400, "Unsupported provider for server-side test")



_agents_tts_http_client = None

def get_agents_tts_http_client():
    global _agents_tts_http_client
    if _agents_tts_http_client is None:
        import httpx
        _agents_tts_http_client = httpx.AsyncClient(timeout=30.0)
    return _agents_tts_http_client


@router.get("/agents/{agent_id}/speak", tags=["Agents & DataStores"])
async def api_agent_speak(agent_id: str, text: str, db: Session = Depends(get_db)):
    from app.core.models import Agent
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
        
    try:
        cfg = json.loads(agent.voice_config_json or "{}")
    except:
        cfg = {}
        
    provider = cfg.get("provider", "mrai")
    voice_id = cfg.get("voice_name", "")
    api_key = cfg.get("api_key", "")
    
    import httpx
    import os
    import re
    from fastapi.responses import StreamingResponse, Response
    
    # Clean markdown and formatting from the text
    cleaned_text = re.sub(r'#{1,6}\s+', '', text)
    cleaned_text = re.sub(r'[*_`~]', '', cleaned_text)
    cleaned_text = re.sub(r'\n+', ' ', cleaned_text)
    cleaned_text = cleaned_text.strip()
    
    if not cleaned_text:
        raise HTTPException(400, "Cleaned text is empty")
        
    from app.core.config import settings
    if provider == "elevenlabs":
        # Stream audio via ElevenLabs Flash model (optimized for ultra low latency ~75ms)
        tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_22050_32&optimize_streaming_latency=4"
        api_key_val = api_key or os.getenv("ELEVENLABS_API_KEY", "") or settings.ELEVENLABS_API_KEY
        if not api_key_val:
            raise HTTPException(400, "ElevenLabs API key is missing. Set ELEVENLABS_API_KEY or configure voice settings.")
        headers = {
            "xi-api-key": api_key_val,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        }
        payload = {
            "text": cleaned_text,
            "model_id": "eleven_flash_v2_5",  # Premium ultra-low latency conversational model
            "voice_settings": {
                "stability": 0.45,         # Natural human rhythm and intonation (prevents robotic voice)
                "similarity_boost": 0.85,  # Reduces digital artifacts and noise (much clearer English/Hindi)
                "style": 0.0,              # Pure/clean output
                "use_speaker_boost": True  # Active speaker boost
            }
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(tts_url, json=payload, headers=headers)
                if r.status_code != 200:
                    raise HTTPException(r.status_code, f"ElevenLabs TTS error: {r.text}")
                return Response(content=r.content, media_type="audio/mpeg")
        except HTTPException:
            raise
        except Exception as err:
            logger.error(f"ElevenLabs streaming failed: {err}")
            raise HTTPException(500, f"ElevenLabs connection failed: {err}")
            
    elif provider == "sarvam":
        # Stream audio via Sarvam AI REST streaming endpoint
        url = "https://api.sarvam.ai/text-to-speech/stream"
        api_key_val = api_key or os.getenv("SARVAM_API_KEY", "") or settings.SARVAM_API_KEY
        if not api_key_val:
            raise HTTPException(400, "Sarvam API key is missing. Set SARVAM_API_KEY or configure voice settings.")
        headers = {"api-subscription-key": api_key_val, "Content-Type": "application/json"}
        spk = voice_id
        if spk == "hi-IN-Neural-A": spk = "shubh"
        elif spk == "hi-IN-Neural-B": spk = "ritu"
        
        # Check if the text contains Devanagari characters (Hindi)
        is_hindi = bool(re.search(r'[\u0900-\u097F]', cleaned_text))
        lang_code = "hi-IN" if is_hindi else "en-IN"
        
        payload = {
            "text": cleaned_text,
            "target_language_code": lang_code,
            "speaker": spk,
            "model": "bulbul:v3"
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code != 200:
                    raise HTTPException(r.status_code, f"Sarvam TTS error: {r.text}")
                return Response(content=r.content, media_type="audio/wav")
        except HTTPException:
            raise
        except Exception as err:
            logger.error(f"Sarvam streaming failed: {err}")
            raise HTTPException(500, f"Sarvam connection failed: {err}")
            
    elif provider == "smallestai":
        url = "https://api.smallest.ai/waves/v1/tts"
        api_key_val = api_key or os.getenv("SMALLEST_API_KEY", "") or settings.SMALLEST_API_KEY
        if not api_key_val:
            raise HTTPException(400, "Smallest AI API key is missing. Set SMALLEST_API_KEY or configure voice settings.")
        headers = {
            "Authorization": f"Bearer {api_key_val.strip()}",
            "Content-Type": "application/json"
        }
        payload = {
            "text": cleaned_text,
            "voice_id": voice_id or "meher",
            "model": "lightning_v3.1_pro",
            "sample_rate": 24000,
            "output_format": "wav"
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code != 200:
                    raise HTTPException(r.status_code, f"Smallest AI TTS error: {r.text}")
                return Response(content=r.content, media_type="audio/wav")
        except HTTPException:
            raise
        except Exception as err:
            logger.error(f"Smallest AI streaming failed: {err}")
            raise HTTPException(500, f"Smallest AI connection failed: {err}")
            
    else:
        raise HTTPException(400, "TTS handled locally by browser for mrai provider")


def auto_fill_visitor_details_from_device(db: Session, session, agent_id: str, device_id: str):
    if not session or not device_id:
        return
    updated = False
    from app.core.models import AgentPublicSession
    if not session.user_name:
        existing = db.query(AgentPublicSession).filter(
            AgentPublicSession.agent_id == agent_id,
            AgentPublicSession.device_id == device_id,
            AgentPublicSession.user_name != None,
            AgentPublicSession.user_name != ''
        ).order_by(AgentPublicSession.updated_at.desc()).first()
        if existing:
            session.user_name = existing.user_name
            updated = True
            
    if not session.phone_number:
        existing = db.query(AgentPublicSession).filter(
            AgentPublicSession.agent_id == agent_id,
            AgentPublicSession.device_id == device_id,
            AgentPublicSession.phone_number != None,
            AgentPublicSession.phone_number != ''
        ).order_by(AgentPublicSession.updated_at.desc()).first()
        if existing:
            session.phone_number = existing.phone_number
            updated = True
            
    if updated:
        db.commit()


class AgentPublicAskReq(BaseModel):
    question: str
    session_id: str
    device_id: str
    device_name: Optional[str] = "Unknown Device"
    is_voice: Optional[bool] = False
    file_context: Optional[str] = None  # Extracted text/description from uploaded file
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    history_override: Optional[list] = None


@router.get("/agents/{agent_id}/public-info", tags=["Agents & DataStores"])
async def api_get_agent_public_info(agent_id: str, db: Session = Depends(get_db)):
    from app.core.models import Agent
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(404, "Agent not found")

    try: c_cfg = json.loads(agent.customization_json or "{}")
    except: c_cfg = {}
    try: v_cfg = json.loads(agent.voice_config_json or "{}")
    except: v_cfg = {}

    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "description": agent.description or "",
        "starting_message": agent.starting_message or "Hello! How can I help you today?",
        "customization": c_cfg,
        "voice_config": v_cfg,
        "is_root": agent.is_root or False,
        "category": agent.category or ""
    }


@router.post("/agents/{agent_id}/public-ask", tags=["Agents & DataStores"])
async def api_agent_public_ask(agent_id: str, req: AgentPublicAskReq, db: Session = Depends(get_db)):
    import re
    from app.core.models import Agent, AgentPublicSession, AgentPublicMessage
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(404, "Agent not found")

    # 1. Root Agent Delegation if agent is_root or category is root_assistant
    if agent.is_root or agent.category == 'root_assistant':
        from app.routes.root_agent import root_agent_chat, RootChatReq
        root_req = RootChatReq(
            message=req.question,
            session_id=req.session_id,
            client_id=agent.client_id
        )
        root_res = await root_agent_chat(req=root_req, x_app_token=None, db=db)
        ans_text = root_res.get("content") or root_res.get("answer") or "Sir, main aapke order par kaam kar raha hoon."
        return {
            "answer": ans_text,
            "content": ans_text,
            "sources": [],
            "is_rag": False,
            "session_id": req.session_id,
            "media": root_res.get("media")
        }

    # 2. Meeting / Note Save Intent Interception for any sub-agent
    q_lower = req.question.lower()
    is_meeting_word = any(w in q_lower for w in ["meeting", "metting", "appointment"])
    is_save_action = any(w in q_lower for w in ["save", "schedule", "set", "kar do", "kr do", "store", "yise save"])
    if is_meeting_word and is_save_action:
        from app.routes.root_agent import _parse_meeting_details
        from app.core.models import RootMeeting
        from datetime import timedelta

        title, meeting_dt = _parse_meeting_details(req.question)
        meeting_obj = RootMeeting(
            meeting_id=secrets.token_hex(8),
            client_id=agent.client_id,
            owner_id=agent.client_id,
            title=title,
            description=req.question,
            meeting_time=meeting_dt,
            duration_mins=30,
            status="scheduled",
            reminder_sent=False,
            notification_sent=False,
            created_at=datetime.utcnow()
        )
        db.add(meeting_obj)
        db.commit()

        time_formatted = meeting_dt.strftime("%d %b %Y, %I:%M %p")
        reminder_time = (meeting_dt - timedelta(minutes=30)).strftime("%I:%M %p")
        resp = (
            f"🗓️ **Sir, aapka Meeting Root Assistant Database me successful Save & Schedule ho gaya hai!**\n\n"
            f"📌 **Title**: {title}\n"
            f"⏰ **Timing**: {time_formatted}\n"
            f"📍 **Status**: Scheduled\n\n"
            f"🔔 **Notification Alert**: Main meeting start hone se 30 minute pehle (`{reminder_time}`) aapko advance reminder notification bhej doonga!"
        )
        return {
            "answer": resp,
            "content": resp,
            "sources": [],
            "is_rag": False,
            "session_id": req.session_id
        }

    # Get configured Q&A training pairs
    try:
        custom_cfg = json.loads(agent.customization_json or "{}")
        qa_pairs = custom_cfg.get("qa_pairs", [])
    except:
        qa_pairs = []

    # Quick Q&A Match helper
    def clean_match_string(s: str) -> str:
        import re
        if not s: return ""
        s_clean = re.sub(r'[^\w\s\u0900-\u097F]', '', s).lower().strip()
        return " ".join(s_clean.split())

    q_clean = clean_match_string(req.question)

    # 1. Commit user message to DB
    session = None
    if req.session_id:
        session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == req.session_id).first()

    if not session:
        import uuid
        session_id_to_use = req.session_id or f"sess_{uuid.uuid4().hex}"
        session = AgentPublicSession(
            session_id=session_id_to_use,
            agent_id=agent_id,
            device_id=req.device_id,
            device_name=req.device_name
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        req.session_id = session_id_to_use
    else:
        if req.device_id and session.device_id != req.device_id:
            session.device_id = req.device_id
        session.updated_at = datetime.utcnow()
        db.commit()

    # Automatically fill details from same device
    auto_fill_visitor_details_from_device(db, session, agent_id, req.device_id)

    user_msg = AgentPublicMessage(
        session_id=req.session_id,
        role="user",
        content=req.question,
        file_url=req.file_url,
        file_name=req.file_name,
        file_type=req.file_type
    )
    db.add(user_msg)
    db.commit()

    user_msg_count = db.query(AgentPublicMessage).filter(
        AgentPublicMessage.session_id == req.session_id,
        AgentPublicMessage.role == "user"
    ).count()

    is_lead_captured = False
    answer = ""
    sources_data = []
    is_rag = False

    def extract_clean_name(text: str) -> Optional[str]:
        if not text: return None
        t = text.strip()
        q_words = {'what', 'where', 'how', 'who', 'when', 'why', 'which', 'can', 'do', 'does', 'is', 'are', 'kya', 'kahan', 'kaise', 'kab', 'kon', 'kitna', 'batao', 'bataiye', 'tell', 'price', 'cost', 'fee', 'detail', 'details', 'service', 'services', 'help', 'hi', 'hello', 'hey'}
        words = [w.lower().strip('.,!?') for w in t.split()]
        has_q_word = any(w in q_words for w in words)
        has_q_mark = '?' in t
        
        patterns = [
            r'^(?:my\s+name\s+is|myname\s+is|my\s+name|myname|i\s+am|iam|name\s+is|mera\s+naam\s+is|mera\s+naam|nam\s+hai|nam)\s+(.+?)(?:\s+hai|\s+hoon|\s+hu|\s+h)?$',
            r'^(.+)\s+(?:hai|hoon|hu)$'
        ]
        for pat in patterns:
            m = re.match(pat, t, re.IGNORECASE)
            if m:
                clean = re.sub(r'[\.,!?]', '', m.group(1)).strip()
                if clean and clean.lower() not in q_words:
                    return clean.title()

        if has_q_word or has_q_mark or len(words) > 4:
            return None

        clean = re.sub(r'[\.,!?]', '', t).strip()
        if clean and clean.lower() not in q_words:
            return clean.title()
        return None

    def extract_clean_phone(text: str) -> Optional[str]:
        if not text: return None
        digits = re.sub(r'[^\d+]', '', text)
        clean_num = re.sub(r'[^\d]', '', digits)
        if 7 <= len(clean_num) <= 13:
            return digits
        return None

    # 2. Lead Capture Interception State Machine
    if not session.user_name and user_msg_count > 1:
        extracted_name = extract_clean_name(req.question)
        if extracted_name:
            session.user_name = extracted_name
            db.commit()
            
            is_hindi = bool(re.search(r'[\u0900-\u097F]', req.question))
            if is_hindi:
                answer = f"आपसे मिलकर अच्छा लगा, {extracted_name}! क्या आप अपना मोबाइल नंबर भी शेयर कर सकते हैं?"
            else:
                answer = f"Nice to meet you, {extracted_name}! Could you also share your mobile number?"
            is_lead_captured = True
            
    elif session.user_name and not session.phone_number:
        extracted_phone = extract_clean_phone(req.question)
        if extracted_phone:
            session.phone_number = extracted_phone
            db.commit()
            
            is_hindi = bool(re.search(r'[\u0900-\u097F]', req.question))
            if is_hindi:
                answer = "धन्यवाद! मैंने आपकी जानकारी सुरक्षित कर ली है। आज मैं आपकी और क्या सहायता कर सकता हूँ?"
            else:
                answer = "Thank you! I have saved your details. How else can I help you today?"
            is_lead_captured = True

    # 3. Standard Chat flow (if not currently capturing lead details)
    if not is_lead_captured:
        # Quick Q&A Match
        matched_a = None
        for pair in qa_pairs:
            pair_q = clean_match_string(pair.get("q", ""))
            if pair_q and (pair_q == q_clean or pair_q in q_clean or q_clean in pair_q):
                matched_a = pair.get("a")
                break

        if matched_a:
            raw_answer = matched_a
            sources_data = [{"source_file": "Training Q&A Pairs", "page_number": 1}]
            is_rag = True
            
            # Check if we need to translate/reformulate based on response_lang
            try: s_cfg = json.loads(agent.system_config_json or "{}")
            except: s_cfg = {}
            response_lang = s_cfg.get("response_language", "default")
            
            import re
            is_user_hindi = bool(re.search(r'[\u0900-\u097F]', req.question))
            is_answer_hindi = bool(re.search(r'[\u0900-\u097F]', raw_answer))
            
            needs_translation = False
            target_lang = None
            
            if response_lang == "hindi":
                needs_translation = not is_answer_hindi
                target_lang = "Hindi (using Devanagari script हिंदी लिपि only)"
            elif response_lang == "english":
                try:
                    raw_answer.encode('ascii')
                    is_answer_english = True
                except UnicodeEncodeError:
                    is_answer_english = False
                needs_translation = not is_answer_english
                target_lang = "English"
            elif response_lang == "hinglish":
                needs_translation = True
                target_lang = "Hinglish (Hindi language written using the English/Latin alphabet, e.g. 'Aap kaise hain?', 'Main help karta hoon')"
            elif response_lang == "default":
                if is_user_hindi and not is_answer_hindi:
                    needs_translation = True
                    target_lang = "Hindi (using Devanagari script हिंदी लिपि only)"
                elif not is_user_hindi and is_answer_hindi:
                    needs_translation = True
                    target_lang = "English"
                    
            if needs_translation:
                try:
                    provider = s_cfg.get('provider', 'gemini')
                    model = s_cfg.get('model', 'gemini-3.5-flash')
                    api_key = s_cfg.get('api_key', '')
                    if provider == 'gemini' and not api_key:
                        import os
                        api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

                    from app.services.llm import llm_with_history
                    translation_prompt = (
                        f"You are a professional translator. Translate the following text into {target_lang}.\n"
                        f"Maintain the exact meaning, structure, facts, formatting, and markdown links (do NOT change or translate URLs in links, e.g. [text](url) -> keep url same).\n"
                        f"Text to translate:\n{raw_answer}"
                    )
                    translated_answer = await llm_with_history(
                        question=translation_prompt,
                        system=f"You are a precise translator translating to {target_lang}. Return ONLY the translated text without any conversational preamble, notes, or extra comments.",
                        history=[],
                        provider=provider,
                        model=model,
                        api_key=api_key
                    )
                    answer = translated_answer.strip()
                except Exception as e:
                    logger.error(f"Failed to translate matched Q&A pair with agent's LLM: {e}")
                    # FALLBACK: Try translating using system's global Gemini / Google API Key!
                    try:
                        import os
                        from app.core.config import settings
                        fallback_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "") or settings.GEMINI_API_KEY
                        if fallback_key:
                            logger.info("Attempting translation fallback using global Gemini key...")
                            translated_answer = await llm_with_history(
                                question=translation_prompt,
                                system=f"You are a precise translator translating to {target_lang}. Return ONLY the translated text without any conversational preamble, notes, or extra comments.",
                                history=[],
                                provider="gemini",
                                model="gemini-3.5-flash",
                                api_key=fallback_key
                            )
                            answer = translated_answer.strip()
                        else:
                            raise ValueError("No global Gemini API key available for fallback")
                    except Exception as fallback_err:
                        logger.error(f"Failed translation fallback: {fallback_err}")
                        answer = raw_answer
            else:
                answer = raw_answer
        else:
            # Standard chat RAG logic
            if getattr(req, "history_override", None) is not None:
                history_list = req.history_override
            else:
                db_history_msgs = db.query(AgentPublicMessage).filter(
                    AgentPublicMessage.session_id == req.session_id
                ).order_by(AgentPublicMessage.created_at.asc()).all()[:-1]

                history_list = [{"role": m.role, "content": m.content} for m in db_history_msgs[-6:]]
            try: ds_ids = json.loads(agent.datastores_json or "[]")
            except: ds_ids = []

            from app.services.embedder import embed_query
            from app.services.vector_store import get_vector_store
            from app.services.llm import build_context_and_sources, llm_with_history

            query_emb = embed_query(req.question)
            results = get_vector_store().search_combined(query_emb, agent_id=agent_id, datastore_ids=ds_ids, top_k=5)
            relevant_results = [res for res in results if res[1] > 0.35]
            context, sources_data_raw = build_context_and_sources(relevant_results)
            sources_data = sources_data_raw
            is_rag = bool(context)

            # Add Q&A pairs to context as high-priority training context
            if qa_pairs:
                qa_context_parts = [f"Q: {p.get('q')}\nA: {p.get('a')}" for p in qa_pairs]
                qa_context = "--- CONFIGURED TRAINING Q&A PAIRS ---\n" + "\n\n".join(qa_context_parts) + "\n--- END OF TRAINING Q&A PAIRS ---\n\n"
                context = qa_context + (context or "")

            try: s_cfg = json.loads(agent.system_config_json or "{}")
            except: s_cfg = {}

            response_lang = s_cfg.get("response_language", "default")
            if response_lang == "hindi":
                lang_rule = "LANGUAGE RULE: You MUST always respond in Hindi (हिंदी) only, using the Devanagari script (हिंदी लिपि). Regardless of what language the user writes in (even if they ask in English), you MUST reply in Hindi using Devanagari script. Do NOT use Romanized Hinglish (Latin alphabet) for your responses."
                context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate it and respond in Hindi (Devanagari script) only."
                greeting_rule = "GREETING RULE: Always reply to greetings in Hindi (Devanagari script) only."
            elif response_lang == "english":
                lang_rule = "LANGUAGE RULE: You MUST always respond in English only. Regardless of what language the user writes in (even if they ask in Hindi), you MUST reply in English."
                context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate it and respond in English only."
                greeting_rule = "GREETING RULE: Always reply to greetings in English only."
            elif response_lang == "hinglish":
                lang_rule = "LANGUAGE RULE: You MUST always respond in Hinglish only (Hindi language written using the English/Latin alphabet, e.g. 'Aap kaise hain?', 'Main aapki kya madad kar sakta hoon?'). Regardless of what language the user writes in, you MUST reply in Hinglish. Do NOT use Hindi Devanagari script."
                context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate/transliterate it and respond in Hinglish only."
                greeting_rule = "GREETING RULE: Always reply to greetings in Hinglish only."
            else:
                lang_rule = "LANGUAGE RULE: Respond ONLY in the same language the user uses. If asked in English, reply in English. If asked in Hindi, reply in Hindi using Devanagari script (हिंदी लिपि) only. Do NOT use Romanized Hinglish (Latin alphabet) for Hindi responses. Do not translate unless asked."
                context_lang_rule = "CONTEXT LANGUAGE RULE: The context files might be in a different language (e.g. Hindi) than the user's question (e.g. English). You MUST always translate the context information and respond in the same language as the user's question. If the user asks in English, you MUST answer in English, even if the knowledge base context is in Hindi."
                greeting_rule = "GREETING RULE: Reply to greetings (Hi, Hello, Namaste) in the SAME language the user used."

            identity = (
                f"You are {agent.name}. {agent.personality}\n"
                f"{lang_rule}\n"
                f"{context_lang_rule}\n"
                f"{greeting_rule}\n"
                f"CORE INSTRUCTIONS: {s_cfg.get('system_prompt', '')}\n"
                f"RESPONSE STYLE: Be natural, conversational and helpful.\n"
            )

            if context:
                system = (
                    f"{identity}\n\n"
                    f"--- KNOWLEDGE BASE CONTEXT ---\n"
                    f"{context}\n"
                    f"--- END OF CONTEXT ---\n\n"
                    f"CRITICAL INSTRUCTIONS:\n"
                    f"1. Prioritize answering based on the provided context if it contains the answer.\n"
                    f"2. IMPORTANT: If the context does not contain the answer, or if the user asks a general question unrelated to the context, you MUST use your general AI knowledge to provide a helpful, correct, and complete response. Do NOT say 'information not found in documents' if you can answer it using your general knowledge."
                )
            else:
                system = (
                    f"{identity}\n\n"
                    f"NOTE: No specific information was found in the internal knowledge base.\n"
                    f"INSTRUCTION: Please use your general AI knowledge to provide a helpful and accurate answer.\n\n"
                    f"FINAL DIRECTIVE: Always be helpful. Stop being robotic."
                )

            # For voice queries, override instructions to ensure extreme brevity (under 1-2 sentences) and force fast low-latency models
            if req.is_voice:
                system += "\n\nCRITICAL VOICE DIRECTIVE: Keep your answer extremely short, conversational, and limited to 1-2 sentences maximum. Do NOT use any bullet points, lists, asterisks, or markdown formatting. Speak naturally."
                orig_provider = s_cfg.get('provider', 'gemini')
                if orig_provider == "openai":
                    voice_provider = "openai"
                    voice_model = "gpt-4o-mini"
                    voice_api_key = s_cfg.get('api_key', '')
                elif orig_provider == "groq":
                    voice_provider = "groq"
                    voice_model = "llama-3.1-8b-instant"
                    voice_api_key = s_cfg.get('api_key', '')
                else:
                    voice_provider = "gemini"
                    voice_model = "gemini-3.5-flash"
                    import os
                    voice_api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
            else:
                voice_provider = s_cfg.get('provider', 'gemini')
                voice_model = s_cfg.get('model', 'gemini-3.5-flash')
                voice_api_key = s_cfg.get('api_key', '')

            # Prepend file context if provided
            effective_question = req.question
            if req.file_context:
                effective_question = f"[File Content/Context]:\n{req.file_context}\n\n[User Question]: {req.question}"

            try:
                if response_lang == "hindi":
                    system += "\n\nCRITICAL LANGUAGE DIRECTIVE: Regardless of user question or context, you MUST respond in Hindi (Devanagari script हिंदी लिपि only). Do NOT use English or Hinglish."
                elif response_lang == "english":
                    system += "\n\nCRITICAL LANGUAGE DIRECTIVE: Regardless of user question or context, you MUST respond in English only."
                elif response_lang == "hinglish":
                    system += "\n\nCRITICAL LANGUAGE DIRECTIVE: Regardless of user question or context, you MUST respond in Hinglish only (Hindi written using the English/Latin alphabet, e.g. 'Aap kaise hain?', 'Main help karta hoon'). Do NOT use Devanagari script."

                answer = await llm_with_history(
                    question=effective_question, system=system, history=history_list,
                    provider=voice_provider,
                    model=voice_model,
                    api_key=voice_api_key,
                    ollama_url="http://localhost:11434",
                )
            except Exception as e:
                orig_provider = s_cfg.get('provider', 'gemini')
                if voice_provider == 'gemini' and orig_provider != 'gemini':
                    logger.warning(f"Voice Gemini failed: {e}. Falling back to agent's configured provider {orig_provider}...")
                    try:
                        answer = await llm_with_history(
                            question=effective_question, system=system, history=history_list,
                            provider=orig_provider,
                            model=s_cfg.get('model', 'gemini-3.5-flash'),
                            api_key=s_cfg.get('api_key', ''),
                            ollama_url="http://localhost:11434",
                        )
                    except Exception as fallback_err:
                        answer = f"Error generating response (Primary and Fallback failed): {fallback_err}"
                elif voice_provider != 'gemini':
                    logger.warning(f"Primary LLM provider {voice_provider} failed: {e}. Falling back to Gemini...")
                    try:
                        import os
                        fallback_api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
                        answer = await llm_with_history(
                            question=effective_question, system=system, history=history_list,
                            provider='gemini',
                            model='gemini-3.5-flash',
                            api_key=fallback_api_key,
                            ollama_url="http://localhost:11434",
                        )
                    except Exception as fallback_err:
                        answer = f"Error generating response (Primary and Fallback failed): {fallback_err}"
                else:
                    answer = f"Error generating response: {e}"

        # 4. Append name or phone prompt if not set yet
        if not session.user_name and user_msg_count >= 3 and (user_msg_count - 3) % 3 == 0:
            is_hindi = bool(re.search(r'[\u0900-\u097F]', answer))
            if is_hindi:
                answer += "\n\nवैसे, आपका नाम क्या है?"
            else:
                answer += "\n\nBy the way, what is your name?"
        elif session.user_name and not session.phone_number and user_msg_count >= 4 and (user_msg_count - 4) % 3 == 0:
            is_hindi = bool(re.search(r'[\u0900-\u097F]', answer))
            if is_hindi:
                answer += "\n\nक्या आप अपना मोबाइल नंबर भी शेयर कर सकते हैं?"
            else:
                answer += "\n\nCould you also share your mobile number?"

    # 5. Log assistant response to DB
    asst_msg = AgentPublicMessage(
        session_id=req.session_id,
        role="assistant",
        content=answer
    )
    db.add(asst_msg)
    db.commit()

    suggested_questions = []
    action_button = None

    try: c_cfg = json.loads(agent.customization_json or "{}")
    except: c_cfg = {}
    whatsapp_number = c_cfg.get("whatsapp_number", "")
    call_number = c_cfg.get("call_number", "")
    
    if not whatsapp_number or not call_number:
        from app.core.models import Client
        client_obj = db.query(Client).filter(Client.client_id == agent.client_id).first()
        if client_obj:
            if not whatsapp_number: whatsapp_number = client_obj.mobile_number or ""
            if not call_number: call_number = client_obj.mobile_number or ""

    if user_msg_count >= 2 and not req.is_voice:
        db_msgs = db.query(AgentPublicMessage).filter(
            AgentPublicMessage.session_id == req.session_id
        ).order_by(AgentPublicMessage.created_at.asc()).all()
        
        chat_lines = []
        for m in db_msgs:
            chat_lines.append(f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}")
        history_text = "\n".join(chat_lines)
        
        analysis = await analyze_chat_for_suggestions_and_actions(history_text, agent)
        suggested_questions = analysis.get("suggested_questions", [])
        
        if analysis.get("meeting_intent") and whatsapp_number:
            action_button = {
                "action_type": "whatsapp",
                "phone_number": whatsapp_number,
                "message": "Schedule a meeting with us",
                "created_at": datetime.utcnow().isoformat()
            }
        elif analysis.get("call_intent") and call_number:
            action_button = {
                "action_type": "call",
                "phone_number": call_number,
                "message": "Give us a call",
                "created_at": datetime.utcnow().isoformat()
            }

        if action_button:
            session.action_button_json = json.dumps(action_button)
            db.commit()

    if not suggested_questions:
        try:
            qa_pairs = c_cfg.get("qa_pairs", [])
            suggested_questions = [p.get("q") for p in qa_pairs if p.get("q")][:5]
        except:
            suggested_questions = []

    return {
        "answer": answer,
        "sources": [s.__dict__ if hasattr(s, '__dict__') else dict(s) for s in sources_data],
        "is_rag": is_rag,
        "session_id": session.session_id,
        "action_button": action_button or (json.loads(session.action_button_json) if session.action_button_json else None),
        "suggested_questions": suggested_questions
    }


@router.get("/agents/{agent_id}/sessions", tags=["Agents & DataStores"])
async def api_get_agent_sessions(agent_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import Agent, AgentPublicSession
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client["client_id"]).first()
    if not agent:
        raise HTTPException(404, "Agent not found")

    sessions = db.query(AgentPublicSession).filter(
        AgentPublicSession.agent_id == agent_id
    ).order_by(AgentPublicSession.updated_at.desc()).all()

    return [s.to_dict() for s in sessions]


@router.get("/agents/sessions/{session_id}/history", tags=["Agents & DataStores"])
async def api_get_session_history(session_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import AgentPublicSession, AgentPublicMessage, Agent
    session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")

    agent = db.query(Agent).filter(Agent.agent_id == session.agent_id, Agent.client_id == client["client_id"]).first()
    if not agent:
        raise HTTPException(403, "Access denied")

    messages = db.query(AgentPublicMessage).filter(
        AgentPublicMessage.session_id == session_id
    ).order_by(AgentPublicMessage.created_at.asc()).all()

    return [m.to_dict() for m in messages]


@router.get("/agents/{agent_id}/public-history", tags=["Agents & DataStores"])
async def api_get_public_history(agent_id: str, device_id: Optional[str] = None, session_id: Optional[str] = None, db: Session = Depends(get_db)):
    from app.core.models import AgentPublicSession, AgentPublicMessage
    session = None
    if session_id:
        session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == session_id).first()
    elif device_id:
        session = db.query(AgentPublicSession).filter(
            AgentPublicSession.agent_id == agent_id,
            AgentPublicSession.device_id == device_id
        ).order_by(AgentPublicSession.updated_at.desc()).first()

    if not session:
        return {"session": None, "messages": []}

    # Automatically fill details from same device if missing
    dev_id_to_use = device_id or session.device_id
    if dev_id_to_use:
        auto_fill_visitor_details_from_device(db, session, agent_id, dev_id_to_use)

    messages = db.query(AgentPublicMessage).filter(
        AgentPublicMessage.session_id == session.session_id
    ).order_by(AgentPublicMessage.created_at.asc()).all()

    return {
        "session": session.to_dict(),
        "messages": [m.to_dict() for m in messages]
    }


@router.get("/agents/{agent_id}/session-status", tags=["Agents & DataStores"])
async def api_get_public_session_status(agent_id: str, device_id: Optional[str] = None, session_id: Optional[str] = None, db: Session = Depends(get_db)):
    from app.core.models import AgentPublicSession
    session = None
    if session_id:
        session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == session_id).first()
    elif device_id:
        session = db.query(AgentPublicSession).filter(
            AgentPublicSession.agent_id == agent_id,
            AgentPublicSession.device_id == device_id
        ).order_by(AgentPublicSession.updated_at.desc()).first()

    if not session:
        return {"session": None}

    # Automatically fill details from same device if missing
    dev_id_to_use = device_id or session.device_id
    if dev_id_to_use:
        auto_fill_visitor_details_from_device(db, session, agent_id, dev_id_to_use)

    return {"session": session.to_dict()}


class AgentSessionSendActionReq(BaseModel):
    action_type: str  # "call" | "whatsapp"
    phone_number: str
    message: Optional[str] = None


@router.post("/agents/sessions/{session_id}/send-action", tags=["Agents & DataStores"])
async def api_send_session_action(session_id: str, req: AgentSessionSendActionReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import AgentPublicSession, Agent, AgentPublicMessage
    session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")

    agent = db.query(Agent).filter(Agent.agent_id == session.agent_id, Agent.client_id == client["client_id"]).first()
    if not agent:
        raise HTTPException(403, "Access denied")

    action_data = {
        "action_type": req.action_type,
        "phone_number": req.phone_number,
        "message": req.message or ("Call Us Now" if req.action_type == "call" else "Connect on WhatsApp"),
        "created_at": datetime.utcnow().isoformat()
    }
    session.action_button_json = json.dumps(action_data)
    session.updated_at = datetime.utcnow()

    # Log action trigger message in history
    action_label = "📞 Call Now" if req.action_type == "call" else "💬 Connect on WhatsApp"
    msg_content = f"⚡ Action Sent: {action_label} ({req.phone_number})"
    asst_msg = AgentPublicMessage(
        session_id=session_id,
        role="assistant",
        content=msg_content
    )
    db.add(asst_msg)
    db.commit()

    return {"status": "success", "action_button": action_data}


@router.delete("/agents/sessions/{session_id}/clear-action", tags=["Agents & DataStores"])
async def api_clear_session_action(session_id: str, db: Session = Depends(get_db)):
    from app.core.models import AgentPublicSession
    session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")

    session.action_button_json = None
    db.commit()
    return {"status": "success"}


def repair_json(s: str) -> str:
    """Repair incomplete/truncated JSON strings by matching and closing open quotes/brackets."""
    s = s.strip()
    if not s:
        return "{}"
    in_quote = False
    escape = False
    repaired = []
    stack = []
    
    for char in s:
        if escape:
            repaired.append(char)
            escape = False
            continue
        if char == '\\':
            repaired.append(char)
            escape = True
            continue
        if char == '"':
            in_quote = not in_quote
            repaired.append(char)
            continue
        
        if not in_quote:
            if char == '{':
                stack.append('}')
            elif char == '[':
                stack.append(']')
            elif char == '}':
                if stack and stack[-1] == '}':
                    stack.pop()
            elif char == ']':
                if stack and stack[-1] == ']':
                    stack.pop()
        repaired.append(char)
        
    if in_quote:
        repaired.append('"')
    while stack:
        repaired.append(stack.pop())
        
    return "".join(repaired)


@router.post("/agents/sessions/{session_id}/analyze", tags=["Agents & DataStores"])
async def api_analyze_session(session_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import AgentPublicSession, AgentPublicMessage, Agent
    session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")

    agent = db.query(Agent).filter(Agent.agent_id == session.agent_id, Agent.client_id == client["client_id"]).first()
    if not agent:
        raise HTTPException(403, "Access denied")

    # Get conversation messages
    messages = db.query(AgentPublicMessage).filter(
        AgentPublicMessage.session_id == session_id
    ).order_by(AgentPublicMessage.created_at.asc()).all()

    if not messages:
        raise HTTPException(400, "No messages to analyze in this session")

    # Build chat history representation for LLM prompt
    chat_lines = []
    for m in messages:
        role_label = "User" if m.role == "user" else "Agent"
        chat_lines.append(f"{role_label}: {m.content}")
    conversation_text = "\n".join(chat_lines)

    # Get agent's LLM settings
    try: s_cfg = json.loads(agent.system_config_json or "{}")
    except: s_cfg = {}

    provider = s_cfg.get('provider', 'gemini')
    model = s_cfg.get('model', 'gemini-3.5-flash')
    api_key = s_cfg.get('api_key', '')
    
    if provider == 'gemini' and not api_key:
        import os
        api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

    # Build analysis prompt
    system_prompt = (
        "You are an expert AI business analyst. "
        "You MUST analyze the chat history between a user (Visitor/User) and an AI agent, "
        "and return a JSON response object. Do not explain, do not add markdown code blocks like ```json."
        "The output MUST match this exact JSON schema:\n"
        "{\n"
        "  \"category\": \"marketing\" or \"calling\" or \"meeting\" (categorize based on user's main objective),\n"
        "  \"intent\": \"Brief summary of what the user wants to do / what their goal is (in Hinglish/Hindi or English as appropriate),\",\n"
        "  \"meaning\": \"Deep explanation/meaning of what this chat signifies,\",\n"
        "  \"next_steps\": \"Specific, action-oriented next steps for the agent or business owner.\"\n"
        "}\n"
        "Ensure all descriptions are helpful, detailed and in Hinglish/Hindi or English (matching the tone/language of the conversation if appropriate). "
        "Return ONLY the raw JSON string."
    )

    question = (
        f"Here is the chat history to analyze:\n\n"
        f"{conversation_text}\n\n"
        f"Please analyze this conversation and return the JSON object."
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
        logger.error(f"Analysis LLM call failed: {e}")
        # Fallback to Gemini with env api key
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
            raise HTTPException(502, f"Analysis failed: {fallback_err}")

    # Extract JSON robustly
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
        try:
            analyzed_data = json.loads(json_content)
        except Exception:
            # Attempt parsing after repairing truncated JSON
            repaired = repair_json(json_content)
            analyzed_data = json.loads(repaired)
        # Validate keys
        for key in ["category", "intent", "meaning", "next_steps"]:
            if key not in analyzed_data:
                analyzed_data[key] = "Not specified"
    except Exception as e:
        logger.error(f"Failed to parse LLM analysis: {e}. Raw content: {raw_result}")
        # Construct fallback dictionary if JSON parsing failed
        analyzed_data = {
            "category": "marketing",
            "intent": "Analysis parsing failed",
            "meaning": raw_result,
            "next_steps": "Please review the raw chat logs."
        }

    # Save to session
    session.analysis_json = json.dumps(analyzed_data)
    db.commit()

    return analyzed_data


@router.websocket("/agents/ws/transcribe")
async def websocket_transcribe(websocket: WebSocket):
    import websockets
    import os
    import json
    import asyncio
    from fastapi import WebSocketDisconnect
    
    await websocket.accept()
    
    dg_api_key = os.getenv("DEEPGRAM_API_KEY", "5d3770e0a1b4aa755f6d799839bb62ba5561a868")
    if not dg_api_key:
        await websocket.send_json({"error": "DEEPGRAM_API_KEY not configured"})
        await websocket.close()
        return

    # Nova-3 Multilingual streaming STT with auto language detection and endpointing
    dg_url = "wss://api.deepgram.com/v1/listen?model=nova-3&language=multi&punctuate=true&smart_format=true&interim_results=true&endpointing=100"
    headers = {
        "Authorization": f"Token {dg_api_key}"
    }

    try:
        connected = False
        for attempt in range(3):
            try:
                async with websockets.connect(dg_url, additional_headers=headers, open_timeout=15.0) as dg_ws:
                    connected = True
                    
                    async def receive_from_client():
                        try:
                            while True:
                                data = await websocket.receive()
                                if "bytes" in data:
                                    await dg_ws.send(data["bytes"])
                                elif "text" in data:
                                    msg = json.loads(data["text"])
                                    if msg.get("type") == "stop":
                                        break
                        except WebSocketDisconnect:
                            pass
                        except Exception as e:
                            # Ignore normal socket closure logs
                            e_str = str(e).lower()
                            if "disconnect" not in e_str and "closed" not in e_str and "receive" not in e_str:
                                logger.error(f"Error receiving from client: {e}")
                        finally:
                            try:
                                await dg_ws.send(b"")
                            except:
                                pass

                    async def send_to_client():
                        try:
                            async for message in dg_ws:
                                await websocket.send_text(message)
                        except WebSocketDisconnect:
                            pass
                        except Exception as e:
                            # Ignore normal socket closure logs
                            e_str = str(e).lower()
                            if "disconnect" not in e_str and "closed" not in e_str and "send" not in e_str:
                                logger.error(f"Error sending to client: {e}")

                    await asyncio.gather(receive_from_client(), send_to_client())
                break
            except Exception as e:
                if connected:
                    # If it failed mid-session, do not retry
                    raise e
                if attempt == 2:
                    # If all retries failed, raise the connection exception
                    raise e
                logger.warning(f"Deepgram handshake failed (attempt {attempt + 1}/3): {e}. Retrying in 0.5s...")
                await asyncio.sleep(0.5)
    except Exception as e:
        logger.error(f"Deepgram WebSocket error: {e}")
        try:
            await websocket.send_json({"error": f"Connection failed: {e}"})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass


@router.post("/agents/{agent_id}/feedback", tags=["Agents & DataStores"])
async def api_submit_agent_feedback(agent_id: str, req: AgentFeedbackCreate, db: Session = Depends(get_db)):
    from app.core.models import Agent, AgentFeedback
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    fb = AgentFeedback(
        agent_id=agent_id,
        user_name=req.user_name,
        user_email=req.user_email,
        feedback_type=req.feedback_type,
        rating=req.rating,
        comment=req.comment,
        device_id=req.device_id,
        session_id=req.session_id
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return {"status": "ok", "feedback": fb.to_dict()}


@router.get("/agents/{agent_id}/feedback", tags=["Agents & DataStores"])
async def api_get_agent_feedback(agent_id: str, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    from app.core.models import Agent, AgentFeedback
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.client_id == client["client_id"]).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or access denied")

    feedbacks = db.query(AgentFeedback).filter(AgentFeedback.agent_id == agent_id).order_by(AgentFeedback.created_at.desc()).all()
    return [fb.to_dict() for fb in feedbacks]


# ── File Upload for Chat ──────────────────────────────────────────────────────

MAX_CHAT_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

@router.post("/agents/{agent_id}/upload-chat-file", tags=["Agents & DataStores"])
async def api_upload_chat_file(agent_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a file (image/PDF/doc/video) and extract text context for AI chat."""
    from app.core.models import Agent
    import io, base64

    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(404, "Agent not found")

    # Read file bytes
    file_bytes = await file.read()
    if len(file_bytes) > MAX_CHAT_FILE_SIZE:
        raise HTTPException(413, f"File too large. Maximum size is 20MB.")

    filename = file.filename or "uploaded_file"
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    content_type = file.content_type or ''
    extracted_text = ""
    file_type = "document"
    preview_data_url = None

    # ── Image files ──
    if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp') or content_type.startswith('image/'):
        file_type = "image"
        # Build base64 data URL for preview
        b64 = base64.b64encode(file_bytes).decode()
        mime = content_type if content_type.startswith('image/') else f'image/{ext}'
        preview_data_url = f"data:{mime};base64,{b64}"

        # Try Gemini Vision to describe the image using agent's configured API key
        try:
            s_cfg_raw = agent.system_config_json or '{}'
            try: s_cfg = json.loads(s_cfg_raw)
            except: s_cfg = {}

            provider = s_cfg.get('provider', 'gemini')
            api_key = s_cfg.get('api_key', '')

            if provider == 'gemini':
                g_key = api_key or os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
                if g_key:
                    import httpx as _httpx
                    vision_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={g_key}"
                    payload = {
                        "contents": [{
                            "parts": [
                                {"text": "Describe this image in detail. Include all text visible in the image, objects, colors, context and any important information shown."},
                                {"inline_data": {"mime_type": mime, "data": b64}}
                            ]
                        }]
                    }
                    async with _httpx.AsyncClient(timeout=30.0) as hc:
                        r = await hc.post(vision_url, json=payload)
                        if r.status_code == 200:
                            data = r.json()
                            extracted_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            elif provider in ('openai',):
                if api_key:
                    from openai import AsyncOpenAI
                    oa = AsyncOpenAI(api_key=api_key)
                    resp = await oa.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": [
                            {"type": "text", "text": "Describe this image in detail, including all visible text and important context."},
                            {"type": "image_url", "image_url": {"url": preview_data_url}}
                        ]}],
                        max_tokens=1024
                    )
                    extracted_text = resp.choices[0].message.content.strip()

            if not extracted_text:
                extracted_text = f"[Image uploaded: {filename}]"
        except Exception as img_err:
            logger.warning(f"Image vision failed: {img_err}")
            extracted_text = f"[Image uploaded: {filename}]"

    # ── PDF files ──
    elif ext == 'pdf' or content_type == 'application/pdf':
        file_type = "pdf"
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for page in reader.pages[:30]:  # max 30 pages
                t = page.extract_text() or ""
                t = t.replace('\x00', '')
                pages_text.append(t)
            extracted_text = "\n\n".join(pages_text).strip()
            if not extracted_text:
                extracted_text = f"[PDF uploaded: {filename} — no text could be extracted]"
        except Exception as pdf_err:
            logger.warning(f"PDF extraction failed: {pdf_err}")
            extracted_text = f"[PDF uploaded: {filename}]"

    # ── DOCX files ──
    elif ext in ('docx', 'doc') or 'wordprocessingml' in content_type:
        file_type = "document"
        try:
            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            extracted_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            if not extracted_text:
                extracted_text = f"[Document uploaded: {filename} — no text could be extracted]"
        except Exception as doc_err:
            logger.warning(f"DOCX extraction failed: {doc_err}")
            extracted_text = f"[Document uploaded: {filename}]"

    # ── Plain text / CSV files ──
    elif ext in ('txt', 'csv', 'md', 'json', 'xml') or content_type.startswith('text/'):
        file_type = "text"
        try:
            extracted_text = file_bytes.decode('utf-8', errors='replace').replace('\x00', '')
            if len(extracted_text) > 20000:
                extracted_text = extracted_text[:20000] + "\n...[truncated]"
        except Exception as txt_err:
            extracted_text = f"[Text file uploaded: {filename}]"

    # ── Video files ──
    elif ext in ('mp4', 'webm', 'mov', 'avi', 'mkv') or content_type.startswith('video/'):
        file_type = "video"
        size_mb = round(len(file_bytes) / (1024 * 1024), 1)
        extracted_text = f"[Video uploaded: {filename} ({size_mb} MB). The user has shared a video file. Please acknowledge it and ask what they would like to know about it.]"

    else:
        # Generic fallback
        file_type = "file"
        extracted_text = f"[File uploaded: {filename}]"

    # Save file to disk
    file_url = None
    try:
        os.makedirs("uploads", exist_ok=True)
        import uuid
        unique_filename = f"chat_{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join("uploads", unique_filename)
        with open(filepath, "wb") as f:
            f.write(file_bytes)
        file_url = f"/uploads/{unique_filename}"
    except Exception as save_err:
        logger.error(f"Failed to save chat file to disk: {save_err}")

    return {
        "success": True,
        "file_type": file_type,
        "display_name": filename,
        "extracted_text": extracted_text,
        "preview_data_url": preview_data_url,
        "size_bytes": len(file_bytes),
        "file_url": file_url
    }


# ── Analyze ALL sessions by device ───────────────────────────────────────────

class AnalyzeDeviceReq(BaseModel):
    device_id: str
    agent_id: str


@router.post("/agents/sessions/analyze-device", tags=["Agents & DataStores"])
async def api_analyze_device_sessions(
    req: AnalyzeDeviceReq,
    x_app_token: Optional[str] = Header(None, alias="X-App-Token"),
    db: Session = Depends(get_db)
):
    """Analyze ALL chat sessions from a specific device for holistic visitor insight."""
    client = _get_client(x_app_token, db)
    from app.core.models import AgentPublicSession, AgentPublicMessage, Agent
    from app.services.llm import llm_with_history

    agent = db.query(Agent).filter(
        Agent.agent_id == req.agent_id,
        Agent.client_id == client["client_id"]
    ).first()
    if not agent:
        raise HTTPException(403, "Access denied or agent not found")

    # Get all sessions for this device & agent
    sessions = db.query(AgentPublicSession).filter(
        AgentPublicSession.agent_id == req.agent_id,
        AgentPublicSession.device_id == req.device_id
    ).order_by(AgentPublicSession.created_at.asc()).all()

    if not sessions:
        raise HTTPException(404, "No sessions found for this device")

    # Merge all messages from all sessions chronologically
    all_chat_lines = []
    total_messages = 0
    for idx, sess in enumerate(sessions):
        messages = db.query(AgentPublicMessage).filter(
            AgentPublicMessage.session_id == sess.session_id
        ).order_by(AgentPublicMessage.created_at.asc()).all()
        if messages:
            all_chat_lines.append(f"--- Session {idx + 1} (Started: {sess.created_at}) ---")
            for m in messages:
                role_label = "User" if m.role == "user" else "Agent"
                all_chat_lines.append(f"{role_label}: {m.content}")
            total_messages += len(messages)

    if not all_chat_lines:
        raise HTTPException(400, "No messages found across all sessions for this device")

    conversation_text = "\n".join(all_chat_lines)

    # Get agent's LLM config
    try: s_cfg = json.loads(agent.system_config_json or "{}")
    except: s_cfg = {}

    provider = s_cfg.get('provider', 'gemini')
    model = s_cfg.get('model', 'gemini-3.5-flash')
    api_key = s_cfg.get('api_key', '')
    if provider == 'gemini' and not api_key:
        api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

    system_prompt = (
        "You are an expert AI business analyst and CRM specialist. "
        "You are analyzing the COMPLETE conversation history of a single visitor across ALL their chat sessions with an AI agent. "
        "Your job is to provide a holistic, deep analysis of this visitor and return a JSON response. "
        "Do not explain, do not add markdown code blocks like ```json. "
        "The output MUST match this exact JSON schema:\n"
        "{\n"
        "  \"category\": \"marketing\" or \"calling\" or \"meeting\" or \"support\" (main purpose),\n"
        "  \"intent\": \"What is the visitor's overall goal across all sessions (Hinglish/Hindi or English)\",\n"
        "  \"meaning\": \"Deep explanation — who is this visitor, what are they looking for, and why did they come back?\",\n"
        "  \"next_steps\": \"Action-oriented steps the business should take to convert or help this visitor.\",\n"
        "  \"key_points\": [\"Key insight 1\", \"Key insight 2\", \"Key insight 3\"] (bullet list of important things: what they asked, what they need, any personal info they shared, urgency, etc.)\n"
        "}\n"
        "key_points must be an array of strings (3-7 items). Ensure all descriptions are detailed and helpful. Return ONLY the raw JSON string."
    )

    question = (
        f"Here is the complete visitor conversation history across {len(sessions)} session(s):\n\n"
        f"{conversation_text}\n\n"
        f"Please analyze ALL sessions holistically and return the JSON analysis."
    )

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
        logger.error(f"Device analysis LLM call failed: {e}")
        try:
            fallback_api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
            raw_result = await llm_with_history(
                question=question, system=system_prompt, history=[],
                provider="gemini", model="gemini-3.5-flash", api_key=fallback_api_key,
                ollama_url="http://localhost:11434"
            )
        except Exception as fe:
            raise HTTPException(502, f"Analysis failed: {fe}")

    # Parse JSON robustly
    import re
    match = re.search(r"```json\s*(.*?)\s*```", raw_result, re.DOTALL | re.IGNORECASE)
    if match:
        json_content = match.group(1).strip()
    else:
        match_simple = re.search(r"```\s*(.*?)\s*```", raw_result, re.DOTALL)
        json_content = match_simple.group(1).strip() if match_simple else raw_result.strip()

    try:
        try:
            analyzed = json.loads(json_content)
        except Exception:
            # Attempt parsing after repairing truncated JSON
            repaired = repair_json(json_content)
            analyzed = json.loads(repaired)
        for key in ["category", "intent", "meaning", "next_steps", "key_points"]:
            if key not in analyzed:
                analyzed[key] = [] if key == "key_points" else "Not specified"
        if not isinstance(analyzed.get("key_points"), list):
            analyzed["key_points"] = [str(analyzed["key_points"])]
    except Exception as parse_err:
        logger.error(f"Failed to parse device analysis JSON: {parse_err}. Raw: {raw_result}")
        analyzed = {
            "category": "marketing",
            "intent": "Analysis parsing failed",
            "meaning": raw_result,
            "next_steps": "Please review the raw chat logs.",
            "key_points": []
        }

    analyzed["session_count"] = len(sessions)
    analyzed["total_messages"] = total_messages
    return analyzed

# OpenAI compatibility models
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    model: Optional[str] = "default"

voice_transcripts = {}

def save_voice_chat_to_db(db: Session, agent_id: str, session_id: str, user_text: str, assistant_text: str):
    try:
        from app.core.models import AgentPublicSession, AgentPublicMessage
        session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == session_id).first()
        if not session:
            session = AgentPublicSession(
                session_id=session_id,
                agent_id=agent_id,
                device_id="dograh-voice-client",
                device_name="Voice Call"
            )
            db.add(session)
            db.commit()
            db.refresh(session)
        else:
            session.updated_at = datetime.utcnow()
            db.commit()
            
        if user_text:
            user_msg = AgentPublicMessage(
                session_id=session_id,
                role="user",
                content=user_text
            )
            db.add(user_msg)
            
        if assistant_text:
            asst_msg = AgentPublicMessage(
                session_id=session_id,
                role="assistant",
                content=assistant_text
            )
            db.add(asst_msg)
            
        db.commit()
        logger.info(f"Saved voice chat turn to DB for session {session_id}")
    except Exception as e:
        logger.error(f"Failed to save voice chat turn to DB: {e}")

@router.post("/agents/{agent_id}/chat/completions", tags=["Agents & DataStores"])
async def api_agent_openai_compatible_chat(
    agent_id: str,
    req: ChatCompletionRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    from fastapi.responses import StreamingResponse
    import uuid
    import asyncio
    import re
    import json
    from datetime import datetime
    from app.services.llm import get_llm_async_client
    from app.core.models import AgentPublicSession
    
    # Extract agent ID dynamically from the system message if provided in the Dograh workflow context
    parsed_agent_id = None
    parsed_session_id = None
    for msg in req.messages:
        if msg.role == "system":
            match = re.search(r'agent_id["\'\s:=]+([a-f0-9]{8,})', msg.content, re.IGNORECASE)
            if match:
                parsed_agent_id = match.group(1)
                logger.info(f"Dynamically routing request to Agent ID: {parsed_agent_id} (parsed from system context)")
            
            match_session = re.search(r'session_id["\'\s:=]+(vsession-[a-z0-9]+)', msg.content, re.IGNORECASE)
            if match_session:
                parsed_session_id = match_session.group(1)
                logger.info(f"Parsed voice session ID: {parsed_session_id}")
            break

    # Try to map parsed_session_id to recent IVR selection if it is a new session
    # Or load the agent_id of the existing session if it already exists
    session_agent_id = None
    if parsed_session_id:
        # Check if we already have this vsession-xxxxxx mapped
        session_exists = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == parsed_session_id).first()
        if session_exists:
            session_agent_id = session_exists.agent_id
            print(f"Loaded active voice session {parsed_session_id} for agent {session_agent_id}", flush=True)
            logger.info(f"Loaded active voice session {parsed_session_id} for agent {session_agent_id}")

    # Fallback: If no session was loaded (new session or greeting turn without parsed_session_id),
    # look up any phone session ("tel_xxxx") updated in the last 40 seconds to route correctly
    if not session_agent_id:
        from datetime import timedelta
        recent_time = datetime.utcnow() - timedelta(seconds=40)
        recent_ivr = db.query(AgentPublicSession).filter(
            AgentPublicSession.session_id.like("tel_%"),
            AgentPublicSession.updated_at >= recent_time
        ).order_by(AgentPublicSession.updated_at.desc()).first()
        if recent_ivr:
            session_agent_id = recent_ivr.agent_id
            print(f"IVR OVERRIDE: mapped request to agent {session_agent_id} from phone session {recent_ivr.session_id}", flush=True)
            logger.info(f"IVR OVERRIDE: mapped request to agent {session_agent_id} from phone session {recent_ivr.session_id}")
            # Heartbeat keep-alive: update the updated_at timestamp to prevent expiration during call
            try:
                recent_ivr.updated_at = datetime.utcnow()
                db.commit()
            except Exception as commit_err:
                db.rollback()
                logger.warning(f"Failed to commit IVR heartbeat update: {commit_err}")

    active_agent_id = session_agent_id or parsed_agent_id or agent_id

    # ROUTING PRIORITY 1: Model field as agent ID (most scalable approach)
    # In Dograh: set Model = <agent_id> (e.g. d6b54c1e63290e77)
    # Works for 1 lakh agents with ONE shared base URL and API key
    if active_agent_id in ("chat", "v1", "models"):
        model_val = (req.model or "").strip()
        if re.match(r'^[a-f0-9]{8,32}$', model_val, re.IGNORECASE):
            active_agent_id = model_val
            logger.info(f"Routing via model-name agent ID: {active_agent_id}")

    # ROUTING PRIORITY 2: Bearer token as agent ID (fallback)
    # In Dograh: set API Key = <agent_id> (e.g. d6b54c1e63290e77)
    if active_agent_id in ("chat", "v1", "models"):
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            bearer_tok = auth_hdr[7:].strip()
            if re.match(r'^[a-f0-9]{8,32}$', bearer_tok, re.IGNORECASE):
                active_agent_id = bearer_tok
                logger.info(f"Routing via Bearer token agent ID: {active_agent_id}")

    # Connection Validation Fallback — only if still no real agent ID resolved
    if active_agent_id in ("chat", "v1", "models"):
        answer = "Connection successful. OpenAI compatible agent completions server is active."
        if req.stream:
            async def stream_mock_generator():
                chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
                created_time = int(datetime.utcnow().timestamp())
                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                words = answer.split(" ")
                for i, word in enumerate(words):
                    space = " " if i > 0 else ""
                    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'content': space + word}, 'finish_reason': None}]})}\n\n"
                    await asyncio.sleep(0.01)
                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(stream_mock_generator(), media_type="text/event-stream")
        else:
            return {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(datetime.utcnow().timestamp()),
                "model": req.model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]
            }

    # Check if this is the initial greeting generation turn (no user messages in history yet)
    is_greeting_turn = not any(msg.role == "user" for msg in req.messages)
    
    if is_greeting_turn:
        from app.core.models import Agent
        agent_obj = db.query(Agent).filter(Agent.agent_id == active_agent_id).first()
        answer = agent_obj.starting_message if (agent_obj and agent_obj.starting_message) else "Hello! How can I help you today?"
        logger.info(f"Greeting turn: Served starting message for Agent ID {active_agent_id}: '{answer}'")
        
        if parsed_session_id:
            voice_transcripts[parsed_session_id] = {
                "user": "",
                "assistant": answer
            }
            save_voice_chat_to_db(db, active_agent_id, parsed_session_id, user_text="", assistant_text=answer)
        
        # Stream the starting greeting word-by-word with a simulated tiny delay to starting playing audio
        if req.stream:
            async def stream_greeting_generator():
                chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
                created_time = int(datetime.utcnow().timestamp())
                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                words = answer.split(" ")
                for i, word in enumerate(words):
                    if await request.is_disconnected():
                        break
                    space = " " if i > 0 else ""
                    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'content': space + word}, 'finish_reason': None}]})}\n\n"
                    await asyncio.sleep(0.01)
                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(stream_greeting_generator(), media_type="text/event-stream")
        else:
            return {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(datetime.utcnow().timestamp()),
                "model": req.model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]
            }
            
    # Non-greeting turn: Process user input
    # Filter out Dograh-injected idle/quiet messages — these are NOT real user speech
    DOGRAH_INJECTED_PHRASES = [
        "the user has been quiet",
        "user has been quiet",
        "politely and briefly ask if they",
        "user is silent",
    ]
    def is_dograh_injected(content: str) -> bool:
        c = (content or "").lower().strip()
        return any(phrase in c for phrase in DOGRAH_INJECTED_PHRASES)

    # Find the REAL last user message (skip Dograh injected ones)
    user_msg = ""
    for msg in reversed(req.messages):
        if msg.role == "user" and not is_dograh_injected(msg.content):
            user_msg = msg.content.strip()
            break
    if not user_msg:
        user_msg = "hello"
        
    if parsed_session_id:
        voice_transcripts[parsed_session_id] = {
            "user": user_msg,
            "assistant": ""
        }
        
    # Extract history override — also skip Dograh injected idle messages
    history_override = []
    user_skipped = False
    for msg in req.messages:
        if msg.role == "system":
            continue
        # Skip Dograh-injected "user has been quiet" messages from history
        if msg.role == "user" and is_dograh_injected(msg.content):
            continue
        if msg.role == "user" and msg.content.strip() == user_msg and not user_skipped:
            user_skipped = True
            continue
        history_override.append({"role": msg.role, "content": msg.content})
    history_override = history_override[-6:]
    
    # If streaming is requested and the provider is Groq or Gemini, run real-time streaming to reduce latency
    if req.stream:
        from app.core.models import Agent
        from app.core.config import settings
        import httpx
        
        agent_obj = db.query(Agent).filter(Agent.agent_id == active_agent_id).first()
        if agent_obj:
            try: s_cfg = json.loads(agent_obj.system_config_json or "{}")
            except: s_cfg = {}
            provider = s_cfg.get('provider', 'gemini')
            model = s_cfg.get('model', 'gemini-3.5-flash')
            api_key = s_cfg.get('api_key', '')
            
            # Fetch default keys if not set
            if provider == "groq":
                if api_key and (api_key.startswith("sk-") or api_key.startswith("AIzaSy")):
                    api_key = ""
                api_key = api_key or settings.GROQ_API_KEY
            elif provider == "gemini":
                if api_key and (api_key.startswith("sk-") or api_key.startswith("gsk_")):
                    api_key = ""
                api_key = api_key or settings.GEMINI_API_KEY
                
            # Perform RAG retrieval to construct system prompt context
            try: ds_ids = json.loads(agent_obj.datastores_json or "[]")
            except: ds_ids = []
            try: custom_cfg = json.loads(agent_obj.customization_json or "{}")
            except: custom_cfg = {}
            qa_pairs = custom_cfg.get("qa_pairs", [])
            
            from app.services.embedder import embed_query
            from app.services.vector_store import get_vector_store
            from app.services.llm import build_context_and_sources

            # Skip RAG for very short/conversational messages (saves 300-500ms)
            # These are greetings/acks that don't need document context
            _short_msg_words = len(user_msg.strip().split())
            _conversational_keywords = {"hello", "hi", "haan", "ha", "nahi", "ok", "okay",
                                        "theek", "hm", "hmm", "kya", "kaun", "accha", "achha",
                                        "bye", "thanks", "shukriya", "please", "haanji", "ji"}
            _is_pure_conversational = (
                _short_msg_words <= 2 and
                any(kw in user_msg.lower() for kw in _conversational_keywords)
            )

            if _is_pure_conversational:
                context = ""
                relevant_results = []
                logger.info(f"RAG skipped for short conversational message: '{user_msg}'")
            else:
                query_emb = embed_query(user_msg)
                results = get_vector_store().search_combined(query_emb, agent_id=active_agent_id, datastore_ids=ds_ids, top_k=5)
                relevant_results = [res for res in results if res[1] > 0.35]
                context, _ = build_context_and_sources(relevant_results)
            
            if qa_pairs:
                qa_context_parts = [f"Q: {p.get('q')}\nA: {p.get('a')}" for p in qa_pairs]
                qa_context = "--- CONFIGURED TRAINING Q&A PAIRS ---\n" + "\n\n".join(qa_context_parts) + "\n--- END OF TRAINING Q&A PAIRS ---\n\n"
                context = qa_context + (context or "")
                
            # Build prompts matching active_agent settings
            response_lang = s_cfg.get("response_language", "default")
            if response_lang == "hindi":
                lang_rule = "LANGUAGE RULE: You MUST always respond in Hindi (हिंदी) only, using the Devanagari script (हिंदी लिपि). Regardless of what language the user writes in, you MUST reply in Hindi using Devanagari script. Do NOT use Romanized Hinglish for responses."
                context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate it and respond in Hindi (Devanagari script) only."
                greeting_rule = "GREETING RULE: Always reply to greetings in Hindi (Devanagari script) only."
            elif response_lang == "english":
                lang_rule = "LANGUAGE RULE: You MUST always respond in English only. Regardless of what language the user writes in, you MUST reply in English."
                context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate it and respond in English only."
                greeting_rule = "GREETING RULE: Always reply to greetings in English only."
            elif response_lang == "hinglish":
                lang_rule = "LANGUAGE RULE: You MUST always respond in Hinglish only (Hindi language written using the English/Latin alphabet). Regardless of what language the user writes in, you MUST reply in Hinglish. Do NOT use Hindi Devanagari script."
                context_lang_rule = "CONTEXT LANGUAGE RULE: Regardless of the language of the context information, you MUST translate/transliterate it and respond in Hinglish only."
                greeting_rule = "GREETING RULE: Always reply to greetings in Hinglish only."
            else:
                lang_rule = "LANGUAGE RULE: Respond ONLY in the same language the user uses. If asked in English, reply in English. If asked in Hindi, reply in Hindi using Devanagari script only."
                context_lang_rule = "CONTEXT LANGUAGE RULE: The context files might be in a different language than the user's question. You MUST translate the context information and respond in the same language as the user's question."
                greeting_rule = "GREETING RULE: Reply to greetings in the SAME language the user used."
                
            identity = (
                f"You are {agent_obj.name}. {agent_obj.personality}\n"
                f"{lang_rule}\n"
                f"{context_lang_rule}\n"
                f"{greeting_rule}\n"
                f"CORE INSTRUCTIONS: {s_cfg.get('system_prompt', '')}\n"
                f"RESPONSE STYLE: Be natural, conversational and helpful.\n"
            )
            
            system_prompt = identity
            if context:
                system_prompt = (
                    f"{identity}\n\n"
                    f"--- KNOWLEDGE BASE CONTEXT ---\n"
                    f"{context}\n"
                    f"--- END OF CONTEXT ---\n\n"
                    f"CRITICAL INSTRUCTIONS:\n"
                    f"1. Prioritize answering based on the provided context if it contains the answer.\n"
                    f"2. IMPORTANT: If the context does not contain the answer, or if the user asks a general question unrelated to the context, you MUST use your general AI knowledge to provide a helpful, correct, and complete response. Do NOT say 'information not found in documents' if you can answer it using your general knowledge."
                )

            # If using Groq, execute real SSE stream forwarding
            if provider == "groq" and api_key:
                # Detect if this is a voice call (Dograh sends user-agent with AsyncOpenAI)
                is_voice_call = "AsyncOpenAI" in request.headers.get("user-agent", "")
                # Voice calls need SHORT responses (Dograh). Text chat can be longer.
                voice_max_tokens = 80   # ~25-35 words max for voice
                chat_max_tokens = 1024

                # For voice: append strict no-markdown, short-response instruction
                if is_voice_call:
                    system_prompt += (
                        "\n\n[VOICE MODE] You are speaking on a phone call. Rules:\n"
                        "- Reply in MAXIMUM 1-2 short sentences, 10-25 words only.\n"
                        "- NEVER use markdown: no **, no -, no #, no |, no bullets, no tables.\n"
                        "- Plain conversational text ONLY. No special characters.\n"
                        "- If the answer is long, summarize it in 1 sentence."
                    )

                # Strip markdown from history messages for voice to reduce noise
                import re as _re
                def strip_md(text: str) -> str:
                    if not text:
                        return text
                    text = _re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)  # bold/italic
                    text = _re.sub(r'^#{1,6}\s+', '', text, flags=_re.MULTILINE)  # headings
                    text = _re.sub(r'^[\-\*\+]\s+', '', text, flags=_re.MULTILINE)  # bullets
                    text = _re.sub(r'\|.*?\|', '', text)  # tables
                    text = _re.sub(r'\n{3,}', '\n\n', text)  # extra newlines
                    return text.strip()

                msgs = [{"role": "system", "content": system_prompt}]
                for t in history_override:
                    content = t.get("content", "")
                    if is_voice_call and t.get("role") == "assistant":
                        content = strip_md(content)  # clean up previous markdown responses
                    msgs.append({"role": t.get("role", "user"), "content": content})
                msgs.append({"role": "user", "content": user_msg})

                payload = {
                    "model": model,
                    "messages": msgs,
                    "temperature": 0.3,
                    "max_tokens": voice_max_tokens if is_voice_call else chat_max_tokens,
                    "stream": True
                }
                hdrs = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                # Model fallback list — only models CONFIRMED working on this API key
                # groq/compound is confirmed working. compound-mini returns 404 and wastes 400ms.
                _env_model = os.getenv("GROQ_MODEL", "")
                groq_models_to_try = list(dict.fromkeys(filter(None, [
                    "groq/compound",             # Confirmed working — use directly, no retry waste
                    "openai/gpt-oss-20b",        # Fallback if compound fails
                    "openai/gpt-oss-120b",
                    _env_model,
                    model,
                ])))

                async def groq_stream_generator():
                    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
                    created_time = int(datetime.utcnow().timestamp())
                    full_reply = []

                    # Try each Groq model until one works
                    last_err = None
                    for attempt_model in groq_models_to_try:
                        full_reply = []
                        attempt_payload = {**payload, "model": attempt_model}
                        try:
                            client = get_llm_async_client()
                            model_ok = True
                            async with client.stream("POST", "https://api.groq.com/openai/v1/chat/completions", headers=hdrs, json=attempt_payload) as response:
                                if response.status_code in (404, 400):
                                    last_err = f"Model {attempt_model} returned {response.status_code}"
                                    logger.warning(f"Groq model '{attempt_model}' unavailable ({response.status_code}), trying next...")
                                    model_ok = False
                                else:
                                    response.raise_for_status()
                                    async for line in response.aiter_lines():
                                        if await request.is_disconnected():
                                            logger.info("Client disconnected. Aborting Groq stream.")
                                            return
                                        if line.strip():
                                            yield line + "\n\n"
                                            try:
                                                if line.startswith("data: "):
                                                    data_str = line[6:]
                                                    if data_str.strip() != "[DONE]":
                                                        data = json.loads(data_str)
                                                        delta = data["choices"][0]["delta"]
                                                        if "content" in delta:
                                                            txt = delta["content"]
                                                            full_reply.append(txt)
                                                            if parsed_session_id and parsed_session_id in voice_transcripts:
                                                                voice_transcripts[parsed_session_id]["assistant"] += txt
                                            except Exception:
                                                pass
                            if model_ok:
                                logger.info(f"Groq stream OK (model={attempt_model}) for agent {active_agent_id}")
                                break  # Success — stop trying models
                        except Exception as model_err:
                            last_err = str(model_err)
                            logger.warning(f"Groq model '{attempt_model}' exception: {model_err}, trying next...")
                            continue

                    else:
                        # All Groq models failed — try OpenAI fallback
                        logger.error(f"All Groq models failed for agent {active_agent_id}. Last error: {last_err}. Trying OpenAI fallback.")
                        oai_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
                        if oai_key and oai_key.startswith("sk-"):
                            oai_payload = {**payload, "model": "gpt-4o-mini"}
                            oai_hdrs = {"Authorization": f"Bearer {oai_key}", "Content-Type": "application/json"}
                            try:
                                client = get_llm_async_client()
                                async with client.stream("POST", "https://api.openai.com/v1/chat/completions", headers=oai_hdrs, json=oai_payload) as response:
                                    response.raise_for_status()
                                    async for line in response.aiter_lines():
                                        if await request.is_disconnected():
                                            return
                                        if line.strip():
                                            yield line + "\n\n"
                                            try:
                                                if line.startswith("data: "):
                                                    data_str = line[6:]
                                                    if data_str.strip() != "[DONE]":
                                                        data = json.loads(data_str)
                                                        delta = data["choices"][0]["delta"]
                                                        if "content" in delta:
                                                            txt = delta["content"]
                                                            full_reply.append(txt)
                                                            if parsed_session_id and parsed_session_id in voice_transcripts:
                                                                voice_transcripts[parsed_session_id]["assistant"] += txt
                                            except Exception:
                                                pass
                                logger.info(f"OpenAI GPT-4o-mini fallback stream OK for agent {active_agent_id}")
                            except Exception as oai_err:
                                logger.error(f"OpenAI fallback stream error: {oai_err}")
                                err_text = "I'm sorry, I had trouble processing that. Please try again."
                                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': err_text}, 'finish_reason': None}]})}\n\n"
                                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                                yield "data: [DONE]\n\n"
                                return
                        else:
                            err_text = "I'm sorry, I had trouble processing that. Please try again."
                            yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': err_text}, 'finish_reason': None}]})}\n\n"
                            yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                            yield "data: [DONE]\n\n"
                            return

                    # Stream finished — save to DB
                    try:
                        assistant_text = "".join(full_reply)
                        if parsed_session_id:
                            save_voice_chat_to_db(db, active_agent_id, parsed_session_id, user_msg, assistant_text)
                    except Exception as db_err:
                        logger.warning(f"DB save error (non-fatal): {db_err}")

                return StreamingResponse(groq_stream_generator(), media_type="text/event-stream")
                
            # If using OpenAI, execute streaming directly from OpenAI API
            elif provider == "openai" and api_key:
                # Filter/check placeholder key formatting
                if api_key and (api_key.startswith("sk-") and len(api_key) < 20):
                    api_key = ""
                api_key = api_key or settings.OPENAI_API_KEY
                
                msgs = [{"role": "system", "content": system_prompt}]
                for t in history_override:
                    msgs.append({"role": t.get("role", "user"), "content": t.get("content", "")})
                msgs.append({"role": "user", "content": user_msg})
                
                payload = {
                    "model": model,
                    "messages": msgs,
                    "temperature": 0.1,
                    "max_tokens": 1024,
                    "stream": True
                }
                hdrs = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                async def openai_stream_generator():
                    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
                    created_time = int(datetime.utcnow().timestamp())
                    full_reply = []
                    try:
                        client = get_llm_async_client()
                        async with client.stream("POST", "https://api.openai.com/v1/chat/completions", headers=hdrs, json=payload) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if await request.is_disconnected():
                                    logger.info("Client disconnected. Aborting OpenAI stream.")
                                    break
                                if line.strip():
                                    yield line + "\n\n"
                                    try:
                                        if line.startswith("data: "):
                                            data_str = line[6:]
                                            if data_str.strip() != "[DONE]":
                                                data = json.loads(data_str)
                                                delta = data["choices"][0]["delta"]
                                                if "content" in delta:
                                                    txt = delta["content"]
                                                    full_reply.append(txt)
                                                    if parsed_session_id and parsed_session_id in voice_transcripts:
                                                        voice_transcripts[parsed_session_id]["assistant"] += txt
                                    except Exception:
                                        pass
                    except Exception as oa_err:
                        logger.error(f"OpenAI streaming error for agent {active_agent_id}: {oa_err}")
                        err_text = "I'm sorry, I had trouble processing that. Please try again."
                        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': err_text}, 'finish_reason': None}]})}\n\n"
                        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    
                    try:
                        assistant_text = "".join(full_reply)
                        if parsed_session_id:
                            save_voice_chat_to_db(db, active_agent_id, parsed_session_id, user_msg, assistant_text)
                    except Exception as db_err:
                        logger.warning(f"DB save error (non-fatal): {db_err}")
                        
                return StreamingResponse(openai_stream_generator(), media_type="text/event-stream")
                
            # If using Gemini, execute streaming and translate to OpenAI format
            elif provider == "gemini" and api_key:
                contents = []
                for t in history_override:
                    contents.append({"role": "user" if t.get("role") == "user" else "model",
                                     "parts": [{"text": t.get("content", "")}]})
                contents.append({"role": "user", "parts": [{"text": user_msg}]})
                
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?key={api_key}"
                payload = {
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": contents,
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
                }
                
                async def gemini_stream_generator():
                    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
                    created_time = int(datetime.utcnow().timestamp())
                    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                    
                    full_reply = []
                    try:
                        client = get_llm_async_client()
                        async with client.stream("POST", url, json=payload) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if await request.is_disconnected():
                                    logger.info("Client disconnected. Aborting Gemini stream.")
                                    break
                                if not line.strip():
                                    continue
                                cleaned = line.strip().lstrip(',[').rstrip(',]')
                                if not cleaned:
                                    continue
                                try:
                                    data = json.loads(cleaned)
                                    delta_text = data["candidates"][0]["content"]["parts"][0]["text"]
                                    if delta_text:
                                        full_reply.append(delta_text)
                                        if parsed_session_id and parsed_session_id in voice_transcripts:
                                            voice_transcripts[parsed_session_id]["assistant"] += delta_text
                                        chunk = {
                                            "id": chunk_id, "object": "chat.completion.chunk",
                                            "created": created_time, "model": model,
                                            "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}]
                                        }
                                        yield f"data: {json.dumps(chunk)}\n\n"
                                except Exception:
                                    pass
                    except Exception as gem_err:
                        logger.error(f"Gemini streaming error for agent {active_agent_id}: {gem_err}")
                        # Yield graceful stop so Dograh pipeline doesn't crash
                        err_text = "I'm sorry, I had trouble processing that. Please try again."
                        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': err_text}, 'finish_reason': None}]})}\n\n"

                    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                    
                    # Stream finished, save to DB
                    try:
                        assistant_text = "".join(full_reply)
                        if parsed_session_id:
                            save_voice_chat_to_db(db, active_agent_id, parsed_session_id, user_msg, assistant_text)
                    except Exception as db_err:
                        logger.warning(f"DB save error (non-fatal): {db_err}")
                        
                return StreamingResponse(gemini_stream_generator(), media_type="text/event-stream")

    # Fallback to synchronous query logic if not streaming or other provider
    ask_req = AgentPublicAskReq(
        question=user_msg,
        session_id=f"dograh-session-{active_agent_id[:6]}",
        device_id="dograh-voice-client",
        is_voice=True,
        history_override=history_override
    )
    result = await api_agent_public_ask(agent_id=active_agent_id, req=ask_req, db=db)
    answer = result.get("answer") or result.get("content") or "I could not retrieve an answer."
    
    if parsed_session_id:
        save_voice_chat_to_db(db, active_agent_id, parsed_session_id, user_msg, answer)
        
    if req.stream:
        async def stream_fallback_generator():
            chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
            created_time = int(datetime.utcnow().timestamp())
            yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
            
            # Yield response split by words with a tiny delay to simulate low-latency streaming
            words = answer.split(" ")
            for i, word in enumerate(words):
                space = " " if i > 0 else ""
                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {'content': space + word}, 'finish_reason': None}]})}\n\n"
                await asyncio.sleep(0.02)
                
            # Finish delta
            yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"
            
        return StreamingResponse(stream_fallback_generator(), media_type="text/event-stream")
    else:
        # Non-streaming response format
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(datetime.utcnow().timestamp()),
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": answer
                    },
                    "finish_reason": "stop"
                }
            ]
        }


@router.post("/agents/chat/completions", tags=["Agents & DataStores"])
async def api_agent_openai_compatible_chat_common(
    req: ChatCompletionRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Common OpenAI-compatible completions endpoint for Dograh and 3rd party integrations.
    
    Per-agent routing via Bearer token:
    - Set Base URL: https://vectorize.diintech.com/api/agents  (same for ALL agents)
    - Set API Key:  <your_agent_id>  (e.g. 9817acf6f7c40d3f)
    
    Dograh sends: Authorization: Bearer 9817acf6f7c40d3f
    We extract the agent ID and route to the correct agent automatically.
    """
    # Extract agent ID from Authorization: Bearer <agent_id>
    import re as _re
    import json as _json

    hdr_dict = dict(request.headers)
    print(f"DEBUG COMPLETIONS HEADERS: {_json.dumps(hdr_dict)}", flush=True)
    print(f"DEBUG COMPLETIONS BODY: {req.json()}", flush=True)

    # ROUTING PRIORITY 1: Model field as agent ID
    # Dograh config per agent: Model = d6b54c1e63290e77 (Enter Custom Value)
    # Shared across ALL agents: Base URL + API Key stay the same
    model_val = (req.model or "").strip()
    model_agent_id = None
    if _re.match(r'^[a-f0-9]{8,32}$', model_val, _re.IGNORECASE):
        model_agent_id = model_val
        logger.info(f"Common completions: routing to agent {model_agent_id} via model field")

    # ROUTING PRIORITY 2: Bearer token as agent ID  
    auth_header = request.headers.get("authorization", "")
    bearer_agent_id = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if _re.match(r'^[a-f0-9]{8,32}$', token, _re.IGNORECASE):
            bearer_agent_id = token
            logger.info(f"Common completions: routing to agent {bearer_agent_id} via Bearer token")

    # Use first available routing, else fall back to mock validation response
    routing_agent_id = model_agent_id or bearer_agent_id or "chat"

    return await api_agent_openai_compatible_chat(
        agent_id=routing_agent_id,
        req=req,
        request=request,
        db=db
    )


@router.get("/agents/{agent_id}/models", tags=["Agents & DataStores"])
async def api_agent_openai_compatible_models(agent_id: str):
    return {
        "object": "list",
        "data": [
            {
                "id": "default",
                "object": "model",
                "created": 1686935002,
                "owned_by": "custom"
            },
            {
                "id": "gpt-4.1",
                "object": "model",
                "created": 1686935002,
                "owned_by": "custom"
            }
        ]
    }


@router.get("/agents/models", tags=["Agents & DataStores"])
async def api_agent_openai_compatible_models_common():
    return await api_agent_openai_compatible_models(agent_id="models")


@router.get("/agents/{agent_id}", tags=["Agents & DataStores"])
async def api_agent_openai_base_check(agent_id: str):
    return {"message": "OpenAI-compatible RAG server is running."}


@router.get("/agents/{agent_id}/voice-transcript/{session_id}", tags=["Agents & DataStores"])
async def api_get_voice_transcript(agent_id: str, session_id: str):
    data = voice_transcripts.get(session_id)
    if not data:
        return {"user": "", "assistant": ""}
    return data



