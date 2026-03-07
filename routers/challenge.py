"""
错题靶向闯关 API
- GET  /api/challenge/queue    今日闯关队列（加权排序）
- POST /api/challenge/variant  为指定错题生成变式题
- POST /api/challenge/submit   提交闯关答案 + SM-2更新
- GET  /api/challenge/stats    闯关统计
"""

import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, case, or_
from datetime import datetime, date, timedelta
from typing import Optional
from pydantic import BaseModel

from models import get_db
from learning_tracking_models import WrongAnswerV2, WrongAnswerRetry


def _extract_answer_letters(raw: str) -> str:
    """从原始答案字符串中提取纯 A-E 字母（去除句号、选项文本等噪声），按字母排序以兼容多选题。"""
    return "".join(sorted(set(re.findall(r"[A-E]", (raw or "").strip().upper()))))

router = APIRouter(prefix="/api/challenge", tags=["challenge"])


# ========== SM-2 Algorithm ==========

def sm2_update(wa: WrongAnswerV2, quality: int):
    """
    SM-2 间隔重复算法更新。
    quality: 0-5 评分
      5 = 答对+确定
      4 = 答对+模糊
      3 = 答对+不确定
      1 = 答错+模糊
      0 = 答错+确定（盲区）
    """
    ef = wa.sm2_ef or 2.5
    reps = wa.sm2_repetitions or 0
    interval = wa.sm2_interval or 0

    if quality >= 3:
        # 答对：延长间隔
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 3
        elif reps == 3:
            interval = 7
        else:
            interval = min(int(interval * ef), 60)  # 最长60天
        # 更新 EF
        ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    else:
        # 答错：重置
        reps = 0
        interval = 1
        # EF 下降
        ef = ef - 0.2

    # EF 下限 1.3
    ef = max(1.3, ef)

    wa.sm2_ef = round(ef, 2)
    wa.sm2_repetitions = reps
    wa.sm2_interval = interval
    wa.next_review_date = date.today() + timedelta(days=interval)

    # 连续正确 3 次 → mastered
    if reps >= 3:
        wa.mastery_status = "archived"
        wa.archived_at = datetime.now()


def quality_from_result(is_correct: bool, confidence: str) -> int:
    """根据答题结果和自信度计算 SM-2 quality 评分"""
    if is_correct:
        if confidence == "sure":
            return 5
        elif confidence == "unsure":
            return 4
        else:
            return 3
    else:
        if confidence == "sure":
            return 0  # 盲区
        elif confidence == "unsure":
            return 1
        else:
            return 1


# ========== Pydantic Models ==========

class ChallengeSubmit(BaseModel):
    wrong_answer_id: int
    user_answer: str
    confidence: str = "unsure"
    time_spent_seconds: int = 0
    is_variant: bool = False  # 是否做的变式题


# ========== GET /queue ==========

@router.get("/queue")
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
        "options": wa.options,
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

@router.post("/variant")
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
    if wa.variant_data and wa.variant_data.get("generated_at"):
        try:
            gen_time = datetime.fromisoformat(wa.variant_data["generated_at"])
            if (datetime.now() - gen_time).total_seconds() < 86400:
                return {
                    "variant_question": wa.variant_data["variant_question"],
                    "variant_options": wa.variant_data["variant_options"],
                    "transform_type": wa.variant_data.get("transform_type", ""),
                    "core_knowledge": wa.variant_data.get("core_knowledge", ""),
                    "cached": True,
                }
        except (ValueError, KeyError):
            pass

    # 调用 AI 生成
    from services.variant_surgery_service import generate_variant
    try:
        variant = await generate_variant(wa)
        wa.variant_data = variant
        wa.updated_at = datetime.now()
        db.commit()

        return {
            "variant_question": variant["variant_question"],
            "variant_options": variant["variant_options"],
            "transform_type": variant.get("transform_type", ""),
            "core_knowledge": variant.get("core_knowledge", ""),
            "cached": False,
        }
    except Exception as e:
        # 变式生成失败，返回原题标记
        return {
            "variant_question": None,
            "error": str(e),
            "fallback": True,
        }


# ========== POST /submit ==========

@router.post("/submit")
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
    if body.is_variant and wa.variant_data:
        correct = _extract_answer_letters(wa.variant_data.get("variant_answer") or "")
    else:
        correct = _extract_answer_letters(wa.correct_answer or "")

    user_ans = _extract_answer_letters(body.user_answer)
    is_correct = user_ans == correct

    # 创建 retry 记录
    retry = WrongAnswerRetry(
        wrong_answer_id=wa.id,
        user_answer=body.user_answer,
        is_correct=is_correct,
        confidence=body.confidence,
        time_spent_seconds=body.time_spent_seconds,
        retried_at=datetime.now(),
        is_variant=body.is_variant,
    )
    db.add(retry)

    # 更新错题统计
    wa.retry_count += 1
    wa.last_retry_correct = is_correct
    wa.last_retry_confidence = body.confidence
    wa.last_retried_at = datetime.now()

    if not is_correct:
        wa.error_count += 1

    # severity 更新
    if not is_correct:
        if body.confidence == "sure" and wa.severity_tag != "critical":
            wa.severity_tag = "critical"
        elif wa.error_count >= 2 and wa.severity_tag not in ("critical", "stubborn"):
            wa.severity_tag = "stubborn"
    else:
        # 答对+确定 → landmine 降级
        if body.confidence == "sure" and wa.severity_tag == "landmine":
            wa.severity_tag = "normal"

    # SM-2 更新
    quality = quality_from_result(is_correct, body.confidence)
    sm2_update(wa, quality)
    auto_archived = wa.mastery_status == "archived"

    wa.updated_at = datetime.now()
    db.commit()

    # 构建返回
    result = {
        "is_correct": is_correct,
        "correct_answer": correct,
        "user_answer": body.user_answer,
        "confidence": body.confidence,
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "retry_count": wa.retry_count,
        # SM-2 状态
        "sm2_ef": wa.sm2_ef,
        "sm2_interval": wa.sm2_interval,
        "sm2_repetitions": wa.sm2_repetitions,
        "next_review_date": wa.next_review_date.isoformat() if wa.next_review_date else None,
        "auto_archived": auto_archived,
        # 解析
        "explanation": wa.explanation,
        "key_point": wa.key_point,
    }

    # 如果是变式题，附加变式解析
    if body.is_variant and wa.variant_data:
        result["variant_explanation"] = wa.variant_data.get("variant_explanation", "")
        result["variant_answer"] = wa.variant_data.get("variant_answer", "")
        result["core_knowledge"] = wa.variant_data.get("core_knowledge", "")

    return result


# ========== GET /stats ==========

@router.get("/stats")
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
