from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.pre_generated_quiz as pre_generated_module


class _FakeAI:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error

    async def generate_json(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._result


@pytest.mark.asyncio
async def test_pre_generated_quiz_normalizes_malformed_ai_payload(monkeypatch):
    monkeypatch.setattr(
        pre_generated_module,
        "get_ai_client",
        lambda: _FakeAI(
            {
                "question": "",
                "options": None,
                "correct_answer": "Z",
                "explanation": "",
                "key_points": None,
                "difficulty": "",
                "common_mistakes": None,
            }
        ),
    )

    service = pre_generated_module.PreGeneratedQuizService()
    result = await service.generate_quiz_with_answer("Shock")

    assert result["question"] == "About Shock, which statement is correct?"
    assert result["options"] == {
        "A": "Option A",
        "B": "Option B",
        "C": "Option C",
        "D": "Option D",
    }
    assert result["correct_answer"] == "A"
    assert result["explanation"] == "No explanation returned."
    assert result["key_points"] == ["Shock"]
    assert result["difficulty"] == "medium"
    assert result["common_mistakes"] == []
    assert result["concept_name"] == "Shock"
    assert result["generated_at"]


@pytest.mark.asyncio
async def test_pre_generated_batch_uses_fallback_for_exceptions(monkeypatch):
    monkeypatch.setattr(pre_generated_module, "get_ai_client", lambda: _FakeAI({}))
    service = pre_generated_module.PreGeneratedQuizService()

    async def fake_generate_quiz_with_answer(concept_name: str, concept_description: str = ""):
        if concept_name == "B":
            raise RuntimeError("boom")
        return {"question": f"Q-{concept_name}", "concept_name": concept_name}

    service.generate_quiz_with_answer = fake_generate_quiz_with_answer

    result = await service.generate_batch(["A", "B"], ["desc-a", "desc-b"])

    assert result[0] == {"question": "Q-A", "concept_name": "A"}
    assert result[1]["is_fallback"] is True
    assert result[1]["concept_name"] == "B"


def test_local_grader_uses_normalized_answers():
    grader = pre_generated_module.LocalGrader()
    result = grader.grade_answer(
        quiz={
            "correct_answer": "B",
            "explanation": "Explanation",
            "key_points": ["Point"],
            "common_mistakes": ["Mistake"],
        },
        user_answer="B. selected",
        confidence="sure",
    )

    assert result["is_correct"] is True
    assert result["score"] == 100
    assert result["correct_answer"] == "B"
    assert result["user_answer"] == "B"
    assert result["error_type"] is None
    assert result["weak_points"] == []


@pytest.mark.asyncio
async def test_comprehensive_analyzer_falls_back_when_ai_fails(monkeypatch):
    monkeypatch.setattr(
        pre_generated_module,
        "get_ai_client",
        lambda: _FakeAI(error=RuntimeError("analysis down")),
    )

    analyzer = pre_generated_module.ComprehensiveAnalyzer()
    quizzes = [
        {"concept_name": "Shock", "question": "Q1", "correct_answer": "A", "key_points": ["Shock"]},
        {"concept_name": "Failure", "question": "Q2", "correct_answer": "B", "key_points": ["Failure"]},
    ]
    graded_results = [
        {"is_correct": True, "weak_points": [], "error_type": None, "confidence_analysis": "aligned"},
        {"is_correct": False, "weak_points": ["Failure"], "error_type": "knowledge_gap", "confidence_analysis": "misaligned"},
    ]
    answers = [
        {"user_answer": "A", "confidence": "sure"},
        {"user_answer": "C", "confidence": "mystery"},
    ]

    result = await analyzer.analyze_comprehensive(quizzes, graded_results, answers)

    assert result["score"] == 50
    assert result["correct_count"] == 1
    assert result["wrong_count"] == 1
    assert result["confidence_stats"] == {"sure": 1, "unsure": 0, "no": 0, "missing": 1}
    assert result["error_types"] == {"blind_spot": 0, "knowledge_gap": 1, "unknown": 0}
    assert result["priority_topics"] == ["Failure"]
    assert result["weak_points_summary"] == ["Failure"]
