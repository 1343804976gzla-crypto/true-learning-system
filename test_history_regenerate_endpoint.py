from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from learning_tracking_models import (
    ChapterReviewChapter,
    ChapterReviewTask,
    ChapterReviewTaskQuestion,
    ChapterReviewUnit,
)
from models import get_db
import routers.history as history_module
import services.chapter_review_service as chapter_review_service_module


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
    app.include_router(history_module.router)

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


@pytest.fixture(autouse=True)
def stub_light_explanation_rewriter(monkeypatch):
    async def _passthrough(unit, summary, questions):
        return questions

    async def _skip_blueprint(**kwargs):
        raise TimeoutError("skip ai blueprint")

    monkeypatch.setattr(
        chapter_review_service_module,
        "_ai_rewrite_question_explanations",
        _passthrough,
    )
    monkeypatch.setattr(
        chapter_review_service_module,
        "_ai_refine_review_concept_blueprint",
        _skip_blueprint,
    )


def test_regenerate_questions_replaces_only_unanswered_questions(client, session_factory, monkeypatch):
    with session_factory() as db:
        chapter = ChapterReviewChapter(
            actor_key="device:local-default",
            chapter_id="med_ch2_hf",
            book="Medicine",
            chapter_number="2",
            chapter_title="Heart Failure",
            ai_summary="Heart failure summary",
            merged_raw_content="heart failure source content",
            cleaned_content="heart failure source content",
            content_version=1,
            first_uploaded_date=date(2026, 3, 20),
            last_uploaded_date=date(2026, 3, 20),
            next_due_date=date(2026, 3, 21),
            review_status="due",
        )
        db.add(chapter)
        db.flush()

        unit = ChapterReviewUnit(
            review_chapter_id=chapter.id,
            content_version=1,
            unit_index=1,
            unit_title="Heart Failure Unit 1",
            raw_text="heart failure raw text",
            cleaned_text="heart failure cleaned text",
            excerpt="heart failure excerpt",
            char_count=120,
            estimated_minutes=15,
            next_round=1,
            completed_rounds=0,
            next_due_date=date(2026, 3, 21),
            review_status="pending",
            carry_over_count=0,
            is_active=True,
        )
        db.add(unit)
        db.flush()

        task = ChapterReviewTask(
            actor_key="device:local-default",
            review_chapter_id=chapter.id,
            unit_id=unit.id,
            content_version=1,
            scheduled_for=date(2026, 3, 21),
            due_reason="Round 1 review",
            estimated_minutes=15,
            question_count=2,
            answered_count=1,
            resume_position=1,
            status="in_progress",
            source_label="Round 1 review",
        )
        db.add(task)
        db.flush()

        kept_question = ChapterReviewTaskQuestion(
            task_id=task.id,
            position=1,
            prompt="Explain why reduced cardiac output and pulmonary congestion can appear together in acute heart failure.",
            reference_answer="A strong answer should connect impaired pump function to lower forward flow, rising filling pressure, fluid redistribution into the lungs, and the resulting dyspnea and fatigue.",
            key_points=["Reduced forward flow", "Pulmonary congestion"],
            explanation="The answer should trace the mechanism from impaired ventricular pumping to elevated filling pressure and then to lung congestion and symptom formation.",
            source_excerpt="Impaired ventricular pumping lowers output while elevated filling pressure causes pulmonary congestion and dyspnea.",
            generation_source="ai",
            user_answer="Keep this answer",
        )
        replaced_question = ChapterReviewTaskQuestion(
            task_id=task.id,
            position=2,
            prompt="Summarize the initial management priorities for heart failure.",
            reference_answer="Cover oxygen support, diuretics, preload reduction, and monitoring priorities for early management.",
            key_points=["Treatment"],
            explanation="Keep the answer anchored to the source text and include the main emergency priorities.",
            source_excerpt="Initial management includes oxygen, diuretics, and hemodynamic monitoring.",
            generation_source="ai",
        )
        db.add_all([kept_question, replaced_question])
        db.commit()

    async def fake_generate_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"Summarize the early treatment priorities for acute heart failure ({index}).",
                "reference_answer": "A high-scoring answer should start with oxygen support and monitoring, then cover diuretics, preload reduction, and rapid reassessment of the patient's hemodynamic response.",
                "key_points": ["Oxygen support", "Diuretics", "Rapid reassessment"],
                "explanation": "This question tests whether the learner can organize early heart-failure management into a clinical sequence. A common mistake is to list measures without first stabilizing oxygenation and monitoring, so the answer should present priorities in order.",
                "source_excerpt": "Early treatment focuses on oxygen support, diuretics, and close reassessment.",
            }
            for index in range(1, question_count + 1)
        ]

    async def fake_refine_questions(unit, summary, questions):
        return questions

    monkeypatch.setattr(
        chapter_review_service_module,
        "_ai_generate_questions",
        fake_generate_questions,
    )
    monkeypatch.setattr(
        chapter_review_service_module,
        "_ai_refine_questions",
        fake_refine_questions,
    )

    response = client.post("/api/history/task/1/regenerate-questions")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["questions"]) == 2
    assert payload["questions"][0]["position"] == 1
    assert payload["questions"][0]["prompt"] == "Explain why reduced cardiac output and pulmonary congestion can appear together in acute heart failure."
    assert payload["questions"][0]["user_answer"] == "Keep this answer"
    assert payload["questions"][1]["position"] == 2
    assert payload["questions"][1]["prompt"].startswith("Summarize the early treatment priorities for acute heart failure")
    assert payload["questions"][1]["user_answer"] == ""
    assert payload["questions"][1]["generation_source"] == "ai"

    with session_factory() as db:
        refreshed_task = db.query(ChapterReviewTask).filter(ChapterReviewTask.id == 1).first()
        stored_questions = (
            db.query(ChapterReviewTaskQuestion)
            .filter(ChapterReviewTaskQuestion.task_id == 1)
            .order_by(ChapterReviewTaskQuestion.position.asc())
            .all()
        )

        assert refreshed_task is not None
        assert len(stored_questions) == 2
        assert stored_questions[0].prompt == "Explain why reduced cardiac output and pulmonary congestion can appear together in acute heart failure."
        assert stored_questions[0].user_answer == "Keep this answer"
        assert stored_questions[1].prompt.startswith("Summarize the early treatment priorities for acute heart failure")
        assert stored_questions[1].generation_source == "ai"
