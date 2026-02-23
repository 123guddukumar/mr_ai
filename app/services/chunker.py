"""
MR AI RAG - Intelligent Chunking Service
Sentence-aware chunking with configurable size and overlap.
"""

import re
import uuid
import logging
from typing import List, Tuple
from app.models.schemas import ChunkMetadata
from app.core.config import settings

logger = logging.getLogger(__name__)


def split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using regex-based sentence boundary detection."""
    # Handle abbreviations and split on sentence boundaries
    sentence_endings = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
    sentences = sentence_endings.split(text)
    # Further split on newline-separated paragraphs
    result = []
    for sent in sentences:
        parts = sent.split('\n\n')
        result.extend(p.strip() for p in parts if p.strip())
    return result


def chunk_text(
    page_texts: List[Tuple[int, str]],
    source_file: str,
    chunk_size: int = None,
    chunk_overlap: int = None
) -> List[ChunkMetadata]:
    """
    Sentence-aware chunking with overlap.
    Groups sentences into chunks of ~chunk_size tokens with chunk_overlap token overlap.
    """
    chunk_size = chunk_size or settings.CHUNK_SIZE
    chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP

    all_chunks: List[ChunkMetadata] = []
    chunk_index = 0

    for page_num, page_text in page_texts:
        sentences = split_into_sentences(page_text)
        if not sentences:
            continue

        current_chunk_sentences: List[str] = []
        current_token_count = 0

        for sentence in sentences:
            sentence_tokens = _count_tokens(sentence)

            # If adding this sentence exceeds chunk_size, flush current chunk
            if current_token_count + sentence_tokens > chunk_size and current_chunk_sentences:
                chunk_text_str = " ".join(current_chunk_sentences)
                all_chunks.append(ChunkMetadata(
                    chunk_id=str(uuid.uuid4()),
                    source_file=source_file,
                    page_number=page_num,
                    chunk_index=chunk_index,
                    text=chunk_text_str,
                    char_start=0,
                    char_end=len(chunk_text_str)
                ))
                chunk_index += 1

                # Overlap: keep last N tokens worth of sentences
                overlap_sentences, overlap_tokens = _get_overlap_sentences(
                    current_chunk_sentences, chunk_overlap
                )
                current_chunk_sentences = overlap_sentences
                current_token_count = overlap_tokens

            current_chunk_sentences.append(sentence)
            current_token_count += sentence_tokens

        # Flush remaining sentences
        if current_chunk_sentences:
            chunk_text_str = " ".join(current_chunk_sentences)
            all_chunks.append(ChunkMetadata(
                chunk_id=str(uuid.uuid4()),
                source_file=source_file,
                page_number=page_num,
                chunk_index=chunk_index,
                text=chunk_text_str,
                char_start=0,
                char_end=len(chunk_text_str)
            ))
            chunk_index += 1

    logger.info(f"Created {len(all_chunks)} chunks from {source_file}")
    return all_chunks


def _count_tokens(text: str) -> int:
    """Approximate token count (~4 chars per token)."""
    return max(1, len(text) // 4)


def _get_overlap_sentences(sentences: List[str], overlap_tokens: int) -> Tuple[List[str], int]:
    """Return the last N sentences that fit within overlap_tokens."""
    selected = []
    total = 0
    for sentence in reversed(sentences):
        t = _count_tokens(sentence)
        if total + t > overlap_tokens:
            break
        selected.insert(0, sentence)
        total += t
    return selected, total
