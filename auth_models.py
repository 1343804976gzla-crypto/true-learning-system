from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from database.domains import CoreBase, core_engine, schema_auto_create_enabled


class User(CoreBase):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_admin = Column(Boolean, nullable=False, default=False, index=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")


class AuthSession(CoreBase):
    __tablename__ = "auth_sessions"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    session_token_hash = Column(String, nullable=False, unique=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    user = relationship("User", back_populates="sessions")


def ensure_auth_tables() -> None:
    if not schema_auto_create_enabled():
        return
    CoreBase.metadata.create_all(
        bind=core_engine,
        tables=[
            User.__table__,
            AuthSession.__table__,
        ],
    )
