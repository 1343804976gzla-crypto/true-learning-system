from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, desc, false, func, or_
from sqlalchemy.orm import Session

from learning_tracking_models import INVALID_CHAPTER_IDS, LearningSession, WrongAnswerRetry, WrongAnswerV2
from models import Chapter, ConceptMastery, DailyUpload, TestRecord
from routers.dashboard import get_dashboard_stats
from routers.learning_tracking import get_progress_board
from services.agent_actions import list_action_tool_definitions
from services.data_identity import (
    build_device_scope_aliases,
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
    query: str = Field(default="", max_length=120)
    chapter_ids: List[str] = Field(default_factory=list)


class LearningSessionsArgs(_ToolArgsModel):
    limit: int = Field(default=5, ge=1, le=10)
    session_type: Literal["exam", "detail_practice", "all"] = "all"
    query: str = Field(default="", max_length=120)
    chapter_ids: List[str] = Field(default_factory=list)


class ProgressSummaryArgs(_ToolArgsModel):
    period: Literal["all", "30d", "7d"] = "all"


class KnowledgeMasteryArgs(_ToolArgsModel):
    limit: int = Field(default=6, ge=3, le=12)
    due_days: int = Field(default=7, ge=0, le=30)
    chapter_ids: List[str] = Field(default_factory=list)


class StudyHistoryArgs(_ToolArgsModel):
    days: int = Field(default=30, ge=7, le=180)
    limit: int = Field(default=6, ge=1, le=12)
    query: str = Field(default="", max_length=120)
    chapter_ids: List[str] = Field(default_factory=list)


class ReviewPressureArgs(_ToolArgsModel):
    daily_planned_review: int = Field(default=20, ge=5, le=200)


class OpenVikingSearchArgs(_ToolArgsModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=8)
    target_uri: str = Field(default="", max_length=500)


class OpenManusConsultArgs(_ToolArgsModel):
    query: str = Field(min_length=1, max_length=2000)
    max_steps: int = Field(default=4, ge=1, le=8)


READ_CONTRACT_VERSION = "db-read.v1"
RUNTIME_OVERRIDE_KEY = "__runtime"
READ_FILTER_KEYS = (
    "status",
    "query",
    "chapter_ids",
    "period",
    "days",
    "session_type",
    "due_days",
    "daily_planned_review",
    "target_uri",
    "max_steps",
)


def _split_runtime_overrides(overrides: Dict[str, Any] | None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    payload = dict(overrides or {})
    runtime_context = payload.pop(RUNTIME_OVERRIDE_KEY, {})
    if not isinstance(runtime_context, dict):
        runtime_context = {}
    return payload, runtime_context


def _clean_read_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_clean_read_value(item) for item in value[:8] if item not in (None, "", [], {})]
    if isinstance(value, dict):
        return {
            str(key): _clean_read_value(item)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, str):
        return " ".join(value.split())[:160]
    return value


def _runtime_focus_briefs(runtime_context: Dict[str, Any]) -> List[Dict[str, str]]:
    briefs: List[Dict[str, str]] = []
    for item in list(runtime_context.get("focuses") or [])[:4]:
        if not isinstance(item, dict):
            continue
        focus_id = " ".join(str(item.get("id") or "").split())
        title = " ".join(str(item.get("title") or "").split())
        if not focus_id and not title:
            continue
        briefs.append(
            {
                "id": focus_id,
                "title": title or focus_id,
            }
        )
    return briefs


def _extract_read_filters(tool_args: Dict[str, Any]) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    for key in READ_FILTER_KEYS:
        value = tool_args.get(key)
        if value in (None, "", [], {}):
            continue
        filters[key] = _clean_read_value(value)
    return filters


def _build_read_contract(
    tool_name: str,
    tool_args: Dict[str, Any],
    runtime_context: Dict[str, Any],
    *,
    read_mode: str,
    source_tables: List[str],
    selected_fields: List[str],
    sort: List[str],
) -> Dict[str, Any]:
    return {
        "version": READ_CONTRACT_VERSION,
        "tool_name": tool_name,
        "read_mode": read_mode,
        "read_full_database": False,
        "scope": "actor_scoped",
        "filters": _extract_read_filters(tool_args),
        "limit": int(tool_args["limit"]) if "limit" in tool_args and str(tool_args.get("limit") or "").strip() else None,
        "source_tables": list(source_tables),
        "selected_fields": list(selected_fields),
        "sort": list(sort),
        "intent": {
            "goal": " ".join(str(runtime_context.get("goal") or "").split()),
            "output_mode": " ".join(str(runtime_context.get("output_mode") or "").split()),
            "time_horizon": " ".join(str(runtime_context.get("time_horizon") or "").split()),
            "message_excerpt": " ".join(str(runtime_context.get("message_excerpt") or "").split()),
            "focuses": _runtime_focus_briefs(runtime_context),
            "reason": " ".join(str(runtime_context.get("reason") or "").split()),
        },
    }


def _attach_standard_read_format(
    tool_name: str,
    tool_args: Dict[str, Any],
    payload: Dict[str, Any],
    runtime_context: Dict[str, Any],
    *,
    read_mode: str,
    source_tables: List[str],
    selected_fields: List[str],
    sort: List[str],
    total_count: int | None = None,
    returned_count: int | None = None,
) -> Dict[str, Any]:
    normalized = dict(payload or {})
    if returned_count is None:
        for key in ("returned_count", "count"):
            value = normalized.get(key)
            if isinstance(value, (int, float)):
                returned_count = int(value)
                break
        else:
            for key in ("items", "recent_uploads", "weak_concepts", "recent_sessions", "daily_trend", "weekly_trend"):
                value = normalized.get(key)
                if isinstance(value, list):
                    returned_count = len(value)
                    break
    if total_count is None:
        for key in ("count", "total_concepts", "total_uploads_in_window", "current_backlog"):
            value = normalized.get(key)
            if isinstance(value, (int, float)):
                total_count = int(value)
                break
    resolved_total = max(int(total_count or 0), 0)
    resolved_returned = max(int(returned_count or 0), 0)
    sampled = bool(normalized.get("sampled")) or resolved_total > resolved_returned

    normalized["tool_name"] = tool_name
    normalized["read_contract"] = _build_read_contract(
        tool_name,
        tool_args,
        runtime_context,
        read_mode=read_mode,
        source_tables=source_tables,
        selected_fields=selected_fields,
        sort=sort,
    )
    normalized["result_stats"] = {
        "total_count": resolved_total,
        "returned_count": resolved_returned,
        "sampled": sampled,
        "has_more": resolved_total > resolved_returned,
    }
    return normalized


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
    runtime_context: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = WrongAnswersArgs.model_validate(overrides or {})
    query_text = _normalize_query_text(args.query)
    chapter_ids = _normalize_chapter_ids(args.chapter_ids)
    base_query = _apply_actor_scope(
        db.query(WrongAnswerV2),
        WrongAnswerV2,
        user_id=user_id,
        device_id=device_id,
    )
    if chapter_ids:
        base_query = base_query.filter(WrongAnswerV2.chapter_id.in_(chapter_ids))
    if query_text:
        like = f"%{query_text}%"
        chapter_match_ids = [
            chapter.id
            for chapter in db.query(Chapter.id)
            .filter(or_(Chapter.chapter_title.ilike(like), Chapter.book.ilike(like)))
            .all()
        ]
        base_query = base_query.filter(
            or_(
                WrongAnswerV2.key_point.ilike(like),
                WrongAnswerV2.question_text.ilike(like),
                WrongAnswerV2.chapter_id.in_(chapter_match_ids) if chapter_match_ids else false(),
            )
        )
    if args.status != "all":
        base_query = base_query.filter(WrongAnswerV2.mastery_status == args.status)

    query = base_query.order_by(
        desc(WrongAnswerV2.updated_at),
        desc(WrongAnswerV2.last_wrong_at),
        desc(WrongAnswerV2.id),
    )

    items = query.limit(args.limit).all()
    total_count = int(base_query.count())
    returned_count = len(items)
    chapter_ids = [item.chapter_id for item in items if item.chapter_id]
    chapters = db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all() if chapter_ids else []
    chapter_map = {chapter.id: chapter for chapter in chapters}
    retry_map = _latest_retry_map(
        db,
        [int(item.id) for item in items],
        user_id=user_id,
        device_id=device_id,
    )
    severity_rows = (
        base_query.with_entities(
            WrongAnswerV2.severity_tag,
            func.count(WrongAnswerV2.id).label("item_count"),
        )
        .group_by(WrongAnswerV2.severity_tag)
        .all()
    )
    severity_counts = {
        str(tag or "unknown"): int(item_count or 0)
        for tag, item_count in severity_rows
    }

    due_query = base_query
    if args.status == "all":
        due_query = due_query.filter(WrongAnswerV2.mastery_status == "active")
    due_count = int(
        due_query.filter(
            WrongAnswerV2.next_review_date.isnot(None),
            WrongAnswerV2.next_review_date <= date.today(),
        ).count()
    )

    key_point_count_expr = func.count(WrongAnswerV2.id)
    key_point_error_expr = func.sum(WrongAnswerV2.error_count)
    top_key_point_rows = (
        base_query.with_entities(
            WrongAnswerV2.key_point,
            key_point_count_expr.label("item_count"),
            key_point_error_expr.label("error_total"),
        )
        .filter(
            WrongAnswerV2.key_point.isnot(None),
            func.trim(WrongAnswerV2.key_point) != "",
        )
        .group_by(WrongAnswerV2.key_point)
        .order_by(desc(key_point_count_expr), desc(key_point_error_expr), WrongAnswerV2.key_point.asc())
        .limit(5)
        .all()
    )

    top_chapter_count_expr = func.count(WrongAnswerV2.id)
    top_chapter_error_expr = func.sum(WrongAnswerV2.error_count)
    top_chapter_rows = (
        base_query.with_entities(
            WrongAnswerV2.chapter_id,
            top_chapter_count_expr.label("item_count"),
            top_chapter_error_expr.label("error_total"),
        )
        .filter(
            WrongAnswerV2.chapter_id.isnot(None),
            func.trim(WrongAnswerV2.chapter_id) != "",
        )
        .group_by(WrongAnswerV2.chapter_id)
        .order_by(desc(top_chapter_count_expr), desc(top_chapter_error_expr), WrongAnswerV2.chapter_id.asc())
        .limit(3)
        .all()
    )
    top_chapter_ids = [str(chapter_id) for chapter_id, _, _ in top_chapter_rows if chapter_id]
    top_chapters = db.query(Chapter).filter(Chapter.id.in_(top_chapter_ids)).all() if top_chapter_ids else []
    top_chapter_map = {chapter.id: chapter for chapter in top_chapters}

    payload = {
        "status": args.status,
        "query": query_text,
        "chapter_ids": chapter_ids,
        "count": total_count,
        "returned_count": returned_count,
        "sampled": total_count > returned_count,
        "severity_counts": severity_counts,
        "due_count": due_count,
        "top_key_points": [
            {
                "name": str(key_point),
                "count": int(item_count or 0),
                "error_total": int(error_total or 0),
            }
            for key_point, item_count, error_total in top_key_point_rows
            if str(key_point or "").strip()
        ],
        "top_chapters": [
            {
                "chapter_id": chapter_id,
                "chapter_label": _chapter_label(top_chapter_map.get(chapter_id or "")) or (chapter_id or "未标记章节"),
                "count": int(item_count or 0),
                "error_total": int(error_total or 0),
            }
            for chapter_id, item_count, error_total in top_chapter_rows
        ],
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
    tool_args = args.model_dump(mode="json")
    return tool_args, _attach_standard_read_format(
        "get_wrong_answers",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="targeted_list",
        source_tables=["wrong_answers_v2", "wrong_answer_retries", "chapters"],
        selected_fields=[
            "id",
            "chapter_id",
            "key_point",
            "question_text",
            "error_count",
            "encounter_count",
            "severity_tag",
            "mastery_status",
            "next_review_date",
            "last_wrong_at",
        ],
        sort=["updated_at desc", "last_wrong_at desc", "id desc"],
        total_count=total_count,
        returned_count=returned_count,
    )


async def _run_learning_sessions(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    runtime_context: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = LearningSessionsArgs.model_validate(overrides or {})
    query_text = _normalize_query_text(args.query)
    chapter_ids = _normalize_chapter_ids(args.chapter_ids)
    query = _apply_actor_scope(
        db.query(LearningSession),
        LearningSession,
        user_id=user_id,
        device_id=device_id,
    ).order_by(desc(LearningSession.started_at), desc(LearningSession.id))
    if args.session_type != "all":
        query = query.filter(LearningSession.session_type == args.session_type)
    if chapter_ids:
        query = query.filter(LearningSession.chapter_id.in_(chapter_ids))
    if query_text:
        like = f"%{query_text}%"
        chapter_match_ids = [
            chapter.id
            for chapter in db.query(Chapter.id)
            .filter(or_(Chapter.chapter_title.ilike(like), Chapter.book.ilike(like)))
            .all()
        ]
        query = query.filter(
            or_(
                LearningSession.title.ilike(like),
                LearningSession.knowledge_point.ilike(like),
                LearningSession.uploaded_content.ilike(like),
                LearningSession.chapter_id.in_(chapter_match_ids) if chapter_match_ids else false(),
            )
        )

    sessions = query.limit(args.limit).all()
    payload = {
        "query": query_text,
        "chapter_ids": chapter_ids,
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
    tool_args = args.model_dump(mode="json")
    return tool_args, _attach_standard_read_format(
        "get_learning_sessions",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="targeted_list",
        source_tables=["learning_sessions"],
        selected_fields=[
            "id",
            "title",
            "session_type",
            "status",
            "chapter_id",
            "knowledge_point",
            "score",
            "accuracy",
            "started_at",
        ],
        sort=["started_at desc", "id desc"],
        total_count=len(sessions),
        returned_count=len(sessions),
    )


async def _run_progress_summary(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    runtime_context: Dict[str, Any] | None = None,
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
    tool_args = args.model_dump(mode="json")
    overview = payload.get("overview") or {}
    return tool_args, _attach_standard_read_format(
        "get_progress_summary",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="aggregate_summary",
        source_tables=["learning_sessions", "question_records", "concept_mastery"],
        selected_fields=[
            "total_questions",
            "total_sessions",
            "accuracy",
            "daily_trend",
            "weak_points",
            "recent_sessions",
        ],
        sort=["period scoped aggregate", "recent_sessions by started_at desc"],
        total_count=int(overview.get("total_questions") or 0),
        returned_count=len(payload.get("recent_sessions") or []),
    )


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


def _normalize_query_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_chapter_ids(values: List[str] | None, *, limit: int = 8) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for raw in values or []:
        chapter_id = str(raw or "").strip()
        if not chapter_id or chapter_id in seen:
            continue
        seen.add(chapter_id)
        normalized.append(chapter_id)
        if len(normalized) >= limit:
            break
    return normalized


def _record_matches_query(record: Dict[str, Any], query_text: str) -> bool:
    if not query_text:
        return True
    return any(
        query_text in str(record.get(field) or "")
        for field in ("book", "chapter_title", "chapter_id", "main_topic", "summary")
    )


def _study_session_date(session: LearningSession) -> date | None:
    if session.started_at:
        return session.started_at.date()
    if session.created_at:
        return session.created_at.date()
    return None


def _stable_synthetic_upload_id(source_id: str) -> int:
    return -int(hashlib.sha1(source_id.encode("utf-8")).hexdigest()[:12], 16)


def _merge_study_history_records(
    uploads: List[DailyUpload],
    session_uploads: List[LearningSession],
    chapter_map: Dict[str, Chapter],
) -> Tuple[List[Dict[str, Any]], int]:
    explicit_records: List[Dict[str, Any]] = []
    explicit_dates: set[date] = set()
    for upload in uploads:
        if upload.date:
            explicit_dates.add(upload.date)
        ai_data = upload.ai_extracted or {}
        explicit_records.append(
            {
                "id": int(upload.id),
                "date": upload.date,
                "book": ai_data.get("book") or "未知",
                "chapter_title": ai_data.get("chapter_title") or "未识别章节",
                "chapter_id": ai_data.get("chapter_id") or "",
                "concept_count": len(ai_data.get("concepts") or []),
                "summary": (ai_data.get("summary") or "")[:160],
                "main_topic": ai_data.get("main_topic") or "",
                "sort_datetime": upload.created_at or datetime.combine(upload.date, datetime.min.time()),
                "source": "daily_upload",
            }
        )

    fallback_records: Dict[Tuple[date, str], Dict[str, Any]] = {}
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
            "book": getattr(chapter, "book", None) or "未识别",
            "chapter_title": getattr(chapter, "chapter_title", None) or (session.title or "未识别章节"),
            "chapter_id": str(session.chapter_id or ""),
            "concept_count": len(getattr(chapter, "concepts", None) or []),
            "summary": raw_content[:160],
            "main_topic": session.knowledge_point or "",
            "sort_datetime": sort_datetime,
            "source": "learning_session",
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
    return combined, len(fallback_records)


async def _run_knowledge_mastery(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = KnowledgeMasteryArgs.model_validate(overrides or {})
    chapter_ids = _normalize_chapter_ids(args.chapter_ids)
    concepts = _apply_actor_scope(
        db.query(ConceptMastery),
        ConceptMastery,
        user_id=user_id,
        device_id=device_id,
    ).all()
    if chapter_ids:
        concepts = [item for item in concepts if str(item.chapter_id or "") in chapter_ids]
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
        "chapter_ids": chapter_ids,
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
    query_text = _normalize_query_text(args.query)
    requested_chapter_ids = set(_normalize_chapter_ids(args.chapter_ids))
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
    all_upload_dates = {
        row[0]
        for row in _apply_actor_scope(
            db.query(DailyUpload.date),
            DailyUpload,
            user_id=user_id,
            device_id=device_id,
        ).distinct().all()
        if row[0]
    }
    session_uploads = (
        _apply_actor_scope(
            db.query(LearningSession),
            LearningSession,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(
            LearningSession.uploaded_content.isnot(None),
            func.trim(LearningSession.uploaded_content) != "",
        )
        .order_by(
            desc(LearningSession.started_at),
            desc(LearningSession.created_at),
            desc(LearningSession.id),
        )
        .all()
    )
    session_upload_dates = {
        study_date
        for session in session_uploads
        if (study_date := _study_session_date(session)) is not None
    }
    window_session_uploads = [
        session
        for session in session_uploads
        if (study_date := _study_session_date(session)) is not None and study_date >= cutoff
    ]
    session_chapter_ids = list(
        {
            str(session.chapter_id).strip()
            for session in window_session_uploads
            if str(session.chapter_id or "").strip()
        }
    )
    chapters = db.query(Chapter).filter(Chapter.id.in_(session_chapter_ids)).all() if session_chapter_ids else []
    chapter_map = {chapter.id: chapter for chapter in chapters}
    merged_records, _session_fallback_count = _merge_study_history_records(
        uploads,
        window_session_uploads,
        chapter_map,
    )
    if requested_chapter_ids:
        merged_records = [
            record for record in merged_records if str(record.get("chapter_id") or "") in requested_chapter_ids
        ]
    if query_text:
        merged_records = [
            record for record in merged_records if _record_matches_query(record, query_text)
        ]
    weekly_cutoff = date.today() - timedelta(days=6)
    weekly_uploads = sum(
        1 for record in merged_records if record.get("date") and record["date"] >= weekly_cutoff
    )

    book_distribution: Dict[str, int] = {}
    for record in merged_records:
        book = str(record.get("book") or "未知")
        book_distribution[book] = book_distribution.get(book, 0) + 1
    book_distribution = dict(
        sorted(book_distribution.items(), key=lambda item: (-item[1], item[0]))[:6]
    )

    recent_uploads = []
    for record in merged_records[: args.limit]:
        recent_uploads.append(
            {
                "id": int(record["id"]),
                "date": record["date"].isoformat() if record.get("date") else None,
                "book": record.get("book") or "未知",
                "chapter_title": record.get("chapter_title") or "未识别章节",
                "chapter_id": record.get("chapter_id"),
                "main_topic": record.get("main_topic"),
                "summary": (record.get("summary") or "")[:160],
                "source": record.get("source"),
            }
        )

    all_study_dates = sorted(all_upload_dates | session_upload_dates)
    filtered_study_dates = sorted(
        {
            record["date"]
            for record in merged_records
            if record.get("date")
        }
    )
    payload = {
        "days": args.days,
        "generated_at": datetime.now().isoformat(),
        "query": query_text,
        "chapter_ids": list(requested_chapter_ids),
        "total_uploads_in_window": len(merged_records),
        "weekly_uploads": weekly_uploads,
        "streak_days": _compute_streak_days(filtered_study_dates if (query_text or requested_chapter_ids) else all_study_dates),
        "latest_study_date": (
            (filtered_study_dates[-1] if filtered_study_dates else None) if (query_text or requested_chapter_ids)
            else (all_study_dates[-1] if all_study_dates else None)
        ).isoformat() if ((filtered_study_dates if (query_text or requested_chapter_ids) else all_study_dates)) else None,
        "daily_upload_count_in_window": sum(1 for record in merged_records if record.get("source") == "daily_upload"),
        "session_fallback_count_in_window": sum(1 for record in merged_records if record.get("source") == "learning_session"),
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
    runtime_context: Dict[str, Any] | None = None,
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
    tool_args = args.model_dump(mode="json")
    return tool_args, _attach_standard_read_format(
        "get_review_pressure",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="aggregate_summary",
        source_tables=["wrong_answers_v2", "test_records"],
        selected_fields=[
            "current_backlog",
            "avg_new_per_day",
            "estimated_days_to_clear",
            "daily_required_reviews",
            "due_wrong_answers",
            "recent_test_accuracy",
        ],
        sort=["aggregate metrics", "recent_test_records by tested_at desc"],
        total_count=int(payload.get("current_backlog") or 0),
        returned_count=len(payload.get("weekly_trend") or []),
    )


async def _run_openviking_search(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    runtime_context: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    del db, user_id, device_id
    args = OpenVikingSearchArgs.model_validate(overrides or {})
    payload = await search_openviking_context(
        query=args.query,
        target_uri=args.target_uri,
        limit=args.limit,
    )
    tool_args = args.model_dump(mode="json")
    return tool_args, _attach_standard_read_format(
        "search_openviking_context",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="external_reference",
        source_tables=["openviking.resources", "openviking.memories", "openviking.skills"],
        selected_fields=["query", "resources", "memories", "skills", "count"],
        sort=["provider managed relevance"],
        total_count=int(payload.get("count") or 0),
        returned_count=int(payload.get("count") or 0),
    )


async def _run_openmanus_consult(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    runtime_context: Dict[str, Any] | None = None,
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
    tool_args = args.model_dump(mode="json")
    return tool_args, _attach_standard_read_format(
        "consult_openmanus",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="delegated_reference",
        source_tables=["openmanus.run_result"],
        selected_fields=["query", "answer", "tool_names", "steps_executed", "message_count"],
        sort=["delegated agent final answer"],
        total_count=int(payload.get("count") or 0),
        returned_count=int(payload.get("count") or 0),
    )


async def _run_knowledge_mastery(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    runtime_context: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = KnowledgeMasteryArgs.model_validate(overrides or {})
    requested_chapter_ids = _normalize_chapter_ids(args.chapter_ids)
    invalid_chapter_ids = [value for value in INVALID_CHAPTER_IDS if str(value or "").strip()]
    today = date.today()
    due_cutoff = today + timedelta(days=args.due_days)
    score_expr = (
        func.coalesce(ConceptMastery.retention, 0.0)
        + func.coalesce(ConceptMastery.understanding, 0.0)
        + func.coalesce(ConceptMastery.application, 0.0)
    ) / 3.0
    measured_filter = or_(
        ConceptMastery.last_tested.isnot(None),
        ConceptMastery.next_review.isnot(None),
        func.coalesce(ConceptMastery.retention, 0.0) > 0,
        func.coalesce(ConceptMastery.understanding, 0.0) > 0,
        func.coalesce(ConceptMastery.application, 0.0) > 0,
    )

    base_query = _apply_actor_scope(
        db.query(ConceptMastery),
        ConceptMastery,
        user_id=user_id,
        device_id=device_id,
    )
    if requested_chapter_ids:
        base_query = base_query.filter(ConceptMastery.chapter_id.in_(requested_chapter_ids))

    usable_query = base_query.filter(
        ConceptMastery.chapter_id.isnot(None),
        func.trim(ConceptMastery.chapter_id) != "",
        ~ConceptMastery.chapter_id.in_(invalid_chapter_ids),
    )
    reported_query = usable_query if usable_query.limit(1).first() is not None else base_query
    measured_query = reported_query.filter(measured_filter)
    focus_query = measured_query if measured_query.limit(1).first() is not None else reported_query

    total_concepts = int(reported_query.count())
    measured_count = int(measured_query.count())
    reported_chapter_ids = [
        str(row[0])
        for row in (
            reported_query.with_entities(ConceptMastery.chapter_id)
            .filter(ConceptMastery.chapter_id.isnot(None), func.trim(ConceptMastery.chapter_id) != "")
            .distinct()
            .limit(24)
            .all()
        )
        if str(row[0] or "").strip()
    ]

    weak_rows = (
        focus_query.order_by(
            score_expr.asc(),
            case((ConceptMastery.next_review.is_(None), 1), else_=0).asc(),
            ConceptMastery.next_review.asc(),
            case((ConceptMastery.last_tested.is_(None), 1), else_=0).asc(),
            ConceptMastery.last_tested.asc(),
            ConceptMastery.concept_id.asc(),
        )
        .limit(args.limit)
        .all()
    )

    due_count_expr = func.sum(
        case(
            (
                and_(
                    ConceptMastery.next_review.isnot(None),
                    ConceptMastery.next_review <= due_cutoff,
                ),
                1,
            ),
            else_=0,
        )
    )
    weak_chapter_rows = (
        focus_query.with_entities(
            ConceptMastery.chapter_id,
            func.count(ConceptMastery.concept_id).label("concept_count"),
            func.avg(score_expr).label("avg_mastery"),
            due_count_expr.label("due_count"),
        )
        .filter(
            ConceptMastery.chapter_id.isnot(None),
            func.trim(ConceptMastery.chapter_id) != "",
            ~ConceptMastery.chapter_id.in_(invalid_chapter_ids),
        )
        .group_by(ConceptMastery.chapter_id)
        .order_by(
            func.avg(score_expr).asc(),
            due_count_expr.desc(),
            func.count(ConceptMastery.concept_id).desc(),
            ConceptMastery.chapter_id.asc(),
        )
        .limit(max(3, min(args.limit, 6)))
        .all()
    )

    chapter_lookup_ids = {
        *reported_chapter_ids,
        *[str(item.chapter_id) for item in weak_rows if str(item.chapter_id or "").strip()],
        *[str(row[0]) for row in weak_chapter_rows if str(row[0] or "").strip()],
    }
    chapters = (
        db.query(Chapter).filter(Chapter.id.in_(list(chapter_lookup_ids))).all()
        if chapter_lookup_ids
        else []
    )
    chapter_map = {chapter.id: chapter for chapter in chapters}

    weak_concepts = [
        {
            "concept_id": item.concept_id,
            "name": item.name,
            "chapter_id": item.chapter_id,
            "chapter_label": _chapter_label(chapter_map.get(item.chapter_id or "")),
            "mastery_score": round(_mastery_score(item) * 100, 1),
            "retention": round(float(item.retention or 0) * 100, 1),
            "understanding": round(float(item.understanding or 0) * 100, 1),
            "application": round(float(item.application or 0) * 100, 1),
            "next_review": item.next_review.isoformat() if item.next_review else None,
        }
        for item in weak_rows
    ]
    weak_chapters = [
        {
            "chapter_id": str(chapter_id or ""),
            "chapter_label": _chapter_label(chapter_map.get(str(chapter_id or ""))) or "未标记章节",
            "concept_count": int(concept_count or 0),
            "avg_mastery": round(float(avg_mastery or 0.0) * 100, 1),
            "due_count": int(due_count or 0),
        }
        for chapter_id, concept_count, avg_mastery, due_count in weak_chapter_rows
        if str(chapter_id or "").strip()
    ]

    avg_row = focus_query.with_entities(
        func.avg(func.coalesce(ConceptMastery.retention, 0.0)),
        func.avg(func.coalesce(ConceptMastery.understanding, 0.0)),
        func.avg(func.coalesce(ConceptMastery.application, 0.0)),
    ).one()
    avg_retention = round(float(avg_row[0] or 0.0) * 100, 1)
    avg_understanding = round(float(avg_row[1] or 0.0) * 100, 1)
    avg_application = round(float(avg_row[2] or 0.0) * 100, 1)
    avg_mastery = round((avg_retention + avg_understanding + avg_application) / 3, 1) if total_concepts else 0.0
    due_today = int(
        focus_query.filter(
            ConceptMastery.next_review.isnot(None),
            ConceptMastery.next_review <= today,
        ).count()
    )
    due_in_window = int(
        focus_query.filter(
            ConceptMastery.next_review.isnot(None),
            ConceptMastery.next_review <= due_cutoff,
        ).count()
    )

    payload = {
        "generated_at": datetime.now().isoformat(),
        "chapter_ids": reported_chapter_ids,
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
    tool_args = args.model_dump(mode="json")
    return tool_args, _attach_standard_read_format(
        "get_knowledge_mastery",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="targeted_summary",
        source_tables=["concept_mastery", "chapters"],
        selected_fields=[
            "concept_id",
            "chapter_id",
            "name",
            "retention",
            "understanding",
            "application",
            "last_tested",
            "next_review",
        ],
        sort=["mastery_score asc", "next_review asc", "last_tested asc"],
        total_count=total_concepts,
        returned_count=len(weak_concepts),
    )


async def _run_study_history(
    db: Session,
    overrides: Dict[str, Any],
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    runtime_context: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args = StudyHistoryArgs.model_validate(overrides or {})
    query_text = _normalize_query_text(args.query)
    requested_chapter_ids = set(_normalize_chapter_ids(args.chapter_ids))
    cutoff = date.today() - timedelta(days=args.days - 1)
    cutoff_dt = datetime.combine(cutoff, datetime.min.time())

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
    all_upload_dates = {
        row[0]
        for row in _apply_actor_scope(
            db.query(DailyUpload.date),
            DailyUpload,
            user_id=user_id,
            device_id=device_id,
        ).distinct().all()
        if row[0]
    }

    session_date_rows = (
        _apply_actor_scope(
            db.query(func.date(func.coalesce(LearningSession.started_at, LearningSession.created_at))),
            LearningSession,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(
            LearningSession.uploaded_content.isnot(None),
            func.trim(LearningSession.uploaded_content) != "",
        )
        .distinct()
        .all()
    )
    session_upload_dates = {
        date.fromisoformat(str(row[0]))
        for row in session_date_rows
        if row[0]
    }

    window_session_uploads = (
        _apply_actor_scope(
            db.query(LearningSession),
            LearningSession,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(
            LearningSession.uploaded_content.isnot(None),
            func.trim(LearningSession.uploaded_content) != "",
            or_(
                LearningSession.started_at >= cutoff_dt,
                and_(
                    LearningSession.started_at.is_(None),
                    LearningSession.created_at >= cutoff_dt,
                ),
            ),
        )
        .order_by(
            desc(LearningSession.started_at),
            desc(LearningSession.created_at),
            desc(LearningSession.id),
        )
        .all()
    )
    session_chapter_ids = list(
        {
            str(session.chapter_id).strip()
            for session in window_session_uploads
            if str(session.chapter_id or "").strip()
        }
    )
    chapters = db.query(Chapter).filter(Chapter.id.in_(session_chapter_ids)).all() if session_chapter_ids else []
    chapter_map = {chapter.id: chapter for chapter in chapters}
    merged_records, _session_fallback_count = _merge_study_history_records(
        uploads,
        window_session_uploads,
        chapter_map,
    )
    if requested_chapter_ids:
        merged_records = [
            record for record in merged_records if str(record.get("chapter_id") or "") in requested_chapter_ids
        ]
    if query_text:
        merged_records = [
            record for record in merged_records if _record_matches_query(record, query_text)
        ]
    weekly_cutoff = date.today() - timedelta(days=6)
    weekly_uploads = sum(
        1 for record in merged_records if record.get("date") and record["date"] >= weekly_cutoff
    )

    book_distribution: Dict[str, int] = {}
    for record in merged_records:
        book = str(record.get("book") or "未知")
        book_distribution[book] = book_distribution.get(book, 0) + 1
    book_distribution = dict(
        sorted(book_distribution.items(), key=lambda item: (-item[1], item[0]))[:6]
    )

    recent_uploads = []
    for record in merged_records[: args.limit]:
        recent_uploads.append(
            {
                "id": int(record["id"]),
                "date": record["date"].isoformat() if record.get("date") else None,
                "book": record.get("book") or "未知",
                "chapter_title": record.get("chapter_title") or "未识别章节",
                "chapter_id": record.get("chapter_id"),
                "main_topic": record.get("main_topic"),
                "summary": (record.get("summary") or "")[:160],
                "source": record.get("source"),
            }
        )

    all_study_dates = sorted(all_upload_dates | session_upload_dates)
    filtered_study_dates = sorted(
        {
            record["date"]
            for record in merged_records
            if record.get("date")
        }
    )
    payload = {
        "days": args.days,
        "generated_at": datetime.now().isoformat(),
        "query": query_text,
        "chapter_ids": list(requested_chapter_ids),
        "total_uploads_in_window": len(merged_records),
        "weekly_uploads": weekly_uploads,
        "streak_days": _compute_streak_days(
            filtered_study_dates if (query_text or requested_chapter_ids) else all_study_dates
        ),
        "latest_study_date": (
            (filtered_study_dates[-1] if filtered_study_dates else None)
            if (query_text or requested_chapter_ids)
            else (all_study_dates[-1] if all_study_dates else None)
        ).isoformat() if ((filtered_study_dates if (query_text or requested_chapter_ids) else all_study_dates)) else None,
        "daily_upload_count_in_window": sum(1 for record in merged_records if record.get("source") == "daily_upload"),
        "session_fallback_count_in_window": sum(1 for record in merged_records if record.get("source") == "learning_session"),
        "book_distribution": book_distribution,
        "recent_uploads": recent_uploads,
    }
    tool_args = args.model_dump(mode="json")
    return tool_args, _attach_standard_read_format(
        "get_study_history",
        tool_args,
        payload,
        runtime_context or {},
        read_mode="windowed_history",
        source_tables=["daily_uploads", "learning_sessions", "chapters"],
        selected_fields=[
            "date",
            "book",
            "chapter_title",
            "chapter_id",
            "main_topic",
            "summary",
            "source",
        ],
        sort=["date desc", "created_at desc", "id desc"],
        total_count=len(merged_records),
        returned_count=len(recent_uploads),
    )


async def execute_agent_tool(
    tool_name: str,
    db: Session,
    overrides: Dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    started = perf_counter()
    overrides, runtime_context = _split_runtime_overrides(overrides)
    if tool_name == "get_wrong_answers":
        tool_args, result = await _run_wrong_answers(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    elif tool_name == "get_learning_sessions":
        tool_args, result = await _run_learning_sessions(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    elif tool_name == "get_progress_summary":
        tool_args, result = await _run_progress_summary(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    elif tool_name == "get_knowledge_mastery":
        tool_args, result = await _run_knowledge_mastery(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    elif tool_name == "get_study_history":
        tool_args, result = await _run_study_history(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    elif tool_name == "get_review_pressure":
        tool_args, result = await _run_review_pressure(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    elif tool_name == "search_openviking_context":
        tool_args, result = await _run_openviking_search(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    elif tool_name == "consult_openmanus":
        tool_args, result = await _run_openmanus_consult(
            db,
            overrides,
            user_id=user_id,
            device_id=device_id,
            runtime_context=runtime_context,
        )
    else:
        raise ValueError(f"不支持的工具: {tool_name}")

    duration_ms = int((perf_counter() - started) * 1000)
    return tool_args, result, duration_ms
