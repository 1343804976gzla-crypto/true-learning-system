import asyncio
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routers.quiz_batch as quiz_batch
from learning_tracking_models import WrongAnswerV2
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
    assert stats["心律失常"]["priority_score"] == 16.0
    assert stats["瓣膜病变"]["original_order"] < stats["心律失常"]["original_order"]

    assert session.query(QuizSession).count() == 1
    assert session.query(WrongAnswerV2).count() == 4


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
