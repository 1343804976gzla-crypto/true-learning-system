from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from learning_tracking_models import WrongAnswerRetry, WrongAnswerV2
from models import get_db
import routers.challenge as challenge_module


def _make_wrong_answer(
    *,
    question_fingerprint: str,
    question_text: str,
    correct_answer: str = "A",
    key_point: str = "Key point",
    mastery_status: str = "active",
    severity_tag: str = "normal",
    error_count: int = 1,
    encounter_count: int = 1,
    retry_count: int = 0,
    sm2_ef: float = 2.5,
    sm2_interval: int = 0,
    sm2_repetitions: int = 0,
    next_review_date: date | None = None,
    last_wrong_at: datetime | None = None,
    last_retried_at: datetime | None = None,
    archived_at: datetime | None = None,
    created_at: datetime | None = None,
    variant_data: dict | None = None,
) -> WrongAnswerV2:
    now = datetime.now()
    return WrongAnswerV2(
        question_fingerprint=question_fingerprint,
        question_text=question_text,
        options={"A": "Choice A", "B": "Choice B", "C": "Choice C", "D": "Choice D", "E": "Choice E"},
        correct_answer=correct_answer,
        explanation=f"Explanation for {question_text}",
        key_point=key_point,
        question_type="A1",
        difficulty="基础",
        chapter_id="med_ch1",
        error_count=error_count,
        encounter_count=encounter_count,
        retry_count=retry_count,
        severity_tag=severity_tag,
        mastery_status=mastery_status,
        linked_record_ids=[],
        sm2_ef=sm2_ef,
        sm2_interval=sm2_interval,
        sm2_repetitions=sm2_repetitions,
        next_review_date=next_review_date,
        variant_data=variant_data,
        first_wrong_at=last_wrong_at or now,
        last_wrong_at=last_wrong_at or now,
        last_retried_at=last_retried_at,
        archived_at=archived_at,
        created_at=created_at or now,
        updated_at=now,
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
    app.include_router(challenge_module.router)

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


def test_challenge_queue_groups_pool_types_and_skips_today_answered(client, session_factory):
    now = datetime.now()
    today = date.today()

    with session_factory() as db:
        critical = _make_wrong_answer(
            question_fingerprint="wa-critical",
            question_text="Critical question",
            severity_tag="critical",
            error_count=5,
            next_review_date=today + timedelta(days=5),
            last_wrong_at=now - timedelta(hours=1),
        )
        core_1 = _make_wrong_answer(
            question_fingerprint="wa-core-1",
            question_text="Core question 1",
            next_review_date=today + timedelta(days=5),
            last_wrong_at=now - timedelta(hours=2),
        )
        core_2 = _make_wrong_answer(
            question_fingerprint="wa-core-2",
            question_text="Core question 2",
            next_review_date=today + timedelta(days=4),
            last_wrong_at=now - timedelta(hours=3),
            variant_data={"variant_question": "Cached variant", "variant_answer": "B", "generated_at": now.isoformat()},
        )
        review_only = _make_wrong_answer(
            question_fingerprint="wa-review",
            question_text="Review question",
            next_review_date=today - timedelta(days=1),
            last_wrong_at=now - timedelta(days=5),
            created_at=now - timedelta(days=8),
        )
        shovel_1 = _make_wrong_answer(
            question_fingerprint="wa-shovel-1",
            question_text="Shovel question 1",
            next_review_date=None,
            last_wrong_at=now - timedelta(days=8),
            created_at=now - timedelta(days=10),
        )
        shovel_2 = _make_wrong_answer(
            question_fingerprint="wa-shovel-2",
            question_text="Shovel question 2",
            next_review_date=today + timedelta(days=10),
            last_wrong_at=now - timedelta(days=9),
            created_at=now - timedelta(days=12),
        )
        answered_today = _make_wrong_answer(
            question_fingerprint="wa-today",
            question_text="Already answered today",
            next_review_date=today - timedelta(days=1),
            last_wrong_at=now - timedelta(days=2),
        )
        db.add_all([critical, core_1, core_2, review_only, shovel_1, shovel_2, answered_today])
        db.flush()
        db.add(
            WrongAnswerRetry(
                wrong_answer_id=answered_today.id,
                user_answer="A",
                is_correct=False,
                confidence="unsure",
                time_spent_seconds=12,
                retried_at=now,
            )
        )
        db.commit()

    response = client.get("/api/challenge/queue?count=6")

    assert response.status_code == 200
    payload = response.json()
    returned_ids = [item["id"] for item in payload["items"]]

    assert payload["count"] == 6
    assert payload["pool_stats"]["critical"] == 1
    assert payload["pool_stats"]["core"] == 2
    assert payload["pool_stats"]["review"] == 1
    assert payload["pool_stats"]["shovel"] == 2
    assert payload["pool_stats"]["today_answered"] == 1
    assert returned_ids == [1, 2, 3, 4, 5, 6]
    assert 7 not in returned_ids
    assert payload["items"][2]["has_variant"] is True
    assert payload["items"][3]["is_overdue"] is True


def test_challenge_variant_endpoint_handles_cache_and_new_generation(client, session_factory, monkeypatch):
    recent_variant = {
        "variant_question": "Recent variant",
        "variant_options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
        "variant_answer": "B",
        "transform_type": "cached",
        "core_knowledge": "Cache core",
        "generated_at": datetime.now().isoformat(),
    }

    with session_factory() as db:
        cached = _make_wrong_answer(
            question_fingerprint="wa-cache",
            question_text="Cached source",
            variant_data=recent_variant,
        )
        fresh = _make_wrong_answer(
            question_fingerprint="wa-fresh",
            question_text="Fresh source",
        )
        db.add_all([cached, fresh])
        db.commit()

    async def fake_generate_variant(_wa):
        return {
            "variant_question": "Generated variant",
            "variant_options": {"A": "AA", "B": "BB", "C": "CC", "D": "DD", "E": "EE"},
            "variant_answer": "C",
            "variant_explanation": "Generated explanation that is long enough for canonicalization.",
            "transform_type": "case-shift",
            "core_knowledge": "Generated core",
        }

    monkeypatch.setattr("services.variant_surgery_service.generate_variant", fake_generate_variant)

    cached_response = client.post("/api/challenge/variant?wrong_answer_id=1")
    fresh_response = client.post("/api/challenge/variant?wrong_answer_id=2")

    assert cached_response.status_code == 200
    assert cached_response.json() == {
        "variant_question": "Recent variant",
        "variant_options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
        "transform_type": "cached",
        "core_knowledge": "Cache core",
        "cached": True,
        "error": None,
        "fallback": False,
    }

    assert fresh_response.status_code == 200
    fresh_payload = fresh_response.json()
    assert fresh_payload["variant_question"] == "Generated variant"
    assert fresh_payload["cached"] is False
    assert fresh_payload["transform_type"] == "case-shift"
    assert fresh_payload["core_knowledge"] == "Generated core"

    with session_factory() as db:
        refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 2).first()
        assert refreshed is not None
        assert refreshed.variant_data["variant_answer"] == "C"
        assert refreshed.variant_data["generated_at"]


