from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from learning_tracking_models import LearningSession, QuestionRecord, SessionStatus, WrongAnswerV2, make_fingerprint
from models import Chapter, get_db
import routers.learning_tracking as tracking_module


def _make_session(
    *,
    session_id: str,
    started_at: datetime,
    session_type: str = "exam",
    chapter_id: str | None = None,
    title: str | None = None,
    total_questions: int = 0,
    score: int = 0,
    duration_seconds: int = 0,
):
    return LearningSession(
        id=session_id,
        session_type=session_type,
        chapter_id=chapter_id,
        title=title or session_id,
        description=title or session_id,
        status=SessionStatus.COMPLETED,
        started_at=started_at,
        completed_at=started_at + timedelta(minutes=10),
        total_questions=total_questions,
        answered_questions=total_questions,
        correct_count=0,
        wrong_count=0,
        sure_count=0,
        unsure_count=0,
        no_count=0,
        duration_seconds=duration_seconds,
        score=score,
        accuracy=0,
    )


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


def test_review_data_progress_board_and_markdown_export(client, session_factory):
    now = datetime.now()
    current_started = now - timedelta(days=1)
    previous_started = now - timedelta(days=8)

    with session_factory() as db:
        db.add(
            Chapter(
                id="med_ch1",
                book="Medicine",
                edition="1",
                chapter_number="1",
                chapter_title="Cardiology",
            )
        )
        db.add_all(
            [
                _make_session(
                    session_id="session-current",
                    started_at=current_started,
                    session_type="exam",
                    chapter_id="med_ch1",
                    title="Current Week Review",
                    total_questions=2,
                    score=60,
                    duration_seconds=240,
                ),
                _make_session(
                    session_id="session-previous",
                    started_at=previous_started,
                    session_type="detail_practice",
                    chapter_id="med_ch1",
                    title="Previous Week Drill",
                    total_questions=1,
                    score=100,
                    duration_seconds=120,
                ),
            ]
        )
        db.add_all(
            [
                QuestionRecord(
                    session_id="session-current",
                    question_index=0,
                    question_type="A1",
                    difficulty="基础",
                    question_text="What is the first-line management for acute heart failure?",
                    options={"A": "Diuretics", "B": "Antibiotics"},
                    correct_answer="A",
                    user_answer="B",
                    is_correct=False,
                    confidence="unsure",
                    explanation="Initial management requires fluid offloading and oxygen support.",
                    key_point="Heart failure management",
                    answered_at=current_started + timedelta(minutes=1),
                    time_spent_seconds=20,
                ),
                QuestionRecord(
                    session_id="session-current",
                    question_index=0,
                    question_type="A1",
                    difficulty="基础",
                    question_text="What is the first-line management for acute heart failure?",
                    options={"A": "Diuretics", "B": "Antibiotics"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    explanation="Initial management requires fluid offloading and oxygen support.",
                    key_point="Heart failure management",
                    answered_at=current_started + timedelta(minutes=2),
                    time_spent_seconds=18,
                ),
                QuestionRecord(
                    session_id="session-current",
                    question_index=1,
                    question_type="A2",
                    difficulty="提高",
                    question_text="Which finding suggests pulmonary congestion?",
                    options={"A": "Crackles", "B": "Mydriasis"},
                    correct_answer="A",
                    user_answer="B",
                    is_correct=False,
                    confidence="no",
                    explanation="Pulmonary congestion commonly presents with crackles.",
                    key_point="Pulmonary congestion",
                    answered_at=current_started + timedelta(minutes=3),
                    time_spent_seconds=25,
                ),
                QuestionRecord(
                    session_id="session-previous",
                    question_index=0,
                    question_type="A1",
                    difficulty="基础",
                    question_text="Which sign indicates shock progression?",
                    options={"A": "Cold extremities", "B": "Normal perfusion"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    explanation="Cold extremities indicate poor peripheral perfusion.",
                    key_point="Shock progression",
                    answered_at=previous_started + timedelta(minutes=2),
                    time_spent_seconds=16,
                ),
            ]
        )
        db.commit()

    review_response = client.get("/api/tracking/review-data?ids=session-current,missing,session-previous")
    progress_response = client.get("/api/tracking/progress-board?period=all")
    markdown_response = client.get("/api/tracking/export-markdown?period=all")

    assert review_response.status_code == 200
    review_payload = review_response.json()
    assert [item["id"] for item in review_payload["sessions"]] == ["session-current", "session-previous"]
    assert review_payload["sessions"][0]["accuracy"] == 50.0
    assert review_payload["sessions"][0]["correct_count"] == 1
    assert review_payload["sessions"][0]["wrong_count"] == 1
    assert len(review_payload["sessions"][0]["questions"]) == 2
    assert review_payload["sessions"][0]["questions"][0]["user_answer"] == "A"
    assert review_payload["sessions"][1]["accuracy"] == 100.0

    assert progress_response.status_code == 200
    progress_payload = progress_response.json()
    assert progress_payload["overview"] == {
        "total_sessions": 2,
        "total_questions": 3,
        "total_correct": 2,
        "total_wrong": 1,
        "avg_accuracy": 66.7,
        "total_duration_seconds": 360,
        "total_duration_hours": 0.1,
    }
    assert progress_payload["confidence_distribution"] == [
        {"key": "sure", "label": "确定", "count": 2, "pct": 66.7},
        {"key": "unsure", "label": "模糊", "count": 0, "pct": 0.0},
        {"key": "no", "label": "不会", "count": 1, "pct": 33.3},
    ]
    assert progress_payload["session_type_distribution"] == [
        {"key": "exam", "label": "整卷测验", "count": 1, "pct": 50.0},
        {"key": "detail_practice", "label": "知识点测验", "count": 1, "pct": 50.0},
        {"key": "other", "label": "其他", "count": 0, "pct": 0.0},
    ]
    assert len(progress_payload["daily_trend_7"]) == 7
    assert len(progress_payload["daily_trend_30"]) == 30
    assert progress_payload["recent_sessions"][0]["id"] == "session-current"
    assert progress_payload["wow_delta"]["direction"] == "down"
    assert progress_payload["wow_delta"]["current_accuracy"] == 50.0
    assert progress_payload["wow_delta"]["previous_accuracy"] == 100.0
    assert progress_payload["weak_points"][0]["name"] == "Pulmonary congestion"
    assert progress_payload["weak_points"][0]["wrong"] == 1

    assert markdown_response.status_code == 200
    markdown_payload = markdown_response.json()
    assert markdown_payload["format"] == "markdown"
    assert markdown_payload["content"].startswith("# 学习轨迹报告 -")
    assert "| 学习次数 | 2 |" in markdown_payload["content"]
    assert "| 总做题数 | 3 |" in markdown_payload["content"]


def test_knowledge_tree_uses_wrong_answer_chapter_fallback_via_http(client, session_factory):
    started_at = datetime.now() - timedelta(days=2)
    question_text = "What is the most common trigger of decompensated heart failure?"

    with session_factory() as db:
        db.add(
            Chapter(
                id="internal_ch1",
                book="Medicine",
                edition="1",
                chapter_number="1",
                chapter_title="Heart Failure",
            )
        )
        db.add(
            _make_session(
                session_id="session-tree",
                started_at=started_at,
                session_type="exam",
                chapter_id="uncategorized_ch0",
                title="Fallback Mapping Session",
                total_questions=1,
            )
        )
        db.add(
            QuestionRecord(
                session_id="session-tree",
                question_index=0,
                question_type="A1",
                difficulty="基础",
                question_text=question_text,
                options={"A": "Infection", "B": "Exercise"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="unsure",
                answered_at=started_at + timedelta(minutes=1),
            )
        )
        db.add(
            WrongAnswerV2(
                question_fingerprint=make_fingerprint(question_text),
                question_text=question_text,
                options={"A": "Infection", "B": "Exercise"},
                correct_answer="A",
                explanation="Infection is the most common trigger.",
                key_point="Heart failure trigger",
                question_type="A1",
                difficulty="基础",
                chapter_id="internal_ch1",
                error_count=1,
                encounter_count=1,
                linked_record_ids=[],
            )
        )
        db.commit()

    response = client.get("/api/tracking/knowledge-tree?period=all")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tree"]
    book_node = next(node for node in payload["tree"] if node["name"] == "Medicine")
    chapter_node = next(node for node in book_node["chapters"] if node["name"] == "1 Heart Failure")
    assert chapter_node["total"] == 1
    assert chapter_node["accuracy"] == 0.0
    assert chapter_node["key_points"][0]["name"].startswith("考点待提取：")
    assert chapter_node["key_points"][0]["dominant_error_type"] == "A1"


def test_ocr_plan_board_reads_timeline_and_special_documents(client, tmp_path):
    plan_dir = tmp_path / "ocr-plan"
    plan_dir.mkdir()
    (plan_dir / "03.01 日计划&答疑 心衰专题.txt").write_text(
        "\n".join(
            [
                "03.01 日计划&答疑 心衰专题",
                "今天有直播",
                "预习 心衰",
                "复习 呼吸衰竭",
                "做题 真题训练",
            ]
        ),
        encoding="utf-8",
    )
    (plan_dir / "03.05 阶段总结.txt").write_text(
        "\n".join(
            [
                "阶段总结",
                "记录滚动复习与考试安排",
            ]
        ),
        encoding="utf-8",
    )

    response = client.get(
        f"/api/tracking/ocr-plan-board?plan_dir={plan_dir.as_posix()}&plan_year=2026"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_dir"] == str(plan_dir)
    assert payload["plan_year"] == 2026
    assert payload["overview"]["total_plan_days"] == 1
    assert payload["overview"]["covered_months"] == 1
    assert payload["overview"]["month_list"] == [3]
    assert payload["overview"]["live_days"] == 1
    assert payload["overview"]["quiz_days"] == 1
    assert payload["overview"]["review_days"] == 1
    assert payload["overview"]["preview_days"] == 1
    assert payload["timeline"][0]["date_key"] == "03-01"
    assert payload["timeline"][0]["live_status"] == "live"
    assert payload["timeline"][0]["categories"]["quiz"] is True
    assert payload["timeline"][0]["focus_topics"]
    assert payload["month_summary"][0]["month"] == 3
    assert payload["special_docs"][0]["name"] == "03.05 阶段总结.txt"
    assert payload["special_docs"][0]["date_key"] == "03-05"
    assert payload["timeline_progress"]["total_days"] == 1
    assert payload["master_plan"]["plan_year"] == 2026
