from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.orm import relationship

from database.domains import AgentBase

Base = AgentBase


class AgentSoul(Base):
    __tablename__ = "agent_souls"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    display_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    personality = Column(Text, nullable=True)
    values = Column(Text, nullable=True)
    communication_style = Column(Text, nullable=True)
    teaching_philosophy = Column(Text, nullable=True)
    forbidden_behaviors = Column(Text, nullable=True)
    is_system = Column(Boolean, nullable=False, default=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    sessions = relationship("AgentSession", back_populates="soul")
