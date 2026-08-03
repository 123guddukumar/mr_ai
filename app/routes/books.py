"""
MR AI RAG - Books Scraper & List Route
"""

import logging
import httpx
import json
import re
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, HttpUrl
from bs4 import BeautifulSoup

from app.core.database import get_db
from app.core.models import Book
from app.services.llm import generate_answer

logger = logging.getLogger(__name__)
router = APIRouter()

class BookAddRequest(BaseModel):
    url: str

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
    created_at: str

    class Config:
        from_attributes = True

def clean_json_string(s: str) -> str:
    s = s.strip()
    # Remove markdown code blocks if present
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\n", "", s)
        s = re.sub(r"\n```$", "", s)
    s = s.strip()
    return s

@router.get("/books", response_model=List[BookResponse], summary="Get all saved books")
async def get_books(db: Session = Depends(get_db)):
    books = db.query(Book).order_by(Book.created_at.desc()).all()
    return [BookResponse.model_validate(b.to_dict()) for b in books]

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
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, 
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            timeout=30.0
        ) as client:
            resp = await client.get(url_str)
            resp.raise_for_status()
            html_content = resp.text
    except Exception as e:
        logger.error(f"Error fetching URL {url_str}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to fetch webpage: {str(e)}")

    # Step 2: Parse Page with BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Extract OpenGraph Metadata directly
    og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
    og_image = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
    
    title_from_meta = og_title.get("content", "").strip() if og_title else ""
    image_from_meta = og_image.get("content", "").strip() if og_image else ""

    # Clean the HTML body to extract main text content
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.extract()
        
    page_text = soup.get_text(separator="\n", strip=True)
    # Truncate text to fit context limits (around 8000 characters)
    truncated_text = page_text[:8000]

    # Step 3: Use LLM to extract book details (Title, Author, Rating, summary, quote)
    llm_prompt = (
        "You are a helpful assistant specialized in extracting book details from webpage contents.\n"
        "Analyze the provided text from a book summary webpage and extract the following details.\n"
        "Return ONLY a raw JSON object (with no backticks, no markdown formatting, no leading/trailing text) containing exactly these fields:\n"
        "{\n"
        '  "title": "Clean book title (e.g. \'The Alchemist\')",\n'
        '  "author": "Author name (e.g. \'Paulo Coelho\')",\n'
        '  "rating": "Average rating value if mentioned (e.g. \'3.9\' or \'4.5\'). If not mentioned, return \'4.2\' as a reasonable default.",\n'
        '  "rating_count": "Total rating count if mentioned (e.g. \'2.6M\' or \'15k\'). If not mentioned, return \'10k\' as a default.",\n'
        '  "bookmark_quote": "A famous inspirational quote, line, or bookmark motto from this book (preferably the most famous one).",\n'
        '  "summary": "A detailed, engaging, and rich summary of the book. Write it in Hinglish (a natural blend of Hindi and English written in the English alphabet, e.g. \'Santiago ek charwaha hai...\') with 2-3 paragraphs."\n'
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
            "summary": "Failed to generate AI summary. Please check page content."
        }

    # Step 4: Finalize fields
    final_title = extracted_data.get("title") or title_from_meta or "Unknown Book"
    final_author = extracted_data.get("author") or "Unknown Author"
    final_rating = extracted_data.get("rating") or "4.0"
    final_rating_count = extracted_data.get("rating_count") or "1k"
    final_quote = extracted_data.get("bookmark_quote") or "Follow your dreams."
    final_summary = extracted_data.get("summary") or "No summary available."
    
    # Use meta image or fallback to a standard elegant cover placeholder if not found
    final_cover_image = image_from_meta
    if not final_cover_image or "logo" in final_cover_image.lower() or len(final_cover_image) < 10:
        # Standard high-quality fallback book image from Unsplash
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
        url=url_str
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
