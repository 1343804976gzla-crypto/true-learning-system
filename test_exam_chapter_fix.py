import asyncio
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import models
import routers.quiz_batch as quiz_batch
import services.quiz_service_v2 as quiz_service_module
from learning_tracking_models import WrongAnswerV2
from models import Base, Chapter


BOOK_PHYSIOLOGY = "\u751f\u7406\u5b66"
BOOK_UNCATEGORIZED = "\u672a\u5206\u7c7b"
TITLE_AUTOFILL_ZERO = "\u81ea\u52a8\u8865\u9f50\u7ae0\u8282(0)"
TITLE_AUTOFILL_PHYSIO = "\u81ea\u52a8\u8865\u9f50\u7ae0\u8282(physio_auto_ch16)"
TITLE_GASTRIC = "\u53e3\u8154\u98df\u7ba1\u548c\u80c3\u5185\u6d88\u5316"
TITLE_INTESTINE = "\u80a0\u5185\u6d88\u5316\u4e0e\u5438\u6536"
TITLE_PENDING = "\u5f85\u4eba\u5de5\u5f52\u7c7b"


class FakeAI:
    def __init__(self, payload):
        self.payload = payload

    async def generate_json(self, *args, **kwargs):
        return self.payload


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


def seed_chapters(session):
    session.add_all(
        [
            Chapter(
                id="0",
                book=BOOK_UNCATEGORIZED,
                edition="test",
                chapter_number="0",
                chapter_title=TITLE_AUTOFILL_ZERO,
                concepts=[],
                first_uploaded=date.today(),
            ),
            Chapter(
                id="uncategorized_ch0",
                book=BOOK_UNCATEGORIZED,
                edition="test",
                chapter_number="0",
                chapter_title=TITLE_PENDING,
                concepts=[],
                first_uploaded=date.today(),
            ),
            Chapter(
                id="physio_auto_ch16",
                book=BOOK_PHYSIOLOGY,
                edition="test",
                chapter_number="16",
                chapter_title=TITLE_AUTOFILL_PHYSIO,
                concepts=[],
                first_uploaded=date.today(),
            ),
            Chapter(
                id="physio_ch16",
                book=BOOK_PHYSIOLOGY,
                edition="test",
                chapter_number="16",
                chapter_title=TITLE_GASTRIC,
                concepts=[],
                first_uploaded=date.today(),
            ),
            Chapter(
                id="physio_ch17",
                book=BOOK_PHYSIOLOGY,
                edition="test",
                chapter_number="17",
                chapter_title=TITLE_INTESTINE,
                concepts=[],
                first_uploaded=date.today(),
            ),
        ]
    )
    session.commit()


def install_fake_get_db(monkeypatch, session_factory):
    def fake_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(models, "get_db", fake_get_db)


def install_counting_get_db(monkeypatch, session_factory):
    counter = {"count": 0}

    def fake_get_db():
        counter["count"] += 1
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(models, "get_db", fake_get_db)
    return counter


def make_valid_question(index):
    return {
        "id": index,
        "type": "A1",
        "difficulty": "\u57fa\u7840",
        "question": f"Question {index}",
        "options": {
            "A": "Option A",
            "B": "Option B",
            "C": "Option C",
            "D": "Option D",
            "E": "Option E",
        },
        "correct_answer": "A",
        "explanation": "Explanation",
        "key_point": f"key-point-{index}",
        "related_questions": "[]",
    }


def test_get_chapter_catalog_lists_real_ids_when_book_hint_missing(monkeypatch, session_factory):
    session = session_factory()
    seed_chapters(session)
    session.close()
    install_fake_get_db(monkeypatch, session_factory)
    monkeypatch.setattr(quiz_service_module, "get_ai_client", lambda: FakeAI({}))

    service = quiz_service_module.QuizService()
    monkeypatch.setattr(service, "_extract_book_hint", lambda content: "")

    catalog = service._get_chapter_catalog("gastric acid secretion without a subject name")

    assert "physio_ch16" in catalog
    assert "physio_ch17" in catalog
    assert TITLE_AUTOFILL_ZERO not in catalog
    assert TITLE_AUTOFILL_PHYSIO not in catalog
    assert "\u3010" in catalog


