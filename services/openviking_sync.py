from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session as OrmSession

from learning_tracking_models import (
    DailyLearningLog,
    DailyReviewPaper,
    DailyReviewPaperItem,
    LearningActivity,
    LearningInsight,
    LearningSession,
    QuestionRecord,
    WrongAnswerRetry,
    WrongAnswerV2,
)
from models import (
    Chapter,
    ConceptLink,
    ConceptMastery,
    DailyUpload,
    FeynmanSession,
    QuizSession,
    SessionLocal,
    TestRecord,
    Variation,
    WrongAnswer,
)
from services.data_identity import ensure_learning_identity_schema
from services.openviking_service import (
    BASE_DIR,
    get_openviking_config,
    is_openviking_enabled,
    openviking_add_resource,
    openviking_mkdir,
    openviking_remove_uri,
    openviking_stat,
)

logger = logging.getLogger(__name__)

SYNC_SCHEMA_VERSION = "openviking-sync.v1"
_SESSION_QUEUE_KEY = "openviking_sync_pending"
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._=-]+")
_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tls-openviking-sync")
_SYNC_HOOKS_INSTALLED = False
_ENSURED_REMOTE_DIRS: set[str] = set()
_ENSURED_REMOTE_DIRS_LOCK = threading.Lock()

SUPPORTED_SYNC_MODELS: tuple[type[Any], ...] = (
    DailyUpload,
    Chapter,
    ConceptMastery,
    TestRecord,
    FeynmanSession,
    ConceptLink,
    Variation,
    WrongAnswer,
    QuizSession,
    LearningSession,
    LearningActivity,
    QuestionRecord,
    DailyLearningLog,
    LearningInsight,
    WrongAnswerV2,
    WrongAnswerRetry,
    DailyReviewPaper,
    DailyReviewPaperItem,
)

MODEL_NAME_MAP = {model.__name__.lower(): model for model in SUPPORTED_SYNC_MODELS}

SUMMARY_FIELD_SPECS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("description", "Description"),
    ("chapter_title", "Chapter Title"),
    ("content_summary", "Content Summary"),
    ("raw_content", "Raw Content"),
    ("knowledge_point", "Knowledge Point"),
    ("key_point", "Key Point"),
    ("ai_question", "AI Question"),
    ("question_text", "Question Text"),
    ("question", "Question"),
    ("ai_explanation", "AI Explanation"),
    ("explanation", "Explanation"),
    ("ai_feedback", "AI Feedback"),
    ("options", "Options"),
    ("ai_options", "AI Options"),
    ("dialogue", "Dialogue"),
    ("questions", "Questions"),
    ("answers", "Answers"),
    ("data", "Activity Data"),
    ("related_data", "Related Data"),
    ("knowledge_points_covered", "Knowledge Points Covered"),
    ("weak_knowledge_points", "Weak Knowledge Points"),
    ("weak_points", "Weak Points"),
    ("variant_data", "Variant Data"),
    ("fusion_data", "Fusion Data"),
    ("snapshot", "Snapshot"),
)

METADATA_FIELDS: tuple[str, ...] = (
    "user_id",
    "device_id",
    "session_type",
    "status",
    "chapter_id",
    "concept_id",
    "knowledge_point",
    "session_id",
    "question_index",
    "test_type",
    "question_type",
    "difficulty",
    "severity_tag",
    "mastery_status",
    "paper_date",
    "created_at",
    "updated_at",
    "tested_at",
    "answered_at",
    "first_wrong_at",
    "last_wrong_at",
    "next_review_date",
)


@dataclass(frozen=True)
class OpenVikingSyncConfig:
    enabled: bool
    upload_enabled: bool
    export_dir: Path
    root_uri: str
    wait: bool
    timeout: float
    batch_size: int


@dataclass(frozen=True)
class SyncOperation:
    action: str
    identity_key: str
    model_name: str
    table_name: str
    record_key: dict[str, Any]
    export_path: Path
    resource_uri: str
    document_title: str
    payload: dict[str, Any] | None = None
    document_text: str | None = None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_openviking_sync_config() -> OpenVikingSyncConfig:
    enabled_raw = os.getenv("OPENVIKING_SYNC_ENABLED")
    if enabled_raw is None:
        sync_enabled = is_openviking_enabled()
    else:
        sync_enabled = _env_flag("OPENVIKING_SYNC_ENABLED", default=False)

    export_dir_raw = (os.getenv("OPENVIKING_SYNC_EXPORT_DIR") or "data/openviking_exports").strip()
    export_dir = Path(export_dir_raw)
    if not export_dir.is_absolute():
        export_dir = (BASE_DIR / export_dir).resolve()

    base_config = get_openviking_config()
    root_uri = (os.getenv("OPENVIKING_SYNC_ROOT_URI") or "viking://resources/true-learning-system").strip().rstrip("/")
    if not root_uri:
        root_uri = "viking://resources/true-learning-system"

    return OpenVikingSyncConfig(
        enabled=sync_enabled,
        upload_enabled=sync_enabled and bool(base_config.enabled and base_config.url),
        export_dir=export_dir,
        root_uri=root_uri,
        wait=_env_flag("OPENVIKING_SYNC_WAIT", default=False),
        timeout=max(base_config.timeout, 1.0),
        batch_size=max(_env_int("OPENVIKING_SYNC_BATCH_SIZE", 50), 1),
    )


