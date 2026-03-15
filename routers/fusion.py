"""
融合升级 API 路由

提供错题融合升级功能的 REST API：
1. 解锁检查 - 验证题目是否满足融合条件
2. 苏格拉底引导 - 帮助用户发现概念联系
3. 融合题创建 - 生成高阶综合题
4. 答案评判 - 手动触发的 AI 评判
5. 错误诊断 - 答错后的苏格拉底式诊断
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import desc, and_, or_
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

from models import get_db
from learning_tracking_models import WrongAnswerV2, WrongAnswerRetry
from services.fusion_service import get_fusion_service, FusionService
from utils.data_contracts import canonicalize_fusion_data, canonicalize_parent_ids

router = APIRouter(prefix="/api/fusion", tags=["fusion"])


# ========== Pydantic 模型 ==========

class UnlockCheckResponse(BaseModel):
    """解锁检查响应"""
    can_unlock: bool
    reason: Optional[str] = None
    consecutive_correct: int = 0
    confidence_sure: bool = False


class SocraticHintResponse(BaseModel):
    """苏格拉底引导响应"""
    guide_questions: List[str]
    hint_text: Optional[str] = None
    source_question_id: int
    source_key_point: Optional[str] = None


class FusionCreateRequest(BaseModel):
    """创建融合题请求"""
    parent_ids: List[int] = Field(..., min_length=2, max_length=4, description="2-4道原题ID")


class FusionCreateResponse(BaseModel):
    """创建融合题响应"""
    fusion_id: int
    fusion_question: str
    fusion_level: int
    parent_ids: List[int]
    expected_key_points: List[str]


class FusionSubmitRequest(BaseModel):
    """提交融合题答案请求"""
    user_answer: str = Field(..., min_length=10, description="用户答案（至少10字）")


class FusionJudgeResponse(BaseModel):
    """融合题评判响应"""
    verdict: str  # correct/partial/incorrect
    score: int  # 0-100
    feedback: str
    weak_links: List[str]
    needs_diagnosis: bool


class DiagnosisRequest(BaseModel):
    """诊断请求"""
    user_answer: str
    reflection: str = Field(..., min_length=20, description="用户对错误的自我反思")


class DiagnosisResponse(BaseModel):
    """诊断响应"""
    diagnosis_type: str  # concept_forgot/relation_error/both
    affected_parent_ids: List[int]
    analysis: str
    recommendation: str


class FusionCandidate(BaseModel):
    """融合候选题目"""
    id: int
    question_text: str
    key_point: Optional[str]
    difficulty: Optional[str]
    archived_at: Optional[datetime]


# ========== API 端点 ==========

@router.post("/{id}/unlock-check", response_model=UnlockCheckResponse)
async def check_fusion_unlock(
    id: int,
    db: Session = Depends(get_db),
    service: FusionService = Depends(get_fusion_service)
):
    """
    检查某题是否满足融合解锁条件：
    - 状态为 archived
    - 连续3次正确 + 信心度100%（sure）
    """
    result = service.check_unlock_status(id, db)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return UnlockCheckResponse(**result)


@router.get("/{id}/socratic-hint", response_model=SocraticHintResponse)
async def get_socratic_hint(
    id: int,
    db: Session = Depends(get_db),
    service: FusionService = Depends(get_fusion_service)
):
    """
    获取苏格拉底式引导
    帮助用户思考概念间的联系，而不是直接推荐融合伙伴
    """
    # 先检查解锁状态
    unlock = service.check_unlock_status(id, db)
    if not unlock.get("can_unlock", False):
        raise HTTPException(
            status_code=403,
            detail=f"题目未满足融合条件: {unlock.get('reason', '未知原因')}"
        )

    result = await service.generate_socratic_hint(id, db)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return SocraticHintResponse(**result)


@router.get("/archived-candidates", response_model=List[FusionCandidate])
async def get_archived_candidates(
    exclude_id: Optional[int] = None,
    key_point: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    获取所有可融合的已归档错题
    用于用户自主寻找融合伙伴
    """
    query = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "archived",
        WrongAnswerV2.is_fusion == False  # 不包括已有的融合题
    )

    if exclude_id:
        query = query.filter(WrongAnswerV2.id != exclude_id)

    if key_point:
        query = query.filter(WrongAnswerV2.key_point.ilike(f"%{key_point}%"))

    candidates = query.order_by(desc(WrongAnswerV2.archived_at)).limit(limit).all()

    return [
        FusionCandidate(
            id=c.id,
            question_text=c.question_text[:200] + "..." if len(c.question_text) > 200 else c.question_text,
            key_point=c.key_point,
            difficulty=c.difficulty,
            archived_at=c.archived_at
        )
        for c in candidates
    ]


