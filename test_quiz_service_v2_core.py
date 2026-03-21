from __future__ import annotations

from services.quiz_service_v2 import QuizService


def _make_service() -> QuizService:
    service = QuizService.__new__(QuizService)
    service.cache_enabled = True
    service.segment_cache_enabled = True
    service.cache_ttl_seconds = 3600
    service._cache = {}
    service._segment_cache = {}
    service.topic_check_enabled = True
    service.topic_overlap_threshold = 0.3
    return service


def _sample_paper(total_questions: int = 10) -> dict:
    questions = []
    for index in range(1, total_questions + 1):
        questions.append(
            {
                "id": index,
                "type": "A1",
                "difficulty": "基础" if index <= 6 else ("提高" if index <= 9 else "难题"),
                "question": f"Question {index}",
                "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
                "correct_answer": "A",
                "explanation": f"Explanation {index}",
                "key_point": f"Point {((index - 1) // 2) + 1}",
            }
        )
    return {
        "paper_title": "Sample paper",
        "total_questions": total_questions,
        "difficulty_distribution": {"基础": 6, "提高": 3, "难题": 1},
        "questions": questions,
        "knowledge_points": ["Point 1", "Point 2", "Point 3", "Point 4", "Point 5"],
        "summary": {"coverage": "Original coverage"},
    }


def test_quiz_cache_compatible_subset_is_isolated_from_source_and_reads():
    service = _make_service()
    source = _sample_paper(10)

    service._save_to_cache("quiz_demo_10", source)
    source["questions"][0]["question"] = "mutated after save"

    cached_subset = service._get_from_cache("quiz_demo_5")

    assert cached_subset is not None
    assert cached_subset["total_questions"] == 5
    assert len(cached_subset["questions"]) == 5
    assert cached_subset["questions"][0]["id"] == 1
    assert cached_subset["questions"][0]["question"] == "Question 1"
    assert cached_subset["difficulty_distribution"] == {"基础": 5, "提高": 0, "难题": 0}
    assert cached_subset["summary"]["coverage"] == "覆盖 3 个知识点"

    cached_subset["questions"][0]["question"] = "mutated after read"
    second_read = service._get_from_cache("quiz_demo_5")
    assert second_read is not None
    assert second_read["questions"][0]["question"] == "Question 1"


def test_segment_cache_returns_defensive_copies():
    service = _make_service()
    segment_questions = [
        {"id": 1, "question": "Segment question", "correct_answer": "A"},
    ]

    service._save_to_segment_cache("segment_demo_1", segment_questions)
    segment_questions[0]["question"] = "mutated after save"

    cached = service._get_from_segment_cache("segment_demo_1")
    assert cached == [{"id": 1, "question": "Segment question", "correct_answer": "A"}]

    cached[0]["question"] = "mutated after read"
    second_read = service._get_from_segment_cache("segment_demo_1")
    assert second_read == [{"id": 1, "question": "Segment question", "correct_answer": "A"}]


def test_normalize_chapter_prediction_prefers_local_inference_when_ai_resolution_conflicts():
    service = _make_service()
    inferred = {
        "book": "内科学",
        "chapter_id": "internal_ch2",
        "chapter_title": "休克",
        "confidence": "medium",
    }

    service._infer_chapter_prediction = lambda content: inferred
    service._extract_book_hint = lambda content: "内科学"
    service._extract_chapter_number_and_title = lambda content: ("2", "休克")
    service._resolve_chapter_from_db = lambda **kwargs: {
        "book": "外科学",
        "chapter_id": "surgery_ch7",
        "chapter_title": "甲状腺",
        "confidence": "high",
    }

    result = service._normalize_chapter_prediction(
        {
            "book": "外科学",
            "chapter_id": "surgery_ch7",
            "chapter_title": "甲状腺",
            "confidence": "high",
        },
        "内科学第二章休克",
    )

    assert result == inferred


def test_grade_paper_uses_normalized_answers_for_single_and_multi_select():
    service = _make_service()
    questions = [
        {
            "id": 1,
            "type": "A1",
            "difficulty": "基础",
            "correct_answer": "B",
            "explanation": "Single-choice explanation",
            "key_point": "Single point",
        },
        {
            "id": 2,
            "type": "X",
            "difficulty": "提高",
            "correct_answer": "AC",
            "explanation": "Multi-choice explanation",
            "key_point": "Multi point",
        },
    ]

    result = service.grade_paper(
        questions=questions,
        user_answers=["B. selected", "C, A"],
        user_confidence={"0": "sure", "1": "unsure"},
    )

    assert result["score"] == 100
    assert result["correct_count"] == 2
    assert result["wrong_count"] == 0
    assert result["details"][0]["user_answer"] == "B"
    assert result["details"][1]["user_answer"] == "AC"
    assert result["confidence_analysis"]["sure_rate"] == 100
    assert result["confidence_analysis"]["unsure_rate"] == 100
