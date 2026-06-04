from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from learning_tracking_models import KnowledgeStateProfile, LearningSession, QuestionRecord, SessionStatus
from models import get_db
import routers.learning_tracking as tracking_module


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for metadata in (CoreBase.metadata, ContentBase.metadata, RuntimeBase.metadata, ReviewBase.metadata):
        metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        for metadata in (ReviewBase.metadata, RuntimeBase.metadata, ContentBase.metadata, CoreBase.metadata):
            metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory):
    app = FastAPI()
    app.include_router(tracking_module.router)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _completed_session(session_id: str = "session-illusion") -> LearningSession:
    return LearningSession(
        id=session_id,
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


def test_analyze_knowledge_state_returns_fallback_illusion_card(client, session_factory):
    with session_factory() as db:
        session = _completed_session()
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
        db.commit()

    response = client.post(
        "/api/tracking/knowledge-state/analyze",
        json={"session_id": "session-illusion", "scope_key": "u:u1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fallback_used"] is True
    assert payload["cards"][0]["current_state"] == "Illusion of Competence"


def test_latest_knowledge_state_returns_existing_profile(client, session_factory):
    with session_factory() as db:
        db.add(
            KnowledgeStateProfile(
                scope_key="u:u1",
                user_id="u1",
                device_id="d1",
                knowledge_point="Resting potential mechanism",
                current_state="Illusion of Competence",
                state_confidence="medium",
                stability_score=0.25,
                calibration_score=0.5,
                last_transition="first_observed",
                last_session_id="session-illusion",
                evidence_snapshot={"attempt_count": 1},
            )
        )
        db.commit()

    response = client.get(
        "/api/tracking/knowledge-state/latest",
        params={"scope_key": "u:u1", "knowledge_point": "Resting potential mechanism"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope_key"] == "u:u1"
    assert payload["knowledge_point"] == "Resting potential mechanism"
    assert payload["current_state"] == "Illusion of Competence"
    assert payload["last_session_id"] == "session-illusion"
    assert payload["evidence_snapshot"] == {"attempt_count": 1}


def test_analyze_knowledge_state_returns_404_for_missing_session(client):
    response = client.post(
        "/api/tracking/knowledge-state/analyze",
        json={"session_id": "missing-session", "scope_key": "u:u1"},
    )

    assert response.status_code == 404