@router.post("/create", response_model=FusionCreateResponse)
async def create_fusion_question(
    request: FusionCreateRequest,
    db: Session = Depends(get_db),
    service: FusionService = Depends(get_fusion_service)
):
    """
    创建融合题
    - 验证 parent_ids 数量（2-4）
    - 验证所有原题都已归档且满足解锁条件
    - 调用 AI 生成融合题
    - 保存到数据库
    """
    # 验证数量
    if len(request.parent_ids) < 2 or len(request.parent_ids) > 4:
        raise HTTPException(status_code=400, detail="融合题必须由2-4道原题组成")

    # 验证所有原题
    parents = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.id.in_(request.parent_ids)
    ).all()

    if len(parents) != len(request.parent_ids):
        raise HTTPException(status_code=404, detail="部分原题不存在")

    # 验证解锁条件
    for parent in parents:
        unlock = service.check_unlock_status(parent.id, db)
        if not unlock.get("can_unlock", False):
            raise HTTPException(
                status_code=403,
                detail=f"原题 {parent.id} 未满足融合条件: {unlock.get('reason')}"
            )

    # 生成融合题
    result = await service.generate_fusion_question(request.parent_ids, db)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # 计算融合层级
    max_parent_level = max(
        [p.fusion_level or 0 for p in parents]
    )
    fusion_level = max_parent_level + 1

    # 创建融合题记录
    # 生成唯一指纹
    import hashlib
    fingerprint_base = f"fusion:{':'.join(map(str, sorted(request.parent_ids)))}:{result['fusion_question'][:50]}"
    question_fingerprint = hashlib.md5(fingerprint_base.encode()).hexdigest()

    # 检查是否已存在
    existing = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.question_fingerprint == question_fingerprint
    ).first()

    if existing:
        raise HTTPException(status_code=409, detail="该融合组合已存在")

    # 计算惩罚系数
    penalty_factors = {1: 1.5, 2: 2.0}
    penalty = penalty_factors.get(fusion_level, 2.5)

    # 创建新记录
    fusion = WrongAnswerV2(
        question_fingerprint=question_fingerprint,
        question_text=result["fusion_question"],
        options=None,  # 融合题是自由作答，无选项
        correct_answer="FUSION",  # 标记为融合题
        explanation=None,  # 评判后生成
        key_point="融合: " + " + ".join([p.key_point or "未标注" for p in parents]),
        question_type="FUSION",  # 特殊题型
        difficulty="难题" if fusion_level >= 2 else "提高",
        chapter_id=parents[0].chapter_id,  # 继承第一个原题的章节
        error_count=0,
        encounter_count=0,
        retry_count=0,
        severity_tag="critical",  # 融合题默认为critical，答错惩罚重
        mastery_status="active",  # 融合题需要重新学习
        parent_ids=canonicalize_parent_ids(request.parent_ids),
        is_fusion=True,
        fusion_level=fusion_level,
        sm2_penalty_factor=penalty,
        fusion_data=canonicalize_fusion_data({
            "expected_key_points": result.get("expected_key_points", []),
            "scoring_criteria": result.get("scoring_criteria", {}),
            "difficulty_level": result.get("difficulty_level", f"L{fusion_level}"),
            "parent_key_points": result.get("parent_key_points", []),
            "judgement_pending": True,
            "user_answer_cache": None
        }),
        sm2_ef=2.5,
        sm2_interval=0,
        sm2_repetitions=0,
        next_review_date=None,
        first_wrong_at=datetime.now(),
        last_wrong_at=datetime.now()
    )

    db.add(fusion)
    db.commit()
    db.refresh(fusion)

    return FusionCreateResponse(
        fusion_id=fusion.id,
        fusion_question=fusion.question_text,
        fusion_level=fusion.fusion_level,
        parent_ids=request.parent_ids,
        expected_key_points=result.get("expected_key_points", [])
    )