def test_challenge_check_answer_and_submit_promote_mastery_state(client, session_factory):
    with session_factory() as db:
        wa = _make_wrong_answer(
            question_fingerprint="wa-submit",
            question_text="Submit question",
            correct_answer="C",
            severity_tag="landmine",
            error_count=3,
            retry_count=0,
            sm2_repetitions=2,
            sm2_interval=3,
            variant_data={
                "variant_question": "Variant question",
                "variant_options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
                "variant_answer": "B",
                "variant_explanation": "Variant explanation",
                "core_knowledge": "Shared mechanism",
                "generated_at": datetime.now().isoformat(),
            },
        )
        db.add(wa)
        db.commit()

    check_response = client.post(
        "/api/challenge/check-answer",
        json={"wrong_answer_id": 1, "user_answer": "B. selected", "is_variant": True},
    )
    submit_response = client.post(
        "/api/challenge/submit",
        json={
            "wrong_answer_id": 1,
            "user_answer": "B",
            "confidence": "sure",
            "time_spent_seconds": 18,
            "is_variant": True,
            "recall_text": "I linked the symptom pattern back to the same mechanism.",
        },
    )

    assert check_response.status_code == 200
    assert check_response.json() == {"is_correct": True}

    assert submit_response.status_code == 200
    payload = submit_response.json()
    assert payload["is_correct"] is True
    assert payload["correct_answer"] == "B"
    assert payload["confidence"] == "sure"
    assert payload["severity_tag"] == "normal"
    assert payload["auto_archived"] is True
    assert payload["can_archive"] is False
    assert payload["variant_explanation"] == "Variant explanation"
    assert payload["explanation"] == "Variant explanation"
    assert payload["core_knowledge"] == "Shared mechanism"

    with session_factory() as db:
        refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 1).first()
        retries = db.query(WrongAnswerRetry).filter(WrongAnswerRetry.wrong_answer_id == 1).all()

        assert refreshed is not None
        assert refreshed.mastery_status == "archived"
        assert refreshed.retry_count == 1
        assert refreshed.sm2_repetitions == 3
        assert refreshed.next_review_date == date.today() + timedelta(days=7)
        assert len(retries) == 1
        assert retries[0].is_variant is True
        assert retries[0].rationale_text == "I linked the symptom pattern back to the same mechanism."


