"""
True Learning System core SQLAlchemy models.
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database.domains import CORE_DATABASE_URL, AppSessionLocal, CoreBase, core_engine, get_db

DATABASE_URL = CORE_DATABASE_URL
engine = core_engine
SessionLocal = AppSessionLocal
Base = CoreBase


class DailyUpload(Base):
    __tablename__ = "daily_uploads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    date = Column(Date, nullable=False, index=True)
    raw_content = Column(Text, nullable=False)
    ai_extracted = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(String, primary_key=True, index=True)
    book = Column(String, nullable=False, index=True)
    edition = Column(String)
    chapter_number = Column(String, nullable=False)
    chapter_title = Column(String, nullable=False)
    content_summary = Column(Text)
    concepts = Column(JSON)
    first_uploaded = Column(Date)
    last_reviewed = Column(Date)

    concept_mastery_records = relationship("ConceptMastery", back_populates="chapter")


class ConceptMastery(Base):
    __tablename__ = "concept_mastery"

    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    concept_id = Column(String, primary_key=True, index=True)
    chapter_id = Column(String, ForeignKey("chapters.id"), nullable=False)
    name = Column(String, nullable=False)
    retention = Column(Float, default=0.0)
    understanding = Column(Float, default=0.0)
    application = Column(Float, default=0.0)
    last_tested = Column(Date)
    next_review = Column(Date, index=True)

    chapter = relationship("Chapter", back_populates="concept_mastery_records")
    test_records = relationship("TestRecord", back_populates="concept")
    wrong_answers = relationship("WrongAnswer", back_populates="concept")


class TestRecord(Base):
    __tablename__ = "test_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    concept_id = Column(String, ForeignKey("concept_mastery.concept_id"))
    test_type = Column(String)

    ai_question = Column(Text)
    ai_options = Column(JSON)
    ai_correct_answer = Column(String)
    ai_explanation = Column(Text)

    user_answer = Column(String)
    confidence = Column(String)

    is_correct = Column(Boolean)
    ai_feedback = Column(Text)
    weak_points = Column(JSON)

    score = Column(Integer)
    tested_at = Column(DateTime, default=datetime.now)

    concept = relationship("ConceptMastery", back_populates="test_records")


class FeynmanSession(Base):
    __tablename__ = "feynman_sessions"

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(String, nullable=False)
    concept_name = Column(String)
    dialogue = Column(JSON)
    passed = Column(Boolean, default=False)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime)


class ConceptLink(Base):
    __tablename__ = "concept_links"

    from_concept = Column(String, nullable=False)
    to_concept = Column(String, nullable=False)
    link_type = Column(String)
    strength = Column(Float, default=1.0)
    user_created = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        PrimaryKeyConstraint("from_concept", "to_concept"),
    )


class Variation(Base):
    __tablename__ = "variations"

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(String, nullable=False, index=True)
    level = Column(Integer)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    explanation = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class WrongAnswer(Base):
    __tablename__ = "wrong_answers"

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(String, ForeignKey("concept_mastery.concept_id"), nullable=False)

    question = Column(Text, nullable=False)
    options = Column(JSON)
    correct_answer = Column(String, nullable=False)
    user_answer = Column(String, nullable=False)
    explanation = Column(Text)

    error_type = Column(String)
    weak_points = Column(JSON)

    review_count = Column(Integer, default=0)
    last_reviewed = Column(DateTime)
    next_review = Column(Date, default=date.today)
    mastery_level = Column(Integer, default=0)
    is_mastered = Column(Boolean, default=False)

    concept = relationship("ConceptMastery", back_populates="wrong_answers")
    created_at = Column(DateTime, default=datetime.now)


class QuizSession(Base):
    __tablename__ = "quiz_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_type = Column(String, default="practice")
    chapter_id = Column(String, ForeignKey("chapters.id"))
    questions = Column(JSON)
    answers = Column(JSON)
    total_questions = Column(Integer, default=10)
    correct_count = Column(Integer, default=0)
    score = Column(Integer, default=0)
    started_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime)

    chapter = relationship("Chapter")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    print("Database initialized")


if __name__ == "__main__":
    init_db()