@router.post("/{id}/submit", response_model=dict)
async def submit_fusion_answer(
    id: int,
    request: FusionSubmitRequest,
    db: Session = Depends(get_db)
):
    """
    提交融合题答案（缓存，不立即评判）
    创造"元认知停顿"，让用户有时间自我反思
    """
    fusion = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.id == id,
        WrongAnswerV2.is_fusion == True
    ).first()

    if not fusion:
        raise HTTPException(status_code=404, detail="融合题不存在")

    # 缓存用户答案
    fusion_data = canonicalize_fusion_data(fusion.fusion_data)
    fusion_data["user_answer_cache"] = request.user_answer
    fusion_data["judgement_pending"] = True
    fusion.fusion_data = canonicalize_fusion_data(fusion_data)
    flag_modified(fusion, "fusion_data")

    db.commit()

    return {
        "message": "答案已缓存，请进行自我反思后请求评判",
        "fusion_id": id,
        "pending_judgement": True,
        "hint": "在请求AI评判前，请再次审视你的答案：逻辑是否严密？概念使用是否准确？"
    }


@router.post("/{id}/judge", response_model=FusionJudgeResponse)
async def judge_fusion_answer(
    id: int,
    db: Session = Depends(get_db),
    service: FusionService = Depends(get_fusion_service)
):
    """
    请求 AI 评判融合题答案（手动触发）
    必须先调用 /submit 提交答案
    """
    fusion = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.id == id,
        WrongAnswerV2.is_fusion == True
    ).first()

    if not fusion:
        raise HTTPException(status_code=404, detail="融合题不存在")

    # 获取缓存的答案
    fusion_data = canonicalize_fusion_data(fusion.fusion_data)
    user_answer = fusion_data.get("user_answer_cache")

    if not user_answer:
        raise HTTPException(status_code=400, detail="请先提交答案（调用 /submit）")

    # 调用 AI 评判
    result = await service.judge_fusion_answer(id, user_answer, db)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # 更新融合数据
    fusion_data["judgement_pending"] = False
    fusion_data["last_judgement"] = {
        "verdict": result["verdict"],
        "score": result["score"],
        "feedback": result["feedback"],
        "weak_links": result["weak_links"],
        "judged_at": datetime.now().isoformat(),
    }
    fusion.fusion_data = canonicalize_fusion_data(fusion_data)
    flag_modified(fusion, "fusion_data")

    # 更新统计
    fusion.retry_count = (fusion.retry_count or 0) + 1
    fusion.last_retried_at = datetime.now()

    # 应用严格模式 SM-2
    is_correct = result["verdict"] == "correct"
    quality = 5 if is_correct else (3 if result["verdict"] == "partial" else 1)
    service.apply_strict_sm2(fusion, is_correct, quality)

    # 记录重做
    retry = WrongAnswerRetry(
        wrong_answer_id=id,
        user_answer=user_answer[:100] + "..." if len(user_answer) > 100 else user_answer,
        is_correct=is_correct,
        confidence="sure" if is_correct else "unsure",
        retried_at=datetime.now()
    )
    db.add(retry)

    db.commit()

    return FusionJudgeResponse(**result)


