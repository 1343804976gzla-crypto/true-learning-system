"""
错题靶向闯关 API
- GET  /api/challenge/queue    今日闯关队列（加权排序）
- POST /api/challenge/variant  为指定错题生成变式题
- POST /api/challenge/submit   提交闯关答案 + SM-2更新
- GET  /api/challenge/stats    闯关统计
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, case, or_
from datetime import datetime, date, timedelta
from typing import Optional
from pydantic import BaseModel

from api_contracts import (
    ChallengeCheckAnswerResponse,
    ChallengeEvaluateRationaleResponse,
    ChallengeQueueResponse,
    ChallengeStatsResponse,
    ChallengeSubmitResponse,
    ChallengeVariantResponse,
)
from models import get_db
from learning_tracking_models import WrongAnswerV2, WrongAnswerRetry
from utils.answer import normalize_answer
from utils.data_contracts import canonicalize_variant_data, coerce_confidence
from utils.sm2 import sm2_update, quality_from_result

router = APIRouter(prefix="/api/challenge", tags=["challenge"])


# ========== Pydantic Models ==========

class ChallengeSubmit(BaseModel):
    wrong_answer_id: int
    user_answer: str
    confidence: str = "unsure"
    time_spent_seconds: int = 0
    is_variant: bool = False  # 是否做的变式题
    recall_text: str = ""           # 回忆阶段文本
    skip_recall: bool = False       # 是否跳过了回忆
    skipped_rationale: bool = False # 是否跳过了自证


class CheckAnswerRequest(BaseModel):
    wrong_answer_id: int
    user_answer: str
    is_variant: bool = False


def _normalize_confidence_value(value: Optional[str]) -> str:
    return coerce_confidence(value, default="unsure")


# ========== GET /queue ==========

@router.get("/queue", response_model=ChallengeQueueResponse)
async def get_challenge_queue(
    count: int = 10,
    db: Session = Depends(get_db)
):
    """
    获取今日闯关队列 - 基于认知心理学抗遗忘算法。

    算法逻辑：
    1. 绝对优先级池（Critical Pool）：致命盲区（critical）无视配额比例，全部优先
    2. 核心区（Core Pool）：50% 配额 - 过去48小时内答错的题目（趁热打铁）
    3. 巩固区（Review Pool）：30% 配额 - SM-2 到期的题目（对抗遗忘曲线）
    4. 铲雪区（Shovel Pool）：20% 配额 + 顺延兜底 - 历史积压死角清理

    配额顺延机制：
    - 如果核心区或巩固区不足目标配额，差值自动累加到铲雪区
    - 确保总题量尽可能接近 count 参数

    碎片化闯关支持：
    - 自动排除今日已答题目，支持分多次闯关
    - 每次进入不会重复做到今天已经做过的题
    """
    today = date.today()
    TOTAL_LIMIT = count  # 总题量限制

    # ========== 全局过滤：今日已答题目排除 ==========
    # 查询今日已答题目 ID 列表（00:00:00 到 23:59:59）
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())

    today_answered_query = db.query(WrongAnswerRetry.wrong_answer_id).filter(
        WrongAnswerRetry.retried_at >= today_start,
        WrongAnswerRetry.retried_at <= today_end
    ).distinct()

    today_answered_ids = [row[0] for row in today_answered_query.all()]

    # ========== 第一步：绝对优先级池（Critical Pool） ==========
    # 查询所有 critical 错题，按 error_count 降序（最顽固的优先）
    critical_query = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active",
        WrongAnswerV2.severity_tag == "critical"
    )

    # 排除今日已答题目
    if today_answered_ids:
        critical_query = critical_query.filter(~WrongAnswerV2.id.in_(today_answered_ids))

    critical_items = critical_query.order_by(
        desc(WrongAnswerV2.error_count)
    ).limit(TOTAL_LIMIT).all()  # 最多取 TOTAL_LIMIT 个

    K = len(critical_items)  # 实际 critical 数量
    selected_ids = [wa.id for wa in critical_items]  # 已选题目 ID 列表

    # 计算剩余配额
    REMAINING = TOTAL_LIMIT - K

    # 如果 critical 已经占满或超过总配额，直接返回
    if REMAINING <= 0:
        return {
            "count": len(critical_items),
            "date": today.isoformat(),
            "items": [_serialize_queue_item(wa, today) for wa in critical_items],
            "pool_stats": {
                "critical": K,
                "core": 0,
                "review": 0,
                "shovel": 0,
                "total": K,
                "today_answered": len(today_answered_ids)
            }
        }

    # ========== 第二步：计算 50/30/20 目标配额 ==========
    TARGET_CORE = int(REMAINING * 0.5)      # 50% 配额
    TARGET_REVIEW = int(REMAINING * 0.3)    # 30% 配额
    TARGET_SHOVEL = REMAINING - TARGET_CORE - TARGET_REVIEW  # 20% 配额（兜底）

    # ========== 第三步：分池查询与顺延兜底机制 ==========

    # --- 3.1 核心区（Core Pool）：趁热打铁 ---
    # 条件：过去 48 小时内答错的题目（基于 last_wrong_at）
    hours_48_ago = datetime.now() - timedelta(hours=48)

    core_query = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active",
        WrongAnswerV2.last_wrong_at >= hours_48_ago  # 过去 48 小时内答错
    )

    # 排除已选题目
    if selected_ids:
        core_query = core_query.filter(~WrongAnswerV2.id.in_(selected_ids))

    # 排除今日已答题目
    if today_answered_ids:
        core_query = core_query.filter(~WrongAnswerV2.id.in_(today_answered_ids))

    core_items = core_query.order_by(
        desc(WrongAnswerV2.last_wrong_at)  # 最近答错的优先
    ).limit(TARGET_CORE).all()

    actual_core = len(core_items)
    selected_ids.extend([wa.id for wa in core_items])

    # 顺延机制：如果核心区不足，差值累加到铲雪区
    if actual_core < TARGET_CORE:
        shortage = TARGET_CORE - actual_core
        TARGET_SHOVEL += shortage

    # --- 3.2 巩固区（Review Pool）：对抗遗忘曲线 ---
    # 条件：SM-2 到期的题目（next_review_date <= today）
    review_query = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active",
        WrongAnswerV2.next_review_date <= today  # 已到期
    )

    # 排除已选题目
    if selected_ids:
        review_query = review_query.filter(~WrongAnswerV2.id.in_(selected_ids))

    # 排除今日已答题目
    if today_answered_ids:
        review_query = review_query.filter(~WrongAnswerV2.id.in_(today_answered_ids))

    review_items = review_query.order_by(
        WrongAnswerV2.next_review_date.asc()  # 最早到期的优先（最长逾期）
    ).limit(TARGET_REVIEW).all()

    actual_review = len(review_items)
    selected_ids.extend([wa.id for wa in review_items])

    # 顺延机制：如果巩固区不足，差值累加到铲雪区
    if actual_review < TARGET_REVIEW:
        shortage = TARGET_REVIEW - actual_review
        TARGET_SHOVEL += shortage

    # --- 3.3 铲雪区（Shovel Pool）：历史积压死角清理 ---
    # 条件：剩余的所有 active 错题
    # 排序：NULL 优先（从未复习），然后按创建时间升序（最久远的优先）
    shovel_query = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active"
    )

    # 排除已选题目
    if selected_ids:
        shovel_query = shovel_query.filter(~WrongAnswerV2.id.in_(selected_ids))

    # 排除今日已答题目
    if today_answered_ids:
        shovel_query = shovel_query.filter(~WrongAnswerV2.id.in_(today_answered_ids))

    shovel_items = shovel_query.order_by(
        WrongAnswerV2.next_review_date.is_(None).desc(),  # NULL 绝对优先
        WrongAnswerV2.created_at.asc()  # 创建时间升序（最久远的优先）
    ).limit(TARGET_SHOVEL).all()

    actual_shovel = len(shovel_items)

    # ========== 第四步：合并与返回 ==========
    final_queue = critical_items + core_items + review_items + shovel_items

    return {
        "count": len(final_queue),
        "date": today.isoformat(),
        "items": [_serialize_queue_item(wa, today) for wa in final_queue],
        "pool_stats": {
            "critical": K,
            "core": actual_core,
            "review": actual_review,
            "shovel": actual_shovel,
            "total": len(final_queue),
            "target_core": TARGET_CORE,
            "target_review": TARGET_REVIEW,
            "target_shovel": TARGET_SHOVEL - (TARGET_CORE - actual_core) - (TARGET_REVIEW - actual_review),  # 原始目标
            "today_answered": len(today_answered_ids)  # 今日已答题目数
        }
    }


def _serialize_queue_item(wa: WrongAnswerV2, today: date) -> dict:
    """序列化队列项目为前端所需格式"""
    is_overdue = wa.next_review_date is None or wa.next_review_date <= today
    return {
        "id": wa.id,
        "question_text": wa.question_text,
        "options": wa.options or {},
        "key_point": wa.key_point,
        "question_type": wa.question_type,
        "difficulty": wa.difficulty,
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "sm2_interval": wa.sm2_interval or 0,
        "sm2_repetitions": wa.sm2_repetitions or 0,
        "next_review_date": wa.next_review_date.isoformat() if wa.next_review_date else None,
        "is_overdue": is_overdue,
        "has_variant": wa.variant_data is not None,
        # 不含 correct_answer
    }


# ========== POST /variant ==========

@router.post("/variant", response_model=ChallengeVariantResponse)
async def generate_challenge_variant(
    wrong_answer_id: int,
    db: Session = Depends(get_db)
):
    """
    为闯关题目生成变式题。
    所有 severity 都可以生成（不限 critical）。
    24h 缓存策略。
    """
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_answer_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    # 缓存策略：24h 内复用
    cached_variant = canonicalize_variant_data(wa.variant_data)
    if cached_variant and cached_variant.get("generated_at"):
        try:
            gen_time = datetime.fromisoformat(cached_variant["generated_at"])
            if (datetime.now() - gen_time).total_seconds() < 86400:
                return {
                    "variant_question": cached_variant.get("variant_question", ""),
                    "variant_options": cached_variant.get("variant_options", {}),
                    "transform_type": cached_variant.get("transform_type", ""),
                    "core_knowledge": cached_variant.get("core_knowledge", ""),
                    "cached": True,
                }
        except (ValueError, KeyError):
            pass

    # 调用 AI 生成
    from services.variant_surgery_service import generate_variant
    try:
        variant = await generate_variant(wa)
        wa.variant_data = canonicalize_variant_data(variant, fallback_generated_at=datetime.now())
        wa.updated_at = datetime.now()
        db.commit()
        stored_variant = canonicalize_variant_data(wa.variant_data) or {}

        return {
            "variant_question": stored_variant.get("variant_question", ""),
            "variant_options": stored_variant.get("variant_options", {}),
            "transform_type": stored_variant.get("transform_type", ""),
            "core_knowledge": stored_variant.get("core_knowledge", ""),
            "cached": False,
        }
    except Exception as e:
        # 变式生成失败，返回原题标记
        return {
            "variant_question": None,
            "error": str(e),
            "fallback": True,
        }


# ========== POST /check-answer (无副作用，仅判定对错) ==========

@router.post("/check-answer", response_model=ChallengeCheckAnswerResponse)
async def check_challenge_answer(body: CheckAnswerRequest, db: Session = Depends(get_db)):
    """
    纯判定接口：仅返回 is_correct，不创建 retry 记录，不更新 SM-2。
    用于 Phase 1 前端决定是否触发 Phase 2（自证），避免前端缺少 variant_answer 导致误判。
    """
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == body.wrong_answer_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    variant_data = canonicalize_variant_data(wa.variant_data) or {}
    if body.is_variant and variant_data:
        correct = normalize_answer(variant_data.get("variant_answer") or "")
    else:
        correct = normalize_answer(wa.correct_answer or "")

    is_correct = normalize_answer(body.user_answer) == correct

    return {"is_correct": is_correct}


# ========== POST /submit ==========

@router.post("/submit", response_model=ChallengeSubmitResponse)
async def submit_challenge(body: ChallengeSubmit, db: Session = Depends(get_db)):
    """
    提交闯关答案。
    1. 判定对错
    2. 创建 retry 记录
    3. SM-2 更新
    4. severity 更新
    5. 返回结果 + 解析
    """
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == body.wrong_answer_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    # 判定对错（提取纯字母，防止 AI 返回 "B. 选项内容" 等格式导致误判）
    confidence = _normalize_confidence_value(body.confidence)

    variant_data = canonicalize_variant_data(wa.variant_data) or {}
    if body.is_variant and variant_data:
        correct = normalize_answer(variant_data.get("variant_answer") or "")
    else:
        correct = normalize_answer(wa.correct_answer or "")

    is_correct = normalize_answer(body.user_answer) == correct

    # 创建 retry 记录
    retry = WrongAnswerRetry(
        wrong_answer_id=wa.id,
        user_answer=body.user_answer,
        is_correct=is_correct,
        confidence=confidence,
        time_spent_seconds=body.time_spent_seconds,
        retried_at=datetime.now(),
        is_variant=body.is_variant,
        rationale_text=body.recall_text or None,
    )
    db.add(retry)

    # 更新错题统计
    wa.retry_count += 1
    wa.last_retry_correct = is_correct
    wa.last_retry_confidence = confidence
    wa.last_retried_at = datetime.now()

    if not is_correct:
        wa.error_count += 1

    # severity 更新
    if not is_correct:
        if confidence == "sure" and wa.severity_tag != "critical":
            wa.severity_tag = "critical"
        elif wa.error_count >= 2 and wa.severity_tag not in ("critical", "stubborn"):
            wa.severity_tag = "stubborn"
    else:
        # 答对+确定 → landmine 降级
        if confidence == "sure" and wa.severity_tag == "landmine":
            wa.severity_tag = "normal"

    # SM-2 更新（含跳过回忆/跳过自证的降档惩罚）
    quality = quality_from_result(is_correct, confidence)
    if body.skip_recall:
        quality = max(0, quality - 1)
    if body.skipped_rationale:
        quality = max(0, quality - 1)
    sm2_update(wa, quality)
    auto_archived = wa.mastery_status == "archived"

    can_archive = (is_correct and confidence == "sure") and not auto_archived

    wa.updated_at = datetime.now()
    db.commit()

    # 构建返回
    result = {
        "is_correct": is_correct,
        "correct_answer": correct,
        "user_answer": body.user_answer,
        "confidence": confidence,
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "retry_count": wa.retry_count,
        # SM-2 状态
        "sm2_ef": wa.sm2_ef,
        "sm2_interval": wa.sm2_interval,
        "sm2_repetitions": wa.sm2_repetitions,
        "next_review_date": wa.next_review_date.isoformat() if wa.next_review_date else None,
        "auto_archived": auto_archived,
        "can_archive": can_archive,
        # 解析
        "explanation": wa.explanation,
        "key_point": wa.key_point,
        "recall_text": body.recall_text or "",
    }

    # 如果是变式题，附加变式解析，并优先使用变式解析作为主解析
    if body.is_variant and variant_data:
        variant_expl = variant_data.get("variant_explanation", "")
        result["variant_explanation"] = variant_expl
        result["variant_answer"] = variant_data.get("variant_answer", "")
        result["core_knowledge"] = variant_data.get("core_knowledge", "")
        # 变式解析非空时，覆盖主 explanation 字段，避免前端回退到原题解析
        if variant_expl:
            result["explanation"] = variant_expl

    return result


# ========== POST /evaluate-rationale ==========

class RationaleEvaluateRequest(BaseModel):
    wrong_answer_id: int
    user_answer: str
    confidence: str = "unsure"
    rationale_text: str = ""
    time_spent_seconds: int = 0


@router.post("/evaluate-rationale", response_model=ChallengeEvaluateRationaleResponse)
async def evaluate_challenge_rationale(
    body: RationaleEvaluateRequest, db: Session = Depends(get_db)
):
    """
    Phase 2 专用：仅做 AI 推理评估，不重复创建 retry 记录和 SM-2 更新。
    submit 端点已在 Phase 1 完成了判定、retry、SM-2 等操作。
    此端点仅返回 AI 对推理文本的评估结果。
    """
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == body.wrong_answer_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    # 判定对错（和 submit 一致的逻辑）
    variant_data = canonicalize_variant_data(wa.variant_data) or {}
    if variant_data:
        correct = normalize_answer(variant_data.get("variant_answer") or "")
    else:
        correct = normalize_answer(wa.correct_answer or "")
    is_correct = normalize_answer(body.user_answer) == correct

    # AI 评估推理
    from services.variant_surgery_service import evaluate_rationale
    ai_eval = await evaluate_rationale(wa, body.user_answer, body.rationale_text, is_correct)

    verdict = ai_eval.get("verdict", "failed")

    # 根据 verdict 更新 severity
    if verdict == "lucky_guess":
        wa.severity_tag = "landmine"
    # 注意：不增加 error_count，因为这个端点不创建 retry 记录
    # error_count 应该在实际提交答案时增加（submit 或 variant/judge）
    wa.updated_at = datetime.now()
    db.commit()

    # 返回 AI 评估 + 变式解析
    result = {
        "is_correct": is_correct,
        "correct_answer": variant_data.get("variant_answer", "") if variant_data else wa.correct_answer,
        "verdict": verdict,
        "reasoning_score": ai_eval.get("reasoning_score", 0),
        "diagnosis": ai_eval.get("diagnosis", ""),
        "weak_links": ai_eval.get("weak_links", []),
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "retry_count": wa.retry_count,
        # SM-2 状态（从已有数据读取，不再重新计算）
        "sm2_ef": wa.sm2_ef,
        "sm2_interval": wa.sm2_interval,
        "sm2_repetitions": wa.sm2_repetitions,
        "next_review_date": wa.next_review_date.isoformat() if wa.next_review_date else None,
        "auto_archived": wa.mastery_status == "archived",
    }

    # 附加解析
    if variant_data:
        result["variant_explanation"] = variant_data.get("variant_explanation", "")
        result["variant_answer"] = variant_data.get("variant_answer", "")
        result["core_knowledge"] = variant_data.get("core_knowledge", "")
    result["explanation"] = wa.explanation
    result["key_point"] = wa.key_point

    return result

@router.get("/stats", response_model=ChallengeStatsResponse)
async def get_challenge_stats(db: Session = Depends(get_db)):
    """闯关统计"""
    today = date.today()

    active = db.query(WrongAnswerV2).filter(WrongAnswerV2.mastery_status == "active")
    total_active = active.count()

    # 今日到期
    overdue = active.filter(
        or_(
            WrongAnswerV2.next_review_date == None,
            WrongAnswerV2.next_review_date <= today,
        )
    ).count()

    # 今日已闯关（今天有 retry 记录的）
    today_start = datetime.combine(today, datetime.min.time())
    today_retries = db.query(WrongAnswerRetry).filter(
        WrongAnswerRetry.retried_at >= today_start
    ).all()
    today_done = len(set(r.wrong_answer_id for r in today_retries))
    today_correct = sum(1 for r in today_retries if r.is_correct)
    today_total = len(today_retries)

    # 已掌握（archived via SM-2）
    mastered = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "archived"
    ).count()

    return {
        "total_active": total_active,
        "overdue_count": overdue,
        "today_done": today_done,
        "today_correct": today_correct,
        "today_total": today_total,
        "today_accuracy": round(today_correct / today_total * 100, 1) if today_total > 0 else 0,
        "mastered_count": mastered,
    }
