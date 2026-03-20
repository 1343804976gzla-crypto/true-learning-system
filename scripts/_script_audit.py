from __future__ import annotations

import json
import sqlite3
from typing import Any


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


def ensure_audit_table(conn: sqlite3.Connection) -> None:
    conn.execute(AUDIT_CHANGE_LOG_DDL)


def write_script_audit(
    conn: sqlite3.Connection,
    *,
    domain_name: str,
    entity_type: str,
    entity_id: str,
    action: str,
    after_payload: dict[str, Any],
    origin_event_type: str,
    origin_public_id: str,
) -> None:
    ensure_audit_table(conn)
    conn.execute(
        """
        INSERT INTO audit_change_log (
            domain_name, entity_type, entity_id, public_id, action,
            actor_key, source, origin_event_type, origin_public_id,
            after_json, changed_fields
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            domain_name,
            entity_type,
            entity_id,
            origin_public_id,
            action,
            "system:script",
            "script",
            origin_event_type,
            origin_public_id,
            json.dumps(after_payload, ensure_ascii=False, sort_keys=True),
            json.dumps(sorted(after_payload.keys()), ensure_ascii=False),
        ),
    )