@router.post("/{id}/diagnose", response_model=DiagnosisResponse)
async def diagnose_fusion_error(
    id: int,
    request: DiagnosisRequest,
    db: Session = Depends(get_db),
    service: FusionService = Depends(get_fusion_service)
):
    """
    答错后的苏格拉底式诊断
    判断是概念遗忘还是关系理解错误
    """
    fusion = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.id == id,
        WrongAnswerV2.is_fusion == True
    ).first()

    if not fusion:
        raise HTTPException(status_code=404, detail="融合题不存在")

    # 获取缓存的答案
    fusion_data = canonicalize_fusion_data(fusion.fusion_data)
    user_answer = fusion_data.get("user_answer_cache", request.user_answer)

    # 调用 AI 诊断
    result = await service.diagnose_error(id, user_answer, request.reflection, db)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # 根据诊断结果处理原题
    diagnosis_type = result.get("diagnosis_type", "relation_error")
    affected_ids = result.get("affected_parent_ids", [])

    # 如果是概念遗忘，恢复相关原题为 active
    if diagnosis_type in ["concept_forgot", "both"] and affected_ids:
        for parent_id in affected_ids:
            parent = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == parent_id).first()
            if parent:
                parent.mastery_status = "active"
                parent.archived_at = None
                parent.severity_tag = "stubborn"  # 标记为顽固病灶

    # 记录诊断结果
    if "diagnosis_history" not in fusion_data:
        fusion_data["diagnosis_history"] = []

    fusion_data["diagnosis_history"].append({
        "diagnosis_type": diagnosis_type,
        "affected_parent_ids": affected_ids,
        "reflection": request.reflection,
        "analysis": result.get("analysis", ""),
        "recommendation": result.get("recommendation", ""),
        "created_at": datetime.now().isoformat()
    })
    fusion.fusion_data = canonicalize_fusion_data(fusion_data)
    flag_modified(fusion, "fusion_data")

    db.commit()

    return DiagnosisResponse(**result)


@router.post("/{id}/archive", response_model=dict)
async def archive_fusion_question(
    id: int,
    db: Session = Depends(get_db)
):
    """
    归档融合题（掌握后）
    可以选择是否同时归档原题（默认归档）
    """
    fusion = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.id == id,
        WrongAnswerV2.is_fusion == True
    ).first()

    if not fusion:
        raise HTTPException(status_code=404, detail="融合题不存在")

    # 归档融合题
    fusion.mastery_status = "archived"
    fusion.archived_at = datetime.now()

    # 同时归档所有原题（如果还未归档）
    parent_ids = fusion.parent_ids or []
    parents = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.id.in_(parent_ids)
    ).all()

    archived_parents = []
    for parent in parents:
        if parent.mastery_status != "archived":
            parent.mastery_status = "archived"
            parent.archived_at = datetime.now()
            archived_parents.append(parent.id)

    db.commit()

    return {
        "message": "融合题已归档",
        "fusion_id": id,
        "archived_parents": archived_parents,
        "note": "原题也已归档，因为你已掌握高阶融合"
    }


@router.get("/queue", response_model=List[dict])
async def get_fusion_queue(
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """
    获取今日融合题队列（独立调度，基础优先）
    只返回到期的融合题
    """
    from datetime import date

    today = date.today()

    fusions = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.is_fusion == True,
        WrongAnswerV2.mastery_status == "active",
        or_(
            WrongAnswerV2.next_review_date == None,
            WrongAnswerV2.next_review_date <= today
        )
    ).order_by(
        WrongAnswerV2.fusion_level,  # 低层级优先
        WrongAnswerV2.next_review_date
    ).limit(limit).all()

    return [
        {
            "id": f.id,
            "question_text": f.question_text[:200] + "..." if len(f.question_text) > 200 else f.question_text,
            "fusion_level": f.fusion_level,
            "key_point": f.key_point,
            "next_review_date": f.next_review_date.isoformat() if f.next_review_date else None
        }
        for f in fusions
    ]
