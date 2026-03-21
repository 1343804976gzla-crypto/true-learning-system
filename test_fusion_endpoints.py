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
import routers.fusion as fusion_module
from utils.data_contracts import canonicalize_fusion_data, canonicalize_parent_ids


def _make_wrong_answer(
    *,
    question_fingerprint: str,
    question_text: str,
    key_point: str,
    mastery_status: str = "archived",
    is_fusion: bool = False,
    parent_ids: list[int] | None = None,
    fusion_level: int = 0,
    fusion_data: dict | None = None,
    next_review_date: date | None = None,
    archived_at: datetime | None = None,
    severity_tag: str = "normal",
) -> WrongAnswerV2:
    now = datetime.now()
    return WrongAnswerV2(
        question_fingerprint=question_fingerprint,
        question_text=question_text,
        options={"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
        correct_answer="A" if not is_fusion else "FUSION",
        explanation=f"Explanation for {question_text}",
        key_point=key_point,
        question_type="FUSION" if is_fusion else "A1",
        difficulty="提高" if is_fusion else "基础",
        chapter_id="med_ch1",
        error_count=1,
        encounter_count=1,
        retry_count=0,
        severity_tag=severity_tag,
        mastery_status=mastery_status,
        linked_record_ids=[],
        parent_ids=parent_ids,
        is_fusion=is_fusion,
        fusion_level=fusion_level,
        sm2_penalty_factor=1.5 if is_fusion else 1.0,
        fusion_data=fusion_data,
        sm2_ef=2.5,
        sm2_interval=0,
        sm2_repetitions=0,
        next_review_date=next_review_date,
        first_wrong_at=now,
        last_wrong_at=now,
        archived_at=archived_at,
        created_at=now,
        updated_at=now,
    )


class _FakeFusionService:
    def __init__(self):
        self.unlock_status: dict[int, dict] = {}
        self.socratic_calls: list[int] = []
        self.create_calls: list[list[int]] = []
        self.judge_calls: list[tuple[int, str]] = []
        self.diagnose_calls: list[tuple[int, str, str]] = []

    def check_unlock_status(self, question_id: int, db):
        return self.unlock_status.get(
            question_id,
            {
                "can_unlock": True,
                "consecutive_correct": 3,
                "confidence_sure": True,
            },
        )

    async def generate_socratic_hint(self, question_id: int, db):
        self.socratic_calls.append(question_id)
        return {
            "guide_questions": [
                "What upstream mechanism links these findings?",
                "Which neighboring concept would change if this mechanism shifted?",
            ],
            "hint_text": "Think in terms of mechanism and downstream consequence.",
            "source_question_id": question_id,
            "source_key_point": "Key point",
        }

    async def generate_fusion_question(self, parent_ids, db):
        self.create_calls.append(list(parent_ids))
        return {
            "fusion_question": "Integrate the two mechanisms into one clinical reasoning answer.",
            "expected_key_points": ["Mechanism A", "Mechanism B"],
            "scoring_criteria": {"逻辑严密性": 30, "概念准确性": 40, "综合应用": 30},
            "difficulty_level": "L2",
            "parent_key_points": ["Point A", "Point B"],
        }

    async def judge_fusion_answer(self, fusion_id, user_answer, db):
        self.judge_calls.append((fusion_id, user_answer))
        return {
            "verdict": "partial",
            "score": 64,
            "feedback": "The answer connects the two concepts, but the causal bridge is incomplete.",
            "weak_links": ["causal bridge"],
            "needs_diagnosis": True,
        }

    async def diagnose_error(self, fusion_id, user_answer, reflection, db):
        self.diagnose_calls.append((fusion_id, user_answer, reflection))
        return {
            "diagnosis_type": "both",
            "affected_parent_ids": [1],
            "analysis": "One source concept was partially forgotten and the relation was also misapplied.",
            "recommendation": "Reopen the first parent and rebuild the relation explicitly.",
        }

    def apply_strict_sm2(self, fusion: WrongAnswerV2, is_correct: bool, quality: int) -> None:
        fusion.sm2_ef = 2.3
        fusion.sm2_interval = 2 if quality >= 3 else 1
        fusion.sm2_repetitions = 1 if quality >= 3 else 0
        fusion.next_review_date = date.today() + timedelta(days=fusion.sm2_interval)


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
    app.include_router(fusion_module.router)
    fake_service = _FakeFusionService()

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[fusion_module.get_fusion_service] = lambda: fake_service
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client, fake_service
    app.dependency_overrides.clear()


def test_fusion_unlock_hint_and_archived_candidates(client, session_factory):
    test_client, fake_service = client
    fake_service.unlock_status[1] = {
        "can_unlock": True,
        "consecutive_correct": 3,
        "confidence_sure": True,
    }

    with session_factory() as db:
        db.add_all(
            [
                _make_wrong_answer(
                    question_fingerprint="parent-1",
                    question_text="Archived parent one",
                    key_point="Shock mechanism",
                    archived_at=datetime.now() - timedelta(days=1),
                ),
                _make_wrong_answer(
                    question_fingerprint="parent-2",
                    question_text="Archived parent two",
                    key_point="Shock progression",
                    archived_at=datetime.now() - timedelta(hours=12),
                ),
                _make_wrong_answer(
                    question_fingerprint="active-1",
                    question_text="Active item",
                    key_point="Shock differential",
                    mastery_status="active",
                    archived_at=None,
                ),
                _make_wrong_answer(
                    question_fingerprint="fusion-archived",
                    question_text="Archived fusion item",
                    key_point="Fusion concept",
                    is_fusion=True,
                    parent_ids=[1, 2],
                    fusion_level=1,
                    archived_at=datetime.now() - timedelta(hours=6),
                ),
            ]
        )
        db.commit()

    unlock_response = test_client.post("/api/fusion/1/unlock-check")
    hint_response = test_client.get("/api/fusion/1/socratic-hint")
    candidates_response = test_client.get("/api/fusion/archived-candidates?exclude_id=1&key_point=Shock")

    assert unlock_response.status_code == 200
    assert unlock_response.json() == {
        "can_unlock": True,
        "reason": None,
        "consecutive_correct": 3,
        "confidence_sure": True,
    }

    assert hint_response.status_code == 200
    hint_payload = hint_response.json()
    assert hint_payload["source_question_id"] == 1
    assert len(hint_payload["guide_questions"]) == 2
    assert fake_service.socratic_calls == [1]

    assert candidates_response.status_code == 200
    assert candidates_response.json() == [
        {
            "id": 2,
            "question_text": "Archived parent two",
            "key_point": "Shock progression",
            "difficulty": "基础",
            "archived_at": candidates_response.json()[0]["archived_at"],
        }
    ]


def test_fusion_create_persists_record_and_blocks_duplicate(client, session_factory):
    test_client, fake_service = client
    fake_service.unlock_status[1] = {"can_unlock": True, "consecutive_correct": 3, "confidence_sure": True}
    fake_service.unlock_status[2] = {"can_unlock": True, "consecutive_correct": 3, "confidence_sure": True}

    with session_factory() as db:
        db.add_all(
            [
                _make_wrong_answer(
                    question_fingerprint="create-parent-1",
                    question_text="Parent one",
                    key_point="Point A",
                    archived_at=datetime.now() - timedelta(days=1),
                ),
                _make_wrong_answer(
                    question_fingerprint="create-parent-2",
                    question_text="Parent two",
                    key_point="Point B",
                    archived_at=datetime.now() - timedelta(days=1),
                ),
            ]
        )
        db.commit()

    first_response = test_client.post("/api/fusion/create", json={"parent_ids": [1, 2]})
    duplicate_response = test_client.post("/api/fusion/create", json={"parent_ids": [1, 2]})

    assert first_response.status_code == 200
    payload = first_response.json()
    assert payload["fusion_id"] == 3
    assert payload["fusion_level"] == 1
    assert payload["parent_ids"] == [1, 2]
    assert payload["expected_key_points"] == ["Mechanism A", "Mechanism B"]
    assert fake_service.create_calls == [[1, 2]]

    assert duplicate_response.status_code == 409
    assert "已存在" in duplicate_response.json()["detail"]

    with session_factory() as db:
        fusion = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 3).first()

        assert fusion is not None
        assert fusion.is_fusion is True
        assert fusion.parent_ids == canonicalize_parent_ids([1, 2])
        assert fusion.fusion_level == 1
        assert fusion.severity_tag == "critical"
        assert fusion.difficulty == "提高"
        assert fusion.fusion_data["expected_key_points"] == ["Mechanism A", "Mechanism B"]
        assert fusion.fusion_data["parent_key_points"] == ["Point A", "Point B"]


