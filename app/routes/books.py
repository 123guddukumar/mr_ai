"""
MR AI RAG - Books Scraper & List Route
"""

import logging
import httpx
import json
import re
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from bs4 import BeautifulSoup

from app.core.database import get_db
from app.core.models import Book
from app.services.llm import generate_answer

logger = logging.getLogger(__name__)
router = APIRouter()

class BookAddRequest(BaseModel):
    url: str

class KeyLessonSchema(BaseModel):
    title: str
    description: str

class BookResponse(BaseModel):
    id: int
    title: str
    author: str
    rating: str
    rating_count: str
    cover_image_url: str
    bookmark_quote: str
    summary: str
    url: str
    video_url: str
    read_time: str
    category: str
    target_audience: str
    key_lessons: List[Dict[str, Any]]
    created_at: str

    class Config:
        from_attributes = True

def clean_json_string(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\n", "", s)
        s = re.sub(r"\n```$", "", s)
    s = s.strip()
    return s

def get_embed_url(url: str) -> str:
    if not url:
        return ""
    # Look for YouTube watch or short links and extract ID
    match = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)([a-zA-Z0-9_-]+)", url)
    if match:
        video_id = match.group(1)
        return f"https://www.youtube.com/embed/{video_id}"
    return url

@router.get("/books", response_model=List[BookResponse], summary="Get all saved books")
async def get_books(db: Session = Depends(get_db)):
    books = db.query(Book).order_by(Book.created_at.desc()).all()
    return [BookResponse.model_validate(b.to_dict()) for b in books]

async def fetch_html_content(url: str) -> str:
    url = url.strip()
    
    # Complete Browser Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0"
    }
    
    # Try 1: HTTPX with complete headers
    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=20.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.warning(f"HTTPX fetch returned status {resp.status_code} for {url}. Trying fallback...")
    except Exception as e:
        logger.warning(f"HTTPX fetch failed for {url}: {e}. Trying fallback...")

    # Try 2: If it didn't succeed and didn't have a trailing slash, try with trailing slash
    if not url.endswith("/"):
        alt_url = url + "/"
        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=20.0) as client:
                resp = await client.get(alt_url)
                if resp.status_code == 200:
                    return resp.text
        except Exception as e:
            logger.warning(f"HTTPX alt fetch failed for {alt_url}: {e}")

    # Try 3: Standard urllib fallback (different TLS fingerprint and engine, often bypasses Cloudflare blocks)
    try:
        import urllib.request
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"Urllib fallback failed for original url: {e}")

    # Try 4: Standard urllib fallback with trailing slash
    if not url.endswith("/"):
        alt_url = url + "/"
        try:
            import urllib.request
            req = urllib.request.Request(
                alt_url, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"Urllib fallback failed for alternative url: {e}")

    raise HTTPException(
        status_code=400, 
        detail="Failed to fetch webpage (403 Forbidden). Cloudflare/security blocks requests. Try using a link ending with a '/' or a different book summary URL."
    )

