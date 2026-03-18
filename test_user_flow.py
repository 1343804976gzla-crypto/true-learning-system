from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import routers.learning_tracking as learning_tracking
import routers.quiz_batch as quiz_batch
from learning_tracking_models import DailyLearningLog, LearningSession, QuestionRecord, WrongAnswerV2
from models import Base, Chapter


BOOK_PHYSIOLOGY = "生理学"
BOOK_UNCATEGORIZED = "未分类"
TITLE_GASTRIC = "口腔食管和胃内消化"
TITLE_PLACEHOLDER = "自动补齐章节(0)"


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
                chapter_title=TITLE_PLACEHOLDER,
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
        ]
    )
    session.commit()


def make_question(index, correct_answer, difficulty, key_point):
    return {
        "id": index,
        "type": "A1",
        "difficulty": difficulty,
        "question": f"Question {index}",
        "options": {
            "A": f"Option {index}A",
            "B": f"Option {index}B",
            "C": f"Option {index}C",
            "D": f"Option {index}D",
            "E": f"Option {index}E",
        },
        "correct_answer": correct_answer,
        "explanation": f"Explanation {index}",
        "key_point": key_point,
    }


class FakeQuizService:
    def __init__(self):
        self.questions = [
            make_question(1, "A", "基础", "胃酸作用"),
            make_question(2, "B", "提高", "胃蛋白酶原激活"),
            make_question(3, "C", "基础", "壁细胞分泌"),
            make_question(4, "D", "难题", "胃排空调节"),
            make_question(5, "E", "提高", "胃内消化总结"),
        ]

    async def generate_exam_paper(self, uploaded_content, num_questions):
        questions = self.questions[:num_questions]
        difficulty_distribution = {"基础": 0, "提高": 0, "难题": 0}
        for question in questions:
            difficulty_distribution[question["difficulty"]] = difficulty_distribution.get(question["difficulty"], 0) + 1

        return {
            "paper_title": "Mock Digestive Exam",
            "total_questions": num_questions,
            "chapter_prediction": {
                "book": BOOK_UNCATEGORIZED,
                "chapter_id": "0",
                "chapter_title": TITLE_PLACEHOLDER,
                "confidence": "low",
            },
            "difficulty_distribution": difficulty_distribution,
            "questions": questions,
            "summary": {"coverage": "digestive", "focus": "gastric", "advice": "review wrong answers"},
        }

    def grade_paper(self, questions, answers, confidence):
        details = []
        correct_count = 0
        wrong_by_difficulty = {"基础": 0, "提高": 0, "难题": 0}
        confidence_analysis = {"sure": 0, "unsure": 0, "no": 0}
        weak_points = []

        for index, question in enumerate(questions):
            normalized_confidence = confidence.get(str(index), "unsure")
            is_correct = (answers[index] or "").strip().upper() == (question.get("correct_answer") or "").strip().upper()
            if is_correct:
                correct_count += 1
            else:
                difficulty = question.get("difficulty", "基础")
                wrong_by_difficulty[difficulty] = wrong_by_difficulty.get(difficulty, 0) + 1
                weak_points.append(f"{question['key_point']}({difficulty})")

            confidence_analysis[normalized_confidence] = confidence_analysis.get(normalized_confidence, 0) + 1
            details.append(
                {
                    "id": question["id"],
                    "type": question["type"],
                    "difficulty": question["difficulty"],
                    "user_answer": answers[index],
                    "correct_answer": question["correct_answer"],
                    "is_correct": is_correct,
                    "confidence": normalized_confidence,
                    "explanation": question["explanation"],
                    "key_point": question["key_point"],
                    "related_questions": "",
                }
            )

        score = int(correct_count / len(questions) * 100) if questions else 0
        return {
            "score": score,
            "correct_count": correct_count,
            "wrong_count": len(questions) - correct_count,
            "total": len(questions),
            "wrong_by_difficulty": wrong_by_difficulty,
            "confidence_analysis": confidence_analysis,
            "details": details,
            "weak_points": weak_points,
            "analysis": "Mock analysis",
        }

    def _infer_chapter_prediction(self, content):
        if "胃" in content:
            return {
                "book": BOOK_PHYSIOLOGY,
                "chapter_id": "physio_ch16",
                "chapter_title": TITLE_GASTRIC,
                "confidence": "high",
            }
        return None


