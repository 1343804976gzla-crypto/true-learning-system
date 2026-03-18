from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.orm import Session

from agent_models import AgentActionLog, AgentSession, AgentTask, AgentTaskEvent, AgentTurnState
from services.agent_runtime import AGENT_IDENTITY_REQUIRED, AgentIdentityRequiredError, ensure_agent_schema
from utils.agent_contracts import (
    AgentActionLogItem,
    AgentTaskCreateRequest,
    AgentTaskDetailResponse,
    AgentTaskEventItem,
    AgentTaskItem,
    AgentTaskListResponse,
    AgentTaskStatusUpdateRequest,
    TaskStatus,
)


class AgentTaskNotFoundError(ValueError):
    pass


_TASK_TRANSITIONS: Dict[str, List[str]] = {
    "pending": ["ready", "cancelled"],
    "ready": ["running", "paused", "failed", "cancelled"],
    "running": ["verifying", "paused", "failed", "cancelled"],
    "verifying": ["completed", "failed", "paused"],
    "paused": ["ready", "running", "cancelled"],
    "failed": ["ready", "cancelled"],
    "completed": [],
    "cancelled": [],
}
_ACTIVE_TASK_STATUSES = {"ready", "running", "verifying", "paused", "failed"}


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _normalize_text(value: Any, *, fallback: str = "") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _normalize_plan_bundle(plan_bundle: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(plan_bundle or {})
    tasks = payload.get("tasks")
    payload["tasks"] = list(tasks) if isinstance(tasks, list) else []
    payload["summary"] = _normalize_text(payload.get("summary"))
    return payload


def _normalize_action_approval_status(value: Any, *, requires_confirmation: bool) -> str:
    candidate = _normalize_text(value).lower()
    if candidate in {"auto", "pending", "approved", "rejected"}:
        return candidate
    return "pending" if requires_confirmation else "auto"


def _normalize_action_execution_status(value: Any) -> str:
    candidate = _normalize_text(value).lower()
    if candidate in {"pending", "success", "failed", "rolled_back"}:
        return candidate
    return "pending"


def _normalize_action_verification_status(value: Any) -> str | None:
    candidate = _normalize_text(value).lower()
    if candidate in {"verified", "mismatch", "skipped", "failed"}:
        return candidate
    return None


def _normalize_action_suggestions(action_suggestions: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for index, raw_item in enumerate(list(action_suggestions or []), start=1):
        item = dict(raw_item or {})
        tool_name = _normalize_text(item.get("tool_name")).lower()
        if not tool_name:
            continue
        requires_confirmation = bool(item.get("requires_confirmation"))
        normalized.append(
            {
                "id": _normalize_text(item.get("id")) or f"{tool_name}-{index}",
                "tool_name": tool_name,
                "title": _normalize_text(item.get("title")) or tool_name,
                "summary": _normalize_text(item.get("summary")) or None,
                "tool_args": dict(item.get("tool_args") or {}) if isinstance(item.get("tool_args"), dict) else {},
                "risk_level": _normalize_text(item.get("risk_level"), fallback="medium").lower() or "medium",
                "requires_confirmation": requires_confirmation,
                "related_action_id": _normalize_text(item.get("related_action_id")) or None,
                "approval_status": _normalize_action_approval_status(
                    item.get("approval_status"),
                    requires_confirmation=requires_confirmation,
                ),
                "execution_status": _normalize_action_execution_status(item.get("execution_status")),
                "verification_status": _normalize_action_verification_status(item.get("verification_status")),
                "preview_summary": _normalize_text(item.get("preview_summary")) or _normalize_text(item.get("summary")) or None,
                "affected_ids": list(item.get("affected_ids") or []),
                "error_message": _normalize_text(item.get("error_message")) or None,
                "confirmed_at": _normalize_text(item.get("confirmed_at")) or None,
                "executed_at": _normalize_text(item.get("executed_at")) or None,
                "updated_at": _normalize_text(item.get("updated_at")) or None,
            }
        )
    return normalized


def _task_action_progress(action_suggestions: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(action_suggestions)
    completed = 0
    failed = 0
    rolled_back = 0
    previewed = 0
    latest_action_at: str | None = None

    for item in action_suggestions:
        execution_status = str(item.get("execution_status") or "pending")
        verification_status = str(item.get("verification_status") or "")
        related_action_id = _normalize_text(item.get("related_action_id"))
        updated_at = _normalize_text(item.get("updated_at")) or _normalize_text(item.get("executed_at")) or _normalize_text(item.get("confirmed_at"))
        if updated_at and (latest_action_at is None or updated_at > latest_action_at):
            latest_action_at = updated_at

        if execution_status == "success" and verification_status == "verified":
            completed += 1
        elif execution_status == "failed":
            failed += 1
        elif execution_status == "rolled_back":
            rolled_back += 1
        elif related_action_id:
            previewed += 1

    pending = max(total - completed - failed - rolled_back - previewed, 0)
    return {
        "suggested_action_count": total,
        "pending_action_count": pending,
        "previewed_action_count": previewed,
        "completed_action_count": completed,
        "failed_action_count": failed,
        "rolled_back_action_count": rolled_back,
        "latest_action_at": latest_action_at,
    }


def _plan_progress(plan_bundle: Dict[str, Any]) -> Dict[str, int]:
    tasks = list(plan_bundle.get("tasks") or [])
    task_count = len(tasks)
    completed_task_count = 0
    subtask_count = 0
    completed_subtask_count = 0

    for task in tasks:
        if str((task or {}).get("status") or "") == "completed":
            completed_task_count += 1
        subtasks = list((task or {}).get("subtasks") or [])
        subtask_count += len(subtasks)
        completed_subtask_count += sum(1 for subtask in subtasks if str((subtask or {}).get("status") or "") == "completed")

    return {
        "task_count": task_count,
        "completed_task_count": completed_task_count,
        "subtask_count": subtask_count,
        "completed_subtask_count": completed_subtask_count,
    }


def _available_transitions(status: str) -> List[TaskStatus]:
    return list(_TASK_TRANSITIONS.get(status, []))  # type: ignore[return-value]


def _serialize_task_event(event: AgentTaskEvent) -> AgentTaskEventItem:
    return AgentTaskEventItem(
        id=int(event.id),
        task_id=event.task_id,
        session_id=event.session_id,
        event_type=event.event_type,
        from_status=event.from_status,
        to_status=event.to_status,
        note=event.note,
        payload=event.payload or {},
        created_at=_iso_datetime(event.created_at) or datetime.now().isoformat(),
    )


def serialize_task(task: AgentTask) -> AgentTaskItem:
    plan_bundle = _normalize_plan_bundle(task.plan_bundle or {})
    action_suggestions = _normalize_action_suggestions(task.action_suggestions or [])
    progress = _plan_progress(plan_bundle)
    action_progress = _task_action_progress(action_suggestions)
    return AgentTaskItem(
        id=task.id,
        session_id=task.session_id,
        user_id=task.user_id,
        device_id=task.device_id,
        related_turn_state_id=int(task.related_turn_state_id) if task.related_turn_state_id is not None else None,
        title=task.title,
        goal=task.goal,
        status=task.status,
        priority=task.priority,
        source=task.source,
        plan_summary=task.plan_summary,
        plan_bundle=plan_bundle,
        action_suggestions=action_suggestions,
        task_count=progress["task_count"],
        completed_task_count=progress["completed_task_count"],
        subtask_count=progress["subtask_count"],
        completed_subtask_count=progress["completed_subtask_count"],
        suggested_action_count=action_progress["suggested_action_count"],
        pending_action_count=action_progress["pending_action_count"],
        previewed_action_count=action_progress["previewed_action_count"],
        completed_action_count=action_progress["completed_action_count"],
        failed_action_count=action_progress["failed_action_count"],
        rolled_back_action_count=action_progress["rolled_back_action_count"],
        latest_action_at=action_progress["latest_action_at"],
        available_transitions=_available_transitions(task.status),
        started_at=_iso_datetime(task.started_at),
        completed_at=_iso_datetime(task.completed_at),
        last_transition_at=_iso_datetime(task.last_transition_at),
        created_at=_iso_datetime(task.created_at) or datetime.now().isoformat(),
        updated_at=_iso_datetime(task.updated_at) or datetime.now().isoformat(),
    )


def serialize_task_list(tasks: List[AgentTask]) -> AgentTaskListResponse:
    return AgentTaskListResponse(total=len(tasks), tasks=[serialize_task(task) for task in tasks])


def serialize_task_detail(
    task: AgentTask,
    *,
    linked_actions: List[AgentActionLogItem] | None = None,
) -> AgentTaskDetailResponse:
    events = sorted(task.events or [], key=lambda item: (item.created_at, item.id))
    return AgentTaskDetailResponse(
        task=serialize_task(task),
        events=[_serialize_task_event(event) for event in events],
        linked_actions=list(linked_actions or []),
    )


def list_session_tasks(db: Session, session_id: str, limit: int = 50) -> List[AgentTask]:
    ensure_agent_schema()
    return (
        db.query(AgentTask)
        .filter(AgentTask.session_id == session_id)
        .order_by(desc(AgentTask.updated_at), desc(AgentTask.created_at), desc(AgentTask.id))
        .limit(limit)
        .all()
    )


def get_task_or_none(db: Session, task_id: str) -> AgentTask | None:
    ensure_agent_schema()
    return db.query(AgentTask).filter(AgentTask.id == task_id).first()


def get_task_for_actor_or_none(
    db: Session,
    task_id: str,
    *,
    user_id: str | None = None,
    device_id: str | None = None,
) -> AgentTask | None:
    if not user_id and not device_id:
        raise AgentIdentityRequiredError(AGENT_IDENTITY_REQUIRED)
    task = get_task_or_none(db, task_id)
    if task is None:
        return None
    if user_id and task.user_id != user_id:
        return None
    if device_id and task.device_id != device_id:
        return None
    return task


def _resolve_task_title(payload: AgentTaskCreateRequest, session: AgentSession, plan_bundle: Dict[str, Any]) -> str:
    candidate = _normalize_text(payload.title)
    if candidate:
        return candidate[:120]
    summary = _normalize_text(payload.plan_summary) or _normalize_text(plan_bundle.get("summary"))
    if summary:
        return summary[:120]
    tasks = list(plan_bundle.get("tasks") or [])
    if tasks:
        first_title = _normalize_text(tasks[0].get("title"))
        if first_title:
            return first_title[:120]
    return f"{session.title} 任务"


def _resolve_task_goal(payload: AgentTaskCreateRequest, plan_bundle: Dict[str, Any], title: str) -> str:
    candidate = _normalize_text(payload.goal)
    if candidate:
        return candidate[:500]
    summary = _normalize_text(payload.plan_summary) or _normalize_text(plan_bundle.get("summary"))
    if summary:
        return summary[:500]
    return title


def _create_task_event(
    db: Session,
    *,
    task: AgentTask,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    payload: Dict[str, Any] | None = None,
) -> AgentTaskEvent:
    event = AgentTaskEvent(
        task_id=task.id,
        session_id=task.session_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        note=_normalize_text(note) or None,
        payload=payload or {},
    )
    db.add(event)
    return event


def append_task_event(
    db: Session,
    *,
    task: AgentTask,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    payload: Dict[str, Any] | None = None,
    auto_commit: bool = True,
) -> AgentTaskEvent:
    ensure_agent_schema()
    event = _create_task_event(
        db,
        task=task,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        note=note,
        payload=payload,
    )
    if auto_commit:
        db.commit()
        db.refresh(task)
    return event


def get_task_by_turn_state_or_none(db: Session, *, session_id: str, turn_state_id: int) -> AgentTask | None:
    ensure_agent_schema()
    return (
        db.query(AgentTask)
        .filter(
            AgentTask.session_id == session_id,
            AgentTask.related_turn_state_id == int(turn_state_id),
        )
        .order_by(desc(AgentTask.updated_at), desc(AgentTask.created_at), desc(AgentTask.id))
        .first()
    )


def _task_suggestion_tool_names(task: AgentTask) -> set[str]:
    names: set[str] = set()
    for item in _normalize_action_suggestions(task.action_suggestions or []):
        tool_name = _normalize_text((item or {}).get("tool_name"))
        if tool_name:
            names.add(tool_name)
    return names


def resolve_task_for_action(
    db: Session,
    *,
    session: AgentSession,
    task_id: str | None = None,
    tool_name: str | None = None,
) -> AgentTask | None:
    ensure_agent_schema()
    if task_id:
        return (
            db.query(AgentTask)
            .filter(AgentTask.id == task_id, AgentTask.session_id == session.id)
            .first()
        )

    candidates = (
        db.query(AgentTask)
        .filter(AgentTask.session_id == session.id)
        .order_by(desc(AgentTask.updated_at), desc(AgentTask.created_at), desc(AgentTask.id))
        .limit(24)
        .all()
    )
    if not candidates:
        return None

    if tool_name:
        matched_active = [
            task
            for task in candidates
            if task.status in _ACTIVE_TASK_STATUSES and tool_name in _task_suggestion_tool_names(task)
        ]
        if matched_active:
            return matched_active[0]

    active = [task for task in candidates if task.status in _ACTIVE_TASK_STATUSES]
    if active:
        return active[0]
    return candidates[0]


def create_agent_task(
    db: Session,
    *,
    session: AgentSession,
    payload: AgentTaskCreateRequest,
    auto_commit: bool = True,
) -> AgentTask:
    ensure_agent_schema()
    plan_bundle = _normalize_plan_bundle(payload.plan_bundle)
    action_suggestions = _normalize_action_suggestions(payload.action_suggestions)
    if payload.related_turn_state_id is not None:
        turn_state = (
            db.query(AgentTurnState)
            .filter(
                AgentTurnState.id == int(payload.related_turn_state_id),
                AgentTurnState.session_id == session.id,
            )
            .first()
        )
        if turn_state is None:
            raise ValueError("related_turn_state_id 不存在，或不属于当前会话")
        existing_task = get_task_by_turn_state_or_none(
            db,
            session_id=session.id,
            turn_state_id=int(payload.related_turn_state_id),
        )
        if existing_task is not None:
            return existing_task

    title = _resolve_task_title(payload, session, plan_bundle)
    goal = _resolve_task_goal(payload, plan_bundle, title)
    now = datetime.now()
    task = AgentTask(
        id=uuid4().hex,
        session_id=session.id,
        user_id=payload.user_id or session.user_id,
        device_id=payload.device_id or session.device_id,
        related_turn_state_id=payload.related_turn_state_id,
        title=title,
        goal=goal,
        status=payload.initial_status,
        priority=payload.priority,
        source=payload.source,
        plan_summary=_normalize_text(payload.plan_summary) or _normalize_text(plan_bundle.get("summary")) or goal,
        plan_bundle=plan_bundle,
        action_suggestions=action_suggestions,
        started_at=now if payload.initial_status in {"running", "verifying"} else None,
        completed_at=now if payload.initial_status in {"completed", "failed", "cancelled"} else None,
        last_transition_at=now,
    )
    db.add(task)
    db.flush()

    progress = _plan_progress(plan_bundle)
    _create_task_event(
        db,
        task=task,
        event_type="created",
        to_status=task.status,
        note=payload.note,
        payload={
            "task_count": progress["task_count"],
            "subtask_count": progress["subtask_count"],
            "source": task.source,
            "related_turn_state_id": task.related_turn_state_id,
        },
    )
    if auto_commit:
        db.commit()
        db.refresh(task)
    return task


def transition_agent_task_status(
    db: Session,
    *,
    task: AgentTask,
    payload: AgentTaskStatusUpdateRequest,
    auto_commit: bool = True,
    raise_on_invalid: bool = True,
) -> AgentTask:
    ensure_agent_schema()
    current_status = str(task.status or "")
    next_status = str(payload.status or "")
    if current_status == next_status:
        if raise_on_invalid:
            raise ValueError("任务已经处于该状态")
        return task

    allowed = _TASK_TRANSITIONS.get(current_status, [])
    if next_status not in allowed:
        if raise_on_invalid:
            raise ValueError(f"不允许从 {current_status} 切换到 {next_status}")
        return task

    now = datetime.now()
    task.status = next_status
    task.last_transition_at = now
    task.updated_at = now
    if next_status in {"running", "verifying"} and task.started_at is None:
        task.started_at = now
    if next_status in {"completed", "failed", "cancelled"}:
        task.completed_at = now
    else:
        task.completed_at = None

    _create_task_event(
        db,
        task=task,
        event_type="status_changed",
        from_status=current_status,
        to_status=next_status,
        note=payload.note,
        payload={"available_transitions": _TASK_TRANSITIONS.get(next_status, [])},
    )
    if auto_commit:
        db.commit()
        db.refresh(task)
    return task


def ensure_task_from_turn(
    db: Session,
    *,
    session: AgentSession,
    turn_state: AgentTurnState,
    plan_bundle: Dict[str, Any] | None,
    action_suggestions: List[Dict[str, Any]] | None,
    auto_commit: bool = True,
) -> AgentTask | None:
    ensure_agent_schema()
    suggestions = list(action_suggestions or [])
    normalized_plan = _normalize_plan_bundle(plan_bundle)
    if not suggestions:
        return None

    existing = get_task_by_turn_state_or_none(
        db,
        session_id=session.id,
        turn_state_id=int(turn_state.id),
    )
    if existing is not None:
        return existing

    payload = AgentTaskCreateRequest(
        session_id=session.id,
        user_id=session.user_id,
        device_id=session.device_id,
        related_turn_state_id=int(turn_state.id),
        title=_normalize_text(normalized_plan.get("summary")) or None,
        goal=_normalize_text(turn_state.goal) or None,
        priority="medium",
        source="plan",
        initial_status="ready",
        plan_summary=_normalize_text(normalized_plan.get("summary")) or None,
        plan_bundle=normalized_plan,
        action_suggestions=suggestions,
        note="auto-created from assistant plan",
    )
    return create_agent_task(db, session=session, payload=payload, auto_commit=auto_commit)


def _task_successful_action_tools(db: Session, *, task_id: str) -> set[str]:
    rows = (
        db.query(AgentActionLog.tool_name)
        .filter(
            AgentActionLog.related_task_id == task_id,
            AgentActionLog.execution_status == "success",
        )
        .all()
    )
    return {str(row[0]) for row in rows if row and row[0]}


def _build_task_action_suggestion_from_log(action_log: AgentActionLog) -> Dict[str, Any]:
    return _normalize_action_suggestions(
        [
            {
                "id": f"{action_log.tool_name}-{action_log.id[:8]}",
                "tool_name": action_log.tool_name,
                "title": action_log.tool_name,
                "summary": action_log.preview_summary,
                "tool_args": dict(action_log.tool_args or {}),
                "risk_level": action_log.risk_level,
                "requires_confirmation": action_log.approval_status in {"pending", "approved"},
                "related_action_id": action_log.id,
                "approval_status": action_log.approval_status,
                "execution_status": action_log.execution_status,
                "verification_status": action_log.verification_status,
                "preview_summary": action_log.preview_summary,
                "affected_ids": list(action_log.affected_ids or []),
                "error_message": action_log.error_message,
                "confirmed_at": _iso_datetime(action_log.confirmed_at),
                "executed_at": _iso_datetime(action_log.executed_at),
                "updated_at": _iso_datetime(action_log.updated_at) or _iso_datetime(datetime.now()),
            }
        ]
    )[0]


def _sync_task_action_suggestion(task: AgentTask, action_log: AgentActionLog) -> None:
    suggestions = _normalize_action_suggestions(task.action_suggestions or [])
    matched_item: Dict[str, Any] | None = None

    for item in suggestions:
        if str(item.get("tool_name") or "") == str(action_log.tool_name or ""):
            matched_item = item
            break

    if matched_item is None:
        matched_item = _build_task_action_suggestion_from_log(action_log)
        suggestions.append(matched_item)

    requires_confirmation = bool(matched_item.get("requires_confirmation"))
    matched_item["related_action_id"] = action_log.id
    matched_item["approval_status"] = _normalize_action_approval_status(
        action_log.approval_status,
        requires_confirmation=requires_confirmation,
    )
    matched_item["execution_status"] = _normalize_action_execution_status(action_log.execution_status)
    matched_item["verification_status"] = _normalize_action_verification_status(action_log.verification_status)
    matched_item["preview_summary"] = _normalize_text(action_log.preview_summary) or matched_item.get("preview_summary")
    matched_item["affected_ids"] = list(action_log.affected_ids or [])
    matched_item["error_message"] = _normalize_text(action_log.error_message) or None
    matched_item["confirmed_at"] = _iso_datetime(action_log.confirmed_at)
    matched_item["executed_at"] = _iso_datetime(action_log.executed_at)
    matched_item["updated_at"] = _iso_datetime(action_log.updated_at) or datetime.now().isoformat()

    task.action_suggestions = suggestions
    task.updated_at = datetime.now()


def sync_task_after_action_preview(
    db: Session,
    *,
    task: AgentTask,
    action_log: AgentActionLog,
    auto_commit: bool = True,
) -> AgentTask:
    _sync_task_action_suggestion(task, action_log)
    append_task_event(
        db,
        task=task,
        event_type="action_previewed",
        note=action_log.preview_summary,
        payload={
            "action_id": action_log.id,
            "tool_name": action_log.tool_name,
            "approval_status": action_log.approval_status,
        },
        auto_commit=auto_commit,
    )
    return task


def sync_task_after_action_execution(
    db: Session,
    *,
    task: AgentTask,
    action_log: AgentActionLog,
    auto_commit: bool = True,
) -> AgentTask:
    ensure_agent_schema()
    _sync_task_action_suggestion(task, action_log)
    success = action_log.execution_status == "success" and action_log.verification_status == "verified"
    if success:
        transition_agent_task_status(
            db,
            task=task,
            payload=AgentTaskStatusUpdateRequest(status="running"),
            auto_commit=False,
            raise_on_invalid=False,
        )
        append_task_event(
            db,
            task=task,
            event_type="action_executed",
            note=action_log.preview_summary,
            payload={
                "action_id": action_log.id,
                "tool_name": action_log.tool_name,
                "verification_status": action_log.verification_status,
                "affected_ids": list(action_log.affected_ids or []),
            },
            auto_commit=False,
        )
        suggested_tools = _task_suggestion_tool_names(task)
        executed_tools = _task_successful_action_tools(db, task_id=task.id)
        if suggested_tools and suggested_tools.issubset(executed_tools):
            transition_agent_task_status(
                db,
                task=task,
                payload=AgentTaskStatusUpdateRequest(status="verifying"),
                auto_commit=False,
                raise_on_invalid=False,
            )
    else:
        append_task_event(
            db,
            task=task,
            event_type="action_failed",
            note=action_log.error_message or action_log.preview_summary,
            payload={
                "action_id": action_log.id,
                "tool_name": action_log.tool_name,
                "verification_status": action_log.verification_status,
                "error_message": action_log.error_message,
            },
            auto_commit=False,
        )
        transition_agent_task_status(
            db,
            task=task,
            payload=AgentTaskStatusUpdateRequest(status="failed", note=action_log.error_message),
            auto_commit=False,
            raise_on_invalid=False,
        )

    if auto_commit:
        db.commit()
        db.refresh(task)
    return task


def sync_task_after_action_rollback(
    db: Session,
    *,
    task: AgentTask,
    action_log: AgentActionLog,
    auto_commit: bool = True,
) -> AgentTask:
    _sync_task_action_suggestion(task, action_log)
    append_task_event(
        db,
        task=task,
        event_type="action_rolled_back",
        note=action_log.preview_summary,
        payload={
            "action_id": action_log.id,
            "tool_name": action_log.tool_name,
        },
        auto_commit=False,
    )
    if str(task.status or "") == "verifying":
        transition_agent_task_status(
            db,
            task=task,
            payload=AgentTaskStatusUpdateRequest(status="paused"),
            auto_commit=False,
            raise_on_invalid=False,
        )
    elif str(task.status or "") == "failed":
        transition_agent_task_status(
            db,
            task=task,
            payload=AgentTaskStatusUpdateRequest(status="ready"),
            auto_commit=False,
            raise_on_invalid=False,
        )

    if auto_commit:
        db.commit()
        db.refresh(task)
    return task
