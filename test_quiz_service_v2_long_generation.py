from __future__ import annotations

import asyncio

import pytest

from services.quiz_service_v2 import QuizService


def _make_service() -> QuizService:
    service = QuizService.__new__(QuizService)
    service.cache_enabled = True
    service.segment_cache_enabled = True
    service.cache_ttl_seconds = 3600
    service._cache = {}
    service._segment_cache = {}
    service.segment_max_concurrency = 2
    service._segment_semaphore = asyncio.Semaphore(2)
    service.total_timeout_cap_seconds = 600
    service.total_timeout_min_seconds = 30
    service.timeout_per_question_seconds = 10
    service.topic_check_enabled = False
    service.topic_overlap_threshold = 0.3
    return service


class _FakeAI:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error

    async def generate_json(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.mark.asyncio
async def test_generate_exam_paper_uses_segmented_path_for_long_content_and_then_top_level_cache():
    service = _make_service()
    calls = []

    async def fake_segmented(uploaded_content, num_questions, difficulty_distribution, max_segment_length):
        calls.append(
            {
                "uploaded_content": uploaded_content,
                "num_questions": num_questions,
                "max_segment_length": max_segment_length,
            }
        )
        return {
            "paper_title": "Segmented paper",
            "total_questions": num_questions,
            "difficulty_distribution": {"基础": num_questions, "提高": 0, "难题": 0},
            "questions": [
                {
                    "id": 1,
                    "type": "A1",
                    "difficulty": "基础",
                    "question": "Question 1",
                    "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
                    "correct_answer": "A",
                    "explanation": "Explanation 1",
                    "key_point": "KP1",
                }
            ],
            "knowledge_points": ["KP1"],
            "summary": {"coverage": "KP1"},
        }

    async def fail_single(*args, **kwargs):
        raise AssertionError("single-paper path should not run for long content")

    service._generate_paper_in_segments = fake_segmented
    service._generate_single_paper = fail_single

    long_content = "A" * 7000
    result_1 = await service.generate_exam_paper(long_content, num_questions=20)
    result_1["questions"][0]["question"] = "mutated"
    result_2 = await service.generate_exam_paper(long_content, num_questions=20)

    assert len(calls) == 1
    assert calls[0]["max_segment_length"] == 6000
    assert result_1["paper_title"] == "Segmented paper"
    assert result_2["questions"][0]["question"] == "Question 1"


@pytest.mark.asyncio
async def test_generate_exam_paper_raises_timeout_marker_for_long_running_job():
    service = _make_service()
    service._get_total_timeout_seconds = lambda content_length, num_questions: 0.01

    async def slow_single(*args, **kwargs):
        await asyncio.sleep(0.05)
        return {}

    service._generate_single_paper = slow_single
    service._generate_paper_in_segments = slow_single

    with pytest.raises(RuntimeError, match=r"^QUIZ_TIMEOUT\|"):
        await service.generate_exam_paper("short content", num_questions=5)


@pytest.mark.asyncio
async def test_segmented_generation_reuses_segment_cache_and_backfills_missing_questions():
    service = _make_service()
    generation_calls = []
    refill_calls = []

    def _question(question_id: int, key_point: str, *, question_text: str | None = None) -> dict:
        return {
            "id": question_id,
            "type": "A1",
            "difficulty": "基础",
            "question": question_text or f"Question {question_id}",
            "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
            "correct_answer": "A",
            "explanation": f"Explanation {question_id}",
            "key_point": key_point,
        }

    async def fake_generate_single_paper_with_limit(uploaded_content, num_questions, difficulty_distribution, segment_key=None):
        if uploaded_content == "SEGMENT-ONE" and segment_key is not None:
            generation_calls.append(("segment", uploaded_content, num_questions))
            result = {
                "questions": [
                    _question(1, "KP-ONE", question_text="Segment one primary"),
                    _question(2, "KP-ONE", question_text="Segment one duplicate"),
                ],
                "chapter_prediction": {
                    "book": "Medicine",
                    "chapter_id": "med_ch1",
                    "chapter_title": "Cardiology",
                    "confidence": "high",
                },
            }
            if segment_key:
                service._save_to_segment_cache(segment_key, result["questions"])
            return result

        if uploaded_content == "SEGMENT-TWO" and segment_key is not None:
            generation_calls.append(("segment", uploaded_content, num_questions))
            raise RuntimeError("segment failed")

        refill_calls.append((uploaded_content, num_questions))
        return {
            "questions": [_question(3, "KP-REFILL", question_text="Refill question")],
        }

    service._generate_single_paper_with_limit = fake_generate_single_paper_with_limit
    service._is_valid_question = lambda q, index: True
    service._is_placeholder_question = lambda q: "placeholder" in (q.get("question") or "").lower()
    service._create_placeholder_question = lambda question_id: _question(
        question_id,
        f"KP-PLACEHOLDER-{question_id}",
        question_text=f"placeholder-{question_id}",
    )
    service._normalize_chapter_prediction = lambda pred, content: pred
    service._infer_chapter_prediction = lambda content: {
        "book": "Fallback",
        "chapter_id": "",
        "chapter_title": "Fallback",
        "confidence": "low",
    }

    content = "SEGMENT-ONESEGMENT-TWO"
    result_1 = await service._generate_paper_in_segments(
        uploaded_content=content,
        num_questions=4,
        difficulty_distribution={"基础": 1.0, "提高": 0.0, "难题": 0.0},
        max_segment_length=11,
    )
    result_2 = await service._generate_paper_in_segments(
        uploaded_content=content,
        num_questions=4,
        difficulty_distribution={"基础": 1.0, "提高": 0.0, "难题": 0.0},
        max_segment_length=11,
    )

    assert result_1["total_questions"] == 4
    assert [q["question"] for q in result_1["questions"]] == [
        "Segment one primary",
        "Refill question",
        "placeholder-3",
        "placeholder-4",
    ]
    assert result_1["chapter_prediction"] == {
        "book": "Medicine",
        "chapter_id": "med_ch1",
        "chapter_title": "Cardiology",
        "confidence": "high",
    }
    assert result_1["knowledge_points"] == ["KP-ONE", "KP-REFILL", "KP-PLACEHOLDER-3", "KP-PLACEHOLDER-4"]

    segment_one_calls = [call for call in generation_calls if call[1] == "SEGMENT-ONE"]
    segment_two_calls = [call for call in generation_calls if call[1] == "SEGMENT-TWO"]
    assert len(segment_one_calls) == 1
    assert len(segment_two_calls) == 2
    assert len(refill_calls) == 2

    assert result_2["questions"][0]["question"] == "Segment one primary"


@pytest.mark.asyncio
async def test_generate_single_paper_adds_topic_warning_and_falls_back_to_inferred_chapter():
    service = _make_service()

    def _question(question_id: int, key_point: str, question_text: str, *, is_valid: bool = True) -> dict:
        return {
            "id": question_id,
            "type": "A1",
            "difficulty": "basic",
            "question": question_text,
            "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
            "correct_answer": "A",
            "explanation": f"Explanation {question_id}",
            "key_point": key_point,
            "is_valid": is_valid,
        }

    fallback_prediction = {
        "book": "Fallback Book",
        "chapter_id": "fallback_chapter",
        "chapter_title": "Fallback Title",
        "confidence": "medium",
    }
    service.ai = _FakeAI(
        {
            "paper_title": "Generated paper",
            "questions": [
                _question(101, "KP-ONE", "kept question"),
                _question(102, "KP-ONE", "duplicate question"),
                _question(103, "KP-THREE", "invalid question", is_valid=False),
            ],
            "chapter_prediction": {"book": "AI Book", "chapter_id": "ai_bad", "chapter_title": "AI Title"},
        }
    )
    service._get_chapter_catalog = lambda content: "catalog"
    service._infer_chapter_prediction = lambda content: fallback_prediction
    service._normalize_chapter_prediction = lambda pred, content: None
    service._is_valid_question = lambda q, index: q.get("is_valid", False)
    service._create_placeholder_question = lambda question_id: _question(
        question_id,
        f"KP-PLACEHOLDER-{question_id}",
        f"placeholder-{question_id}",
    )

    async def fake_topic_check(*args, **kwargs):
        return False, 0.12, "topic drift"

    service._validate_topic_consistency = fake_topic_check

    result = await service._generate_single_paper(
        uploaded_content="source content",
        num_questions=3,
        difficulty_distribution={"\u57fa\u7840": 1.0, "\u63d0\u9ad8": 0.0, "\u96be\u9898": 0.0},
    )

    assert result["total_questions"] == 3
    assert [q["id"] for q in result["questions"]] == [1, 2, 3]
    assert [q["question"] for q in result["questions"]] == [
        "kept question",
        "placeholder-2",
        "placeholder-3",
    ]
    assert result["summary"]["topic_warning"] == "topic drift"
    assert result["chapter_prediction"] == fallback_prediction


@pytest.mark.asyncio
async def test_generate_single_paper_returns_default_paper_with_error_message_on_ai_failure():
    service = _make_service()
    fallback_prediction = {
        "book": "Fallback Book",
        "chapter_id": "fallback_chapter",
        "chapter_title": "Fallback Title",
        "confidence": "medium",
    }
    service.ai = _FakeAI(error=RuntimeError("boom"))
    service._get_chapter_catalog = lambda content: "catalog"
    service._infer_chapter_prediction = lambda content: fallback_prediction
    service._generate_default_paper = lambda num_questions: {
        "paper_title": "default sentinel",
        "total_questions": num_questions,
        "difficulty_distribution": {},
        "questions": [{"id": idx, "question": f"default-{idx}"} for idx in range(1, num_questions + 1)],
        "summary": {"coverage": "fallback"},
        "sentinel": True,
    }

    result = await service._generate_single_paper(
        uploaded_content="source content",
        num_questions=2,
        difficulty_distribution={"\u57fa\u7840": 1.0, "\u63d0\u9ad8": 0.0, "\u96be\u9898": 0.0},
    )

    assert result["sentinel"] is True
    assert result["chapter_prediction"] == fallback_prediction
    assert result["total_questions"] == 2
    assert "boom" in result["error_message"]


@pytest.mark.asyncio
async def test_generate_single_paper_with_limit_saves_only_non_empty_segment_results_to_cache():
    service = _make_service()
    calls = []

    async def fake_generate_single_paper(uploaded_content, num_questions, difficulty_distribution):
        calls.append((uploaded_content, num_questions, difficulty_distribution))
        if uploaded_content == "segment body":
            return {"questions": [{"id": 1, "question": "Q1"}]}
        return {"questions": []}

    distribution = {"\u57fa\u7840": 1.0, "\u63d0\u9ad8": 0.0, "\u96be\u9898": 0.0}
    service._generate_single_paper = fake_generate_single_paper

    result = await service._generate_single_paper_with_limit(
        uploaded_content="segment body",
        num_questions=2,
        difficulty_distribution=distribution,
        segment_key="segment-key",
    )
    await service._generate_single_paper_with_limit(
        uploaded_content="empty segment",
        num_questions=1,
        difficulty_distribution=distribution,
        segment_key="empty-key",
    )

    assert result == {"questions": [{"id": 1, "question": "Q1"}]}
    assert calls == [
        ("segment body", 2, distribution),
        ("empty segment", 1, distribution),
    ]
    assert service._get_from_segment_cache("segment-key") == [{"id": 1, "question": "Q1"}]
    assert service._get_from_segment_cache("empty-key") is None


@pytest.mark.asyncio
async def test_segmented_generation_falls_back_to_inferred_chapter_when_segments_have_no_prediction():
    service = _make_service()

    def _question(question_id: int, key_point: str, question_text: str) -> dict:
        return {
            "id": question_id,
            "type": "A1",
            "difficulty": "basic",
            "question": question_text,
            "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
            "correct_answer": "A",
            "explanation": f"Explanation {question_id}",
            "key_point": key_point,
        }

    async def fake_generate_single_paper_with_limit(uploaded_content, num_questions, difficulty_distribution, segment_key=None):
        if uploaded_content == "SEGMENT-ONE":
            return {"questions": [_question(10, "KP-ONE", "segment-one")]}
        if uploaded_content == "SEGMENT-TWO":
            return {"questions": [_question(20, "KP-TWO", "segment-two")]}
        raise AssertionError(f"unexpected segment: {uploaded_content}")

    fallback_prediction = {
        "book": "Fallback Book",
        "chapter_id": "fallback_chapter",
        "chapter_title": "Fallback Title",
        "confidence": "low",
    }
    service._generate_single_paper_with_limit = fake_generate_single_paper_with_limit
    service._is_placeholder_question = lambda q: False
    service._infer_chapter_prediction = lambda content: fallback_prediction

    result = await service._generate_paper_in_segments(
        uploaded_content="SEGMENT-ONESEGMENT-TWO",
        num_questions=2,
        difficulty_distribution={"\u57fa\u7840": 1.0, "\u63d0\u9ad8": 0.0, "\u96be\u9898": 0.0},
        max_segment_length=11,
    )

    assert result["chapter_prediction"] == fallback_prediction
    assert [q["id"] for q in result["questions"]] == [1, 2]
    assert [q["question"] for q in result["questions"]] == ["segment-one", "segment-two"]


@pytest.mark.asyncio
async def test_segmented_generation_rebalances_tiny_tail_into_fewer_segments():
    service = _make_service()
    calls = []

    def _question(question_id: int, key_point: str, question_text: str) -> dict:
        return {
            "id": question_id,
            "type": "A1",
            "difficulty": "basic",
            "question": question_text,
            "options": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
            "correct_answer": "A",
            "explanation": f"Explanation {question_id}",
            "key_point": key_point,
        }

    async def fake_generate_single_paper_with_limit(uploaded_content, num_questions, difficulty_distribution, segment_key=None):
        calls.append((uploaded_content, num_questions))
        return {
            "questions": [
                _question(len(calls), f"KP-{len(calls)}", f"segment-{len(uploaded_content)}")
            ]
        }

    service._generate_single_paper_with_limit = fake_generate_single_paper_with_limit
    service._is_placeholder_question = lambda q: False
    service._infer_chapter_prediction = lambda content: {
        "book": "Fallback Book",
        "chapter_id": "fallback_chapter",
        "chapter_title": "Fallback Title",
        "confidence": "low",
    }

    result = await service._generate_paper_in_segments(
        uploaded_content="A" * 21,
        num_questions=2,
        difficulty_distribution={"\u57fa\u7840": 1.0, "\u63d0\u9ad8": 0.0, "\u96be\u9898": 0.0},
        max_segment_length=10,
    )

    assert calls == [("A" * 11, 1), ("A" * 10, 1)]
    assert [q["question"] for q in result["questions"]] == ["segment-11", "segment-10"]
