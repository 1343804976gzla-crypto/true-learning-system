"""
数据看板 API
实时计算错题消耗进度和预期清仓时间
"""

from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from api_contracts import DashboardStatsResponse
from models import get_db
from learning_tracking_models import WrongAnswerV2, WrongAnswerRetry

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    daily_planned_review: int = Query(default=20, ge=1, description="每日计划复习量"),
    db: Session = Depends(get_db)
):
    """
    实时计算数据看板的 5 个核心指标

    Args:
        daily_planned_review: 每日计划复习量（默认 20 题）

    Returns:
        {
            "today_eliminated": 今日消除量,
            "today_retried": 今日重做量,
            "avg_new_per_day": 7天日均新增,
            "current_backlog": 当前错题积压量,
            "estimated_days_to_clear": 预计清仓天数,
            "daily_required_reviews": 需做错题数,
            "can_clear": 是否可清仓,
            "clear_message": 清仓提示信息
        }
    """

    # ========== 1. 今日消除量 ==========
    # 统计今天（00:00 到 23:59）状态变更为"已归档"的错题数量
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())

    today_eliminated = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "archived",
        WrongAnswerV2.archived_at >= today_start,
        WrongAnswerV2.archived_at <= today_end
    ).count()


    # ========== 2. 今日重做量 ==========
    # 统计今天重做的错题数量（不论对错）
    today_retried = db.query(WrongAnswerRetry).filter(
        WrongAnswerRetry.retried_at >= today_start,
        WrongAnswerRetry.retried_at <= today_end
    ).count()


    # ========== 3. 7天日均新增 ==========
    # 统计过去 7 天内新创建的错题总数 ÷ 7
    seven_days_ago = datetime.now() - timedelta(days=7)

    new_in_7days = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.created_at >= seven_days_ago
    ).count()

    avg_new_per_day = round(new_in_7days / 7, 2)


    # ========== 4. 当前错题积压量 ==========
    # 目前尚未归档的、处于活跃状态的错题总数
    current_backlog = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active"
    ).count()


    # ========== 5. 预计清仓天数 & 需做错题数 ==========
    # 公式：当前错题积压量 ÷ (每日计划复习量 - 7天日均新增)
    net_daily_progress = daily_planned_review - avg_new_per_day

    can_clear = True
    clear_message = ""
    estimated_days_to_clear = None
    daily_required_reviews = daily_planned_review

    if net_daily_progress <= 0:
        # 分母为 0 或负数：无法清仓
        can_clear = False
        clear_message = "⚠️ 无法清仓：新增速度 ≥ 复习速度，请提高每日计划复习量"
        estimated_days_to_clear = float('inf')  # 无穷大
    elif current_backlog == 0:
        # 没有积压，已清仓
        can_clear = True
        clear_message = "🎉 已清仓！当前无积压错题"
        estimated_days_to_clear = 0
    else:
        # 正常计算
        estimated_days_to_clear = round(current_backlog / net_daily_progress, 1)
        clear_message = f"✅ 按当前速度，预计 {estimated_days_to_clear} 天后清仓"

    # 计算需做错题数（为了达到清仓目标，每天需要做多少题）
    # 如果当前无法清仓，建议增加复习量
    if not can_clear:
        # 建议：至少要比新增速度快 5 题/天
        daily_required_reviews = int(avg_new_per_day + 5)
    else:
        daily_required_reviews = daily_planned_review


    # ========== 6. 严重度分布统计（额外数据） ==========
    severity_counts = {}
    for tag in ["critical", "stubborn", "landmine", "normal"]:
        count = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.mastery_status == "active",
            WrongAnswerV2.severity_tag == tag
        ).count()
        severity_counts[tag] = count


    # ========== 7. 本周趋势数据（额外数据） ==========
    # 统计过去 7 天每天的新增和消除数量
    weekly_trend = []
    for i in range(6, -1, -1):  # 从 6 天前到今天
        day = date.today() - timedelta(days=i)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = datetime.combine(day, datetime.max.time())

        new_count = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.created_at >= day_start,
            WrongAnswerV2.created_at <= day_end
        ).count()

        eliminated_count = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.mastery_status == "archived",
            WrongAnswerV2.archived_at >= day_start,
            WrongAnswerV2.archived_at <= day_end
        ).count()

        weekly_trend.append({
            "date": day.isoformat(),
            "new": new_count,
            "eliminated": eliminated_count,
            "net": new_count - eliminated_count
        })


    return {
        # 核心指标
        "today_eliminated": today_eliminated,
        "today_retried": today_retried,
        "avg_new_per_day": avg_new_per_day,
        "current_backlog": current_backlog,
        "estimated_days_to_clear": estimated_days_to_clear,
        "daily_required_reviews": daily_required_reviews,
        "can_clear": can_clear,
        "clear_message": clear_message,

        # 额外数据
        "severity_counts": severity_counts,
        "weekly_trend": weekly_trend,

        # 元数据
        "daily_planned_review": daily_planned_review,
        "net_daily_progress": round(net_daily_progress, 2),
        "calculated_at": datetime.now().isoformat()
    }
