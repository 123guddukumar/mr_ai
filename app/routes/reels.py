"""
MR AI RAG - Reels / Video Generation Routes
POST /api/memory/{memory_id}/generate-reel  → Generate a video from memory sources (placeholder)
GET  /api/memory/{memory_id}/sources-text   → Get concatenated source text for the memory
GET  /api/memory/{memory_id}/chat-history   → Get Q&A chat history for reel selection
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.clients import validate_client_token

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Auth dependency ───────────────────────────────────────────────────────────

def _require_client(
    x_client_token: Optional[str] = Header(None, alias="X-Client-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not x_client_token:
        raise HTTPException(401, "Missing X-Client-Token header.")
    record = validate_client_token(x_client_token, db=db)
    if not record:
        raise HTTPException(401, "Invalid or expired client token.")
    return record


# ── Schemas ───────────────────────────────────────────────────────────────────

class GenerateReelRequest(BaseModel):
    topic: Optional[str] = Field(default="", max_length=500)
    style: Optional[str] = Field(default="cinematic", max_length=100)
    content_text: Optional[str] = Field(default="", max_length=5000)  # Specific answer text


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/memory/{memory_id}/chat-history", tags=["Reels"])
async def get_chat_history(
    memory_id: str,
    limit: int = 10,
    offset: int = 0,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Return Q&A pairs from memory chat history, for use in reel generation."""
    from app.core.models import Memory, MemoryChat

    mem = db.query(Memory).filter(
        Memory.memory_id == memory_id,
        Memory.client_id == client["client_id"],
    ).first()
    if not mem:
        raise HTTPException(404, f"Memory '{memory_id}' not found.")

    # Fetch all messages from memory_chats table (not global chat_history)
    all_msgs = db.query(MemoryChat).filter(
        MemoryChat.memory_id == memory_id,
    ).order_by(MemoryChat.timestamp.asc()).all()

    # Pair into Q&A: user message → next assistant message
    qa_pairs = []
    i = 0
    while i < len(all_msgs):
        msg = all_msgs[i]
        if msg.role == "user":
            question = msg.content
            answer = ""
            answer_timestamp = ""
            if i + 1 < len(all_msgs) and all_msgs[i + 1].role == "assistant":
                answer = all_msgs[i + 1].content
                answer_timestamp = all_msgs[i + 1].timestamp.isoformat() if all_msgs[i + 1].timestamp else ""
                i += 2
            else:
                i += 1
            qa_pairs.append({
                "question": question,
                "answer": answer,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else "",
                "answer_timestamp": answer_timestamp,
            })
        else:
            i += 1

    total = len(qa_pairs)
    # Slice: return last `limit` pairs by default (most recent)
    page_pairs = qa_pairs[max(0, total - offset - limit): total - offset] if offset == 0 else qa_pairs[offset: offset + limit]

    return {
        "memory_id": memory_id,
        "memory_name": mem.name,
        "total": total,
        "offset": offset,
        "limit": limit,
        "qa_pairs": list(reversed(page_pairs)),  # newest first for display
    }


@router.get("/memory/{memory_id}/sources-text", tags=["Reels"])
async def get_sources_text(
    memory_id: str,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Return concatenated text content from all sources in the memory."""
    from app.core.models import Memory, MemorySource

    mem = db.query(Memory).filter(
        Memory.memory_id == memory_id,
        Memory.client_id == client["client_id"],
    ).first()
    if not mem:
        raise HTTPException(404, f"Memory '{memory_id}' not found.")

    sources = db.query(MemorySource).filter(MemorySource.memory_id == memory_id).all()
    sources_list = [s.to_dict() for s in sources]

    text_parts = []
    for s in sources:
        text_parts.append(f"📄 Source: {s.source_name} ({s.source_type}) — {s.chunk_count} chunks")

    combined_text = "\n\n".join(text_parts) if text_parts else f"Memory '{mem.name}' has no sources yet."

    return {
        "memory_id": memory_id,
        "memory_name": mem.name,
        "source_count": len(sources),
        "sources": sources_list,
        "combined_text": combined_text,
    }


@router.post("/memory/{memory_id}/generate-reel", tags=["Reels"])
async def generate_reel(
    memory_id: str,
    req: GenerateReelRequest,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """
    Generate a short video reel from memory content.
    Placeholder — integrate D-ID, HeyGen, Runway ML, or Pictory for real generation.
    """
    from app.core.models import Memory, MemorySource

    mem = db.query(Memory).filter(
        Memory.memory_id == memory_id,
        Memory.client_id == client["client_id"],
    ).first()
    if not mem:
        raise HTTPException(404, f"Memory '{memory_id}' not found.")

    sources = db.query(MemorySource).filter(MemorySource.memory_id == memory_id).all()
    topic = req.topic or mem.name
    content_text = req.content_text or topic

    # ── PLACEHOLDER: Replace this block with real AI video generation ──────────
    placeholder_videos = [
        "https://www.w3schools.com/html/mov_bbb.mp4",
        "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
        "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
    ]
    import hashlib
    idx = int(hashlib.md5(memory_id.encode()).hexdigest(), 16) % len(placeholder_videos)
    video_url = placeholder_videos[idx]
    # ──────────────────────────────────────────────────────────────────────────

    logger.info(f"Reel generated for memory {memory_id} | content length: {len(content_text)}")

    return {
        "success": True,
        "memory_id": memory_id,
        "memory_name": mem.name,
        "topic": topic,
        "style": req.style,
        "content_text": content_text[:200] + "..." if len(content_text) > 200 else content_text,
        "video_url": video_url,
        "source_count": len(sources),
        "note": "Placeholder video. Integrate D-ID, HeyGen, or Runway ML for real AI video generation.",
    }
