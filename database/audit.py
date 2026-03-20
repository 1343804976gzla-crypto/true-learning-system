from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy.engine import Engine
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Mapper, Session

from database.domains import (
    AgentBase,
    ContentBase,
    CoreBase,
    LegacyBase,
    ReviewBase,
    RuntimeBase,
    agent_engine,
    content_engine,
    core_engine,
    legacy_engine,
    review_engine,
    runtime_engine,
    get_sqlite_path,
)
from services.data_identity import build_actor_key, get_request_identity
from services.llm_audit import get_llm_audit_request_context


AUDIT_CHANGE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS audit_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    public_id TEXT,
    action TEXT NOT NULL,
    actor_key TEXT,
    user_id TEXT,
    device_id TEXT,
    request_id TEXT,
    trace_id TEXT,
    source TEXT,
    origin_event_type TEXT,
    origin_public_id TEXT,
    before_json TEXT,
    after_json TEXT,
    changed_fields TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


AUDIT_INDEX_DDLS = [
    "CREATE INDEX IF NOT EXISTS ix_audit_change_log_domain ON audit_change_log(domain_name)",
    "CREATE INDEX IF NOT EXISTS ix_audit_change_log_entity ON audit_change_log(entity_type, entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_change_log_trace ON audit_change_log(trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_change_log_request ON audit_change_log(request_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_change_log_created_at ON audit_change_log(created_at)",
]

AUDIT_INSERT_SQL = """
INSERT INTO audit_change_log (
    domain_name,
    entity_type,
    entity_id,
    public_id,
    action,
    actor_key,
    user_id,
    device_id,
    request_id,
    trace_id,
    source,
    origin_event_type,
    origin_public_id,
    before_json,
    after_json,
    changed_fields
) VALUES (
    :domain_name,
    :entity_type,
    :entity_id,
    :public_id,
    :action,
    :actor_key,
    :user_id,
    :device_id,
    :request_id,
    :trace_id,
    :source,
    :origin_event_type,
    :origin_public_id,
    :before_json,
    :after_json,
    :changed_fields
)
"""

_METADATA_DOMAIN_MAP = {
    ContentBase.metadata: "content",
    RuntimeBase.metadata: "runtime",
    ReviewBase.metadata: "review",
    AgentBase.metadata: "agent",
    LegacyBase.metadata: "legacy",
    CoreBase.metadata: "shadow",
}

_DOMAIN_ENGINE_MAP = {
    "content": content_engine,
    "runtime": runtime_engine,
    "review": review_engine,
    "agent": agent_engine,
    "legacy": legacy_engine,
    "shadow": core_engine,
}


@dataclass(frozen=True)
class AuditTarget:
    name: str
    engine: Engine
    path: Path | None


def audit_targets() -> list[AuditTarget]:
    return [
        AuditTarget("content", content_engine, get_sqlite_path(str(content_engine.url))),
        AuditTarget("runtime", runtime_engine, get_sqlite_path(str(runtime_engine.url))),
        AuditTarget("review", review_engine, get_sqlite_path(str(review_engine.url))),
        AuditTarget("agent", agent_engine, get_sqlite_path(str(agent_engine.url))),
        AuditTarget("legacy", legacy_engine, get_sqlite_path(str(legacy_engine.url))),
        AuditTarget("shadow", core_engine, get_sqlite_path(str(core_engine.url))),
    ]


def ensure_audit_tables(*, include_shadow: bool = False) -> None:
    for target in audit_targets():
        if target.name == "shadow" and not include_shadow:
            continue
        with target.engine.begin() as connection:
            connection.exec_driver_sql(AUDIT_CHANGE_LOG_DDL)
            for ddl in AUDIT_INDEX_DDLS:
                connection.exec_driver_sql(ddl)


