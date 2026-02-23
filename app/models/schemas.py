"""
MR AI RAG - Pydantic Data Models
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime


class ChunkMetadata(BaseModel):
    chunk_id: str
    source_file: str
    page_number: int
    chunk_index: int
    text: str
    char_start: int = 0
    char_end: int = 0


class UploadResponse(BaseModel):
    success: bool
    filename: str
    total_pages: int
    total_chunks: int
    message: str
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    top_k: Optional[int] = Field(default=5, ge=1, le=20)


class SourceCitation(BaseModel):
    filename: str
    page_number: int
    excerpt: str
    similarity_score: float


class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: List[SourceCitation]
    context_found: bool
    model_used: str
    answered_at: datetime = Field(default_factory=datetime.utcnow)


class HealthResponse(BaseModel):
    status: str
    app_name: str
    version: str
    vector_store_loaded: bool
    total_chunks_indexed: int
    embedding_model: str
    llm_provider: str
    llm_model: str


# ── Provider Config Models ────────────────────────────────────────────────────

ProviderName = Literal["openai", "gemini", "claude", "ollama", "huggingface"]


class ProviderConfigRequest(BaseModel):
    provider: ProviderName
    api_key: Optional[str] = ""
    model: Optional[str] = ""
    ollama_url: Optional[str] = "http://localhost:11434"


class ProviderConfigResponse(BaseModel):
    success: bool
    provider: str
    model: str
    message: str


class ProviderStatusResponse(BaseModel):
    current_provider: str
    current_model: str
    providers: List[dict]


class ErrorResponse(BaseModel):
    error: str
    detail: str
    status_code: int
