"""
批量测验路由 - 整卷生成模式
支持：选择题目数量(5/10/15/20)，一次性生成整套试卷
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict
import json
import uuid
import hashlib

from models import get_db, QuizSession, WrongAnswer, ConceptMastery, Chapter
from learning_tracking_models import WrongAnswerV2, make_fingerprint
from services.quiz_service_v2 import get_quiz_service

router = APIRouter(prefix="/api/quiz/batch", tags=["batch_quiz"])

# 试卷缓存
_exam_cache = {}

# 单独存储用于细节练习的数据（不删除）
_detail_cache = {}

INVALID_CHAPTER_IDS = {"", "0", "unknown_ch0", "未知_ch0", "无法识别_ch0", "未分类_ch0", "uncategorized_ch0"}


def _normalize_confirmed_chapter_id(chapter_id: str) -> str:
    normalized = str(chapter_id or "").strip()
    return normalized if normalized and normalized not in INVALID_CHAPTER_IDS else ""

class GenerateRequest(BaseModel):
    uploaded_content: str
    num_questions: int = 10

class SubmitRequest(BaseModel):
    answers: List[str]
    confidence: Optional[Dict[str, str]] = {}

class GenerateVariationRequest(BaseModel):
    key_point: str
    base_question: dict
    uploaded_content: str = ""
    num_variations: int = 5

class ConfirmChapterRequest(BaseModel):
    chapter_id: str


@router.post("/confirm-chapter/{exam_id}")
async def confirm_chapter(exam_id: str, request: ConfirmChapterRequest):
    """用户确认/修正AI预测的章节归属，更新缓存"""
    exam = _exam_cache.get(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="试卷不存在或已过期")
    confirmed_chapter_id = _normalize_confirmed_chapter_id(request.chapter_id)
    exam["chapter_id"] = confirmed_chapter_id
    print(f"[Exam] 章节确认: exam={exam_id}, chapter={confirmed_chapter_id or '未确认'}")
    return {"success": True, "chapter_id": confirmed_chapter_id}

@router.post("/generate/{chapter_id}")
async def generate_exam(
    chapter_id: str,
    request: GenerateRequest,
    db: Session = Depends(get_db)
):
    """生成整套试卷 - 整卷生成，避免知识点重复"""
    uploaded_content = request.uploaded_content
    num_questions = request.num_questions

    if num_questions not in [5, 10, 15, 20]:
        num_questions = 10

    print(f"[Exam] 为章节 {chapter_id} 生成 {num_questions} 道题")

    if not uploaded_content or len(uploaded_content) < 100:
        raise HTTPException(status_code=400, detail="请提供至少100字的讲课内容")

    quiz_service = get_quiz_service()

    try:
        result = await quiz_service.generate_exam_paper(
            uploaded_content=uploaded_content,
            num_questions=num_questions
        )

        exam_id = str(uuid.uuid4())
        _exam_cache[exam_id] = {
            "chapter_id": _normalize_confirmed_chapter_id(chapter_id),
            "chapter_prediction": result.get("chapter_prediction"),
            "questions": result["questions"],
            "created_at": datetime.now(),
            "num_questions": num_questions,
            "uploaded_content": uploaded_content  # 保存原始内容用于变式题生成
        }

        questions_for_student = []
        knowledge_points = []
        for q in result["questions"]:
            questions_for_student.append({
                "id": q["id"],
                "type": q["type"],
                "difficulty": q["difficulty"],
                "question": q["question"],
                "options": q["options"],
                "key_point": q.get("key_point", ""),
                "correct_answer": q.get("correct_answer", ""),
                "explanation": q.get("explanation", "")
            })
            kp = q.get("key_point", "").strip()
            if kp and kp not in knowledge_points:
                knowledge_points.append(kp)

        # 调试：打印 chapter_prediction
        chapter_pred = result.get("chapter_prediction")
        print(f"[Exam] AI 返回的 chapter_prediction: {chapter_pred}")

        return {
            "exam_id": exam_id,
            "paper_title": result["paper_title"],
            "total_questions": result["total_questions"],
            "difficulty_distribution": result["difficulty_distribution"],
            "chapter_prediction": chapter_pred,
            "questions": questions_for_student,
            "knowledge_points": knowledge_points,
            "summary": result["summary"]
        }
        
    except Exception as e:
        print(f"[Exam] 出卷失败: {e}")
        import traceback
        traceback.print_exc()
        msg = str(e)
        if msg.startswith("QUIZ_TIMEOUT|"):
            user_msg = msg.split("|", 1)[1] if "|" in msg else "生成超时，请稍后重试"
            raise HTTPException(
                status_code=504,
                detail=(
                    f"{user_msg} "
                    "建议：先尝试10题或15题，或稍后1-2分钟重试。"
                )
            )
        raise HTTPException(status_code=500, detail=f"生成试卷失败: {str(e)}")

@router.post("/submit/{exam_id}")
async def submit_exam(
    exam_id: str,
    request: SubmitRequest,
    db: Session = Depends(get_db)
):
    """提交试卷 - 直接对比答案，无AI讲解"""
    exam = _exam_cache.get(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="试卷已过期或不存在")

    answers = request.answers
    confidence = request.confidence
    questions = exam.get("questions", [])
    num_questions = exam.get("num_questions", 10)
    chapter_id = exam.get("chapter_id", "")
    chapter_prediction = exam.get("chapter_prediction") or {}

    if len(answers) != num_questions:
        raise HTTPException(status_code=400, detail=f"答案数量不正确，需要{num_questions}个答案")
    
    quiz_service = get_quiz_service()
    result = quiz_service.grade_paper(questions, answers, confidence)

    # Resolve a valid chapter id for QuizSession foreign key.
    session_chapter_id = None
    # 方式1: 直接使用 chapter_id（来自前端确认）
    if chapter_id and chapter_id not in INVALID_CHAPTER_IDS:
        if db.query(Chapter).filter(Chapter.id == chapter_id).first():
            session_chapter_id = chapter_id

    # 方式2: 使用 AI 的 chapter_prediction
    if not session_chapter_id and isinstance(chapter_prediction, dict):
        predicted_id = str(chapter_prediction.get("chapter_id") or "").strip()
        if predicted_id and predicted_id not in INVALID_CHAPTER_IDS:
            if db.query(Chapter).filter(Chapter.id == predicted_id).first():
                session_chapter_id = predicted_id

    # 方式3: 从题目内容推断章节（最后的安全网）
    if not session_chapter_id and questions:
        try:
            # 收集题目的考点信息用于匹配
            key_points = [q.get("key_point", "") for q in questions[:5] if q.get("key_point")]
            content_hint = " ".join(key_points[:3])
            if content_hint:
                quiz_service_for_chapter = get_quiz_service()
                inferred = quiz_service_for_chapter._infer_chapter_prediction(content_hint)
                if inferred and inferred.get("chapter_id"):
                    inferred_id = inferred["chapter_id"]
                    if inferred_id not in INVALID_CHAPTER_IDS:
                        if db.query(Chapter).filter(Chapter.id == inferred_id).first():
                            session_chapter_id = inferred_id
                            print(f"[Exam] 从题目考点推断章节: {inferred_id}")
        except Exception as e:
            print(f"[Exam] 章节推断失败: {e}")

    # 方式4: 从原始讲课内容推断章节（题目考点失效时的最终兜底）
    if not session_chapter_id:
        uploaded_content = (exam.get("uploaded_content") or "").strip()
        if uploaded_content:
            try:
                quiz_service_for_chapter = get_quiz_service()
                inferred = quiz_service_for_chapter._infer_chapter_prediction(uploaded_content[:8000])
                if inferred and inferred.get("chapter_id"):
                    inferred_id = inferred["chapter_id"]
                    if inferred_id not in INVALID_CHAPTER_IDS:
                        if db.query(Chapter).filter(Chapter.id == inferred_id).first():
                            session_chapter_id = inferred_id
                            print(f"[Exam] 从原始内容推断章节: {inferred_id}")
            except Exception as e:
                print(f"[Exam] 原始内容章节推断失败: {e}")

    print(f"[Exam] 最终章节ID: {session_chapter_id} (原始: {chapter_id})")

    quiz_session = QuizSession(
        session_type=f"exam_{num_questions}",
        chapter_id=session_chapter_id,
        questions=questions,
        answers=[{"question_index": i, "user_answer": answers[i], 
                 "is_correct": result["details"][i]["is_correct"]} 
                for i in range(num_questions)],
        total_questions=num_questions,
        correct_count=result["correct_count"],
        score=result["score"],
        completed_at=datetime.now()
    )
    db.add(quiz_session)

    def ensure_concept_for_wrong(q: dict, q_index: int) -> str:
        target_chapter_id = session_chapter_id or "uncategorized_ch0"

        chapter = db.query(Chapter).filter(Chapter.id == target_chapter_id).first()
        if not chapter:
            chapter = Chapter(
                id=target_chapter_id,
                book="未分类",
                edition="贺银成2027",
                chapter_number="0",
                chapter_title="待人工归类",
                concepts=[],
                first_uploaded=date.today(),
            )
            db.add(chapter)
            db.flush()

        key_point = (q.get("key_point") or "").strip() or f"试卷考点{q_index + 1}"
        digest = hashlib.md5(f"{target_chapter_id}|{key_point}".encode("utf-8")).hexdigest()[:12]
        concept_id = f"{target_chapter_id}_exam_{digest}"

        concept = db.query(ConceptMastery).filter(ConceptMastery.concept_id == concept_id).first()
        if not concept:
            concept = ConceptMastery(
                concept_id=concept_id,
                chapter_id=target_chapter_id,
                name=key_point,
                retention=0.0,
                understanding=0.0,
                application=0.0,
            )
            db.add(concept)
            db.flush()

        return concept_id

    # 错题录入：使用 WrongAnswerV2 系统（带指纹去重）
    for i, detail in enumerate(result["details"]):
        if not detail["is_correct"]:
            question = questions[i]

            # 生成题目指纹（用于去重）
            question_text = question.get("question", "")
            fingerprint = make_fingerprint(question_text)

            # 检查是否已存在（按指纹去重）
            existing = db.query(WrongAnswerV2).filter(
                WrongAnswerV2.question_fingerprint == fingerprint
            ).first()

            if existing:
                # 已存在：更新统计
                existing.error_count += 1
                existing.encounter_count += 1
                existing.last_wrong_at = datetime.now()
                existing.updated_at = datetime.now()

                # 更新严重度标签
                if detail.get("confidence") == "sure" and existing.severity_tag != "critical":
                    existing.severity_tag = "critical"  # 自信但答错 → 致命盲区
                elif existing.error_count >= 2 and existing.severity_tag not in ("critical", "stubborn"):
                    existing.severity_tag = "stubborn"  # 错误次数 >= 2 → 顽固病灶

                print(f"[WrongAnswer] 更新已有错题: {fingerprint[:8]}... (错误次数: {existing.error_count})")
            else:
                # 不存在：创建新错题
                concept_id = ensure_concept_for_wrong(question, i)
                wrong_chapter_id = session_chapter_id or "uncategorized_ch0"

                # 判断初始严重度
                if detail.get("confidence") == "sure":
                    severity = "critical"  # 自信但答错 → 致命盲区
                elif detail.get("confidence") in ("unsure", "no"):
                    severity = "landmine"  # 不确定但答错 → 隐形地雷
                else:
                    severity = "normal"

                wrong = WrongAnswerV2(
                    question_fingerprint=fingerprint,
                    question_text=question_text,
                    options=question.get("options", {}),
                    correct_answer=question.get("correct_answer", ""),
                    explanation=question.get("explanation", ""),
                    key_point=question.get("key_point", ""),
                    question_type=question.get("type", "A1"),
                    difficulty=question.get("difficulty", "基础"),
                    chapter_id=wrong_chapter_id,
                    error_count=1,
                    encounter_count=1,
                    severity_tag=severity,
                    mastery_status="active",
                    first_wrong_at=datetime.now(),
                    last_wrong_at=datetime.now(),
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                db.add(wrong)
                print(f"[WrongAnswer] 新增错题: {fingerprint[:8]}... (严重度: {severity})")

    db.commit()

    _detail_cache[exam_id] = exam

    if exam_id in _exam_cache:
        del _exam_cache[exam_id]
    
    print(f"[Exam] 批改完成: {result['score']}分")
    return result

@router.get("/session/{exam_id}")
async def get_exam(exam_id: str):
    """获取试卷（用于页面刷新恢复）"""
    exam = _exam_cache.get(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="试卷已过期")

    questions = []
    for q in exam["questions"]:
        questions.append({
            "id": q["id"],
            "type": q["type"],
            "difficulty": q["difficulty"],
            "question": q["question"],
            "options": q["options"]
        })

    return {
        "exam_id": exam_id,
        "questions": questions,
        "num_questions": exam["num_questions"]
    }

@router.get("/detail/{exam_id}")
async def get_exam_for_detail(exam_id: str):
    """获取试卷用于细节练习（保留完整数据包括答案）"""
    exam = _exam_cache.get(exam_id)
    if not exam:
        exam = _detail_cache.get(exam_id)
        if not exam:
            raise HTTPException(status_code=404, detail="试卷数据已过期，请重新生成试卷")

    questions = []
    for q in exam["questions"]:
        questions.append({
            "id": q["id"],
            "type": q["type"],
            "difficulty": q["difficulty"],
            "question": q["question"],
            "options": q["options"],
            "key_point": q.get("key_point", ""),
            "correct_answer": q.get("correct_answer", ""),
            "explanation": q.get("explanation", "")
        })

    knowledge_points = []
    for q in exam["questions"]:
        kp = q.get("key_point", "").strip()
        if kp and kp not in knowledge_points:
            knowledge_points.append(kp)

    return {
        "exam_id": exam_id,
        "chapter_id": exam.get("chapter_id", ""),
        "questions": questions,
        "knowledge_points": knowledge_points,
        "num_questions": exam["num_questions"],
        "uploadedContent": exam.get("uploaded_content", "")  # 传递原始内容给前端
    }

@router.post("/generate-variations")
async def generate_variation_questions(
    request: GenerateVariationRequest,
    db: Session = Depends(get_db)
):
    """基于知识点生成变式题"""
    print(f"[Variation] 生成变式题: {request.key_point}")
    
    quiz_service = get_quiz_service()
    
    try:
        variations = await quiz_service.generate_variation_questions(
            key_point=request.key_point,
            base_question=request.base_question,
            uploaded_content=request.uploaded_content,
            num_variations=request.num_variations
        )
        
        print(f"[Variation] 生成成功: {len(variations)} 道变式题")
        return {"variations": variations}
        
    except Exception as e:
        print(f"[Variation] 生成失败: {e}")
        import traceback
        traceback.print_exc()
        # 不再静默返回原题冒充变式，返回明确的错误标记
        return {
            "variations": [],
            "error": str(e),
            "is_fallback": True
        }
