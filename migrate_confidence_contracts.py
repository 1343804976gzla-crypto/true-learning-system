"""
Normalize legacy confidence values to the canonical contract:

- sure
- unsure
- no

Default mode is dry-run. Use --apply to write changes.
Use --rewrite-empty to backfill NULL/blank values to "unsure".
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv

from utils.data_contracts import normalize_confidence

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CANONICAL_CONFIDENCE = {"sure", "unsure", "no"}


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


def canonicalize_confidence(value: Any, rewrite_empty: bool) -> Any:
    if value is None:
        return "unsure" if rewrite_empty else None

    text = str(value).strip()
    if not text:
        return "unsure" if rewrite_empty else value

    normalized = normalize_confidence(text)
    if normalized in CANONICAL_CONFIDENCE:
        return normalized

    return value


def summarize_values(values: List[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        if value is None:
            label = "<null>"
        else:
            text = str(value).strip()
            label = text if text else "<empty>"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def migrate_scalar_column(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    value_column: str,
    rewrite_empty: bool,
    apply: bool,
) -> Dict[str, Any]:
    cursor = conn.cursor()
    rows = cursor.execute(f"SELECT {id_column}, {value_column} FROM {table}").fetchall()

    before_values = [row[1] for row in rows]
    changed_rows: List[Tuple[Any, Any]] = []

    for row_id, current_value in rows:
        new_value = canonicalize_confidence(current_value, rewrite_empty=rewrite_empty)
        if new_value != current_value:
            changed_rows.append((new_value, row_id))

    if apply and changed_rows:
        cursor.executemany(
            f"UPDATE {table} SET {value_column} = ? WHERE {id_column} = ?",
            changed_rows,
        )

    after_values = [
        canonicalize_confidence(value, rewrite_empty=rewrite_empty)
        for value in before_values
    ]

    return {
        "target": f"{table}.{value_column}",
        "rows_scanned": len(rows),
        "rows_changed": len(changed_rows),
        "before": summarize_values(before_values),
        "after": summarize_values(after_values),
    }


def _parse_json_value(raw_value: Any) -> Any:
    if raw_value is None:
        return None
    if isinstance(raw_value, (list, dict)):
        return raw_value
    text = str(raw_value).strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return None


def migrate_quiz_session_answers(
    conn: sqlite3.Connection,
    rewrite_empty: bool,
    apply: bool,
) -> Dict[str, Any]:
    cursor = conn.cursor()
    rows = cursor.execute("SELECT id, answers FROM quiz_sessions").fetchall()

    scanned = 0
    changed = 0
    before_values: List[Any] = []
    after_values: List[Any] = []
    updates: List[Tuple[str, int]] = []

    for session_id, raw_answers in rows:
        parsed = _parse_json_value(raw_answers)
        if not isinstance(parsed, list):
            continue

        session_changed = False
        scanned += 1
        for item in parsed:
            if not isinstance(item, dict):
                continue
            if "confidence" not in item:
                continue

            current_value = item.get("confidence")
            before_values.append(current_value)
            new_value = canonicalize_confidence(current_value, rewrite_empty=rewrite_empty)
            after_values.append(new_value)

            if new_value != current_value:
                item["confidence"] = new_value
                session_changed = True

        if session_changed:
            changed += 1
            updates.append((json.dumps(parsed, ensure_ascii=False), session_id))

    if apply and updates:
        cursor.executemany(
            "UPDATE quiz_sessions SET answers = ? WHERE id = ?",
            updates,
        )

    return {
        "target": "quiz_sessions.answers[].confidence",
        "rows_scanned": scanned,
        "rows_changed": changed,
        "before": summarize_values(before_values),
        "after": summarize_values(after_values),
    }


def run_migration(db_path: str, rewrite_empty: bool, apply: bool) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    try:
        results = [
            migrate_scalar_column(conn, "question_records", "id", "confidence", rewrite_empty, apply),
            migrate_scalar_column(conn, "test_records", "id", "confidence", rewrite_empty, apply),
            migrate_scalar_column(conn, "wrong_answer_retries", "id", "confidence", rewrite_empty, apply),
            migrate_scalar_column(conn, "wrong_answers_v2", "id", "last_retry_confidence", rewrite_empty, apply),
            migrate_quiz_session_answers(conn, rewrite_empty, apply),
        ]

        if apply:
            conn.commit()
        else:
            conn.rollback()

        return results
    finally:
        conn.close()


def print_report(results: List[Dict[str, Any]], apply: bool, rewrite_empty: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[confidence-migration] mode={mode} rewrite_empty={rewrite_empty}")
    print()

    total_changed = 0
    for item in results:
        total_changed += int(item["rows_changed"])
        print(f"{item['target']}")
        print(f"  scanned: {item['rows_scanned']}")
        print(f"  changed: {item['rows_changed']}")
        print(f"  before: {item['before']}")
        print(f"  after : {item['after']}")
        print()

    print(f"total_changed={total_changed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize legacy confidence values.")
    parser.add_argument("--db", dest="db_path", default=resolve_db_path(), help="SQLite database path")
    parser.add_argument("--apply", action="store_true", help="Write changes to the database")
    parser.add_argument(
        "--rewrite-empty",
        action="store_true",
        help='Backfill NULL/blank confidence to "unsure"',
    )
    args = parser.parse_args()

    db_path = str(Path(args.db_path).expanduser().resolve())
    if not Path(db_path).exists():
        raise SystemExit(f"Database not found: {db_path}")

    results = run_migration(
        db_path=db_path,
        rewrite_empty=args.rewrite_empty,
        apply=args.apply,
    )
    print_report(results, apply=args.apply, rewrite_empty=args.rewrite_empty)


if __name__ == "__main__":
    main()
