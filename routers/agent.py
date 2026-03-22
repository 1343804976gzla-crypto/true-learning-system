from __future__ import annotations

import json
from time import perf_counter
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database.domains import get_agent_db
from services.agent_actions import (
    AgentActionNotFoundError,
    AgentWriteActionsDisabledError,
    agent_write_actions_disabled_message,
    execute_agent_action,
    list_session_actions,
    list_task_actions,
    serialize_action_log,
    serialize_action_list,
)
from services.agent_tasks import (
    AgentTaskNotFoundError,
    create_agent_task,
    get_task_for_actor_or_none,
    list_session_tasks,
    serialize_task_detail,
    serialize_task_list,
    transition_agent_task_status,
)
from services.agent_runtime import (
    AGENT_DUPLICATE_REQUEST_IN_PROGRESS,
    AGENT_IDENTITY_REQUIRED,
    AgentDuplicateRequestInProgressError,
    AgentDuplicateResponseAvailableError,
    AgentIdentityRequiredError,
    AgentLlmRateLimitError,
    AgentSessionNotFoundError,
    create_session,
    finalize_chat_turn,
    get_session_for_actor_or_none,
    list_messages,
    list_sessions,
    list_turn_states,
    prepare_chat_turn,
    run_chat,
    serialize_message,
    serialize_session,
    serialize_turn_state,
    serialize_tool_call,
    summarize_session,
    stream_chat_turn,
    _release_chat_request,
    _resolve_session_for_payload,
    _wait_for_existing_chat_response,
)
from services.mem0_bridge import get_mem0_bridge_status
from services.openmanus_bridge import get_openmanus_bridge_status
from services.agent_tools import list_available_agent_tools
from utils.agent_contracts import (
    AgentActionExecuteRequest,
    AgentActionExecuteResponse,
    AgentActionListResponse,
    AgentChatRequest,
    AgentChatResponse,
    AgentMessageListResponse,
    AgentSessionCreateRequest,
    AgentSessionItem,
    AgentSessionListResponse,
    AgentSummaryResponse,
    AgentTaskCreateRequest,
    AgentTaskDetailResponse,
    AgentTaskListResponse,
    AgentTaskStatusUpdateRequest,
    AgentTurnStateListResponse,
    AgentToolDefinition,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _sse_event(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _agent_error_response(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentIdentityRequiredError) or str(exc) == AGENT_IDENTITY_REQUIRED:
        return HTTPException(status_code=400, detail="缺少 device_id 或 user_id")
    if isinstance(exc, AgentSessionNotFoundError):
        return HTTPException(status_code=404, detail="会话不存在")
    if isinstance(exc, AgentActionNotFoundError):
        return HTTPException(status_code=404, detail="动作记录不存在")
    if isinstance(exc, AgentWriteActionsDisabledError):
        return HTTPException(status_code=403, detail=agent_write_actions_disabled_message())
    if isinstance(exc, AgentTaskNotFoundError):
        return HTTPException(status_code=404, detail="任务不存在")
    if isinstance(exc, AgentDuplicateRequestInProgressError) or str(exc) == AGENT_DUPLICATE_REQUEST_IN_PROGRESS:
        return HTTPException(status_code=409, detail="同一请求仍在处理中，请稍后刷新会话")
    if isinstance(exc, AgentLlmRateLimitError):
        return HTTPException(status_code=429, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _sse_done_payload(result: AgentChatResponse) -> dict:
    return {
        "session": result.session.model_dump(mode="json"),
        "user_message": result.user_message.model_dump(mode="json"),
        "assistant_message": result.assistant_message.model_dump(mode="json"),
        "tool_calls": [tool_call.model_dump(mode="json") for tool_call in result.tool_calls],
        "context_usage": result.context_usage.model_dump(mode="json"),
        "trace_id": result.trace_id,
        "error_message": result.error_message,
    }


def _resolve_session_with_optional_actor(
    db: Session,
    session_id: str,
    *,
    user_id: str | None = None,
    device_id: str | None = None,
):
    return get_session_for_actor_or_none(db, session_id, user_id=user_id, device_id=device_id)


def _serialize_task_detail_response(db: Session, task) -> AgentTaskDetailResponse:
    linked_actions = [serialize_action_log(item) for item in list_task_actions(db, task.id, limit=20)]
    return serialize_task_detail(task, linked_actions=linked_actions)


@router.post("/sessions", response_model=AgentSessionItem)
async def create_agent_session(payload: AgentSessionCreateRequest, db: Session = Depends(get_agent_db)) -> AgentSessionItem:
    try:
        session = create_session(db, payload)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    return serialize_session(db, session)


@router.get("/sessions", response_model=AgentSessionListResponse)
async def get_agent_sessions(
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    status: str = Query(default="active", pattern="^(active|archived|deleted|all)$"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_agent_db),
) -> AgentSessionListResponse:
    try:
        sessions = list_sessions(db, user_id=user_id, device_id=device_id, status=status, limit=limit)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    items = [serialize_session(db, session) for session in sessions]
    return AgentSessionListResponse(total=len(items), sessions=items)


@router.get("/sessions/{session_id}", response_model=AgentSessionItem)
async def get_agent_session(
    session_id: str,
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_agent_db),
) -> AgentSessionItem:
    try:
        session = _resolve_session_with_optional_actor(db, session_id, user_id=user_id, device_id=device_id)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return serialize_session(db, session)


@router.get("/sessions/{session_id}/messages", response_model=AgentMessageListResponse)
async def get_agent_messages(
    session_id: str,
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_agent_db),
) -> AgentMessageListResponse:
    try:
        session = _resolve_session_with_optional_actor(db, session_id, user_id=user_id, device_id=device_id)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = [serialize_message(message) for message in list_messages(db, session_id=session_id, limit=limit)]
    return AgentMessageListResponse(total=len(messages), messages=messages)


@router.get("/sessions/{session_id}/turns", response_model=AgentTurnStateListResponse)
async def get_agent_turns(
    session_id: str,
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_agent_db),
) -> AgentTurnStateListResponse:
    try:
        session = _resolve_session_with_optional_actor(db, session_id, user_id=user_id, device_id=device_id)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    turns = [serialize_turn_state(item) for item in list_turn_states(db, session_id=session_id, limit=limit)]
    return AgentTurnStateListResponse(total=len(turns), turns=turns)


