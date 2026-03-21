"""HTTP-layer regression tests for POST /api/quiz/batch/generate/{chapter_id}.

Covers the full request→service→response→DB-persist chain with a mocked
QuizService so no real LLM calls are made.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import CoreBase, ContentBase, RuntimeBase, ReviewBase
from database.audit import AUDIT_CHANGE_LOG_DDL
from learning_tracking_models import BatchExamState
from models import get_db
import routers.quiz_batch as quiz_batch_module
import services.data_identity as data_identity_module


def _sample_generate_result(num_questions: int = 10) -> dict:
    questions = []
    for i in range(1, num_questions + 1):
        questions.append({
            "id": i,
            "type": "A1",
            "difficulty": "基础" if i <= 6 else ("提高" if i <= 9 else "难题"),
            "question": f"Question {i}",
            "options": {"A": "Opt A", "B": "Opt B", "C": "Opt C", "D": "Opt D", "E": "Opt E"},
            "correct_answer": "A",
            "explanation": f"Explanation {i}",
            "key_point": f"KP{((i - 1) // 2) + 1}",
        })
    return {
        "paper_title": "Test Paper",
        "total_questions": num_questions,
        "difficulty_distribution": {"基础": 6, "提高": 3, "难题": 1},
        "questions": questions,
        "knowledge_points": [f"KP{j}" for j in range(1, 6)],
        "summary": {"coverage": "full"},
        "chapter_prediction": {
            "book": "内科学",
            "chapter_id": "internal_ch2",
            "chapter_title": "心力衰竭",
            "confidence": "high",
        },
    }


class _FakeQuizService:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls = []

    async def generate_exam_paper(self, uploaded_content, num_questions=10):
        self.calls.append({"uploaded_content": uploaded_content, "num_questions": num_questions})
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for metadata in (CoreBase.metadata, ContentBase.metadata, RuntimeBase.metadata, ReviewBase.metadata):
        metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(AUDIT_CHANGE_LOG_DDL)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        for metadata in (ReviewBase.metadata, RuntimeBase.metadata, ContentBase.metadata, CoreBase.metadata):
            metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory, monkeypatch):
    app = FastAPI()
    app.include_router(quiz_batch_module.router)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        data_identity_module, "get_request_identity",
        lambda: ("test-user", "test-device"),
    )
    # Clear exam cache between tests
    quiz_batch_module._exam_cache.clear()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    quiz_batch_module._exam_cache.clear()


# ── Happy path ──


def test_generate_returns_200_with_correct_response_structure(client, monkeypatch):
    fake_service = _FakeQuizService(result=_sample_generate_result(10))
    monkeypatch.setattr(quiz_batch_module, "get_quiz_service", lambda: fake_service)

    response = client.post(
        "/api/quiz/batch/generate/internal_ch2",
        json={"uploaded_content": "A" * 200, "num_questions": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exam_id"]
    assert payload["paper_title"] == "Test Paper"
    assert payload["total_questions"] == 10
    assert len(payload["questions"]) == 10
    assert payload["questions"][0]["question"] == "Question 1"
    assert payload["chapter_prediction"]["book"] == "内科学"
    assert len(payload["knowledge_points"]) >= 1
    assert fake_service.calls[0]["num_questions"] == 10


def test_generate_persists_exam_state_to_database(client, session_factory, monkeypatch):
    fake_service = _FakeQuizService(result=_sample_generate_result(5))
    monkeypatch.setattr(quiz_batch_module, "get_quiz_service", lambda: fake_service)

    response = client.post(
        "/api/quiz/batch/generate/internal_ch2",
        json={"uploaded_content": "B" * 200, "num_questions": 5},
    )

    assert response.status_code == 200
    exam_id = response.json()["exam_id"]

    with session_factory() as db:
        state = db.query(BatchExamState).filter(BatchExamState.id == exam_id).first()
        assert state is not None
        assert state.num_questions == 5
        assert state.chapter_id == "internal_ch2"
        assert state.submitted_at is None


# ── Validation ──


def test_generate_rejects_short_content_with_400(client, monkeypatch):
    fake_service = _FakeQuizService(result=_sample_generate_result())
    monkeypatch.setattr(quiz_batch_module, "get_quiz_service", lambda: fake_service)

    response = client.post(
        "/api/quiz/batch/generate/ch1",
        json={"uploaded_content": "too short", "num_questions": 10},
    )

    assert response.status_code == 400
    assert "100" in response.json()["detail"]
    assert len(fake_service.calls) == 0


def test_generate_normalizes_invalid_num_questions_to_10(client, monkeypatch):
    fake_service = _FakeQuizService(result=_sample_generate_result(10))
    monkeypatch.setattr(quiz_batch_module, "get_quiz_service", lambda: fake_service)

    response = client.post(
        "/api/quiz/batch/generate/ch1",
        json={"uploaded_content": "C" * 200, "num_questions": 7},
    )

    assert response.status_code == 200
    assert fake_service.calls[0]["num_questions"] == 10


# ── Error handling ──


def test_generate_returns_504_on_quiz_timeout(client, monkeypatch):
    fake_service = _FakeQuizService(
        error=RuntimeError("QUIZ_TIMEOUT|生成超时，内容过长")
    )
    monkeypatch.setattr(quiz_batch_module, "get_quiz_service", lambda: fake_service)

    response = client.post(
        "/api/quiz/batch/generate/ch1",
        json={"uploaded_content": "D" * 200, "num_questions": 10},
    )

    assert response.status_code == 504
    assert "超时" in response.json()["detail"]


def test_generate_returns_500_on_unexpected_error(client, monkeypatch):
    fake_service = _FakeQuizService(error=RuntimeError("unexpected boom"))
    monkeypatch.setattr(quiz_batch_module, "get_quiz_service", lambda: fake_service)

    response = client.post(
        "/api/quiz/batch/generate/ch1",
        json={"uploaded_content": "E" * 200, "num_questions": 10},
    )

    assert response.status_code == 500
    assert "unexpected boom" in response.json()["detail"]
