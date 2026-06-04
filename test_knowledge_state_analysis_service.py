from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from learning_tracking_models import (
    KnowledgeStateEvent,
    KnowledgeStateProfile,
    LearningSession,
    QuestionRecord,
    SessionStatus,
    WrongAnswerV2,
)
from services.knowledge_state_analysis import analyze_completed_session


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for metadata in (CoreBase.metadata, ContentBase.metadata, RuntimeBase.metadata, ReviewBase.metadata):
        metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session(), engine


def _close_db(db, engine) -> None:
    db.close()
    for metadata in (ReviewBase.metadata, RuntimeBase.metadata, ContentBase.metadata, CoreBase.metadata):
        metadata.drop_all(engine)
    engine.dispose()


def test_knowledge_state_models_create_runtime_tables():
    db, engine = _make_db()
    try:
        profile = KnowledgeStateProfile(
            scope_key="scope-1",
            user_id="user-1",
            device_id="device-1",
            knowledge_point="Cardiac output regulation",
            current_state="Conscious Weakness",
            state_confidence="medium",
            stability_score=0.42,
            calibration_score=0.35,
            last_transition="first_observed",
            last_session_id="session-1",
            evidence_snapshot={"attempt_count": 3},
        )
        db.add(profile)
        db.flush()
        event = KnowledgeStateEvent(
            profile_id=profile.id,
            scope_key="scope-1",
            session_id="session-1",
            knowledge_point="Cardiac output regulation",
            previous_state=None,
            current_state="Conscious Weakness",
            transition="first_observed",
            state_confidence="medium",
            evidence_packet={"knowledge_points": []},
            llm_analysis={"fallback_used": True},
            guardrail_flags=["confidence_missing"],
        )
        db.add(event)
        db.commit()

        assert db.query(KnowledgeStateProfile).count() == 1
        assert db.query(KnowledgeStateEvent).count() == 1
        assert profile.events[0].transition == "first_observed"
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_detects_illusion_of_competence_and_persists_event():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-illusion",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=0,
            wrong_count=1,
            accuracy=0.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Which ion drives resting membrane potential?",
                options={"A": "K+", "B": "Na+"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="sure",
                key_point="Resting potential mechanism",
                answered_at=datetime.now(),
                time_spent_seconds=12,
            )
        )
        db.add(
            WrongAnswerV2(
                user_id="u1",
                device_id="d1",
                scope_key="u:u1",
                question_fingerprint="fp-1",
                question_text="Which ion drives resting membrane potential?",
                options={"A": "K+", "B": "Na+"},
                correct_answer="A",
                key_point="Resting potential mechanism",
                error_count=2,
                encounter_count=2,
                retry_count=0,
                severity_tag="critical",
                mastery_status="active",
            )
        )
        db.commit()

        result = analyze_completed_session(db, "session-illusion", scope_key="u:u1", llm_client=None)

        assert result["fallback_used"] is True
        assert result["cards"][0]["knowledge_point"] == "Resting potential mechanism"
        assert result["cards"][0]["current_state"] == "Illusion of Competence"
        assert result["cards"][0]["transition"] == "first_observed"
        assert result["cards"][0]["state_confidence"] == "medium"
        assert db.query(KnowledgeStateProfile).one().current_state == "Illusion of Competence"
        assert db.query(KnowledgeStateEvent).one().guardrail_flags == ["sure_wrong"]
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_detects_calibration_improved_transition():
    db, engine = _make_db()
    try:
        db.add(
            KnowledgeStateProfile(
                scope_key="u:u1",
                user_id="u1",
                device_id="d1",
                knowledge_point="Afterload compensation",
                current_state="Illusion of Competence",
                state_confidence="medium",
                last_transition="first_observed",
                last_session_id="old-session",
            )
        )
        session = LearningSession(
            id="session-calibrated",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=0,
            wrong_count=1,
            accuracy=0.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Afterload rises. What happens first?",
                options={"A": "Shortening velocity falls", "B": "Velocity rises"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="unsure",
                key_point="Afterload compensation",
                answered_at=datetime.now(),
            )
        )
        db.commit()

        result = analyze_completed_session(db, "session-calibrated", scope_key="u:u1", llm_client=None)

        assert result["cards"][0]["previous_state"] == "Illusion of Competence"
        assert result["cards"][0]["current_state"] == "Conscious Weakness"
        assert result["cards"][0]["transition"] == "calibration_improved"
        assert "自信判断" in result["cards"][0]["interesting_insight"]
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_marks_missing_confidence_low_reliability():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-missing-confidence",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=1,
            wrong_count=0,
            accuracy=1.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="What is preload?",
                options={"A": "End diastolic stretch", "B": "Pressure after ejection"},
                correct_answer="A",
                user_answer="A",
                is_correct=True,
                confidence=None,
                key_point="Preload definition",
                answered_at=datetime.now(),
            )
        )
        db.commit()

        result = analyze_completed_session(db, "session-missing-confidence", scope_key="u:u1", llm_client=None)

        assert result["cards"][0]["state_confidence"] == "low"
        assert "confidence_missing" in result["cards"][0]["guardrail_flags"]
        assert result["low_reliability_notes"]
    finally:
        _close_db(db, engine)