def install_openviking_sync_hooks() -> None:
    global _SYNC_HOOKS_INSTALLED
    if _SYNC_HOOKS_INSTALLED:
        return

    event.listen(OrmSession, "after_flush", _capture_session_sync_changes)
    event.listen(OrmSession, "after_commit", _dispatch_session_sync_changes)
    event.listen(OrmSession, "after_rollback", _clear_session_sync_changes)
    _SYNC_HOOKS_INSTALLED = True


def list_supported_openviking_models() -> list[type[Any]]:
    return list(SUPPORTED_SYNC_MODELS)


def backfill_openviking_records(
    *,
    model_names: Sequence[str] | None = None,
    limit_per_model: int | None = None,
    batch_size: int | None = None,
    export_only: bool = False,
) -> dict[str, int]:
    config = get_openviking_sync_config()
    selected_models = _resolve_backfill_models(model_names)
    effective_batch_size = max(int(batch_size or config.batch_size), 1)
    counts: dict[str, int] = {}

    ensure_learning_identity_schema()

    with SessionLocal() as db:
        for model in selected_models:
            query = db.query(model)
            pk_columns = list(sa_inspect(model).primary_key)
            if pk_columns:
                query = query.order_by(*pk_columns)

            processed = 0
            pending: list[SyncOperation] = []

            for instance in query.yield_per(effective_batch_size):
                if limit_per_model is not None and processed >= limit_per_model:
                    break

                operation = build_sync_operation(instance, action="upsert")
                if operation is None:
                    continue

                pending.append(operation)
                processed += 1

                if len(pending) >= effective_batch_size:
                    process_sync_operations(pending, export_only=export_only)
                    pending = []

            if pending:
                process_sync_operations(pending, export_only=export_only)

            counts[model.__name__] = processed

    return counts


def bulk_import_openviking_exports(
    *,
    model_names: Sequence[str] | None = None,
) -> dict[str, int]:
    config = get_openviking_sync_config()
    if not config.enabled:
        return {}
    if not config.upload_enabled:
        raise RuntimeError("OpenViking upload is disabled.")

    selected_models = _resolve_backfill_models(model_names)
    config.export_dir.mkdir(parents=True, exist_ok=True)
    _ensure_remote_directory(config.root_uri)

    counts: dict[str, int] = {}
    bulk_request_timeout = max(config.timeout, 300.0)
    for model in selected_models:
        table_name = str(sa_inspect(model).local_table.name)
        local_dir = config.export_dir / table_name
        file_count = len(list(local_dir.glob("*.md"))) if local_dir.exists() else 0
        counts[model.__name__] = file_count
        if file_count == 0:
            continue

        target_uri = f"{config.root_uri}/{table_name}"
        openviking_remove_uri(target_uri, recursive=True, missing_ok=True)
        openviking_add_resource(
            path=str(local_dir),
            to=target_uri,
            reason=f"Bulk import {model.__name__} exports from True Learning System.",
            instruction="Import all exported records in this directory as searchable knowledge resources.",
            wait=config.wait,
            timeout=config.timeout,
            preserve_structure=False,
            request_timeout=bulk_request_timeout,
        )

    return counts


def build_sync_operation(instance: Any, *, action: str) -> SyncOperation | None:
    if not _is_supported_instance(instance):
        return None

    mapper = sa_inspect(instance).mapper
    model_name = mapper.class_.__name__
    table_name = str(mapper.local_table.name)
    record_key = _extract_primary_key(instance)
    if not record_key:
        return None

    identity_key = f"{model_name}:{json.dumps(record_key, sort_keys=True, ensure_ascii=False)}"
    slug = _build_record_slug(model_name, record_key)
    config = get_openviking_sync_config()
    export_path = config.export_dir / table_name / f"{slug}.md"
    resource_uri = f"{config.root_uri}/{table_name}/{slug}.md"
    document_title = _build_document_title(model_name, record_key, instance)

    if action == "delete":
        return SyncOperation(
            action=action,
            identity_key=identity_key,
            model_name=model_name,
            table_name=table_name,
            record_key=record_key,
            export_path=export_path,
            resource_uri=resource_uri,
            document_title=document_title,
        )

    record = _serialize_instance(instance)
    payload = {
        "schema_version": SYNC_SCHEMA_VERSION,
        "source_system": "true-learning-system",
        "model": model_name,
        "table": table_name,
        "record_key": record_key,
        "record_slug": slug,
        "resource_uri": resource_uri,
        "document_title": document_title,
        "captured_at": datetime.now().isoformat(),
        "record": record,
    }
    document_text = _render_document(payload)

    return SyncOperation(
        action=action,
        identity_key=identity_key,
        model_name=model_name,
        table_name=table_name,
        record_key=record_key,
        export_path=export_path,
        resource_uri=resource_uri,
        document_title=document_title,
        payload=payload,
        document_text=document_text,
    )


