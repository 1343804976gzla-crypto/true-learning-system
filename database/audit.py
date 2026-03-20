from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from database.domains import (
    agent_engine,
    content_engine,
    core_engine,
    legacy_engine,
    review_engine,
    runtime_engine,
    get_sqlite_path,
)


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
