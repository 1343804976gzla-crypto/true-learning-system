"""
Normalize legacy JSON columns to the canonical storage contracts.

Default mode is dry-run. Use --apply to write changes.
This migration targets the JSON fields most likely to be consumed by
future LLM workflows and analytics exports.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

from dotenv import load_dotenv

from utils.data_contracts import (
    canonicalize_ai_evaluation,
    canonicalize_answer_changes,
    canonicalize_fusion_data,
    canonicalize_learning_activity_data,
    canonicalize_linked_record_ids,
    canonicalize_parent_ids,
    canonicalize_quiz_answers,
    canonicalize_quiz_questions,
    canonicalize_string_list,
    canonicalize_variant_data,
    normalize_option_map,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class JsonMigrationTarget:
    table: str
    id_column: str
    value_column: str
    target: str
    canonicalizer: Callable[[Any], Any]


def resolve_db_path() -> str:
    db_setting = (os.getenv("DATABASE_PATH") or "./data/learning.db").strip()

    if db_setting.startswith("sqlite:///"):
        parsed = urlparse(db_setting)
        raw_path = parsed.path
        if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        db_path = Path(raw_path)
    else:
        db_path = Path(db_setting)
        if not db_path.is_absolute():
            db_path = (BASE_DIR / db_path).resolve()

    return str(db_path)


def _parse_json_value(raw_value: Any) -> Any:
    if raw_value is None:
        return None
    if isinstance(raw_value, (dict, list)):
        return raw_value

    text = str(raw_value).strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return text


def _serialize_json_value(value: Any) -> Any:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _describe_shape(value: Any) -> str:
    if value is None:
        return "<null>"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    return type(value).__name__


def _summarize_shapes(values: List[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        label = _describe_shape(value)
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _optional_value(raw_value: Any, canonicalizer: Callable[[Any], Any]) -> Any:
    if raw_value is None:
        return None
    if isinstance(raw_value, str) and not raw_value.strip():
        return None
    return canonicalizer(raw_value)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


TARGETS = [
    JsonMigrationTarget(
        table="quiz_sessions",
        id_column="id",
        value_column="questions",
        target="quiz_sessions.questions",
        canonicalizer=canonicalize_quiz_questions,
    ),
    JsonMigrationTarget(
        table="quiz_sessions",
        id_column="id",
        value_column="answers",
        target="quiz_sessions.answers",
        canonicalizer=canonicalize_quiz_answers,
    ),
    JsonMigrationTarget(
        table="test_records",
        id_column="id",
        value_column="ai_options",
        target="test_records.ai_options",
        canonicalizer=normalize_option_map,
    ),
    JsonMigrationTarget(
        table="test_records",
        id_column="id",
        value_column="weak_points",
        target="test_records.weak_points",
        canonicalizer=canonicalize_string_list,
    ),
    JsonMigrationTarget(
        table="wrong_answers",
        id_column="id",
        value_column="options",
        target="wrong_answers.options",
        canonicalizer=normalize_option_map,
    ),
    JsonMigrationTarget(
        table="wrong_answers",
        id_column="id",
        value_column="weak_points",
        target="wrong_answers.weak_points",
        canonicalizer=canonicalize_string_list,
    ),
    JsonMigrationTarget(
        table="learning_activities",
        id_column="id",
        value_column="data",
        target="learning_activities.data",
        canonicalizer=canonicalize_learning_activity_data,
    ),
    JsonMigrationTarget(
        table="question_records",
        id_column="id",
        value_column="options",
        target="question_records.options",
        canonicalizer=normalize_option_map,
    ),
    JsonMigrationTarget(
        table="question_records",
        id_column="id",
        value_column="answer_changes",
        target="question_records.answer_changes",
        canonicalizer=canonicalize_answer_changes,
    ),
    JsonMigrationTarget(
        table="daily_learning_logs",
        id_column="id",
        value_column="knowledge_points_covered",
        target="daily_learning_logs.knowledge_points_covered",
        canonicalizer=canonicalize_string_list,
    ),
    JsonMigrationTarget(
        table="daily_learning_logs",
        id_column="id",
        value_column="weak_knowledge_points",
        target="daily_learning_logs.weak_knowledge_points",
        canonicalizer=canonicalize_string_list,
    ),
    JsonMigrationTarget(
        table="daily_learning_logs",
        id_column="id",
        value_column="session_ids",
        target="daily_learning_logs.session_ids",
        canonicalizer=canonicalize_string_list,
    ),
    JsonMigrationTarget(
        table="wrong_answers_v2",
        id_column="id",
        value_column="options",
        target="wrong_answers_v2.options",
        canonicalizer=normalize_option_map,
    ),
    JsonMigrationTarget(
        table="wrong_answers_v2",
        id_column="id",
        value_column="linked_record_ids",
        target="wrong_answers_v2.linked_record_ids",
        canonicalizer=canonicalize_linked_record_ids,
    ),
    JsonMigrationTarget(
        table="wrong_answers_v2",
        id_column="id",
        value_column="variant_data",
        target="wrong_answers_v2.variant_data",
        canonicalizer=lambda raw: _optional_value(raw, canonicalize_variant_data),
    ),
    JsonMigrationTarget(
        table="wrong_answers_v2",
        id_column="id",
        value_column="parent_ids",
        target="wrong_answers_v2.parent_ids",
        canonicalizer=lambda raw: _optional_value(raw, canonicalize_parent_ids),
    ),
    JsonMigrationTarget(
        table="wrong_answers_v2",
        id_column="id",
        value_column="fusion_data",
        target="wrong_answers_v2.fusion_data",
        canonicalizer=lambda raw: _optional_value(raw, canonicalize_fusion_data),
    ),
    JsonMigrationTarget(
        table="wrong_answer_retries",
        id_column="id",
        value_column="ai_evaluation",
        target="wrong_answer_retries.ai_evaluation",
        canonicalizer=lambda raw: _optional_value(raw, canonicalize_ai_evaluation),
    ),
]


def migrate_json_column(
    conn: sqlite3.Connection,
    target: JsonMigrationTarget,
    apply: bool,
) -> Dict[str, Any]:
    if not _table_exists(conn, target.table):
        return {
            "target": target.target,
            "rows_scanned": 0,
            "rows_changed": 0,
            "before": {},
            "after": {},
            "changed_ids": [],
            "skipped": True,
            "reason": f"missing table: {target.table}",
        }

    cursor = conn.cursor()
    rows = cursor.execute(
        f"SELECT {target.id_column}, {target.value_column} FROM {target.table}"
    ).fetchall()

    before_values: List[Any] = []
    after_values: List[Any] = []
    changed_ids: List[Any] = []
    updates: List[Any] = []

    for row_id, raw_value in rows:
        current_value = _parse_json_value(raw_value)
        input_value = current_value if current_value is not None else raw_value
        normalized_value = target.canonicalizer(input_value)

        before_values.append(current_value)
        after_values.append(normalized_value)

        if _serialize_json_value(current_value) == _serialize_json_value(normalized_value):
            continue

        changed_ids.append(row_id)
        updates.append((_serialize_json_value(normalized_value), row_id))

    if apply and updates:
        cursor.executemany(
            f"UPDATE {target.table} SET {target.value_column} = ? WHERE {target.id_column} = ?",
            updates,
        )

    return {
        "target": target.target,
        "rows_scanned": len(rows),
        "rows_changed": len(changed_ids),
        "before": _summarize_shapes(before_values),
        "after": _summarize_shapes(after_values),
        "changed_ids": changed_ids[:10],
        "skipped": False,
    }


def run_migration(db_path: str, apply: bool) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    try:
        results = [migrate_json_column(conn, target, apply) for target in TARGETS]

        if apply:
            conn.commit()
        else:
            conn.rollback()

        return results
    finally:
        conn.close()


def print_report(results: List[Dict[str, Any]], apply: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[json-contract-migration] mode={mode}")
    print()

    total_changed = 0
    for item in results:
        print(item["target"])
        if item.get("skipped"):
            print(f"  skipped: {item.get('reason')}")
            print()
            continue

        total_changed += int(item["rows_changed"])
        print(f"  scanned: {item['rows_scanned']}")
        print(f"  changed: {item['rows_changed']}")
        print(f"  before: {item['before']}")
        print(f"  after : {item['after']}")
        if item["changed_ids"]:
            print(f"  changed_ids(sample): {item['changed_ids']}")
        print()

    print(f"total_changed={total_changed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize legacy JSON column shapes.")
    parser.add_argument("--db", dest="db_path", default=resolve_db_path(), help="SQLite database path")
    parser.add_argument("--apply", action="store_true", help="Write changes to the database")
    args = parser.parse_args()

    db_path = str(Path(args.db_path).expanduser().resolve())
    if not Path(db_path).exists():
        raise SystemExit(f"Database not found: {db_path}")

    results = run_migration(db_path=db_path, apply=args.apply)
    print_report(results, apply=args.apply)


if __name__ == "__main__":
    main()
