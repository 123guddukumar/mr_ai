import json
import logging
import secrets
import os
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Request, Response
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

@router.post("/agents", tags=["Agents & DataStores"])
async def api_create_agent(req: CreateAgentReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    # Extract all fields for creation
    params = req.dict()
    # Map complex fields to JSON strings
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
    return agent.to_dict()

@router.get("/agents", tags=["Agents & DataStores"])
async def api_list_agents(x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    agents = get_agents(client["client_id"], db)
    return [a.to_dict() for a in agents]

@router.patch("/agents/{agent_id}", tags=["Agents & DataStores"])
async def api_update_agent(agent_id: str, req: UpdateAgentReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client = _get_client(x_app_token, db)
    updates = req.dict(exclude_unset=True)
    
    # Convert dict fields to JSON strings for core logic
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
    d["sources"] = [
        {"id": s.id, "source_type": s.source_type, "source_name": s.source_name, "chunk_count": s.chunk_count}
        for s in srcs
    ]
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
                
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script","style","nav","footer","header"]): tag.decompose()
                
                page_text = " ".join(soup.get_text(" ", strip=True).split())
                all_text += f"\n--- Source: {curr_url} ---\n{page_text}\n"
                
                if depth < max_depth:
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
    
    chunks, texts = _make_agent_chunks(text, fname, agent_id, is_ds=False)
    if chunks:
        from app.services.embedder import embed_texts
        from app.services.vector_store import get_vector_store
        embeddings = embed_texts(texts)
        get_vector_store().add_chunks(embeddings, chunks)
        db.add(AgentKnowledgeSource(agent_id=agent_id, source_type="pdf", source_name=fname, chunk_count=len(chunks)))
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
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script","style","nav","footer","header"]): tag.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())[:30000]
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

class AgentAskReq(BaseModel):
    question: str
    history: list = []

@router.post("/agents/{agent_id}/ask", tags=["Agents & DataStores"])
async def agent_ask(agent_id: str, req: AgentAskReq, db: Session = Depends(get_db)):
    from app.core.models import Agent, DataStore
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.is_active == True).first()
    if not agent: raise HTTPException(404, "Agent not found")

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
    for pair in qa_pairs:
        pair_q = clean_match_string(pair.get("q", ""))
        if pair_q and (pair_q == q_clean or pair_q in q_clean or q_clean in pair_q):
            return {
                "answer": pair.get("a"),
                "sources": [{"source_file": "Training Q&A Pairs", "page_number": 1}],
                "is_rag": True
            }

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
    
    identity = (
        f"You are {agent.name}. {agent.personality}\n"
        f"LANGUAGE RULE: Respond ONLY in the same language the user uses. If asked in English, reply in English. If asked in Hindi, reply in Hindi. Do not translate unless asked.\n"
        f"GREETING RULE: Reply to greetings (Hi, Hello, Namaste) in the SAME language the user used. Example: If user says 'Namaste', you say 'Namaste, main {agent.name} hoon...'. If user says 'Hi', you say 'Hi, I am {agent.name}...'.\n"
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

    # Call LLM
    from app.services.llm import llm_with_history
    try:
        answer = await llm_with_history(
            question=req.question, system=system, history=req.history[-6:],
            provider=s_cfg.get('provider', 'gemini'),
            model=s_cfg.get('model', 'gemini-3.5-flash'),
            api_key=s_cfg.get('api_key', ''),
            ollama_url="http://localhost:11434",
        )
    except Exception as e:
        raise HTTPException(502, f"LLM error: {e}")

    return {
        "answer": answer,
        "sources": [s.__dict__ if hasattr(s, '__dict__') else dict(s) for s in sources_data],
        "is_rag": bool(context)
    }


class TestVoiceReq(BaseModel):
    provider: str
    voice_id: str
    api_key: str
    text: str

@router.post("/agents/test-voice", tags=["Agents & DataStores"])
async def api_test_voice(req: TestVoiceReq):
    import httpx
    if req.provider == "elevenlabs":
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{req.voice_id}"
        headers = {"xi-api-key": req.api_key, "Content-Type": "application/json"}
        payload = {"text": req.text, "model_id": "eleven_monolingual_v1"}
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if not r.is_success: raise HTTPException(r.status_code, f"ElevenLabs error: {r.text}")
            return Response(content=r.content, media_type="audio/mpeg")
    elif req.provider == "sarvam":
        url = "https://api.sarvam.ai/text-to-speech"
        headers = {"api-subscription-key": req.api_key, "Content-Type": "application/json"}
        payload = {"inputs": [req.text], "target_language_code": "hi-IN", "speaker": req.voice_id}
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if not r.is_success: raise HTTPException(r.status_code, f"Sarvam error: {r.text}")
            import base64
            audio_base64 = r.json()["audios"][0]
            return Response(content=base64.b64decode(audio_base64), media_type="audio/wav")
        raise HTTPException(400, "Unsupported provider for server-side test")


class AgentPublicAskReq(BaseModel):
    question: str
    session_id: str
    device_id: str
    device_name: Optional[str] = "Unknown Device"


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
        "voice_config": v_cfg
    }


@router.post("/agents/{agent_id}/public-ask", tags=["Agents & DataStores"])
async def api_agent_public_ask(agent_id: str, req: AgentPublicAskReq, db: Session = Depends(get_db)):
    from app.core.models import Agent, AgentPublicSession, AgentPublicMessage
    agent = db.query(Agent).filter(Agent.agent_id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(404, "Agent not found")

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
    matched_a = None
    for pair in qa_pairs:
        pair_q = clean_match_string(pair.get("q", ""))
        if pair_q and (pair_q == q_clean or pair_q in q_clean or q_clean in pair_q):
            matched_a = pair.get("a")
            break

    if matched_a:
        # User logging
        session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == req.session_id).first()
        if not session:
            session = AgentPublicSession(
                session_id=req.session_id,
                agent_id=agent_id,
                device_id=req.device_id,
                device_name=req.device_name
            )
            db.add(session)
            db.commit()
            db.refresh(session)

        user_msg = AgentPublicMessage(
            session_id=req.session_id,
            role="user",
            content=req.question
        )
        db.add(user_msg)
        
        bot_msg = AgentPublicMessage(
            session_id=req.session_id,
            role="assistant",
            content=matched_a
        )
        db.add(bot_msg)
        db.commit()

        return {
            "answer": matched_a,
            "sources": [{"source_file": "Training Q&A Pairs", "page_number": 1}],
            "is_rag": True
        }

    # Standard chat RAG logic & Lead Capture logic below
    session = db.query(AgentPublicSession).filter(AgentPublicSession.session_id == req.session_id).first()
    if not session:
        session = AgentPublicSession(
            session_id=req.session_id,
            agent_id=agent_id,
            device_id=req.device_id,
            device_name=req.device_name
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    user_msg = AgentPublicMessage(
        session_id=req.session_id,
        role="user",
        content=req.question
    )
    db.add(user_msg)
    db.commit()

    user_msg_count = db.query(AgentPublicMessage).filter(
        AgentPublicMessage.session_id == req.session_id,
        AgentPublicMessage.role == "user"
    ).count()

    answer = ""
    # Lead Capture state machine
    if not session.user_name and user_msg_count > 3:
        name_captured = req.question.strip()
        session.user_name = name_captured
        db.commit()
        answer = f"Nice to meet you, {name_captured}! Could you also share your mobile number?"
    elif session.user_name and not session.phone_number:
        phone_captured = req.question.strip()
        session.phone_number = phone_captured
        db.commit()
        answer = "Thank you! I have saved your details. How else can I help you today?"
    else:
        # Standard chat RAG logic
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
        context, sources_data = build_context_and_sources(relevant_results)

        # Add Q&A pairs to context as high-priority training context
        if qa_pairs:
            qa_context_parts = [f"Q: {p.get('q')}\nA: {p.get('a')}" for p in qa_pairs]
            qa_context = "--- CONFIGURED TRAINING Q&A PAIRS ---\n" + "\n\n".join(qa_context_parts) + "\n--- END OF TRAINING Q&A PAIRS ---\n\n"
            context = qa_context + (context or "")

        try: s_cfg = json.loads(agent.system_config_json or "{}")
        except: s_cfg = {}

        identity = (
            f"You are {agent.name}. {agent.personality}\n"
            f"LANGUAGE RULE: Respond ONLY in the same language the user uses. If asked in English, reply in English. If asked in Hindi, reply in Hindi. Do not translate unless asked.\n"
            f"GREETING RULE: Reply to greetings (Hi, Hello, Namaste) in the SAME language the user used.\n"
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

        try:
            answer = await llm_with_history(
                question=req.question, system=system, history=history_list,
                provider=s_cfg.get('provider', 'gemini'),
                model=s_cfg.get('model', 'gemini-3.5-flash'),
                api_key=s_cfg.get('api_key', ''),
                ollama_url="http://localhost:11434",
            )
            # Prompt for name after 2 turns (on 3rd user message submission)
            if not session.user_name and user_msg_count == 3:
                answer += "\n\nBy the way, what is your name?"
        except Exception as e:
            answer = f"Error generating response: {e}"

    asst_msg = AgentPublicMessage(
        session_id=req.session_id,
        role="assistant",
        content=answer
    )
    db.add(asst_msg)
    db.commit()

    return {
        "answer": answer,
        "is_rag": bool(context) if 'context' in locals() else False
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

