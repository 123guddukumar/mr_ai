"""
MR AI RAG - SQLAlchemy ORM Models
Tables: clients, api_keys, chat_history, notifications, email_otp
"""

import json
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text,
    ForeignKey, Index
)
from sqlalchemy.orm import relationship, Session
from app.core.database import Base, get_db


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    email = Column(String(300), unique=True, index=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    token = Column(String(200), nullable=True, index=True)
    is_verified = Column(Boolean, default=False, nullable=False)
    login_method = Column(String(20), default="email", nullable=False)  # "email" | "google" | "qr"
    avatar_url = Column(String(500), nullable=True)  # Google profile picture URL
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Ownership
    created_by_admin_id = Column(Integer, ForeignKey("admins.id"), nullable=True)
    
    # New App Fields
    business_name = Column(String(200), nullable=True)
    category = Column(String(100), nullable=True, default="General")
    mobile_number = Column(String(20), nullable=True)
    website_url = Column(String(300), nullable=True)
    gst_number = Column(String(50), nullable=True)
    pan_number = Column(String(50), nullable=True)
    city = Column(String(100), nullable=True)
    pin_code = Column(String(20), nullable=True)
    address = Column(Text, nullable=True)
    logo_url = Column(String(500), nullable=True)
    created_by_client_id = Column(String(64), nullable=True) # If created by another client
    user_type = Column(String(50), nullable=True) # New, Prime, Demo, etc.
    dob = Column(String(50), nullable=True)
    profession = Column(String(100), nullable=True)

    # Relationships
    api_keys = relationship("ApiKey", back_populates="client", cascade="all, delete-orphan")
    chat_history = relationship("ChatMessage", back_populates="client", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="client", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "client_id": self.client_id,
            "name": self.name,
            "email": self.email,
            "token": self.token,
            "is_verified": self.is_verified,
            "login_method": self.login_method or "email",
            "avatar_url": self.avatar_url or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "last_login": self.last_login.isoformat() if self.last_login else "",
            "created_by_admin_id": self.created_by_admin_id,
            "category": self.category or "General",
            "business_name": self.business_name or "",
            "mobile_number": self.mobile_number or "",
            "website_url": self.website_url or "",
            "gst_number": self.gst_number or "",
            "pan_number": self.pan_number or "",
            "city": self.city or "",
            "pin_code": self.pin_code or "",
            "address": self.address or "",
            "logo_url": self.logo_url or "",
            "created_by_client_id": self.created_by_client_id or "",
            "user_type": self.user_type or "New",
            "dob": self.dob or "",
            "profession": self.profession or "",
        }


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key_id = Column(String(64), unique=True, index=True, nullable=False)
    client_id = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=True)
    name = Column(String(200), nullable=False)
    created_by = Column(String(200), default="admin")
    key_hash = Column(String(128), nullable=False, index=True)
    key_preview = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    request_count = Column(Integer, default=0)

    client = relationship("Client", back_populates="api_keys")

    def to_dict(self):
        return {
            "id": self.key_id,
            "key_id": self.key_id,
            "name": self.name,
            "created_by": self.created_by,
            "key_hash": self.key_hash,
            "key_preview": self.key_preview,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "request_count": self.request_count,
            "client_id": self.client_id,
        }


class ChatMessage(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)      # "user" | "assistant"
    content = Column(Text, nullable=False)
    sources_json = Column(Text, default="[]")      # JSON array of source names
    category = Column(String(30), nullable=True, default="home")   # "home" | "playground"
    source_type = Column(String(30), nullable=True, default="")    # "pdf"|"yt"|"web"|"vid"|"json"
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    client = relationship("Client", back_populates="chat_history")

    @property
    def sources(self):
        try:
            return json.loads(self.sources_json or "[]")
        except Exception:
            return []

    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
            "sources": self.sources,
            "category": self.category or "home",
            "source_type": self.source_type or "",
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
        }


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(50), nullable=False)      # "api_key", "chat", "system", "security"
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    client = relationship("Client", back_populates="notifications")

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


