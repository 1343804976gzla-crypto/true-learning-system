from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
import os
import threading
from time import perf_counter, sleep
from typing import Any, AsyncIterator, Dict, List, Optional
import json
import re
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import desc, func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from agent_models import (
    AgentActionLog,
    AgentMemory,
    AgentMessage,
    AgentSession,
    AgentTask,
    AgentTaskEvent,
    AgentToolCall,
    AgentToolCache,
    AgentTurnState,
)
from learning_tracking_models import LearningSession, WrongAnswerV2
from models import Chapter, engine
from services.agent_actions import list_action_tool_definitions
from services.agent_context import build_agent_context, estimate_tokens, redact_sensitive_output
from services.agent_memory import (
    get_cached_tool_result,
    refresh_session_summary,
    store_long_term_memories,
    store_tool_cache_result,
)
from services.agent_prompt_templates import resolve_prompt_template
from services.agent_tools import execute_agent_tool, resolve_requested_tools
from services.ai_client import get_ai_client
from services.data_identity import (
    build_device_scope_aliases,
    canonicalize_storage_identity,
    resolve_query_identity,
)
from utils.agent_contracts import (
    AgentChatRequest,
    AgentChatResponse,
    AgentContextUsage,
    AgentMessageItem,
    AgentPlanBundle,
    AgentPlanSubtask,
    AgentPlanTask,
    AgentSessionCreateRequest,
    AgentSessionItem,
    AgentSourceCard,
    AgentSourceStat,
    AgentSummaryResponse,
    AgentTurnStateItem,
    AgentToolCallItem,
)

logger = logging.getLogger(__name__)

_AGENT_SCHEMA_READY = False
DEFAULT_AGENT_PROVIDER = "deepseek"
DEFAULT_AGENT_MODEL = "deepseek-chat"
DEFAULT_TUTOR_TEMPLATE = "tutor.v2"
AGENT_IDENTITY_REQUIRED = "agent_identity_required"
AGENT_SESSION_NOT_FOUND = "agent_session_not_found"
AGENT_DUPLICATE_REQUEST_IN_PROGRESS = "agent_duplicate_request_in_progress"
AGENT_DUPLICATE_WAIT_TIMEOUT_SECONDS = float(os.getenv("AGENT_DUPLICATE_WAIT_TIMEOUT_SECONDS") or 60.0)
_IN_FLIGHT_CHAT_REQUESTS: set[tuple[str, str]] = set()
_IN_FLIGHT_CHAT_REQUESTS_LOCK = threading.Lock()


@dataclass
class PreparedChatTurn:
    session: AgentSession
    user_message: AgentMessage
    tool_calls: List[AgentToolCall]
    selected_tools: List[str]
    tool_results: Dict[str, Any]
    request_analysis: Dict[str, Any]
    response_strategy: Dict[str, Any]
    tool_run_snapshots: List[Dict[str, Any]]
    planning_trace: List[Dict[str, Any]]
    turn_state: AgentTurnState
    context: Dict[str, Any]
    context_usage: AgentContextUsage
    trace_id: str
    source_cards: List[AgentSourceCard]
    draft_plan: AgentPlanBundle
    action_suggestions: List[Dict[str, Any]]


class AgentIdentityRequiredError(ValueError):
    pass


class AgentSessionNotFoundError(LookupError):
    pass


class AgentDuplicateRequestInProgressError(RuntimeError):
    pass


class AgentDuplicateResponseAvailableError(RuntimeError):
    def __init__(self, response: AgentChatResponse):
        super().__init__("duplicate agent response available")
        self.response = response


def _is_retryable_sqlite_lock_error(exc: Exception) -> bool:
    return "database is locked" in str(exc).lower()


def _reserve_chat_request(session_id: str, trace_id: str) -> bool:
    key = (session_id, trace_id)
    with _IN_FLIGHT_CHAT_REQUESTS_LOCK:
        if key in _IN_FLIGHT_CHAT_REQUESTS:
            return False
        _IN_FLIGHT_CHAT_REQUESTS.add(key)
        return True