def test_complete_user_flow(monkeypatch, session_factory):
    session = session_factory()
    seed_chapters(session)

    def fake_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(quiz_batch, "get_quiz_service", lambda: FakeQuizService())
    main.app.dependency_overrides[quiz_batch.get_db] = fake_get_db
    main.app.dependency_overrides[learning_tracking.get_db] = fake_get_db
    main.app.dependency_overrides[main.get_db] = fake_get_db
    quiz_batch._exam_cache.clear()
    quiz_batch._detail_cache.clear()

    uploaded_content = "胃内消化 胃酸 胃蛋白酶原 壁细胞 主细胞 胃排空调节 " * 20
    user_answers = ["A", "C", "C", "A", "E"]
    user_confidence = ["sure", "unsure", "sure", "no", "unsure"]
    expected_correct = 3
    expected_wrong = 2
    expected_score = 60

    try:
        with TestClient(main.app) as client:
            generate_resp = client.post(
                "/api/quiz/batch/generate/0",
                json={"uploaded_content": uploaded_content, "num_questions": 5},
            )
            assert generate_resp.status_code == 200
            generate_data = generate_resp.json()
            assert generate_data["paper_title"] == "Mock Digestive Exam"
            assert generate_data["total_questions"] == 5
            assert generate_data["chapter_prediction"]["chapter_id"] == "0"
            assert len(generate_data["questions"]) == 5

            start_resp = client.post(
                "/api/tracking/session/start",
                json={
                    "session_type": "exam",
                    "chapter_id": "0",
                    "title": generate_data["paper_title"],
                    "uploaded_content": uploaded_content[:500],
                },
            )
            assert start_resp.status_code == 200
            start_data = start_resp.json()
            session_id = start_data["session_id"]
            assert session_id

            for index, question in enumerate(generate_data["questions"]):
                record_resp = client.post(
                    f"/api/tracking/session/{session_id}/question",
                    json={
                        "question_index": index,
                        "question_type": question.get("type", "A1"),
                        "difficulty": question.get("difficulty", "基础"),
                        "question_text": question.get("question", ""),
                        "options": question.get("options", {}),
                        "correct_answer": question.get("correct_answer", ""),
                        "user_answer": user_answers[index],
                        "is_correct": user_answers[index] == question.get("correct_answer", ""),
                        "confidence": user_confidence[index],
                        "explanation": question.get("explanation", ""),
                        "key_point": question.get("key_point", f"考点{index + 1}"),
                    },
                )
                assert record_resp.status_code == 200
                record_data = record_resp.json()
                assert record_data["success"] is True
                assert record_data["updated"] is False

            submit_resp = client.post(
                f"/api/quiz/batch/submit/{generate_data['exam_id']}",
                json={
                    "answers": user_answers,
                    "confidence": {str(index): value for index, value in enumerate(user_confidence)},
                },
            )
            assert submit_resp.status_code == 200
            submit_data = submit_resp.json()
            assert submit_data["score"] == expected_score
            assert submit_data["correct_count"] == expected_correct
            assert submit_data["wrong_count"] == expected_wrong
            assert submit_data["total"] == 5
            assert len(submit_data["details"]) == 5
            assert submit_data["analysis"] == "Mock analysis"

            complete_resp = client.post(
                f"/api/tracking/session/{session_id}/complete",
                json={"score": submit_data["score"], "total_questions": 5},
            )
            assert complete_resp.status_code == 200
            complete_data = complete_resp.json()
            assert complete_data == {
                "success": True,
                "session_id": session_id,
                "score": expected_score,
                "accuracy": 60.0,
                "duration": complete_data["duration"],
            }
            assert isinstance(complete_data["duration"], int)
            assert complete_data["duration"] >= 0

            sessions_resp = client.get("/api/tracking/sessions")
            assert sessions_resp.status_code == 200
            sessions_data = sessions_resp.json()
            assert sessions_data["total"] == 1
            assert sessions_data["sessions"] == [
                {
                    "id": session_id,
                    "session_type": "exam",
                    "title": "Mock Digestive Exam",
                    "score": expected_score,
                    "accuracy": 60.0,
                    "correct_count": expected_correct,
                    "wrong_count": expected_wrong,
                    "total_questions": 5,
                    "sure_count": 2,
                    "unsure_count": 2,
                    "no_count": 1,
                    "duration_seconds": sessions_data["sessions"][0]["duration_seconds"],
                    "started_at": sessions_data["sessions"][0]["started_at"],
                    "status": "completed",
                }
            ]

            daily_logs_resp = client.get("/api/tracking/daily-logs", params={"days": 7})
            assert daily_logs_resp.status_code == 200
            daily_logs_data = daily_logs_resp.json()
            assert daily_logs_data["logs"] == [
                {
                    "date": date.today().isoformat(),
                    "total_sessions": 1,
                    "total_questions": 5,
                    "accuracy": 60.0,
                    "average_score": float(expected_score),
                    "duration_minutes": daily_logs_data["logs"][0]["duration_minutes"],
                    "knowledge_points": 5,
                    "weak_points": ["胃排空调节", "胃蛋白酶原激活"],
                }
            ]

            detail_resp = client.get(f"/api/tracking/session/{session_id}")
            assert detail_resp.status_code == 200
            detail_data = detail_resp.json()
            assert detail_data["id"] == session_id
            assert detail_data["session_type"] == "exam"
            assert detail_data["title"] == "Mock Digestive Exam"
            assert detail_data["score"] == expected_score
            assert detail_data["accuracy"] == 60.0
            assert detail_data["total_questions"] == 5
            assert detail_data["answered_questions"] == 5
            assert detail_data["correct_count"] == expected_correct
            assert detail_data["wrong_count"] == expected_wrong
            assert detail_data["sure_count"] == 2
            assert detail_data["unsure_count"] == 2
            assert detail_data["no_count"] == 1
            assert detail_data["status"] == "completed"
            assert len(detail_data["activities"]) == 2
            assert len(detail_data["questions"]) == 5
            assert [item["is_correct"] for item in detail_data["questions"]] == [True, False, True, False, True]

        verify_db = session_factory()
        try:
            learning_session = verify_db.query(LearningSession).one()
            assert learning_session.score == expected_score
            assert learning_session.correct_count == expected_correct
            assert learning_session.wrong_count == expected_wrong
            assert learning_session.answered_questions == 5
            assert learning_session.status == "completed"

            question_records = verify_db.query(QuestionRecord).order_by(QuestionRecord.question_index).all()
            assert len(question_records) == 5
            assert [record.is_correct for record in question_records] == [True, False, True, False, True]

            wrong_answers = verify_db.query(WrongAnswerV2).all()
            assert len(wrong_answers) == expected_wrong + 1
            assert {item.chapter_id for item in wrong_answers} == {"physio_ch16"}
            assert sum(1 for item in wrong_answers if item.severity_tag == "landmine") == 1
            assert sum(1 for item in wrong_answers if item.error_count == 0 and item.severity_tag == "landmine") == 1
            assert all(
                item.severity_tag != "landmine" or item.error_count == 0
                for item in wrong_answers
            )

            daily_log = verify_db.query(DailyLearningLog).one()
            assert daily_log.total_sessions == 1
            assert daily_log.total_questions == 5
            assert daily_log.total_correct == expected_correct
            assert daily_log.total_wrong == expected_wrong
        finally:
            verify_db.close()
    finally:
        main.app.dependency_overrides.pop(quiz_batch.get_db, None)
        main.app.dependency_overrides.pop(learning_tracking.get_db, None)
        main.app.dependency_overrides.pop(main.get_db, None)
        quiz_batch._exam_cache.clear()
        quiz_batch._detail_cache.clear()
        session.close()