class EmailOTP(Base):
    __tablename__ = "email_otp"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(300), nullable=False, index=True)
    purpose = Column(String(30), nullable=False, default="register")   # "register" | "reset"
    otp_hash = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_email_otp_email_purpose", "email", "purpose"),
    )


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=True)
    password_hash = Column(String(128), nullable=False)
    admin_token = Column(String(200), nullable=True, index=True)  # Session token
    is_super = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email or "",
            "is_super": self.is_super,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "last_login": self.last_login.isoformat() if self.last_login else "",
        }


class QRToken(Base):
    """Short-lived token embedded in a QR code so a client can auto-login by scanning."""
    __tablename__ = "qr_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(128), unique=True, index=True, nullable=False)
    client_id = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Personal Memory Models ────────────────────────────────────────────────────

class Memory(Base):
    """A per-client personal RAG chatbot configuration."""
    __tablename__ = "memories"

    id         = Column(Integer, primary_key=True, index=True)
    memory_id  = Column(String(64), unique=True, index=True, nullable=False)  # short slug
    client_id  = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    name        = Column(String(200), nullable=False)
    description = Column(Text, default="")
    # Stored provider config (provider api key is stored plain — user's own key)
    mrairag_api_key  = Column(String(200), nullable=True)   # MR AI RAG API key for this memory
    provider          = Column(String(50), default="gemini")
    provider_model    = Column(String(100), default="gemini-2.5-flash")
    provider_api_key  = Column(String(500), nullable=True)  # AI provider API key
    ollama_url        = Column(String(300), nullable=True)
    is_active  = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    sources     = relationship("MemorySource", back_populates="memory", cascade="all, delete-orphan")
    chat_msgs   = relationship("MemoryChat",   back_populates="memory", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "memory_id":   self.memory_id,
            "name":         self.name,
            "description":  self.description or "",
            "provider":     self.provider,
            "provider_model": self.provider_model,
            "is_active":   self.is_active,
            "created_at":  self.created_at.isoformat() if self.created_at else "",
            "source_count": len(self.sources),
        }


class MemorySource(Base):
    """A document/URL/video indexed into a Memory."""
    __tablename__ = "memory_sources"

    id           = Column(Integer, primary_key=True, index=True)
    memory_id    = Column(String(64), ForeignKey("memories.memory_id", ondelete="CASCADE"), nullable=False, index=True)
    source_type  = Column(String(30), nullable=False)   # pdf | url | youtube | video | json
    source_name  = Column(String(500), nullable=False)
    chunk_count  = Column(Integer, default=0)
    indexed_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    memory = relationship("Memory", back_populates="sources")

    def to_dict(self):
        return {
            "id":          self.id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "chunk_count": self.chunk_count,
            "raw_text":    self.raw_text or "",
            "indexed_at":  self.indexed_at.isoformat() if self.indexed_at else "",
        }


class MemoryChat(Base):
    """A chat message within a Memory chatbot session."""
    __tablename__ = "memory_chats"

    id          = Column(Integer, primary_key=True, index=True)
    memory_id   = Column(String(64), ForeignKey("memories.memory_id", ondelete="CASCADE"), nullable=False, index=True)
    role        = Column(String(20), nullable=False)    # user | assistant
    content     = Column(Text, nullable=False)
    sources_json = Column(Text, default="[]")
    timestamp   = Column(DateTime, default=datetime.utcnow, nullable=False)

    memory = relationship("Memory", back_populates="chat_msgs")

    @property
    def sources(self):
        try:
            return json.loads(self.sources_json or "[]")
        except Exception:
            return []

    def to_dict(self):
        return {
            "role":      self.role,
            "content":   self.content,
            "sources":   self.sources,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
        }


# ── DataStore & Agent Models ──────────────────────────────────────────────────

