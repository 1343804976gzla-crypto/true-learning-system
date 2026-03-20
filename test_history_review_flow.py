from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.chapter_review_service  # noqa: F401
from learning_tracking_models import ChapterReviewChapter, ChapterReviewTask, ChapterReviewUnit
from main import app
from models import Base, get_db


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


def test_upload_creates_review_plan_task_and_pdf_export(client, monkeypatch):
    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            return {
                "book": "内科学",
                "edition": "1",
                "chapter_number": "2",
                "chapter_title": "心力衰竭",
                "chapter_id": "med_ch2_hf",
                "summary": "讲述心衰的定义、分型、诱因与处理要点。",
                "concepts": [
                    {"id": "hf_def", "name": "心衰定义"},
                    {"id": "hf_tx", "name": "治疗原则"},
                ],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"第{i}题：请概述心衰相关要点。",
                "reference_answer": "应覆盖定义、诱因和处理原则。",
                "key_points": ["定义", "诱因", "处理原则"],
                "explanation": "答案应围绕原文中的关键概念组织。",
                "source_excerpt": unit.excerpt or unit.cleaned_text[:80],
            }
            for i in range(1, question_count + 1)
        ]

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)

    headers = {"x-tls-device-id": "review-device-a"}
    upload_response = client.post(
        "/api/upload",
        json={
            "content": "心力衰竭的定义。心力衰竭的诱因。心力衰竭的处理原则。\n\n急性失代偿时要尽快评估容量状态。",
            "date": "2026-03-19",
        },
        headers=headers,
    )
    assert upload_response.status_code == 200

    plan_response = client.get("/api/history/review-plan", headers=headers)
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["task_count"] >= 1

    task_id = plan_payload["tasks"][0]["task_id"]
    detail_response = client.get(f"/api/history/review-task/{task_id}", headers=headers)
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert len(detail_payload["questions"]) == 10
    assert detail_payload["chapter_title"] == "心力衰竭"
    assert detail_payload["source_content"]
    assert detail_payload["content_version"] == 1

    autosave_response = client.post(
        f"/api/history/review-task/{task_id}/autosave",
        json={
            "resume_position": 2,
            "answers": [
                {
                    "question_id": detail_payload["questions"][0]["id"],
                    "user_answer": "心衰需要先明确概念和诱因。",
                },
                {
                    "question_id": detail_payload["questions"][1]["id"],
                    "user_answer": "急性失代偿时要先评估容量状态。",
                },
            ],
        },
        headers=headers,
    )
    assert autosave_response.status_code == 200
    assert autosave_response.json()["answered_count"] == 2
    assert autosave_response.json()["resume_position"] == 2

    pdf_response = client.get("/api/history/review-pdf", headers=headers)
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"].startswith("application/pdf")
    assert pdf_response.content[:4] == b"%PDF"


def test_review_task_grading_and_completion_flow(client, monkeypatch):
    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            return {
                "book": "外科学",
                "edition": "1",
                "chapter_number": "5",
                "chapter_title": "休克",
                "chapter_id": "surgery_shock",
                "summary": "讲述休克的定义、分型和处理流程。",
                "concepts": [{"id": "shock", "name": "休克"}],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"题目{i}：什么是休克？",
                "reference_answer": "需要说明定义、分型和处理思路。",
                "key_points": ["定义", "分型", "处理思路"],
                "explanation": "答案应覆盖原文中的三部分。",
                "source_excerpt": unit.excerpt or unit.cleaned_text[:80],
            }
            for i in range(1, question_count + 1)
        ]

    async def fake_ai_grading(task):
        return {
            "results": [
                {
                    "position": question.position,
                    "score": 88,
                    "good_points": ["定义", "处理思路"],
                    "missing_points": ["分型"],
                    "feedback": "主要框架正确，但还缺一个关键点。",
                    "suggestion": "补上分型后会更完整。",
                }
                for question in task.questions
            ],
            "recommended_status": "normal",
            "overall_feedback": "整体作答扎实，可以进入下一轮复习。",
        }

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)
    monkeypatch.setattr("services.chapter_review_service._ai_grade_questions", fake_ai_grading)

    headers = {"x-tls-device-id": "review-device-b"}
    upload_response = client.post(
        "/api/upload",
        json={
            "content": "休克的定义。休克的分型。休克的处理思路。",
            "date": "2026-03-19",
        },
        headers=headers,
    )
    assert upload_response.status_code == 200

    plan_payload = client.get("/api/history/review-plan", headers=headers).json()
    task_id = plan_payload["tasks"][0]["task_id"]
    detail_payload = client.get(f"/api/history/review-task/{task_id}", headers=headers).json()

    autosave_payload = {
        "resume_position": 10,
        "answers": [
            {
                "question_id": question["id"],
                "user_answer": "休克需要说明定义、分型和处理思路。",
            }
            for question in detail_payload["questions"]
        ],
    }
    autosave_response = client.post(
        f"/api/history/review-task/{task_id}/autosave",
        json=autosave_payload,
        headers=headers,
    )
    assert autosave_response.status_code == 200
    assert autosave_response.json()["answered_count"] == 10

    grade_response = client.post(f"/api/history/review-task/{task_id}/grade", headers=headers)
    assert grade_response.status_code == 200
    grade_payload = grade_response.json()
    assert grade_payload["ai_recommended_status"] == "normal"
    assert grade_payload["status"] == "awaiting_choice"
    assert grade_payload["grading_score"] == 88.0

    complete_response = client.post(
        f"/api/history/review-task/{task_id}/complete",
        json={"selected_status": "normal"},
        headers=headers,
    )
    assert complete_response.status_code == 200
    complete_payload = complete_response.json()
    assert complete_payload["status"] == "completed"
    assert complete_payload["user_selected_status"] == "normal"


