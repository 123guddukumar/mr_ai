"""
MR AI RAG - PDF Parsing Service
Extracts text from PDF files using PyMuPDF with page-level metadata.
"""

import fitz  # PyMuPDF
import re
import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> List[Tuple[int, str]]:
    """
    Extract text from each page of a PDF.
    Returns list of (page_number, text) tuples (1-indexed page numbers).
    """
    pages = []
    try:
        doc = fitz.open(file_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            text = _clean_text(text)
            if text.strip():
                pages.append((page_num + 1, text))
        doc.close()
        logger.info(f"Extracted {len(pages)} pages from {file_path}")
    except Exception as e:
        logger.error(f"Error extracting PDF {file_path}: {e}")
        raise RuntimeError(f"Failed to parse PDF: {e}")
    return pages


def _clean_text(text: str) -> str:
    """Clean extracted text: remove excess whitespace, fix line breaks."""
    # Collapse multiple newlines into double newline (paragraph break)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove hyphenated line breaks (common in PDFs)
    text = re.sub(r'-\n([a-z])', r'\1', text)
    # Normalize spaces
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def count_tokens_approx(text: str) -> int:
    """Approximate token count: avg ~4 chars per token."""
    return max(1, len(text) // 4)