class DataStore(Base):
    """A collection of data sources that can be shared across agents."""
    __tablename__ = "datastores"

    id           = Column(Integer, primary_key=True, index=True)
    datastore_id = Column(String(64), unique=True, index=True, nullable=False)
    client_id    = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    name         = Column(String(200), nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    sources = relationship("DataStoreSource", back_populates="datastore", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "datastore_id": self.datastore_id,
            "name":         self.name,
            "created_at":   self.created_at.isoformat() if self.created_at else "",
            "source_count": len(self.sources),
        }

class DataStoreSource(Base):
    __tablename__ = "datastore_sources"

    id           = Column(Integer, primary_key=True, index=True)
    datastore_id = Column(String(64), ForeignKey("datastores.datastore_id", ondelete="CASCADE"), nullable=False, index=True)
    source_type  = Column(String(30), nullable=False)   # pdf | url | youtube | video | json
    source_name  = Column(String(500), nullable=False)
    chunk_count  = Column(Integer, default=0)
    raw_text     = Column(Text, nullable=True) # Full transcript or page text
    indexed_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    datastore = relationship("DataStore", back_populates="sources")

    def to_dict(self):
        return {
            "id":          self.id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "chunk_count": self.chunk_count,
            "raw_text":    self.raw_text or "",
            "indexed_at":  self.indexed_at.isoformat() if self.indexed_at else "",
        }

class Agent(Base):
    """A highly configurable AI agent with personality, voice, and RAG logic."""
    __tablename__ = "agents"

    id               = Column(Integer, primary_key=True, index=True)
    agent_id         = Column(String(64), unique=True, index=True, nullable=False)
    client_id        = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    name             = Column(String(200), nullable=False)
    description      = Column(Text, default="")
    category         = Column(String(100), default="General")
    personality      = Column(Text, default="")
    starting_message = Column(Text, default="Hello! How can I help you today?")
    
    # JSON Configs
    voice_config_json   = Column(Text, default="{}") 
    system_config_json  = Column(Text, default="{}")
    customization_json  = Column(Text, default="{}")
    datastores_json     = Column(Text, default="[]") 
    
    is_active  = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    knowledge_sources = relationship("AgentKnowledgeSource", back_populates="agent", cascade="all, delete-orphan")

    def to_dict(self):
        try: v_cfg = json.loads(self.voice_config_json or "{}")
        except: v_cfg = {}
        try: s_cfg = json.loads(self.system_config_json or "{}")
        except: s_cfg = {}
        try: c_cfg = json.loads(self.customization_json or "{}")
        except: c_cfg = {}
        try: ds_ids = json.loads(self.datastores_json or "[]")
        except: ds_ids = []

        return {
            "agent_id":         self.agent_id,
            "name":             self.name,
            "description":      self.description or "",
            "category":         self.category or "General",
            "personality":      self.personality or "",
            "starting_message": self.starting_message or "",
            "voice_config":     v_cfg,
            "system_config":    s_cfg,
            "customization":    c_cfg,
            "datastores":       ds_ids,
            "is_active":        self.is_active,
            "created_at":       self.created_at.isoformat() if self.created_at else "",
            "kb_source_count":  len(self.knowledge_sources),
        }

class AgentKnowledgeSource(Base):
    __tablename__ = "agent_knowledge_sources"

    id           = Column(Integer, primary_key=True, index=True)
    agent_id     = Column(String(64), ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False, index=True)
    source_type  = Column(String(30), nullable=False)
    source_name  = Column(String(500), nullable=False)
    chunk_count  = Column(Integer, default=0)
    indexed_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    agent = relationship("Agent", back_populates="knowledge_sources")

    def to_dict(self):
        return {
            "id":          self.id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "chunk_count": self.chunk_count,
            "raw_text":    self.raw_text or "",
            "indexed_at":  self.indexed_at.isoformat() if self.indexed_at else "",
        }

class WebsiteProject(Base):
    """Stores AI-upgraded websites for users."""
    __tablename__ = "website_projects"

    id           = Column(Integer, primary_key=True, index=True)
    project_id   = Column(String(64), unique=True, index=True, nullable=False)
    client_id    = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    name         = Column(String(200), nullable=False)
    url          = Column(String(500), nullable=True)
    html_code    = Column(Text, nullable=False)
    scraped_text = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "project_id": self.project_id,
            "name":       self.name,
            "url":        self.url or "",
            "html_code":  self.html_code,
            "scraped_text": self.scraped_text or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
        }


