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
            pool_recycle=300,
            pool_timeout=30,
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
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_root BOOLEAN DEFAULT FALSE",
        "ALTER TABLE agent_public_sessions ADD COLUMN IF NOT EXISTS action_button_json TEXT",
        "ALTER TABLE datastore_sources ADD COLUMN IF NOT EXISTS raw_text TEXT",
        "ALTER TABLE agent_knowledge_sources ADD COLUMN IF NOT EXISTS raw_text TEXT",
        "ALTER TABLE agent_public_sessions ADD COLUMN IF NOT EXISTS analysis_json TEXT",
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
        "ALTER TABLE classroom_subtopics ADD COLUMN IF NOT EXISTS image_url_16_9 VARCHAR(500)",
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS image_url_9_16 VARCHAR(500)",
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS image_url_16_9 VARCHAR(500)",
        "ALTER TABLE classroom_papers ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
        "ALTER TABLE classroom_papers ADD COLUMN IF NOT EXISTS image_url_9_16 VARCHAR(500)",
        "ALTER TABLE classroom_papers ADD COLUMN IF NOT EXISTS image_url_16_9 VARCHAR(500)",
        "CREATE TABLE IF NOT EXISTS root_memories (id SERIAL PRIMARY KEY, memory_id VARCHAR(64), client_id VARCHAR(64), owner_id VARCHAR(64), category VARCHAR(50) DEFAULT 'note', title VARCHAR(300), content TEXT, tags_json TEXT DEFAULT '[]', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS memory_id VARCHAR(64)",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS owner_id VARCHAR(64)",
        "ALTER TABLE root_memories ALTER COLUMN owner_id DROP NOT NULL",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'note'",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS title VARCHAR(300)",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS content TEXT",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS tags_json TEXT DEFAULT '[]'",
        "ALTER TABLE root_memories ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "CREATE TABLE IF NOT EXISTS root_meetings (id SERIAL PRIMARY KEY, meeting_id VARCHAR(64), client_id VARCHAR(64), owner_id VARCHAR(64), title VARCHAR(300), description TEXT, meeting_time TIMESTAMP, duration_mins INTEGER DEFAULT 30, status VARCHAR(30) DEFAULT 'scheduled', reminder_sent BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS meeting_id VARCHAR(64)",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS owner_id VARCHAR(64)",
        "ALTER TABLE root_meetings ALTER COLUMN owner_id DROP NOT NULL",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS title VARCHAR(300)",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS meeting_time TIMESTAMP",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS duration_mins INTEGER DEFAULT 30",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'scheduled'",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE",
        "ALTER TABLE root_meetings ALTER COLUMN reminder_sent DROP NOT NULL",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS notification_sent BOOLEAN DEFAULT FALSE",
        "ALTER TABLE root_meetings ALTER COLUMN notification_sent DROP NOT NULL",
        "ALTER TABLE root_meetings ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "CREATE TABLE IF NOT EXISTS root_media (id SERIAL PRIMARY KEY, media_id VARCHAR(64), client_id VARCHAR(64), owner_id VARCHAR(64), media_type VARCHAR(30), name VARCHAR(300), description TEXT, file_url TEXT, file_path TEXT, raw_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS media_id VARCHAR(64)",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS owner_id VARCHAR(64)",
        "ALTER TABLE root_media ALTER COLUMN owner_id DROP NOT NULL",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS media_type VARCHAR(30)",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS name VARCHAR(300)",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS file_url TEXT",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS file_path TEXT",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS raw_text TEXT",
        "ALTER TABLE root_media ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "CREATE TABLE IF NOT EXISTS agent_feedbacks (id SERIAL PRIMARY KEY, agent_id VARCHAR(64), user_name VARCHAR(200), user_email VARCHAR(300), feedback_type VARCHAR(50) DEFAULT 'feedback', rating INTEGER, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "ALTER TABLE agent_feedbacks ADD COLUMN IF NOT EXISTS device_id VARCHAR(64)",
        "ALTER TABLE agent_feedbacks ADD COLUMN IF NOT EXISTS session_id VARCHAR(64)",
        # ── Root Daily Planner ──────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS root_daily_plans (id SERIAL PRIMARY KEY, plan_id VARCHAR(64) UNIQUE, client_id VARCHAR(64), owner_id VARCHAR(64), title VARCHAR(300) NOT NULL, description TEXT DEFAULT '', category VARCHAR(50) DEFAULT 'work', plan_date VARCHAR(20) NOT NULL, plan_time VARCHAR(10) NOT NULL, status VARCHAR(30) DEFAULT 'pending', is_completed BOOLEAN DEFAULT FALSE, completed_at TIMESTAMP, from_meeting BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS plan_id VARCHAR(64)",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS owner_id VARCHAR(64)",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS title VARCHAR(300)",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'work'",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS plan_date VARCHAR(20)",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS plan_time VARCHAR(10)",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'pending'",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS is_completed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS from_meeting BOOLEAN DEFAULT FALSE",
        "ALTER TABLE root_daily_plans ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
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

