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
        """
        keep_meta = [m for m in self.metadata if m.source_file != source_file]
        if len(keep_meta) == len(self.metadata):
            return  # Nothing to delete

        logger.info(f"Removing chunks for: {source_file}")
        
        keep_indices = [i for i, m in enumerate(self.metadata) if m.source_file != source_file]
        keep_embeddings = None
        if keep_indices:
            keep_embeddings = np.vstack([self.index.reconstruct(i) for i in keep_indices])
            
        self._init_index()
        if keep_indices and keep_embeddings is not None:
            self.index.add(keep_embeddings)
            self.metadata = keep_meta
        else:
            self.metadata = []
        self.save()

    def search_by_memory(
        self, query_embedding: np.ndarray, memory_id: str, top_k: int = 5
    ) -> List[Tuple[ChunkMetadata, float]]:
        """
        Search for top_k chunks filtered to a specific memory_id.
        Strategy: over-fetch from FAISS then filter by metadata.
        """
        if self.index.ntotal == 0:
            return []

        # Count chunks belonging to this memory to set a sensible fetch size
        memory_chunks = [m for m in self.metadata if m.memory_id == memory_id]
        if not memory_chunks:
            return []

        # Over-fetch: ask for up to 4x more than top_k to account for filtering
        fetch_k = min(max(top_k * 4, 20), self.index.ntotal)
        scores, indices = self.index.search(query_embedding, fetch_k)

        results = []
        MIN_SCORE = 0.0
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            chunk = self.metadata[idx]
            if chunk.memory_id != memory_id:
                continue
            similarity = float(np.clip(score, 0, 1))
            if similarity >= MIN_SCORE:
                results.append((chunk, similarity))
            if len(results) >= top_k:
                break

        return results

    def delete_by_memory(self, memory_id: str):
        """Remove all chunks belonging to a given memory_id."""
        keep_meta = [m for m in self.metadata if m.memory_id != memory_id]
        if len(keep_meta) == len(self.metadata):
            return

        logger.info(f"Removing all chunks for memory: {memory_id}")
        self._init_index()
        if keep_meta:
            from app.services.embedder import embed_texts
            texts = [m.text for m in keep_meta]
            embeddings = embed_texts(texts)
            self.index.add(embeddings)
            self.metadata = keep_meta
        self.save()

    def search_by_paper(
        self, query_embedding: np.ndarray, paper_id: str, top_k: int = 5
    ) -> List[Tuple[ChunkMetadata, float]]:
        """Search for top_k chunks filtered to a specific paper_id."""
        if self.index.ntotal == 0:
            return []

        paper_chunks = [m for m in self.metadata if m.paper_id == paper_id]
        if not paper_chunks:
            return []

        fetch_k = min(max(top_k * 4, 20), self.index.ntotal)
        scores, indices = self.index.search(query_embedding, fetch_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            chunk = self.metadata[idx]
            if chunk.paper_id != paper_id:
                continue
            similarity = float(np.clip(score, 0, 1))
            results.append((chunk, similarity))
            if len(results) >= top_k:
                break

        return results

    def delete_by_paper(self, paper_id: str):
        """Remove all chunks belonging to a given paper_id."""
        keep_meta = [m for m in self.metadata if getattr(m, "paper_id", None) != paper_id]
        if len(keep_meta) == len(self.metadata):
            return

        logger.info(f"Removing all chunks for paper: {paper_id}")
        
        keep_indices = [i for i, m in enumerate(self.metadata) if getattr(m, "paper_id", None) != paper_id]
        keep_embeddings = None
        if keep_indices:
            keep_embeddings = np.vstack([self.index.reconstruct(i) for i in keep_indices])
            
        self._init_index()
        if keep_indices and keep_embeddings is not None:
            self.index.add(keep_embeddings)
            self.metadata = keep_meta
        else:
            self.metadata = []
        self.save()

    def search_by_pyq_set(
        self, query_embedding: np.ndarray, pyq_set_id: str, top_k: int = 5
    ) -> List[Tuple[ChunkMetadata, float]]:
        """Search for top_k chunks filtered to a specific pyq_set_id."""
        if self.index.ntotal == 0:
            return []

        pyq_chunks = [m for m in self.metadata if m.pyq_set_id == pyq_set_id]
        if not pyq_chunks:
            return []

        fetch_k = min(max(top_k * 4, 20), self.index.ntotal)
        scores, indices = self.index.search(query_embedding, fetch_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            chunk = self.metadata[idx]
            if chunk.pyq_set_id != pyq_set_id:
                continue
            similarity = float(np.clip(score, 0, 1))
            results.append((chunk, similarity))
            if len(results) >= top_k:
                break

        return results

    def delete_by_pyq_set(self, pyq_set_id: str):
        """Remove all chunks belonging to a given pyq_set_id."""
        keep_meta = [m for m in self.metadata if getattr(m, "pyq_set_id", None) != pyq_set_id]
        if len(keep_meta) == len(self.metadata):
            return

        logger.info(f"Removing all chunks for PYQ Set: {pyq_set_id}")
        
        keep_indices = [i for i, m in enumerate(self.metadata) if getattr(m, "pyq_set_id", None) != pyq_set_id]
        keep_embeddings = None
        if keep_indices:
            keep_embeddings = np.vstack([self.index.reconstruct(i) for i in keep_indices])
            
        self._init_index()
        if keep_indices and keep_embeddings is not None:
            self.index.add(keep_embeddings)
            self.metadata = keep_meta
        else:
            self.metadata = []
        self.save()

    def purge_by_source(self, datastore_id: Optional[str] = None, agent_id: Optional[str] = None, source_file: Optional[str] = None):
        """Remove chunks for a specific file within a datastore or agent context."""
        def is_match(m):
            if source_file and m.source_file != source_file: return False
            if datastore_id and m.datastore_id != datastore_id: return False
            if agent_id and m.agent_id != agent_id: return False
            return True

        keep_meta = [m for m in self.metadata if not is_match(m)]
        if len(keep_meta) == len(self.metadata):
            return

        logger.info(f"Purging vectors for source: {source_file} in ds:{datastore_id}/ag:{agent_id}")
        self._init_index()
        if keep_meta:
            from app.services.embedder import embed_texts
            texts = [m.text for m in keep_meta]
            embeddings = embed_texts(texts)
            self.index.add(embeddings)
            self.metadata = keep_meta
        self.save()

    def search_combined(
        self, query_embedding: np.ndarray, agent_id: str, datastore_ids: List[str], top_k: int = 5
    ) -> List[Tuple[ChunkMetadata, float]]:
        """
        Search for top_k chunks across multiple contexts:
        - Chunks belonging directly to agent_id
        - Chunks belonging to any of the datastore_ids
        """
        if self.index.ntotal == 0:
            return []

        # Over-fetch for filtering
        fetch_k = min(max(top_k * 5, 50), self.index.ntotal)
        scores, indices = self.index.search(query_embedding, fetch_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1: continue
            chunk = self.metadata[idx]
            
            is_match = False
            if agent_id and chunk.agent_id == agent_id:
                is_match = True
            elif datastore_ids and chunk.datastore_id in datastore_ids:
                is_match = True
            
            if is_match:
                similarity = float(np.clip(score, 0, 1))
                results.append((chunk, similarity))
            
            if len(results) >= top_k:
                break
        
        return results


# Module-level singleton
_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