def process_sync_operations(
    operations: Sequence[SyncOperation],
    *,
    export_only: bool = False,
) -> dict[str, int]:
    config = get_openviking_sync_config()
    if not operations:
        return {"upserted": 0, "deleted": 0, "failed": 0}
    if not config.enabled and not export_only:
        return {"upserted": 0, "deleted": 0, "failed": 0}

    config.export_dir.mkdir(parents=True, exist_ok=True)

    counts = {"upserted": 0, "deleted": 0, "failed": 0}
    upload_skipped_logged = False

    for operation in operations:
        try:
            if operation.action == "delete":
                _remove_local_export(operation.export_path)
                if not export_only and config.upload_enabled:
                    _delete_remote_resource(operation)
                counts["deleted"] += 1
                continue

            _write_local_export(operation)

            if export_only:
                counts["upserted"] += 1
                continue

            if not config.upload_enabled:
                if not upload_skipped_logged:
                    logger.warning("OpenViking sync export completed locally, but remote upload is disabled.")
                    upload_skipped_logged = True
                counts["upserted"] += 1
                continue

            _upsert_remote_resource(operation, config)
            counts["upserted"] += 1
        except Exception as exc:
            counts["failed"] += 1
            logger.warning(
                "OpenViking sync failed for %s %s: %s",
                operation.model_name,
                operation.record_key,
                str(exc)[:300],
            )

    return counts


def _capture_session_sync_changes(session: OrmSession, flush_context: Any) -> None:
    if not get_openviking_sync_config().enabled:
        return

    pending: dict[str, SyncOperation] = session.info.setdefault(_SESSION_QUEUE_KEY, {})

    for instance in session.new:
        _queue_session_operation(pending, instance, action="upsert")

    for instance in session.dirty:
        if instance in session.deleted:
            continue
        if not session.is_modified(instance, include_collections=False):
            continue
        _queue_session_operation(pending, instance, action="upsert")

    for instance in session.deleted:
        _queue_session_operation(pending, instance, action="delete")


def _dispatch_session_sync_changes(session: OrmSession) -> None:
    pending = session.info.pop(_SESSION_QUEUE_KEY, None)
    if not pending:
        return
    _submit_sync_operations(list(pending.values()))


def _clear_session_sync_changes(session: OrmSession) -> None:
    session.info.pop(_SESSION_QUEUE_KEY, None)


def _submit_sync_operations(operations: Sequence[SyncOperation]) -> None:
    config = get_openviking_sync_config()
    if not operations or not config.enabled:
        return
    _SYNC_EXECUTOR.submit(process_sync_operations, list(operations))


def _queue_session_operation(pending: dict[str, SyncOperation], instance: Any, *, action: str) -> None:
    operation = build_sync_operation(instance, action=action)
    if operation is None:
        return
    pending[operation.identity_key] = operation


def _resolve_backfill_models(model_names: Sequence[str] | None) -> list[type[Any]]:
    if not model_names:
        return list(SUPPORTED_SYNC_MODELS)

    resolved: list[type[Any]] = []
    for name in model_names:
        key = str(name or "").strip().lower()
        model = MODEL_NAME_MAP.get(key)
        if model is None:
            raise ValueError(f"Unsupported model for OpenViking backfill: {name}")
        resolved.append(model)
    return resolved


def _is_supported_instance(instance: Any) -> bool:
    return any(isinstance(instance, model) for model in SUPPORTED_SYNC_MODELS)


def _extract_primary_key(instance: Any) -> dict[str, Any]:
    mapper = sa_inspect(instance).mapper
    record_key: dict[str, Any] = {}
    for column in mapper.primary_key:
        value = _json_safe_value(getattr(instance, column.key, None), depth=2)
        if value is None:
            return {}
        record_key[column.key] = value
    return record_key


