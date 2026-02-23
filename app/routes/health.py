"""
MR AI RAG - Health Check Route
"""

from fastapi import APIRouter
from app.core.config import settings
from app.models.schemas import HealthResponse
from app.services.vector_store import get_vector_store
from app.services.llm import get_active_provider, get_active_model

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="System health check")
async def health_check():
    store = get_vector_store()
    return HealthResponse(
        status="ok",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        vector_store_loaded=store.index is not None,
        total_chunks_indexed=store.total_chunks,
        embedding_model=settings.EMBEDDING_MODEL,
        llm_provider=get_active_provider(),
        llm_model=get_active_model()
    )
