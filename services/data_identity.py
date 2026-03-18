from __future__ import annotations

from contextvars import ContextVar
import os
from threading import Lock
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession

from models import engine

DEVICE_ID_HEADER = "x-tls-device-id"
USER_ID_HEADER = "x-tls-user-id"
DEFAULT_DEVICE_ID = "local-default"
SINGLE_USER_MODE_ENV = "SINGLE_USER_MODE"

_current_device_id: ContextVar[str | None] = ContextVar("tls_device_id", default=None)
_current_user_id: ContextVar[str | None] = ContextVar("tls_user_id", default=None)
_IDENTITY_SCHEMA_READY = False
_IDENTITY_SCHEMA_LOCK = Lock()
_SINGLE_USER_DEVICE_CACHE: tuple[str, ...] | None = None
_SINGLE_USER_DEVICE_CACHE_LOCK = Lock()

_IDENTITY_TABLES = (
    "daily_uploads",
    "concept_mastery",
    "test_records",
    "learning_sessions",
    "learning_activities",
    "question_records",
    "wrong_answers_v2",
    "wrong_answer_retries",
)
_SINGLE_USER_DEVICE_TABLES = _IDENTITY_TABLES + (
    "daily_review_papers",
    "daily_learning_logs",
    "batch_exam_states",
    "agent_sessions",
)


def _normalize_identity(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _is_truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_single_user_mode() -> bool:
    return _is_truthy_env(os.getenv(SINGLE_USER_MODE_ENV))


def _build_actor_key_from_resolved(user_id: str | None, device_id: str | None) -> str:
    if user_id:
        return f"user:{user_id}|device:{device_id or DEFAULT_DEVICE_ID}"
    return f"device:{device_id or DEFAULT_DEVICE_ID}"


def _load_single_user_device_aliases() -> list[str]:
    global _SINGLE_USER_DEVICE_CACHE
    if _SINGLE_USER_DEVICE_CACHE is not None:
        return list(_SINGLE_USER_DEVICE_CACHE)

    with _SINGLE_USER_DEVICE_CACHE_LOCK:
        if _SINGLE_USER_DEVICE_CACHE is not None:
            return list(_SINGLE_USER_DEVICE_CACHE)

        aliases = {DEFAULT_DEVICE_ID}
        with engine.begin() as connection:
            for table_name in dict.fromkeys(_SINGLE_USER_DEVICE_TABLES):
                existing_columns = {
                    str(row[1]).lower()
                    for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
                }
                if "device_id" not in existing_columns:
                    continue

                rows = connection.exec_driver_sql(
                    f"""
                    SELECT DISTINCT device_id
                    FROM {table_name}
                    WHERE device_id IS NOT NULL AND TRIM(device_id) <> ''
                    """
                ).fetchall()
                for row in rows:
                    device_id = _normalize_identity(row[0])
                    if not device_id:
                        continue
                    if device_id == DEFAULT_DEVICE_ID or device_id.startswith("local-"):
                        aliases.add(device_id)

        ordered = [DEFAULT_DEVICE_ID] + sorted(alias for alias in aliases if alias != DEFAULT_DEVICE_ID)
        _SINGLE_USER_DEVICE_CACHE = tuple(ordered)
        return list(_SINGLE_USER_DEVICE_CACHE)


def _single_user_device_aliases(device_id: str | None = None) -> list[str]:
    aliases = _load_single_user_device_aliases()
    normalized_device = _normalize_identity(device_id)
    if normalized_device and (normalized_device == DEFAULT_DEVICE_ID or normalized_device.startswith("local-")):
        aliases.append(normalized_device)
    return list(dict.fromkeys(aliases))


def clear_identity_caches_for_tests() -> None:
    global _SINGLE_USER_DEVICE_CACHE
    _SINGLE_USER_DEVICE_CACHE = None


def canonicalize_storage_identity(user_id: str | None = None, device_id: str | None = None) -> tuple[str | None, str | None]:
    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)
    if is_single_user_mode():
        return None, DEFAULT_DEVICE_ID
    return normalized_user, normalized_device


