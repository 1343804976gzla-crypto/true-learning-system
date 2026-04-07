from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, JSON, MetaData, inspect
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_SOURCE_FILES: dict[str, str] = {
    "core": "learning.db",
    "content": "content_knowledge.db",
    "runtime": "learning_runtime.db",
    "review": "wrong_answer_review.db",
    "agent": "agent.db",
    "legacy": "legacy_compat.db",
}

DOMAIN_ORDER: tuple[str, ...] = ("core", "content", "runtime", "review", "agent", "legacy")


@dataclass(frozen=True)
class SourceDatabase:
    domain: str
    path: Path


def resolve_source_databases(
    *,
    source_dir: str | Path | None = None,
    overrides: dict[str, str] | None = None,
) -> list[SourceDatabase]:
    base_dir = Path(source_dir or (PROJECT_ROOT / "data")).resolve()
    override_map = {key: value for key, value in (overrides or {}).items() if value}
    databases: list[SourceDatabase] = []
    for domain in DOMAIN_ORDER:
        configured = override_map.get(domain)
        path = Path(configured).resolve() if configured else (base_dir / DEFAULT_SOURCE_FILES[domain]).resolve()
        databases.append(SourceDatabase(domain=domain, path=path))
    return databases


def resolve_target_database_url(explicit_url: str | None = None) -> str:
    candidate = str(explicit_url or "").strip()
    if candidate:
        return candidate
    for env_name in ("DATABASE_URL", "ALEMBIC_DATABASE_URL"):
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


def require_postgres_target_url(explicit_url: str | None = None) -> str:
    target_url = resolve_target_database_url(explicit_url)
    if not target_url:
        raise ValueError("target PostgreSQL URL is required; pass --target-url or set DATABASE_URL")
    if not target_url.lower().startswith("postgresql"):
        raise ValueError(f"target URL must be PostgreSQL, got: {target_url}")
    return target_url


def discover_source_tables(engine: Engine) -> list[str]:
    table_names = [name for name in inspect(engine).get_table_names() if not name.startswith("sqlite_")]
    metadata = MetaData()
    metadata.reflect(bind=engine, only=table_names)
    return [table.name for table in metadata.sorted_tables]


def table_row_count(engine: Engine, table_name: str) -> int:
    with engine.connect() as connection:
        value = connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table_name}"').scalar()
    return int(value or 0)


def normalize_scalar_for_target(value: Any, target_type: Any) -> Any:
    if value is None:
        return None

    if isinstance(target_type, JSON):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
        return value

    if isinstance(target_type, Boolean):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        lowered = str(value).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return value

    if isinstance(target_type, Integer):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        stripped = str(value).strip()
        return int(stripped) if stripped else None

    if isinstance(target_type, Float):
        if isinstance(value, (int, float)):
            return float(value)
        stripped = str(value).strip()
        return float(stripped) if stripped else None

    if isinstance(target_type, DateTime):
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return None
            candidate = candidate.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                return value
        return value

    if isinstance(target_type, Date):
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return None
            try:
                return date.fromisoformat(candidate[:10])
            except ValueError:
                return value
        return value

    return value


def normalize_row_for_target(row: dict[str, Any], target_table: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column in target_table.columns:
        if column.name not in row:
            continue
        payload[column.name] = normalize_scalar_for_target(row[column.name], column.type)
    return payload


def write_json_report(path: str | Path, payload: Any) -> Path:
    report_path = Path(path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path
