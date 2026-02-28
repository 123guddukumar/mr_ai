"""
MR AI RAG - Upload Route v2
POST /upload          → single PDF (backward compat)
POST /upload-batch    → multiple PDFs at once (up to 20)
POST /suggest-prompts → AI-generated suggested questions from indexed sources
"""

import os
import logging
import aiofiles
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from app.core.api_keys import require_api_key
from pydantic import BaseModel

from app.core.config import settings
from app.models.schemas import UploadResponse
from app.services.pdf_parser import extract_text_from_pdf
from app.services.chunker import chunk_text
from app.services.embedder import embed_texts, embed_query
from app.services.vector_store import get_vector_store
from app.services.llm import generate_answer

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FILE_SIZE_MB = 50
MAX_BATCH_FILES  = 20


# ── Pydantic Models ───────────────────────────────────────────────────────────

class BatchUploadResult(BaseModel):
    filename: str
    success: bool
    total_pages: int = 0
    total_chunks: int = 0
    error: str = ""

class BatchUploadResponse(BaseModel):
    total_files: int
    successful: int
    failed: int
    results: List[BatchUploadResult]
    total_chunks_added: int

class SuggestPromptsRequest(BaseModel):
    source_names: List[str] = []
    count: int = 6

class SuggestPromptsResponse(BaseModel):
    prompts: List[str]
    based_on: List[str]


# ── Core indexing helper ──────────────────────────────────────────────────────

