"""
错题本和测验路由 - 优化版
处理错题记录、复习、固定10道题练习
重点优化: AI出题并行化
"""

import asyncio
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
from schemas import QuizSubmitRequest, GeneratedQuiz, QuizSubmission, QuizResult, QuizOption
from services.quiz_service import get_quiz_service
from utils.answer import answers_match
from utils.data_contracts import (
    canonicalize_string_list,
    canonicalize_quiz_answers,
    canonicalize_quiz_questions,
    coerce_confidence,
    normalize_confidence,
    normalize_option_map,
)

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


def _normalize_legacy_question_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(item or {})
    payload["options"] = normalize_option_map(payload.get("options"))
    return payload


def _to_quiz_option_payload(options: Any) -> Dict[str, str]:
    normalized = normalize_option_map(options)
    return {key: normalized.get(key, "") for key in ("A", "B", "C", "D")}


@router.post("/generate/{concept_id}", response_model=GeneratedQuiz)
async def generate_quiz(
    concept_id: str,
    db: Session = Depends(get_db)
):
    """
    为指定知识点生成AI题目 (单题模式 - 兼容旧版)
    """
    # 获取知识点
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == concept_id
    ).first()
    
    if not concept:
        raise HTTPException(status_code=404, detail="知识点不存在")
    
    # 生成题目
    quiz_service = get_quiz_service()
    try:
        quiz_data = await quiz_service.generate_quiz(
            concept_name=concept.name
        )
    except Exception as e:
        print(f"AI生成题目失败: {e}")
        # 返回默认题目
        quiz_data = {
            "question": f"关于{concept.name}，以下说法正确的是？",
            "options": {"A": "正确", "B": "错误", "C": "不确定", "D": "以上都不对"},
            "correct_answer": "A",
            "explanation": f"本题考查{concept.name}的相关知识"
        }
    
    # 保存到数据库
    normalized_options = normalize_option_map(quiz_data.get("options"))
    test_record = TestRecord(
        concept_id=concept_id,
        test_type="ai_quiz",
        ai_question=quiz_data["question"],
        ai_options=normalized_options,
        ai_correct_answer=quiz_data.get("correct_answer", "A"),
        ai_explanation=quiz_data.get("explanation", "")
    )
    db.add(test_record)
    db.commit()
    db.refresh(test_record)
    
    return GeneratedQuiz(
        id=test_record.id,
        concept_id=concept_id,
        concept_name=concept.name,
        question=quiz_data["question"],
        options=QuizOption(**_to_quiz_option_payload(normalized_options))
    )


