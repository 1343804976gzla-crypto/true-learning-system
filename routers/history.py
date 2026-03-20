"""
上传历史记录路由
"""

import hashlib
import io
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api_contracts import (
    HistoryLearningStatsResponse,
    HistoryReviewPlanResponse,
    HistoryReviewTaskDetailResponse,
    HistoryTimelineResponse,
    HistoryUploadResponse,
)
from models import get_db, DailyUpload, Chapter
from learning_tracking_models import LearningSession
from services.chapter_review_service import (
    DEFAULT_REVIEW_TIME_BUDGET_MINUTES,
    complete_task_with_status,
    ensure_daily_review_plan,
    ensure_task_questions,
    export_today_review_pdf,
    grade_task_answers,
    save_task_progress,
    serialize_task_detail,
)
from services.data_identity import DEFAULT_DEVICE_ID, resolve_request_actor_scope

router = APIRouter(prefix="/api/history", tags=["history"])


class ReviewTaskAnswerPayload(BaseModel):
    question_id: Optional[int] = None
    position: Optional[int] = None
    user_answer: str = ""


class ReviewTaskAutosaveRequest(BaseModel):
    answers: List[ReviewTaskAnswerPayload] = Field(default_factory=list)
    resume_position: int = 0


class ReviewTaskCompleteRequest(BaseModel):
    selected_status: str


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _source_label(source_type: str) -> str:
    return {
        "upload": "内容上传",
        "session": "学习会话",
    }.get(source_type, "学习记录")


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


def _study_session_date(session: LearningSession) -> date | None:
    if session.started_at:
        return session.started_at.date()
    if session.created_at:
        return session.created_at.date()
    return None


def _stable_synthetic_upload_id(source_id: str) -> int:
    return -int(hashlib.sha1(source_id.encode("utf-8")).hexdigest()[:12], 16)


