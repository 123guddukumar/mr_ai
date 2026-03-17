"""
MR AI RAG - Personal Memory Routes
POST   /api/memory                             — create memory bot
GET    /api/memory                             — list my memories
GET    /api/memory/{memory_id}                 — get memory detail + sources
DELETE /api/memory/{memory_id}                 — delete memory + all chunks
POST   /api/memory/{memory_id}/upload-pdf      — index PDF into memory
POST   /api/memory/{memory_id}/ingest-url      — scrape URL into memory
POST   /api/memory/{memory_id}/ingest-youtube  — YouTube into memory
POST   /api/memory/{memory_id}/ingest-json     — JSON into memory
POST   /api/memory/{memory_id}/ask             — RAG chat (with history)
GET    /api/memory/{memory_id}/history         — get chat messages
GET    /api/memory/{memory_id}/embed-info      — embed snippet info
"""

import json
import logging
import os
import secrets
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, Request, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_client(x_client_token: Optional[str], db: Session) -> dict:
    if not x_client_token:
        raise HTTPException(401, "Missing X-Client-Token header")
    from app.core.clients import validate_client_token
    client = validate_client_token(x_client_token)
    if not client:
        raise HTTPException(401, "Invalid or expired client token")
    return client


def _get_memory(memory_id: str, client_id: str, db: Session):
    from app.core.models import Memory
    mem = db.query(Memory).filter(
        Memory.memory_id == memory_id,
        Memory.client_id == client_id,
    ).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    return mem


# ── Pydantic models ────────────────────────────────────────────────────────────

class CreateMemoryReq(BaseModel):
    name: str
    description: str = ""
    mrairag_api_key: str = ""
    provider: str = "gemini"
    provider_model: str = "gemini-2.5-flash"
    provider_api_key: str = ""
    ollama_url: str = "http://localhost:11434"

class AskReq(BaseModel):
    question: str
    top_k: int = 5
    history: list = []   # [{role:"user"|"assistant", content:"..."}] last N turns

class IngestUrlReq(BaseModel):
    url: str

class IngestYouTubeReq(BaseModel):
    url: str
    whisper_model: str = "base"

class IngestJsonReq(BaseModel):
    json_data: dict = {}
    json_text: str = ""
    title: str = "JSON Data"

class IngestJsonUrlReq(BaseModel):
    url: str
    title: str = "JSON Data"

class IngestMongoDBReq(BaseModel):
    connection_string: str
    database: str
    collection: str
    title: str = "MongoDB Data"
    filter: dict = {}       # optional pymongo filter, default = all docs
    limit: int = 1000       # max docs to fetch (safety cap)


# ── Chunking helper ────────────────────────────────────────────────────────────

def _make_chunks(text: str, source_name: str, memory_id: str, chunk_size: int = 500):
    from app.models.schemas import ChunkMetadata
    import uuid
    chunks, texts = [], []
    for i in range(0, len(text), chunk_size):
        piece = text[i:i+chunk_size].strip()
        if len(piece) < 20:
            continue
        cm = ChunkMetadata(
            chunk_id=str(uuid.uuid4()), source_file=source_name,
            page_number=1, chunk_index=i // chunk_size,
            text=piece, memory_id=memory_id,
        )
        chunks.append(cm); texts.append(piece)
    return chunks, texts


# ── 1. Create memory ──────────────────────────────────────────────────────────