@router.post("/submit", response_model=QuizResult)
async def submit_answer(
    data: QuizSubmission,
    db: Session = Depends(get_db)
):
    """
    提交答案，AI批改 (单题模式 - 兼容旧版)
    """
    # 获取测试记录
    test = db.query(TestRecord).filter(TestRecord.id == data.test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    
    # AI批改
    normalized_confidence = coerce_confidence(data.confidence, default="unsure")
    normalized_options = normalize_option_map(test.ai_options)
    quiz_service = get_quiz_service()
    try:
        grading_result = await quiz_service.grade_answer(
            question=test.ai_question,
            options=normalized_options,
            correct_answer=test.ai_correct_answer,
            user_answer=data.user_answer,
            confidence=normalized_confidence
        )
    except Exception as e:
        print(f"AI批改失败: {e}")
        # 返回默认批改结果
        is_correct = answers_match(data.user_answer, test.ai_correct_answer)
        grading_result = {
            "is_correct": is_correct,
            "score": 100 if is_correct else 0,
            "feedback": "回答正确！" if is_correct else "回答错误，建议复习相关知识点。",
            "weak_points": [],
            "suggestion": "继续学习"
        }
    
    # 更新测试记录
    normalized_weak_points = canonicalize_string_list(grading_result.get("weak_points"))
    test.user_answer = data.user_answer
    test.confidence = normalized_confidence
    test.ai_options = normalized_options
    test.is_correct = grading_result["is_correct"]
    test.ai_feedback = grading_result.get("feedback", "")
    test.weak_points = normalized_weak_points
    test.score = grading_result.get("score", 0)
    
    # 更新知识点掌握度
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == test.concept_id
    ).first()
    
    if concept:
        # 根据正确率调整掌握度
        if grading_result["is_correct"]:
            concept.retention = min(concept.retention + 0.1, 1.0)
        else:
            concept.retention = max(concept.retention - 0.05, 0.0)
        
        concept.last_tested = date.today()
        # 计算下次复习时间
        if grading_result["is_correct"]:
            concept.next_review = date.today() + timedelta(days=3)
        else:
            concept.next_review = date.today() + timedelta(days=1)
    
    # 如果答错，记录到错题本
    if not grading_result["is_correct"]:
        existing = db.query(WrongAnswer).filter(
            WrongAnswer.concept_id == test.concept_id,
            WrongAnswer.question == test.ai_question
        ).first()
        
        if not existing:
            wrong = WrongAnswer(
                concept_id=test.concept_id,
                question=test.ai_question,
                options=normalized_options,
                correct_answer=test.ai_correct_answer,
                user_answer=data.user_answer,
                explanation=test.ai_explanation,
                error_type="unknown",
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
            existing.user_answer = data.user_answer
    
    db.commit()
    
    return QuizResult(
        test_id=test.id,
        concept_id=test.concept_id,
        concept_name=concept.name if concept else "",
        question=test.ai_question,
        options=QuizOption(**_to_quiz_option_payload(normalized_options)),
        correct_answer=test.ai_correct_answer,
        ai_explanation=test.ai_explanation or "",
        user_answer=data.user_answer,
        is_correct=grading_result["is_correct"],
        confidence=normalized_confidence,
        ai_feedback=grading_result.get("feedback", ""),
        weak_points=normalized_weak_points,
        score=grading_result.get("score", 0),
        suggestion=grading_result.get("suggestion", ""),
        next_review=concept.next_review if concept else None
    )


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
    quiz_service = get_quiz_service()
    
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
                "options": normalize_option_map(wa.options),
                "correct_answer": wa.correct_answer,
                "explanation": wa.explanation or "暂无解析",
                "is_wrong_answer": True,
                "wrong_answer_id": wa.id
            })
    
    elif mode == "repeat":
        # 从该章节的所有知识点中选取10道已有题目
        concepts = db.query(ConceptMastery).filter(
            ConceptMastery.chapter_id == chapter_id
        ).limit(10).all()
        
        for concept in concepts:
            # 查找该知识点的历史题目
            test_record = db.query(TestRecord).filter(
                TestRecord.concept_id == concept.concept_id
            ).order_by(TestRecord.tested_at.desc()).first()
            
            if test_record and test_record.ai_question:
                questions.append({
                    "question_id": f"repeat_{concept.concept_id}",
                    "concept_id": concept.concept_id,
                    "question": test_record.ai_question,
                    "options": normalize_option_map(test_record.ai_options),
                    "correct_answer": test_record.ai_correct_answer or "A",
                    "explanation": test_record.ai_explanation or "暂无解析",
                    "is_wrong_answer": False
                })
    
    else:  # practice 正常练习 - 调用AI生成新题 (并行优化版)
        # 获取该章节的知识点
        concepts = db.query(ConceptMastery).filter(
            ConceptMastery.chapter_id == chapter_id
        ).limit(10).all()
        
        # 并行生成题目
        async def generate_single_question(concept):
            """为单个知识点生成题目"""
            try:
                quiz_data = await quiz_service.generate_quiz(concept_name=concept.name)
                return {
                    "success": True,
                    "concept": concept,
                    "quiz_data": quiz_data
                }
            except Exception as e:
                print(f"生成题目失败 for {concept.name}: {e}")
                return {
                    "success": False,
                    "concept": concept,
                    "error": str(e)
                }
        
        # 并行生成所有题目 (关键优化: 从串行改为并行)
        tasks = [generate_single_question(c) for c in concepts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        for result in results:
            if isinstance(result, Exception):
                continue
            if not result.get("success"):
                # 使用默认题目
                concept = result["concept"]
                questions.append({
                    "question_id": f"practice_{concept.concept_id}",
                    "concept_id": concept.concept_id,
                    "question": f"关于{concept.name}，以下说法正确的是？",
                    "options": {"A": "正确", "B": "错误", "C": "不确定", "D": "以上都不对"},
                    "correct_answer": "A",
                    "explanation": f"本题考查{concept.name}的相关知识",
                    "is_wrong_answer": False
                })
            else:
                concept = result["concept"]
                quiz_data = result["quiz_data"]
                
                # 保存到TestRecord
                test_record = TestRecord(
                    concept_id=concept.concept_id,
                    test_type="ai_quiz",
                    ai_question=quiz_data["question"],
                    ai_options=normalize_option_map(quiz_data.get("options")),
                    ai_correct_answer=quiz_data.get("correct_answer", "A"),
                    ai_explanation=quiz_data.get("explanation", "")
                )
                db.add(test_record)
                
                questions.append({
                    "question_id": f"practice_{concept.concept_id}",
                    "concept_id": concept.concept_id,
                    "question": quiz_data["question"],
                    "options": normalize_option_map(quiz_data.get("options")),
                    "correct_answer": quiz_data.get("correct_answer", "A"),
                    "explanation": quiz_data.get("explanation", "暂无解析"),
                    "is_wrong_answer": False
                })
        
        # 提交所有生成的题目到数据库
        db.commit()
    
    # 如果不足10题，补充默认题目
    while len(questions) < 10:
        questions.append({
            "question_id": f"empty_{len(questions)}",
            "concept_id": "",
            "question": "题目生成中...",
            "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
            "correct_answer": "A",
            "explanation": "",
            "is_wrong_answer": False
        })
    
    # 只取前10题
    questions = questions[:10]
    
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
    db.refresh(session)
    
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
    
    quiz_service = get_quiz_service()
    
    # 记录答案
    answers = []
    correct_count = 0
    
    for idx, answer in enumerate(data.answers):
        question = session.questions[idx] if idx < len(session.questions) else None
        if not question:
            continue
        
        if not question.get("concept_id"):
            continue
        
        normalized_options = normalize_option_map(question.get("options"))
        
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
                    options=normalized_options,
                    correct_answer=question.get("correct_answer", "A"),
                    user_answer=answer.user_answer,
                    explanation=question.get("explanation", ""),
                    error_type="unknown",
                    weak_points=[],
                    review_count=1,
                    last_reviewed=datetime.now(),
                    next_review=date.today() + timedelta(days=1),
                    mastery_level=0,
                    is_mastered=False
                )
                db.add(wrong_answer)
    
    # 更新会话
    normalized_answers = canonicalize_quiz_answers(answers)
    session.answers = normalized_answers
    session.correct_count = correct_count
    session.score = int(correct_count / 10 * 100)
    session.completed_at = datetime.now()
    db.commit()
    
    return {
        "session_id": session_id,
        "score": session.score,
        "correct_count": correct_count,
        "wrong_count": 10 - correct_count,
        "answers": normalized_answers
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
    is_correct: bool,
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
        intervals = [1, 3, 7, 14, 30]
        interval = intervals[min(wrong.mastery_level, len(intervals)-1)]
        wrong.next_review = date.today() + timedelta(days=interval)
    else:
        # 又答错了，降低掌握等级
        wrong.mastery_level = max(wrong.mastery_level - 1, 0)
        wrong.is_mastered = False
        wrong.next_review = date.today() + timedelta(days=1)
    
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