def test_fusion_submit_judge_and_diagnose_update_state(client, session_factory):
    test_client, fake_service = client

    with session_factory() as db:
        parent_1 = _make_wrong_answer(
            question_fingerprint="diag-parent-1",
            question_text="Parent concept one",
            key_point="Concept A",
            archived_at=datetime.now() - timedelta(days=2),
        )
        parent_2 = _make_wrong_answer(
            question_fingerprint="diag-parent-2",
            question_text="Parent concept two",
            key_point="Concept B",
            archived_at=datetime.now() - timedelta(days=2),
        )
        fusion = _make_wrong_answer(
            question_fingerprint="fusion-active",
            question_text="Fusion active question",
            key_point="Fusion A+B",
            mastery_status="active",
            is_fusion=True,
            parent_ids=[1, 2],
            fusion_level=1,
            fusion_data={
                "expected_key_points": ["Mechanism A", "Mechanism B"],
                "scoring_criteria": {"逻辑严密性": 30, "概念准确性": 40, "综合应用": 30},
                "judgement_pending": False,
            },
        )
        db.add_all([parent_1, parent_2, fusion])
        db.commit()

    submit_response = test_client.post(
        "/api/fusion/3/submit",
        json={"user_answer": "I would integrate both mechanisms and explain how they interact in sequence."},
    )
    judge_response = test_client.post("/api/fusion/3/judge")
    diagnose_response = test_client.post(
        "/api/fusion/3/diagnose",
        json={
            "user_answer": "I would integrate both mechanisms and explain how they interact in sequence.",
            "reflection": "I realized I mixed up one source concept and also skipped the bridge between the two mechanisms.",
        },
    )

    assert submit_response.status_code == 200
    assert submit_response.json() == {
        "message": "答案已缓存，请进行自我反思后请求评判",
        "fusion_id": 3,
        "pending_judgement": True,
        "hint": "在请求AI评判前，请再次审视你的答案：逻辑是否严密？概念使用是否准确？",
    }

    assert judge_response.status_code == 200
    judge_payload = judge_response.json()
    assert judge_payload == {
        "verdict": "partial",
        "score": 64,
        "feedback": "The answer connects the two concepts, but the causal bridge is incomplete.",
        "weak_links": ["causal bridge"],
        "needs_diagnosis": True,
    }
    assert fake_service.judge_calls == [
        (3, "I would integrate both mechanisms and explain how they interact in sequence.")
    ]

    assert diagnose_response.status_code == 200
    diagnose_payload = diagnose_response.json()
    assert diagnose_payload["diagnosis_type"] == "both"
    assert diagnose_payload["affected_parent_ids"] == [1]
    assert fake_service.diagnose_calls == [
        (
            3,
            "I would integrate both mechanisms and explain how they interact in sequence.",
            "I realized I mixed up one source concept and also skipped the bridge between the two mechanisms.",
        )
    ]

    with session_factory() as db:
        fusion = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 3).first()
        parent_1 = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 1).first()
        parent_2 = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 2).first()
        retries = db.query(WrongAnswerRetry).filter(WrongAnswerRetry.wrong_answer_id == 3).all()
        fusion_data = canonicalize_fusion_data(fusion.fusion_data)

        assert fusion is not None
        assert fusion.retry_count == 1
        assert fusion.sm2_interval == 2
        assert fusion.next_review_date == date.today() + timedelta(days=2)
        assert fusion_data["judgement_pending"] is False
        assert fusion_data["user_answer_cache"] == "I would integrate both mechanisms and explain how they interact in sequence."
        assert fusion_data["last_judgement"]["verdict"] == "partial"
        assert len(fusion_data["diagnosis_history"]) == 1
        assert fusion_data["diagnosis_history"][0]["affected_parent_ids"] == [1]
        assert len(retries) == 1
        assert retries[0].is_correct is False
        assert parent_1.mastery_status == "active"
        assert parent_1.severity_tag == "stubborn"
        assert parent_2.mastery_status == "archived"


