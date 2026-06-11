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
    
    # Auto-migrate: add new columns/modifications dynamically
    statements = [
        "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS category VARCHAR(30) DEFAULT 'home'",
        "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS source_type VARCHAR(30) DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS login_method VARCHAR(20) DEFAULT 'email'",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500) DEFAULT ''",
        "ALTER TABLE admins ADD COLUMN IF NOT EXISTS is_super BOOLEAN DEFAULT FALSE",
        "ALTER TABLE admins ADD COLUMN IF NOT EXISTS email VARCHAR(200) DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS business_name VARCHAR(200)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS mobile_number VARCHAR(20)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS website_url VARCHAR(300)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS gst_number VARCHAR(50)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS pan_number VARCHAR(50)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS city VARCHAR(100)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS pin_code VARCHAR(20)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS address TEXT",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS created_by_admin_id INTEGER",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS category VARCHAR(100)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS created_by_client_id VARCHAR(64)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS user_type VARCHAR(50)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS dob VARCHAR(50)",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS profession VARCHAR(100)",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS voice_config_json TEXT DEFAULT '{}'",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS system_config_json TEXT DEFAULT '{}'",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS customization_json TEXT DEFAULT '{}'",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS datastores_json TEXT DEFAULT '[]'",
        "ALTER TABLE datastore_sources ADD COLUMN IF NOT EXISTS raw_text TEXT",
        "ALTER TABLE agent_knowledge_sources ADD COLUMN IF NOT EXISTS raw_text TEXT",
        "ALTER TABLE social_contents ALTER COLUMN media_url TYPE TEXT",
        "ALTER TABLE social_contents ALTER COLUMN title TYPE VARCHAR(500)",
        "ALTER TABLE social_contents ADD COLUMN IF NOT EXISTS scenes_json TEXT",
        "ALTER TABLE social_contents ADD COLUMN IF NOT EXISTS metadata_json TEXT",
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS category VARCHAR(200)",
        "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS paper_id VARCHAR(64)",
        "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS color VARCHAR(50) DEFAULT '#4f46e5'",
        "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
        "ALTER TABLE classroom_chapters ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
        "ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS notes TEXT",
        "ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS script TEXT",
        "ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS video_length INTEGER",
        "ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS script TEXT",
        "ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS notes TEXT",
        "ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
        "ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
        "ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS banner_url VARCHAR(500)",
        "ALTER TABLE ca_topics ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
        "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS image_url_9_16 VARCHAR(500)",
        "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS image_url_16_9 VARCHAR(500)",
        "ALTER TABLE classroom_chapters ADD COLUMN IF NOT EXISTS image_url_9_16 VARCHAR(500)",
        "ALTER TABLE classroom_chapters ADD COLUMN IF NOT EXISTS image_url_16_9 VARCHAR(500)",
        "ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS image_url_9_16 VARCHAR(500)",
        "ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS image_url_16_9 VARCHAR(500)",
        "ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS image_url_9_16 VARCHAR(500)",
        "ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS image_url_16_9 VARCHAR(500)"
    ]
    
    try:
        with engine.connect() as conn:
            for stmt in statements:
                try:
                    conn.execute(__import__('sqlalchemy').text(stmt))
                    conn.commit()
                except Exception as e:
                    logger.debug(f"Migration statement skipped: {stmt} -> {e}")
                    conn.rollback()
    except Exception as e:
        logger.warning(f"⚠️ Migration connection failed: {e}")
        
    logger.info("✅ Database tables initialized.")