def resolve_actor_identity(user_id: str | None = None, device_id: str | None = None) -> tuple[str | None, str]:
    if is_single_user_mode():
        return None, DEFAULT_DEVICE_ID

    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)
    if normalized_user and (not normalized_device or normalized_device == DEFAULT_DEVICE_ID):
        normalized_device = f"user:{normalized_user}"
    elif not normalized_device:
        normalized_device = DEFAULT_DEVICE_ID
    return normalized_user, normalized_device


def build_actor_key(user_id: str | None = None, device_id: str | None = None) -> str:
    normalized_user, normalized_device = resolve_actor_identity(user_id, device_id)
    return _build_actor_key_from_resolved(normalized_user, normalized_device)


def build_actor_key_aliases(user_id: str | None = None, device_id: str | None = None) -> list[str]:
    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)
    if is_single_user_mode():
        return [
            _build_actor_key_from_resolved(None, alias_device_id)
            for alias_device_id in _single_user_device_aliases(normalized_device)
        ]
    aliases = [build_actor_key(normalized_user, normalized_device)]
    if normalized_user and normalized_device in {None, DEFAULT_DEVICE_ID, f"user:{normalized_user}"}:
        legacy_key = f"user:{normalized_user}|device:{DEFAULT_DEVICE_ID}"
        if legacy_key not in aliases:
            aliases.append(legacy_key)
    elif not normalized_user and normalized_device and normalized_device != DEFAULT_DEVICE_ID and normalized_device.startswith("local-"):
        legacy_key = build_actor_key(None, DEFAULT_DEVICE_ID)
        if legacy_key not in aliases:
            aliases.append(legacy_key)
    return aliases


def build_device_scope_aliases(user_id: str | None = None, device_id: str | None = None) -> list[str]:
    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)
    if is_single_user_mode():
        return _single_user_device_aliases(normalized_device)
    aliases: list[str] = []

    if normalized_user:
        if normalized_device and normalized_device not in {DEFAULT_DEVICE_ID, f"user:{normalized_user}"}:
            aliases.append(normalized_device)
        return aliases

    if not normalized_device:
        return aliases

    aliases.append(normalized_device)
    if normalized_device != DEFAULT_DEVICE_ID and normalized_device.startswith("local-"):
        aliases.append(DEFAULT_DEVICE_ID)
    return list(dict.fromkeys(aliases))


def resolve_query_identity(user_id: str | None = None, device_id: str | None = None) -> tuple[str | None, str | None]:
    if is_single_user_mode():
        return None, DEFAULT_DEVICE_ID

    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)
    if normalized_user and normalized_device in {DEFAULT_DEVICE_ID, f"user:{normalized_user}"}:
        normalized_device = None
    return normalized_user, normalized_device


def resolve_request_actor_scope(
    user_id: str | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    request_user_id, request_device_id = get_request_identity()
    candidate_user_id = _normalize_identity(user_id) or request_user_id
    candidate_device_id = _normalize_identity(device_id)
    if candidate_device_id is None and request_device_id and (
        request_device_id != DEFAULT_DEVICE_ID or not candidate_user_id
    ):
        candidate_device_id = request_device_id

    scope_user_id, scope_device_id = resolve_query_identity(candidate_user_id, candidate_device_id)
    paper_user_id, paper_device_id = resolve_actor_identity(candidate_user_id, candidate_device_id)

    return {
        "request_user_id": request_user_id,
        "request_device_id": request_device_id,
        "candidate_user_id": candidate_user_id,
        "candidate_device_id": candidate_device_id,
        "scope_user_id": scope_user_id,
        "scope_device_id": scope_device_id,
        "scope_device_ids": build_device_scope_aliases(scope_user_id, scope_device_id),
        "paper_user_id": paper_user_id,
        "paper_device_id": paper_device_id,
        "actor_key": build_actor_key(candidate_user_id, candidate_device_id),
        "actor_keys": build_actor_key_aliases(candidate_user_id, candidate_device_id),
    }


def resolve_request_identity(request: Request) -> tuple[str | None, str | None]:
    user_id = _normalize_identity(request.headers.get(USER_ID_HEADER))
    device_id = _normalize_identity(request.headers.get(DEVICE_ID_HEADER))
    if is_single_user_mode():
        return None, DEFAULT_DEVICE_ID
    if not user_id and not device_id:
        device_id = DEFAULT_DEVICE_ID
    return user_id, device_id


def set_request_identity(*, user_id: str | None, device_id: str | None) -> tuple[Any, Any]:
    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)
    if is_single_user_mode():
        normalized_user, normalized_device = canonicalize_storage_identity(normalized_user, normalized_device)
    user_token = _current_user_id.set(normalized_user)
    device_token = _current_device_id.set(normalized_device or (DEFAULT_DEVICE_ID if not normalized_user else None))
    return user_token, device_token