def _truncate_text(value: str, *, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(+{len(value) - limit} chars)"


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    return _truncate_text(str(value), limit=400)


def _mapper_for_target(target: Any) -> Mapper | None:
    if target is None:
        return None
    if isinstance(target, Mapper):
        return target

    mapper = getattr(target, "__mapper__", None)
    if mapper is not None:
        return mapper

    try:
        inspection = sa_inspect(target)
    except Exception:
        return None

    mapper = getattr(inspection, "mapper", None)
    if mapper is not None:
        return mapper
    return None


def resolve_audit_domain_name(target: Any = None, *, domain_name: str | None = None) -> str:
    if domain_name:
        return str(domain_name)

    mapper = _mapper_for_target(target)
    metadata = getattr(getattr(mapper, "local_table", None), "metadata", None)
    resolved = _METADATA_DOMAIN_MAP.get(metadata)
    if resolved:
        return resolved
    raise ValueError("unable to resolve audit domain")


def model_to_audit_dict(
    target: Any | None,
    *,
    include_fields: Iterable[str] | None = None,
    exclude_fields: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    if target is None:
        return None
    if isinstance(target, Mapping):
        payload = {str(key): _json_safe_value(value) for key, value in target.items()}
        if include_fields is not None:
            include = {str(item) for item in include_fields}
            payload = {key: value for key, value in payload.items() if key in include}
        if exclude_fields is not None:
            exclude = {str(item) for item in exclude_fields}
            payload = {key: value for key, value in payload.items() if key not in exclude}
        return payload

    mapper = _mapper_for_target(target)
    if mapper is None:
        return {"value": _json_safe_value(target)}

    include = {str(item) for item in include_fields} if include_fields is not None else None
    exclude = {str(item) for item in exclude_fields} if exclude_fields is not None else set()
    payload: dict[str, Any] = {}
    for column in mapper.columns:
        key = str(column.key)
        if include is not None and key not in include:
            continue
        if key in exclude:
            continue
        try:
            value = getattr(target, key)
        except Exception:
            continue
        payload[key] = _json_safe_value(value)
    return payload


def diff_audit_fields(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> list[str]:
    before_payload = dict(before or {})
    after_payload = dict(after or {})
    changed = {
        key
        for key in set(before_payload.keys()) | set(after_payload.keys())
        if before_payload.get(key) != after_payload.get(key)
    }
    return sorted(changed)


def _resolve_entity_id(target: Any, *, fallback: Any = None) -> str:
    if fallback is not None:
        return str(fallback)

    mapper = _mapper_for_target(target)
    if mapper is not None:
        primary_values: list[str] = []
        for column in mapper.primary_key:
            try:
                value = getattr(target, column.key)
            except Exception:
                value = None
            if value is not None and str(value).strip():
                primary_values.append(str(value))
        if primary_values:
            return ":".join(primary_values)

    for attr in ("id", "concept_id", "session_id", "wrong_answer_id"):
        value = getattr(target, attr, None)
        if value is not None and str(value).strip():
            return str(value)

    raise ValueError("audit entity_id is required")


def _resolve_public_id(target: Any, *, fallback: Any = None) -> str | None:
    if fallback is not None:
        text = str(fallback).strip()
        return text or None

    candidate = getattr(target, "id", None)
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()

    for attr in ("concept_id", "session_id", "question_fingerprint", "actor_key"):
        value = getattr(target, attr, None)
        text = str(value or "").strip()
        if text:
            return text
    return None


def _resolve_connection(
    db: Session | None,
    *,
    target: Any = None,
    domain_name: str | None = None,
) -> tuple[Any, bool]:
    if db is not None:
        mapper = _mapper_for_target(target)
        if mapper is not None:
            return db.connection(bind_arguments={"mapper": mapper}), False

    resolved_domain = resolve_audit_domain_name(target, domain_name=domain_name)
    engine = _DOMAIN_ENGINE_MAP[resolved_domain]
    return engine.begin(), True


def log_audit_change(
    *,
    db: Session | None = None,
    target: Any = None,
    domain_name: str | None = None,
    entity_type: str | None = None,
    entity_id: Any | None = None,
    public_id: Any | None = None,
    action: str,
    before: Mapping[str, Any] | Any | None = None,
    after: Mapping[str, Any] | Any | None = None,
    actor: Mapping[str, Any] | None = None,
    actor_key: str | None = None,
    user_id: str | None = None,
    device_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    source: str | None = None,
    origin_event_type: str | None = None,
    origin_public_id: str | None = None,
) -> None:
    resolved_domain = resolve_audit_domain_name(target, domain_name=domain_name)
    before_payload = model_to_audit_dict(before)
    after_payload = model_to_audit_dict(after)
    changed_fields = diff_audit_fields(before_payload, after_payload)
    request_context = get_llm_audit_request_context()
    request_user_id, request_device_id = get_request_identity()

    effective_user_id = (
        user_id
        or (str(actor.get("paper_user_id") or actor.get("scope_user_id") or "").strip() if actor else None)
        or request_context.get("actor_user_id")
        or request_user_id
    )
    effective_device_id = (
        device_id
        or (str(actor.get("paper_device_id") or actor.get("scope_device_id") or "").strip() if actor else None)
        or request_context.get("actor_device_id")
        or request_device_id
    )
    effective_actor_key = actor_key
    if not effective_actor_key and actor:
        effective_actor_key = str(actor.get("actor_key") or "").strip() or None
    if not effective_actor_key and (effective_user_id or effective_device_id):
        effective_actor_key = build_actor_key(effective_user_id, effective_device_id)

    payload = {
        "domain_name": resolved_domain,
        "entity_type": entity_type
        or getattr(target, "__name__", getattr(target, "__class__", type(target)).__name__),
        "entity_id": _resolve_entity_id(target, fallback=entity_id),
        "public_id": _resolve_public_id(target, fallback=public_id),
        "action": str(action),
        "actor_key": effective_actor_key,
        "user_id": effective_user_id,
        "device_id": effective_device_id,
        "request_id": request_id or request_context.get("request_id"),
        "trace_id": trace_id or request_context.get("request_id"),
        "source": source or ("http" if request_context.get("request_id") else "system"),
        "origin_event_type": origin_event_type,
        "origin_public_id": origin_public_id,
        "before_json": json.dumps(before_payload, ensure_ascii=False, sort_keys=True) if before_payload is not None else None,
        "after_json": json.dumps(after_payload, ensure_ascii=False, sort_keys=True) if after_payload is not None else None,
        "changed_fields": json.dumps(changed_fields, ensure_ascii=False) if changed_fields else None,
    }

    connection_context, should_close = _resolve_connection(db, target=target, domain_name=resolved_domain)
    if should_close:
        with connection_context as connection:
            connection.exec_driver_sql(AUDIT_INSERT_SQL, payload)
        return

    connection_context.exec_driver_sql(AUDIT_INSERT_SQL, payload)
