"""
批量测验路由 - 整卷生成模式
支持：选择题目数量(5/10/15/20)，一次性生成整套试卷
"""

from datetime import date, datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
import json
import uuid
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api_contracts import (
    BatchExamConfirmChapterResponse,
    BatchExamDetailResponse,
    BatchExamGenerateResponse,
    BatchExamSessionResponse,
    BatchExamSubmitResponse,
    BatchVariationResponse,
)
from models import get_db, QuizSession, WrongAnswer, ConceptMastery, Chapter
from learning_tracking_models import WrongAnswerV2, make_fingerprint, INVALID_CHAPTER_IDS
from services.quiz_service_v2 import get_quiz_service
from utils.data_contracts import (
    canonicalize_quiz_answers,
    canonicalize_quiz_questions,
    coerce_confidence,
    normalize_option_map,
)

router = APIRouter(prefix="/api/quiz/batch", tags=["batch_quiz"])

# 试卷缓存
_exam_cache = {}

# 单独存储用于细节练习的数据（不删除）
_detail_cache = {}

DETAIL_PRIORITY_WEIGHTS = {
    "error_count": 3,
    "severity": {
        "stubborn": 10,
        "critical": 8,
        "landmine": 5,
        "normal": 3,
        "": 0,
    },
}
DETAIL_SEVERITY_RANK = {"": 0, "normal": 1, "landmine": 2, "critical": 3, "stubborn": 4}


def _normalize_confirmed_chapter_id(chapter_id: str) -> str:
    normalized = str(chapter_id or "").strip()
    return normalized if normalized and normalized not in INVALID_CHAPTER_IDS else ""


def _get_question_key_point(question: Dict[str, Any], index: int) -> str:
    key_point = str(question.get("key_point") or "").strip()
    return key_point or f"考点{index + 1}"


def _severity_from_confidence(confidence: str) -> str:
    normalized = coerce_confidence(confidence, default="unsure")
    if normalized == "sure":
        return "critical"
    if normalized in {"unsure", "no"}:
        return "landmine"
    return "normal"


def _normalize_fuzzy_option_list(raw_options: Any, question_options: Optional[Dict[str, Any]] = None) -> List[str]:
    allowed = {
        str(key or "").strip().upper()
        for key in (question_options or {}).keys()
        if str(key or "").strip().upper() in {"A", "B", "C", "D", "E"}
    }
    if not allowed:
        allowed = {"A", "B", "C", "D", "E"}

    normalized: List[str] = []
    for item in raw_options or []:
        option = str(item or "").strip().upper()
        if option and option in allowed and option not in normalized:
            normalized.append(option)

    return sorted(normalized)


def _build_fuzzy_option_cache(
    questions: List[Dict[str, Any]],
    confidence: Optional[Dict[str, str]],
    fuzzy_options: Optional[Dict[str, List[str]]],
) -> Dict[str, Dict[str, Any]]:
    cached: Dict[str, Dict[str, Any]] = {}
    if not isinstance(fuzzy_options, dict):
        return cached

    confidence_map = confidence or {}
    for raw_index, raw_options in fuzzy_options.items():
        try:
            question_index = int(raw_index)
        except (TypeError, ValueError):
            continue

        if question_index < 0 or question_index >= len(questions):
            continue

        conf = coerce_confidence(
            confidence_map.get(str(question_index), confidence_map.get(question_index, "")),
            default="unsure",
        )
        if conf != "unsure":
            continue

        question = questions[question_index] or {}
        normalized_options = _normalize_fuzzy_option_list(raw_options, question.get("options"))
        if not normalized_options:
            continue

        option_texts: Dict[str, str] = {}
        question_options = question.get("options") or {}
        for option in normalized_options:
            option_text = str(question_options.get(option) or f"选项{option}").strip()
            option_texts[option] = option_text

        cached[str(question_index)] = {
            "options": normalized_options,
            "option_texts": option_texts,
            "key_point": _get_question_key_point(question, question_index),
        }

    return cached