def test_chapter_metadata_cache_reuses_single_db_load(monkeypatch, session_factory):
    session = session_factory()
    seed_chapters(session)
    session.close()
    counter = install_counting_get_db(monkeypatch, session_factory)
    monkeypatch.setattr(quiz_service_module, "get_ai_client", lambda: FakeAI({}))

    service = quiz_service_module.QuizService()
    content = f"{BOOK_PHYSIOLOGY} 第16章 {TITLE_GASTRIC}"

    catalog1 = service._get_chapter_catalog(content)
    catalog2 = service._get_chapter_catalog(content)
    prediction1 = service._infer_chapter_prediction(content)
    prediction2 = service._infer_chapter_prediction(content)

    assert counter["count"] == 1
    assert catalog1 == catalog2
    assert prediction1 == prediction2 == {
        "book": BOOK_PHYSIOLOGY,
        "chapter_id": "physio_ch16",
        "chapter_title": TITLE_GASTRIC,
        "confidence": "high",
    }


def test_chapter_metadata_cache_refreshes_after_ttl(monkeypatch, session_factory):
    session = session_factory()
    seed_chapters(session)
    session.close()
    counter = install_counting_get_db(monkeypatch, session_factory)
    monkeypatch.setattr(quiz_service_module, "get_ai_client", lambda: FakeAI({}))

    service = quiz_service_module.QuizService()
    content = f"{BOOK_PHYSIOLOGY} 第16章 {TITLE_GASTRIC}"

    service._get_chapter_catalog(content)
    assert counter["count"] == 1

    service._chapter_cache_expire_at = datetime.now() - timedelta(seconds=1)
    service._get_chapter_catalog(content)

    assert counter["count"] == 2


def test_generate_exam_paper_reuses_larger_cached_paper_for_smaller_request(monkeypatch):
    monkeypatch.setattr(quiz_service_module, "get_ai_client", lambda: FakeAI({}))

    class CountingQuizService(quiz_service_module.QuizService):
        def __init__(self):
            super().__init__()
            self.generate_calls = []

        async def _generate_single_paper(self, uploaded_content, num_questions, difficulty_distribution):
            self.generate_calls.append(num_questions)
            difficulty_cycle = ["基础", "提高", "难题"]
            questions = []
            for index in range(1, num_questions + 1):
                question = make_valid_question(index)
                question["difficulty"] = difficulty_cycle[(index - 1) % len(difficulty_cycle)]
                questions.append(question)

            difficulty_distribution = {"基础": 0, "提高": 0, "难题": 0}
            for question in questions:
                difficulty_distribution[question["difficulty"]] += 1

            return {
                "paper_title": "Mock Paper",
                "total_questions": num_questions,
                "chapter_prediction": {
                    "book": BOOK_PHYSIOLOGY,
                    "chapter_id": "physio_ch16",
                    "chapter_title": TITLE_GASTRIC,
                    "confidence": "high",
                },
                "difficulty_distribution": difficulty_distribution,
                "questions": questions,
                "knowledge_points": [question["key_point"] for question in questions],
                "summary": {"coverage": f"覆盖 {num_questions} 个知识点", "focus": "all", "advice": "all"},
            }

    service = CountingQuizService()
    content = "gastric physiology " * 50

    first_result = asyncio.run(
        service.generate_exam_paper(
            uploaded_content=content,
            num_questions=20,
        )
    )
    second_result = asyncio.run(
        service.generate_exam_paper(
            uploaded_content=content,
            num_questions=10,
        )
    )

    assert len(first_result["questions"]) == 20
    assert service.generate_calls == [20]
    assert second_result["total_questions"] == 10
    assert [question["id"] for question in second_result["questions"]] == list(range(1, 11))
    assert second_result["knowledge_points"] == [f"key-point-{index}" for index in range(1, 11)]
    assert second_result["difficulty_distribution"] == {"基础": 4, "提高": 3, "难题": 3}
    assert second_result["summary"]["coverage"] == "覆盖 10 个知识点"
    assert second_result["chapter_prediction"] == first_result["chapter_prediction"]
    assert service._get_cache_key(content, 10) in service._cache


