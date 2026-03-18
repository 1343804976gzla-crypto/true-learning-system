from datetime import date, datetime, timedelta
from uuid import uuid4

from backfill_concept_mastery import RecordSnapshot, backfill_for_device, compute_mastery_metrics
from learning_tracking_models import LearningSession, QuestionRecord
from models import Chapter, ConceptMastery, SessionLocal, TestRecord as TestRecordModel, WrongAnswer, init_db
from services.data_identity import ensure_learning_identity_schema

TestRecordModel.__test__ = False


def _record(
    *,
    record_id: int,
    is_correct: bool,
    confidence: str | None,
    answered_at: datetime,
    difficulty: str = "基础",
    session_type: str = "detail_practice",
) -> RecordSnapshot:
    return RecordSnapshot(
        record_id=record_id,
        user_id=None,
        device_id="device-a",
        session_id="session-a",
        chapter_id="chapter-a",
        session_type=session_type,
        key_point="Cardiac output",
        question_text="What determines cardiac output?",
        difficulty=difficulty,
        confidence=confidence,
        is_correct=is_correct,
        answered_at=answered_at,
    )


def test_compute_mastery_metrics_rewards_consistent_recent_success():
    now = datetime(2026, 3, 15, 18, 0, 0)
    metrics = compute_mastery_metrics(
        [
            _record(record_id=1, is_correct=True, confidence="sure", answered_at=now - timedelta(days=4), difficulty="提高"),
            _record(record_id=2, is_correct=True, confidence="sure", answered_at=now - timedelta(days=2), difficulty="难题"),
            _record(record_id=3, is_correct=True, confidence="sure", answered_at=now - timedelta(days=1), difficulty="难题", session_type="exam"),
        ]
    )

    assert metrics["retention"] > 0.8
    assert metrics["understanding"] > 0.8
    assert metrics["application"] > 0.85
    assert metrics["last_tested"].isoformat() == "2026-03-14"
    assert metrics["next_review"].isoformat() == "2026-03-24"


def test_compute_mastery_metrics_penalizes_confident_errors():
    now = datetime(2026, 3, 15, 18, 0, 0)
    metrics = compute_mastery_metrics(
        [
            _record(record_id=1, is_correct=False, confidence="sure", answered_at=now - timedelta(days=2), difficulty="难题"),
            _record(record_id=2, is_correct=False, confidence="sure", answered_at=now - timedelta(days=1), difficulty="提高", session_type="exam"),
            _record(record_id=3, is_correct=True, confidence="unsure", answered_at=now, difficulty="基础"),
        ]
    )

    assert metrics["retention"] < 0.3
    assert metrics["understanding"] < 0.3
    assert metrics["application"] < 0.35
    assert metrics["last_tested"].isoformat() == "2026-03-15"
    assert metrics["next_review"].isoformat() == "2026-03-16"


def test_compute_mastery_metrics_treats_missing_confidence_as_neutral():
    now = datetime(2026, 3, 15, 18, 0, 0)
    metrics = compute_mastery_metrics(
        [
            _record(record_id=1, is_correct=True, confidence=None, answered_at=now - timedelta(days=2)),
            _record(record_id=2, is_correct=True, confidence=None, answered_at=now - timedelta(days=1)),
        ]
    )

    assert metrics["retention"] > 0.9
    assert metrics["understanding"] > 0.85
    assert metrics["application"] > 0.85


def test_backfill_merges_placeholder_concepts_and_updates_session_chapter():
    init_db()
    ensure_learning_identity_schema()

    device_id = f"backfill-{uuid4().hex}"
    today = date.today()
    now = datetime(2026, 3, 15, 20, 0, 0)
    real_chapter_id = f"{device_id}-chapter"
    real_concept_id = f"{device_id}-real"
    placeholder_concept_id = f"{device_id}-placeholder"
    session_id = f"{device_id}-session"

    with SessionLocal() as db:
        db.add(
            Chapter(
                id=real_chapter_id,
                book="Physiology",
                edition="1",
                chapter_number="1",
                chapter_title="Cardiac Output",
                concepts=[],
                first_uploaded=today,
            )
        )
        if db.query(Chapter).filter(Chapter.id == "0").first() is None:
            db.add(
                Chapter(
                    id="0",
                    book="Uncategorized",
                    edition="1",
                    chapter_number="0",
                    chapter_title="Placeholder",
                    concepts=[],
                    first_uploaded=today,
                )
            )
        db.add_all(
            [
                ConceptMastery(
                    concept_id=real_concept_id,
                    device_id=device_id,
                    chapter_id=real_chapter_id,
                    name="Cardiac output",
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                ),
                ConceptMastery(
                    concept_id=placeholder_concept_id,
                    device_id=device_id,
                    chapter_id="0",
                    name="Cardiac output",
                    retention=0.7,
                    understanding=0.8,
                    application=0.6,
                    last_tested=today - timedelta(days=1),
                    next_review=today,
                ),
                LearningSession(
                    id=session_id,
                    device_id=device_id,
                    session_type="detail_practice",
                    chapter_id="0",
                    title="Detail practice: Cardiac output",
                    knowledge_point="Cardiac output",
                    status="completed",
                    total_questions=1,
                    answered_questions=1,
                    correct_count=1,
                    wrong_count=0,
                    score=100,
                    accuracy=1.0,
                    started_at=now - timedelta(minutes=10),
                    completed_at=now - timedelta(minutes=5),
                    duration_seconds=300,
                ),
                QuestionRecord(
                    session_id=session_id,
                    device_id=device_id,
                    question_index=0,
                    question_type="A1",
                    difficulty="鍩虹",
                    question_text="Cardiac output depends on heart rate and stroke volume.",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    key_point="Cardiac output",
                    answered_at=now - timedelta(minutes=8),
                    time_spent_seconds=30,
                ),
                TestRecordModel(
                    device_id=device_id,
                    concept_id=placeholder_concept_id,
                    test_type="ai_quiz",
                    ai_question="Placeholder test",
                    ai_options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    ai_correct_answer="A",
                    user_answer="A",
                    confidence="sure",
                    is_correct=True,
                    score=100,
                    tested_at=now - timedelta(hours=1),
                ),
                WrongAnswer(
                    concept_id=placeholder_concept_id,
                    question="Placeholder wrong question",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    user_answer="B",
                    explanation="Placeholder explanation",
                    error_type="knowledge_gap",
                    weak_points=["Cardiac output"],
                    next_review=today,
                ),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        summary = backfill_for_device(db, device_id, apply_changes=True)

    assert summary["merged_placeholder_concepts"] == 1
    assert summary["updated_sessions"] == 1

    with SessionLocal() as db:
        concepts = (
            db.query(ConceptMastery)
            .filter(ConceptMastery.device_id == device_id, ConceptMastery.name == "Cardiac output")
            .all()
        )
        assert len(concepts) == 1
        concept = concepts[0]
        assert concept.concept_id == real_concept_id
        assert concept.chapter_id == real_chapter_id
        assert concept.retention > 0.0
        assert concept.understanding > 0.0
        assert concept.application > 0.0

        session = db.query(LearningSession).filter(LearningSession.id == session_id).one()
        assert session.chapter_id == real_chapter_id

        test_record = db.query(TestRecordModel).filter(TestRecordModel.device_id == device_id).one()
        assert test_record.concept_id == real_concept_id

        wrong_answer = (
            db.query(WrongAnswer)
            .filter(WrongAnswer.concept_id == real_concept_id)
            .one()
        )
        assert wrong_answer.concept_id == real_concept_id