def reset_request_identity(tokens: tuple[Any, Any]) -> None:
    user_token, device_token = tokens
    _current_user_id.reset(user_token)
    _current_device_id.reset(device_token)


def get_request_identity() -> tuple[str | None, str | None]:
    return _current_user_id.get(), _current_device_id.get()


def _model_supports_identity(instance: Any) -> bool:
    return any(hasattr(instance, attr) for attr in ("device_id", "user_id", "actor_key"))


def _apply_identity_to_instance(instance: Any, *, user_id: str | None, device_id: str | None) -> None:
    if not _model_supports_identity(instance):
        return
    normalized_user, normalized_device = resolve_actor_identity(user_id, device_id)
    if hasattr(instance, "user_id") and normalized_user and not getattr(instance, "user_id", None):
        setattr(instance, "user_id", normalized_user)
    if hasattr(instance, "device_id") and normalized_device and not getattr(instance, "device_id", None):
        setattr(instance, "device_id", normalized_device)
    if hasattr(instance, "actor_key") and not getattr(instance, "actor_key", None):
        setattr(instance, "actor_key", build_actor_key(normalized_user, normalized_device))


@event.listens_for(OrmSession, "before_flush")
def _populate_identity_columns(session: OrmSession, flush_context: Any, instances: Any) -> None:
    user_id, device_id = get_request_identity()
    if not user_id and not device_id:
        return

    for instance in session.new:
        _apply_identity_to_instance(instance, user_id=user_id, device_id=device_id)

    for instance in session.dirty:
        _apply_identity_to_instance(instance, user_id=user_id, device_id=device_id)


def _daily_review_paper_columns(connection: Any) -> set[str]:
    return {
        str(row[1]).lower()
        for row in connection.exec_driver_sql("PRAGMA table_info(daily_review_papers)").fetchall()
    }


def _daily_review_unique_indexes(connection: Any) -> list[list[str]]:
    unique_indexes: list[list[str]] = []
    for row in connection.exec_driver_sql("PRAGMA index_list(daily_review_papers)").fetchall():
        if not bool(row[2]):
            continue
        index_name = str(row[1])
        columns = [
            str(index_row[2]).lower()
            for index_row in connection.exec_driver_sql(f"PRAGMA index_info({index_name})").fetchall()
        ]
        unique_indexes.append(columns)
    return unique_indexes


def _daily_learning_log_columns(connection: Any) -> set[str]:
    return {
        str(row[1]).lower()
        for row in connection.exec_driver_sql("PRAGMA table_info(daily_learning_logs)").fetchall()
    }


def _daily_learning_log_unique_indexes(connection: Any) -> list[list[str]]:
    unique_indexes: list[list[str]] = []
    for row in connection.exec_driver_sql("PRAGMA index_list(daily_learning_logs)").fetchall():
        if not bool(row[2]):
            continue
        index_name = str(row[1])
        columns = [
            str(index_row[2]).lower()
            for index_row in connection.exec_driver_sql(f"PRAGMA index_info({index_name})").fetchall()
        ]
        unique_indexes.append(columns)
    return unique_indexes