@router.post("/books", response_model=BookResponse, summary="Scrape and add a new book")
async def add_book(req: BookAddRequest, db: Session = Depends(get_db)):
    url_str = req.url.strip()
    if not url_str.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL format. Must start with http:// or https://")

    # Check if book already exists in DB
    existing_book = db.query(Book).filter(Book.url == url_str).first()
    if existing_book:
        return BookResponse.model_validate(existing_book.to_dict())

    # Step 1: Scrape Webpage Content
    logger.info(f"Scraping book URL: {url_str}")
    html_content = await fetch_html_content(url_str)

    # Step 2: Parse Page with BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Extract OpenGraph Metadata directly
    og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
    og_image = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
    
    title_from_meta = og_title.get("content", "").strip() if og_title else ""
    image_from_meta = og_image.get("content", "").strip() if og_image else ""

    # Try to find embedded YouTube video directly in the html
    scraped_video_url = ""
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if "youtube.com" in src or "youtu.be" in src:
            if src.startswith("//"):
                src = "https:" + src
            scraped_video_url = get_embed_url(src)
            break

    if not scraped_video_url:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "youtube.com/watch" in href or "youtu.be/" in href or "youtube.com/embed/" in href:
                scraped_video_url = get_embed_url(href)
                break

    # Clean the HTML body to extract main text content
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.extract()
        
    page_text = soup.get_text(separator="\n", strip=True)
    # Truncate text to fit context limits (around 8000 characters)
    truncated_text = page_text[:8000]

    # Step 3: Use LLM to extract book details
    llm_prompt = (
        "You are a helpful assistant specialized in extracting book details from webpage contents.\n"
        "Analyze the provided text from a book summary webpage and extract the details.\n"
        "Return ONLY a raw JSON object (with no backticks, no markdown formatting, no leading/trailing text) containing exactly these fields:\n"
        "{\n"
        '  "title": "Clean book title (e.g. \'The Alchemist\')",\n'
        '  "author": "Author name (e.g. \'Paulo Coelho\')",\n'
        '  "rating": "Average rating value if mentioned (e.g. \'3.9\' or \'4.5\'). If not mentioned, return \'4.2\'.",\n'
        '  "rating_count": "Total rating count if mentioned (e.g. \'2.6M\' or \'15k\'). If not, return \'10k\'.",\n'
        '  "bookmark_quote": "A famous inspirational quote or bookmark motto from this book.",\n'
        '  "summary": "A detailed, engaging, and rich summary of the book. Write it in Hinglish (a natural blend of Hindi and English written in the English alphabet, e.g. \'Santiago ek charwaha hai...\') with 2-3 paragraphs.",\n'
        '  "read_time": "Reading time if mentioned (e.g. \'4 Min Read\'). If not, return \'5 Min Read\'.",\n'
        '  "category": "One or two category tags representing the genre (e.g. \'Self-Improvement\' or \'Business & Finance\' or \'Fiction\').",\n'
        '  "target_audience": "Brief description of who should read this book or summary (e.g. \'Office workers stuck in corporate jobs, teenagers seeking life goals\').",\n'
        '  "key_lessons": [\n'
        '     {"title": "Lesson 1 Title", "description": "1-2 sentence description of lesson 1"},\n'
        '     {"title": "Lesson 2 Title", "description": "1-2 sentence description of lesson 2"},\n'
        '     {"title": "Lesson 3 Title", "description": "1-2 sentence description of lesson 3"}\n'
        '  ],\n'
        '  "video_url": "If any youtube video URL is mentioned or linked in text, return it. Else leave blank."\n'
        "}\n"
    )

    extracted_data = {}
    try:
        llm_response = await generate_answer(question=llm_prompt, context=truncated_text)
        cleaned_response = clean_json_string(llm_response)
        logger.info(f"LLM Response: {cleaned_response}")
        extracted_data = json.loads(cleaned_response)
    except Exception as e:
        logger.error(f"Failed to parse LLM response for book metadata: {e}")
        # Fallback values if LLM parsing fails
        extracted_data = {
            "title": title_from_meta or "Unknown Book",
            "author": "Unknown Author",
            "rating": "4.0",
            "rating_count": "1k",
            "bookmark_quote": "Follow your dreams.",
            "summary": "Failed to generate AI summary. Please check page content.",
            "read_time": "5 Min Read",
            "category": "General",
            "target_audience": "Anyone interested in learning.",
            "key_lessons": [
                {"title": "Follow your dreams", "description": "Pursue your personal legend despite obstacles."},
                {"title": "Beat your fears", "description": "Fear is the biggest barrier to progress."},
                {"title": "Persevere", "description": "Get up every time you fall on the journey."}
            ],
            "video_url": ""
        }

    # Step 4: Finalize fields
    final_title = extracted_data.get("title") or title_from_meta or "Unknown Book"
    final_author = extracted_data.get("author") or "Unknown Author"
    final_rating = extracted_data.get("rating") or "4.0"
    final_rating_count = extracted_data.get("rating_count") or "1k"
    final_quote = extracted_data.get("bookmark_quote") or "Follow your dreams."
    final_summary = extracted_data.get("summary") or "No summary available."
    final_read_time = extracted_data.get("read_time") or "5 Min Read"
    final_category = extracted_data.get("category") or "General"
    final_target = extracted_data.get("target_audience") or "General readers"
    
    # Handle key lessons formatting
    raw_lessons = extracted_data.get("key_lessons") or []
    final_lessons_str = json.dumps(raw_lessons)

    # Pick the best video url
    final_video = get_embed_url(extracted_data.get("video_url") or scraped_video_url)

    # Use meta image or fallback
    final_cover_image = image_from_meta
    if not final_cover_image or "logo" in final_cover_image.lower() or len(final_cover_image) < 10:
        final_cover_image = "https://images.unsplash.com/photo-1544947950-fa07a98d237f?q=80&w=800&auto=format&fit=crop"

    # Step 5: Save to Database
    db_book = Book(
        title=final_title,
        author=final_author,
        rating=final_rating,
        rating_count=final_rating_count,
        cover_image_url=final_cover_image,
        bookmark_quote=final_quote,
        summary=final_summary,
        url=url_str,
        video_url=final_video,
        read_time=final_read_time,
        category=final_category,
        target_audience=final_target,
        key_lessons=final_lessons_str
    )
    
    db.add(db_book)
    try:
        db.commit()
        db.refresh(db_book)
    except Exception as e:
        db.rollback()
        logger.error(f"Database error saving book: {e}")
        raise HTTPException(status_code=500, detail="Failed to save book to database")

    return BookResponse.model_validate(db_book.to_dict())

@router.delete("/books/{book_id}", summary="Delete a book")
async def delete_book(book_id: int, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    
    db.delete(book)
    db.commit()
    return {"success": True, "message": "Book deleted successfully"}