def _merge_history_records(
    uploads: List[DailyUpload],
    session_uploads: List[LearningSession],
    chapter_map: dict[str, Chapter],
):
    explicit_records = []
    explicit_dates = {upload.date for upload in uploads if upload.date}
    for upload in uploads:
        ai_data = upload.ai_extracted or {}
        explicit_records.append(
            {
                "id": int(upload.id),
                "date": upload.date,
                "recorded_at": upload.created_at or datetime.combine(upload.date, datetime.min.time()),
                "book": ai_data.get("book", "未知"),
                "chapter_title": ai_data.get("chapter_title", "未识别章节"),
                "chapter_id": ai_data.get("chapter_id", ""),
                "concept_count": len(ai_data.get("concepts", [])),
                "summary": ai_data.get("summary", ""),
                "main_topic": ai_data.get("main_topic", ""),
                "source_type": "upload",
                "sort_datetime": upload.created_at or datetime.combine(upload.date, datetime.min.time()),
            }
        )

    fallback_records: dict[tuple[date, str], dict] = {}
    for session in session_uploads:
        study_date = _study_session_date(session)
        if not study_date or study_date in explicit_dates:
            continue
        raw_content = str(session.uploaded_content or "").strip()
        if not raw_content:
            continue
        content_signature = hashlib.sha1(raw_content.encode("utf-8")).hexdigest()
        dedupe_key = (study_date, content_signature)
        chapter = chapter_map.get(str(session.chapter_id or "").strip())
        sort_datetime = session.started_at or session.created_at or datetime.combine(study_date, datetime.min.time())
        record = {
            "id": _stable_synthetic_upload_id(session.id),
            "date": study_date,
            "recorded_at": sort_datetime,
            "book": getattr(chapter, "book", None) or "未识别",
            "chapter_title": getattr(chapter, "chapter_title", None) or (session.title or "未识别章节"),
            "chapter_id": str(session.chapter_id or ""),
            "concept_count": len(getattr(chapter, "concepts", None) or []),
            "summary": raw_content[:160],
            "main_topic": session.knowledge_point or "",
            "source_type": "session",
            "sort_datetime": sort_datetime,
        }
        existing = fallback_records.get(dedupe_key)
        if existing is None or sort_datetime > existing["sort_datetime"]:
            fallback_records[dedupe_key] = record

    combined = explicit_records + list(fallback_records.values())
    combined.sort(
        key=lambda item: (
            item.get("date") or date.min,
            item.get("sort_datetime") or datetime.min,
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    return combined


def _build_history_snapshot(db: Session, *, actor: dict, start_date: date | None = None) -> dict:
    uploads_query = _apply_actor_scope(
        db.query(DailyUpload),
        DailyUpload,
        actor=actor,
    )
    if start_date is not None:
        uploads_query = uploads_query.filter(DailyUpload.date >= start_date)
    uploads = uploads_query.order_by(DailyUpload.date.desc(), DailyUpload.id.desc()).all()

    all_upload_dates = {
        row[0]
        for row in _apply_actor_scope(
            db.query(DailyUpload.date),
            DailyUpload,
            actor=actor,
        ).distinct().all()
        if row[0]
    }

    session_query = (
        _apply_actor_scope(
            db.query(LearningSession),
            LearningSession,
            actor=actor,
        )
        .filter(
            LearningSession.uploaded_content.isnot(None),
            LearningSession.uploaded_content != "",
        )
        .order_by(LearningSession.started_at.desc(), LearningSession.created_at.desc(), LearningSession.id.desc())
    )
    session_uploads = session_query.all()
    if start_date is not None:
        window_session_uploads = [
            session
            for session in session_uploads
            if (study_date := _study_session_date(session)) is not None and study_date >= start_date
        ]
    else:
        window_session_uploads = [session for session in session_uploads if _study_session_date(session) is not None]
    session_upload_dates = {
        study_date
        for session in session_uploads
        if (study_date := _study_session_date(session)) is not None
    }

    chapter_ids = list(
        {
            str(session.chapter_id).strip()
            for session in window_session_uploads
            if str(session.chapter_id or "").strip()
        }
    )
    chapters = db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all() if chapter_ids else []
    chapter_map = {chapter.id: chapter for chapter in chapters}

    return {
        "records": _merge_history_records(uploads, window_session_uploads, chapter_map),
        "all_study_dates": sorted(all_upload_dates | session_upload_dates),
    }


@router.get("/uploads", response_model=HistoryUploadResponse)
async def get_upload_history(
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    获取上传历史记录
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=max(days - 1, 0))
    actor = resolve_request_actor_scope()
    snapshot = _build_history_snapshot(db, actor=actor, start_date=start_date)

    count_by_date: dict[date, int] = {}
    result = []
    for record in snapshot["records"]:
        record_date = record["date"]
        count_by_date[record_date] = count_by_date.get(record_date, 0) + 1
        summary = str(record.get("summary") or "")
        summary_preview = summary if len(summary) <= 100 else summary[:100].rstrip() + "..."
        source_type = str(record.get("source_type") or "upload")
        result.append({
            "id": int(record["id"]),
            "date": record_date.isoformat(),
            "recorded_at": _serialize_datetime(record.get("recorded_at")),
            "book": record.get("book", "未知"),
            "chapter_title": record.get("chapter_title", "未识别"),
            "chapter_id": record.get("chapter_id", ""),
            "concept_count": int(record.get("concept_count") or 0),
            "summary": summary_preview,
            "main_topic": record.get("main_topic", ""),
            "source_type": source_type,
            "source_label": _source_label(source_type),
        })

    peak_date = None
    peak_count = 0
    if count_by_date:
        peak_date_obj, peak_count = max(count_by_date.items(), key=lambda item: (item[1], item[0]))
        peak_date = peak_date_obj.isoformat()

    active_days = len(count_by_date)
    average_uploads_per_active_day = round(len(result) / active_days, 1) if active_days else 0.0

    return {
        "total": len(result),
        "days": days,
        "active_days": active_days,
        "average_uploads_per_active_day": average_uploads_per_active_day,
        "peak_date": peak_date,
        "peak_count": peak_count,
        "uploads": result
    }


@router.get("/stats", response_model=HistoryLearningStatsResponse)
async def get_learning_stats(
    db: Session = Depends(get_db)
):
    """
    获取学习统计
    """
    actor = resolve_request_actor_scope()
    snapshot = _build_history_snapshot(db, actor=actor, start_date=None)
    records = snapshot["records"]
    total_uploads = len(records)
    week_ago = date.today() - timedelta(days=6)
    weekly_uploads = sum(1 for record in records if record.get("date") and record["date"] >= week_ago)

    book_stats = {}
    source_stats = {}
    count_by_date: dict[date, int] = {}
    for record in records:
        record_date = record.get("date")
        if record_date:
            count_by_date[record_date] = count_by_date.get(record_date, 0) + 1
        book = str(record.get("book") or "未知")
        book_stats[book] = book_stats.get(book, 0) + 1
        source_label = _source_label(str(record.get("source_type") or "upload"))
        source_stats[source_label] = source_stats.get(source_label, 0) + 1
    all_study_dates = snapshot["all_study_dates"]
    streak_days = _compute_streak_days(all_study_dates)
    active_days = len(all_study_dates)
    average_uploads_per_active_day = round(total_uploads / active_days, 1) if active_days else 0.0
    busiest_day = None
    busiest_day_count = 0
    if count_by_date:
        busiest_day_obj, busiest_day_count = max(count_by_date.items(), key=lambda item: (item[1], item[0]))
        busiest_day = busiest_day_obj.isoformat()

    return {
        "total_uploads": total_uploads,
        "weekly_uploads": weekly_uploads,
        "latest_study_date": all_study_dates[-1].isoformat() if all_study_dates else None,
        "streak_days": streak_days,
        "active_days": active_days,
        "average_uploads_per_active_day": average_uploads_per_active_day,
        "busiest_day": busiest_day,
        "busiest_day_count": busiest_day_count,
        "book_distribution": book_stats,
        "source_distribution": source_stats,
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
    snapshot = _build_history_snapshot(db, actor=actor, start_date=start_date)
    count_by_date = {}
    for record in snapshot["records"]:
        record_date = record.get("date")
        if not record_date:
            continue
        count_by_date[record_date] = count_by_date.get(record_date, 0) + 1

    timeline = []
    current_date = start_date
    while current_date <= end_date:
        count = count_by_date.get(current_date, 0)
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


@router.get("/review-plan", response_model=HistoryReviewPlanResponse)
async def get_today_review_plan(
    time_budget_minutes: int = Query(default=DEFAULT_REVIEW_TIME_BUDGET_MINUTES, ge=15, le=120),
    review_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    payload = ensure_daily_review_plan(
        db,
        actor_key=actor["actor_key"],
        target_date=review_date,
        time_budget_minutes=time_budget_minutes,
    )
    db.commit()
    return payload


@router.get("/review-task/{task_id}", response_model=HistoryReviewTaskDetailResponse)
async def get_review_task_detail(
    task_id: int,
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    task = await ensure_task_questions(db, actor_key=actor["actor_key"], task_id=task_id)
    db.commit()
    return serialize_task_detail(task)


@router.post("/review-task/{task_id}/autosave", response_model=HistoryReviewTaskDetailResponse)
async def autosave_review_task(
    task_id: int,
    body: ReviewTaskAutosaveRequest,
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    payload = save_task_progress(
        db,
        actor_key=actor["actor_key"],
        task_id=task_id,
        answers=[item.model_dump() for item in body.answers],
        resume_position=body.resume_position,
    )
    db.commit()
    return payload


@router.post("/review-task/{task_id}/grade", response_model=HistoryReviewTaskDetailResponse)
async def grade_review_task(
    task_id: int,
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    payload = await grade_task_answers(db, actor_key=actor["actor_key"], task_id=task_id)
    db.commit()
    return payload


@router.post("/review-task/{task_id}/complete", response_model=HistoryReviewTaskDetailResponse)
async def complete_review_task(
    task_id: int,
    body: ReviewTaskCompleteRequest,
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    payload = complete_task_with_status(
        db,
        actor_key=actor["actor_key"],
        task_id=task_id,
        selected_status=body.selected_status,
    )
    db.commit()
    return payload


@router.get("/review-pdf", response_model=None, response_class=StreamingResponse)
async def export_review_pdf(
    review_date: Optional[date] = Query(default=None),
    time_budget_minutes: int = Query(default=DEFAULT_REVIEW_TIME_BUDGET_MINUTES, ge=15, le=120),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    actor = resolve_request_actor_scope()
    pdf_bytes = await export_today_review_pdf(
        db,
        actor_key=actor["actor_key"],
        target_date=review_date,
        time_budget_minutes=time_budget_minutes,
    )
    db.commit()
    filename = f"chapter-review-{(review_date or date.today()).isoformat()}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"",
            "Cache-Control": "no-store",
        },
    )
