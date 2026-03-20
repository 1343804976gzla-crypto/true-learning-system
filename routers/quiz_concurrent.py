"""
骞跺彂娴嬮獙璺敱 - 浼樺寲鐨?0棰樼粌涔?骞跺彂鐢熸垚銆佹壒閲忔壒鏀广€丄I鎬荤粨
"""

from datetime import date, datetime, timedelta
import random
from types import SimpleNamespace
from typing import List

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api_contracts import QuizAnalysisResponse, QuizSessionStartResponse, QuizSessionSubmitResponse
from models import get_db, QuizSession, ConceptMastery, Chapter, TestRecord, WrongAnswer
from services.concurrent_quiz import (
    get_concurrent_generator, 
    get_batch_grader,
    get_ai_analyzer
)
from utils.data_contracts import (
    canonicalize_string_list,
    canonicalize_quiz_answers,
    canonicalize_quiz_questions,
    normalize_confidence,
    normalize_option_map,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quiz-v2", tags=["quiz-v2"])


def _normalize_name(name: str) -> str:
    return "".join((name or "").strip().lower().split())


def _concept_rank(concept: ConceptMastery) -> int:
    cid = (concept.concept_id or "").lower()
    rank = 0
    if "_repeat_" not in cid:
        rank += 2
    if "_seed_" not in cid:
        rank += 1
    return rank


def _build_concept_slots(concepts: List[ConceptMastery], target: int = 10) -> List[SimpleNamespace]:
    """Build concept slots, capping at available unique concepts to avoid duplicates.

    When unique concepts < target, we generate fewer slots rather than cycling
    the same concept name through modulo — AI can't reliably produce distinct
    questions when it receives the same concept_name multiple times.
    """
    best_by_name = {}
    for concept in concepts:
        name = (concept.name or "").strip()
        if not name:
            continue
        key = _normalize_name(name)
        current = best_by_name.get(key)
        if current is None or _concept_rank(concept) > _concept_rank(current):
            best_by_name[key] = concept

    selected = list(best_by_name.values())
    if not selected:
        return []

    random.shuffle(selected)

    # Cap at available unique concepts — no modulo cycling
    actual_count = min(target, len(selected))
    slots: List[SimpleNamespace] = []

    for i in range(actual_count):
        slots.append(
            SimpleNamespace(
                concept_id=selected[i].concept_id,
                name=selected[i].name,
                description="",
            )
        )

    if actual_count < target:
        logger.warning("知识点不足: %d/%d，生成 %d 题", len(selected), target, actual_count)

    return slots


@router.post("/start/{chapter_id}", response_model=QuizSessionStartResponse)
async def start_concurrent_quiz(
    chapter_id: str,
    db: Session = Depends(get_db)
):
    """
    寮€濮?0棰樺苟鍙戞祴楠?    骞跺彂鐢熸垚10閬撻鐩紝澶у箙鎻愬崌閫熷害
    """
    concepts = db.query(ConceptMastery).filter(
        ConceptMastery.chapter_id == chapter_id
    ).all()

    # 鑷姩淇锛氳嫢绔犺妭瀛樺湪浣?concept_mastery 缂哄け锛屽垯浠?chapters.concepts 鍥炲～
    if not concepts:
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        repaired = 0
        if chapter and isinstance(chapter.concepts, list):
            for i, item in enumerate(chapter.concepts):
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                concept_id = (item.get("id") or f"{chapter_id}_auto_{i}").strip()
                exists = db.query(ConceptMastery).filter(
                    ConceptMastery.concept_id == concept_id
                ).first()
                if exists:
                    continue
                db.add(ConceptMastery(
                    concept_id=concept_id,
                    chapter_id=chapter_id,
                    name=name,
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                ))
                repaired += 1
        if repaired:
            db.commit()
            logger.info("[quiz-v2] 自动回填知识点 chapter=%s, repaired=%s", chapter_id, repaired)
        concepts = db.query(ConceptMastery).filter(
            ConceptMastery.chapter_id == chapter_id
        ).all()

        # 鍐嶉檷绾э細绔犺妭娌℃湁缁撴瀯鍖栫煡璇嗙偣鏃讹紝鑷姩娉ㄥ叆绔犺妭绾х瀛愮煡璇嗙偣锛岄伩鍏嶆帴鍙ｇ洿鎺?04
        if not concepts and chapter:
            base = (chapter.chapter_title or chapter.chapter_number or chapter_id).strip()
            seed_topics = [
                f"{base}-鍩虹姒傚康",
                f"{base}-鐥呭洜鏈哄埗",
                f"{base}-涓村簥琛ㄧ幇",
                f"{base}-璇婃柇瑕佺偣",
                f"{base}-娌荤枟鍘熷垯",
            ]
            seeded = 0
            for i, name in enumerate(seed_topics):
                concept_id = f"{chapter_id}_seed_{i}"
                exists = db.query(ConceptMastery).filter(
                    ConceptMastery.concept_id == concept_id
                ).first()
                if exists:
                    continue
                db.add(ConceptMastery(
                    concept_id=concept_id,
                    chapter_id=chapter_id,
                    name=name,
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                ))
                seeded += 1
            if seeded:
                db.commit()
                logger.info("[quiz-v2] 注入章节种子知识点 chapter=%s, seeded=%s", chapter_id, seeded)
            concepts = db.query(ConceptMastery).filter(
                ConceptMastery.chapter_id == chapter_id
            ).all()

    if not concepts:
        raise HTTPException(status_code=404, detail="No available concepts in this chapter. Please upload or enrich chapter concepts first.")
    concepts = _build_concept_slots(concepts, target=10)
    if not concepts:
        raise HTTPException(status_code=404, detail="No available concepts in this chapter. Please upload or enrich chapter concepts first.")

    concept_names = [c.name for c in concepts]
    concept_descriptions = [c.description for c in concepts]
    
    logger.info("开始为 %d 个知识点生成题目...", len(concept_names))
    
    
    generator = get_concurrent_generator()
    import asyncio
    quizzes = await generator.generate_quiz_batch(concept_names, concept_descriptions)
    
    logger.info("[quiz-v2] generated %d questions", len(quizzes))
    
    
    questions = []
    for i, (quiz, concept) in enumerate(zip(quizzes, concepts)):
        normalized_options = normalize_option_map(quiz.get("options"))
        test_record = TestRecord(
            concept_id=concept.concept_id,
            test_type="ai_quiz_concurrent",
            ai_question=quiz["question"],
            ai_options=normalized_options,
            ai_correct_answer=quiz.get("correct_answer", "A"),
            ai_explanation=quiz.get("explanation", ""),
            score=0
        )
        db.add(test_record)
        db.commit()
        db.refresh(test_record)
        
        questions.append({
            "question_id": f"q_{test_record.id}",
            "test_id": test_record.id,
            "concept_id": concept.concept_id,
            "concept_name": concept.name,
            "question": quiz["question"],
            "options": normalized_options,
            "correct_answer": quiz.get("correct_answer", "A"),
            "explanation": quiz.get("explanation", ""),
            "key_points": quiz.get("key_points", []),
            "difficulty": quiz.get("difficulty", "medium")
        })
    
    
    normalized_questions = canonicalize_quiz_questions(questions)

    session = QuizSession(
        session_type="concurrent_practice",
        chapter_id=chapter_id,
        questions=normalized_questions,
        answers=[],
        total_questions=len(normalized_questions),
        correct_count=0,
        score=0,
        started_at=datetime.now()
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    
    return {
        "session_id": session.id,
        "total_questions": len(normalized_questions),
        "questions": normalized_questions,
        "generation_method": "concurrent"
    }


@router.post("/submit/{session_id}", response_model=QuizSessionSubmitResponse)
async def submit_concurrent_quiz(
    session_id: int,
    data: dict,
    db: Session = Depends(get_db)
):
    """
    鎻愪氦10棰樺苟鍙戞祴楠岀瓟妗?    鎵归噺鎵规敼锛孉I鎬荤粨鍒嗘瀽
    """
    session = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Quiz session not found")
    
    answers = data.get("answers", [])
    if len(answers) != len(session.questions):
        raise HTTPException(status_code=400, detail="Invalid request payload")
    
    logger.info("开始批改 %d 道题目...", len(answers))
    
    
    grader = get_batch_grader()
    import asyncio
    graded_results = await grader.grade_batch(session.questions, answers)
    
    logger.info("开始批改 %d 道题目...", len(answers))
    
    
    answer_records = []
    correct_count = 0
    
    for i, (question, answer, graded) in enumerate(zip(session.questions, answers, graded_results)):
        is_correct = graded.get("is_correct", False)
        normalized_weak_points = canonicalize_string_list(graded.get("weak_points"))
        normalized_options = normalize_option_map(question.get("options"))
        if is_correct:
            correct_count += 1
        
        normalized_confidence = normalize_confidence(answer.get("confidence"))
        if normalized_confidence not in {"sure", "unsure", "no"}:
            normalized_confidence = None
        record = {
            "question_index": i,
            "test_id": question["test_id"],
            "user_answer": answer.get("user_answer"),
            "is_correct": is_correct,
            "confidence": normalized_confidence,
            "time_spent": answer.get("time_spent", 0),
            "score": graded.get("score", 0),
            "feedback": graded.get("feedback", ""),
            "weak_points": normalized_weak_points
        }
        answer_records.append(record)
        
        
        test = db.query(TestRecord).filter(TestRecord.id == question["test_id"]).first()
        if test:
            test.user_answer = answer.get("user_answer")
            test.confidence = normalized_confidence
            test.is_correct = is_correct
            test.ai_feedback = graded.get("feedback", "")
            test.weak_points = normalized_weak_points
            test.score = graded.get("score", 0)
        
        
        concept = db.query(ConceptMastery).filter(
            ConceptMastery.concept_id == question["concept_id"]
        ).first()
        if concept:
            if is_correct:
                concept.retention = min(concept.retention + 0.1, 1.0)
            else:
                concept.retention = max(concept.retention - 0.05, 0.0)
            concept.last_tested = date.today()
        
        # 璁板綍閿欓
        if not is_correct:
            existing = db.query(WrongAnswer).filter(
                WrongAnswer.concept_id == question["concept_id"],
                WrongAnswer.question == question["question"]
            ).first()
            
            if not existing:
                wrong = WrongAnswer(
                    concept_id=question["concept_id"],
                    question=question["question"],
                    options=normalized_options,
                    correct_answer=question["correct_answer"],
                    user_answer=answer.get("user_answer"),
                    explanation=question["explanation"],
                    error_type=graded.get("error_type", "unknown"),
                    weak_points=normalized_weak_points,
                    review_count=1,
                    last_reviewed=datetime.now(),
                    next_review=date.today() + timedelta(days=1),
                    mastery_level=0,
                    is_mastered=False
                )
                db.add(wrong)
            else:
                existing.review_count += 1
                existing.last_reviewed = datetime.now()
                existing.user_answer = answer.get("user_answer")
                existing.options = normalized_options
                existing.weak_points = normalized_weak_points
    
    # AI鎬荤粨鍒嗘瀽
    logger.info("开始生成综合分析报告...")
    analyzer = get_ai_analyzer()
    analysis = await analyzer.analyze_session(
        session.questions,
        graded_results,
        answers
    )
    logger.info("批改完成")
    
    # 鏇存柊浼氳瘽
    normalized_answers = canonicalize_quiz_answers(answer_records)
    session.answers = normalized_answers
    session.correct_count = correct_count
    session.score = int(correct_count / len(session.questions) * 100)
    session.completed_at = datetime.now()
    db.commit()
    
    return {
        "session_id": session_id,
        "score": session.score,
        "correct_count": correct_count,
        "wrong_count": len(session.questions) - correct_count,
        "answers": normalized_answers,
        "ai_analysis": analysis
    }


@router.get("/analysis/{session_id}", response_model=QuizAnalysisResponse)
async def get_analysis(
    session_id: int,
    db: Session = Depends(get_db)
):
    """
    鑾峰彇娴嬮獙鐨凙I鍒嗘瀽鎶ュ憡
    """
    session = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Quiz session not found")
    
    if not session.answers:
        raise HTTPException(status_code=400, detail="娴嬮獙灏氭湭瀹屾垚")
    
    
    analyzer = get_ai_analyzer()
    import asyncio
    analysis = await analyzer.analyze_session(
        session.questions,
        session.answers,
        session.answers
    )
    
    return {
        "session_id": session_id,
        "score": session.score,
        "analysis": analysis
    }