@router.post("/chat", response_model=AgentChatResponse)
async def post_agent_chat(payload: AgentChatRequest, db: Session = Depends(get_agent_db)) -> AgentChatResponse:
    try:
        return await run_chat(db, payload)
    except Exception as exc:
        raise _agent_error_response(exc) from exc


@router.post("/chat/stream", response_model=None, response_class=StreamingResponse)
async def post_agent_chat_stream(payload: AgentChatRequest, db: Session = Depends(get_agent_db)) -> StreamingResponse:
    async def event_stream():
        yield _sse_event("ready", {"message": "agent stream connected"})

        prepared_turn = None
        assistant_chunks: List[str] = []
        started = perf_counter()

        try:
            try:
                prepared_turn = await prepare_chat_turn(db, payload)
            except AgentDuplicateResponseAvailableError as exc:
                yield _sse_event("done", _sse_done_payload(exc.response))
                return
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
                    yield _sse_event("done", _sse_done_payload(reused_response))
                    return
                raise

            yield _sse_event(
                "session",
                {
                    "session": serialize_session(db, prepared_turn.session).model_dump(mode="json"),
                    "user_message": serialize_message(prepared_turn.user_message).model_dump(mode="json"),
                    "trace_id": prepared_turn.trace_id,
                },
            )

            for tool_call in prepared_turn.tool_calls:
                yield _sse_event("tool_call", serialize_tool_call(tool_call).model_dump(mode="json"))

            yield _sse_event(
                "message_start",
                {
                    "assistant_message_id": None,
                    "message_status": "pending",
                    "context_usage": prepared_turn.context_usage.model_dump(mode="json"),
                    "sources": [card.model_dump(mode="json") for card in prepared_turn.source_cards],
                    "plan": prepared_turn.draft_plan.model_dump(mode="json"),
                    "action_suggestions": prepared_turn.action_suggestions,
                    "response_strategy": prepared_turn.response_strategy,
                    "execution_state": prepared_turn.turn_state.execution_state,
                    "turn_state_id": int(prepared_turn.turn_state.id),
                },
            )

            async for chunk in stream_chat_turn(prepared_turn):
                assistant_chunks.append(chunk)
                yield _sse_event("delta", {"content": chunk})

            result = finalize_chat_turn(
                db,
                prepared_turn,
                "".join(assistant_chunks),
                assistant_status="completed",
                latency_ms=int((perf_counter() - started) * 1000),
            )
            yield _sse_event("done", _sse_done_payload(result))
        except Exception as exc:
            if prepared_turn is None:
                if payload.client_request_id:
                    try:
                        session = _resolve_session_for_payload(db, payload)
                    except Exception:
                        session = None
                    if session is not None:
                        _release_chat_request(session.id, payload.client_request_id)
                yield _sse_event("error", {"detail": _agent_error_response(exc).detail})
                return
            detail = str(exc)[:500]
            result = finalize_chat_turn(
                db,
                prepared_turn,
                "".join(assistant_chunks),
                assistant_status="error",
                latency_ms=int((perf_counter() - started) * 1000),
                error_message=detail,
            )
            yield _sse_event("done", _sse_done_payload(result))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/summarize", response_model=AgentSummaryResponse)
