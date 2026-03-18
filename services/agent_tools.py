from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from learning_tracking_models import INVALID_CHAPTER_IDS, LearningSession, WrongAnswerRetry, WrongAnswerV2
from models import Chapter, ConceptMastery, DailyUpload, TestRecord
from routers.dashboard import get_dashboard_stats
from routers.learning_tracking import get_progress_board
from services.agent_actions import list_action_tool_definitions
from services.data_identity import (
    build_device_scope_aliases,
    ensure_learning_identity_schema,
    resolve_query_identity,
)
from services.openmanus_bridge import run_openmanus_consult
from services.openviking_service import is_openviking_enabled, search_openviking_context
from utils.agent_contracts import AgentToolDefinition


class _ToolArgsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WrongAnswersArgs(_ToolArgsModel):
    status: Literal["active", "archived", "all"] = "active"
    limit: int = Field(default=6, ge=1, le=12)


class LearningSessionsArgs(_ToolArgsModel):
    limit: int = Field(default=5, ge=1, le=10)
    session_type: Literal["exam", "detail_practice", "all"] = "all"


class ProgressSummaryArgs(_ToolArgsModel):
    period: Literal["all", "30d", "7d"] = "all"


class KnowledgeMasteryArgs(_ToolArgsModel):
    limit: int = Field(default=6, ge=3, le=12)
    due_days: int = Field(default=7, ge=0, le=30)


class StudyHistoryArgs(_ToolArgsModel):
    days: int = Field(default=30, ge=7, le=180)
    limit: int = Field(default=6, ge=1, le=12)


class ReviewPressureArgs(_ToolArgsModel):
    daily_planned_review: int = Field(default=20, ge=5, le=200)


class OpenVikingSearchArgs(_ToolArgsModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=8)
    target_uri: str = Field(default="", max_length=500)


class OpenManusConsultArgs(_ToolArgsModel):
    query: str = Field(min_length=1, max_length=2000)
    max_steps: int = Field(default=4, ge=1, le=8)


READ_TOOL_DEFINITIONS: List[AgentToolDefinition] = [
    AgentToolDefinition(
        name="get_wrong_answers",
        description="提取当前错题本的重点条目，用于分析薄弱点与复习优先级。",
        default_limit=6,
        keywords=["错题", "薄弱", "复习", "重做", "弱点", "难点"],
        tool_type="read",
        risk_level="low",
    ),
    AgentToolDefinition(
        name="get_learning_sessions",
        description="提取最近学习会话，用于还原近期学习轨迹和做题情况。",
        default_limit=5,
        keywords=["最近", "会话", "学习记录", "轨迹", "做题", "历史"],
        tool_type="read",
        risk_level="low",
    ),
    AgentToolDefinition(
        name="get_progress_summary",
        description="提取总体掌握进度、正确率和近期趋势摘要。",
        default_limit=1,
        keywords=["进度", "掌握", "趋势", "统计", "表现", "情况", "压力"],
        tool_type="read",
        risk_level="low",
    ),
    AgentToolDefinition(
        name="get_knowledge_mastery",
        description="提取知识点掌握度、到期复习和掌握断层，用于判断真正的薄弱区。",
        default_limit=6,
        keywords=["知识点", "掌握度", "记忆", "理解", "应用", "到期", "复习"],
        tool_type="read",
        risk_level="low",
    ),
    AgentToolDefinition(
        name="get_study_history",
        description="提取上传历史、连续学习天数和科目覆盖，补足长期学习轨迹。",
        default_limit=6,
        keywords=["上传", "连续", "历史", "时间线", "打卡", "学习天数"],
        tool_type="read",
        risk_level="low",
    ),
    AgentToolDefinition(
        name="get_review_pressure",
        description="提取错题积压、清仓速度和复习压力指标，用于安排每日复习量。",
        default_limit=1,
        keywords=["积压", "压力", "清仓", "负担", "预测", "风险", "待复习"],
        tool_type="read",
        risk_level="low",
    ),
]

READ_TOOL_DEFINITIONS.append(
    AgentToolDefinition(
        name="search_openviking_context",
        description="Search OpenViking for external documents, knowledge base entries, and long-term context.",
        default_limit=5,
        keywords=["OpenViking", "openviking", "资料库", "知识库", "文档", "外部资料", "长期记忆", "上下文库"],
        tool_type="read",
        risk_level="low",
    )
)

