import asyncio
import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routers.quiz_batch as quiz_batch
from learning_tracking_models import LearningSession, SessionStatus, WrongAnswerV2
from models import Base, Chapter, ConceptMastery, QuizSession


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


def seed_detail_sort_data(session):
    session.add(
        Chapter(
            id="chapter1",
            book="内科学",
            edition="test",
            chapter_number="01",
            chapter_title="心血管系统",
            concepts=[],
            first_uploaded=date.today(),
        )
    )

    concept_rows = [
        ("chapter1_k1", "冠心病", 0.8),
        ("chapter1_k2", "心衰分类", 0.3),
        ("chapter1_k3", "瓣膜病变", 0.5),
        ("chapter1_k4", "心律失常", 0.2),
        ("chapter1_k5", "高血压", 0.9),
    ]
    for concept_id, name, understanding in concept_rows:
        session.add(
            ConceptMastery(
                concept_id=concept_id,
                chapter_id="chapter1",
                name=name,
                retention=0.0,
                understanding=understanding,
                application=0.0,
            )
        )

    session.commit()


def make_question(index, key_point, correct_answer="A"):
    return {
        "id": index,
        "type": "A1",
        "difficulty": "基础",
        "question": f"Question {index} for {key_point}",
        "options": {
            "A": "Option A",
            "B": "Option B",
            "C": "Option C",
            "D": "Option D",
            "E": "Option E",
        },
        "correct_answer": correct_answer,
        "explanation": f"Explanation {index}",
        "key_point": key_point,
    }


def test_submit_exam_then_detail_returns_sorted_knowledge_points(monkeypatch, session_factory):
    session = session_factory()
    seed_detail_sort_data(session)
    quiz_batch._exam_cache.clear()
    quiz_batch._detail_cache.clear()

    exam_id = "exam-detail-order"
    quiz_batch._exam_cache[exam_id] = {
        "chapter_id": "chapter1",
        "chapter_prediction": {},
        "questions": [
            make_question(1, "冠心病"),
            make_question(2, "心衰分类"),
            make_question(3, "瓣膜病变"),
            make_question(4, "心衰分类"),
            make_question(5, "心律失常"),
            make_question(6, "高血压"),
        ],
        "created_at": datetime.now(),
        "num_questions": 6,
        "uploaded_content": "心血管系统课程讲义",
    }

    class FakeQuizService:
        def grade_paper(self, questions, answers, confidence):
            return {
                "score": 33,
                "correct_count": 2,
                "wrong_count": 4,
                "details": [
                    {"id": 1, "is_correct": True, "confidence": "sure", "key_point": "冠心病"},
                    {"id": 2, "is_correct": False, "confidence": "sure", "key_point": "心衰分类"},
                    {"id": 3, "is_correct": False, "confidence": "sure", "key_point": "瓣膜病变"},
                    {"id": 4, "is_correct": False, "confidence": "no", "key_point": "心衰分类"},
                    {"id": 5, "is_correct": False, "confidence": "unsure", "key_point": "心律失常"},
                    {"id": 6, "is_correct": True, "confidence": "sure", "key_point": "高血压"},
                ],
            }

        def _infer_chapter_prediction(self, content):
            return None

    monkeypatch.setattr(quiz_batch, "get_quiz_service", lambda: FakeQuizService())

    submit_request = quiz_batch.SubmitRequest(
        answers=["A", "B", "B", "B", "B", "A"],
        confidence={"1": "sure", "2": "sure", "3": "no", "4": "unsure"},
    )
    submit_result = asyncio.run(
        quiz_batch.submit_exam(exam_id=exam_id, request=submit_request, db=session)
    )

    assert submit_result["score"] == 33
    assert exam_id in quiz_batch._detail_cache
    assert quiz_batch._detail_cache[exam_id]["exam_wrong_questions"]

    detail_result = asyncio.run(quiz_batch.get_exam_for_detail(exam_id=exam_id, db=session))

    assert detail_result["knowledge_points"] == [
        "心衰分类",
        "瓣膜病变",
        "心律失常",
        "冠心病",
        "高血压",
    ]

    stats = detail_result["knowledge_point_stats"]
    assert stats["心衰分类"]["error_count"] == 2
    assert stats["心衰分类"]["severity_tag"] == "stubborn"
    assert stats["心衰分类"]["priority_score"] == 23.0
    assert stats["瓣膜病变"]["priority_score"] == 16.0
    assert stats["心律失常"]["priority_score"] == 14.0
    assert stats["瓣膜病变"]["original_order"] < stats["心律失常"]["original_order"]

    assert session.query(QuizSession).count() == 1
    assert session.query(WrongAnswerV2).count() == 4


