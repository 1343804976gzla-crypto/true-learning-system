"""
上传历史记录路由
"""

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api_contracts import (
    HistoryLearningStatsResponse,
    HistoryTimelineResponse,
    HistoryUploadResponse,
)
from models import get_db, DailyUpload, Chapter
from services.data_identity import DEFAULT_DEVICE_ID, resolve_request_actor_scope

router = APIRouter(prefix="/api/history", tags=["history"])


def _apply_actor_scope(query, model, *, actor: dict):
    scope_user_id = actor.get("scope_user_id")
    scope_device_id = actor.get("scope_device_id")
    scope_device_ids = list(actor.get("scope_device_ids") or [])

    if scope_user_id and hasattr(model, "user_id"):
        query = query.filter(model.user_id == scope_user_id)
    if scope_device_ids and hasattr(model, "device_id"):
        device_column = getattr(model, "device_id")
        if not scope_user_id and DEFAULT_DEVICE_ID in scope_device_ids:
            query = query.filter(
                or_(
                    device_column.in_(scope_device_ids),
                    device_column.is_(None),
                    device_column == "",
                )
            )
        elif len(scope_device_ids) == 1:
            query = query.filter(device_column == scope_device_ids[0])
        else:
            query = query.filter(device_column.in_(scope_device_ids))
    elif scope_device_id and hasattr(model, "device_id"):
        query = query.filter(getattr(model, "device_id") == scope_device_id)
    return query


def _compute_streak_days(study_dates):
    normalized_dates = {item for item in study_dates if item}
    streak = 0
    cursor = date.today()
    while cursor in normalized_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


@router.get("/uploads", response_model=HistoryUploadResponse)
async def get_upload_history(
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    获取上传历史记录
    """
    from datetime import datetime
    
    # 计算日期范围
    end_date = date.today()
    start_date = end_date - timedelta(days=max(days - 1, 0))
    actor = resolve_request_actor_scope()
    
    # 查询上传记录
    uploads = (
        _apply_actor_scope(
            db.query(DailyUpload),
            DailyUpload,
            actor=actor,
        )
        .filter(DailyUpload.date >= start_date)
        .order_by(DailyUpload.date.desc())
        .all()
    )
    
    result = []
    for upload in uploads:
        ai_data = upload.ai_extracted or {}
        result.append({
            "id": upload.id,
            "date": upload.date.isoformat(),
            "book": ai_data.get("book", "未知"),
            "chapter_title": ai_data.get("chapter_title", "未识别"),
            "chapter_id": ai_data.get("chapter_id", ""),
            "concept_count": len(ai_data.get("concepts", [])),
            "summary": ai_data.get("summary", "")[:100] + "..." if ai_data.get("summary") else "",
            "main_topic": ai_data.get("main_topic", "")
        })
    
    return {
        "total": len(result),
        "days": days,
        "uploads": result
    }


@router.get("/stats", response_model=HistoryLearningStatsResponse)
async def get_learning_stats(
    db: Session = Depends(get_db)
):
    """
    获取学习统计
    """
    from sqlalchemy import func

    actor = resolve_request_actor_scope()
    uploads_query = _apply_actor_scope(
        db.query(DailyUpload),
        DailyUpload,
        actor=actor,
    )
    
    # 总上传次数
    total_uploads = uploads_query.count()
    
    # 最近7天上传次数
    week_ago = date.today() - timedelta(days=7)
    weekly_uploads = uploads_query.filter(
        DailyUpload.date >= week_ago
    ).count()
    
    # 学习的科目分布
    book_stats = {}
    for upload in uploads_query.all():
        ai_data = upload.ai_extracted or {}
        book = str(ai_data.get("book") or "未知")
        book_stats[book] = book_stats.get(book, 0) + 1
    
    # 最近的学习日期
    latest = uploads_query.order_by(
        DailyUpload.date.desc()
    ).first()

    study_dates = [row[0] for row in uploads_query.with_entities(DailyUpload.date).distinct().all()]
    streak_days = _compute_streak_days(study_dates)
    
    return {
        "total_uploads": total_uploads,
        "weekly_uploads": weekly_uploads,
        "latest_study_date": latest.date.isoformat() if latest else None,
        "streak_days": streak_days,
        "book_distribution": book_stats
    }


@router.get("/timeline", response_model=HistoryTimelineResponse)
async def get_learning_timeline(
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    获取学习时间线
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=max(days - 1, 0))
    actor = resolve_request_actor_scope()
    
    # 按日期分组统计
    from sqlalchemy import func
    
    daily_stats = (
        _apply_actor_scope(
            db.query(
                DailyUpload.date,
                func.count(DailyUpload.id).label("count")
            ),
            DailyUpload,
            actor=actor,
        )
        .filter(DailyUpload.date >= start_date)
        .group_by(DailyUpload.date)
        .all()
    )
    
    # 构建时间线
    timeline = []
    current_date = start_date
    while current_date <= end_date:
        count = next((s.count for s in daily_stats if s.date == current_date), 0)
        timeline.append({
            "date": current_date.isoformat(),
            "has_study": count > 0,
            "upload_count": count
        })
        current_date += timedelta(days=1)
    
    return {
        "days": days,
        "timeline": timeline
    }
