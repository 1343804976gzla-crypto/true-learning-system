from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from database.domains import AgentBase

Base = AgentBase


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    title = Column(String, nullable=False, default="新会话")
    agent_type = Column(String, nullable=False, default="tutor", index=True)
    status = Column(String, nullable=False, default="active", index=True)
    model = Column(String, nullable=False, default="auto")
    provider = Column(String, nullable=False, default="auto")
    prompt_template_id = Column(String, nullable=False, default="tutor.v1")
    context_summary = Column(Text, nullable=True)
    last_message_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    messages = relationship("AgentMessage", back_populates="session", cascade="all, delete-orphan")
    memories = relationship("AgentMemory", back_populates="session", cascade="all, delete-orphan")
    tool_calls = relationship("AgentToolCall", back_populates="session", cascade="all, delete-orphan")
    turn_states = relationship("AgentTurnState", back_populates="session", cascade="all, delete-orphan")
    tool_cache_entries = relationship("AgentToolCache", back_populates="session", cascade="all, delete-orphan")
    action_logs = relationship("AgentActionLog", back_populates="session", cascade="all, delete-orphan")
    tasks = relationship("AgentTask", back_populates="session", cascade="all, delete-orphan")


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=False, index=True)
    role = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    content_structured = Column(JSON, nullable=True)
    tool_name = Column(String, nullable=True)
    tool_input = Column(JSON, nullable=True)
    tool_output = Column(JSON, nullable=True)
    message_status = Column(String, nullable=False, default="completed", index=True)
    token_input = Column(Integer, nullable=False, default=0)
    token_output = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer, nullable=False, default=0)
    trace_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    session = relationship("AgentSession", back_populates="messages")
    tool_calls = relationship("AgentToolCall", back_populates="message")


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=False, index=True)
    memory_type = Column(String, nullable=False, default="session_summary", index=True)
    summary = Column(Text, nullable=False)
    source_message_ids = Column(JSON, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    session = relationship("AgentSession", back_populates="memories")


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("agent_messages.id"), nullable=True, index=True)
    tool_name = Column(String, nullable=False, index=True)
    tool_args = Column(JSON, nullable=True)
    tool_result = Column(JSON, nullable=True)
    success = Column(Boolean, nullable=False, default=True, index=True)
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    session = relationship("AgentSession", back_populates="tool_calls")
    message = relationship("AgentMessage", back_populates="tool_calls")


class AgentTurnState(Base):
    __tablename__ = "agent_turn_states"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=False, index=True)
    user_message_id = Column(Integer, ForeignKey("agent_messages.id"), nullable=False, index=True)
    assistant_message_id = Column(Integer, ForeignKey("agent_messages.id"), nullable=True, index=True)
    trace_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="prepared", index=True)
    goal = Column(String, nullable=True)
    request_analysis = Column(JSON, nullable=True)
    selected_tools = Column(JSON, nullable=True)
    tool_snapshots = Column(JSON, nullable=True)
    plan_draft = Column(JSON, nullable=True)
    plan_final = Column(JSON, nullable=True)
    execution_state = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    session = relationship("AgentSession", back_populates="turn_states")


class AgentToolCache(Base):
    __tablename__ = "agent_tool_cache"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=True, index=True)
    tool_name = Column(String, nullable=False, index=True)
    cache_key = Column(String, nullable=False, index=True)
    tool_args = Column(JSON, nullable=True)
    tool_result = Column(JSON, nullable=True)
    trace_id = Column(String, nullable=True, index=True)
    hit_count = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=True, index=True)
    last_used_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    session = relationship("AgentSession", back_populates="tool_cache_entries")


class AgentActionLog(Base):
    __tablename__ = "agent_action_logs"

    id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=False, index=True)
    related_task_id = Column(String, ForeignKey("agent_tasks.id"), nullable=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    tool_name = Column(String, nullable=False, index=True)
    tool_type = Column(String, nullable=False, default="write")
    tool_args = Column(JSON, nullable=False)
    risk_level = Column(String, nullable=False, default="medium", index=True)
    approval_status = Column(String, nullable=False, default="pending", index=True)
    execution_status = Column(String, nullable=False, default="pending", index=True)
    triggered_by = Column(String, nullable=False, default="user_request", index=True)
    preview_summary = Column(Text, nullable=True)
    preview_context = Column(JSON, nullable=True)
    affected_ids = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    verification_status = Column(String, nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    session = relationship("AgentSession", back_populates="action_logs")


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=False, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    related_turn_state_id = Column(Integer, ForeignKey("agent_turn_states.id"), nullable=True, index=True)
    title = Column(String, nullable=False, default="New Task")
    goal = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="ready", index=True)
    priority = Column(String, nullable=False, default="medium", index=True)
    source = Column(String, nullable=False, default="plan", index=True)
    plan_summary = Column(Text, nullable=True)
    plan_bundle = Column(JSON, nullable=True)
    action_suggestions = Column(JSON, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_transition_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    session = relationship("AgentSession", back_populates="tasks")
    events = relationship("AgentTaskEvent", back_populates="task", cascade="all, delete-orphan")


class AgentTaskEvent(Base):
    __tablename__ = "agent_task_events"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, ForeignKey("agent_tasks.id"), nullable=False, index=True)
    session_id = Column(String, ForeignKey("agent_sessions.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False, default="created", index=True)
    from_status = Column(String, nullable=True, index=True)
    to_status = Column(String, nullable=True, index=True)
    note = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    task = relationship("AgentTask", back_populates="events")
