# ═══════════════════════════════════════════════════════════════
# MR AI RAG v2 — Dockerfile
# ═══════════════════════════════════════════════════════════════
# Multi-stage build:
#   Stage 1 (builder) — install heavy Python deps
#   Stage 2 (runtime) — lean final image
# ═══════════════════════════════════════════════════════════════

# ── Stage 1: Builder ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps into /install
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="MR AI RAG"
LABEL description="RAG system with multi-provider LLM, PDF, Web, YouTube & Video support"
LABEL version="2.0.0"

# ── System runtime dependencies ───────────────────────────────
# ffmpeg  → audio extraction for Whisper video transcription
# libgomp → required by faiss-cpu
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Copy installed Python packages from builder ───────────────
COPY --from=builder /install /usr/local

# ── App setup ─────────────────────────────────────────────────
WORKDIR /app

# Create required directories with correct permissions
RUN mkdir -p /app/uploads /app/vector_store /app/frontend

# Copy application source
COPY app/           ./app/
COPY frontend/      ./frontend/
COPY requirements.txt .

# Optional: copy .env if present (override via docker run -e or docker-compose)
COPY .env* ./

# ── Runtime settings ──────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PORT=8000

# Persist uploads and vector store across restarts
VOLUME ["/app/uploads", "/app/vector_store"]

# ── Health check ──────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# ── Expose port ───────────────────────────────────────────────
EXPOSE ${PORT}

# ── Entrypoint ────────────────────────────────────────────────
# Using gunicorn + uvicorn workers for production
# Adjust --workers based on your CPU count (2 × CPU + 1 is recommended)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --proxy-headers --forwarded-allow-ips='*'"]