def test_fusion_archive_and_queue_endpoints_cover_due_items(client, session_factory):
    test_client, _ = client
    today = date.today()

    with session_factory() as db:
        active_parent = _make_wrong_answer(
            question_fingerprint="archive-parent",
            question_text="Parent waiting for archive",
            key_point="Parent concept",
            mastery_status="active",
            archived_at=None,
        )
        due_low_level = _make_wrong_answer(
            question_fingerprint="queue-fusion-1",
            question_text="Low level due fusion",
            key_point="Fusion low",
            mastery_status="active",
            is_fusion=True,
            parent_ids=[1],
            fusion_level=1,
            next_review_date=today,
            fusion_data={"expected_key_points": ["Low"]},
        )
        due_high_level = _make_wrong_answer(
            question_fingerprint="queue-fusion-2",
            question_text="High level due fusion",
            key_point="Fusion high",
            mastery_status="active",
            is_fusion=True,
            parent_ids=[1],
            fusion_level=2,
            next_review_date=today,
            fusion_data={"expected_key_points": ["High"]},
        )
        future_fusion = _make_wrong_answer(
            question_fingerprint="queue-fusion-3",
            question_text="Future fusion",
            key_point="Fusion future",
            mastery_status="active",
            is_fusion=True,
            parent_ids=[1],
            fusion_level=1,
            next_review_date=today + timedelta(days=4),
            fusion_data={"expected_key_points": ["Future"]},
        )
        db.add_all([active_parent, due_low_level, due_high_level, future_fusion])
        db.commit()

    queue_response = test_client.get("/api/fusion/queue")
    archive_response = test_client.post("/api/fusion/2/archive")

    assert queue_response.status_code == 200
    assert queue_response.json() == [
        {
            "id": 2,
            "question_text": "Low level due fusion",
            "fusion_level": 1,
            "key_point": "Fusion low",
            "next_review_date": today.isoformat(),
        },
        {
            "id": 3,
            "question_text": "High level due fusion",
            "fusion_level": 2,
            "key_point": "Fusion high",
            "next_review_date": today.isoformat(),
        },
    ]

    assert archive_response.status_code == 200
    assert archive_response.json() == {
        "message": "融合题已归档",
        "fusion_id": 2,
        "archived_parents": [1],
        "note": "原题也已归档，因为你已掌握高阶融合",
    }

    with session_factory() as db:
        fusion = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 2).first()
        parent = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == 1).first()

        assert fusion is not None
        assert fusion.mastery_status == "archived"
        assert fusion.archived_at is not None
        assert parent is not None
        assert parent.mastery_status == "archived"
        assert parent.archived_at is not None