@router.post("/memory", tags=["Memory"])
async def create_memory(
    req: CreateMemoryReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    from app.core.models import Memory
    memory_id = "mem-" + secrets.token_hex(8)
    mem = Memory(
        memory_id=memory_id, client_id=client["client_id"],
        name=req.name, description=req.description,
        mrairag_api_key=req.mrairag_api_key or None,
        provider=req.provider, provider_model=req.provider_model,
        provider_api_key=req.provider_api_key or None,
        ollama_url=req.ollama_url,
    )
    db.add(mem); db.commit(); db.refresh(mem)
    logger.info(f"Memory created: {memory_id} for {client['client_id']}")
    return {**mem.to_dict(), "message": "Memory bot created!"}


# ── 2. List memories ──────────────────────────────────────────────────────────

@router.get("/memory", tags=["Memory"])
async def list_memories(
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    from app.core.models import Memory
    mems = db.query(Memory).filter(
        Memory.client_id == client["client_id"],
        Memory.is_active == True,
    ).order_by(Memory.created_at.desc()).all()
    return {"memories": [m.to_dict() for m in mems], "total": len(mems)}


# ── 3. Get memory detail ──────────────────────────────────────────────────────

@router.get("/memory/{memory_id}", tags=["Memory"])
async def get_memory(
    memory_id: str,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    mem = _get_memory(memory_id, client["client_id"], db)
    data = mem.to_dict()
    data["sources"] = [s.to_dict() for s in mem.sources]
    return data


# ── 4. Delete memory ──────────────────────────────────────────────────────────

@router.delete("/memory/{memory_id}", tags=["Memory"])
async def delete_memory(
    memory_id: str,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    mem = _get_memory(memory_id, client["client_id"], db)
    from app.services.vector_store import get_vector_store
    try:
        get_vector_store().delete_by_memory(memory_id)
    except Exception as e:
        logger.warning(f"Vector store delete failed (continuing): {e}")
    db.delete(mem); db.commit()
    logger.info(f"Memory deleted: {memory_id}")
    return {"success": True, "message": "Memory deleted"}


# ── 4b. Update memory ─────────────────────────────────────────────────────────

class UpdateMemoryReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    provider: Optional[str] = None
    provider_model: Optional[str] = None
    provider_api_key: Optional[str] = None
    ollama_url: Optional[str] = None

@router.patch("/memory/{memory_id}", tags=["Memory"])
async def update_memory(
    memory_id: str,
    req: UpdateMemoryReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    mem = _get_memory(memory_id, client["client_id"], db)
    if req.name is not None:           mem.name = req.name.strip()
    if req.description is not None:    mem.description = req.description.strip()
    if req.provider is not None:       mem.provider = req.provider
    if req.provider_model is not None: mem.provider_model = req.provider_model
    if req.provider_api_key is not None: mem.provider_api_key = req.provider_api_key
    if req.ollama_url is not None:     mem.ollama_url = req.ollama_url
    db.commit(); db.refresh(mem)
    return {"success": True, "memory": mem.to_dict()}




# ── 5. Upload PDF ─────────────────────────────────────────────────────────────

@router.post("/memory/{memory_id}/upload-pdf", tags=["Memory"])
async def memory_upload_pdf(
    memory_id: str,
    file: UploadFile = File(...),
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    _get_memory(memory_id, client["client_id"], db)

    fname = file.filename or "document.pdf"
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    from app.services.embedder import embed_texts
    from app.services.vector_store import get_vector_store
    from app.models.schemas import ChunkMetadata
    import PyPDF2, io, uuid

    reader = PyPDF2.PdfReader(io.BytesIO(content))
    chunks, texts = [], []
    for page_num, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        for i in range(0, len(text), 500):
            piece = text[i:i+500].strip()
            if len(piece) < 30:
                continue
            cm = ChunkMetadata(
                chunk_id=str(uuid.uuid4()), source_file=fname,
                page_number=page_num+1, chunk_index=i//500,
                text=piece, memory_id=memory_id,
            )
            chunks.append(cm); texts.append(piece)

    if not chunks:
        raise HTTPException(422, "No text could be extracted from PDF")

    embeddings = embed_texts(texts)
    get_vector_store().add_chunks(embeddings, chunks)

    from app.core.models import MemorySource
    db.add(MemorySource(memory_id=memory_id, source_type="pdf", source_name=fname, chunk_count=len(chunks)))
    db.commit()
    return {"success": True, "filename": fname, "total_chunks": len(chunks)}


# ── 6. Ingest URL ─────────────────────────────────────────────────────────────

@router.post("/memory/{memory_id}/ingest-url", tags=["Memory"])
async def memory_ingest_url(
    memory_id: str,
    req: IngestUrlReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    _get_memory(memory_id, client["client_id"], db)

    try:
        import httpx
        from bs4 import BeautifulSoup
        async with httpx.AsyncClient(timeout=30) as hc:
            resp = await hc.get(req.url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script","style","nav","footer","header"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())[:30000]
        title = soup.title.string.strip() if soup.title else req.url
    except Exception as e:
        raise HTTPException(502, f"Failed to scrape URL: {e}")

    if len(text) < 100:
        raise HTTPException(422, "Page has too little text content")

    from app.services.embedder import embed_texts
    from app.services.vector_store import get_vector_store
    chunks, texts = _make_chunks(text, title, memory_id)
    embeddings = embed_texts(texts)
    get_vector_store().add_chunks(embeddings, chunks)

    from app.core.models import MemorySource
    db.add(MemorySource(memory_id=memory_id, source_type="url", source_name=title, chunk_count=len(chunks)))
    db.commit()
    return {"success": True, "title": title, "total_chunks": len(chunks)}


# ── 7. Ingest YouTube ─────────────────────────────────────────────────────────

@router.post("/memory/{memory_id}/ingest-youtube", tags=["Memory"])
async def memory_ingest_youtube(
    memory_id: str,
    req: IngestYouTubeReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    _get_memory(memory_id, client["client_id"], db)

    text = ""
    title = req.url
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import re
        vid_id = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", req.url)
        if vid_id:
            transcript = YouTubeTranscriptApi.get_transcript(vid_id.group(1))
            text = " ".join(e["text"] for e in transcript)
            title = f"YouTube: {vid_id.group(1)}"
    except Exception:
        pass

    if not text:
        raise HTTPException(422, "Could not get transcript.")

    from app.services.embedder import embed_texts
    from app.services.vector_store import get_vector_store
    chunks, texts = _make_chunks(text, title, memory_id)
    embeddings = embed_texts(texts)
    get_vector_store().add_chunks(embeddings, chunks)

    from app.core.models import MemorySource
    db.add(MemorySource(memory_id=memory_id, source_type="youtube", source_name=title, chunk_count=len(chunks)))
    db.commit()
    return {"success": True, "title": title, "total_chunks": len(chunks)}


# ── 8. Ingest JSON ────────────────────────────────────────────────────────────

@router.post("/memory/{memory_id}/ingest-json", tags=["Memory"])
async def memory_ingest_json(
    memory_id: str,
    req: IngestJsonReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    _get_memory(memory_id, client["client_id"], db)

    if req.json_text:
        text = req.json_text[:30000]
    elif req.json_data:
        text = json.dumps(req.json_data, indent=2, ensure_ascii=False)[:30000]
    else:
        raise HTTPException(400, "Provide json_data or json_text")

    if len(text) < 20:
        raise HTTPException(422, "JSON content too short")

    from app.services.embedder import embed_texts
    from app.services.vector_store import get_vector_store
    chunks, texts = _make_chunks(text, req.title, memory_id)
    embeddings = embed_texts(texts)
    get_vector_store().add_chunks(embeddings, chunks)

    from app.core.models import MemorySource
    db.add(MemorySource(memory_id=memory_id, source_type="json", source_name=req.title, chunk_count=len(chunks)))
    db.commit()
    return {"success": True, "title": req.title, "total_chunks": len(chunks)}


# ── 8b. Ingest JSON from URL ─────────────────────────────────────────────────

@router.post("/memory/{memory_id}/ingest-json-url", tags=["Memory"])
async def memory_ingest_json_url(
    memory_id: str,
    req: IngestJsonUrlReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Fetch a JSON file/API from a URL, then index it into the memory."""
    client = _get_client(x_client_token, db)
    _get_memory(memory_id, client["client_id"], db)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as hc:
            resp = await hc.get(
                req.url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )
        resp.raise_for_status()
        try:
            data = resp.json()
            text = json.dumps(data, indent=2, ensure_ascii=False)[:30000]
        except Exception:
            text = resp.text[:30000]
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch JSON URL: {e}")

    if len(text) < 20:
        raise HTTPException(422, "JSON content too short")

    from app.services.embedder import embed_texts
    from app.services.vector_store import get_vector_store
    title = req.title or req.url
    chunks, texts = _make_chunks(text, title, memory_id)
    embeddings = embed_texts(texts)
    get_vector_store().add_chunks(embeddings, chunks)

    from app.core.models import MemorySource
    db.add(MemorySource(memory_id=memory_id, source_type="json", source_name=title, chunk_count=len(chunks)))
    db.commit()
    return {"success": True, "title": title, "total_chunks": len(chunks)}


# ── 8c. Ingest MongoDB Collection ─────────────────────────────────────────────

@router.post("/memory/{memory_id}/ingest-mongodb", tags=["Memory"])
async def memory_ingest_mongodb(
    memory_id: str,
    req: IngestMongoDBReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Connect to a MongoDB collection, fetch all documents, and RAG-index them."""
    client = _get_client(x_client_token, db)
    mem = _get_memory(memory_id, client["client_id"], db)

    try:
        from pymongo import MongoClient as PyMongoClient
        mc = PyMongoClient(req.connection_string, serverSelectionTimeoutMS=10000)
        col = mc[req.database][req.collection]
        cursor = col.find(req.filter or {}).limit(req.limit)
        docs = list(cursor)
        mc.close()
    except Exception as e:
        raise HTTPException(502, f"MongoDB connection failed: {e}")

    if not docs:
        raise HTTPException(422, "MongoDB collection returned no documents")

    # Serialize docs to text (remove _id for clean serialization)
    for d in docs:
        d.pop("_id", None)
    text = json.dumps(docs, indent=2, ensure_ascii=False, default=str)[:60000]

    from app.services.embedder import embed_texts
    from app.services.vector_store import get_vector_store
    title = req.title or f"{req.database}/{req.collection}"
    chunks, texts = _make_chunks(text, title, memory_id)
    embeddings = embed_texts(texts)
    get_vector_store().add_chunks(embeddings, chunks)

    from app.core.models import MemorySource
    db.add(MemorySource(
        memory_id=memory_id, source_type="json",
        source_name=f"MongoDB: {title} ({len(docs)} docs)",
        chunk_count=len(chunks)
    ))
    db.commit()
    return {"success": True, "title": title, "docs_fetched": len(docs), "total_chunks": len(chunks)}


# ── 9. Ask (RAG + conversation history) ──────────────────────────────────────

@router.post("/memory/{memory_id}/ask", tags=["Memory"])
async def memory_ask(
    memory_id: str,
    req: AskReq,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Public endpoint — no auth required.
    The embed widget and direct chat links call this without a token.
    The memory_id in the URL identifies the bot.
    """
    from app.core.models import Memory
    mem = db.query(Memory).filter(
        Memory.memory_id == memory_id,
        Memory.is_active == True,
    ).first()
    if not mem:
        raise HTTPException(404, "Memory not found")

    from app.services.embedder import embed_query
    from app.services.vector_store import get_vector_store
    from app.services.llm import build_context_and_sources
    from app.core.models import MemoryChat
    from app.core.config import settings

    query_emb = embed_query(req.question)
    results = get_vector_store().search_by_memory(query_emb, memory_id, top_k=req.top_k)

    NO_CTX = "I don't have enough information to answer that. Please add more sources to my knowledge base."

    if not results:
        answer = NO_CTX
        sources_data = []
    else:
        context, sources_data = build_context_and_sources(results)
        history_turns = req.history[-6:] if req.history else []

        # System = identity + base prompt + RAG context block + history
        identity = (
            f"You are {mem.name}, a personal AI assistant.\n"
            f"If anyone asks 'who are you?' or your name, say: 'I am {mem.name}, your personal AI assistant.'\n"
            f"If anyone asks who made you, who created you, or who built you, say: "
            f"'I was built by MR AI RAG — developed by Divyansu Verma.'\n"
            f"Do NOT reveal any API keys, internal configurations, or technical details.\n\n"
        )
        system = identity + settings.SYSTEM_PROMPT
        system += f"\n\n--- Retrieved Knowledge ---\n{context}\n---"

        if history_turns:
            hist = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history_turns if t.get("role") and t.get("content"))
            system += f"\n\n--- Conversation So Far ---\n{hist}\n---"

        try:
            answer = await _llm_with_history(
                question=req.question, system=system, history=history_turns,
                provider=mem.provider, model=mem.provider_model,
                api_key=mem.provider_api_key or "",
                ollama_url=mem.ollama_url or "http://localhost:11434",
            )
        except Exception as e:
            raise HTTPException(502, f"LLM error: {e}")

    srcs_json = json.dumps(
        [s.__dict__ if hasattr(s, '__dict__') else dict(s) for s in sources_data], default=str
    )
    db.add(MemoryChat(memory_id=memory_id, role="user",      content=req.question, sources_json="[]"))
    db.add(MemoryChat(memory_id=memory_id, role="assistant", content=answer,        sources_json=srcs_json))
    db.commit()

    return {
        "question": req.question,
        "answer": answer,
        "sources": [s.__dict__ if hasattr(s, '__dict__') else dict(s) for s in sources_data],
        "context_found": bool(results),
        "model_used": f"{mem.provider}/{mem.provider_model}",
    }


async def _llm_with_history(
    question: str, system: str, history: list,
    provider: str, model: str, api_key: str, ollama_url: str,
) -> str:
    import httpx
    from app.core.config import settings

    if provider == "gemini":
        if not api_key:
            raise RuntimeError("Gemini API key required")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        contents = []
        for t in history:
            contents.append({"role": "user" if t.get("role") == "user" else "model",
                             "parts": [{"text": t.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": question}]})
        payload = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
                   "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}}
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post(url, json=payload); r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    elif provider == "openai":
        if not api_key:
            raise RuntimeError("OpenAI API key required")
        from openai import AsyncOpenAI
        msgs = [{"role": "system", "content": system}]
        for t in history:
            msgs.append({"role": t.get("role", "user"), "content": t.get("content", "")})
        msgs.append({"role": "user", "content": question})
        cl = AsyncOpenAI(api_key=api_key)
        resp = await cl.chat.completions.create(model=model, messages=msgs, max_tokens=1024, temperature=0.1)
        return resp.choices[0].message.content.strip()

    elif provider == "claude":
        if not api_key:
            raise RuntimeError("Anthropic API key required")
        hdrs = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        msgs = []
        for t in history:
            msgs.append({"role": t.get("role", "user"), "content": t.get("content", "")})
        msgs.append({"role": "user", "content": question})
        payload = {"model": model, "max_tokens": 1024, "system": system, "messages": msgs}
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post("https://api.anthropic.com/v1/messages", headers=hdrs, json=payload)
            r.raise_for_status()
            return r.json()["content"][0]["text"].strip()

    elif provider == "ollama":
        msgs = [{"role": "system", "content": system}]
        for t in history:
            msgs.append({"role": t.get("role", "user"), "content": t.get("content", "")})
        msgs.append({"role": "user", "content": question})
        async with httpx.AsyncClient(timeout=120.0) as hc:
            r = await hc.post(f"{ollama_url}/api/chat", json={"model": model, "stream": False, "messages": msgs})
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

    elif provider == "huggingface":
        if not api_key:
            raise RuntimeError("HuggingFace API key required")
        full_prompt = f"<s>[INST] {system}\n\nUser: {question} [/INST]"
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post(
                f"https://api-inference.huggingface.co/models/{model}",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"inputs": full_prompt, "parameters": {"max_new_tokens": 512, "temperature": 0.1, "return_full_text": False}},
            ); r.raise_for_status()
            return r.json()[0]["generated_text"].strip()
    else:
        raise ValueError(f"Unsupported provider: {provider}")


# ── 10. Get chat history ──────────────────────────────────────────────────────

@router.get("/memory/{memory_id}/history", tags=["Memory"])
async def memory_history(
    memory_id: str,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    client = _get_client(x_client_token, db)
    _get_memory(memory_id, client["client_id"], db)
    from app.core.models import MemoryChat
    msgs = db.query(MemoryChat).filter(
        MemoryChat.memory_id == memory_id
    ).order_by(MemoryChat.timestamp.asc()).all()
    return {"history": [m.to_dict() for m in msgs], "total": len(msgs)}


# ── 11. Embed info (auth optional — public for embed widget) ──────────────────

@router.get("/memory/{memory_id}/embed-info", tags=["Memory"])
async def memory_embed_info(
    memory_id: str,
    x_client_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    from app.core.models import Memory
    mem = db.query(Memory).filter(Memory.memory_id == memory_id).first()
    if not mem:
        raise HTTPException(404, "Memory not found")

    if x_client_token:
        client = _get_client(x_client_token, db)
        if mem.client_id != client["client_id"]:
            raise HTTPException(403, "Not your memory")

    base_url = os.getenv("BASE_URL", "https://test.3rdai.co")
    # direct=1 skips login so it goes straight to chat
    chat_url  = f"{base_url}/memory-chat-public?id={memory_id}&direct=1"
    embed_url = f"{base_url}/embed/{memory_id}"
    iframe_code = (
        f'<iframe src="{chat_url}" width="400" height="600" frameborder="0" '
        f'style="border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.3);" '
        f'allow="microphone clipboard-write"></iframe>'
    )

    return {
        "memory_id": memory_id,
        "name": mem.name,
        "embed_url": embed_url,
        "chat_url": chat_url,
        "iframe_code": iframe_code,
        "source_count": len(mem.sources),
    }


# ── Visitor Tracking ───────────────────────────────────────────────────────────
import json as _json

VISITS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "visits")


@router.post("/memory/{memory_id}/log-visit", tags=["Memory"])
async def log_visit(memory_id: str, request: Request):
    """Public endpoint — records a visitor from the QR public chat page."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    os.makedirs(VISITS_DIR, exist_ok=True)
    entry = {"name": str(body.get("name", "Anonymous"))[:120], "timestamp": datetime.utcnow().isoformat()}
    visit_file = os.path.join(VISITS_DIR, f"{memory_id}.jsonl")
    with open(visit_file, "a", encoding="utf-8") as f:
        f.write(_json.dumps(entry) + "\n")
    return {"success": True}


@router.get("/memory/{memory_id}/visits", tags=["Memory"])
async def get_visits(memory_id: str, x_client_token: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Authenticated — returns visitor log (most-recent 100) for a memory bot."""
    client = _get_client(x_client_token, db)
    _get_memory(memory_id, client["client_id"], db)
    visit_file = os.path.join(VISITS_DIR, f"{memory_id}.jsonl")
    visits: list = []
    if os.path.exists(visit_file):
        with open(visit_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        visits.append(_json.loads(line))
                    except Exception:
                        pass
    visits = sorted(visits, key=lambda x: x.get("timestamp", ""), reverse=True)[:100]
    return {"visits": visits, "total": len(visits)}
