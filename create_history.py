"""
上传历史看板功能
创建历史记录页面和API
"""

# 创建新的路由文件
upload_history_router = '''"""
上传历史记录路由
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from datetime import date, timedelta
from typing import List, Optional

from models import get_db, DailyUpload, Chapter

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/uploads")
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
    start_date = end_date - timedelta(days=days)
    
    # 查询上传记录
    uploads = db.query(DailyUpload).filter(
        DailyUpload.date >= start_date
    ).order_by(DailyUpload.date.desc()).all()
    
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


@router.get("/stats")
async def get_learning_stats(
    db: Session = Depends(get_db)
):
    """
    获取学习统计
    """
    from sqlalchemy import func
    
    # 总上传次数
    total_uploads = db.query(DailyUpload).count()
    
    # 最近7天上传次数
    week_ago = date.today() - timedelta(days=7)
    weekly_uploads = db.query(DailyUpload).filter(
        DailyUpload.date >= week_ago
    ).count()
    
    # 学习的科目分布
    chapters = db.query(Chapter).all()
    book_stats = {}
    for ch in chapters:
        if ch.book not in book_stats:
            book_stats[ch.book] = 0
        book_stats[ch.book] += 1
    
    # 最近的学习日期
    latest = db.query(DailyUpload).order_by(
        DailyUpload.date.desc()
    ).first()
    
    return {
        "total_uploads": total_uploads,
        "weekly_uploads": weekly_uploads,
        "latest_study_date": latest.date.isoformat() if latest else None,
        "book_distribution": book_stats
    }


@router.get("/timeline")
async def get_learning_timeline(
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    获取学习时间线
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    # 按日期分组统计
    from sqlalchemy import func
    
    daily_stats = db.query(
        DailyUpload.date,
        func.count(DailyUpload.id).label("count")
    ).filter(
        DailyUpload.date >= start_date
    ).group_by(DailyUpload.date).all()
    
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
'''

with open('routers/history.py', 'w', encoding='utf-8') as f:
    f.write(upload_history_router)

print('✅ history.py 路由已创建')
