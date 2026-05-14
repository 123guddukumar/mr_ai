"""
MR AI RAG - Website Ingestion Route
POST /ingest-url - Scrape a webpage, chunk, embed, and store in FAISS.
"""

import logging
import re
import uuid
import httpx
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Depends
from app.core.api_keys import require_api_key
from pydantic import BaseModel, HttpUrl
from app.models.schemas import ChunkMetadata
from app.services.chunker import chunk_text
from app.services.embedder import embed_texts
from app.services.vector_store import get_vector_store
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


class IngestURLRequest(BaseModel):
    url: str


class IngestURLResponse(BaseModel):
    success: bool
    url: str
    title: str
    total_chunks: int
    message: str


def extract_text_from_html(html: str) -> tuple[str, str]:
    """
    Extract clean readable text + page title from raw HTML using BeautifulSoup.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # Get title
    title = soup.title.string if soup.title else "Untitled Page"
    title = (title or "").strip()

    # Remove unwanted tags
    for tag in ["script", "style", "noscript", "nav", "footer", "header",
                "aside", "form", "iframe", "svg", "button", "input"]:
        for s in soup.select(tag):
            s.decompose()

    # Get text
    text = soup.get_text(separator="\n")
    
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    return title, text.strip()


@router.post("/ingest-url", response_model=IngestURLResponse, summary="Scrape and index a website URL")
async def ingest_url(req: IngestURLRequest, _key: dict = Depends(require_api_key)):
    """
    Scrape a public webpage and index its content into FAISS.
    - Fetches HTML with browser-like headers
    - Strips all HTML tags cleanly
    - Chunks, embeds and stores with URL as source identifier
    """
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL provided.")

    # ── Fetch HTML ────────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers=HEADERS
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"Request timed out fetching: {url}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Website returned error {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {str(e)}")

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise HTTPException(
            status_code=415,
            detail=f"URL does not return HTML content (got: {content_type})"
        )

    # ── Extract Text ──────────────────────────────────────────────────────────
    html = response.text
    title, text = extract_text_from_html(html)

    if len(text) < 100:
        raise HTTPException(
            status_code=422,
            detail="Page has too little readable text. It may require JavaScript to load."
        )

    # ── Use domain+path as source identifier ──────────────────────────────────
    source_id = f"{parsed.netloc}{parsed.path}".rstrip("/") or parsed.netloc

    # ── Chunk as a single "page 1" document ──────────────────────────────────
    page_texts = [(1, text)]
    chunks = chunk_text(page_texts, source_file=source_id)

    if not chunks:
        raise HTTPException(status_code=422, detail="Could not extract text chunks from page.")

    # ── Embed & Store ─────────────────────────────────────────────────────────
    embeddings = embed_texts([c.text for c in chunks])
    store = get_vector_store()
    store.add_chunks(embeddings, chunks)

    logger.info(f"Indexed {len(chunks)} chunks from URL: {url}")
    return IngestURLResponse(
        success=True,
        url=url,
        title=title,
        total_chunks=len(chunks),
        message=f"Successfully indexed {len(chunks)} chunks from '{title}'"
    )