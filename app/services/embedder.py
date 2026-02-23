"""
MR AI RAG - Embedding Service
Generates embeddings using sentence-transformers (local, no API key required).
"""

import logging
import numpy as np
from typing import List
from sentence_transformers import SentenceTransformer
from app.core.config import settings

logger = logging.getLogger(__name__)

# Singleton model instance
_model: SentenceTransformer = None


def get_embedding_model() -> SentenceTransformer:
    """Lazy-load embedding model singleton."""
    global _model
    if _model is None:
        logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
        _model = SentenceTransformer(settings.EMBEDDING_MODEL)
        logger.info("Embedding model loaded successfully.")
    return _model


def embed_texts(texts: List[str]) -> np.ndarray:
    """
    Generate normalized embeddings for a list of texts.
    Returns numpy array of shape (len(texts), EMBEDDING_DIMENSION).
    Normalized for cosine similarity via dot product.
    """
    if not texts:
        return np.array([])
    model = get_embedding_model()
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    # L2 normalize for cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)  # avoid division by zero
    normalized = embeddings / norms
    return normalized.astype(np.float32)


def embed_query(query: str) -> np.ndarray:
    """Embed a single query string. Returns shape (1, EMBEDDING_DIMENSION)."""
    return embed_texts([query])