READ_TOOL_DEFINITIONS.append(
    AgentToolDefinition(
        name="consult_openmanus",
        description="Delegate a complex question to the locally deployed OpenManus sub-agent and retrieve its final answer.",
        default_limit=1,
        keywords=["OpenManus", "openmanus", "子代理", "外部代理", "复杂任务", "多步代理"],
        tool_type="read",
        risk_level="medium",
    )
)

TOOL_DEFINITIONS = READ_TOOL_DEFINITIONS

_TOOL_KEYWORD_MAP = {item.name: item.keywords for item in READ_TOOL_DEFINITIONS}
_TOOL_BUNDLES = [
    (
        ["进度", "掌握", "趋势", "统计", "伪掌握", "整体", "掌握度"],
        ["get_progress_summary", "get_knowledge_mastery"],
    ),
    (
        ["错题", "薄弱", "弱点", "重做", "复习", "易错", "高风险"],
        ["get_wrong_answers", "get_review_pressure"],
    ),
    (
        ["最近", "会话", "学习记录", "轨迹", "历史", "做题"],
        ["get_learning_sessions", "get_study_history"],
    ),
    (
        ["上传", "连续", "时间线", "打卡"],
        ["get_study_history"],
    ),
    (
        ["压力", "积压", "清仓", "负担", "到期", "预测", "未来", "接下来"],
        ["get_review_pressure", "get_progress_summary", "get_learning_sessions"],
    ),
]
_PLANNING_KEYWORDS = ["计划", "安排", "优先级", "今天", "今晚", "明天", "拆解", "任务", "怎么复习", "怎么学"]


def list_available_agent_tools() -> List[AgentToolDefinition]:
    return READ_TOOL_DEFINITIONS + list_action_tool_definitions()


def resolve_requested_tools(message: str, requested_tools: List[str] | None) -> List[str]:
    requested = [tool for tool in (requested_tools or []) if tool]
    allowed = {item.name for item in READ_TOOL_DEFINITIONS}
    openviking_enabled = is_openviking_enabled()

    if requested:
        invalid = [tool for tool in requested if tool not in allowed]
        if invalid:
            raise ValueError(f"不支持的工具: {', '.join(invalid)}")
        return list(dict.fromkeys(requested))

    matched: List[str] = []
    lowered = message.lower()
    for keywords, bundle in _TOOL_BUNDLES:
        if any(keyword.lower() in lowered for keyword in keywords):
            matched.extend(bundle)

    for tool_name, keywords in _TOOL_KEYWORD_MAP.items():
        if tool_name == "search_openviking_context" and not openviking_enabled:
            continue
        if any(keyword.lower() in lowered for keyword in keywords):
            matched.append(tool_name)

    if any(keyword.lower() in lowered for keyword in _PLANNING_KEYWORDS):
        matched.extend(
            [
                "get_progress_summary",
                "get_knowledge_mastery",
                "get_wrong_answers",
                "get_review_pressure",
                "get_learning_sessions",
            ]
        )

    if not matched:
        matched.extend(["get_progress_summary", "get_knowledge_mastery", "get_review_pressure"])

    return list(dict.fromkeys(matched))


def _chapter_label(chapter: Chapter | None) -> str | None:
    if chapter is None:
        return None
    parts = [chapter.book, chapter.chapter_number, chapter.chapter_title]
    return " / ".join([part for part in parts if part])


def _apply_actor_scope(query, model, *, user_id: str | None = None, device_id: str | None = None):
    user_id, device_id = resolve_query_identity(user_id, device_id)
    device_ids = build_device_scope_aliases(user_id, device_id)
    if user_id and hasattr(model, "user_id"):
        query = query.filter(model.user_id == user_id)
    if device_ids and hasattr(model, "device_id"):
        if len(device_ids) == 1:
            query = query.filter(model.device_id == device_ids[0])
        else:
            query = query.filter(model.device_id.in_(device_ids))
    elif device_id and hasattr(model, "device_id"):
        query = query.filter(model.device_id == device_id)
    return query