def _release_chat_request(session_id: str, trace_id: str) -> None:
    key = (session_id, trace_id)
    with _IN_FLIGHT_CHAT_REQUESTS_LOCK:
        _IN_FLIGHT_CHAT_REQUESTS.discard(key)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _strip_markdown_for_preview(text: str) -> str:
    value = text or ""
    value = re.sub(r"```(?:[\w#+.-]+)?", " ", value)
    value = value.replace("```", " ")
    value = re.sub(r"`([^`\n]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_\n]+)__", r"\1", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", value)
    value = re.sub(r"(?m)^\s*>\s?", "", value)
    value = re.sub(r"(?m)^\s*[-*+]\s+", "", value)
    value = re.sub(r"(?m)^\s*\d+\.\s+", "", value)
    return value.replace("**", "").replace("__", "")


def _shorten(text: str, limit: int = 80) -> str:
    value = " ".join(_strip_markdown_for_preview(text).split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _default_device_id(user_id: str | None, device_id: str | None) -> str:
    return device_id or (f"user:{user_id}" if user_id else "local-default")


def _require_actor_identity(user_id: str | None, device_id: str | None) -> None:
    if user_id or device_id:
        return
    raise AgentIdentityRequiredError(AGENT_IDENTITY_REQUIRED)


def _session_matches_actor(session: AgentSession, user_id: str | None, device_id: str | None) -> bool:
    user_id, device_id = resolve_query_identity(user_id, device_id)
    device_ids = build_device_scope_aliases(user_id, device_id)
    matched = False

    if user_id:
        if session.user_id and session.user_id != user_id:
            return False
        matched = matched or session.user_id == user_id

    if device_ids:
        if session.device_id and session.device_id not in device_ids:
            return False
        matched = matched or session.device_id in device_ids
    elif device_id:
        if session.device_id and session.device_id != device_id:
            return False
        matched = matched or session.device_id == device_id

    return matched


def _actor_identity_key(user_id: str | None, device_id: str | None) -> str:
    user_id, device_id = canonicalize_storage_identity(user_id, device_id)
    return f"user:{user_id or ''}|device:{device_id or ''}"


def _deterministic_session_id_for_payload(payload: AgentChatRequest) -> str | None:
    if not payload.client_request_id:
        return None
    seed = (
        f"agent-session:{_actor_identity_key(payload.user_id, payload.device_id)}:"
        f"{payload.agent_type}:{payload.client_request_id}"
    )
    return uuid5(NAMESPACE_URL, seed).hex


def _default_title(title: str | None) -> str:
    clean = " ".join((title or "").split())
    return clean[:40] if clean else "新会话"


def _title_from_message(message: str) -> str:
    clean = " ".join((message or "").split())
    return clean[:24] if clean else "新会话"


def _default_prompt_template_for_agent_type(agent_type: str) -> str | None:
    if agent_type == "tutor":
        return DEFAULT_TUTOR_TEMPLATE
    return None


def _resolved_agent_model(provider: str | None, model: str | None) -> tuple[str, str]:
    resolved_provider = (provider or "").strip()
    resolved_model = (model or "").strip()
    if not resolved_provider or resolved_provider == "auto":
        resolved_provider = DEFAULT_AGENT_PROVIDER
    if not resolved_model or resolved_model == "auto":
        resolved_model = DEFAULT_AGENT_MODEL
    return resolved_provider, resolved_model


def _apply_session_runtime_defaults(session: AgentSession) -> AgentSession:
    session.provider, session.model = _resolved_agent_model(session.provider, session.model)
    if session.agent_type == "tutor" and (not session.prompt_template_id or session.prompt_template_id == "tutor.v1"):
        session.prompt_template_id, _ = resolve_prompt_template(session.agent_type, DEFAULT_TUTOR_TEMPLATE)
    return session


def _session_model_options(session: AgentSession) -> Dict[str, str]:
    provider, model = _resolved_agent_model(session.provider, session.model)
    return {
        "preferred_provider": provider,
        "preferred_model": model,
    }


def _format_percent(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    if 0 <= numeric <= 1:
        numeric *= 100
    return f"{numeric:.1f}%"


def _format_hours(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{numeric:.1f}h"


def _format_count(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "0"


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json(item) for item in value]
    return value


def _build_execution_state(
    *,
    trace_id: str,
    request_analysis: Dict[str, Any],
    response_strategy: Dict[str, Any] | None,
    selected_tools: List[str],
    tool_run_snapshots: List[Dict[str, Any]],
    plan_bundle: AgentPlanBundle,
    planning_trace: List[Dict[str, Any]] | None = None,
    stage: str,
    error_message: str | None = None,
    context_usage: AgentContextUsage | None = None,
) -> Dict[str, Any]:
    total_tools = len(tool_run_snapshots)
    cache_hits = sum(1 for item in tool_run_snapshots if item.get("cache_hit"))
    failed_tools = sum(1 for item in tool_run_snapshots if item.get("status") == "failed")
    completed_tools = sum(1 for item in tool_run_snapshots if item.get("status") in {"completed", "cached"})
    iteration_map: Dict[int, Dict[str, Any]] = {}
    for item in tool_run_snapshots:
        iteration = int(item.get("iteration") or 1)
        bucket = iteration_map.setdefault(
            iteration,
            {
                "iteration": iteration,
                "reason": item.get("reason") or "执行工具",
                "tools": [],
                "cache_hits": 0,
                "failed_tools": 0,
            },
        )
        bucket["tools"].append(item.get("tool_name"))
        if item.get("cache_hit"):
            bucket["cache_hits"] += 1
        if item.get("status") == "failed":
            bucket["failed_tools"] += 1

    payload = {
        "trace_id": trace_id,
        "stage": stage,
        "goal": request_analysis.get("goal"),
        "time_horizon": request_analysis.get("time_horizon"),
        "output_mode": request_analysis.get("output_mode"),
        "selected_tools": selected_tools,
        "tool_runs": _normalize_json(tool_run_snapshots),
        "stats": {
            "total_tools": total_tools,
            "completed_tools": completed_tools,
            "failed_tools": failed_tools,
            "cache_hits": cache_hits,
            "focus_count": len(request_analysis.get("focuses") or []),
            "task_count": len(plan_bundle.tasks),
            "iteration_count": len(iteration_map),
            "replan_count": max(0, len((planning_trace or [])) - 1),
        },
        "plan_summary": plan_bundle.summary,
        "response_strategy": _normalize_json(response_strategy or {}),
        "iterations": [iteration_map[key] for key in sorted(iteration_map.keys())],
        "plan_versions": _normalize_json(planning_trace or []),
    }
    if context_usage is not None:
        payload["context_usage"] = context_usage.model_dump(mode="json")
    if error_message:
        payload["error_message"] = error_message
    return payload


TOOL_LABELS = {
    "get_progress_summary": "总体进度",
    "get_knowledge_mastery": "知识点掌握",
    "get_wrong_answers": "错题本",
    "get_review_pressure": "复习压力",
    "get_learning_sessions": "学习会话",
    "get_study_history": "学习历史",
    "agent": "模型推理",
}

TOOL_LABELS["search_openviking_context"] = "OpenViking context"
TOOL_LABELS["consult_openmanus"] = "OpenManus 子代理"

FOCUS_LIBRARY: Dict[str, Dict[str, Any]] = {
    "progress_diagnosis": {
        "title": "校准总体掌握状态",
        "description": "先确认整体正确率、掌握度和近期波动，避免把局部表现误判成整体水平。",
        "tools": ["get_progress_summary", "get_knowledge_mastery"],
        "priority": "high",
    },
    "weakness_review": {
        "title": "定位高风险错题与到期复习",
        "description": "优先找出高风险、重复出错和已经到期的条目，避免复习顺序失焦。",
        "tools": ["get_wrong_answers", "get_review_pressure"],
        "priority": "high",
    },
    "history_reconstruction": {
        "title": "回看近期学习轨迹",
        "description": "核对最近的学习节奏、会话表现和长期连续性，判断问题是偶发还是持续。",
        "tools": ["get_learning_sessions", "get_study_history"],
        "priority": "medium",
    },
    "planning_schedule": {
        "title": "拆出本轮执行顺序",
        "description": "把当前需求落到具体的行动顺序、时间块或提问顺序上，而不是只做概述。",
        "tools": ["get_progress_summary", "get_knowledge_mastery", "get_wrong_answers", "get_review_pressure", "get_learning_sessions"],
        "priority": "high",
    },
    "future_forecast": {
        "title": "推演接下来的风险变化",
        "description": "基于当前趋势、积压和最近表现判断接下来几天的复习压力和可能波动。",
        "tools": ["get_review_pressure", "get_progress_summary", "get_learning_sessions"],
        "priority": "high",
    },
}

FOCUS_LIBRARY["external_context_search"] = {
    "title": "检索外部上下文资料",
    "description": "从 OpenViking 的资料库、知识库或长期记忆中补充外部证据，不把缺失信息编造成结论。",
    "tools": ["search_openviking_context"],
    "priority": "high",
}

OUTPUT_MODE_LABELS = {
    "plan": "行动方案",
    "prediction": "趋势预测",
    "history": "历史复盘",
    "diagnosis": "诊断结论",
    "answer": "问题回答",
}

MAX_AUTO_TOOL_ITERATIONS = 3
FOLLOW_UP_PLANNER_SCHEMA = {
    "should_continue": True,
    "decision_reason": "为什么还需要补数据，或者为什么当前已经足够。",
    "next_tools": [
        {
            "tool_name": "get_wrong_answers",
            "reason": "补充高风险错题证据",
        }
    ],
}
RESPONSE_STRATEGY_SCHEMA = {
    "strategy": "answer",
    "reason": "为什么当前应该采用该回答方式。",
    "instruction": "给回答模型的写作指令。",
    "clarifying_questions": ["如果需要澄清，列出最多3个问题。"],
}

DIRECT_ANSWER_STYLE_GUIDANCE = (
    "默认先像正常聊天一样直接回答，可先用一两句说清判断；"
    "只有在问题明显复杂，或用户明确要求列表、计划、分步骤时，再做简短分点。"
    "除非用户明确要求，否则不要固定套“结论 / 依据 / 下一步建议”标题。"
)
CAUTIOUS_ANSWER_STYLE_GUIDANCE = (
    "可以先给当前判断，但用自然口吻顺手交代证据边界和不确定性；"
    "除非用户明确要求，否则不要固定标题或长列表。"
)
CLARIFY_STYLE_GUIDANCE = (
    "用自然口吻只问最关键的 1 到 2 个澄清问题，不要先给大段方案；"
    "如果上下文已经基本够用，只问 1 个问题即可。"
)
NO_DATA_STYLE_GUIDANCE = (
    "先直接说明当前缺少关键学习数据，语气自然、简短，不要反复免责声明；"
    "只给 1 到 2 条通用建议，并告诉用户最值得补充的一项信息。"
)


def ensure_agent_schema() -> None:
    global _AGENT_SCHEMA_READY
    if _AGENT_SCHEMA_READY:
        return

    AgentSession.__table__.create(bind=engine, checkfirst=True)
    AgentMessage.__table__.create(bind=engine, checkfirst=True)
    AgentMemory.__table__.create(bind=engine, checkfirst=True)
    AgentToolCall.__table__.create(bind=engine, checkfirst=True)
    AgentTurnState.__table__.create(bind=engine, checkfirst=True)
    AgentToolCache.__table__.create(bind=engine, checkfirst=True)
    AgentActionLog.__table__.create(bind=engine, checkfirst=True)
    AgentTask.__table__.create(bind=engine, checkfirst=True)
    AgentTaskEvent.__table__.create(bind=engine, checkfirst=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_messages_session_trace_role "
            "ON agent_messages (session_id, trace_id, role)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_turn_states_session_trace "
            "ON agent_turn_states (session_id, trace_id)"
        )
    _AGENT_SCHEMA_READY = True


def _session_message_stats(db: Session, session_ids: List[str]) -> tuple[Dict[str, int], Dict[str, str]]:
    if not session_ids:
        return {}, {}

    count_rows = (
        db.query(AgentMessage.session_id, func.count(AgentMessage.id))
        .filter(AgentMessage.session_id.in_(session_ids))
        .group_by(AgentMessage.session_id)
        .all()
    )
    count_map = {str(session_id): int(count or 0) for session_id, count in count_rows}

    latest_rows = (
        db.query(AgentMessage)
        .filter(AgentMessage.session_id.in_(session_ids))
        .order_by(AgentMessage.session_id, desc(AgentMessage.created_at), desc(AgentMessage.id))
        .all()
    )
    preview_map: Dict[str, str] = {}
    for row in latest_rows:
        preview_map.setdefault(row.session_id, _shorten(row.content, limit=90))

    return count_map, preview_map


def serialize_session(
    db: Session,
    session: AgentSession,
    message_count: int | None = None,
    last_message_preview: str | None = None,
) -> AgentSessionItem:
    if message_count is None or last_message_preview is None:
        counts, previews = _session_message_stats(db, [session.id])
        message_count = counts.get(session.id, 0)
        last_message_preview = previews.get(session.id)

    return AgentSessionItem(
        id=session.id,
        user_id=session.user_id,
        device_id=session.device_id,
        title=session.title,
        agent_type=session.agent_type,
        status=session.status,
        model=session.model,
        provider=session.provider,
        prompt_template_id=session.prompt_template_id,
        context_summary=session.context_summary,
        message_count=message_count or 0,
        last_message_preview=last_message_preview,
        last_message_at=_iso(session.last_message_at),
        created_at=_iso(session.created_at) or datetime.now().isoformat(),
        updated_at=_iso(session.updated_at) or datetime.now().isoformat(),
    )


def serialize_message(message: AgentMessage) -> AgentMessageItem:
    return AgentMessageItem(
        id=int(message.id),
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        content_structured=message.content_structured or {},
        tool_name=message.tool_name,
        tool_input=message.tool_input,
        tool_output=message.tool_output,
        message_status=message.message_status,
        token_input=int(message.token_input or 0),
        token_output=int(message.token_output or 0),
        latency_ms=int(message.latency_ms or 0),
        trace_id=message.trace_id,
        created_at=_iso(message.created_at) or datetime.now().isoformat(),
    )


def serialize_tool_call(tool_call: AgentToolCall) -> AgentToolCallItem:
    return AgentToolCallItem(
        id=int(tool_call.id),
        session_id=tool_call.session_id,
        message_id=int(tool_call.message_id) if tool_call.message_id is not None else None,
        tool_name=tool_call.tool_name,
        tool_args=tool_call.tool_args or {},
        tool_result=tool_call.tool_result,
        success=bool(tool_call.success),
        error_message=tool_call.error_message,
        duration_ms=int(tool_call.duration_ms or 0),
        created_at=_iso(tool_call.created_at) or datetime.now().isoformat(),
    )


def serialize_turn_state(turn_state: AgentTurnState) -> AgentTurnStateItem:
    return AgentTurnStateItem(
        id=int(turn_state.id),
        session_id=turn_state.session_id,
        user_message_id=int(turn_state.user_message_id),
        assistant_message_id=int(turn_state.assistant_message_id) if turn_state.assistant_message_id is not None else None,
        trace_id=turn_state.trace_id,
        status=turn_state.status,
        goal=turn_state.goal,
        request_analysis=turn_state.request_analysis or {},
        selected_tools=turn_state.selected_tools or [],
        tool_snapshots=turn_state.tool_snapshots or [],
        plan_draft=turn_state.plan_draft or {},
        plan_final=turn_state.plan_final or {},
        execution_state=turn_state.execution_state or {},
        error_message=turn_state.error_message,
        created_at=_iso(turn_state.created_at) or datetime.now().isoformat(),
        updated_at=_iso(turn_state.updated_at) or datetime.now().isoformat(),
    )


def create_session(db: Session, payload: AgentSessionCreateRequest) -> AgentSession:
    ensure_agent_schema()
    _require_actor_identity(payload.user_id, payload.device_id)
    stored_user_id, stored_device_id = canonicalize_storage_identity(payload.user_id, payload.device_id)
    resolved_provider, resolved_model = _resolved_agent_model(payload.provider, payload.model)
    template_seed = payload.prompt_template_id or _default_prompt_template_for_agent_type(payload.agent_type)
    template_id, _ = resolve_prompt_template(payload.agent_type, template_seed)
    session = AgentSession(
        id=uuid4().hex,
        user_id=stored_user_id,
        device_id=stored_device_id,
        title=_default_title(payload.title),
        agent_type=payload.agent_type,
        status="active",
        model=resolved_model,
        provider=resolved_provider,
        prompt_template_id=template_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    logger.info("[AGENT_SESSION] created session_id=%s agent_type=%s model=%s", session.id, session.agent_type, resolved_model)
    return session


def list_sessions(
    db: Session,
    user_id: str | None = None,
    device_id: str | None = None,
    status: str = "active",
    limit: int = 20,
) -> List[AgentSession]:
    ensure_agent_schema()
    _require_actor_identity(user_id, device_id)
    user_id, device_id = resolve_query_identity(user_id, device_id)
    device_ids = build_device_scope_aliases(user_id, device_id)
    activity_at = func.coalesce(AgentSession.last_message_at, AgentSession.created_at)
    query = db.query(AgentSession).order_by(desc(activity_at), desc(AgentSession.created_at))
    if user_id:
        query = query.filter(AgentSession.user_id == user_id)
    if device_ids:
        if len(device_ids) == 1:
            query = query.filter(AgentSession.device_id == device_ids[0])
        else:
            query = query.filter(AgentSession.device_id.in_(device_ids))
    elif device_id:
        query = query.filter(AgentSession.device_id == device_id)
    if status != "all":
        query = query.filter(AgentSession.status == status)
    return query.limit(limit).all()


def get_session_or_none(db: Session, session_id: str) -> AgentSession | None:
    ensure_agent_schema()
    return db.query(AgentSession).filter(AgentSession.id == session_id).first()


def get_session_for_actor_or_none(
    db: Session,
    session_id: str,
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> AgentSession | None:
    ensure_agent_schema()
    _require_actor_identity(user_id, device_id)
    session = get_session_or_none(db, session_id)
    if session is None:
        return None
    if not _session_matches_actor(session, user_id, device_id):
        return None
    return session


def list_messages(db: Session, session_id: str, limit: int = 100) -> List[AgentMessage]:
    ensure_agent_schema()
    messages = (
        db.query(AgentMessage)
        .filter(AgentMessage.session_id == session_id)
        .order_by(desc(AgentMessage.created_at), desc(AgentMessage.id))
        .limit(limit)
        .all()
    )
    messages.reverse()
    return messages


def list_turn_states(db: Session, session_id: str, limit: int = 50) -> List[AgentTurnState]:
    ensure_agent_schema()
    turns = (
        db.query(AgentTurnState)
        .filter(AgentTurnState.session_id == session_id)
        .order_by(desc(AgentTurnState.created_at), desc(AgentTurnState.id))
        .limit(limit)
        .all()
    )
    turns.reverse()
    return turns


def list_tool_calls_for_message(db: Session, session_id: str, message_id: int) -> List[AgentToolCall]:
    ensure_agent_schema()
    return (
        db.query(AgentToolCall)
        .filter(
            AgentToolCall.session_id == session_id,
            AgentToolCall.message_id == message_id,
        )
        .order_by(AgentToolCall.created_at, AgentToolCall.id)
        .all()
    )


def summarize_session(
    db: Session,
    session_id: str,
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> AgentSummaryResponse:
    ensure_agent_schema()
    session = get_session_for_actor_or_none(db, session_id, user_id=user_id, device_id=device_id)
    if session is None:
        raise AgentSessionNotFoundError(AGENT_SESSION_NOT_FOUND)

    memory = refresh_session_summary(db, session)
    if memory is None:
        raise ValueError("当前消息数量不足，暂时无法生成摘要")

    db.commit()
    db.refresh(memory)
    return AgentSummaryResponse(
        session_id=session_id,
        summary=memory.summary,
        memory_id=int(memory.id),
        message_count=len(list_messages(db, session_id)),
    )


def _get_turn_state_by_trace_id(db: Session, session_id: str, trace_id: str) -> AgentTurnState | None:
    ensure_agent_schema()
    return (
        db.query(AgentTurnState)
        .filter(
            AgentTurnState.session_id == session_id,
            AgentTurnState.trace_id == trace_id,
        )
        .order_by(desc(AgentTurnState.updated_at), desc(AgentTurnState.id))
        .first()
    )


def _build_chat_response_from_existing_turn(
    db: Session,
    *,
    session: AgentSession,
    turn_state: AgentTurnState,
) -> AgentChatResponse | None:
    assistant_message_id = int(turn_state.assistant_message_id or 0)
    if assistant_message_id <= 0:
        return None

    user_message = db.query(AgentMessage).filter(AgentMessage.id == int(turn_state.user_message_id)).first()
    assistant_message = db.query(AgentMessage).filter(AgentMessage.id == assistant_message_id).first()
    if user_message is None or assistant_message is None:
        return None

    structured = assistant_message.content_structured or {}
    context_usage = AgentContextUsage(**(structured.get("context_usage") or {}))
    tool_calls = list_tool_calls_for_message(db, session.id, int(user_message.id))

    return AgentChatResponse(
        session=serialize_session(db, session),
        user_message=serialize_message(user_message),
        assistant_message=serialize_message(assistant_message),
        tool_calls=[serialize_tool_call(tool_call) for tool_call in tool_calls],
        context_usage=context_usage,
        trace_id=turn_state.trace_id,
        error_message=turn_state.error_message,
    )


def _get_existing_chat_response(
    db: Session,
    *,
    session: AgentSession,
    trace_id: str,
) -> AgentChatResponse | None:
    turn_state = _get_turn_state_by_trace_id(db, session.id, trace_id)
    if turn_state is None:
        return None
    return _build_chat_response_from_existing_turn(db, session=session, turn_state=turn_state)


async def _wait_for_existing_chat_response(
    db: Session,
    *,
    session: AgentSession,
    trace_id: str,
    timeout_seconds: float = AGENT_DUPLICATE_WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.1,
) -> AgentChatResponse | None:
    deadline = perf_counter() + timeout_seconds
    while perf_counter() < deadline:
        try:
            db.rollback()
            db.expire_all()
            response = _get_existing_chat_response(db, session=session, trace_id=trace_id)
        except OperationalError as exc:
            db.rollback()
            if not _is_retryable_sqlite_lock_error(exc):
                raise
            await asyncio.sleep(poll_interval_seconds)
            continue
        if response is not None:
            return response
        await asyncio.sleep(poll_interval_seconds)
    return None


def _raise_duplicate_chat_conflict(
    db: Session,
    *,
    session_id: str,
    trace_id: str,
    user_id: str | None,
    device_id: str | None,
) -> None:
    db.rollback()
    session = get_session_for_actor_or_none(
        db,
        session_id,
        user_id=user_id,
        device_id=device_id,
    )
    if session is not None:
        existing_response = _get_existing_chat_response(db, session=session, trace_id=trace_id)
        if existing_response is not None:
            raise AgentDuplicateResponseAvailableError(existing_response)
    raise AgentDuplicateRequestInProgressError(AGENT_DUPLICATE_REQUEST_IN_PROGRESS)


def _tool_type_label(session_type: str | None) -> str:
    mapping = {
        "exam": "整卷测验",
        "detail_practice": "知识点测验",
        "all": "全部会话",
    }
    return mapping.get(session_type or "", session_type or "学习会话")


def _build_wrong_answer_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    items = payload.get("items") or []
    count = int(payload.get("count") or len(items))
    returned_count = int(payload.get("returned_count") or len(items))
    severity_counts = payload.get("severity_counts") or {}
    critical_count = int(
        severity_counts.get("critical")
        or sum(1 for item in items if item.get("severity_tag") == "critical")
    )
    review_due_count = int(payload.get("due_count") or 0)
    if not review_due_count:
        today = date.today()
        for item in items:
            raw_date = item.get("next_review_date")
            if not raw_date:
                continue
            try:
                if date.fromisoformat(str(raw_date)) <= today:
                    review_due_count += 1
            except ValueError:
                continue

    status = str(payload.get("status") or "active")
    status_label = {
        "active": "活跃",
        "archived": "已归档",
        "all": "全部",
    }.get(status, status)
    top_key_points = payload.get("top_key_points") or []
    top_chapters = payload.get("top_chapters") or []

    bullets = []
    if top_key_points:
        bullets.append(
            "高频薄弱点："
            + " / ".join(
                f"{_shorten(str(item.get('name') or '未命名考点'), 18)}({int(item.get('count') or 0)})"
                for item in top_key_points[:3]
            )
        )
    if top_chapters:
        bullets.append(
            "问题最集中章节："
            + " / ".join(
                f"{_shorten(str(item.get('chapter_label') or item.get('chapter_id') or '未标记章节'), 18)}({int(item.get('count') or 0)})"
                for item in top_chapters[:2]
            )
        )
    for item in items[: max(0, 3 - len(bullets))]:
        label = item.get("chapter_label") or item.get("key_point") or "未命名错题"
        tags = [item.get("question_type"), item.get("severity_tag")]
        bullets.append(f"最近样本：{_shorten(str(label), 40)} · " + " / ".join([tag for tag in tags if tag]))

    if count > returned_count:
        summary = (
            f"共发现 {count} 条{status_label}错题，当前展示最近 {returned_count} 条样本；"
            f"高风险 {critical_count} 条。"
        )
    else:
        summary = f"共提取 {count} 条{status_label}错题，高风险 {critical_count} 条。"
    if review_due_count:
        summary += f" 其中 {review_due_count} 条已到复习时间。"

    return AgentSourceCard(
        tool_name=tool_name,
        title="错题本快照",
        summary=summary,
        count=count,
        stats=[
            AgentSourceStat(label="总量", value=_format_count(count)),
            AgentSourceStat(label="展示", value=_format_count(returned_count)),
            AgentSourceStat(label="高风险", value=_format_count(critical_count)),
            AgentSourceStat(label="到期待复习", value=_format_count(review_due_count)),
        ],
        bullets=bullets,
    )


def _build_learning_sessions_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    items = payload.get("items") or []
    count = int(payload.get("count") or len(items))
    accuracies = [float(item.get("accuracy") or 0) for item in items]
    avg_accuracy = sum(accuracies) / len(accuracies) if accuracies else 0.0
    total_questions = sum(int(item.get("total_questions") or 0) for item in items)

    bullets = []
    for item in items[:3]:
        title = item.get("title") or "未命名练习"
        session_type = _tool_type_label(item.get("session_type"))
        accuracy = _format_percent(item.get("accuracy"))
        wrong_count = int(item.get("wrong_count") or 0)
        bullets.append(f"{_shorten(str(title), 42)} · {session_type} · 正确率 {accuracy} · 错题 {wrong_count}")

    return AgentSourceCard(
        tool_name=tool_name,
        title="近期学习轨迹",
        summary=f"已抓取最近 {count} 次学习会话，平均正确率 {avg_accuracy:.1f}%，覆盖题量 {total_questions} 题。",
        count=count,
        stats=[
            AgentSourceStat(label="会话数", value=_format_count(count)),
            AgentSourceStat(label="平均正确率", value=f"{avg_accuracy:.1f}%"),
            AgentSourceStat(label="覆盖题量", value=_format_count(total_questions)),
        ],
        bullets=bullets,
    )


def _build_progress_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    overview = payload.get("overview") or {}
    trend = payload.get("daily_trend") or []
    weak_points = payload.get("weak_points") or []
    confidence_distribution = payload.get("confidence_distribution") or []
    weakest_area = payload.get("weakest_area") or {}
    wow_delta = payload.get("wow_delta") or {}

    active_days = sum(1 for item in trend if int(item.get("questions") or 0) > 0)
    dominant_confidence = max(confidence_distribution, key=lambda item: item.get("count") or 0, default=None)
    delta = wow_delta.get("delta")
    delta_prefix = "+" if isinstance(delta, (int, float)) and delta >= 0 else ""

    bullets = []
    if weakest_area:
        bullets.append(
            f"当前最弱区域是 {_shorten(str(weakest_area.get('name') or '未识别区域'), 42)}，"
            f"累计 {weakest_area.get('total') or 0} 题，正确率 {_format_percent(weakest_area.get('accuracy'))}。"
        )
    if dominant_confidence:
        bullets.append(
            f"答题信心以 {dominant_confidence.get('label') or dominant_confidence.get('key') or '未知'} 为主，"
            f"占比 {_format_percent(dominant_confidence.get('pct'))}。"
        )
    for item in weak_points[:2]:
        bullets.append(
            f"{_shorten(str(item.get('name') or '未命名考点'), 40)} · 正确率 {_format_percent(item.get('accuracy'))} · 错误 {item.get('wrong') or 0}"
        )

    delta_text = "--"
    if isinstance(delta, (int, float)):
        delta_text = f"{delta_prefix}{delta:.1f}%"

    return AgentSourceCard(
        tool_name=tool_name,
        title="总体进度统计",
        summary=(
            f"累计 {overview.get('total_questions') or 0} 题，整体正确率 {_format_percent(overview.get('avg_accuracy'))}，"
            f"活跃学习 {active_days} 天，本周波动 {delta_text}。"
        ),
        count=int(overview.get("total_questions") or 0),
        stats=[
            AgentSourceStat(label="累计会话", value=_format_count(overview.get("total_sessions"))),
            AgentSourceStat(label="累计题量", value=_format_count(overview.get("total_questions"))),
            AgentSourceStat(label="整体正确率", value=_format_percent(overview.get("avg_accuracy"))),
            AgentSourceStat(label="学习时长", value=_format_hours(overview.get("total_duration_hours"))),
        ],
        bullets=bullets,
    )


def _build_knowledge_mastery_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    weak_concepts = payload.get("weak_concepts") or []
    weak_chapters = payload.get("weak_chapters") or []
    window_days = int(payload.get("window_days") or 7)
    bullets = []
    for item in weak_concepts[:2]:
        bullets.append(
            f"{_shorten(str(item.get('name') or '未命名知识点'), 40)} · 掌握度 {item.get('mastery_score') or 0}% · "
            f"{item.get('chapter_label') or '未标记章节'}"
        )
    for item in weak_chapters[:1]:
        bullets.append(
            f"{_shorten(str(item.get('chapter_label') or '未标记章节'), 40)} · 平均掌握度 {item.get('avg_mastery') or 0}% · "
            f"{item.get('due_count') or 0} 个知识点临近复习"
        )

    return AgentSourceCard(
        tool_name=tool_name,
        title="知识点掌握面",
        summary=(
            f"已扫描 {payload.get('total_concepts') or 0} 个知识点，综合掌握度 {payload.get('avg_mastery') or 0}%，"
            f"今日到期 {payload.get('due_today') or 0} 个，{payload.get('window_days') or 0} 天内到期 {payload.get('due_in_window') or 0} 个。"
        ),
        count=int(payload.get("total_concepts") or 0),
        stats=[
            AgentSourceStat(label="知识点", value=_format_count(payload.get("total_concepts"))),
            AgentSourceStat(label="综合掌握度", value=f"{float(payload.get('avg_mastery') or 0):.1f}%"),
            AgentSourceStat(label="今日到期", value=_format_count(payload.get("due_today"))),
            AgentSourceStat(label=f"{window_days}天到期", value=_format_count(payload.get("due_in_window"))),
        ],
        bullets=bullets,
    )


def _build_study_history_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    recent_uploads = payload.get("recent_uploads") or []
    book_distribution = payload.get("book_distribution") or {}
    fallback_count = int(payload.get("session_fallback_count_in_window") or 0)
    bullets = []
    if book_distribution:
        top_books = list(book_distribution.keys())[:3]
        bullets.append("最近覆盖科目：" + " / ".join(str(book) for book in top_books))
    if fallback_count:
        bullets.append(f"其中 {fallback_count} 条来自带原文的学习会话补记")
    for item in recent_uploads[:2]:
        source = item.get("source")
        source_suffix = " · 会话补记" if source == "learning_session" else ""
        bullets.append(
            f"{item.get('date') or '--'} · {item.get('book') or '未知'} · "
            f"{_shorten(str(item.get('chapter_title') or '未识别章节'), 32)}{source_suffix}"
        )

    return AgentSourceCard(
        tool_name=tool_name,
        title="长期学习记录",
        summary=(
            f"近 {payload.get('days') or 0} 天共有 {payload.get('total_uploads_in_window') or 0} 次上传记录，"
            f"最近 7 天 {payload.get('weekly_uploads') or 0} 次，连续学习 {payload.get('streak_days') or 0} 天。"
            + (f" 其中 {fallback_count} 次由学习会话原文补记。" if fallback_count else "")
        ),
        count=int(payload.get("total_uploads_in_window") or 0),
        stats=[
            AgentSourceStat(label="近窗上传", value=_format_count(payload.get("total_uploads_in_window"))),
            AgentSourceStat(label="近7天上传", value=_format_count(payload.get("weekly_uploads"))),
            AgentSourceStat(label="连续学习", value=_format_count(payload.get("streak_days"))),
            AgentSourceStat(label="覆盖科目", value=_format_count(len(book_distribution))),
        ],
        bullets=bullets,
    )


def _build_review_pressure_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    severity_counts = payload.get("severity_counts") or {}
    critical_count = int(severity_counts.get("critical") or 0)
    stubborn_count = int(severity_counts.get("stubborn") or 0)
    estimate = payload.get("estimated_days_to_clear")
    if isinstance(estimate, float) and estimate == float("inf"):
        estimate_text = "无法清仓"
    elif isinstance(estimate, float):
        estimate_text = f"{estimate:.1f} 天"
    elif isinstance(estimate, int):
        estimate_text = f"{estimate} 天"
    else:
        estimate_text = "无法估计"

    bullets = []
    if payload.get("clear_message"):
        bullets.append(str(payload.get("clear_message")))
    if payload.get("recent_test_accuracy") is not None:
        bullets.append(f"最近测试正确率约 {float(payload.get('recent_test_accuracy') or 0):.1f}%")
    bullets.append(
        f"严重度分布：critical {critical_count} / stubborn {stubborn_count} / 到期待处理 {payload.get('due_wrong_answers') or 0}"
    )

    return AgentSourceCard(
        tool_name=tool_name,
        title="复习压力评估",
        summary=(
            f"当前活跃积压 {payload.get('current_backlog') or 0} 条，今天至少需要处理 "
            f"{payload.get('daily_required_reviews') or 0} 条，预计清仓时间 {estimate_text}。"
        ),
        count=int(payload.get("current_backlog") or 0),
        stats=[
            AgentSourceStat(label="积压量", value=_format_count(payload.get("current_backlog"))),
            AgentSourceStat(label="今日需复习", value=_format_count(payload.get("daily_required_reviews"))),
            AgentSourceStat(label="日均新增", value=f"{float(payload.get('avg_new_per_day') or 0):.1f}"),
            AgentSourceStat(label="到期待处理", value=_format_count(payload.get("due_wrong_answers"))),
        ],
        bullets=bullets,
    )


def _build_openviking_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    status = str(payload.get("status") or "ok")
    query = str(payload.get("query") or "").strip()
    target_uri = str(payload.get("target_uri") or "").strip()
    items = payload.get("items") or []
    count = int(payload.get("count") or len(items))
    memories = payload.get("memories") or []
    resources = payload.get("resources") or []
    skills = payload.get("skills") or []

    bullets: List[str] = []
    if query:
        bullets.append(f"query: {_shorten(query, 72)}")
    if target_uri:
        bullets.append(f"target: {_shorten(target_uri, 72)}")

    if status == "disabled":
        bullets.append("OpenViking is disabled in the current environment.")
        return AgentSourceCard(
            tool_name=tool_name,
            title="OpenViking external context",
            summary="OpenViking is not enabled, so no external context was queried for this turn.",
            count=0,
            stats=[
                AgentSourceStat(label="status", value="disabled"),
                AgentSourceStat(label="results", value="0"),
            ],
            bullets=bullets[:4],
        )

    if status == "error":
        error_message = _shorten(str(payload.get("error") or "OpenViking request failed."), 96)
        bullets.append(f"error: {error_message}")
        return AgentSourceCard(
            tool_name=tool_name,
            title="OpenViking external context",
            summary="OpenViking search failed, so this turn has no external context evidence.",
            count=0,
            stats=[
                AgentSourceStat(label="status", value="error"),
                AgentSourceStat(label="results", value="0"),
            ],
            bullets=bullets[:4],
        )

    for item in items[:3]:
        uri = _shorten(str(item.get("uri") or "--"), 56)
        context_type = str(item.get("context_type") or "resource")
        abstract = item.get("abstract") or item.get("overview") or item.get("match_reason") or ""
        score = item.get("score")
        bullet = f"{context_type} | {uri}"
        if isinstance(score, (int, float)):
            bullet += f" | {float(score):.3f}"
        if abstract:
            bullet += f" | {_shorten(str(abstract), 48)}"
        bullets.append(bullet)

    if count:
        summary = (
            f"OpenViking returned {count} external context hits, including "
            f"{len(resources)} resources, {len(memories)} memories, and {len(skills)} skills."
        )
    else:
        summary = "OpenViking was queried, but no matching external context was found."

    return AgentSourceCard(
        tool_name=tool_name,
        title="OpenViking external context",
        summary=summary,
        count=count,
        stats=[
            AgentSourceStat(label="results", value=_format_count(count)),
            AgentSourceStat(label="resources", value=_format_count(len(resources))),
            AgentSourceStat(label="memories", value=_format_count(len(memories))),
            AgentSourceStat(label="skills", value=_format_count(len(skills))),
        ],
        bullets=bullets[:4],
    )


def _build_openmanus_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    answer = _shorten(str(payload.get("answer") or ""), 220)
    query = _shorten(str(payload.get("query") or ""), 72)
    tool_names = [str(item) for item in list(payload.get("tool_names") or []) if str(item or "").strip()]
    steps_executed = int(payload.get("steps_executed") or 0)
    count = int(payload.get("count") or (1 if answer else 0))

    bullets: List[str] = []
    if query:
        bullets.append(f"query: {query}")
    if answer:
        bullets.append(f"answer: {answer}")
    if tool_names:
        bullets.append("tools: " + " / ".join(tool_names[:4]))

    summary = "OpenManus returned a delegated answer for this turn."
    if answer:
        summary = f"OpenManus 子代理返回了一段可供当前回合参考的答案：{answer}"

    return AgentSourceCard(
        tool_name=tool_name,
        title="OpenManus 子代理",
        summary=summary,
        count=count,
        stats=[
            AgentSourceStat(label="status", value=str(payload.get("status") or "completed")),
            AgentSourceStat(label="steps", value=_format_count(steps_executed)),
            AgentSourceStat(label="tools", value=_format_count(len(tool_names))),
        ],
        bullets=bullets[:4],
    )


def _source_key_label(key: str) -> str:
    return str(key or "").replace("_", " ").strip() or "value"


def _preview_scalar(value: Any, limit: int = 40) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.1f}" if not value.is_integer() else str(int(value))
    if isinstance(value, (int, str)):
        return _shorten(str(value), limit=limit)
    return _shorten(str(value), limit=limit)


def _build_generic_source(tool_name: str, payload: Dict[str, Any]) -> AgentSourceCard:
    count = _estimate_tool_result_count(tool_name, payload)
    stats: List[AgentSourceStat] = []
    bullets: List[str] = []

    for key, value in payload.items():
        if key in {"items", "generated_at"}:
            continue
        if isinstance(value, (str, int, float, bool)):
            stats.append(AgentSourceStat(label=_source_key_label(key), value=_preview_scalar(value)))
        elif isinstance(value, dict) and value:
            preview = " / ".join(
                f"{_source_key_label(child_key)} {_preview_scalar(child_value, limit=20)}"
                for child_key, child_value in list(value.items())[:3]
                if child_value not in (None, "", [], {})
            )
            if preview:
                bullets.append(f"{_source_key_label(key)}: {preview}")
        elif isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                for item in value[:3]:
                    parts = [
                        f"{_source_key_label(child_key)} {_preview_scalar(child_value, limit=18)}"
                        for child_key, child_value in item.items()
                        if child_value not in (None, "", [], {}) and not str(child_key).endswith("_id")
                    ]
                    if parts:
                        bullets.append(" · ".join(parts[:3]))
            else:
                joined = " / ".join(_preview_scalar(item, limit=18) for item in value[:3] if item not in (None, ""))
                if joined:
                    bullets.append(f"{_source_key_label(key)}: {joined}")

        if len(stats) >= 4 and len(bullets) >= 3:
            break

    if not stats and count:
        stats.append(AgentSourceStat(label="records", value=_format_count(count)))

    if not bullets:
        bullets.append("工具已返回结构化数据，可展开索引查看详情。")

    summary = f"{_tool_label(tool_name)}已返回结构化结果。"
    if count:
        summary = f"{_tool_label(tool_name)}已返回结构化结果，共 {count} 条记录。"

    return AgentSourceCard(
        tool_name=tool_name,
        title=f"{_tool_label(tool_name)}数据摘要",
        summary=summary,
        count=count,
        stats=stats[:4],
        bullets=bullets[:4],
    )


def build_source_cards(selected_tools: List[str], tool_results: Dict[str, Any]) -> List[AgentSourceCard]:
    cards: List[AgentSourceCard] = []
    for tool_name in selected_tools:
        payload = tool_results.get(tool_name)
        if not isinstance(payload, dict):
            continue

        if tool_name == "get_wrong_answers":
            cards.append(_build_wrong_answer_source(tool_name, payload))
        elif tool_name == "get_learning_sessions":
            cards.append(_build_learning_sessions_source(tool_name, payload))
        elif tool_name == "get_progress_summary":
            cards.append(_build_progress_source(tool_name, payload))
        elif tool_name == "get_knowledge_mastery":
            cards.append(_build_knowledge_mastery_source(tool_name, payload))
        elif tool_name == "get_study_history":
            cards.append(_build_study_history_source(tool_name, payload))
        elif tool_name == "get_review_pressure":
            cards.append(_build_review_pressure_source(tool_name, payload))
        elif tool_name == "search_openviking_context":
            cards.append(_build_openviking_source(tool_name, payload))
        elif tool_name == "consult_openmanus":
            cards.append(_build_openmanus_source(tool_name, payload))
        else:
            cards.append(_build_generic_source(tool_name, payload))

    return cards


def _tool_label(tool_name: str) -> str:
    return TOOL_LABELS.get(tool_name, tool_name)


def _infer_time_horizon(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return "当前"
    if any(keyword in text for keyword in ["今晚", "今天", "今夜"]):
        return "今晚"
    if any(keyword in text for keyword in ["明天", "明日"]):
        return "明天"
    if any(keyword in text for keyword in ["本周", "这周", "周内"]):
        return "本周"
    if any(keyword in text for keyword in ["接下来", "未来", "后面", "近期"]):
        return "近期"
    if any(keyword in text for keyword in ["长期", "年度", "整体", "阶段"]):
        return "长期"
    return "当前"


def _infer_output_mode(message: str) -> str:
    text = (message or "").strip()
    if any(keyword in text for keyword in ["计划", "安排", "优先级", "怎么复习", "怎么学", "任务", "拆解"]):
        return "plan"
    if any(keyword in text for keyword in ["预测", "未来", "接下来", "趋势", "风险变化"]):
        return "prediction"
    if any(keyword in text for keyword in ["历史", "轨迹", "回顾", "复盘", "连续", "上传"]):
        return "history"
    if any(keyword in text for keyword in ["为什么", "哪里", "分析", "判断", "伪掌握", "掌握", "进度"]):
        return "diagnosis"
    return "answer"


def build_request_analysis(
    message: str,
    selected_tools: List[str],
    *,
    requested_tools_explicit: bool = False,
) -> Dict[str, Any]:
    clean_message = " ".join((message or "").split())
    output_mode = _infer_output_mode(clean_message)
    time_horizon = _infer_time_horizon(clean_message)
    focus_ids: List[str] = []

    if any(keyword in clean_message for keyword in ["计划", "安排", "优先级", "今晚", "今天", "明天", "拆解", "任务"]):
        focus_ids.append("planning_schedule")
    if any(keyword in clean_message for keyword in ["预测", "未来", "接下来", "趋势", "风险变化"]):
        focus_ids.append("future_forecast")
    if any(keyword in clean_message for keyword in ["掌握", "进度", "整体", "统计", "伪掌握", "知识点", "掌握度"]):
        focus_ids.append("progress_diagnosis")
    if any(keyword in clean_message for keyword in ["错题", "薄弱", "复习", "易错", "高风险", "积压", "压力"]):
        focus_ids.append("weakness_review")
    if any(keyword in clean_message for keyword in ["最近", "历史", "轨迹", "会话", "上传", "连续", "打卡"]):
        focus_ids.append("history_reconstruction")

    if any(
        keyword in clean_message
        for keyword in ["OpenViking", "openviking", "资料库", "知识库", "文档", "外部资料", "长期记忆", "上下文库"]
    ):
        focus_ids.append("external_context_search")

    if not focus_ids:
        if any(tool in selected_tools for tool in ["get_progress_summary", "get_knowledge_mastery"]):
            focus_ids.append("progress_diagnosis")
        if any(tool in selected_tools for tool in ["get_wrong_answers", "get_review_pressure"]):
            focus_ids.append("weakness_review")
        if any(tool in selected_tools for tool in ["get_learning_sessions", "get_study_history"]):
            focus_ids.append("history_reconstruction")
        if "search_openviking_context" in selected_tools:
            focus_ids.append("external_context_search")

    if output_mode == "plan" and "planning_schedule" not in focus_ids:
        focus_ids.insert(0, "planning_schedule")
    if output_mode == "prediction" and "future_forecast" not in focus_ids:
        focus_ids.insert(0, "future_forecast")
    if not focus_ids:
        focus_ids = ["progress_diagnosis"]

    focus_ids = list(dict.fromkeys(focus_ids))
    focuses = []
    for focus_id in focus_ids:
        spec = FOCUS_LIBRARY[focus_id]
        focuses.append(
            {
                "id": focus_id,
                "title": spec["title"],
                "description": spec["description"],
                "priority": spec["priority"],
                "tools": spec["tools"],
                "active_tools": [tool for tool in spec["tools"] if tool in selected_tools],
            }
        )

    tool_labels = [_tool_label(tool_name) for tool_name in selected_tools]
    output_label = OUTPUT_MODE_LABELS.get(output_mode, OUTPUT_MODE_LABELS["answer"])
    goal = f"围绕“{_shorten(clean_message, 32)}”给出基于数据的{output_label}" if clean_message else f"给出基于数据的{output_label}"

    return {
        "goal": goal,
        "message_excerpt": _shorten(clean_message, 120),
        "time_horizon": time_horizon,
        "output_mode": output_mode,
        "output_label": output_label,
        "focuses": focuses,
        "selected_tools": selected_tools,
        "selected_tool_labels": tool_labels,
        "tool_selection_mode": "explicit" if requested_tools_explicit else "auto",
    }


def _extract_action_items(assistant_text: str, limit: int = 3) -> List[str]:
    text = (assistant_text or "").strip()
    if not text:
        return []

    actions: List[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        matched = re.match(r"^(?:[-*•]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*(.+)$", clean)
        candidate = matched.group(1).strip() if matched else ""
        if candidate and len(candidate) >= 6:
            actions.append(candidate)

    if not actions:
        sentences = re.split(r"[。！？!?\n]+", text)
        for sentence in sentences:
            clean = sentence.strip(" -•\t")
            if len(clean) >= 8:
                actions.append(clean)

    unique_actions: List[str] = []
    for item in actions:
        normalized = " ".join(item.split())
        if normalized and normalized not in unique_actions:
            unique_actions.append(normalized)
        if len(unique_actions) >= limit:
            break
    return unique_actions


def _action_tool_definition_map() -> Dict[str, Any]:
    return {item.name: item for item in list_action_tool_definitions()}


def _safe_int_list(values: Any, *, limit: int) -> List[int]:
    normalized: List[int] = []
    seen: set[int] = set()
    for raw in values or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
        if len(normalized) >= limit:
            break
    return normalized


def _safe_string_list(values: Any, *, limit: int) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = " ".join(str(raw or "").split())
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
        if len(normalized) >= limit:
            break
    return normalized


def _build_action_suggestion(
    tool_name: str,
    *,
    title: str,
    summary: str,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    tool_meta = _action_tool_definition_map().get(tool_name)
    return {
        "id": f"{tool_name}-{uuid4().hex[:8]}",
        "tool_name": tool_name,
        "title": title,
        "summary": summary,
        "tool_args": _normalize_json(tool_args),
        "risk_level": getattr(tool_meta, "risk_level", "medium"),
        "requires_confirmation": bool(getattr(tool_meta, "requires_confirmation", False)),
    }


def _build_action_suggestions(
    request_analysis: Dict[str, Any],
    tool_results: Dict[str, Any],
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    output_mode = request_analysis.get("output_mode")

    wrong_answer_items = ((tool_results.get("get_wrong_answers") or {}).get("items") or [])
    active_wrong_ids = _safe_int_list(
        [item.get("id") for item in wrong_answer_items if item.get("mastery_status") == "active"],
        limit=8,
    )
    if not active_wrong_ids:
        active_wrong_ids = _safe_int_list(
            [
                item.get("id")
                for item in wrong_answer_items
                if str(item.get("mastery_status") or "").strip().lower() != "archived"
            ],
            limit=8,
        )
    archivable_wrong_ids = _safe_int_list(
        [
            item.get("id")
            for item in wrong_answer_items
            if item.get("mastery_status") == "active" and item.get("last_retry_correct") is True
        ],
        limit=6,
    )

    weak_concepts = ((tool_results.get("get_knowledge_mastery") or {}).get("weak_concepts") or [])
    weak_concept_ids = _safe_string_list([item.get("concept_id") for item in weak_concepts], limit=4)

    if active_wrong_ids:
        suggestions.append(
            _build_action_suggestion(
                "create_daily_review_paper",
                title="生成每日复习卷",
                summary=f"把当前高优先级错题先整理成一套 {min(len(active_wrong_ids), 8)} 题的复习卷，适合立刻开始复习。",
                tool_args={
                    "wrong_answer_ids": active_wrong_ids,
                    "target_count": min(max(len(active_wrong_ids), 1), 8),
                    "allow_replace": True,
                },
            )
        )

    if weak_concept_ids:
        quiz_target_count = 6 if output_mode == "plan" else 5
        suggestions.append(
            _build_action_suggestion(
                "generate_quiz_set",
                title="生成巩固题组",
                summary=f"围绕当前偏弱知识点生成 {quiz_target_count} 道练习题，适合做一轮针对性检测。",
                tool_args={
                    "concept_ids": weak_concept_ids,
                    "target_count": quiz_target_count,
                    "session_type": "practice",
                },
            )
        )
        suggestions.append(
            _build_action_suggestion(
                "update_concept_mastery",
                title="回写知识点掌握度",
                summary="把最近做题和错题结果重新汇总到知识点掌握表，先校准再做后续计划会更稳。",
                tool_args={
                    "concept_ids": weak_concept_ids,
                    "review_in_days": 3 if output_mode == "plan" else 7,
                },
            )
        )

    if archivable_wrong_ids:
        suggestions.append(
            _build_action_suggestion(
                "update_wrong_answer_status",
                title="归档已复做通过的错题",
                summary=f"这些错题最近已经复做正确，可以先预览归档动作，再决定是否执行。",
                tool_args={
                    "wrong_answer_ids": archivable_wrong_ids,
                    "target_status": "archived",
                    "reason": "最近复做正确，建议归档",
                },
            )
        )

    deduped: List[Dict[str, Any]] = []
    seen_tools: set[str] = set()
    for item in suggestions:
        tool_name = str(item.get("tool_name") or "")
        if not tool_name or tool_name in seen_tools:
            continue
        seen_tools.add(tool_name)
        deduped.append(item)
        if len(deduped) >= 3:
            break
    return deduped


def _estimate_tool_result_count(tool_name: str, tool_result: Dict[str, Any] | None) -> int:
    if not isinstance(tool_result, dict):
        return 0
    if tool_name == "consult_openmanus":
        return int(tool_result.get("count") or (1 if str(tool_result.get("answer") or "").strip() else 0))
    if tool_name == "get_knowledge_mastery":
        return int(tool_result.get("total_concepts") or 0)
    if tool_name == "get_review_pressure":
        return int(tool_result.get("current_backlog") or 0)
    if tool_name == "get_study_history":
        return int(tool_result.get("total_uploads_in_window") or 0)
    if tool_name == "get_progress_summary":
        overview = tool_result.get("overview") or {}
        return int(overview.get("total_questions") or 0)
    if "count" in tool_result:
        return int(tool_result.get("count") or 0)
    if "items" in tool_result and isinstance(tool_result.get("items"), list):
        return len(tool_result.get("items") or [])
    return 0


def _is_sparse_tool_result(tool_name: str, tool_result: Dict[str, Any] | None) -> bool:
    count = _estimate_tool_result_count(tool_name, tool_result)
    if tool_name in {"get_review_pressure", "get_knowledge_mastery", "consult_openmanus"}:
        return count <= 0
    return count < 2


def _derive_follow_up_tools(
    request_analysis: Dict[str, Any],
    tool_results: Dict[str, Any],
    executed_tools: List[str],
) -> List[Dict[str, str]]:
    if request_analysis.get("tool_selection_mode") == "explicit":
        return []

    follow_ups: List[Dict[str, str]] = []
    existing = set(executed_tools)
    output_mode = request_analysis.get("output_mode")
    focuses = request_analysis.get("focuses") or []

    for focus in focuses:
        for tool_name in focus.get("tools") or []:
            if tool_name not in existing:
                follow_ups.append(
                    {
                        "tool_name": tool_name,
                        "reason": f"补全步骤：{focus.get('title') or focus.get('id')}",
                    }
                )
                existing.add(tool_name)

    sparse_tools = {tool_name for tool_name in executed_tools if _is_sparse_tool_result(tool_name, tool_results.get(tool_name))}
    if ("get_progress_summary" in sparse_tools or "get_wrong_answers" in sparse_tools) and "get_knowledge_mastery" not in existing:
        follow_ups.append({"tool_name": "get_knowledge_mastery", "reason": "进度或错题数据偏薄，补读知识点掌握度"})
        existing.add("get_knowledge_mastery")
    if ("get_learning_sessions" in sparse_tools or "get_progress_summary" in sparse_tools) and "get_study_history" not in existing:
        follow_ups.append({"tool_name": "get_study_history", "reason": "近期轨迹不足，补读长期学习历史"})
        existing.add("get_study_history")
    if output_mode in {"plan", "prediction"} and "get_review_pressure" not in existing:
        follow_ups.append({"tool_name": "get_review_pressure", "reason": "行动方案/预测需要复习压力数据"})
        existing.add("get_review_pressure")

    deduped: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in follow_ups:
        tool_name = item["tool_name"]
        if tool_name in seen:
            continue
        seen.add(tool_name)
        deduped.append(item)
        if len(deduped) >= 4:
            break
    return deduped


def _summarize_source_cards_for_planner(source_cards: List[AgentSourceCard]) -> List[Dict[str, Any]]:
    return [
        {
            "tool_name": card.tool_name,
            "title": card.title,
            "summary": card.summary,
            "stats": [stat.model_dump(mode="json") for stat in card.stats[:3]],
            "bullets": card.bullets[:2],
        }
        for card in source_cards[:6]
    ]


def _summarize_focus_coverage(
    request_analysis: Dict[str, Any],
    source_cards: List[AgentSourceCard],
) -> List[Dict[str, Any]]:
    available_tools = {card.tool_name for card in source_cards}
    coverage: List[Dict[str, Any]] = []
    for focus in request_analysis.get("focuses") or []:
        focus_tools = list(dict.fromkeys(focus.get("tools") or []))
        covered_tools = [tool_name for tool_name in focus_tools if tool_name in available_tools]
        coverage.append(
            {
                "id": focus.get("id"),
                "title": focus.get("title") or focus.get("id") or "未命名步骤",
                "required_tools": focus_tools,
                "covered_tools": covered_tools,
                "coverage_ratio": round(len(covered_tools) / len(focus_tools), 2) if focus_tools else 1,
            }
        )
    return coverage


def _default_clarifying_questions(request_analysis: Dict[str, Any]) -> List[str]:
    output_mode = request_analysis.get("output_mode")
    questions: List[str] = []
    if output_mode == "plan":
        questions.append("你希望我优先拆解今天、明天，还是本周的复习安排？")
    elif output_mode == "prediction":
        questions.append("你想看接下来几天的风险变化，还是更长期的趋势？")
    elif output_mode == "history":
        questions.append("你这轮更想复盘上传历史、学习节奏，还是连续打卡情况？")
    else:
        questions.append("你这轮最想先解决的是总体进度、错题复习，还是复习压力？")
    questions.append("如果要我给具体建议，你更希望按优先级排序，还是按时间顺序拆解？")
    return questions[:3]


_TOPIC_QUERY_TOOLS = {
    "get_learning_sessions",
    "get_wrong_answers",
    "get_study_history",
    "get_knowledge_mastery",
}
_TOPIC_RECOMMENDED_TOOLS = [
    "get_learning_sessions",
    "get_wrong_answers",
    "get_study_history",
    "get_knowledge_mastery",
]
_TOPIC_VARIANT_GROUPS: Dict[str, List[str]] = {
    "细胞电活动": ["细胞电活动", "心肌电活动", "动作电位", "静息电位", "电生理", "兴奋性"],
    "心肌电活动": ["细胞电活动", "心肌电活动", "动作电位", "静息电位", "电生理", "兴奋性"],
    "动作电位": ["细胞电活动", "心肌电活动", "动作电位", "静息电位", "电生理", "兴奋性"],
    "静息电位": ["细胞电活动", "心肌电活动", "动作电位", "静息电位", "电生理", "兴奋性"],
}
_TOPIC_PATTERNS = [
    re.compile(r"(细胞电活动|心肌电活动|动作电位|静息电位|电生理|兴奋性)"),
    re.compile(r"(?:最近|近期|这段时间|关于|针对|对于)?([\u4e00-\u9fffA-Za-z0-9·()（）]{2,20})(?:学得|掌握|情况|这部分|这一块|这个知识点)"),
]


def _normalize_topic_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def _extract_topic_hint(db: Session, message: str) -> str:
    clean_message = _normalize_topic_text(message)
    if not clean_message:
        return ""

    entity_candidates = {
        _normalize_topic_text(title)
        for _, title in db.query(Chapter.id, Chapter.chapter_title).all()
        if _normalize_topic_text(title)
    }
    entity_candidates.update(
        {
            _normalize_topic_text(item[0])
            for item in db.query(LearningSession.knowledge_point)
            .filter(LearningSession.knowledge_point.isnot(None))
            .distinct()
            .all()
            if _normalize_topic_text(item[0])
        }
    )
    entity_candidates.update(
        {
            _normalize_topic_text(item[0])
            for item in db.query(WrongAnswerV2.key_point)
            .filter(WrongAnswerV2.key_point.isnot(None))
            .distinct()
            .all()
            if _normalize_topic_text(item[0])
        }
    )

    entity_matches = sorted(
        {
            candidate
            for candidate in entity_candidates
            if candidate in clean_message
        },
        key=len,
        reverse=True,
    )
    if entity_matches:
        return entity_matches[0]

    for pattern in _TOPIC_PATTERNS:
        match = pattern.search(clean_message)
        if not match:
            continue
        topic = _normalize_topic_text(match.group(1))
        if topic:
            return topic
    return ""


def _expand_topic_variants(topic: str) -> List[str]:
    normalized_topic = _normalize_topic_text(topic)
    if not normalized_topic:
        return []

    variants = {normalized_topic}
    for key, aliases in _TOPIC_VARIANT_GROUPS.items():
        if key in normalized_topic or normalized_topic in aliases or any(alias in normalized_topic for alias in aliases):
            variants.update(aliases)
    if "电活动" in normalized_topic or "电生理" in normalized_topic:
        variants.update(["电活动", "动作电位", "静息电位", "兴奋性", "心肌电活动"])
    return sorted({item for item in variants if item}, key=len, reverse=True)


def _derive_topic_tool_overrides(
    db: Session,
    message: str,
    selected_tools: List[str],
) -> Dict[str, Dict[str, Any]]:
    topic = _extract_topic_hint(db, message)
    if not topic:
        return {}

    topic_variants = _expand_topic_variants(topic)
    chapter_ids: List[str] = []
    seen_chapter_ids: set[str] = set()
    for chapter_id, chapter_title in db.query(Chapter.id, Chapter.chapter_title).all():
        normalized_title = _normalize_topic_text(chapter_title)
        if not normalized_title:
            continue
        if not any(variant in normalized_title or normalized_title in variant for variant in topic_variants):
            continue
        chapter_id_text = str(chapter_id or "").strip()
        if not chapter_id_text or chapter_id_text in seen_chapter_ids:
            continue
        seen_chapter_ids.add(chapter_id_text)
        chapter_ids.append(chapter_id_text)
        if len(chapter_ids) >= 6:
            break

    overrides: Dict[str, Dict[str, Any]] = {}
    for tool_name in selected_tools:
        if tool_name not in _TOPIC_QUERY_TOOLS:
            continue
        override: Dict[str, Any] = {}
        if tool_name in {"get_learning_sessions", "get_wrong_answers", "get_study_history"}:
            override["query"] = topic
        if chapter_ids:
            override["chapter_ids"] = chapter_ids
        if tool_name == "get_learning_sessions":
            override["limit"] = 8
        elif tool_name == "get_wrong_answers":
            override["limit"] = 8
        elif tool_name == "get_study_history":
            override["limit"] = 8
        elif tool_name == "get_knowledge_mastery":
            override["limit"] = 8
        overrides[tool_name] = override
    return overrides


def _extend_tools_for_topic_query(
    db: Session,
    message: str,
    selected_tools: List[str],
    *,
    requested_tools_explicit: bool,
) -> List[str]:
    if requested_tools_explicit:
        return list(selected_tools)
    topic = _extract_topic_hint(db, message)
    if not topic:
        return list(selected_tools)
    return list(dict.fromkeys(list(selected_tools) + _TOPIC_RECOMMENDED_TOOLS))


def _merge_tool_overrides(
    auto_overrides: Dict[str, Dict[str, Any]],
    payload_overrides: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for tool_name in set(auto_overrides) | set(payload_overrides):
        merged[tool_name] = {
            **auto_overrides.get(tool_name, {}),
            **payload_overrides.get(tool_name, {}),
        }
    return merged


def _looks_ambiguous(message: str) -> bool:
    clean_message = " ".join((message or "").split())
    if not clean_message:
        return True
    if len(clean_message) <= 4:
        return True

    vague_markers = ["这个", "那个", "它", "这样", "上面", "下面", "这里", "那里", "帮我看看", "怎么弄"]
    concrete_markers = [
        "进度",
        "错题",
        "复习",
        "历史",
        "计划",
        "安排",
        "预测",
        "趋势",
        "掌握",
        "压力",
        "知识点",
        "学习",
        "今天",
        "明天",
        "本周",
        "未来",
    ]
    return any(marker in clean_message for marker in vague_markers) and not any(
        marker in clean_message for marker in concrete_markers
    )


def _derive_rule_response_strategy(
    *,
    user_message: str,
    request_analysis: Dict[str, Any],
    selected_tools: List[str],
    tool_results: Dict[str, Any],
    source_cards: List[AgentSourceCard],
) -> Dict[str, Any]:
    focus_coverage = _summarize_focus_coverage(request_analysis, source_cards)
    missing_focuses = [item["title"] for item in focus_coverage if not item["covered_tools"]]
    sparse_tools = [
        tool_name
        for tool_name in selected_tools
        if _is_sparse_tool_result(tool_name, tool_results.get(tool_name))
    ]

    if _looks_ambiguous(user_message):
        return {
            "strategy": "clarify",
            "source": "rule",
            "reason": "用户诉求仍偏模糊，缺少明确的分析目标或时间范围。",
            "instruction": "先提出 1 到 3 个澄清问题，不要直接下结论；等用户补充后再给计划或判断。",
            "clarifying_questions": _default_clarifying_questions(request_analysis),
        }

    if not source_cards:
        return {
            "strategy": "answer_with_caveat",
            "source": "rule",
            "reason": "当前没有可用学习数据证据，只能先给保守回答。",
            "instruction": "先明确当前缺少结构化学习数据支撑，避免把推测说成事实；只给通用建议，并说明还需要哪些数据。",
            "clarifying_questions": [],
        }

    if missing_focuses or sparse_tools:
        reason_parts: List[str] = []
        if missing_focuses:
            reason_parts.append("未覆盖步骤：" + " / ".join(missing_focuses[:3]))
        if sparse_tools:
            reason_parts.append(
                "数据偏薄："
                + " / ".join(_tool_label(tool_name) for tool_name in sparse_tools[:3])
            )
        return {
            "strategy": "answer_with_caveat",
            "source": "rule",
            "reason": "；".join(reason_parts) or "当前证据覆盖不完整。",
            "instruction": "可以回答，但必须先说明证据边界和不确定性；结论只建立在已获取的数据上，并点明缺失数据可能改变判断。",
            "clarifying_questions": [],
        }

    return {
        "strategy": "answer",
        "source": "rule",
        "reason": "用户诉求明确，且当前数据已覆盖主要分析步骤。",
        "instruction": "直接基于当前结构化数据给出结论、依据和下一步建议；避免重复解释缺省信息。",
        "clarifying_questions": [],
    }


def _derive_rule_response_strategy(
    *,
    user_message: str,
    request_analysis: Dict[str, Any],
    selected_tools: List[str],
    tool_results: Dict[str, Any],
    source_cards: List[AgentSourceCard],
) -> Dict[str, Any]:
    focus_coverage = _summarize_focus_coverage(request_analysis, source_cards)
    missing_focuses = [item["title"] for item in focus_coverage if not item["covered_tools"]]
    sparse_tools = [
        tool_name
        for tool_name in selected_tools
        if _is_sparse_tool_result(tool_name, tool_results.get(tool_name))
    ]

    if _looks_ambiguous(user_message):
        return {
            "strategy": "clarify",
            "source": "rule",
            "reason": "用户诉求还偏模糊，缺少明确的分析目标或时间范围。",
            "instruction": CLARIFY_STYLE_GUIDANCE,
            "clarifying_questions": _default_clarifying_questions(request_analysis),
        }

    if not source_cards:
        return {
            "strategy": "answer_with_caveat",
            "source": "rule",
            "reason": "当前没有可用的学习数据证据，只能先给保守回答。",
            "instruction": NO_DATA_STYLE_GUIDANCE,
            "clarifying_questions": [],
        }

    if missing_focuses or sparse_tools:
        reason_parts: List[str] = []
        if missing_focuses:
            reason_parts.append("未覆盖步骤：" + " / ".join(missing_focuses[:3]))
        if sparse_tools:
            reason_parts.append(
                "数据偏薄：" + " / ".join(_tool_label(tool_name) for tool_name in sparse_tools[:3])
            )
        return {
            "strategy": "answer_with_caveat",
            "source": "rule",
            "reason": "；".join(reason_parts) or "当前证据覆盖还不完整。",
            "instruction": CAUTIOUS_ANSWER_STYLE_GUIDANCE,
            "clarifying_questions": [],
        }

    return {
        "strategy": "answer",
        "source": "rule",
        "reason": "用户诉求明确，且当前数据已覆盖主要分析步骤。",
        "instruction": DIRECT_ANSWER_STYLE_GUIDANCE,
        "clarifying_questions": [],
    }


async def _decide_response_strategy(
    *,
    user_message: str,
    request_analysis: Dict[str, Any],
    selected_tools: List[str],
    tool_results: Dict[str, Any],
    source_cards: List[AgentSourceCard],
    draft_plan: AgentPlanBundle,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
) -> Dict[str, Any]:
    rule_strategy = _derive_rule_response_strategy(
        user_message=user_message,
        request_analysis=request_analysis,
        selected_tools=selected_tools,
        tool_results=tool_results,
        source_cards=source_cards,
    )

    ai_client = get_ai_client()
    if not hasattr(ai_client, "generate_json"):
        return rule_strategy

    planner_prompt = "\n\n".join(
        [
            "你是学习 agent 的回答策略规划器。你的任务不是直接回答用户，而是决定这一轮应该如何回答。",
            "规则：",
            "1. strategy 只能是 answer / answer_with_caveat / clarify。",
            "2. answer 表示用户诉求清晰，且当前数据足够支撑直接作答。",
            "3. answer_with_caveat 表示可以先回答，但必须明确数据边界、缺失和不确定性。",
            "4. clarify 表示用户诉求仍不清楚，应先提出最多 3 个澄清问题，不要直接给结论。",
            "5. instruction 必须写成给回答模型的执行指令，具体、可操作。",
            "6. clarifying_questions 仅在 clarify 时填写；否则返回空数组。",
            f"[用户原始消息]\n{user_message}",
            f"[用户目标]\n{request_analysis.get('goal') or '未识别'}",
            f"[输出类型]\n{request_analysis.get('output_label') or request_analysis.get('output_mode') or '回答'}",
            f"[已选工具]\n{json.dumps(selected_tools, ensure_ascii=False)}",
            f"[焦点覆盖]\n{json.dumps(_summarize_focus_coverage(request_analysis, source_cards), ensure_ascii=False)}",
            f"[当前来源摘要]\n{json.dumps(_summarize_source_cards_for_planner(source_cards), ensure_ascii=False)}",
            f"[当前计划摘要]\n{json.dumps(draft_plan.model_dump(mode='json'), ensure_ascii=False)}",
            f"[规则基线]\n{json.dumps(rule_strategy, ensure_ascii=False)}",
        ]
    )

    try:
        planner_payload = await ai_client.generate_json(
            planner_prompt,
            schema=RESPONSE_STRATEGY_SCHEMA,
            max_tokens=700,
            temperature=0.1,
            timeout=35,
            use_heavy=False,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        strategy = str(planner_payload.get("strategy") or "").strip()
        if strategy not in {"answer", "answer_with_caveat", "clarify"}:
            raise ValueError(f"invalid response strategy: {strategy}")

        reason = str(planner_payload.get("reason") or "").strip() or rule_strategy["reason"]
        instruction = (
            str(planner_payload.get("instruction") or "").strip()
            or rule_strategy["instruction"]
        )
        style_guidance = {
            "answer": DIRECT_ANSWER_STYLE_GUIDANCE,
            "answer_with_caveat": CAUTIOUS_ANSWER_STYLE_GUIDANCE,
            "clarify": CLARIFY_STYLE_GUIDANCE,
        }.get(strategy)
        if style_guidance and style_guidance not in instruction:
            instruction = f"{instruction} {style_guidance}".strip()
        clarifying_questions: List[str] = []
        for item in planner_payload.get("clarifying_questions") or []:
            question = str(item).strip()
            if not question or question in clarifying_questions:
                continue
            clarifying_questions.append(question)
            if len(clarifying_questions) >= 3:
                break
        if strategy == "clarify" and not clarifying_questions:
            clarifying_questions = list(rule_strategy.get("clarifying_questions") or [])
        if strategy != "clarify":
            clarifying_questions = []

        return {
            "strategy": strategy,
            "source": "llm",
            "reason": reason,
            "instruction": instruction,
            "clarifying_questions": clarifying_questions,
        }
    except Exception as exc:
        return {
            **rule_strategy,
            "source": "rule_fallback",
            "planner_error": str(exc)[:160],
        }


async def _decide_follow_up_tools(
    *,
    request_analysis: Dict[str, Any],
    tool_results: Dict[str, Any],
    executed_tools: List[str],
    source_cards: List[AgentSourceCard],
    draft_plan: AgentPlanBundle,
    iteration: int,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
) -> Dict[str, Any]:
    if request_analysis.get("tool_selection_mode") == "explicit":
        return {
            "follow_ups": [],
            "source": "explicit",
            "reason": "用户显式指定了工具，本轮不自动扩展。",
        }

    rule_follow_ups = _derive_follow_up_tools(request_analysis, tool_results, executed_tools)
    candidate_tools = [item["tool_name"] for item in rule_follow_ups]
    if not candidate_tools:
        return {
            "follow_ups": [],
            "source": "rule",
            "reason": "当前没有剩余候选工具，数据面已覆盖本轮需求。",
        }

    ai_client = get_ai_client()
    if not hasattr(ai_client, "generate_json"):
        return {
            "follow_ups": rule_follow_ups,
            "source": "rule_fallback",
            "reason": "当前 AI client 不支持结构化调度，回退到规则链。",
        }

    planner_prompt = "\n\n".join(
        [
            "你是一个学习 agent 的数据调度器。你的任务不是回答用户，而是判断是否还需要继续补充数据工具。",
            "规则：",
            "1. 只能从候选工具中选择。",
            "2. 如果当前数据已经足够回答，就返回 should_continue=false。",
            "3. 最多选择 2 个工具。",
            "4. reason 必须具体说明为什么这个工具有助于当前诉求。",
            f"[当前轮次]\n第 {iteration} 轮",
            f"[用户目标]\n{request_analysis.get('goal') or '未识别'}",
            f"[输出类型]\n{request_analysis.get('output_label') or request_analysis.get('output_mode') or '回答'}",
            f"[当前已执行工具]\n{json.dumps(executed_tools, ensure_ascii=False)}",
            f"[当前来源摘要]\n{json.dumps(_summarize_source_cards_for_planner(source_cards), ensure_ascii=False)}",
            f"[当前计划摘要]\n{json.dumps(draft_plan.model_dump(mode='json'), ensure_ascii=False)}",
            f"[候选工具]\n{json.dumps(rule_follow_ups, ensure_ascii=False)}",
        ]
    )

    try:
        planner_payload = await ai_client.generate_json(
            planner_prompt,
            schema=FOLLOW_UP_PLANNER_SCHEMA,
            max_tokens=900,
            temperature=0.1,
            timeout=35,
            use_heavy=False,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        should_continue = bool(planner_payload.get("should_continue"))
        decision_reason = str(planner_payload.get("decision_reason") or "模型未提供理由。").strip()
        raw_tools = planner_payload.get("next_tools") or []
        allowed_reasons = {item["tool_name"]: item["reason"] for item in rule_follow_ups}
        valid_follow_ups: List[Dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_tools:
            tool_name = str(item.get("tool_name") or "").strip()
            if not tool_name or tool_name in seen or tool_name not in allowed_reasons:
                continue
            seen.add(tool_name)
            reason = str(item.get("reason") or "").strip() or allowed_reasons[tool_name]
            valid_follow_ups.append({"tool_name": tool_name, "reason": reason})
            if len(valid_follow_ups) >= 2:
                break

        if should_continue and valid_follow_ups:
            return {
                "follow_ups": valid_follow_ups,
                "source": "llm",
                "reason": decision_reason,
            }
        if should_continue and not valid_follow_ups:
            return {
                "follow_ups": rule_follow_ups[:2],
                "source": "llm_fallback",
                "reason": f"{decision_reason} 但模型未给出有效工具，回退规则链。",
            }
        return {
            "follow_ups": [],
            "source": "llm",
            "reason": decision_reason,
        }
    except Exception as exc:
        return {
            "follow_ups": rule_follow_ups,
            "source": "rule_fallback",
            "reason": f"模型调度失败，回退规则链: {str(exc)[:120]}",
        }


def _build_plan_trace_entry(
    *,
    iteration: int,
    request_analysis: Dict[str, Any],
    selected_tools: List[str],
    draft_plan: AgentPlanBundle,
    follow_up_specs: List[Dict[str, str]],
    decision_source: str,
    decision_reason: str,
) -> Dict[str, Any]:
    focus_statuses = [
        {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "dependencies": task.dependencies,
        }
        for task in draft_plan.tasks
        if task.id.startswith("focus-")
    ]
    return {
        "iteration": iteration,
        "goal": request_analysis.get("goal"),
        "selected_tools": selected_tools,
        "plan_summary": draft_plan.summary,
        "task_count": len(draft_plan.tasks),
        "decision_source": decision_source,
        "decision_reason": decision_reason,
        "focus_statuses": focus_statuses,
        "next_follow_ups": _normalize_json(follow_up_specs),
    }


async def _execute_tool_batch(
    *,
    db: Session,
    session: AgentSession,
    user_message: AgentMessage,
    trace_id: str,
    tool_names: List[str],
    tool_overrides: Dict[str, Dict[str, Any]],
    tool_calls: List[AgentToolCall],
    tool_results: Dict[str, Any],
    tool_run_snapshots: List[Dict[str, Any]],
    iteration: int,
    reason_map: Dict[str, str],
) -> None:
    for tool_name in tool_names:
        if tool_name in tool_results:
            continue

        overrides = dict(tool_overrides.get(tool_name, {}))
        if tool_name == "search_openviking_context" and not overrides.get("query"):
            overrides["query"] = user_message.content
        if tool_name == "consult_openmanus" and not overrides.get("query"):
            overrides["query"] = user_message.content
        try:
            cache_entry = get_cached_tool_result(
                db,
                session_id=session.id,
                tool_name=tool_name,
                tool_args=overrides,
            )
            if cache_entry is not None:
                tool_args = cache_entry.tool_args or overrides
                tool_result = cache_entry.tool_result or {}
                duration_ms = 0
                cache_hit = True
            else:
                tool_args, tool_result, duration_ms = await execute_agent_tool(
                    tool_name=tool_name,
                    db=db,
                    overrides=overrides,
                    user_id=session.user_id,
                    device_id=session.device_id,
                )
                store_tool_cache_result(
                    db,
                    session_id=session.id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=tool_result,
                    trace_id=trace_id,
                )
                cache_hit = False

            tool_call = AgentToolCall(
                session_id=session.id,
                message_id=int(user_message.id),
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                success=True,
                duration_ms=duration_ms,
            )
            tool_results[tool_name] = tool_result
            tool_run_snapshots.append(
                {
                    "tool_name": tool_name,
                    "status": "cached" if cache_hit else "completed",
                    "cache_hit": cache_hit,
                    "duration_ms": duration_ms,
                    "tool_args": _normalize_json(tool_args),
                    "result_count": _estimate_tool_result_count(tool_name, tool_result),
                    "iteration": iteration,
                    "reason": reason_map.get(tool_name) or "执行工具",
                }
            )
        except Exception as exc:
            tool_call = AgentToolCall(
                session_id=session.id,
                message_id=int(user_message.id),
                tool_name=tool_name,
                tool_args=overrides,
                tool_result=None,
                success=False,
                error_message=str(exc)[:500],
                duration_ms=0,
            )
            tool_run_snapshots.append(
                {
                    "tool_name": tool_name,
                    "status": "failed",
                    "cache_hit": False,
                    "duration_ms": 0,
                    "tool_args": _normalize_json(overrides),
                    "error_message": str(exc)[:500],
                    "iteration": iteration,
                    "reason": reason_map.get(tool_name) or "执行工具",
                }
            )

        db.add(tool_call)
        db.flush()
        tool_calls.append(tool_call)


def _focus_status(available_cards: List[AgentSourceCard], assistant_status: str) -> str:
    if available_cards:
        return "completed"
    if assistant_status == "error":
        return "failed"
    return "in-progress" if assistant_status == "pending" else "pending"


def _build_focus_subtasks(
    focus_id: str,
    focus_tools: List[str],
    source_card_map: Dict[str, AgentSourceCard],
    assistant_status: str,
) -> List[AgentPlanSubtask]:
    subtasks: List[AgentPlanSubtask] = []
    for index, tool_name in enumerate(focus_tools):
        card = source_card_map.get(tool_name)
        if card is None:
            subtasks.append(
                AgentPlanSubtask(
                    id=f"{focus_id}-{tool_name}-pending",
                    title=f"等待读取{_tool_label(tool_name)}",
                    description=f"这一步需要读取 {_tool_label(tool_name)} 的结构化数据后才能继续判断。",
                    status="failed" if assistant_status == "error" else "in-progress",
                    priority="high" if index == 0 else "medium",
                    tools=[tool_name],
                )
            )
            continue

        stat_preview = "，".join(f"{stat.label}{stat.value}" for stat in card.stats[:2])
        details = f"{card.summary} 关键指标：{stat_preview}。" if stat_preview else card.summary
        subtasks.append(
            AgentPlanSubtask(
                id=f"{focus_id}-{tool_name}",
                title=f"读取{card.title}",
                description=details,
                status="completed",
                priority="high" if index == 0 else "medium",
                tools=[tool_name],
            )
        )

    return subtasks


def build_plan_bundle(
    request_analysis: Dict[str, Any],
    source_cards: List[AgentSourceCard],
    assistant_text: str = "",
    assistant_status: str = "pending",
) -> AgentPlanBundle:
    source_card_map = {card.tool_name: card for card in source_cards}
    focuses = request_analysis.get("focuses") or []
    goal = request_analysis.get("goal") or "围绕当前问题生成基于数据的回答"
    time_horizon = request_analysis.get("time_horizon") or "当前"
    output_label = request_analysis.get("output_label") or OUTPUT_MODE_LABELS["answer"]
    selected_tool_labels = request_analysis.get("selected_tool_labels") or []

    tasks: List[AgentPlanTask] = [
        AgentPlanTask(
            id="request-scope",
            title="锁定本轮诉求",
            description=goal,
            status="completed",
            priority="high",
            level=0,
            subtasks=[
                AgentPlanSubtask(
                    id="request-scope-message",
                    title="核心问题",
                    description=request_analysis.get("message_excerpt") or "用户未提供明确问题。",
                    status="completed",
                    priority="high",
                    tools=[],
                ),
                AgentPlanSubtask(
                    id="request-scope-output",
                    title=f"目标产物：{output_label}",
                    description=f"这轮需要输出的是{output_label}，时间范围聚焦在{time_horizon}。",
                    status="completed",
                    priority="medium",
                    tools=[],
                ),
                AgentPlanSubtask(
                    id="request-scope-tools",
                    title="已选数据面",
                    description=" / ".join(selected_tool_labels) if selected_tool_labels else "当前尚未选定数据来源。",
                    status="completed" if selected_tool_labels else "pending",
                    priority="medium",
                    tools=request_analysis.get("selected_tools") or [],
                ),
            ],
        )
    ]

    focus_task_ids: List[str] = []
    for index, focus in enumerate(focuses):
        focus_tools = list(dict.fromkeys(focus.get("tools") or []))
        available_cards = [source_card_map[tool_name] for tool_name in focus_tools if tool_name in source_card_map]
        task_id = f"focus-{focus['id']}"
        focus_task_ids.append(task_id)
        tasks.append(
            AgentPlanTask(
                id=task_id,
                title=focus.get("title") or f"分析步骤 {index + 1}",
                description=focus.get("description") or "基于数据完成当前分析步骤。",
                status=_focus_status(available_cards, assistant_status),
                priority=focus.get("priority") or "medium",
                level=0,
                dependencies=["request-scope"],
                subtasks=_build_focus_subtasks(focus.get("id") or f"focus-{index}", focus_tools, source_card_map, assistant_status),
            )
        )

    action_items = _extract_action_items(assistant_text, limit=3)
    synthesis_status = {
        "completed": "completed",
        "error": "failed",
        "pending": "in-progress",
    }.get(assistant_status, "in-progress")

    synthesis_subtasks = [
        AgentPlanSubtask(
            id="agent-synthesis-proof",
            title="汇总数据证据",
            description=f"把前面步骤中的数据证据压成统一判断，并对齐“{request_analysis.get('message_excerpt') or '当前问题'}”。",
            status="completed" if source_cards else ("failed" if assistant_status == "error" else "in-progress"),
            priority="high",
            tools=[card.tool_name for card in source_cards],
        )
    ]

    if action_items:
        for index, item in enumerate(action_items):
            synthesis_subtasks.append(
                AgentPlanSubtask(
                    id=f"agent-synthesis-action-{index}",
                    title=_shorten(item, limit=30),
                    description=item,
                    status="completed" if synthesis_status == "completed" else synthesis_status,
                    priority="high" if index == 0 else "medium",
                    tools=["agent"],
                )
            )
    else:
        synthesis_subtasks.append(
            AgentPlanSubtask(
                id="agent-synthesis-action-0",
                title=f"等待输出{output_label}",
                description=f"当前正在根据已加载数据组织{output_label}。",
                status=synthesis_status,
                priority="high",
                tools=["agent"],
            )
        )

    if synthesis_status == "completed":
        summary = f"已按“{request_analysis.get('message_excerpt') or '当前问题'}”完成 {len(focuses)} 个分析步骤，核对 {len(source_cards)} 个数据来源，并生成 {max(len(action_items), 1)} 条结果。"
    elif synthesis_status == "failed":
        summary = f"已完成诉求拆解和数据读取，但本轮{output_label}生成异常。"
    else:
        summary = f"已按“{request_analysis.get('message_excerpt') or '当前问题'}”拆出 {1 + len(focuses)} 个步骤，正在整理{output_label}。"

    synthesis_task = AgentPlanTask(
        id="agent-synthesis",
        title=f"输出{output_label}",
        description=_shorten(assistant_text, limit=120) if assistant_text.strip() else f"把前面证据转成{output_label}，并对齐{time_horizon}场景。",
        status=synthesis_status,
        priority="high",
        level=1,
        dependencies=["request-scope", *focus_task_ids],
        subtasks=synthesis_subtasks,
    )

    return AgentPlanBundle(summary=summary, tasks=[*tasks, synthesis_task])


def _serialize_source_cards(source_cards: List[AgentSourceCard]) -> List[Dict[str, Any]]:
    return [card.model_dump(mode="json") for card in source_cards]


def _serialize_plan_bundle(plan_bundle: AgentPlanBundle) -> Dict[str, Any]:
    return plan_bundle.model_dump(mode="json")


def _assistant_content_structured(
    prepared_turn: PreparedChatTurn,
    plan_bundle: AgentPlanBundle,
    execution_state: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "selected_tools": prepared_turn.selected_tools,
        "request_analysis": prepared_turn.request_analysis,
        "response_strategy": prepared_turn.response_strategy,
        "context_usage": prepared_turn.context_usage.model_dump(mode="json"),
        "memories": prepared_turn.context.get("retrieved_memories") or [],
        "sources": _serialize_source_cards(prepared_turn.source_cards),
        "plan": _serialize_plan_bundle(plan_bundle),
        "action_suggestions": prepared_turn.action_suggestions,
        "execution_state": execution_state,
        "turn_state_id": int(prepared_turn.turn_state.id),
    }


def _resolve_session_for_payload(db: Session, payload: AgentChatRequest) -> AgentSession:
    _require_actor_identity(payload.user_id, payload.device_id)
    if payload.session_id:
        session = get_session_for_actor_or_none(
            db,
            payload.session_id,
            user_id=payload.user_id,
            device_id=payload.device_id,
        )
        if session is None:
            raise AgentSessionNotFoundError(AGENT_SESSION_NOT_FOUND)
    else:
        stored_user_id, stored_device_id = canonicalize_storage_identity(payload.user_id, payload.device_id)
        create_payload = AgentSessionCreateRequest(
            user_id=stored_user_id,
            device_id=stored_device_id,
            title=_title_from_message(payload.message),
            agent_type=payload.agent_type,
            model=payload.model,
            provider=payload.provider,
            prompt_template_id=payload.prompt_template_id,
        )
        deterministic_session_id = _deterministic_session_id_for_payload(payload)
        if not deterministic_session_id:
            session = create_session(db, create_payload)
        else:
            session = get_session_for_actor_or_none(
                db,
                deterministic_session_id,
                user_id=payload.user_id,
                device_id=payload.device_id,
            )
            if session is None:
                resolved_provider, resolved_model = _resolved_agent_model(
                    create_payload.provider,
                    create_payload.model,
                )
                template_seed = create_payload.prompt_template_id or _default_prompt_template_for_agent_type(
                    create_payload.agent_type
                )
                template_id, _ = resolve_prompt_template(create_payload.agent_type, template_seed)
                session = AgentSession(
                    id=deterministic_session_id,
                    user_id=stored_user_id,
                    device_id=stored_device_id,
                    title=_default_title(create_payload.title),
                    agent_type=create_payload.agent_type,
                    status="active",
                    model=resolved_model,
                    provider=resolved_provider,
                    prompt_template_id=template_id,
                )
                db.add(session)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    session = get_session_for_actor_or_none(
                        db,
                        deterministic_session_id,
                        user_id=payload.user_id,
                        device_id=payload.device_id,
                    )
                    if session is None:
                        raise AgentDuplicateRequestInProgressError(AGENT_DUPLICATE_REQUEST_IN_PROGRESS)
                except OperationalError as exc:
                    db.rollback()
                    if not _is_retryable_sqlite_lock_error(exc):
                        raise

                    session = None
                    for _ in range(30):
                        session = get_session_for_actor_or_none(
                            db,
                            deterministic_session_id,
                            user_id=payload.user_id,
                            device_id=payload.device_id,
                        )
                        if session is not None:
                            break
                        sleep(0.05)

                    if session is None:
                        raise AgentDuplicateRequestInProgressError(AGENT_DUPLICATE_REQUEST_IN_PROGRESS) from exc
                else:
                    db.refresh(session)

    _apply_session_runtime_defaults(session)

    if payload.prompt_template_id:
        session.prompt_template_id, _ = resolve_prompt_template(session.agent_type, payload.prompt_template_id)
    elif session.agent_type == "tutor" and session.prompt_template_id == "tutor.v1":
        session.prompt_template_id, _ = resolve_prompt_template(session.agent_type, DEFAULT_TUTOR_TEMPLATE)
    if payload.device_id and not session.device_id:
        session.device_id = payload.device_id
    if payload.user_id and not session.user_id:
        session.user_id = payload.user_id
    session.provider, session.model = _resolved_agent_model(
        payload.provider if payload.provider and payload.provider != "auto" else session.provider,
        payload.model if payload.model and payload.model != "auto" else session.model,
    )

    return session


async def prepare_chat_turn(db: Session, payload: AgentChatRequest) -> PreparedChatTurn:
    ensure_agent_schema()
    session = _resolve_session_for_payload(db, payload)
    llm_options = _session_model_options(session)
    trace_id = payload.client_request_id or uuid4().hex
    logger.info("[AGENT_SESSION] chat turn session_id=%s trace_id=%s", session.id, trace_id)
    reserved_request = False

    if payload.client_request_id:
        existing_response = _get_existing_chat_response(db, session=session, trace_id=trace_id)
        if existing_response is not None:
            raise AgentDuplicateResponseAvailableError(existing_response)
        if not _reserve_chat_request(session.id, trace_id):
            existing_response = _get_existing_chat_response(db, session=session, trace_id=trace_id)
            if existing_response is not None:
                raise AgentDuplicateResponseAvailableError(existing_response)
            raise AgentDuplicateRequestInProgressError(AGENT_DUPLICATE_REQUEST_IN_PROGRESS)
        reserved_request = True
        if _get_turn_state_by_trace_id(db, session.id, trace_id) is not None:
            _release_chat_request(session.id, trace_id)
            reserved_request = False
            existing_response = _get_existing_chat_response(db, session=session, trace_id=trace_id)
            if existing_response is not None:
                raise AgentDuplicateResponseAvailableError(existing_response)
            raise AgentDuplicateRequestInProgressError(AGENT_DUPLICATE_REQUEST_IN_PROGRESS)

    user_message = AgentMessage(
        session_id=session.id,
        role="user",
        content=payload.message,
        content_structured={
            "requested_tools": payload.requested_tools,
            "client_request_id": payload.client_request_id,
        },
        message_status="completed",
        trace_id=trace_id,
    )
    db.add(user_message)
    try:
        db.flush()
    except IntegrityError:
        _raise_duplicate_chat_conflict(
            db,
            session_id=session.id,
            trace_id=trace_id,
            user_id=payload.user_id,
            device_id=payload.device_id,
        )

    initial_selected_tools = resolve_requested_tools(payload.message, payload.requested_tools)
    initial_selected_tools = _extend_tools_for_topic_query(
        db,
        payload.message,
        initial_selected_tools,
        requested_tools_explicit=bool(payload.requested_tools),
    )
    auto_tool_overrides = _derive_topic_tool_overrides(db, payload.message, initial_selected_tools)
    effective_tool_overrides = _merge_tool_overrides(auto_tool_overrides, payload.tool_overrides)
    request_analysis = build_request_analysis(
        payload.message,
        initial_selected_tools,
        requested_tools_explicit=bool(payload.requested_tools),
    )
    user_message.content_structured = {
        "requested_tools": payload.requested_tools,
        "client_request_id": payload.client_request_id,
        "selected_tools": initial_selected_tools,
        "tool_overrides": effective_tool_overrides,
        "request_analysis": request_analysis,
    }
    tool_calls: List[AgentToolCall] = []
    tool_results: Dict[str, Any] = {}
    tool_run_snapshots: List[Dict[str, Any]] = []
    planning_trace: List[Dict[str, Any]] = []
    pending_specs = [{"tool_name": tool_name, "reason": "初始检索"} for tool_name in initial_selected_tools]
    selected_tools = list(initial_selected_tools)
    source_cards: List[AgentSourceCard] = []
    draft_plan = build_plan_bundle(request_analysis, source_cards, assistant_status="pending")
    iteration = 1
    max_iterations = 1 if payload.requested_tools else MAX_AUTO_TOOL_ITERATIONS

    while pending_specs and iteration <= max_iterations:
        current_tool_names = [item["tool_name"] for item in pending_specs]
        await _execute_tool_batch(
            db=db,
            session=session,
            user_message=user_message,
            trace_id=trace_id,
            tool_names=current_tool_names,
            tool_overrides=effective_tool_overrides,
            tool_calls=tool_calls,
            tool_results=tool_results,
            tool_run_snapshots=tool_run_snapshots,
            iteration=iteration,
            reason_map={item["tool_name"]: item["reason"] for item in pending_specs},
        )

        executed_tools = list(dict.fromkeys([item["tool_name"] for item in tool_run_snapshots]))
        selected_tools = list(dict.fromkeys(selected_tools + executed_tools))
        request_analysis = build_request_analysis(
            payload.message,
            selected_tools,
            requested_tools_explicit=bool(payload.requested_tools),
        )
        source_cards = build_source_cards(selected_tools, tool_results)
        draft_plan = build_plan_bundle(request_analysis, source_cards, assistant_status="pending")
        planner_decision = await _decide_follow_up_tools(
            request_analysis=request_analysis,
            tool_results=tool_results,
            executed_tools=selected_tools,
            source_cards=source_cards,
            draft_plan=draft_plan,
            iteration=iteration,
            preferred_provider=llm_options["preferred_provider"],
            preferred_model=llm_options["preferred_model"],
        )
        next_follow_ups = planner_decision["follow_ups"]
        planning_trace.append(
            _build_plan_trace_entry(
                iteration=iteration,
                request_analysis=request_analysis,
                selected_tools=selected_tools,
                draft_plan=draft_plan,
                follow_up_specs=next_follow_ups,
                decision_source=planner_decision["source"],
                decision_reason=planner_decision["reason"],
            )
        )

        if not next_follow_ups:
            pending_specs = []
            break

        pending_specs = [item for item in next_follow_ups if item["tool_name"] not in selected_tools]
        if not pending_specs:
            break
        iteration += 1

    user_message.content_structured = {
        "requested_tools": payload.requested_tools,
        "client_request_id": payload.client_request_id,
        "selected_tools": selected_tools,
        "tool_overrides": effective_tool_overrides,
        "request_analysis": request_analysis,
    }
    response_strategy = await _decide_response_strategy(
        user_message=payload.message,
        request_analysis=request_analysis,
        selected_tools=selected_tools,
        tool_results=tool_results,
        source_cards=source_cards,
        draft_plan=draft_plan,
        preferred_provider=llm_options["preferred_provider"],
        preferred_model=llm_options["preferred_model"],
    )
    action_suggestions = _build_action_suggestions(request_analysis, tool_results)
    user_message.content_structured = {
        **(user_message.content_structured or {}),
        "response_strategy": response_strategy,
    }
    initial_execution_state = _build_execution_state(
        trace_id=trace_id,
        request_analysis=request_analysis,
        response_strategy=response_strategy,
        selected_tools=selected_tools,
        tool_run_snapshots=tool_run_snapshots,
        plan_bundle=draft_plan,
        planning_trace=planning_trace,
        stage="prepared",
    )
    turn_state = AgentTurnState(
        session_id=session.id,
        user_message_id=int(user_message.id),
        trace_id=trace_id,
        status="prepared",
        goal=request_analysis.get("goal"),
        request_analysis=_normalize_json(request_analysis),
        selected_tools=selected_tools,
        tool_snapshots=_normalize_json(tool_run_snapshots),
        plan_draft=draft_plan.model_dump(mode="json"),
        execution_state=initial_execution_state,
    )
    db.add(turn_state)
    try:
        db.flush()
    except IntegrityError:
        _raise_duplicate_chat_conflict(
            db,
            session_id=session.id,
            trace_id=trace_id,
            user_id=payload.user_id,
            device_id=payload.device_id,
        )
    user_message.content_structured = {
        **(user_message.content_structured or {}),
        "turn_state_id": int(turn_state.id),
        "execution_state": initial_execution_state,
    }
    context = build_agent_context(
        db=db,
        session=session,
        current_message=user_message,
        relevant_learning_data={
            "selected_tools": selected_tools,
            "tool_results": tool_results,
            "tool_run_snapshots": tool_run_snapshots,
        },
        request_analysis=request_analysis,
        draft_plan=draft_plan.model_dump(mode="json"),
        response_strategy=response_strategy,
    )
    context_usage = AgentContextUsage(**context["context_usage"])
    initial_execution_state = _build_execution_state(
        trace_id=trace_id,
        request_analysis=request_analysis,
        response_strategy=response_strategy,
        selected_tools=selected_tools,
        tool_run_snapshots=tool_run_snapshots,
        plan_bundle=draft_plan,
        planning_trace=planning_trace,
        stage="prepared",
        context_usage=context_usage,
    )
    turn_state.execution_state = initial_execution_state
    user_message.content_structured = {
        **(user_message.content_structured or {}),
        "execution_state": initial_execution_state,
        "turn_state_id": int(turn_state.id),
        "response_strategy": response_strategy,
    }

    return PreparedChatTurn(
        session=session,
        user_message=user_message,
        tool_calls=tool_calls,
        selected_tools=selected_tools,
        tool_results=tool_results,
        request_analysis=request_analysis,
        response_strategy=response_strategy,
        tool_run_snapshots=tool_run_snapshots,
        planning_trace=planning_trace,
        turn_state=turn_state,
        context=context,
        context_usage=context_usage,
        trace_id=trace_id,
        source_cards=source_cards,
        draft_plan=draft_plan,
        action_suggestions=action_suggestions,
    )


async def stream_chat_turn(prepared_turn: PreparedChatTurn) -> AsyncIterator[str]:
    llm_options = _session_model_options(prepared_turn.session)
    async for chunk in get_ai_client().generate_content_stream(
        prepared_turn.context["compiled_prompt"],
        max_tokens=1800,
        temperature=0.3,
        timeout=90,
        use_heavy=False,
        preferred_provider=llm_options["preferred_provider"],
        preferred_model=llm_options["preferred_model"],
    ):
        yield chunk


def finalize_chat_turn(
    db: Session,
    prepared_turn: PreparedChatTurn,
    assistant_text: str,
    *,
    assistant_status: str,
    latency_ms: int,
    error_message: str | None = None,
) -> AgentChatResponse:
    safe_text = redact_sensitive_output(assistant_text or "").strip()
    if not safe_text:
        safe_text = "当前模型暂时不可用，请稍后重试。"
    plan_bundle = build_plan_bundle(
        prepared_turn.request_analysis,
        prepared_turn.source_cards,
        safe_text,
        assistant_status=assistant_status,
    )
    execution_state = _build_execution_state(
        trace_id=prepared_turn.trace_id,
        request_analysis=prepared_turn.request_analysis,
        response_strategy=prepared_turn.response_strategy,
        selected_tools=prepared_turn.selected_tools,
        tool_run_snapshots=prepared_turn.tool_run_snapshots,
        plan_bundle=plan_bundle,
        planning_trace=prepared_turn.planning_trace,
        stage="completed" if assistant_status == "completed" else "error",
        error_message=error_message,
        context_usage=prepared_turn.context_usage,
    )

    assistant_message = AgentMessage(
        session_id=prepared_turn.session.id,
        role="assistant",
        content=safe_text,
        content_structured=_assistant_content_structured(prepared_turn, plan_bundle, execution_state),
        message_status=assistant_status,
        token_input=prepared_turn.context_usage.total_estimated_tokens,
        token_output=estimate_tokens(safe_text),
        latency_ms=latency_ms,
        trace_id=prepared_turn.trace_id,
    )
    db.add(assistant_message)

    if prepared_turn.session.title == "新会话":
        prepared_turn.session.title = _title_from_message(prepared_turn.user_message.content)
    prepared_turn.session.last_message_at = datetime.now()
    prepared_turn.session.updated_at = datetime.now()

    db.flush()
    prepared_turn.turn_state.assistant_message_id = int(assistant_message.id)
    prepared_turn.turn_state.status = "completed" if assistant_status == "completed" else "error"
    prepared_turn.turn_state.tool_snapshots = _normalize_json(prepared_turn.tool_run_snapshots)
    prepared_turn.turn_state.plan_final = plan_bundle.model_dump(mode="json")
    prepared_turn.turn_state.execution_state = execution_state
    prepared_turn.turn_state.error_message = error_message
    prepared_turn.turn_state.updated_at = datetime.now()
    prepared_turn.user_message.content_structured = {
        **(prepared_turn.user_message.content_structured or {}),
        "execution_state": execution_state,
        "turn_state_id": int(prepared_turn.turn_state.id),
        "response_strategy": prepared_turn.response_strategy,
    }

    if assistant_status == "completed" and prepared_turn.action_suggestions:
        from services.agent_tasks import ensure_task_from_turn

        ensure_task_from_turn(
            db,
            session=prepared_turn.session,
            turn_state=prepared_turn.turn_state,
            plan_bundle=plan_bundle.model_dump(mode="json"),
            action_suggestions=prepared_turn.action_suggestions,
            auto_commit=False,
        )

    store_long_term_memories(
        db,
        session=prepared_turn.session,
        message=prepared_turn.user_message,
        request_analysis=prepared_turn.request_analysis,
    )

    completed_messages = list_messages(db, prepared_turn.session.id, limit=100)
    if len(completed_messages) % 6 == 0:
        refresh_session_summary(db, prepared_turn.session)

    db.commit()
    db.refresh(prepared_turn.session)
    db.refresh(prepared_turn.user_message)
    db.refresh(assistant_message)
    db.refresh(prepared_turn.turn_state)
    _release_chat_request(prepared_turn.session.id, prepared_turn.trace_id)

    return AgentChatResponse(
        session=serialize_session(db, prepared_turn.session),
        user_message=serialize_message(prepared_turn.user_message),
        assistant_message=serialize_message(assistant_message),
        tool_calls=[serialize_tool_call(tool_call) for tool_call in prepared_turn.tool_calls],
        context_usage=prepared_turn.context_usage,
        trace_id=prepared_turn.trace_id,
        error_message=error_message,
    )


async def run_chat(db: Session, payload: AgentChatRequest) -> AgentChatResponse:
    try:
        prepared_turn = await prepare_chat_turn(db, payload)
    except AgentDuplicateResponseAvailableError as exc:
        return exc.response
    except AgentDuplicateRequestInProgressError:
        if not payload.client_request_id:
            raise
        session = _resolve_session_for_payload(db, payload)
        reused_response = await _wait_for_existing_chat_response(
            db,
            session=session,
            trace_id=payload.client_request_id,
        )
        if reused_response is not None:
            return reused_response
        raise AgentDuplicateRequestInProgressError(AGENT_DUPLICATE_REQUEST_IN_PROGRESS)
    except Exception:
        if payload.client_request_id:
            try:
                session = _resolve_session_for_payload(db, payload)
            except Exception:
                session = None
            if session is not None:
                _release_chat_request(session.id, payload.client_request_id)
        raise

    llm_options = _session_model_options(prepared_turn.session)
    assistant_text = ""
    error_message: Optional[str] = None
    assistant_status = "completed"
    started = perf_counter()

    try:
        assistant_text = await get_ai_client().generate_content(
            prepared_turn.context["compiled_prompt"],
            max_tokens=1800,
            temperature=0.3,
            timeout=90,
            use_heavy=False,
            preferred_provider=llm_options["preferred_provider"],
            preferred_model=llm_options["preferred_model"],
        )
    except Exception as exc:
        error_message = str(exc)[:500]
        assistant_status = "error"

    latency_ms = int((perf_counter() - started) * 1000)
    return finalize_chat_turn(
        db,
        prepared_turn,
        assistant_text,
        assistant_status=assistant_status,
        latency_ms=latency_ms,
        error_message=error_message,
    )