def test_reupload_same_chapter_merges_content_and_resets_review_cycle(client, session_factory, monkeypatch):
    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            if "补充" in content:
                summary = "更新后的总结，加入了新的处理策略。"
            else:
                summary = "初次上传的总结。"
            return {
                "book": "内科学",
                "edition": "1",
                "chapter_number": "2",
                "chapter_title": "心力衰竭",
                "chapter_id": "med_ch2_hf",
                "summary": summary,
                "concepts": [
                    {"id": "hf_def", "name": "心衰定义"},
                    {"id": "hf_tx", "name": "治疗原则"},
                ],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"第{i}题：请根据原文回答心衰的关键点。",
                "reference_answer": "需要覆盖定义、诱因和处理原则。",
                "key_points": ["定义", "诱因", "处理原则"],
                "explanation": "答案应围绕原文中的核心信息组织。",
                "source_excerpt": unit.excerpt or unit.cleaned_text[:80],
            }
            for i in range(1, question_count + 1)
        ]

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)

    headers = {"x-tls-device-id": "review-device-c"}
    first_content = "心力衰竭的定义。心力衰竭的诱因。"
    second_content = "心力衰竭的补充处理策略。补充随访与复盘要求。"

    first_upload = client.post(
        "/api/upload",
        json={
            "content": first_content,
            "date": "2026-03-18",
        },
        headers=headers,
    )
    assert first_upload.status_code == 200

    first_plan = client.get("/api/history/review-plan", headers=headers)
    assert first_plan.status_code == 200
    first_task_id = first_plan.json()["tasks"][0]["task_id"]

    first_detail = client.get(f"/api/history/review-task/{first_task_id}", headers=headers)
    assert first_detail.status_code == 200
    first_question_id = first_detail.json()["questions"][0]["id"]

    autosave = client.post(
        f"/api/history/review-task/{first_task_id}/autosave",
        json={
            "resume_position": 1,
            "answers": [
                {
                    "question_id": first_question_id,
                    "user_answer": "先回答定义与诱因。",
                }
            ],
        },
        headers=headers,
    )
    assert autosave.status_code == 200
    assert autosave.json()["status"] == "in_progress"

    second_upload = client.post(
        "/api/upload",
        json={
            "content": second_content,
            "date": "2026-03-19",
        },
        headers=headers,
    )
    assert second_upload.status_code == 200

    with session_factory() as db:
        review_chapter = (
            db.query(ChapterReviewChapter)
            .filter(ChapterReviewChapter.actor_key == "device:review-device-c")
            .first()
        )
        assert review_chapter is not None
        assert review_chapter.content_version == 2
        assert first_content in review_chapter.merged_raw_content
        assert second_content in review_chapter.merged_raw_content
        assert review_chapter.ai_summary == "更新后的总结，加入了新的处理策略。"
        assert review_chapter.next_due_date == date(2026, 3, 20)

        active_units = (
            db.query(ChapterReviewUnit)
            .filter(
                ChapterReviewUnit.review_chapter_id == review_chapter.id,
                ChapterReviewUnit.is_active.is_(True),
            )
            .all()
        )
        assert active_units
        assert all(unit.content_version == 2 for unit in active_units)
        assert all(unit.next_round == 1 for unit in active_units)

        cancelled_task = db.query(ChapterReviewTask).filter(ChapterReviewTask.id == first_task_id).first()
        assert cancelled_task is not None
        assert cancelled_task.status == "cancelled"

    second_plan = client.get("/api/history/review-plan", headers=headers)
    assert second_plan.status_code == 200
    second_plan_payload = second_plan.json()
    assert second_plan_payload["task_count"] >= 1
    assert second_plan_payload["tasks"][0]["task_id"] != first_task_id

    second_task_id = second_plan_payload["tasks"][0]["task_id"]
    second_detail = client.get(f"/api/history/review-task/{second_task_id}", headers=headers)
    assert second_detail.status_code == 200
    second_detail_payload = second_detail.json()
    assert second_detail_payload["content_version"] == 2
    assert first_content in second_detail_payload["source_content"]
    assert second_content in second_detail_payload["source_content"]