def _rebuild_daily_learning_logs_table(connection: Any) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        connection.exec_driver_sql("DROP TABLE IF EXISTS daily_learning_logs_new")
        connection.exec_driver_sql(
            """
            CREATE TABLE daily_learning_logs_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id TEXT,
                device_id TEXT,
                actor_key TEXT NOT NULL,
                date DATE NOT NULL,
                total_sessions INTEGER DEFAULT 0,
                total_questions INTEGER DEFAULT 0,
                total_correct INTEGER DEFAULT 0,
                total_wrong INTEGER DEFAULT 0,
                average_score FLOAT DEFAULT 0.0,
                total_duration_seconds INTEGER DEFAULT 0,
                first_session_at DATETIME,
                last_session_at DATETIME,
                knowledge_points_covered JSON,
                weak_knowledge_points JSON,
                session_ids JSON,
                created_at DATETIME,
                updated_at DATETIME,
                CONSTRAINT uq_daily_learning_logs_actor_date UNIQUE (actor_key, date)
            )
            """
        )
        connection.exec_driver_sql("DROP TABLE IF EXISTS daily_learning_logs")
        connection.exec_driver_sql("ALTER TABLE daily_learning_logs_new RENAME TO daily_learning_logs")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_learning_logs_user_id ON daily_learning_logs(user_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_learning_logs_device_id ON daily_learning_logs(device_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_learning_logs_actor_key ON daily_learning_logs(actor_key)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_learning_logs_date ON daily_learning_logs(date)")
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")


def _ensure_daily_learning_log_schema(connection: Any) -> None:
    existing_columns = _daily_learning_log_columns(connection)
    if not existing_columns:
        return

    unique_indexes = _daily_learning_log_unique_indexes(connection)
    has_actor_columns = {"user_id", "device_id", "actor_key"}.issubset(existing_columns)
    has_actor_date_unique = ["actor_key", "date"] in unique_indexes
    has_legacy_date_unique = ["date"] in unique_indexes
    if has_actor_columns and has_actor_date_unique and not has_legacy_date_unique:
        return

    _rebuild_daily_learning_logs_table(connection)


def _rebuild_daily_review_papers_table(connection: Any, existing_columns: set[str]) -> None:
    old_rows = connection.exec_driver_sql(
        """
        SELECT id, paper_date, total_questions, config, created_at, updated_at
        FROM daily_review_papers
        ORDER BY id
        """
    ).mappings().all()

    if {"user_id", "device_id", "actor_key"}.issubset(existing_columns):
        identity_sql = """
        SELECT
            p.id AS paper_id,
            p.user_id AS paper_user_id,
            p.device_id AS paper_device_id,
            p.actor_key AS paper_actor_key,
            wa.user_id AS wrong_answer_user_id,
            wa.device_id AS wrong_answer_device_id
        FROM daily_review_papers p
        LEFT JOIN daily_review_paper_items pi ON pi.paper_id = p.id
        LEFT JOIN wrong_answers_v2 wa ON wa.id = pi.wrong_answer_id
        ORDER BY p.id, pi.id
        """
    else:
        identity_sql = """
        SELECT
            p.id AS paper_id,
            NULL AS paper_user_id,
            NULL AS paper_device_id,
            NULL AS paper_actor_key,
            wa.user_id AS wrong_answer_user_id,
            wa.device_id AS wrong_answer_device_id
        FROM daily_review_papers p
        LEFT JOIN daily_review_paper_items pi ON pi.paper_id = p.id
        LEFT JOIN wrong_answers_v2 wa ON wa.id = pi.wrong_answer_id
        ORDER BY p.id, pi.id
        """

    identity_rows = connection.exec_driver_sql(identity_sql).mappings().all()
    inferred_identity: dict[int, dict[str, set[str]]] = {}
    paper_fallback: dict[int, tuple[str | None, str | None, str | None]] = {}
    for row in identity_rows:
        paper_id = int(row["paper_id"])
        paper_fallback.setdefault(
            paper_id,
            (
                _normalize_identity(row.get("paper_user_id")),
                _normalize_identity(row.get("paper_device_id")),
                _normalize_identity(row.get("paper_actor_key")),
            ),
        )
        bucket = inferred_identity.setdefault(paper_id, {"user_ids": set(), "device_ids": set()})
        wrong_answer_user_id = _normalize_identity(row.get("wrong_answer_user_id"))
        wrong_answer_device_id = _normalize_identity(row.get("wrong_answer_device_id"))
        if wrong_answer_user_id:
            bucket["user_ids"].add(wrong_answer_user_id)
        if wrong_answer_device_id:
            bucket["device_ids"].add(wrong_answer_device_id)

    connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        connection.exec_driver_sql("DROP TABLE IF EXISTS daily_review_papers_new")
        connection.exec_driver_sql(
            """
            CREATE TABLE daily_review_papers_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id TEXT,
                device_id TEXT,
                actor_key TEXT NOT NULL,
                paper_date DATE NOT NULL,
                total_questions INTEGER DEFAULT 0,
                config JSON,
                created_at DATETIME,
                updated_at DATETIME,
                CONSTRAINT uq_daily_review_papers_actor_date UNIQUE (actor_key, paper_date)
            )
            """
        )
        for row in old_rows:
            paper_id = int(row["id"])
            fallback_user_id, fallback_device_id, fallback_actor_key = paper_fallback.get(
                paper_id,
                (None, None, None),
            )
            inferred = inferred_identity.get(paper_id, {"user_ids": set(), "device_ids": set()})
            user_id = fallback_user_id
            device_id = fallback_device_id
            if not user_id and len(inferred["user_ids"]) == 1:
                user_id = next(iter(inferred["user_ids"]))
            if not device_id and len(inferred["device_ids"]) == 1:
                device_id = next(iter(inferred["device_ids"]))
            normalized_user, normalized_device = resolve_actor_identity(user_id, device_id)
            actor_key = fallback_actor_key or build_actor_key(normalized_user, normalized_device)
            connection.exec_driver_sql(
                """
                INSERT INTO daily_review_papers_new (
                    id, user_id, device_id, actor_key, paper_date, total_questions, config, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    normalized_user,
                    normalized_device,
                    actor_key,
                    row["paper_date"],
                    row["total_questions"],
                    row["config"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        connection.exec_driver_sql("DROP TABLE daily_review_papers")
        connection.exec_driver_sql("ALTER TABLE daily_review_papers_new RENAME TO daily_review_papers")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_review_papers_user_id ON daily_review_papers(user_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_review_papers_device_id ON daily_review_papers(device_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_review_papers_actor_key ON daily_review_papers(actor_key)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_review_papers_paper_date ON daily_review_papers(paper_date)")
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")


def _ensure_daily_review_paper_schema(connection: Any) -> None:
    existing_columns = _daily_review_paper_columns(connection)
    if not existing_columns:
        return

    unique_indexes = _daily_review_unique_indexes(connection)
    has_actor_columns = {"user_id", "device_id", "actor_key"}.issubset(existing_columns)
    has_actor_date_unique = ["actor_key", "paper_date"] in unique_indexes
    has_legacy_date_unique = ["paper_date"] in unique_indexes
    if has_actor_columns and has_actor_date_unique and not has_legacy_date_unique:
        return

    _rebuild_daily_review_papers_table(connection, existing_columns)


def ensure_learning_identity_schema() -> None:
    global _IDENTITY_SCHEMA_READY
    if _IDENTITY_SCHEMA_READY:
        return

    with _IDENTITY_SCHEMA_LOCK:
        if _IDENTITY_SCHEMA_READY:
            return

        with engine.begin() as connection:
            dialect = connection.dialect.name
            if dialect != "sqlite":
                _IDENTITY_SCHEMA_READY = True
                return

            for table_name in _IDENTITY_TABLES:
                existing_columns = {
                    str(row[1]).lower()
                    for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
                }
                if not existing_columns:
                    continue

                if "user_id" not in existing_columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN user_id TEXT")
                if "device_id" not in existing_columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN device_id TEXT")

                connection.exec_driver_sql(
                    f"UPDATE {table_name} SET device_id = ? WHERE device_id IS NULL OR TRIM(device_id) = ''",
                    (DEFAULT_DEVICE_ID,),
                )

            _ensure_daily_review_paper_schema(connection)
            _ensure_daily_learning_log_schema(connection)

            _IDENTITY_SCHEMA_READY = True