def test_generate_exam_paper_does_not_fallback_to_placeholder_chapter(monkeypatch, session_factory):
    session = session_factory()
    seed_chapters(session)
    session.close()
    install_fake_get_db(monkeypatch, session_factory)

    fake_result = {
        "paper_title": "Mock Paper",
        "total_questions": 5,
        "chapter_prediction": {
            "book": BOOK_UNCATEGORIZED,
            "chapter_id": "0",
            "chapter_title": TITLE_AUTOFILL_ZERO,
            "confidence": "low",
        },
        "difficulty_distribution": {
            "\u57fa\u7840": 2,
            "\u63d0\u9ad8": 2,
            "\u96be\u9898": 1,
        },
        "questions": [make_valid_question(i) for i in range(1, 6)],
        "summary": {"coverage": "all", "focus": "all", "advice": "all"},
    }

    monkeypatch.setattr(quiz_service_module, "get_ai_client", lambda: FakeAI(fake_result))
    service = quiz_service_module.QuizService()

    async def fake_topic_check(*args, **kwargs):
        return True, 1.0, "ok"

    monkeypatch.setattr(service, "_validate_topic_consistency", fake_topic_check)
    monkeypatch.setattr(service, "_infer_chapter_prediction", lambda content: None)
    monkeypatch.setattr(service, "_get_chapter_catalog", lambda content="": "catalog")

    result = asyncio.run(
        service.generate_exam_paper(
            uploaded_content="gastric physiology " * 40,
            num_questions=5,
        )
    )

    assert result["chapter_prediction"] == {
        "book": "",
        "chapter_id": "",
        "chapter_title": "",
        "confidence": "low",
    }


def test_list_chapters_grouped_filters_placeholder_rows(session_factory):
    session = session_factory()
    seed_chapters(session)

    grouped = asyncio.run(main.list_chapters_grouped(db=session))

    assert BOOK_UNCATEGORIZED not in grouped
    assert [item["id"] for item in grouped[BOOK_PHYSIOLOGY]] == ["physio_ch16", "physio_ch17"]
    assert all(
        not item["title"].startswith("\u81ea\u52a8\u8865\u9f50\u7ae0\u8282")
        for item in grouped[BOOK_PHYSIOLOGY]
    )


def test_submit_exam_uses_uploaded_content_fallback_for_wrong_answer_chapter(monkeypatch, session_factory):
    session = session_factory()
    seed_chapters(session)

    exam_id = "exam-fallback-uploaded-content"
    quiz_batch._exam_cache[exam_id] = {
        "chapter_id": "",
        "chapter_prediction": {"book": BOOK_PHYSIOLOGY, "chapter_id": "", "chapter_title": TITLE_GASTRIC},
        "questions": [
            {
                "id": 1,
                "type": "A1",
                "difficulty": "\u57fa\u7840",
                "question": "What does gastric acid do?",
                "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
                "correct_answer": "A",
                "explanation": "Because it activates pepsinogen.",
                "key_point": "",
            }
        ],
        "created_at": datetime.now(),
        "num_questions": 1,
        "uploaded_content": "\u80c3\u5185\u6d88\u5316 \u80c3\u9178 \u58c1\u7ec6\u80de \u4e3b\u7ec6\u80de",
    }

    class FakeQuizService:
        def grade_paper(self, questions, answers, confidence):
            return {
                "details": [{"is_correct": False, "confidence": "sure"}],
                "correct_count": 0,
                "score": 0,
            }

        def _infer_chapter_prediction(self, content):
            if "\u80c3" in content:
                return {
                    "book": BOOK_PHYSIOLOGY,
                    "chapter_id": "physio_ch16",
                    "chapter_title": TITLE_GASTRIC,
                    "confidence": "high",
                }
            return None

    monkeypatch.setattr(quiz_batch, "get_quiz_service", lambda: FakeQuizService())

    request = quiz_batch.SubmitRequest(answers=["B"], confidence={"0": "sure"})
    asyncio.run(quiz_batch.submit_exam(exam_id=exam_id, request=request, db=session))

    wrong_answer = session.query(WrongAnswerV2).one()
    assert wrong_answer.chapter_id == "physio_ch16"
    assert session.query(models.QuizSession).one().chapter_id == "physio_ch16"


