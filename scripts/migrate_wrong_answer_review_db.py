from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.domains import CORE_DATABASE_URL, REVIEW_DATABASE_URL, get_sqlite_path
from learning_tracking_models import ReviewBase
from scripts._script_audit import write_script_audit

REVIEW_TABLES = [
    "wrong_answers_v2",
    "wrong_answer_retries",
    "daily_review_papers",
    "daily_review_paper_items",
    "chapter_review_chapters",
    "chapter_review_units",
    "chapter_review_tasks",
    "chapter_review_task_questions",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate review-domain tables into a dedicated wrong_answer_review.db file.")
    parser.add_argument("--source", type=str, default="", help="Source SQLite database path. Defaults to DATABASE_PATH.")
    parser.add_argument("--target", type=str, default="", help="Target SQLite database path. Defaults to REVIEW_DATABASE_PATH.")
    parser.add_argument("--replace", action="store_true", help="Replace existing rows in the target review tables.")
    return parser.parse_args()


def _resolve_path(cli_value: str, fallback_url: str) -> Path:
    if cli_value:
        candidate = Path(cli_value)
        return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()

    resolved = get_sqlite_path(fallback_url)
    if resolved is None:
        raise ValueError(f"unsupported database url: {fallback_url}")
    return resolved


def _snapshot_path(db_path: Path, label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"{db_path.stem}.{label}.{timestamp}{db_path.suffix}"


def _sqlite_backup(source: Path, target: Path) -> None:
    source_conn = sqlite3.connect(str(source))
    target_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()]


def _table_count(conn: sqlite3.Connection, table_name: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])


def _ensure_target_schema(target_path: Path) -> None:
    target_engine = create_engine(
        f"sqlite:///{target_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    try:
        ReviewBase.metadata.create_all(bind=target_engine)
    finally:
        target_engine.dispose()


def _copy_table(source_conn: sqlite3.Connection, target_conn: sqlite3.Connection, table_name: str, *, replace: bool) -> int:
    source_columns = _table_columns(source_conn, table_name)
    target_columns = _table_columns(target_conn, table_name)
    common_columns = [column for column in source_columns if column in target_columns]
    if not common_columns:
        return 0

    quoted_columns = ", ".join(f'"{column}"' for column in common_columns)
    rows = source_conn.execute(f'SELECT {quoted_columns} FROM "{table_name}"').fetchall()
    if replace:
        target_conn.execute(f'DELETE FROM "{table_name}"')

    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in common_columns)
    target_conn.executemany(
        f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({placeholders})',
        rows,
    )
    return len(rows)


def main() -> int:
    args = _parse_args()
    source_path = _resolve_path(args.source, CORE_DATABASE_URL)
    target_path = _resolve_path(args.target, REVIEW_DATABASE_URL)

    if source_path.resolve() == target_path.resolve():
        raise SystemExit("source and target point to the same database file; configure REVIEW_DATABASE_PATH first")

    if not source_path.exists():
        raise SystemExit(f"source database not found: {source_path}")

    source_snapshot = _snapshot_path(source_path, "pre-review-split")
    _sqlite_backup(source_path, source_snapshot)
    print(f"[backup] source snapshot created: {source_snapshot}")

    if target_path.exists() and args.replace:
        target_snapshot = _snapshot_path(target_path, "pre-review-replace")
        _sqlite_backup(target_path, target_snapshot)
        print(f"[backup] target snapshot created: {target_snapshot}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_target_schema(target_path)

    source_conn = sqlite3.connect(str(source_path))
    target_conn = sqlite3.connect(str(target_path))
    try:
        target_conn.execute("PRAGMA foreign_keys = OFF")
        existing_target_rows = sum(_table_count(target_conn, table) for table in REVIEW_TABLES if _table_columns(target_conn, table))
        if existing_target_rows and not args.replace:
            raise SystemExit(
                f"target database already contains {existing_target_rows} review rows; rerun with --replace to overwrite"
            )

        copied_counts: dict[str, int] = {}
        for table_name in REVIEW_TABLES:
            copied_counts[table_name] = _copy_table(source_conn, target_conn, table_name, replace=args.replace)
        target_conn.commit()
        target_conn.execute("PRAGMA foreign_keys = ON")

        print("[migrate] copied rows:")
        for table_name in REVIEW_TABLES:
            source_count = _table_count(source_conn, table_name)
            target_count = _table_count(target_conn, table_name)
            status = "OK" if source_count == target_count else "MISMATCH"
            print(
                f"  - {table_name}: source={source_count} target={target_count} copied={copied_counts[table_name]} [{status}]"
            )
            if source_count != target_count:
                raise SystemExit(f"row count mismatch for {table_name}")
        write_script_audit(
            target_conn,
            domain_name="review",
            entity_type="migrate_wrong_answer_review_db",
            entity_id=f"migrate:{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            action="script_run",
            after_payload={
                "source": str(source_path),
                "target": str(target_path),
                "replace": bool(args.replace),
                "tables": REVIEW_TABLES,
                "copied_counts": copied_counts,
            },
            origin_event_type="script.migrate_wrong_answer_review_db",
            origin_public_id=str(target_path),
        )
        target_conn.commit()
    finally:
        target_conn.close()
        source_conn.close()

    print(f"[done] review domain migrated to: {target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