def test_submit_exam_updates_concept_mastery_without_touching_understanding(monkeypatch, session_factory):
    session = session_factory()
    quiz_batch._exam_cache.clear()
    quiz_batch._detail_cache.clear()

    session.add(
        Chapter(
            id="chapter1",
            book="Cardiology",
            edition="test",
            chapter_number="01",
            chapter_title="Heart Failure",
            concepts=[],
            first_uploaded=date.today(),
        )
    )
    session.add(
        ConceptMastery(
            concept_id="chapter1_existing",
            chapter_id="chapter1",
            name="existing-key-point",
            retention=0.2,
            understanding=0.4,
            application=0.1,
        )
    )
    session.commit()

    exam_id = "exam-concept-mastery-sync"
    quiz_batch._exam_cache[exam_id] = {
        "chapter_id": "chapter1",
        "chapter_prediction": {},
        "questions": [
            make_question(1, "fresh-key-point"),
            make_question(2, "existing-key-point"),
        ],
        "created_at": datetime.now(),
        "num_questions": 2,
        "uploaded_content": "heart failure lecture notes",
    }

    class FakeQuizService:
        def grade_paper(self, questions, answers, confidence):
            return {
                "score": 50,
                "correct_count": 1,
                "wrong_count": 1,
                "details": [
                    {"id": 1, "is_correct": True, "confidence": "sure", "key_point": "fresh-key-point"},
                    {"id": 2, "is_correct": False, "confidence": "sure", "key_point": "existing-key-point"},
                ],
            }

        def _infer_chapter_prediction(self, content):
            return None

    monkeypatch.setattr(quiz_batch, "get_quiz_service", lambda: FakeQuizService())

    submit_request = quiz_batch.SubmitRequest(
        answers=["A", "B"],
        confidence={"1": "sure", "2": "sure"},
    )
    submit_result = asyncio.run(
        quiz_batch.submit_exam(exam_id=exam_id, request=submit_request, db=session)
    )

    assert submit_result["score"] == 50

    fresh = session.query(ConceptMastery).filter(ConceptMastery.name == "fresh-key-point").one()
    existing = session.query(ConceptMastery).filter(ConceptMastery.concept_id == "chapter1_existing").one()

    assert fresh.chapter_id == "chapter1"
    assert fresh.retention == pytest.approx(0.12)
    assert fresh.application == pytest.approx(0.10)
    assert fresh.understanding == pytest.approx(0.0)
    assert fresh.last_tested == date.today()
    assert fresh.next_review == date.today() + timedelta(days=7)

    assert existing.retention == pytest.approx(0.12)
    assert existing.application == pytest.approx(0.0)
    assert existing.understanding == pytest.approx(0.4)
    assert existing.last_tested == date.today()
    assert existing.next_review == date.today() + timedelta(days=1)