async def _index_pdf(content: bytes, filename: str, upload_dir: str) -> BatchUploadResult:
    """Parse, chunk, embed and store one PDF. Returns result."""
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        return BatchUploadResult(filename=filename, success=False,
                                 error=f"Exceeds {MAX_FILE_SIZE_MB}MB limit")
    file_path = os.path.join(upload_dir, filename)
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    try:
        page_texts = extract_text_from_pdf(file_path)
    except Exception as e:
        return BatchUploadResult(filename=filename, success=False, error=str(e))

    if not page_texts:
        return BatchUploadResult(filename=filename, success=False,
                                 error="No readable text — may be a scanned/image PDF")

    chunks = chunk_text(page_texts, source_file=filename)
    if not chunks:
        return BatchUploadResult(filename=filename, success=False,
                                 error="Could not extract text chunks")

    embeddings = embed_texts([c.text for c in chunks])
    get_vector_store().add_chunks(embeddings, chunks)
    logger.info(f"Indexed '{filename}': {len(chunks)} chunks from {len(page_texts)} pages")

    return BatchUploadResult(filename=filename, success=True,
                             total_pages=len(page_texts), total_chunks=len(chunks))


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse, summary="Upload a single PDF")
async def upload_document(file: UploadFile = File(...), _key: dict = Depends(require_api_key)):
    """Single PDF upload — backward compatible."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty.")

    upload_dir = os.path.join(settings.BASE_DIR, settings.UPLOAD_DIR)
    os.makedirs(upload_dir, exist_ok=True)

    res = await _index_pdf(content, file.filename, upload_dir)
    if not res.success:
        raise HTTPException(422, res.error)

    return UploadResponse(
        success=True, filename=res.filename,
        total_pages=res.total_pages, total_chunks=res.total_chunks,
        message=f"Indexed {res.total_chunks} chunks from {res.total_pages} pages."
    )


@router.post("/upload-batch", response_model=BatchUploadResponse,
             summary="Upload multiple PDFs at once (up to 20)")
async def upload_batch(files: List[UploadFile] = File(...), _key: dict = Depends(require_api_key)):
    """
    Upload and index up to 20 PDFs in one request.
    Partial success is allowed — each file is processed independently.
    """
    if not files:
        raise HTTPException(400, "No files provided.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(400, f"Maximum {MAX_BATCH_FILES} files per batch. Got {len(files)}.")

    upload_dir = os.path.join(settings.BASE_DIR, settings.UPLOAD_DIR)
    os.makedirs(upload_dir, exist_ok=True)

    results: List[BatchUploadResult] = []

    for upload in files:
        fname = upload.filename or "unknown.pdf"
        if not fname.lower().endswith(".pdf"):
            results.append(BatchUploadResult(filename=fname, success=False,
                                             error="Not a PDF file"))
            continue
        content = await upload.read()
        if not content:
            results.append(BatchUploadResult(filename=fname, success=False,
                                             error="Empty file"))
            continue
        res = await _index_pdf(content, fname, upload_dir)
        results.append(res)

    ok     = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    return BatchUploadResponse(
        total_files=len(results),
        successful=len(ok),
        failed=len(failed),
        results=results,
        total_chunks_added=sum(r.total_chunks for r in ok)
    )


@router.post("/suggest-prompts", response_model=SuggestPromptsResponse,
             summary="Generate suggested questions from indexed sources")
async def suggest_prompts(req: SuggestPromptsRequest, _key: dict = Depends(require_api_key)):
    """
    Sample chunks from the vector store and ask the active LLM to
    generate relevant questions the user could ask about the content.
    """
    store = get_vector_store()
    if store.total_chunks == 0:
        raise HTTPException(404, "No sources indexed yet — add a PDF, website or video first.")

    # Gather diverse chunks by searching with generic queries
    generic_queries = [
        "main topic overview introduction",
        "key findings conclusions results",
        "important concepts definitions",
        "examples case studies data",
        "summary takeaways recommendations",
    ]
    seen_ids = set()
    all_chunks = []
    for q in generic_queries:
        emb = embed_query(q)
        hits = store.search(emb, top_k=10)
        for chunk, _ in hits:
            if chunk.chunk_id not in seen_ids:
                seen_ids.add(chunk.chunk_id)
                all_chunks.append(chunk)

    # Filter by requested source names if provided
    if req.source_names:
        filtered = [c for c in all_chunks
                    if any(s.lower() in c.source_file.lower() for s in req.source_names)]
        if filtered:
            all_chunks = filtered

    source_names = sorted({c.source_file for c in all_chunks})
    count = max(3, min(req.count, 12))

    # Build context sample (limit to ~6000 chars)
    context_parts = []
    char_budget = 6000
    for chunk in all_chunks:
        snippet = f"[{chunk.source_file}]: {chunk.text[:350]}"
        if char_budget - len(snippet) < 0:
            break
        context_parts.append(snippet)
        char_budget -= len(snippet)

    context_text = "\n\n".join(context_parts)

    prompt = (
        f"You are a helpful assistant. Based on the document content below, "
        f"generate exactly {count} specific, insightful questions a user could ask.\n\n"
        f"Rules:\n"
        f"- Each question must be directly answerable from the content\n"
        f"- Make questions varied: factual, analytical, and comparative\n"
        f"- Keep each question under 15 words\n"
        f"- Return ONLY the questions, one per line\n"
        f"- No numbering, no bullets, no explanations\n\n"
        f"Document content:\n{context_text}\n\n"
        f"Generate {count} questions (one per line, questions only):"
    )

    try:
        raw = await generate_answer("Generate suggested questions", prompt)
    except Exception as e:
        raise HTTPException(502, f"LLM error: {e}")

    # Parse lines into clean questions
    prompts = []
    for line in raw.strip().splitlines():
        q = line.strip().lstrip("•-–—*0123456789.)> \t").strip('"\'').strip()
        if len(q) > 8:
            if not q.endswith("?"):
                q += "?"
            prompts.append(q)

    # Deduplicate
    seen = set()
    unique: List[str] = []
    for p in prompts:
        key = p.lower()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    if not unique:
        unique = [
            "What are the main topics covered in these documents?",
            "What are the key takeaways from this content?",
            "Can you summarize the most important points?",
        ]

    return SuggestPromptsResponse(prompts=unique[:count], based_on=source_names[:10])