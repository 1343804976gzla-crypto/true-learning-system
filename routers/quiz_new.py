"""
错题本和测验路由
处理错题记录、复习、固定10道题练习
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api_contracts import (
    LegacyQuizStatsResponse,
    LegacyWrongAnswerListResponse,
    LegacyWrongAnswerReviewResponse,
    QuizSessionStartResponse,
    QuizSessionSubmitResponse,
)
from models import get_db, WrongAnswer, QuizSession, ConceptMastery, Chapter, TestRecord
from schemas import QuizResponse, QuizSubmitRequest, QuizResultResponse
from utils.answer import answers_match
from utils.data_contracts import (
    canonicalize_quiz_answers,
    canonicalize_quiz_questions,
    normalize_confidence,
    normalize_option_map,
)

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


def _normalize_legacy_question_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(item or {})
    payload["options"] = normalize_option_map(payload.get("options"))
    return payload


@router.post("/start/{chapter_id}", response_model=QuizSessionStartResponse)
async def start_quiz(
    chapter_id: str,
    mode: str = "practice",  # 'practice', 'wrong_answer_review', 'repeat'
    db: Session = Depends(get_db)
):
    """
    开始一次10道题的测验
    mode: practice(正常练习), wrong_answer_review(错题复习), repeat(重复练习)
    """
    questions = []
    
    if mode == "wrong_answer_review":
        # 从错题本中选取未掌握的错题
        wrong_answers = db.query(WrongAnswer).filter(
            WrongAnswer.concept_id.like(f"{chapter_id}%"),
            WrongAnswer.is_mastered == False
        ).order_by(WrongAnswer.next_review.asc()).limit(10).all()
        
        for wa in wrong_answers:
            questions.append({
                "question_id": f"wrong_{wa.id}",
                "concept_id": wa.concept_id,
                "question": wa.question,
                "options": wa.options,
                "correct_answer": wa.correct_answer,
                "explanation": wa.explanation,
                "is_wrong_answer": True,
                "wrong_answer_id": wa.id
            })
    
    elif mode == "repeat":
        # 从该章节的所有知识点中随机选取10道
        concepts = db.query(ConceptMastery).filter(
            ConceptMastery.chapter_id == chapter_id
        ).all()
        
        # 使用已有测试记录生成变式题
        for concept in concepts[:10]:
            # 查找该知识点的历史题目
            test_record = db.query(TestRecord).filter(
                TestRecord.concept_id == concept.concept_id
            ).order_by(TestRecord.tested_at.desc()).first()
            
            if test_record and test_record.ai_question:
                questions.append({
                    "question_id": f"repeat_{concept.concept_id}",
                    "concept_id": concept.concept_id,
                    "question": test_record.ai_question,
                    "options": test_record.ai_options,
                    "correct_answer": test_record.ai_correct_answer,
                    "explanation": test_record.ai_explanation,
                    "is_wrong_answer": False
                })
    
    else:  # practice 正常练习
        # 从该章节知识点生成新题目（这里简化处理，实际应调用AI生成）
        concepts = db.query(ConceptMastery).filter(
            ConceptMastery.chapter_id == chapter_id
        ).limit(10).all()
        
        for concept in concepts:
            # 查找或生成题目
            test_record = db.query(TestRecord).filter(
                TestRecord.concept_id == concept.concept_id
            ).first()
            
            if test_record:
                questions.append({
                    "question_id": f"practice_{concept.concept_id}",
                    "concept_id": concept.concept_id,
                    "question": test_record.ai_question,
                    "options": test_record.ai_options,
                    "correct_answer": test_record.ai_correct_answer,
                    "explanation": test_record.ai_explanation,
                    "is_wrong_answer": False
                })
    
    # 如果不足10题，补充空白
    while len(questions) < 10:
        questions.append({
            "question_id": f"empty_{len(questions)}",
            "concept_id": "",
            "question": "题目生成中...",
            "options": {"A": "", "B": "", "C": "", "D": ""},
            "correct_answer": "A",
            "explanation": "",
            "is_wrong_answer": False
        })
    
    # 创建测验会话
    normalized_questions = canonicalize_quiz_questions(questions)

    session = QuizSession(
        session_type=mode,
        chapter_id=chapter_id,
        questions=normalized_questions,
        answers=[],
        total_questions=10,
        correct_count=0,
        score=0
    )
    db.add(session)
    db.commit()
    
    normalized_questions = [_normalize_legacy_question_payload(question) for question in normalized_questions]

    return {
        "session_id": session.id,
        "mode": mode,
        "total_questions": 10,
        "questions": normalized_questions
    }


@router.post("/submit/{session_id}", response_model=QuizSessionSubmitResponse)
async def submit_quiz(
    session_id: int,
    data: QuizSubmitRequest,
    db: Session = Depends(get_db)
):
    """
    提交测验答案
    """
    session = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="测验会话不存在")
    
    # 记录答案
    answers = []
    correct_count = 0
    
    for idx, answer in enumerate(data.answers):
        question = session.questions[idx] if idx < len(session.questions) else None
        if not question:
            continue
        
        is_correct = answers_match(answer.user_answer, question.get("correct_answer") or "")
        if is_correct:
            correct_count += 1
        
        normalized_confidence = normalize_confidence(answer.confidence)
        if normalized_confidence not in {"sure", "unsure", "no"}:
            normalized_confidence = None
        answer_record = {
            "question_index": idx,
            "user_answer": answer.user_answer,
            "is_correct": is_correct,
            "time_spent": answer.time_spent,
            "confidence": normalized_confidence
        }
        answers.append(answer_record)
        
        # 如果答错，记录到错题本
        if not is_correct:
            # 检查是否已存在
            existing = db.query(WrongAnswer).filter(
                WrongAnswer.concept_id == question["concept_id"],
                WrongAnswer.question == question["question"]
            ).first()
            
            if existing:
                # 更新错误次数
                existing.review_count += 1
                existing.last_reviewed = datetime.now()
                existing.user_answer = answer.user_answer
            else:
                # 创建新错题记录
                wrong_answer = WrongAnswer(
                    concept_id=question["concept_id"],
                    question=question["question"],
                    options=question["options"],
                    correct_answer=question["correct_answer"],
                    user_answer=answer.user_answer,
                    explanation=question["explanation"],
                    error_type="unknown",  # 可后续分析
                    weak_points=[],
                    review_count=1,
                    last_reviewed=datetime.now(),
                    next_review=date.today() + timedelta(days=1),
                    mastery_level=0,
                    is_mastered=False
                )
                db.add(wrong_answer)
    
    # 更新会话
    session.answers = canonicalize_quiz_answers(answers)
    session.correct_count = correct_count
    session.score = int(correct_count / 10 * 100)
    session.completed_at = datetime.now()
    db.commit()
    
    return {
        "session_id": session_id,
        "score": session.score,
        "correct_count": correct_count,
        "wrong_count": 10 - correct_count,
        "answers": answers
    }


@router.get("/wrong-answers/{chapter_id}", response_model=LegacyWrongAnswerListResponse)
async def get_wrong_answers(
    chapter_id: str,
    include_mastered: bool = False,
    db: Session = Depends(get_db)
):
    """
    获取错题本
    """
    query = db.query(WrongAnswer).filter(
        WrongAnswer.concept_id.like(f"{chapter_id}%")
    )
    
    if not include_mastered:
        query = query.filter(WrongAnswer.is_mastered == False)
    
    wrong_answers = query.order_by(WrongAnswer.next_review.asc()).all()
    
    return {
        "total": len(wrong_answers),
        "chapter_id": chapter_id,
        "wrong_answers": [
            {
                "id": wa.id,
                "concept_id": wa.concept_id,
                "question": wa.question,
                "options": normalize_option_map(wa.options),
                "correct_answer": wa.correct_answer,
                "user_answer": wa.user_answer,
                "explanation": wa.explanation,
                "error_type": wa.error_type,
                "review_count": wa.review_count,
                "mastery_level": wa.mastery_level,
                "is_mastered": wa.is_mastered,
                "next_review": wa.next_review.isoformat() if wa.next_review else None,
                "created_at": wa.created_at.isoformat() if wa.created_at else None
            }
            for wa in wrong_answers
        ]
    }


@router.post("/wrong-answers/{wrong_id}/review", response_model=LegacyWrongAnswerReviewResponse)
async def review_wrong_answer(
    wrong_id: int,
    is_correct: bool,  # 这次是否答对
    db: Session = Depends(get_db)
):
    """
    复习错题，更新掌握状态
    """
    wrong = db.query(WrongAnswer).filter(WrongAnswer.id == wrong_id).first()
    if not wrong:
        raise HTTPException(status_code=404, detail="错题不存在")
    
    # 更新复习状态
    wrong.review_count += 1
    wrong.last_reviewed = datetime.now()
    
    if is_correct:
        # 答对了，提升掌握等级
        wrong.mastery_level = min(wrong.mastery_level + 1, 5)
        
        # 如果连续答对3次，标记为已掌握
        if wrong.mastery_level >= 3:
            wrong.is_mastered = True
        
        # 延长下次复习时间（间隔重复）
        intervals = [1, 3, 7, 14, 30]  # 1天, 3天, 7天, 14天, 30天
        interval = intervals[min(wrong.mastery_level, len(intervals)-1)]
        wrong.next_review = date.today() + timedelta(days=interval)
    else:
        # 又答错了，降低掌握等级
        wrong.mastery_level = max(wrong.mastery_level - 1, 0)
        wrong.is_mastered = False
        wrong.next_review = date.today() + timedelta(days=1)  # 明天再复习
    
    db.commit()
    
    return {
        "id": wrong_id,
        "mastery_level": wrong.mastery_level,
        "is_mastered": wrong.is_mastered,
        "next_review": wrong.next_review.isoformat(),
        "review_count": wrong.review_count
    }


@router.get("/stats/{chapter_id}", response_model=LegacyQuizStatsResponse)
async def get_quiz_stats(
    chapter_id: str,
    db: Session = Depends(get_db)
):
    """
    获取测验统计
    """
    # 总测验次数
    total_sessions = db.query(QuizSession).filter(
        QuizSession.chapter_id == chapter_id
    ).count()
    
    # 错题数量
    wrong_count = db.query(WrongAnswer).filter(
        WrongAnswer.concept_id.like(f"{chapter_id}%"),
        WrongAnswer.is_mastered == False
    ).count()
    
    # 待复习错题
    due_wrong = db.query(WrongAnswer).filter(
        WrongAnswer.concept_id.like(f"{chapter_id}%"),
        WrongAnswer.is_mastered == False,
        WrongAnswer.next_review <= date.today()
    ).count()
    
    return {
        "total_sessions": total_sessions,
        "wrong_answer_count": wrong_count,
        "due_for_review": due_wrong
    }
