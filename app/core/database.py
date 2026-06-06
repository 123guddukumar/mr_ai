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
            
            # Admin New Columns
            conn.execute(__import__('sqlalchemy').text(
                "ALTER TABLE admins ADD COLUMN IF NOT EXISTS is_super BOOLEAN DEFAULT FALSE"
            ))
            conn.execute(__import__('sqlalchemy').text(
                "ALTER TABLE admins ADD COLUMN IF NOT EXISTS email VARCHAR(200) DEFAULT ''"
            ))
            
            # Client (App) New Columns
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS business_name VARCHAR(200)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS mobile_number VARCHAR(20)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS website_url VARCHAR(300)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS gst_number VARCHAR(50)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS pan_number VARCHAR(50)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS city VARCHAR(100)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS pin_code VARCHAR(20)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS address TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS created_by_admin_id INTEGER"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS created_by_client_id VARCHAR(64)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS user_type VARCHAR(50)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS dob VARCHAR(50)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS profession VARCHAR(100)"))
            
            # Agents Table New Columns
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS voice_config_json TEXT DEFAULT '{}'"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS system_config_json TEXT DEFAULT '{}'"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS customization_json TEXT DEFAULT '{}'"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS datastores_json TEXT DEFAULT '[]'"))
            
            # Knowledge Source New Columns
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE datastore_sources ADD COLUMN IF NOT EXISTS raw_text TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE agent_knowledge_sources ADD COLUMN IF NOT EXISTS raw_text TEXT"))
            
            # Social Content Fixes (Type changes and new columns)
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE social_contents ALTER COLUMN media_url TYPE TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE social_contents ALTER COLUMN title TYPE VARCHAR(500)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE social_contents ADD COLUMN IF NOT EXISTS scenes_json TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE social_contents ADD COLUMN IF NOT EXISTS metadata_json TEXT"))
            
            # Classroom Upgrade Columns
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE exams ADD COLUMN IF NOT EXISTS category VARCHAR(200)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS paper_id VARCHAR(64)"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS color VARCHAR(50) DEFAULT '#4f46e5'"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS notes TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS script TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS video_length INTEGER"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS script TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS description TEXT"))
            conn.execute(__import__('sqlalchemy').text("ALTER TABLE classroom_topics ADD COLUMN IF NOT EXISTS notes TEXT"))
            
            conn.commit()
    except Exception:
        pass  # Table may not exist yet or DB may not support IF NOT EXISTS
    logger.info("✅ Database tables initialized.")
