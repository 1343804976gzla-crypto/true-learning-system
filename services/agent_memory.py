from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from agent_models import AgentMemory, AgentMessage, AgentSession, AgentToolCache, AgentToolCall
from services.mem0_bridge import search_mem0_memories, store_mem0_memory_records

TOOL_CACHE_TTLS = {
    "get_progress_summary": 300,
    "get_knowledge_mastery": 600,
    "get_wrong_answers": 300,
    "get_review_pressure": 300,
    "get_learning_sessions": 300,
    "get_study_history": 900,
    "search_openviking_context": 180,
    "consult_openmanus": 1800,
}

LONG_TERM_MEMORY_TTLS = {
    "user_goal": 45,
    "study_preference": 120,
    "study_constraint": 30,
    "learner_profile": 180,
}

LONG_TERM_MEMORY_TYPES = tuple(LONG_TERM_MEMORY_TTLS.keys())

_MEMORY_TYPE_LABELS = {
    "user_goal": "goal",
    "study_preference": "preference",
    "study_constraint": "constraint",
    "learner_profile": "profile",
}

_GOAL_PATTERNS = [
    re.compile(r"((?:这周|本周|今天|今晚|明天|接下来)?[^。！？\n]{0,10}(?:我想|我要|我希望|我打算|我准备)[^。！？\n]{2,48})"),
    re.compile(r"((?:目标|计划)[^。！？\n]{2,48})"),
]

_PREFERENCE_PATTERNS = [
    re.compile(r"((?:优先|先|更想|更喜欢|更关注|尽量)[^。！？\n]{2,48})"),
    re.compile(r"((?:不要|别)[^。！？\n]{2,40})"),
]

_CONSTRAINT_PATTERNS = [
    re.compile(r"((?:只有|只能|最多|没时间|时间不多)[^。！？\n]{2,48})"),
    re.compile(r"((?:考前|截止前)[^。！？\n]{2,48})"),
]

_PROFILE_PATTERNS = [
    re.compile(r"((?:我是|我在准备|我现在是)[^。！？\n]{2,48})"),
]

_MEMORY_STOP_WORDS = {
    "今天",
    "今晚",
    "这周",
    "本周",
    "一下",
    "怎么",
    "安排",
    "帮我",
    "因为",
    "所以",
    "然后",
}


def get_latest_session_summary(db: Session, session_id: str) -> Optional[AgentMemory]:
    return (
        db.query(AgentMemory)
        .filter(
            AgentMemory.session_id == session_id,
            AgentMemory.memory_type == "session_summary",
        )
        .order_by(desc(AgentMemory.created_at), desc(AgentMemory.id))
        .first()
    )


def _normalize_memory_text(text: str, *, limit: int = 120) -> str:
    value = " ".join((text or "").replace("，", " ").replace("；", " ").split())
    return value[:limit].strip()


def _memory_scope_query(
    db: Session,
    *,
    user_id: str | None,
    device_id: str | None,
):
    query = db.query(AgentMemory).join(AgentSession, AgentSession.id == AgentMemory.session_id)
    if user_id:
        return query.filter(AgentMemory.user_id == user_id)
    if device_id:
        return query.filter(AgentSession.device_id == device_id)
    return query.filter(AgentMemory.session_id.is_(None))


def _iter_pattern_matches(patterns: list[re.Pattern[str]], text: str) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text or ""):
            candidate = _normalize_memory_text(match.group(1))
            if candidate:
                matches.append(candidate)
    return matches


def _memory_key(memory_type: str, summary: str) -> tuple[str, str]:
    return memory_type, _normalize_memory_text(summary, limit=200).lower()


