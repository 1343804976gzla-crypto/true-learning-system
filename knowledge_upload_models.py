from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database.domains import ContentBase, content_engine

Base = ContentBase


class KnowledgeUploadRecord(Base):
    __tablename__ = "knowledge_upload_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)

    source_type = Column(String, nullable=False, default="text_paste")
    source_name = Column(String, nullable=True)
    raw_text_snapshot = Column(Text, nullable=False)
    preview_snapshot = Column(JSON, nullable=True)
    saved_note_count = Column(Integer, default=0)
    pending_item_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    sources = relationship("KnowledgePointSource", back_populates="upload", cascade="all, delete-orphan")
    pending_items = relationship(
        "KnowledgePendingClassification",
        back_populates="upload",
        cascade="all, delete-orphan",
    )


class KnowledgePointNote(Base):
    __tablename__ = "knowledge_point_notes"
    __table_args__ = (
        UniqueConstraint(
            "actor_key",
            "chapter_id",
            "concept_key",
            name="uq_knowledge_point_notes_actor_chapter_key",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)

    chapter_id = Column(String, ForeignKey("chapters.id"), nullable=False, index=True)
    concept_key = Column(String, nullable=False)
    concept_name = Column(String, nullable=False)
    note_summary = Column(Text, nullable=True)
    note_body = Column(Text, nullable=False)
    source_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    chapter = relationship("Chapter")
    sources = relationship("KnowledgePointSource", back_populates="note", cascade="all, delete-orphan")


class KnowledgePointSource(Base):
    __tablename__ = "knowledge_point_sources"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)

    note_id = Column(Integer, ForeignKey("knowledge_point_notes.id"), nullable=False, index=True)
    upload_id = Column(Integer, ForeignKey("knowledge_upload_records.id"), nullable=False, index=True)
    source_type = Column(String, nullable=False, default="text_paste")
    source_name = Column(String, nullable=True)
    source_excerpt = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now)

    note = relationship("KnowledgePointNote", back_populates="sources")
    upload = relationship("KnowledgeUploadRecord", back_populates="sources")


class KnowledgePendingClassification(Base):
    __tablename__ = "knowledge_pending_classifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)

    upload_id = Column(Integer, ForeignKey("knowledge_upload_records.id"), nullable=False, index=True)
    source_type = Column(String, nullable=False, default="text_paste")
    source_name = Column(String, nullable=True)
    book_hint = Column(String, nullable=True)
    chapter_number_hint = Column(String, nullable=True)
    chapter_title_hint = Column(String, nullable=True)
    chapter_candidates = Column(JSON, nullable=True)
    knowledge_points = Column(JSON, nullable=False)
    source_excerpt = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)
    resolved_chapter_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    resolved_at = Column(DateTime, nullable=True)

    upload = relationship("KnowledgeUploadRecord", back_populates="pending_items")


class KnowledgeDailyReport(Base):
    __tablename__ = "knowledge_daily_reports"
    __table_args__ = (
        UniqueConstraint("actor_key", "report_date", name="uq_knowledge_daily_reports_actor_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)

    report_date = Column(Date, nullable=False, index=True, default=date.today)
    snapshot = Column(JSON, nullable=False)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def create_knowledge_upload_tables() -> None:
    Base.metadata.create_all(
        bind=content_engine,
        tables=[
            KnowledgeUploadRecord.__table__,
            KnowledgePointNote.__table__,
            KnowledgePointSource.__table__,
            KnowledgePendingClassification.__table__,
            KnowledgeDailyReport.__table__,
        ],
    )
