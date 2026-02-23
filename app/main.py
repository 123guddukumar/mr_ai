"""
MR AI RAG v2 - Main FastAPI Application
"""

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routes import upload, query, health, provider, website, youtube
from app.services.embedder import get_embedding_model
from app.services.vector_store import get_vector_store

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    get_embedding_model()
    store = get_vector_store()
    logger.info(f"✅ Ready — {store.total_chunks} chunks indexed.")
    yield
    logger.info("🛑 Shutting down.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Production-ready RAG backend with multi-provider LLM support.",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal Server Error", "detail": str(exc)})


# ── API Routes ────────────────────────────────────────────────────────────────
app.include_router(health.router, prefix="/api", tags=["System"])
app.include_router(provider.router, prefix="/api", tags=["Provider"])
app.include_router(upload.router, prefix="/api", tags=["Documents"])
app.include_router(website.router, prefix="/api", tags=["Documents"])
app.include_router(youtube.router, prefix="/api", tags=["Documents"])
app.include_router(query.router, prefix="/api", tags=["Query"])


# ── Serve Frontend ────────────────────────────────────────────────────────────
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_path, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        index = os.path.join(frontend_path, "index.html")
        return FileResponse(index)

    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(os.path.join(frontend_path, "index.html"))