# ── LMS / Training Models ─────────────────────────────────────────────────────

class Course(Base):
    __tablename__ = "courses"
    id          = Column(Integer, primary_key=True, index=True)
    course_id   = Column(String(64), unique=True, index=True, nullable=False)
    client_id   = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    title       = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    chapters = relationship("Chapter", back_populates="course", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "course_id": self.course_id,
            "title": self.title,
            "description": self.description or "",
            "chapter_count": len(self.chapters),
            "created_at": self.created_at.isoformat() if self.created_at else ""
        }

class Chapter(Base):
    __tablename__ = "chapters"
    id          = Column(Integer, primary_key=True, index=True)
    course_id   = Column(String(64), ForeignKey("courses.course_id", ondelete="CASCADE"), nullable=False, index=True)
    title       = Column(String(300), nullable=False)
    order       = Column(Integer, default=0)

    course = relationship("Course", back_populates="chapters")
    topics = relationship("Topic", back_populates="chapter", cascade="all, delete-orphan")

class Topic(Base):
    __tablename__ = "topics"
    id          = Column(Integer, primary_key=True, index=True)
    chapter_id  = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    title       = Column(String(300), nullable=False)
    content     = Column(Text, nullable=True) # AI Generated content (1-2 mins read)
    order       = Column(Integer, default=0)

    chapter   = relationship("Chapter", back_populates="topics")
    questions = relationship("Question", back_populates="topic", cascade="all, delete-orphan")

class Question(Base):
    __tablename__ = "questions"
    id          = Column(Integer, primary_key=True, index=True)
    topic_id    = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=True)
    course_id   = Column(String(64), ForeignKey("courses.course_id", ondelete="CASCADE"), nullable=True) # For final test
    question    = Column(Text, nullable=False)
    options_json = Column(Text, nullable=False) # JSON list of options
    correct_idx = Column(Integer, nullable=False)
    is_test     = Column(Boolean, default=False) # True if part of final course test

    topic = relationship("Topic", back_populates="questions")

class UserCourseProgress(Base):
    __tablename__ = "user_course_progress"
    id          = Column(Integer, primary_key=True, index=True)
    client_id   = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    course_id   = Column(String(64), ForeignKey("courses.course_id", ondelete="CASCADE"), nullable=False)
    score       = Column(Integer, default=0)
    passed      = Column(Boolean, default=False)
    completed_at = Column(DateTime, default=datetime.utcnow)


class SocialContent(Base):
    """Stores generated social media posts and reels for users."""
    __tablename__ = "social_contents"

    id           = Column(Integer, primary_key=True, index=True)
    content_id   = Column(String(64), unique=True, index=True, nullable=False)
    client_id    = Column(String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    content_type = Column(String(20), nullable=False)  # "post" | "reel"
    title        = Column(String(500), nullable=True)
    body         = Column(Text, nullable=True)  # Caption or Script
    media_url    = Column(Text, nullable=True) # URL to image or video
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "content_id": self.content_id,
            "content_type": self.content_type,
            "title": self.title or "",
            "body": self.body or "",
            "media_url": self.media_url or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }

class SystemSettings(Base):
    """Global system-wide settings."""
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)
    buffer_api_key = Column(String(500), nullable=True)
    buffer_org_id = Column(String(200), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "buffer_api_key": self.buffer_api_key,
            "buffer_org_id": self.buffer_org_id
        }