def _extract_long_term_memory_candidates(
    *,
    message_text: str,
    request_analysis: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    normalized_text = _normalize_memory_text(message_text, limit=240)
    candidates: list[dict[str, str]] = []

    for summary in _iter_pattern_matches(_GOAL_PATTERNS, normalized_text):
        candidates.append({"memory_type": "user_goal", "summary": summary})
    for summary in _iter_pattern_matches(_PREFERENCE_PATTERNS, normalized_text):
        candidates.append({"memory_type": "study_preference", "summary": summary})
    for summary in _iter_pattern_matches(_CONSTRAINT_PATTERNS, normalized_text):
        candidates.append({"memory_type": "study_constraint", "summary": summary})
    for summary in _iter_pattern_matches(_PROFILE_PATTERNS, normalized_text):
        candidates.append({"memory_type": "learner_profile", "summary": summary})

    goal = _normalize_memory_text(str((request_analysis or {}).get("goal") or ""), limit=120)
    time_horizon = _normalize_memory_text(str((request_analysis or {}).get("time_horizon") or ""), limit=40)
    if goal and len(goal) >= 4:
        if time_horizon and time_horizon not in {"当前", "current"}:
            candidates.append({"memory_type": "user_goal", "summary": f"{time_horizon} {goal}"})
        else:
            candidates.append({"memory_type": "user_goal", "summary": goal})

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = _memory_key(item["memory_type"], item["summary"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:6]


def store_long_term_memories(
    db: Session,
    *,
    session: AgentSession,
    message: AgentMessage,
    request_analysis: dict[str, Any] | None = None,
) -> list[AgentMemory]:
    candidates = _extract_long_term_memory_candidates(
        message_text=message.content,
        request_analysis=request_analysis,
    )
    if not candidates:
        return []

    existing = (
        _memory_scope_query(db, user_id=session.user_id, device_id=session.device_id)
        .filter(AgentMemory.memory_type.in_(LONG_TERM_MEMORY_TYPES))
        .order_by(desc(AgentMemory.created_at), desc(AgentMemory.id))
        .limit(120)
        .all()
    )
    seen = {_memory_key(item.memory_type, item.summary) for item in existing}
    created: list[AgentMemory] = []
    now = datetime.now()

    for item in candidates:
        key = _memory_key(item["memory_type"], item["summary"])
        if key in seen:
            continue
        memory = AgentMemory(
            user_id=session.user_id,
            session_id=session.id,
            memory_type=item["memory_type"],
            summary=item["summary"],
            source_message_ids=[int(message.id)],
            expires_at=now + timedelta(days=LONG_TERM_MEMORY_TTLS.get(item["memory_type"], 45)),
            created_at=now,
        )
        db.add(memory)
        created.append(memory)
        seen.add(key)

    if created:
        db.flush()
        store_mem0_memory_records(
            [
                {
                    "summary": item.summary,
                    "memory_type": item.memory_type,
                    "memory_label": _MEMORY_TYPE_LABELS.get(item.memory_type, item.memory_type),
                    "source_message_ids": list(item.source_message_ids or []),
                }
                for item in created
            ],
            user_id=session.user_id,
            device_id=session.device_id,
            session_id=session.id,
        )
    return created


def _extract_memory_terms(text: str) -> list[str]:
    normalized = _normalize_memory_text(text, limit=200).lower()
    english_terms = re.findall(r"[a-z0-9][a-z0-9_\-]{2,20}", normalized)
    chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,12}", normalized)
    terms: list[str] = []
    seen: set[str] = set()
    for term in [*english_terms, *chinese_terms]:
        if term in _MEMORY_STOP_WORDS or len(term) < 2:
            continue
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms[:12]


def search_long_term_memories(
    db: Session,
    *,
    session: AgentSession,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    candidates = (
        _memory_scope_query(db, user_id=session.user_id, device_id=session.device_id)
        .filter(AgentMemory.memory_type.in_(LONG_TERM_MEMORY_TYPES))
        .order_by(desc(AgentMemory.created_at), desc(AgentMemory.id))
        .limit(120)
        .all()
    )

    query_terms = _extract_memory_terms(query)
    scored: list[dict[str, Any]] = []
    now = datetime.now()

    for memory in candidates:
        summary = memory.summary or ""
        lowered = summary.lower()
        score = 0.0
        for term in query_terms:
            if term in lowered:
                score += 2.0 if len(term) >= 4 else 1.0
        if memory.session_id == session.id:
            score += 0.5
        age_days = max(0.0, (now - (memory.created_at or now)).total_seconds() / 86400.0)
        if age_days <= 7:
            score += 0.35
        elif age_days <= 30:
            score += 0.15
        if score <= 0:
            continue
        scored.append(
            {
                "id": int(memory.id),
                "memory_type": memory.memory_type,
                "memory_label": _MEMORY_TYPE_LABELS.get(memory.memory_type, memory.memory_type),
                "summary": summary,
                "session_id": memory.session_id,
                "created_at": memory.created_at.isoformat() if memory.created_at else None,
                "score": round(score, 2),
            }
        )

    scored.sort(key=lambda item: (float(item["score"]), str(item["created_at"] or "")), reverse=True)
    local_hits = scored[:limit]

    if not local_hits:
        for memory in candidates[: min(limit, 3)]:
            local_hits.append(
                {
                    "id": int(memory.id),
                    "memory_type": memory.memory_type,
                    "memory_label": _MEMORY_TYPE_LABELS.get(memory.memory_type, memory.memory_type),
                    "summary": memory.summary,
                    "session_id": memory.session_id,
                    "created_at": memory.created_at.isoformat() if memory.created_at else None,
                    "score": 0.0,
                }
            )

    remote_hits: list[dict[str, Any]] = []
    for item in search_mem0_memories(
        query,
        user_id=session.user_id,
        device_id=session.device_id,
        session_id=session.id,
        limit=limit,
    ):
        memory_text = " ".join(str(item.get("memory") or item.get("summary") or "").split())
        if not memory_text:
            continue
        metadata = dict(item.get("metadata") or {})
        memory_type = " ".join(str(metadata.get("memory_type") or "mem0_memory").split()) or "mem0_memory"
        remote_hits.append(
            {
                "id": str(item.get("id") or ""),
                "memory_type": memory_type,
                "memory_label": metadata.get("memory_label") or _MEMORY_TYPE_LABELS.get(memory_type, memory_type),
                "summary": memory_text,
                "session_id": metadata.get("session_id"),
                "created_at": item.get("created_at"),
                "score": float(item.get("score") or 0.0),
            }
        )

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*local_hits, *sorted(remote_hits, key=lambda entry: (float(entry.get("score") or 0.0), str(entry.get("created_at") or "")), reverse=True)]:
        key = _memory_key(str(item.get("memory_type") or ""), str(item.get("summary") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _shorten(text: str, limit: int = 80) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def build_session_summary_text(messages: list[AgentMessage], tool_calls: list[AgentToolCall]) -> str:
    user_messages = [_shorten(message.content) for message in messages if message.role == "user" and message.content]
    assistant_messages = [
        _shorten(message.content)
        for message in messages
        if message.role == "assistant" and message.message_status == "completed" and message.content
    ]
    tool_names = list(dict.fromkeys([call.tool_name for call in tool_calls if call.success]))

    parts = []
    if user_messages:
        parts.append(f"用户最近关注：{'；'.join(user_messages[-3:])}")
    if assistant_messages:
        parts.append(f"系统已回应：{'；'.join(assistant_messages[-2:])}")
    if tool_names:
        parts.append(f"已引用数据：{'、'.join(tool_names)}")

    summary = " ".join(parts).strip()
    return summary[:800] if summary else "当前会话尚未形成稳定摘要。"


def refresh_session_summary(db: Session, session: AgentSession) -> Optional[AgentMemory]:
    messages = (
        db.query(AgentMessage)
        .filter(AgentMessage.session_id == session.id)
        .order_by(AgentMessage.created_at, AgentMessage.id)
        .all()
    )
    if len(messages) < 4:
        return None

    tool_calls = (
        db.query(AgentToolCall)
        .filter(AgentToolCall.session_id == session.id)
        .order_by(desc(AgentToolCall.created_at), desc(AgentToolCall.id))
        .limit(8)
        .all()
    )

    summary = build_session_summary_text(messages, tool_calls)
    session.context_summary = summary

    memory = AgentMemory(
        user_id=session.user_id,
        session_id=session.id,
        memory_type="session_summary",
        summary=summary,
        source_message_ids=[int(message.id) for message in messages[-8:]],
        expires_at=datetime.now() + timedelta(days=30),
        created_at=datetime.now(),
    )
    db.add(memory)
    db.flush()
    return memory


def _stable_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_tool_cache_key(tool_name: str, tool_args: dict[str, Any] | None = None) -> str:
    payload = f"{tool_name}:{_stable_json(tool_args)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def get_tool_cache_ttl(tool_name: str) -> int:
    return TOOL_CACHE_TTLS.get(tool_name, 300)


def get_cached_tool_result(
    db: Session,
    *,
    session_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
) -> Optional[AgentToolCache]:
    now = datetime.now()
    cache_key = build_tool_cache_key(tool_name, tool_args)

    scoped_entry = (
        db.query(AgentToolCache)
        .filter(
            AgentToolCache.session_id == session_id,
            AgentToolCache.tool_name == tool_name,
            AgentToolCache.cache_key == cache_key,
            AgentToolCache.expires_at.isnot(None),
            AgentToolCache.expires_at >= now,
        )
        .order_by(desc(AgentToolCache.last_used_at), desc(AgentToolCache.id))
        .first()
    )
    if scoped_entry is not None:
        scoped_entry.hit_count = int(scoped_entry.hit_count or 0) + 1
        scoped_entry.last_used_at = now
        db.flush()
        return scoped_entry

    shared_entry = (
        db.query(AgentToolCache)
        .filter(
            AgentToolCache.session_id.is_(None),
            AgentToolCache.tool_name == tool_name,
            AgentToolCache.cache_key == cache_key,
            AgentToolCache.expires_at.isnot(None),
            AgentToolCache.expires_at >= now,
        )
        .order_by(desc(AgentToolCache.last_used_at), desc(AgentToolCache.id))
        .first()
    )
    if shared_entry is not None:
        shared_entry.hit_count = int(shared_entry.hit_count or 0) + 1
        shared_entry.last_used_at = now
        db.flush()
    return shared_entry


def store_tool_cache_result(
    db: Session,
    *,
    session_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any] | None,
    tool_result: dict[str, Any] | None,
    trace_id: str | None = None,
) -> AgentToolCache:
    now = datetime.now()
    cache_key = build_tool_cache_key(tool_name, tool_args)
    expires_at = now + timedelta(seconds=get_tool_cache_ttl(tool_name))
    entry = (
        db.query(AgentToolCache)
        .filter(
            AgentToolCache.session_id == session_id,
            AgentToolCache.tool_name == tool_name,
            AgentToolCache.cache_key == cache_key,
        )
        .order_by(desc(AgentToolCache.updated_at), desc(AgentToolCache.id))
        .first()
    )
    if entry is None:
        entry = AgentToolCache(
            session_id=session_id,
            tool_name=tool_name,
            cache_key=cache_key,
            tool_args=tool_args or {},
            tool_result=tool_result or {},
            trace_id=trace_id,
            hit_count=0,
            expires_at=expires_at,
            last_used_at=now,
        )
        db.add(entry)
    else:
        entry.tool_args = tool_args or {}
        entry.tool_result = tool_result or {}
        entry.trace_id = trace_id
        entry.expires_at = expires_at
        entry.last_used_at = now
    db.flush()
    return entry
