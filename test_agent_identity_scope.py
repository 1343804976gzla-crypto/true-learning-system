from __future__ import annotations

from datetime import date, datetime, timedelta

from learning_tracking_models import LearningSession, QuestionRecord, WrongAnswerV2
from models import DailyUpload, SessionLocal
from services.agent_tools import execute_agent_tool


async def test_agent_tools_include_legacy_anonymous_device_data():
    legacy_device_id = "local-default"
    current_device_id = "local-current"
    now = datetime.now()

    with SessionLocal() as db:
        db.add_all(
            [
                LearningSession(
                    id="legacy-session",
                    device_id=legacy_device_id,
                    session_type="detail_practice",
                    title="Legacy session",
                    status="completed",
                    started_at=now - timedelta(days=3),
                    completed_at=now - timedelta(days=3, minutes=-5),
                    total_questions=1,
                    correct_count=1,
                    wrong_count=0,
                    duration_seconds=300,
                ),
                LearningSession(
                    id="current-session",
                    device_id=current_device_id,
                    session_type="detail_practice",
                    title="Current session",
                    status="completed",
                    started_at=now - timedelta(days=1),
                    completed_at=now - timedelta(days=1, minutes=-5),
                    total_questions=1,
                    correct_count=0,
                    wrong_count=1,
                    duration_seconds=300,
                ),
                QuestionRecord(
                    session_id="legacy-session",
                    device_id=legacy_device_id,
                    question_index=1,
                    question_type="A1",
                    difficulty="基础",
                    question_text="legacy question",
                    options={"A": "1", "B": "2"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    answered_at=now - timedelta(days=3),
                ),
                QuestionRecord(
                    session_id="current-session",
                    device_id=current_device_id,
                    question_index=1,
                    question_type="A1",
                    difficulty="基础",
                    question_text="current question",
                    options={"A": "1", "B": "2"},
                    correct_answer="A",
                    user_answer="B",
                    is_correct=False,
                    answered_at=now - timedelta(days=1),
                ),
                DailyUpload(
                    device_id=legacy_device_id,
                    date=date.today() - timedelta(days=3),
                    raw_content="legacy upload",
                    ai_extracted={"book": "生理", "chapter_title": "旧章节"},
                ),
                DailyUpload(
                    device_id=current_device_id,
                    date=date.today() - timedelta(days=1),
                    raw_content="current upload",
                    ai_extracted={"book": "生理", "chapter_title": "新章节"},
                ),
                WrongAnswerV2(
                    device_id=legacy_device_id,
                    question_fingerprint="legacy-fingerprint",
                    question_text="legacy wrong",
                    options={"A": "1", "B": "2"},
                    correct_answer="A",
                    mastery_status="active",
                    severity_tag="normal",
                    next_review_date=date.today(),
                    first_wrong_at=now - timedelta(days=3),
                    last_wrong_at=now - timedelta(days=3),
                    created_at=now - timedelta(days=3),
                    updated_at=now - timedelta(days=3),
                ),
                WrongAnswerV2(
                    device_id=current_device_id,
                    question_fingerprint="current-fingerprint",
                    question_text="current wrong",
                    options={"A": "1", "B": "2"},
                    correct_answer="A",
                    mastery_status="active",
                    severity_tag="critical",
                    next_review_date=date.today(),
                    first_wrong_at=now - timedelta(days=1),
                    last_wrong_at=now - timedelta(days=1),
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
            ]
        )
        db.commit()

        _, sessions_payload, _ = await execute_agent_tool(
            "get_learning_sessions",
            db,
            {"limit": 10},
            device_id=current_device_id,
        )
        _, history_payload, _ = await execute_agent_tool(
            "get_study_history",
            db,
            {"days": 30, "limit": 10},
            device_id=current_device_id,
        )
        _, progress_payload, _ = await execute_agent_tool(
            "get_progress_summary",
            db,
            {"period": "all"},
            device_id=current_device_id,
        )
        _, review_payload, _ = await execute_agent_tool(
            "get_review_pressure",
            db,
            {"daily_planned_review": 20},
            device_id=current_device_id,
        )

    session_titles = {item["title"] for item in sessions_payload["items"]}
    upload_dates = {item["date"] for item in history_payload["recent_uploads"]}

    assert session_titles == {"Legacy session", "Current session"}
    assert history_payload["total_uploads_in_window"] == 2
    assert upload_dates == {
        (date.today() - timedelta(days=3)).isoformat(),
        (date.today() - timedelta(days=1)).isoformat(),
    }
    assert progress_payload["overview"]["total_sessions"] == 2
    assert progress_payload["overview"]["total_questions"] == 2
    assert review_payload["current_backlog"] == 2
    assert review_payload["due_wrong_answers"] == 2