def test_challenge_evaluate_rationale_marks_lucky_guess_without_creating_retry(client, session_factory, monkeypatch):
    with session_factory() as db:
        wa = _make_wrong_answer(
            question_fingerprint="wa-rationale",
            question_text="Rationale question",
            correct_answer="A",
            severity_tag="normal",
            retry_count=2,
            variant_data={
                "variant_question": "Variant rationale question",
                "variant_options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
                "variant_answer": "A",
                "variant_explanation": "Variant rationale explanation",
                "core_knowledge": "Rationale core",
                "generated_at": datetime.now().isoformat(),
            },
        )
        db.add(wa)
        db.commit()

    async def fake_evaluate_rationale(_wa, _user_answer, _rationale_text, _is_correct):
        return {
            "verdict": "lucky_guess",
            "reasoning_score": 42,
            "diagnosis": "The final answer is right, but the reasoning chain is incomplete.",
            "weak_links": ["causal chain", "differential narrowing"],
        }

    monkeypatch.setattr("services.variant_surgery_service.evaluate_rationale", fake_evaluate_rationale)

    response = client.post(
        "/api/challenge/evaluate-rationale",
        json={
            "wrong_answer_id": 1,
            "user_answer": "A",
            "confidence": "sure",
            "rationale_text": "I mostly eliminated options instead of proving the mechanism.",
            "time_spent_seconds": 21,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_correct"] is True
    assert payload["verdict"] == "lucky_guess"
    assert payload["severity_tag"] == "landmine"
    assert payload["retry_count"] == 2
    assert payload["variant_explanation"] == "Variant rationale explanation"
    assert payload["core_knowledge"] == "Rationale core"

    with session_factory() as db:
        refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 1).first()
        retries = db.query(WrongAnswerRetry).filter(WrongAnswerRetry.wrong_answer_id == 1).all()

        assert refreshed is not None
        assert refreshed.severity_tag == "landmine"
        assert refreshed.retry_count == 2
        assert retries == []


def test_challenge_stats_aggregate_today_progress(client, session_factory):
    now = datetime.now()
    today = date.today()

    with session_factory() as db:
        overdue = _make_wrong_answer(
            question_fingerprint="wa-stats-overdue",
            question_text="Overdue question",
            mastery_status="active",
            next_review_date=today - timedelta(days=1),
        )
        due_without_date = _make_wrong_answer(
            question_fingerprint="wa-stats-nodate",
            question_text="Due without date",
            mastery_status="active",
            next_review_date=None,
        )
        future = _make_wrong_answer(
            question_fingerprint="wa-stats-future",
            question_text="Future question",
            mastery_status="active",
            next_review_date=today + timedelta(days=3),
        )
        archived = _make_wrong_answer(
            question_fingerprint="wa-stats-archived",
            question_text="Archived question",
            mastery_status="archived",
            archived_at=now - timedelta(days=1),
        )
        db.add_all([overdue, due_without_date, future, archived])
        db.flush()
        db.add_all(
            [
                WrongAnswerRetry(
                    wrong_answer_id=overdue.id,
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    time_spent_seconds=10,
                    retried_at=now,
                ),
                WrongAnswerRetry(
                    wrong_answer_id=overdue.id,
                    user_answer="B",
                    is_correct=False,
                    confidence="unsure",
                    time_spent_seconds=12,
                    retried_at=now,
                ),
                WrongAnswerRetry(
                    wrong_answer_id=future.id,
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    time_spent_seconds=8,
                    retried_at=now,
                ),
            ]
        )
        db.commit()

    response = client.get("/api/challenge/stats")

    assert response.status_code == 200
    assert response.json() == {
        "total_active": 3,
        "overdue_count": 2,
        "today_done": 2,
        "today_correct": 2,
        "today_total": 3,
        "today_accuracy": 66.7,
        "mastered_count": 1,
    }
