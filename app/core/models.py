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
from sqlalchemy.orm import relationship
from app.core.database import Base


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
    password_hash = Column(String(128), nullable=False)
    admin_token = Column(String(200), nullable=True, index=True)  # Session token
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
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
