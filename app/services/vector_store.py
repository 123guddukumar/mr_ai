"""
MR AI RAG - FAISS Vector Store Service
Manages FAISS index and chunk metadata with persistence.
"""

import faiss
import json
import logging
import numpy as np
import os
from typing import List, Tuple, Optional
from app.core.config import settings
from app.models.schemas import ChunkMetadata

logger = logging.getLogger(__name__)


class VectorStore:
    """
    FAISS-backed vector store with metadata persistence.
    Uses Inner Product (dot product) on normalized vectors = cosine similarity.
    """

    def __init__(self):
        self.index: Optional[faiss.IndexFlatIP] = None
        self.metadata: List[ChunkMetadata] = []
        self._index_path = os.path.join(settings.BASE_DIR, settings.VECTOR_STORE_PATH)
        self._meta_path = os.path.join(settings.BASE_DIR, settings.METADATA_STORE_PATH)
        self._load()

    def _load(self):
        """Load existing FAISS index and metadata from disk if available."""
        os.makedirs(os.path.dirname(self._index_path), exist_ok=True)

        index_file = f"{self._index_path}.index"
        if os.path.exists(index_file) and os.path.exists(self._meta_path):
            try:
                self.index = faiss.read_index(index_file)
                with open(self._meta_path, "r") as f:
                    raw = json.load(f)
                self.metadata = [ChunkMetadata(**item) for item in raw]
                logger.info(f"Loaded FAISS index with {self.index.ntotal} vectors.")
            except Exception as e:
                logger.warning(f"Failed to load existing index, starting fresh: {e}")
                self._init_index()
        else:
            self._init_index()

    def _init_index(self):
        """Initialize a fresh FAISS index."""
        self.index = faiss.IndexFlatIP(settings.EMBEDDING_DIMENSION)
        self.metadata = []
        logger.info("Initialized fresh FAISS index.")

    def save(self):
        """Persist FAISS index and metadata to disk."""
        index_file = f"{self._index_path}.index"
        faiss.write_index(self.index, index_file)
        with open(self._meta_path, "w") as f:
            json.dump([chunk.model_dump() for chunk in self.metadata], f, indent=2, default=str)
        logger.info(f"Saved FAISS index with {self.index.ntotal} vectors.")

    def add_chunks(self, embeddings: np.ndarray, chunks: List[ChunkMetadata]):
        """Add embeddings and their metadata to the store."""
        if embeddings.shape[0] == 0:
            return
        self.index.add(embeddings)
        self.metadata.extend(chunks)
        self.save()
        logger.info(f"Added {len(chunks)} chunks. Total: {self.index.ntotal}")

    def search(
        self, query_embedding: np.ndarray, top_k: int = None
    ) -> List[Tuple[ChunkMetadata, float]]:
        """
        Search for top_k most similar chunks using cosine similarity.
        Returns list of (ChunkMetadata, similarity_score) tuples.
        """
        top_k = top_k or settings.TOP_K_RESULTS

        if self.index.ntotal == 0:
            logger.warning("Vector store is empty.")
            return []

        top_k = min(top_k, self.index.ntotal)
        scores, indices = self.index.search(query_embedding, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            chunk = self.metadata[idx]
            # Cosine similarity is already in [-1, 1]; clip to [0, 1] for display
            similarity = float(np.clip(score, 0, 1))
            results.append((chunk, similarity))

        return results

    @property
    def total_chunks(self) -> int:
        return self.index.ntotal if self.index else 0

    def delete_by_source(self, source_file: str):
        """
        Remove all chunks from a given source file.
        Note: FAISS IndexFlatIP doesn't support deletion natively,
        so we rebuild the index without those chunks.
        """
        keep_meta = [m for m in self.metadata if m.source_file != source_file]
        if len(keep_meta) == len(self.metadata):
            return  # Nothing to delete

        logger.info(f"Removing chunks for: {source_file}")
        # Rebuild index
        self._init_index()
        if keep_meta:
            from app.services.embedder import embed_texts
            texts = [m.text for m in keep_meta]
            embeddings = embed_texts(texts)
            self.index.add(embeddings)
            self.metadata = keep_meta
        self.save()


# Module-level singleton
_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
