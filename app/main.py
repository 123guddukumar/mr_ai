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
from app.routes import upload, query, health, provider, website, youtube, jsondata, apikeys, clients, admin as admin_routes, memory as memory_routes, reels as reels_routes
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
    # Initialize PostgreSQL tables
    from app.core.database import init_db
    try:
        init_db()
    except Exception as e:
        logger.warning(f"⚠️ DB init failed (is PostgreSQL running?): {e}")
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
app.include_router(jsondata.router, prefix="/api", tags=["Documents"])
app.include_router(query.router, prefix="/api", tags=["Query"])
app.include_router(apikeys.router, prefix="/api", tags=["API Keys"])
app.include_router(clients.router, prefix="/api", tags=["Clients"])
app.include_router(admin_routes.router, prefix="/api", tags=["Admin"])
app.include_router(memory_routes.router, prefix="/api", tags=["Memory"])
app.include_router(reels_routes.router, prefix="/api", tags=["Reels"])


# ── Serve Frontend ────────────────────────────────────────────────────────────
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_path, "assets")), name="assets")

    @app.get("/api-docs", include_in_schema=False)
    async def api_docs_page():
        return FileResponse(os.path.join(frontend_path, "api-docs.html"))

    @app.get("/playground", include_in_schema=False)
    async def playground_page():
        return FileResponse(os.path.join(frontend_path, "playground.html"))

    @app.get("/login", include_in_schema=False)
    async def login_page():
        return FileResponse(os.path.join(frontend_path, "login.html"))

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_page():
        return FileResponse(os.path.join(frontend_path, "dashboard.html"))

    @app.get("/help", include_in_schema=False)
    async def help_page():
        return FileResponse(os.path.join(frontend_path, "help.html"))

    @app.get("/admin-login", include_in_schema=False)
    async def admin_login_page():
        return FileResponse(os.path.join(frontend_path, "admin-login.html"))

    @app.get("/admin", include_in_schema=False)
    async def admin_dashboard_page():
        return FileResponse(os.path.join(frontend_path, "admin.html"))

    @app.get("/memory", include_in_schema=False)
    async def memory_page():
        return FileResponse(os.path.join(frontend_path, "memory.html"))

    @app.get("/memory-chat", include_in_schema=False)
    async def memory_chat_page():
        return FileResponse(os.path.join(frontend_path, "memory-chat.html"))

    @app.get("/memory-chat-public", include_in_schema=False)
    async def memory_chat_public_page():
        return FileResponse(os.path.join(frontend_path, "memory-chat-public.html"))

    @app.get("/reels", include_in_schema=False)
    async def reels_page():
        return FileResponse(os.path.join(frontend_path, "reels.html"))

    @app.get("/embed/{memory_id}", include_in_schema=False)
    async def embed_page(memory_id: str):
        return FileResponse(os.path.join(frontend_path, "embed.html"))

    @app.get("/api-docs-page", include_in_schema=False)
    async def api_docs_page():
        return FileResponse(os.path.join(frontend_path, "api-docs.html"))

    @app.get("/aws-deploy-guide", include_in_schema=False)
    async def aws_deploy_guide():
        return FileResponse(os.path.join(frontend_path, "aws-deploy-guide.html"))

    @app.get("/developer-guide", include_in_schema=False)
    async def developer_guide():
        return FileResponse(os.path.join(frontend_path, "developer-guide.html"))

    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(os.path.join(frontend_path, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        index = os.path.join(frontend_path, "index.html")
        return FileResponse(index)