def test_quiz_detail_page_uses_backend_knowledge_point_order(tmp_path):
    template_path = Path(r"C:\Users\35456\true-learning-system\templates\quiz_detail.html")
    text = template_path.read_text(encoding="utf-8")
    start = text.index("function getQuestionKnowledgeKey")
    end = text.index("function buildMaskedExplanationHtml")
    helper_snippet = text[start:end]

    script = helper_snippet + """
const questions = [
  { key_point: '冠心病', type: 'A1', difficulty: '基础', question: 'Q1', options: {}, correct_answer: 'A', explanation: 'E1' },
  { key_point: '心律失常', type: 'A1', difficulty: '基础', question: 'Q2', options: {}, correct_answer: 'A', explanation: 'E2' },
  { key_point: '心衰分类', type: 'A1', difficulty: '基础', question: 'Q3', options: {}, correct_answer: 'A', explanation: 'E3' }
];
const knowledgeMap = buildKnowledgeMapFromQuestions(questions);
const ordered = getOrderedKnowledgePointsFromExamData(
  { knowledge_points: ['心衰分类', '冠心病'] },
  knowledgeMap
);
console.log(JSON.stringify(ordered));
"""

    script_path = tmp_path / "quiz_detail_order_check.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        ["node", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert json.loads(result.stdout.strip()) == ["心衰分类", "冠心病", "心律失常"]

def test_detail_stats_include_completed_practice_session_counts(session_factory):
    session = session_factory()
    seed_detail_sort_data(session)

    session.add_all([
        LearningSession(
            id="detail-1",
            session_type="detail_practice",
            chapter_id="chapter1",
            knowledge_point="detail-alpha",
            title="detail-1",
            description="detail-1",
            status=SessionStatus.COMPLETED,
            completed_at=datetime.now() - timedelta(hours=5),
        ),
        LearningSession(
            id="detail-2",
            session_type="detail_practice",
            chapter_id="chapter1",
            knowledge_point="detail-alpha",
            title="detail-2",
            description="detail-2",
            status=SessionStatus.COMPLETED,
            completed_at=datetime.now() - timedelta(hours=1),
        ),
        LearningSession(
            id="detail-3",
            session_type="detail_practice",
            chapter_id="chapter1",
            knowledge_point="detail-beta",
            title="detail-3",
            description="detail-3",
            status=SessionStatus.IN_PROGRESS,
        ),
    ])
    session.commit()

    ordered, stats = quiz_batch._build_detail_knowledge_order(
        {
            "chapter_id": "chapter1",
            "questions": [
                make_question(1, "detail-alpha"),
                make_question(2, "detail-beta"),
            ],
            "exam_wrong_questions": [],
        },
        session,
    )

    assert ordered == ["detail-alpha", "detail-beta"]
    assert stats["detail-alpha"]["practice_session_count"] == 2
    assert stats["detail-alpha"]["last_practiced_at"] is not None
    assert stats["detail-beta"]["practice_session_count"] == 0


def test_quiz_detail_page_shows_practice_session_counts():
    template_path = Path(r"C:\Users\35456\true-learning-system\templates\quiz_detail.html")
    text = template_path.read_text(encoding="utf-8")

    assert "practice_session_count" in text
    assert "getKnowledgePracticeCount" in text


def test_variation_generation_backfills_to_requested_count(monkeypatch):
    service = quiz_batch.get_quiz_service()

    async def fake_generate_json(*args, **kwargs):
        return {
            "variations": [
                {
                    "id": 1,
                    "type": "A1",
                    "difficulty": "basic",
                    "variation_type": "concept",
                    "question": "same stem",
                    "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
                    "correct_answer": "A",
                    "explanation": "exp",
                },
                {
                    "id": 2,
                    "type": "A1",
                    "difficulty": "basic",
                    "variation_type": "case",
                    "question": "same stem",
                    "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
                    "correct_answer": "B",
                    "explanation": "exp",
                },
            ]
        }

    monkeypatch.setattr(service.ai, "generate_json", fake_generate_json)

    variations = asyncio.run(
        service.generate_variation_questions(
            key_point="detail-alpha",
            base_question=make_question(1, "detail-alpha"),
            uploaded_content="cardiology notes",
            num_variations=5,
        )
    )

    assert len(variations) == 5
    assert len({item["question"] for item in variations}) == 5