def _latest_retry_map(
    db: Session,
    wrong_answer_ids: List[int],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Dict[int, WrongAnswerRetry]:
    if not wrong_answer_ids:
        return {}

    retries = (
        _apply_actor_scope(
            db.query(WrongAnswerRetry),
            WrongAnswerRetry,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(WrongAnswerRetry.wrong_answer_id.in_(wrong_answer_ids))
        .order_by(desc(WrongAnswerRetry.retried_at), desc(WrongAnswerRetry.id))
        .all()
    )

    latest: Dict[int, WrongAnswerRetry] = {}
    for retry in retries:
        latest.setdefault(int(retry.wrong_answer_id), retry)
    return latest


async def _run_wrong_answers(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = WrongAnswersArgs.model_validate(overrides or {})
    query = _apply_actor_scope(
        db.query(WrongAnswerV2),
        WrongAnswerV2,
        user_id=user_id,
        device_id=device_id,
    ).order_by(
        desc(WrongAnswerV2.updated_at),
        desc(WrongAnswerV2.last_wrong_at),
        desc(WrongAnswerV2.id),
    )
    if args.status != "all":
        query = query.filter(WrongAnswerV2.mastery_status == args.status)

    items = query.limit(args.limit).all()
    chapter_ids = [item.chapter_id for item in items if item.chapter_id]
    chapters = db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all() if chapter_ids else []
    chapter_map = {chapter.id: chapter for chapter in chapters}
    retry_map = _latest_retry_map(
        db,
        [int(item.id) for item in items],
        user_id=user_id,
        device_id=device_id,
    )

    payload = {
        "status": args.status,
        "count": len(items),
        "items": [
            {
                "id": int(item.id),
                "chapter_id": item.chapter_id,
                "chapter_label": _chapter_label(chapter_map.get(item.chapter_id or "")),
                "key_point": item.key_point,
                "question_type": item.question_type,
                "difficulty": item.difficulty,
                "question_preview": (item.question_text or "")[:180],
                "error_count": int(item.error_count or 0),
                "encounter_count": int(item.encounter_count or 0),
                "severity_tag": item.severity_tag,
                "mastery_status": item.mastery_status,
                "next_review_date": item.next_review_date.isoformat() if item.next_review_date else None,
                "last_retry_correct": retry_map.get(int(item.id)).is_correct if retry_map.get(int(item.id)) else None,
                "last_wrong_at": item.last_wrong_at.isoformat() if item.last_wrong_at else None,
            }
            for item in items
        ],
    }
    return args.model_dump(mode="json"), payload


async def _run_learning_sessions(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = LearningSessionsArgs.model_validate(overrides or {})
    query = _apply_actor_scope(
        db.query(LearningSession),
        LearningSession,
        user_id=user_id,
        device_id=device_id,
    ).order_by(desc(LearningSession.started_at), desc(LearningSession.id))
    if args.session_type != "all":
        query = query.filter(LearningSession.session_type == args.session_type)

    sessions = query.limit(args.limit).all()
    payload = {
        "count": len(sessions),
        "items": [
            {
                "session_id": session.id,
                "title": session.title,
                "session_type": session.session_type,
                "status": session.status,
                "chapter_id": session.chapter_id,
                "knowledge_point": session.knowledge_point,
                "score": int(session.score or 0),
                "accuracy": round(float(session.accuracy or 0) * 100, 1) if float(session.accuracy or 0) <= 1 else round(float(session.accuracy or 0), 1),
                "total_questions": int(session.total_questions or 0),
                "correct_count": int(session.correct_count or 0),
                "wrong_count": int(session.wrong_count or 0),
                "duration_seconds": int(session.duration_seconds or 0),
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            }
            for session in sessions
        ],
    }
    return args.model_dump(mode="json"), payload


async def _run_progress_summary(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = ProgressSummaryArgs.model_validate(overrides or {})
    route_period = "all" if args.period == "all" else args.period.replace("d", "")
    board = await get_progress_board(
        period=route_period,
        date_str=None,
        user_id=user_id,
        device_id=device_id,
        db=db,
    )
    trend_key = "daily_trend_7" if args.period == "7d" else "daily_trend_30"

    payload = {
        "period": args.period,
        "generated_at": datetime.now().isoformat(),
        "overview": board.get("overview", {}),
        "confidence_distribution": board.get("confidence_distribution", []),
        "session_type_distribution": board.get("session_type_distribution", []),
        "daily_trend": board.get(trend_key, [])[:14],
        "weak_points": board.get("weak_points", [])[:6],
        "recent_sessions": board.get("recent_sessions", [])[:5],
        "weakest_area": board.get("weakest_area"),
        "wow_delta": board.get("wow_delta"),
    }
    return args.model_dump(mode="json"), payload


def _mastery_score(item: ConceptMastery) -> float:
    values = [float(item.retention or 0), float(item.understanding or 0), float(item.application or 0)]
    return sum(values) / len(values)


def _is_measured_concept(item: ConceptMastery) -> bool:
    if item.last_tested or item.next_review:
        return True
    return any(float(value or 0) > 0 for value in (item.retention, item.understanding, item.application))


def _is_placeholder_chapter_id(chapter_id: str | None) -> bool:
    normalized = str(chapter_id or "").strip()
    return not normalized or normalized in INVALID_CHAPTER_IDS


def _compute_streak_days(study_dates: List[date]) -> int:
    normalized_dates = {item for item in study_dates if item}
    streak = 0
    cursor = date.today()
    while cursor in normalized_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def _run_knowledge_mastery(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = KnowledgeMasteryArgs.model_validate(overrides or {})
    concepts = _apply_actor_scope(
        db.query(ConceptMastery),
        ConceptMastery,
        user_id=user_id,
        device_id=device_id,
    ).all()
    usable_concepts = [item for item in concepts if not _is_placeholder_chapter_id(item.chapter_id)]
    reported_concepts = usable_concepts or concepts
    today = date.today()
    due_cutoff = today + timedelta(days=args.due_days)
    measured_concepts = [item for item in reported_concepts if _is_measured_concept(item)]
    focus_concepts = measured_concepts or reported_concepts

    chapter_ids = [item.chapter_id for item in reported_concepts if item.chapter_id]
    chapters = db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all() if chapter_ids else []
    chapter_map = {chapter.id: chapter for chapter in chapters}

    weak_candidates = sorted(
        focus_concepts,
        key=lambda item: (
            _mastery_score(item),
            item.next_review or date.max,
            item.last_tested or date.min,
        ),
    )
    weak_concepts = []
    for item in weak_candidates[: args.limit]:
        mastery_score = round(_mastery_score(item) * 100, 1)
        weak_concepts.append(
            {
                "concept_id": item.concept_id,
                "name": item.name,
                "chapter_id": item.chapter_id,
                "chapter_label": _chapter_label(chapter_map.get(item.chapter_id or "")),
                "mastery_score": mastery_score,
                "retention": round(float(item.retention or 0) * 100, 1),
                "understanding": round(float(item.understanding or 0) * 100, 1),
                "application": round(float(item.application or 0) * 100, 1),
                "next_review": item.next_review.isoformat() if item.next_review else None,
            }
        )

    chapter_scores: Dict[str, Dict[str, Any]] = {}
    for item in focus_concepts:
        if _is_placeholder_chapter_id(item.chapter_id):
            continue
        chapter_id = item.chapter_id or ""
        bucket = chapter_scores.setdefault(
            chapter_id,
            {
                "chapter_id": chapter_id,
                "chapter_label": _chapter_label(chapter_map.get(chapter_id)),
                "count": 0,
                "score_sum": 0.0,
                "due_count": 0,
            },
        )
        bucket["count"] += 1
        bucket["score_sum"] += _mastery_score(item)
        if item.next_review and item.next_review <= due_cutoff:
            bucket["due_count"] += 1

    weak_chapters = sorted(
        [
            {
                "chapter_id": key,
                "chapter_label": value["chapter_label"] or "未标记章节",
                "concept_count": value["count"],
                "avg_mastery": round(value["score_sum"] / value["count"] * 100, 1) if value["count"] else 0.0,
                "due_count": value["due_count"],
            }
            for key, value in chapter_scores.items()
            if value["count"] > 0
        ],
        key=lambda item: (item["avg_mastery"], -item["due_count"], -item["concept_count"]),
    )[: max(3, min(args.limit, 6))]

    metric_source = focus_concepts
    total_concepts = len(reported_concepts)
    measured_count = len(measured_concepts)
    metric_count = len(metric_source)
    avg_retention = round(sum(float(item.retention or 0) for item in metric_source) / metric_count * 100, 1) if metric_count else 0.0
    avg_understanding = round(sum(float(item.understanding or 0) for item in metric_source) / metric_count * 100, 1) if metric_count else 0.0
    avg_application = round(sum(float(item.application or 0) for item in metric_source) / metric_count * 100, 1) if metric_count else 0.0
    avg_mastery = round((avg_retention + avg_understanding + avg_application) / 3, 1) if metric_count else 0.0
    due_today = sum(1 for item in metric_source if item.next_review and item.next_review <= today)
    due_in_window = sum(1 for item in metric_source if item.next_review and item.next_review <= due_cutoff)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "total_concepts": total_concepts,
        "measured_concepts": measured_count,
        "unmeasured_concepts": max(total_concepts - measured_count, 0),
        "avg_mastery": avg_mastery,
        "avg_retention": avg_retention,
        "avg_understanding": avg_understanding,
        "avg_application": avg_application,
        "due_today": due_today,
        "due_in_window": due_in_window,
        "window_days": args.due_days,
        "weak_concepts": weak_concepts,
        "weak_chapters": weak_chapters,
    }
    return args.model_dump(mode="json"), payload


async def _run_study_history(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = StudyHistoryArgs.model_validate(overrides or {})
    cutoff = date.today() - timedelta(days=args.days - 1)
    uploads = (
        _apply_actor_scope(
            db.query(DailyUpload),
            DailyUpload,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(DailyUpload.date >= cutoff)
        .order_by(desc(DailyUpload.date), desc(DailyUpload.id))
        .all()
    )
    all_upload_dates = [
        row[0]
        for row in _apply_actor_scope(
            db.query(DailyUpload.date),
            DailyUpload,
            user_id=user_id,
            device_id=device_id,
        ).distinct().all()
    ]
    weekly_cutoff = date.today() - timedelta(days=6)
    weekly_uploads = _apply_actor_scope(
        db.query(DailyUpload),
        DailyUpload,
        user_id=user_id,
        device_id=device_id,
    ).filter(DailyUpload.date >= weekly_cutoff).count()

    book_distribution: Dict[str, int] = {}
    for upload in uploads:
        extracted = upload.ai_extracted or {}
        book = str(extracted.get("book") or "未知")
        book_distribution[book] = book_distribution.get(book, 0) + 1
    book_distribution = dict(
        sorted(book_distribution.items(), key=lambda item: (-item[1], item[0]))[:6]
    )

    recent_uploads = []
    for upload in uploads[: args.limit]:
        ai_data = upload.ai_extracted or {}
        recent_uploads.append(
            {
                "id": int(upload.id),
                "date": upload.date.isoformat() if upload.date else None,
                "book": ai_data.get("book") or "未知",
                "chapter_title": ai_data.get("chapter_title") or "未识别章节",
                "chapter_id": ai_data.get("chapter_id"),
                "main_topic": ai_data.get("main_topic"),
                "summary": (ai_data.get("summary") or "")[:160],
            }
        )

    payload = {
        "days": args.days,
        "generated_at": datetime.now().isoformat(),
        "total_uploads_in_window": len(uploads),
        "weekly_uploads": weekly_uploads,
        "streak_days": _compute_streak_days(all_upload_dates),
        "book_distribution": book_distribution,
        "recent_uploads": recent_uploads,
    }
    return args.model_dump(mode="json"), payload


async def _run_review_pressure(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = ReviewPressureArgs.model_validate(overrides or {})
    dashboard = await get_dashboard_stats(
        daily_planned_review=args.daily_planned_review,
        user_id=user_id,
        device_id=device_id,
        db=db,
    )
    today = date.today()
    due_wrong_answers = _apply_actor_scope(
        db.query(WrongAnswerV2),
        WrongAnswerV2,
        user_id=user_id,
        device_id=device_id,
    ).filter(
        WrongAnswerV2.mastery_status == "active",
        WrongAnswerV2.next_review_date.isnot(None),
        WrongAnswerV2.next_review_date <= today,
    ).count()
    recent_tests = (
        _apply_actor_scope(
            db.query(TestRecord),
            TestRecord,
            user_id=user_id,
            device_id=device_id,
        )
        .order_by(desc(TestRecord.tested_at), desc(TestRecord.id))
        .limit(20)
        .all()
    )
    recent_test_accuracy = round(
        sum(1 for item in recent_tests if item.is_correct) / len(recent_tests) * 100,
        1,
    ) if recent_tests else None
    estimate = dashboard.get("estimated_days_to_clear")
    if isinstance(estimate, (int, float)) and not math.isfinite(float(estimate)):
        estimate = None

    payload = {
        "generated_at": datetime.now().isoformat(),
        "daily_planned_review": int(args.daily_planned_review),
        "current_backlog": int(dashboard.get("current_backlog") or 0),
        "avg_new_per_day": float(dashboard.get("avg_new_per_day") or 0),
        "estimated_days_to_clear": estimate,
        "daily_required_reviews": int(dashboard.get("daily_required_reviews") or 0),
        "can_clear": bool(dashboard.get("can_clear")),
        "clear_message": dashboard.get("clear_message"),
        "severity_counts": dashboard.get("severity_counts") or {},
        "weekly_trend": (dashboard.get("weekly_trend") or [])[:7],
        "net_daily_progress": float(dashboard.get("net_daily_progress") or 0),
        "due_wrong_answers": int(due_wrong_answers or 0),
        "recent_test_accuracy": recent_test_accuracy,
    }
    return args.model_dump(mode="json"), payload


async def _run_openviking_search(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    del db, user_id, device_id
    args = OpenVikingSearchArgs.model_validate(overrides or {})
    payload = await search_openviking_context(
        query=args.query,
        target_uri=args.target_uri,
        limit=args.limit,
    )
    return args.model_dump(mode="json"), payload


async def _run_openmanus_consult(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    del db, user_id, device_id
    args = OpenManusConsultArgs.model_validate(overrides or {})
    result = run_openmanus_consult(
        args.query,
        max_steps=args.max_steps,
    )
    payload = {
        "status": str(result.get("status") or "completed"),
        "query": args.query,
        "answer": str(result.get("answer") or "").strip(),
        "tool_names": list(result.get("tool_names") or []),
        "steps_executed": int(result.get("steps_executed") or 0),
        "message_count": int(result.get("message_count") or 0),
        "assistant_message_count": int(result.get("assistant_message_count") or 0),
        "run_result": str(result.get("run_result") or "").strip(),
        "count": int(result.get("count") or (1 if str(result.get("answer") or "").strip() else 0)),
    }
    return args.model_dump(mode="json"), payload


async def execute_agent_tool(
    tool_name: str,
    db: Session,
    overrides: Dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    started = perf_counter()
    overrides = overrides or {}
    if tool_name not in {"search_openviking_context", "consult_openmanus"}:
        ensure_learning_identity_schema()

    if tool_name == "get_wrong_answers":
        tool_args, result = await _run_wrong_answers(db, overrides, user_id=user_id, device_id=device_id)
    elif tool_name == "get_learning_sessions":
        tool_args, result = await _run_learning_sessions(db, overrides, user_id=user_id, device_id=device_id)
    elif tool_name == "get_progress_summary":
        tool_args, result = await _run_progress_summary(db, overrides, user_id=user_id, device_id=device_id)
    elif tool_name == "get_knowledge_mastery":
        tool_args, result = await _run_knowledge_mastery(db, overrides, user_id=user_id, device_id=device_id)
    elif tool_name == "get_study_history":
        tool_args, result = await _run_study_history(db, overrides, user_id=user_id, device_id=device_id)
    elif tool_name == "get_review_pressure":
        tool_args, result = await _run_review_pressure(db, overrides, user_id=user_id, device_id=device_id)
    elif tool_name == "search_openviking_context":
        tool_args, result = await _run_openviking_search(db, overrides, user_id=user_id, device_id=device_id)
    elif tool_name == "consult_openmanus":
        tool_args, result = await _run_openmanus_consult(db, overrides, user_id=user_id, device_id=device_id)
    else:
        raise ValueError(f"不支持的工具: {tool_name}")

    duration_ms = int((perf_counter() - started) * 1000)
    return tool_args, result, duration_ms
