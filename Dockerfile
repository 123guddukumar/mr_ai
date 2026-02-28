# ═══════════════════════════════════════════════════════════════════════
# MR AI RAG v2 — Dockerfile
# Optimized for: Intel i3 dual-core, 8GB RAM, CPU-only, Windows 11
# Build time:    ~8-12 min (first time), ~1-2 min (cached rebuild)
# Image size:    ~1.8 GB
# ═══════════════════════════════════════════════════════════════════════

# ── STAGE 1: Dependency builder ─────────────────────────────────────────
# Install all heavy Python packages here, then copy to lean runtime image.
# This keeps the final image small and build cache efficient.
FROM python:3.11-slim AS builder

WORKDIR /build

# Install only what's needed to compile Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        git \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first (faster resolver)
RUN pip install --upgrade pip setuptools wheel --no-cache-dir

# ── Copy requirements and install ──────────────────────────────────────
COPY requirements.txt .

# Install everything into /install prefix (copied to runtime in stage 2)
# --no-cache-dir   → saves ~200MB during build
# --prefix         → isolates from system Python
RUN pip install \
        --prefix=/install \
        --no-cache-dir \
        --no-warn-script-location \
        -r requirements.txt


# ── STAGE 2: Lean runtime image ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="MR AI RAG"
LABEL version="2.0.0"
LABEL description="RAG · PDF · Web · YouTube · Video · Multi-LLM"

# ── System runtime deps ─────────────────────────────────────────────────
# ffmpeg   → audio extraction for Whisper video transcription
# libgomp1 → OpenMP, required by faiss-cpu and sentence-transformers
# curl     → used by HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# ── Copy Python packages from builder ──────────────────────────────────
COPY --from=builder /install /usr/local

# ── Working directory ───────────────────────────────────────────────────
WORKDIR /app

# ── Create data directories ─────────────────────────────────────────────
RUN mkdir -p /app/uploads /app/vector_store

# ── Copy source code ────────────────────────────────────────────────────
# Copy in order: least-changed first → maximises Docker layer cache
COPY requirements.txt        ./requirements.txt
COPY app/                    ./app/
COPY frontend/               ./frontend/

# Copy .env if it exists (silently skip if not found)
# Real secrets should be passed via docker-compose env: or -e flags
COPY .env.example            ./.env.example
# COPY .env                  ./.env   ← uncomment only for local dev

# ── Environment variables ───────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    # Torch CPU-only — disables GPU detection warnings
    CUDA_VISIBLE_DEVICES="" \
    # Sentence-transformers cache inside container
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    # Whisper model cache
    WHISPER_CACHE=/app/.cache/whisper \
    PORT=8000

# Create cache dirs
RUN mkdir -p /app/.cache/huggingface /app/.cache/whisper

# ── Persistent data volumes ─────────────────────────────────────────────
# These directories survive container restarts via named volumes
VOLUME ["/app/uploads", "/app/vector_store", "/app/.cache"]

# ── Port ────────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Health check ────────────────────────────────────────────────────────
# --start-period 90s gives time for embedding model to load on first start
HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --start-period=90s \
    --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# ── Start command ───────────────────────────────────────────────────────
# i3 dual-core → 1 worker is safest to avoid OOM on 8GB RAM
# Increase to 2 only if you have RAM available after other apps
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "30", \
     "--log-level", "info"]