def _build_record_slug(model_name: str, record_key: Mapping[str, Any]) -> str:
    parts = [f"{_sanitize_component(key)}={_sanitize_component(value)}" for key, value in record_key.items()]
    slug = "__".join(part for part in parts if part)
    if not slug:
        slug = _sanitize_component(model_name)

    if len(slug) <= 120:
        return slug

    digest = hashlib.sha1(json.dumps(record_key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return f"{_sanitize_component(model_name)}-{digest}"


def _sanitize_component(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return "blank"
    sanitized = _SAFE_COMPONENT_RE.sub("_", text).strip("._")
    if sanitized:
        return sanitized[:80]
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _build_document_title(model_name: str, record_key: Mapping[str, Any], instance: Any) -> str:
    key_text = ", ".join(f"{key}={value}" for key, value in record_key.items())
    label = None
    for field in ("title", "chapter_title", "name", "knowledge_point", "key_point", "concept_id", "session_id"):
        value = getattr(instance, field, None)
        text = _to_text(value)
        if text:
            label = text
            break

    if label and key_text:
        return f"{model_name}: {key_text} - {label}"
    if label:
        return f"{model_name}: {label}"
    return f"{model_name}: {key_text}"


def _serialize_instance(instance: Any) -> dict[str, Any]:
    mapper = sa_inspect(instance).mapper
    record: dict[str, Any] = {}
    for column in mapper.columns:
        record[column.key] = _json_safe_value(getattr(instance, column.key, None))
    return record


def _json_safe_value(value: Any, *, depth: int = 6) -> Any:
    if depth <= 0:
        return _to_text(value)

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item, depth=depth - 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return _json_safe_value(enum_value, depth=depth - 1)
    return _to_text(value)


def _render_document(payload: Mapping[str, Any]) -> str:
    record = payload.get("record") or {}
    metadata_lines = [f"- model: `{payload['model']}`", f"- table: `{payload['table']}`"]
    key_text = ", ".join(f"{key}={value}" for key, value in (payload.get("record_key") or {}).items())
    if key_text:
        metadata_lines.append(f"- record_key: `{key_text}`")
    metadata_lines.append(f"- resource_uri: `{payload['resource_uri']}`")
    metadata_lines.append(f"- captured_at: `{payload['captured_at']}`")

    for field in METADATA_FIELDS:
        value = record.get(field)
        rendered = _render_inline_value(value)
        if rendered:
            metadata_lines.append(f"- {field}: {rendered}")

    sections = [f"# {payload['document_title']}", "", *metadata_lines]
    summary_sections = _build_summary_sections(record)
    if summary_sections:
        sections.extend(["", "## Searchable Content", ""])
        sections.extend(summary_sections)

    sections.extend(
        [
            "",
            "## Payload",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(sections)


def _build_summary_sections(record: Mapping[str, Any]) -> list[str]:
    sections: list[str] = []
    for field, label in SUMMARY_FIELD_SPECS:
        if field not in record:
            continue
        rendered = _render_block_value(record.get(field))
        if not rendered:
            continue
        sections.append(f"### {label}\n{rendered}")
    return sections


def _render_inline_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
    else:
        text = json.dumps(_json_safe_value(value, depth=3), ensure_ascii=False, sort_keys=True)
    if not text:
        return ""
    if len(text) > 180:
        return f"`{text[:177].rstrip()}...`"
    return f"`{text}`"


def _render_block_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        text = json.dumps(_json_safe_value(value, depth=4), ensure_ascii=False, indent=2)
    if not text:
        return ""
    if len(text) > 2400:
        text = f"{text[:2397].rstrip()}..."
    return text


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _write_local_export(operation: SyncOperation) -> None:
    if not operation.document_text:
        return
    operation.export_path.parent.mkdir(parents=True, exist_ok=True)
    operation.export_path.write_text(operation.document_text, encoding="utf-8")


def _remove_local_export(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _upsert_remote_resource(operation: SyncOperation, config: OpenVikingSyncConfig) -> None:
    table_uri = f"{config.root_uri}/{operation.table_name}"
    _ensure_remote_directory(config.root_uri)
    _ensure_remote_directory(table_uri)
    # OpenViking materializes uploaded resources as directory-like nodes, so replace them recursively.
    openviking_remove_uri(operation.resource_uri, recursive=True, missing_ok=True)
    openviking_add_resource(
        path=str(operation.export_path),
        to=operation.resource_uri,
        reason=f"Mirror {operation.model_name} records from True Learning System.",
        instruction="Keep this resource in sync with the source database record.",
        wait=config.wait,
        timeout=config.timeout,
        preserve_structure=False,
    )


def _delete_remote_resource(operation: SyncOperation) -> None:
    openviking_remove_uri(operation.resource_uri, recursive=True, missing_ok=True)


def _ensure_remote_directory(uri: str) -> None:
    with _ENSURED_REMOTE_DIRS_LOCK:
        if uri in _ENSURED_REMOTE_DIRS:
            return

    if openviking_stat(uri, missing_ok=True) is None:
        openviking_mkdir(uri)

    with _ENSURED_REMOTE_DIRS_LOCK:
        _ENSURED_REMOTE_DIRS.add(uri)
