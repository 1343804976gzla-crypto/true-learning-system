"""SQLAlchemy models for API Hub usage tracking, health logs, and pricing."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, Float, Index, Integer, String, Text

from database.domains import RuntimeBase


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApiHubUsage(RuntimeBase):
    """Per-call usage records (structured, queryable supplement to JSONL)."""

    __tablename__ = "api_hub_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    logical_call_id = Column(Text, default="")
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    pool_name = Column(String(64), default="")
    status = Column(String(16), nullable=False)  # 'success' | 'error'
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    elapsed_ms = Column(Integer, default=0)
    caller = Column(String(256), default="")
    request_path = Column(String(512), default="")
    created_at = Column(String(40), default=_utc_now_iso)

    __table_args__ = (
        Index("ix_usage_created", "created_at"),
        Index("ix_usage_provider", "provider"),
    )


class ApiHubHealthLog(RuntimeBase):
    """Provider health snapshots (periodic)."""

    __tablename__ = "api_hub_health_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)  # 'healthy' | 'degraded' | 'down'
    success_rate = Column(Float)
    avg_latency_ms = Column(Integer)
    sample_count = Column(Integer)
    checked_at = Column(String(40), default=_utc_now_iso)


class ApiHubPrice(RuntimeBase):
    """Cost price configuration per provider/model."""

    __tablename__ = "api_hub_price"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    input_per_1k = Column(Float, nullable=False)
    output_per_1k = Column(Float, nullable=False)
    updated_at = Column(String(40), default=_utc_now_iso)

    __table_args__ = (
        Index("ix_price_provider_model", "provider", "model", unique=True),
    )
