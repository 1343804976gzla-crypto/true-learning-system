"""
测试路由
出题、答题、批改
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, timedelta

from models import get_db, ConceptMastery, TestRecord
from schemas import (
    GeneratedQuiz, QuizSubmission, QuizResult,
    QuizOption
)
from services import get_quiz_service
from utils.helpers import calculate_next_review

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


@router.post("/generate/{concept_id}", response_model=GeneratedQuiz)
async def generate_quiz(
    concept_id: str,
    db: Session = Depends(get_db)
):
    """
    为指定知识点生成AI题目
    """
    # 获取知识点
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == concept_id
    ).first()
    
    if not concept:
        raise HTTPException(status_code=404, detail="知识点不存在")
    
    # 生成题目
    quiz_service = get_quiz_service()
    quiz_data = await quiz_service.generate_quiz(
        concept_name=concept.name
    )
    
    # 保存到数据库
    test_record = TestRecord(
        concept_id=concept_id,
        test_type="ai_quiz",
        ai_question=quiz_data["question"],
        ai_options=quiz_data["options"],
        ai_correct_answer=quiz_data["correct_answer"],
        ai_explanation=quiz_data["explanation"]
    )
    db.add(test_record)
    db.commit()
    db.refresh(test_record)
    
    return GeneratedQuiz(
        id=test_record.id,
        concept_id=concept_id,
        concept_name=concept.name,
        question=quiz_data["question"],
        options=QuizOption(**quiz_data["options"])
    )


@router.post("/submit", response_model=QuizResult)
async def submit_answer(
    data: QuizSubmission,
    db: Session = Depends(get_db)
):
    """
    提交答案，AI批改
    """
    # 获取测试记录
    test = db.query(TestRecord).filter(TestRecord.id == data.test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    
    # AI批改
    quiz_service = get_quiz_service()
    grading_result = await quiz_service.grade_answer(
        question=test.ai_question,
        options=test.ai_options,
        correct_answer=test.ai_correct_answer,
        user_answer=data.user_answer,
        confidence=data.confidence
    )
    
    # 更新测试记录
    test.user_answer = data.user_answer
    test.confidence = data.confidence
    test.is_correct = grading_result["is_correct"]
    test.ai_feedback = grading_result["feedback"]
    test.weak_points = grading_result.get("weak_points", [])
    test.score = grading_result["score"]
    
    # 更新知识点掌握度
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == test.concept_id
    ).first()
    
    if concept:
        # 简单更新记忆保留度
        old_retention = concept.retention
        new_retention = (old_retention * 0.7) + (grading_result["score"] / 100 * 0.3)
        concept.retention = min(new_retention, 1.0)
        concept.last_tested = date.today()
        
        # 计算下次复习时间
        if grading_result["is_correct"]:
            current_interval = 1
            if concept.next_review and concept.last_tested:
                current_interval = (concept.next_review - concept.last_tested).days
                if current_interval < 1:
                    current_interval = 1
            
            next_interval = calculate_next_review(grading_result["score"], current_interval)
            concept.next_review = date.today() + timedelta(days=next_interval)
    
    db.commit()
    
    return QuizResult(
        test_id=test.id,
        concept_id=test.concept_id,
        concept_name=concept.name if concept else "未知",
        question=test.ai_question,
        options=QuizOption(**test.ai_options),
        correct_answer=test.ai_correct_answer,
        ai_explanation=test.ai_explanation,
        user_answer=data.user_answer,
        is_correct=grading_result["is_correct"],
        confidence=data.confidence,
        ai_feedback=grading_result["feedback"],
        weak_points=grading_result.get("weak_points", []),
        score=grading_result["score"],
        suggestion=grading_result.get("suggestion", ""),
        next_review=concept.next_review if concept else None
    )
