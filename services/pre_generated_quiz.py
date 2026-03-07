"""
Pre-generated quiz service.
Generates question + answer + explanation together for fast grading flows.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.ai_client import get_ai_client


class PreGeneratedQuizService:
    """Generate ready-to-grade quizzes."""

    def __init__(self):
        self.ai = get_ai_client()

    async def generate_quiz_with_answer(
        self,
        concept_name: str,
        concept_description: str = "",
    ) -> Dict[str, Any]:
        """Generate one quiz item with answer and explanation."""
        prompt = f"""You are a medical exam question writer.
Create ONE multiple-choice question for this concept.
Concept: {concept_name}
Extra focus: {concept_description}

Rules:
1. 4 options only: A/B/C/D.
2. Exactly one correct answer.
3. Distractors must be plausible.
4. Explanation should include why correct and why others are wrong.

Return JSON only.
"""

        schema = {
            "question": "Question text",
            "options": {
                "A": "Option A",
                "B": "Option B",
                "C": "Option C",
                "D": "Option D",
            },
            "correct_answer": "A",
            "explanation": "Detailed explanation",
            "key_points": ["key point"],
            "difficulty": "easy/medium/hard",
            "common_mistakes": ["mistake"],
        }

        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=2000, use_heavy=True, timeout=240)
            if not isinstance(result, dict):
                raise ValueError("AI result is not a dict")

            result.setdefault("question", f"About {concept_name}, which statement is correct?")
            result.setdefault("options", {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"})
            result.setdefault("correct_answer", "A")
            result.setdefault("explanation", "No explanation returned.")
            result.setdefault("key_points", [concept_name])
            result.setdefault("difficulty", "medium")
            result.setdefault("common_mistakes", [])

            result["concept_name"] = concept_name
            result["generated_at"] = datetime.now().isoformat()
            return result
        except Exception as e:
            print(f"[pre-gen] generation failed for concept={concept_name}: {e}")
            return self._create_fallback_quiz(concept_name)

    async def generate_batch(
        self,
        concept_names: List[str],
        concept_descriptions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate up to 10 quiz items in parallel."""
        concept_names = concept_names[:10]
        if concept_descriptions is None:
            concept_descriptions = [""] * len(concept_names)
        else:
            concept_descriptions = (concept_descriptions + [""] * len(concept_names))[: len(concept_names)]

        tasks = [
            self.generate_quiz_with_answer(name, concept_descriptions[i])
            for i, name in enumerate(concept_names)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        quizzes: List[Dict[str, Any]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"[pre-gen] batch item {i + 1} failed: {result}")
                quizzes.append(self._create_fallback_quiz(concept_names[i]))
            else:
                quizzes.append(result)

        return quizzes

    def _create_fallback_quiz(self, concept_name: str) -> Dict[str, Any]:
        """Fallback item when AI call fails."""
        return {
            "question": f"About {concept_name}, which statement is correct?",
            "options": {
                "A": f"{concept_name} is clinically important.",
                "B": f"{concept_name} has no diagnostic value.",
                "C": f"{concept_name} is unrelated to treatment decisions.",
                "D": "A is correct.",
            },
            "correct_answer": "D",
            "explanation": f"This fallback item checks basic understanding of {concept_name}.",
            "key_points": [concept_name],
            "difficulty": "medium",
            "common_mistakes": [f"Ignoring the core meaning of {concept_name}"],
            "concept_name": concept_name,
            "is_fallback": True,
        }


class LocalGrader:
    """Grade answers locally without AI."""

    def grade_answer(self, quiz: Dict[str, Any], user_answer: str, confidence: str) -> Dict[str, Any]:
        """Grade one answer."""
        # 清理答案：去除空格、点号、逗号等，只保留字母A-E
        def clean_answer(ans):
            import re
            return re.sub(r'[^A-E]', '', (ans or "").strip().upper())

        correct_answer = clean_answer(quiz.get("correct_answer"))
        user_answer = clean_answer(user_answer)

        if quiz.get("type") == "X":
            is_correct = sorted(user_answer) == sorted(correct_answer)
        else:
            is_correct = user_answer == correct_answer

        score = 100 if is_correct else 0
        error_type = None
        if not is_correct:
            if confidence == "sure":
                error_type = "blind_spot"
            elif confidence == "unsure":
                error_type = "knowledge_gap"
            else:
                error_type = "unknown"

        if is_correct:
            feedback = "Correct."
            confidence_analysis = "Confidence and correctness are aligned."
        else:
            feedback = "Incorrect. Review the core concept and distractors."
            confidence_analysis = "Confidence and correctness are misaligned."

        return {
            "is_correct": is_correct,
            "score": score,
            "correct_answer": correct_answer,
            "user_answer": user_answer,
            "feedback": feedback,
            "explanation": quiz.get("explanation", ""),
            "error_type": error_type,
            "confidence_analysis": confidence_analysis,
            "key_points": quiz.get("key_points", []),
            "common_mistakes": quiz.get("common_mistakes", []),
            "weak_points": [] if is_correct else quiz.get("key_points", []),
        }

    def grade_batch(self, quizzes: List[Dict[str, Any]], answers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Grade a batch of answers."""
        results = []
        for quiz, answer in zip(quizzes, answers):
            results.append(
                self.grade_answer(
                    quiz,
                    answer.get("user_answer", ""),
                    answer.get("confidence", "unsure"),
                )
            )
        return results


class ComprehensiveAnalyzer:
    """Generate session-level analysis."""

    def __init__(self):
        self.ai = get_ai_client()

    async def analyze_comprehensive(
        self,
        quizzes: List[Dict[str, Any]],
        graded_results: List[Dict[str, Any]],
        answers: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyze all answers together."""
        total = max(1, len(graded_results))
        correct_count = sum(1 for r in graded_results if r.get("is_correct"))
        wrong_count = total - correct_count
        score = int(correct_count / total * 100)

        confidence_stats = {"sure": 0, "unsure": 0, "dont_know": 0}
        for a in answers:
            c = a.get("confidence", "unsure")
            confidence_stats[c] = confidence_stats.get(c, 0) + 1

        all_weak_points: List[str] = []
        for r in graded_results:
            all_weak_points.extend(r.get("weak_points", []))

        error_types = {"blind_spot": 0, "knowledge_gap": 0, "unknown": 0}
        for r in graded_results:
            et = r.get("error_type")
            if et:
                error_types[et] = error_types.get(et, 0) + 1

        answer_details = []
        for i, (quiz, result, answer) in enumerate(zip(quizzes, graded_results, answers)):
            answer_details.append(
                {
                    "index": i + 1,
                    "concept": quiz.get("concept_name"),
                    "question": (quiz.get("question") or "")[:80],
                    "correct_answer": quiz.get("correct_answer"),
                    "user_answer": answer.get("user_answer"),
                    "is_correct": result.get("is_correct"),
                    "confidence": answer.get("confidence"),
                    "confidence_analysis": result.get("confidence_analysis"),
                    "key_points": quiz.get("key_points", []),
                    "error_type": result.get("error_type"),
                }
            )

        prompt = self._build_analysis_prompt(
            score,
            correct_count,
            wrong_count,
            confidence_stats,
            error_types,
            answer_details,
            all_weak_points,
        )

        schema = {
            "overall_assessment": "Overall summary",
            "score_analysis": "Score analysis",
            "confidence_analysis": "Confidence analysis",
            "strengths": ["strength"],
            "weaknesses": ["weakness"],
            "danger_zones": ["danger zone"],
            "study_recommendations": ["recommendation"],
            "priority_topics": ["topic"],
            "memory_tips": ["tip"],
            "next_steps": "next step",
        }

        try:
            analysis = await self.ai.generate_json(prompt, schema, max_tokens=2000, use_heavy=True, timeout=360)
            analysis["score"] = score
            analysis["correct_count"] = correct_count
            analysis["wrong_count"] = wrong_count
            analysis["confidence_stats"] = confidence_stats
            analysis["error_types"] = error_types
            analysis["weak_points_summary"] = list(set(all_weak_points))
            return analysis
        except Exception as e:
            print(f"[pre-gen] comprehensive analysis failed: {e}")
            return self._create_fallback_analysis(score, correct_count, wrong_count, all_weak_points)

    def _build_analysis_prompt(
        self,
        score: int,
        correct_count: int,
        wrong_count: int,
        confidence_stats: Dict[str, int],
        error_types: Dict[str, int],
        answer_details: List[Dict[str, Any]],
        weak_points: List[str],
    ) -> str:
        """Build AI prompt for comprehensive analysis."""
        details_text = []
        for d in answer_details:
            status = "OK" if d["is_correct"] else "WRONG"
            details_text.append(
                f"{d['index']}. [{status}] {d['concept']} | "
                f"user={d['user_answer']} correct={d['correct_answer']} | "
                f"conf={d['confidence']}"
            )

        return (
            "Analyze this medical quiz session and return JSON only.\n"
            f"Score={score}, correct={correct_count}, wrong={wrong_count}.\n"
            f"Confidence stats={confidence_stats}.\n"
            f"Error types={error_types}.\n"
            f"Details:\n" + "\n".join(details_text) + "\n"
            f"Weak points={list(set(weak_points))}."
        )

    def _create_fallback_analysis(
        self,
        score: int,
        correct_count: int,
        wrong_count: int,
        weak_points: List[str],
    ) -> Dict[str, Any]:
        """Fallback analysis payload."""
        return {
            "overall_assessment": f"Score {score}. Correct {correct_count}, wrong {wrong_count}.",
            "score_analysis": "Review weak topics and redo related questions.",
            "confidence_analysis": "Compare confidence vs correctness to detect blind spots.",
            "strengths": ["Completed full quiz"],
            "weaknesses": ["Some concepts need reinforcement"],
            "danger_zones": [],
            "study_recommendations": ["Review wrong answers", "Summarize key mechanisms", "Retest tomorrow"],
            "priority_topics": list(set(weak_points))[:5],
            "memory_tips": ["Use spaced repetition", "Create contrast tables"],
            "next_steps": "Focus on top weak points and rerun a quiz set.",
            "score": score,
            "correct_count": correct_count,
            "wrong_count": wrong_count,
            "confidence_stats": {"sure": 0, "unsure": 0, "dont_know": 0},
            "error_types": {"blind_spot": 0, "knowledge_gap": 0, "unknown": 0},
            "weak_points_summary": list(set(weak_points)),
        }


_pre_gen_service = None
_local_grader = None
_comprehensive_analyzer = None


def get_pre_gen_service():
    global _pre_gen_service
    if _pre_gen_service is None:
        _pre_gen_service = PreGeneratedQuizService()
    return _pre_gen_service


def get_local_grader():
    global _local_grader
    if _local_grader is None:
        _local_grader = LocalGrader()
    return _local_grader


def get_comprehensive_analyzer():
    global _comprehensive_analyzer
    if _comprehensive_analyzer is None:
        _comprehensive_analyzer = ComprehensiveAnalyzer()
    return _comprehensive_analyzer
