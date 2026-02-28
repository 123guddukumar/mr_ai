"""
MR AI RAG - Query Route
POST /ask
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from app.core.api_keys import require_api_key
from app.core.config import settings
from app.models.schemas import QueryRequest, QueryResponse
from app.services.embedder import embed_query
from app.services.vector_store import get_vector_store
from app.services.llm import generate_answer, build_context_and_sources, get_active_provider, get_active_model

logger = logging.getLogger(__name__)
router = APIRouter()

NO_CONTEXT_ANSWER = "The requested information is not available in the provided documents."
MIN_SIMILARITY_THRESHOLD = 0.25


@router.post("/ask", response_model=QueryResponse, summary="Ask a question from indexed documents")
async def ask_question(request: QueryRequest, _key: dict = Depends(require_api_key)):
    store = get_vector_store()
    if store.total_chunks == 0:
        raise HTTPException(status_code=404, detail="No documents indexed yet. Please upload PDF documents first.")

    query_embedding = embed_query(request.question)
    top_k = request.top_k or settings.TOP_K_RESULTS
    results = store.search(query_embedding, top_k=top_k)
    results = [(chunk, score) for chunk, score in results if score >= MIN_SIMILARITY_THRESHOLD]

    model_used = f"{get_active_provider()}/{get_active_model()}"

    if not results:
        return QueryResponse(
            question=request.question,
            answer=NO_CONTEXT_ANSWER,
            sources=[],
            context_found=False,
            model_used=model_used
        )

    context, sources = build_context_and_sources(results)

    try:
        answer = await generate_answer(request.question, context)
    except Exception as e:
        logger.error(f"LLM generation failed: {e}")
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {str(e)}")

    return QueryResponse(
        question=request.question,
        answer=answer,
        sources=sources,
        context_found=True,
        model_used=model_used
    )
