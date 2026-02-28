"""
MR AI RAG - Core Configuration
All system settings loaded from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # App Info
    APP_NAME: str = "MR AI RAG"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    VECTOR_STORE_PATH: str = "vector_store/faiss_index"
    METADATA_STORE_PATH: str = "vector_store/metadata.json"
    UPLOAD_DIR: str = "uploads"

    # Embedding Model (local, no API key required)
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384

    # Chunking
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 100
    TOP_K_RESULTS: int = 5

    # LLM Provider: openai | gemini | claude | ollama | huggingface
    LLM_PROVIDER: str = "openai"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_MAX_TOKENS: int = 1024
    OPENAI_TEMPERATURE: float = 0.1

    # Google Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Anthropic Claude
    CLAUDE_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-3-5-haiku-20241022"

    # Ollama (local)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"

    # HuggingFace
    HF_MODEL_ID: str = "mistralai/Mistral-7B-Instruct-v0.2"
    HF_API_KEY: str = ""

    # API Key Management
    API_KEY_ADMIN_SECRET: str = "change-me-admin-secret"
    API_KEYS_ENABLED: bool = False

    # PostgreSQL Database
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/mrairag"

    # SMTP Email (for OTP)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "rkinstitute85@gmail.com"
    SMTP_PASSWORD: str = "hchdlojdrkwtacnx"

    # Anti-hallucination System Prompt
    SYSTEM_PROMPT: str = (
        "You are MR AI RAG. "
        "You must answer strictly using provided context. "
        "If answer not found, say: "
        "'The requested information is not available in the provided documents.' "
        "Always cite sources with file name and page number."
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
