"""
MR AI RAG - PostgreSQL Database Connection
SQLAlchemy 2.x sync engine with connection pooling.
Tables are auto-created on first startup.
"""

import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

Base = declarative_base()

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        from app.core.config import settings
        url = settings.DATABASE_URL
        _engine = create_engine(
            url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            echo=settings.DEBUG,
        )
        logger.info(f"✅ Database engine created: {url.split('@')[-1]}")
    return _engine


def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
        )
    return _SessionLocal


def get_db():
    """FastAPI dependency: yields a DB session and closes it after request."""
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist. Called on app startup."""
    from app.core import models  # noqa: F401 — ensure models are imported
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Auto-migrate: add new columns to chat_history if they don't exist yet
    try:
        with engine.connect() as conn:
            conn.execute(__import__('sqlalchemy').text(
                "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS category VARCHAR(30) DEFAULT 'home'"
            ))
            conn.execute(__import__('sqlalchemy').text(
                "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS source_type VARCHAR(30) DEFAULT ''"
            ))
            conn.execute(__import__('sqlalchemy').text(
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS login_method VARCHAR(20) DEFAULT 'email'"
            ))
            conn.execute(__import__('sqlalchemy').text(
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500) DEFAULT ''"
            ))
            conn.commit()
    except Exception:
        pass  # Table may not exist yet or DB may not support IF NOT EXISTS
    logger.info("✅ Database tables initialized.")