def test_batch_exam_api_flow_resolves_real_chapter_from_uploaded_content(monkeypatch, session_factory):
    session = session_factory()
    seed_chapters(session)

    def fake_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    class FakeQuizService:
        async def generate_exam_paper(self, uploaded_content, num_questions):
            return {
                "paper_title": "Mock Exam",
                "total_questions": num_questions,
                "chapter_prediction": {
                    "book": BOOK_UNCATEGORIZED,
                    "chapter_id": "0",
                    "chapter_title": TITLE_AUTOFILL_ZERO,
                    "confidence": "low",
                },
                "difficulty_distribution": {
                    "\u57fa\u7840": num_questions,
                    "\u63d0\u9ad8": 0,
                    "\u96be\u9898": 0,
                },
                "questions": [make_valid_question(i) for i in range(1, num_questions + 1)],
                "summary": {"coverage": "all", "focus": "all", "advice": "all"},
            }

        def grade_paper(self, questions, answers, confidence):
            return {
                "details": [{"is_correct": False, "confidence": "sure"} for _ in questions],
                "correct_count": 0,
                "score": 0,
            }

        def _infer_chapter_prediction(self, content):
            if "\u80c3" in content:
                return {
                    "book": BOOK_PHYSIOLOGY,
                    "chapter_id": "physio_ch16",
                    "chapter_title": TITLE_GASTRIC,
                    "confidence": "high",
                }
            return None

    monkeypatch.setattr(quiz_batch, "get_quiz_service", lambda: FakeQuizService())
    main.app.dependency_overrides[quiz_batch.get_db] = fake_get_db
    main.app.dependency_overrides[main.get_db] = fake_get_db
    quiz_batch._exam_cache.clear()

    try:
        client = TestClient(main.app)

        generate_resp = client.post(
            "/api/quiz/batch/generate/0",
            json={
                "uploaded_content": "\u80c3\u5185\u6d88\u5316 \u80c3\u9178 \u58c1\u7ec6\u80de \u4e3b\u7ec6\u80de " * 30,
                "num_questions": 5,
            },
        )
        assert generate_resp.status_code == 200
        generate_data = generate_resp.json()
        assert generate_data["chapter_prediction"]["chapter_id"] == "0"

        submit_resp = client.post(
            f"/api/quiz/batch/submit/{generate_data['exam_id']}",
            json={"answers": ["B", "B", "B", "B", "B"], "confidence": {"0": "sure"}},
        )
        assert submit_resp.status_code == 200

        verify_db = session_factory()
        try:
            wrong_answers = verify_db.query(WrongAnswerV2).all()
            assert len(wrong_answers) == 5
            assert {item.chapter_id for item in wrong_answers} == {"physio_ch16"}
            quiz_sessions = verify_db.query(models.QuizSession).all()
            assert len(quiz_sessions) == 1
            assert quiz_sessions[0].chapter_id == "physio_ch16"
        finally:
            verify_db.close()
    finally:
        main.app.dependency_overrides.pop(quiz_batch.get_db, None)
        main.app.dependency_overrides.pop(main.get_db, None)
        quiz_batch._exam_cache.clear()
        session.close()


def test_quiz_batch_modal_prefills_unique_title_match_only(tmp_path):
    template_path = Path(r"C:\Users\35456\true-learning-system\templates\quiz_batch.html")
    template_text = template_path.read_text(encoding="utf-8")
    helper_start = template_text.index("function isSelectableChapterId")
    helper_end = template_text.index("let uploadedContent = '';")
    helper_snippet = "const chapterId = '0';\n" + template_text[helper_start:helper_end]

    script = helper_snippet + """
let chaptersGrouped = {
  '\\u751f\\u7406\\u5b66': [
    { id: 'physio_ch15', title: '\\u6d88\\u5316\\u6982\\u8ff0' },
    { id: 'physio_ch16', title: '\\u53e3\\u8154\\u98df\\u7ba1\\u548c\\u80c3\\u5185\\u6d88\\u5316' },
    { id: 'physio_ch17', title: '\\u80a0\\u5185\\u6d88\\u5316\\u4e0e\\u5438\\u6536' }
  ]
};
const exactish = findPredictedChapterMatch({
  book: '\\u751f\\u7406\\u5b66',
  chapter_id: '',
  chapter_title: '\\u80a0\\u5185\\u6d88\\u5316\\u548c\\u5438\\u6536'
});
const vague = findPredictedChapterMatch({
  book: '\\u751f\\u7406\\u5b66',
  chapter_id: '',
  chapter_title: '\\u6d88\\u5316\\u7cfb\\u7edf'
});
console.log(exactish ? exactish.chapter.id + ':' + exactish.score : 'null');
console.log(vague ? vague.chapter.id + ':' + vague.score : 'null');
"""

    script_path = tmp_path / "quiz_batch_modal_match.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        ["node", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert lines[-2] == "physio_ch17:100"
    assert lines[-1] == "null"
