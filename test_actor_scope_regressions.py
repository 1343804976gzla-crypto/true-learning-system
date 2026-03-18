from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routers.quiz_batch as quiz_batch
from agent_models import AgentMemory, AgentSession
from learning_tracking_models import DailyLearningLog, LearningSession, QuestionRecord, WrongAnswerV2
from main import app
from models import Base, Chapter, DailyUpload, get_db
from routers.learning_tracking import rebuild_daily_logs
from services.agent_actions import UpdateWrongAnswerStatusArgs, _prepare_update_wrong_answer_status
from services.agent_runtime import list_sessions


@pytest.fixture(autouse=True)
def disable_single_user_mode(monkeypatch):
    from services.data_identity import clear_identity_caches_for_tests

    monkeypatch.setenv("SINGLE_USER_MODE", "false")
    clear_identity_caches_for_tests()
    try:
        yield
    finally:
        monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
        clear_identity_caches_for_tests()


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory):
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


def _seed_tracking_row(
    session,
    *,
    session_id: str,
    device_id: str,
    started_at: datetime,
    title: str,
) -> None:
    session.add(
        LearningSession(
            id=session_id,
            device_id=device_id,
            session_type="detail_practice",
            title=title,
            status="completed",
            total_questions=1,
            correct_count=1,
            wrong_count=0,
            score=100,
            accuracy=1.0,
            started_at=started_at,
            completed_at=started_at + timedelta(minutes=5),
            duration_seconds=300,
        )
    )
    session.flush()
    session.add(
        QuestionRecord(
            session_id=session_id,
            device_id=device_id,
            question_index=0,
            question_type="A1",
            difficulty="基础",
            question_text=f"Question for {title}",
            options={"A": "1", "B": "2"},
            correct_answer="A",
            user_answer="A",
            is_correct=True,
            confidence="sure",
            key_point=title,
            answered_at=started_at + timedelta(minutes=1),
            time_spent_seconds=30,
        )
    )


def test_history_and_tracking_routes_scope_by_device(client, session_factory):
    with session_factory() as db:
        db.add(Chapter(id="chap-a", book="Book A", edition="1", chapter_number="1", chapter_title="A", concepts=[]))
        db.add(Chapter(id="chap-b", book="Book B", edition="1", chapter_number="1", chapter_title="B", concepts=[]))
        db.add_all(
            [
                DailyUpload(
                    device_id="device-a",
                    date=date(2026, 3, 18),
                    raw_content="a",
                    ai_extracted={"book": "Book A", "chapter_title": "A"},
                ),
                DailyUpload(
                    device_id="device-b",
                    date=date(2026, 3, 18),
                    raw_content="b",
                    ai_extracted={"book": "Book B", "chapter_title": "B"},
                ),
            ]
        )
        _seed_tracking_row(
            db,
            session_id="sess-a",
            device_id="device-a",
            started_at=datetime(2026, 3, 18, 9, 0, 0),
            title="Alpha",
        )
        _seed_tracking_row(
            db,
            session_id="sess-b",
            device_id="device-b",
            started_at=datetime(2026, 3, 18, 10, 0, 0),
            title="Beta",
        )
        db.commit()

    headers = {"x-tls-device-id": "device-a"}
    history = client.get("/api/history/stats", headers=headers)
    sessions = client.get("/api/tracking/sessions?limit=10", headers=headers)
    archive = client.get("/api/tracking/knowledge-archive", headers=headers)

    assert history.status_code == 200
    assert history.json()["total_uploads"] == 1
    assert history.json()["book_distribution"] == {"Book A": 1}

    assert sessions.status_code == 200
    assert sessions.json()["total"] == 1
    assert [item["title"] for item in sessions.json()["sessions"]] == ["Alpha"]

    assert archive.status_code == 200
    assert archive.json()["total_questions"] == 1
    assert [item["name"] for item in archive.json()["knowledge_points"]] == ["Alpha"]


def test_legacy_local_default_data_is_visible_and_daily_logs_deduped(client, session_factory):
    local_device = "local-current-device"
    day = date(2026, 3, 18)
    with session_factory() as db:
        db.add_all(
            [
                DailyUpload(
                    device_id="local-default",
                    date=day - timedelta(days=1),
                    raw_content="legacy",
                    ai_extracted={"book": "Legacy", "chapter_title": "Legacy Chapter"},
                ),
                DailyUpload(
                    device_id=local_device,
                    date=day,
                    raw_content="current",
                    ai_extracted={"book": "Current", "chapter_title": "Current Chapter"},
                ),
            ]
        )
        _seed_tracking_row(
            db,
            session_id="sess-legacy",
            device_id="local-default",
            started_at=datetime(2026, 3, 18, 8, 0, 0),
            title="LegacySession",
        )
        _seed_tracking_row(
            db,
            session_id="sess-current",
            device_id=local_device,
            started_at=datetime(2026, 3, 18, 9, 0, 0),
            title="CurrentSession",
        )
        db.commit()
        rebuild_daily_logs(db)

    headers = {"x-tls-device-id": local_device}
    uploads = client.get("/api/history/uploads?days=30", headers=headers)
    sessions = client.get("/api/tracking/sessions?limit=10", headers=headers)
    daily_logs = client.get("/api/tracking/daily-logs?days=30", headers=headers)

    assert uploads.status_code == 200
    assert uploads.json()["total"] == 2

    assert sessions.status_code == 200
    assert sessions.json()["total"] == 2
    assert {item["title"] for item in sessions.json()["sessions"]} == {"LegacySession", "CurrentSession"}

    assert daily_logs.status_code == 200
    assert len(daily_logs.json()["logs"]) == 1
    assert daily_logs.json()["logs"][0]["total_sessions"] == 2