async def post_agent_summary(
    session_id: str,
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_agent_db),
) -> AgentSummaryResponse:
    try:
        return summarize_session(db, session_id, user_id=user_id, device_id=device_id)
    except Exception as exc:
        raise _agent_error_response(exc) from exc


@router.get("/sessions/{session_id}/actions", response_model=AgentActionListResponse)
async def get_agent_actions(
    session_id: str,
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_agent_db),
) -> AgentActionListResponse:
    try:
        session = _resolve_session_with_optional_actor(db, session_id, user_id=user_id, device_id=device_id)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return serialize_action_list(list_session_actions(db, session_id=session.id, limit=limit))


@router.post("/actions", response_model=AgentActionExecuteResponse)
async def post_agent_action(
    payload: AgentActionExecuteRequest,
    db: Session = Depends(get_agent_db),
) -> AgentActionExecuteResponse:
    try:
        session = _resolve_session_with_optional_actor(
            db,
            payload.session_id,
            user_id=payload.user_id,
            device_id=payload.device_id,
        )
        if session is None:
            raise AgentSessionNotFoundError(payload.session_id)
        return execute_agent_action(db, session, payload)
    except Exception as exc:
        raise _agent_error_response(exc) from exc


@router.get("/sessions/{session_id}/tasks", response_model=AgentTaskListResponse)
async def get_agent_tasks(
    session_id: str,
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_agent_db),
) -> AgentTaskListResponse:
    try:
        session = _resolve_session_with_optional_actor(db, session_id, user_id=user_id, device_id=device_id)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return serialize_task_list(list_session_tasks(db, session_id=session.id, limit=limit))


@router.post("/tasks", response_model=AgentTaskDetailResponse)
async def post_agent_task(
    payload: AgentTaskCreateRequest,
    db: Session = Depends(get_agent_db),
) -> AgentTaskDetailResponse:
    try:
        session = _resolve_session_with_optional_actor(
            db,
            payload.session_id,
            user_id=payload.user_id,
            device_id=payload.device_id,
        )
        if session is None:
            raise AgentSessionNotFoundError(payload.session_id)
        task = create_agent_task(db, session=session, payload=payload)
        return _serialize_task_detail_response(db, task)
    except Exception as exc:
        raise _agent_error_response(exc) from exc


@router.get("/tasks/{task_id}", response_model=AgentTaskDetailResponse)
async def get_agent_task(
    task_id: str,
    user_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_agent_db),
) -> AgentTaskDetailResponse:
    try:
        task = get_task_for_actor_or_none(db, task_id, user_id=user_id, device_id=device_id)
    except Exception as exc:
        raise _agent_error_response(exc) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _serialize_task_detail_response(db, task)


@router.post("/tasks/{task_id}/status", response_model=AgentTaskDetailResponse)
async def post_agent_task_status(
    task_id: str,
    payload: AgentTaskStatusUpdateRequest,
    db: Session = Depends(get_agent_db),
) -> AgentTaskDetailResponse:
    try:
        task = get_task_for_actor_or_none(db, task_id, user_id=payload.user_id, device_id=payload.device_id)
        if task is None:
            raise AgentTaskNotFoundError(task_id)
        task = transition_agent_task_status(db, task=task, payload=payload)
        return _serialize_task_detail_response(db, task)
    except Exception as exc:
        raise _agent_error_response(exc) from exc


@router.get("/tools", response_model=List[AgentToolDefinition])
async def get_agent_tools() -> List[AgentToolDefinition]:
    return list_available_agent_tools()


@router.get("/reference/status", response_model=dict)
async def get_agent_reference_status() -> dict:
    return {
        "mem0": get_mem0_bridge_status(),
        "openmanus": get_openmanus_bridge_status(),
    }
