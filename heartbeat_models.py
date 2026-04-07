from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from database.domains import AgentBase

Base = AgentBase


class HeartbeatTask(Base):
    __tablename__ = "heartbeat_tasks"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    soul_id = Column(String, ForeignKey("agent_souls.id"), nullable=True, index=True)
    dedupe_key = Column(String, nullable=True, unique=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    task_type = Column(String, nullable=False, default="agent_chat", index=True)
    cron_expr = Column(String, nullable=True)
    timezone = Column(String, nullable=False, default="Asia/Shanghai")
    next_run_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True, index=True)
    last_status = Column(String, nullable=False, default="idle", index=True)
    is_enabled = Column(Boolean, nullable=False, default=True, index=True)
    payload = Column(JSON, nullable=True)
    last_result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    soul = relationship("AgentSoul")
    delivery_logs = relationship(
        "HeartbeatDeliveryLog",
        back_populates="task",
        cascade="all, delete-orphan",
    )


class HeartbeatDeliveryLog(Base):
    __tablename__ = "heartbeat_delivery_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, ForeignKey("heartbeat_tasks.id"), nullable=False, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    channel = Column(String, nullable=False, default="telegram", index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    delivery_target = Column(String, nullable=True)
    content_preview = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    delivery_payload = Column(JSON, nullable=True)
    delivered_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    task = relationship("HeartbeatTask", back_populates="delivery_logs")