def _aggregate_exam_wrong_questions(wrong_questions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in wrong_questions:
        key_point = str(item.get("key_point") or "").strip()
        if not key_point:
            continue

        entry = grouped.setdefault(
            key_point,
            {
                "key_point": key_point,
                "error_count": 0,
                "severity_tag": "",
                "severity_rank": 0,
            },
        )
        entry["error_count"] += int(item.get("error_count") or 1)

        severity_tag = str(item.get("severity_tag") or "").strip()
        severity_rank = DETAIL_SEVERITY_RANK.get(severity_tag, 0)
        if severity_rank > entry["severity_rank"]:
            entry["severity_rank"] = severity_rank
            entry["severity_tag"] = severity_tag

    for entry in grouped.values():
        if entry["error_count"] >= 2:
            entry["severity_tag"] = "stubborn"
            entry["severity_rank"] = DETAIL_SEVERITY_RANK["stubborn"]
        entry["severity_weight"] = DETAIL_PRIORITY_WEIGHTS["severity"].get(entry["severity_tag"], 0)

    return grouped


def _build_detail_knowledge_order(exam: Dict[str, Any], db: Session) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    questions = exam.get("questions", [])
    knowledge_points: List[str] = []
    original_order: Dict[str, int] = {}
    for i, question in enumerate(questions):
        key_point = _get_question_key_point(question, i)
        if key_point not in original_order:
            original_order[key_point] = len(knowledge_points)
            knowledge_points.append(key_point)

    wrong_summary = _aggregate_exam_wrong_questions(exam.get("exam_wrong_questions") or [])
    chapter_id = _normalize_confirmed_chapter_id(exam.get("chapter_id", ""))
    concept_rows = []
    if knowledge_points:
        query = db.query(ConceptMastery.name, ConceptMastery.understanding)
        if chapter_id:
            query = query.filter(ConceptMastery.chapter_id == chapter_id)
        concept_rows = query.filter(ConceptMastery.name.in_(knowledge_points)).all()

    understanding_map: Dict[str, float] = {}
    for name, understanding in concept_rows:
        kp_name = str(name or "").strip()
        if not kp_name:
            continue
        current_value = understanding_map.get(kp_name, 0.0)
        understanding_map[kp_name] = max(current_value, float(understanding or 0.0))

    stats_map: Dict[str, Dict[str, Any]] = {}
    ranked_rows = []
    for key_point in knowledge_points:
        wrong_data = wrong_summary.get(key_point, {})
        understanding = min(1.0, max(0.0, float(understanding_map.get(key_point, 0.0))))
        mastery_penalty = round((1.0 - understanding) * 10, 2)
        error_count = int(wrong_data.get("error_count") or 0)
        severity_tag = str(wrong_data.get("severity_tag") or "")
        severity_weight = int(wrong_data.get("severity_weight") or 0)
        priority_score = round(
            error_count * DETAIL_PRIORITY_WEIGHTS["error_count"] + severity_weight + mastery_penalty,
            2,
        )
        stats = {
            "key_point": key_point,
            "error_count": error_count,
            "severity_tag": severity_tag,
            "severity_weight": severity_weight,
            "understanding": understanding,
            "mastery_penalty": mastery_penalty,
            "priority_score": priority_score,
            "original_order": original_order[key_point],
        }
        stats_map[key_point] = stats
        ranked_rows.append(stats)

    ranked_rows.sort(key=lambda item: (-item["priority_score"], item["original_order"]))
    ordered_points = [item["key_point"] for item in ranked_rows]
    return ordered_points, stats_map

class GenerateRequest(BaseModel):
    uploaded_content: str
    num_questions: int = 10

class SubmitRequest(BaseModel):
    answers: List[str]
    confidence: Optional[Dict[str, str]] = {}
    fuzzy_options: Optional[Dict[str, List[str]]] = {}

class GenerateVariationRequest(BaseModel):
    key_point: str
    base_question: dict
    uploaded_content: str = ""
    num_variations: int = 5

class ConfirmChapterRequest(BaseModel):
    chapter_id: str


@router.post("/confirm-chapter/{exam_id}", response_model=BatchExamConfirmChapterResponse)
async def confirm_chapter(exam_id: str, request: ConfirmChapterRequest):
    """用户确认/修正AI预测的章节归属，更新缓存"""
    exam = _exam_cache.get(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="试卷不存在或已过期")
    confirmed_chapter_id = _normalize_confirmed_chapter_id(request.chapter_id)
    exam["chapter_id"] = confirmed_chapter_id
    print(f"[Exam] 章节确认: exam={exam_id}, chapter={confirmed_chapter_id or '未确认'}")
    return {"success": True, "chapter_id": confirmed_chapter_id}

@router.post("/generate/{chapter_id}", response_model=BatchExamGenerateResponse)
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
        normalized_generated_questions = canonicalize_quiz_questions(result["questions"])
        _exam_cache[exam_id] = {
            "chapter_id": _normalize_confirmed_chapter_id(chapter_id),
            "chapter_prediction": result.get("chapter_prediction"),
            "questions": normalized_generated_questions,
            "created_at": datetime.now(),
            "num_questions": num_questions,
            "uploaded_content": uploaded_content  # 保存原始内容用于变式题生成
        }

        questions_for_student = []
        knowledge_points = []
        for q in normalized_generated_questions:
            questions_for_student.append({
                "id": str(q["id"]),
                "type": q["type"],
                "difficulty": q["difficulty"],
                "question": q["question"],
                "options": normalize_option_map(q.get("options")),
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
            "difficulty_distribution": result.get("difficulty_distribution") or {},
            "chapter_prediction": chapter_pred if isinstance(chapter_pred, dict) else {},
            "questions": questions_for_student,
            "knowledge_points": knowledge_points,
            "summary": result.get("summary") or {}
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

@router.post("/submit/{exam_id}", response_model=BatchExamSubmitResponse)
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
    confidence = {
        str(key): coerce_confidence(value, default="unsure")
        for key, value in (request.confidence or {}).items()
    }
    fuzzy_options = request.fuzzy_options or {}
    questions = exam.get("questions", [])
    num_questions = exam.get("num_questions", 10)
    chapter_id = exam.get("chapter_id", "")
    chapter_prediction = exam.get("chapter_prediction") or {}

    if len(answers) != num_questions:
        raise HTTPException(status_code=400, detail=f"答案数量不正确，需要{num_questions}个答案")
    
    quiz_service = get_quiz_service()
    result = quiz_service.grade_paper(questions, answers, confidence)
    exam_fuzzy_options = _build_fuzzy_option_cache(questions, confidence, fuzzy_options)
    result["fuzzy_options"] = exam_fuzzy_options
    exam_wrong_questions = []

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

    normalized_questions = canonicalize_quiz_questions(questions)
    normalized_answers = canonicalize_quiz_answers([
        {
            "question_index": i,
            "user_answer": answers[i],
            "is_correct": result["details"][i]["is_correct"],
            "confidence": result["details"][i].get("confidence"),
        }
        for i in range(num_questions)
    ])

    quiz_session = QuizSession(
        session_type=f"exam_{num_questions}",
        chapter_id=session_chapter_id,
        questions=normalized_questions,
        answers=normalized_answers,
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
            key_point = _get_question_key_point(question, i)
            current_exam_severity = _severity_from_confidence(detail.get("confidence"))
            exam_wrong_questions.append(
                {
                    "key_point": key_point,
                    "severity_tag": current_exam_severity,
                    "error_count": 1,
                    "question_index": i,
                }
            )

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
                    options=normalize_option_map(question.get("options")),
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
    exam["chapter_id"] = session_chapter_id or exam.get("chapter_id", "")
    exam["exam_wrong_questions"] = exam_wrong_questions
    exam["fuzzy_options"] = exam_fuzzy_options
    exam["score"] = result.get("score")
    exam["wrong_count"] = result.get("wrong_count")

    _detail_cache[exam_id] = exam

    if exam_id in _exam_cache:
        del _exam_cache[exam_id]
    
    print(f"[Exam] 批改完成: {result['score']}分")
    return result

@router.get("/session/{exam_id}", response_model=BatchExamSessionResponse)
async def get_exam(exam_id: str):
    """获取试卷（用于页面刷新恢复）"""
    exam = _exam_cache.get(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="试卷已过期")

    questions = []
    for q in exam["questions"]:
        questions.append({
            "id": str(q["id"]),
            "type": q["type"],
            "difficulty": q["difficulty"],
            "question": q["question"],
            "options": normalize_option_map(q.get("options"))
        })

    return {
        "exam_id": exam_id,
        "questions": questions,
        "num_questions": exam["num_questions"]
    }

@router.get("/detail/{exam_id}", response_model=BatchExamDetailResponse)
async def get_exam_for_detail(exam_id: str, db: Session = Depends(get_db)):
    """获取试卷用于细节练习（保留完整数据包括答案）"""
    exam = _exam_cache.get(exam_id)
    if not exam:
        exam = _detail_cache.get(exam_id)
        if not exam:
            raise HTTPException(status_code=404, detail="试卷数据已过期，请重新生成试卷")

    questions = []
    for q in exam["questions"]:
        questions.append({
            "id": str(q["id"]),
            "type": q["type"],
            "difficulty": q["difficulty"],
            "question": q["question"],
            "options": normalize_option_map(q.get("options")),
            "key_point": q.get("key_point", ""),
            "correct_answer": q.get("correct_answer", ""),
            "explanation": q.get("explanation", "")
        })

    knowledge_points, knowledge_point_stats = _build_detail_knowledge_order(exam, db)

    return {
        "exam_id": exam_id,
        "chapter_id": exam.get("chapter_id") or "",
        "questions": questions,
        "knowledge_points": knowledge_points,
        "knowledge_point_stats": knowledge_point_stats,
        "fuzzy_options": exam.get("fuzzy_options", {}),
        "num_questions": exam["num_questions"],
        "uploadedContent": exam.get("uploaded_content") or ""  # 传递原始内容给前端
    }

@router.post("/generate-variations", response_model=BatchVariationResponse)
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