def test_single_user_mode_collapses_local_device_identity(monkeypatch):
    from services import data_identity

    monkeypatch.setenv("SINGLE_USER_MODE", "true")
    data_identity.clear_identity_caches_for_tests()

    aliases = data_identity.build_device_scope_aliases(None, "local-single-user-current")
    actor_scope = data_identity.resolve_request_actor_scope(device_id="local-single-user-current")

    assert "local-default" in aliases
    assert "local-single-user-current" in aliases
    assert actor_scope["paper_device_id"] == "local-default"
    assert "device:local-default" in actor_scope["actor_keys"]

    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    data_identity.clear_identity_caches_for_tests()


class _FakeBatchQuizService:
    async def generate_exam_paper(self, uploaded_content: str, num_questions: int):
        return {
            "paper_title": "Scoped Exam",
            "total_questions": num_questions,
            "chapter_prediction": {"chapter_id": "chap-a", "book": "Book A", "chapter_title": "A"},
            "difficulty_distribution": {"基础": num_questions},
            "summary": {"coverage": "single"},
            "questions": [
                {
                    "id": "q1",
                    "type": "A1",
                    "difficulty": "基础",
                    "question": "Scoped question",
                    "options": {"A": "1", "B": "2"},
                    "correct_answer": "A",
                    "explanation": "Scoped explanation",
                    "key_point": "Scoped key point",
                }
            ][:num_questions],
        }

    def grade_paper(self, questions, answers, confidence):
        return {
            "score": 100,
            "correct_count": len(questions),
            "wrong_count": 0,
            "total": len(questions),
            "wrong_by_difficulty": {"基础": 0},
            "confidence_analysis": {"sure": 0, "unsure": 0, "no": 0},
            "details": [
                {
                    "id": 1,
                    "type": "A1",
                    "difficulty": "基础",
                    "user_answer": "A",
                    "correct_answer": "A",
                    "is_correct": True,
                    "confidence": "sure",
                    "explanation": "Scoped explanation",
                    "key_point": "Scoped key point",
                    "related_questions": "",
                }
            ],
            "weak_points": [],
            "analysis": "ok",
        }

    def _infer_chapter_prediction(self, content: str):
        return {"chapter_id": "chap-a"}


def test_batch_exam_state_is_scoped_and_survives_cache_clear(client, session_factory, monkeypatch):
    with session_factory() as db:
        db.add(Chapter(id="chap-a", book="Book A", edition="1", chapter_number="1", chapter_title="A", concepts=[]))
        db.commit()

    monkeypatch.setattr(quiz_batch, "get_quiz_service", lambda: _FakeBatchQuizService())

    generate = client.post(
        "/api/quiz/batch/generate/chap-a",
        headers={"x-tls-device-id": "device-a"},
        json={"uploaded_content": "x" * 120, "num_questions": 1},
    )
    assert generate.status_code == 200
    exam_id = generate.json()["exam_id"]

    quiz_batch._exam_cache.clear()
    quiz_batch._detail_cache.clear()

    own_session = client.get(f"/api/quiz/batch/session/{exam_id}", headers={"x-tls-device-id": "device-a"})
    other_session = client.get(f"/api/quiz/batch/session/{exam_id}", headers={"x-tls-device-id": "device-b"})

    assert own_session.status_code == 200
    assert other_session.status_code == 404


def test_agent_legacy_alias_supports_sessions_actions_and_memory(session_factory):
    local_device = "local-current-agent"
    legacy_session_id = f"legacy-{uuid4().hex}"
    current_session_id = f"current-{uuid4().hex}"
    now = datetime.now()

    with session_factory() as db:
        db.add_all(
            [
                AgentSession(
                    id=legacy_session_id,
                    device_id="local-default",
                    title="Legacy Agent Session",
                    agent_type="tutor",
                    status="active",
                ),
                AgentSession(
                    id=current_session_id,
                    device_id=local_device,
                    title="Current Agent Session",
                    agent_type="tutor",
                    status="active",
                ),
                AgentMemory(
                    user_id=None,
                    session_id=legacy_session_id,
                    memory_type="user_goal",
                    summary="legacy memory summary",
                    source_message_ids=[],
                    created_at=now,
                ),
                WrongAnswerV2(
                    device_id="local-default",
                    question_fingerprint=f"legacy-wa-{uuid4().hex}",
                    question_text="legacy wrong answer",
                    options={"A": "1", "B": "2"},
                    correct_answer="A",
                    key_point="legacy-key-point",
                    question_type="A1",
                    difficulty="基础",
                    error_count=1,
                    encounter_count=1,
                    severity_tag="critical",
                    mastery_status="active",
                    first_wrong_at=now,
                    last_wrong_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.commit()

        sessions = list_sessions(db, device_id=local_device, status="all", limit=10)
        preview = _prepare_update_wrong_answer_status(
            db,
            UpdateWrongAnswerStatusArgs(wrong_answer_ids=[1], target_status="archived", reason="regression"),
            user_id=None,
            device_id=local_device,
        )

    assert {session.title for session in sessions} == {"Legacy Agent Session", "Current Agent Session"}
    assert preview.preview_summary.startswith("将 1 道错题更新为 archived")
