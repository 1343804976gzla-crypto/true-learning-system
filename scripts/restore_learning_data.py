from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


TEST_QUESTION_TEXTS = {
    "legacy wrong",
    "current wrong",
    "current question",
    "legacy question",
    "Seed Wrong Question",
    "Seed Question 1",
    "Seed Question 2",
    "Rollback paper first",
    "Rollback paper second",
    "compat question",
    "User scoped agent question",
    "legacy pending question",
    "测试题干",
    "规范化测试题",
    "原题",
    "融合题",
    "Which sign is classic for asthma exacerbation?",
}

TEST_KEY_POINTS = {
    "Seed Concept",
    "测试考点",
    "原考点",
    "融合考点",
    "kp",
    "paper-kp-first",
    "paper-kp-second",
}

TEST_SESSION_TITLES = {
    "Legacy session",
    "Current session",
    "Seed Session",
    "contract-test-session",
    "answer-change-contract-test",
    "Respiratory Quiz",
    "Detail practice: Cardiac output",
}

TEST_DEVICE_PREFIXES = (
    "review-",
    "agent-action-",
    "agent-device-",
    "legacy-pending-",
    "codex-openviking-live-check",
    "agent-scope-",
)

TEST_CHAPTER_PREFIXES = (
    "seed-chapter-",
    "contract",
)


@dataclass
class RestoreSummary:
    cleaned_wrong_answers: int = 0
    cleaned_question_records: int = 0
    cleaned_learning_sessions: int = 0
    cleaned_wrong_answer_retries: int = 0
    imported_learning_sessions: int = 0
    imported_question_records: int = 0
    imported_wrong_answers: int = 0
    imported_wrong_answer_retries: int = 0
    imported_quiz_sessions: int = 0


def parse_args() -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parents[1]
    default_current = project_dir / "data" / "learning.db"
    default_source = project_dir / "data" / "learning.cleanup-backup-20260318_181742.db"
    default_output = project_dir / "data" / f"learning.restored-{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

    parser = argparse.ArgumentParser(description="Build a restored learning DB candidate from backup + current DB.")
    parser.add_argument("--current", type=Path, default=default_current)
    parser.add_argument("--source", type=Path, default=default_source)
    parser.add_argument("--output", type=Path, default=default_output)
    return parser.parse_args()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def is_test_device(device_id: Any) -> bool:
    text = normalize_text(device_id)
    return any(text.startswith(prefix) for prefix in TEST_DEVICE_PREFIXES)


def is_test_chapter_id(chapter_id: Any) -> bool:
    text = normalize_text(chapter_id)
    return any(text.startswith(prefix) for prefix in TEST_CHAPTER_PREFIXES)


def is_test_wrong_answer(row: sqlite3.Row) -> bool:
    return (
        normalize_text(row["question_text"]) in TEST_QUESTION_TEXTS
        or normalize_text(row["key_point"]) in TEST_KEY_POINTS
        or is_test_device(row["device_id"])
        or is_test_chapter_id(row["chapter_id"])
    )


def is_test_question_record(row: sqlite3.Row) -> bool:
    return (
        normalize_text(row["question_text"]) in TEST_QUESTION_TEXTS
        or normalize_text(row["key_point"]) in TEST_KEY_POINTS
        or is_test_device(row["device_id"])
        or normalize_text(row["session_id"]).startswith("ov-sync-session-")
        or normalize_text(row["session_id"]).startswith("seed-session-")
    )


def is_test_learning_session(row: sqlite3.Row) -> bool:
    return (
        normalize_text(row["title"]) in TEST_SESSION_TITLES
        or is_test_device(row["device_id"])
        or is_test_chapter_id(row["chapter_id"])
        or normalize_text(row["id"]).startswith("legacy-session-")
        or normalize_text(row["id"]).startswith("current-session-")
        or normalize_text(row["id"]).startswith("backfill-")
        or normalize_text(row["id"]).startswith("seed-session-")
        or normalize_text(row["id"]).startswith("ov-sync-session-")
    )


def is_test_quiz_session(row: sqlite3.Row) -> bool:
    session_type = normalize_text(row["session_type"])
    chapter_id = normalize_text(row["chapter_id"])
    if session_type in {"practice", "pre_generated", "concurrent_practice"} and (
        chapter_id == "0" or chapter_id.startswith("contract")
    ):
        return True
    return False


def iso_max(conn: sqlite3.Connection, query: str) -> str:
    row = conn.execute(query).fetchone()
    return normalize_text(row[0] if row else "")


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def insert_row(conn: sqlite3.Connection, table: str, row: sqlite3.Row, *, omit: set[str] | None = None) -> int:
    omit = omit or set()
    payload = {key: row[key] for key in row.keys() if key not in omit}
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.execute(sql, [payload[column] for column in columns])
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def cleanup_output(conn: sqlite3.Connection, summary: RestoreSummary) -> None:
    wrong_rows = conn.execute(
        """
        SELECT id, device_id, question_text, key_point, chapter_id
        FROM wrong_answers_v2
        """
    ).fetchall()
    wrong_ids_to_delete = [int(row["id"]) for row in wrong_rows if is_test_wrong_answer(row)]
    if wrong_ids_to_delete:
        placeholders = ", ".join("?" for _ in wrong_ids_to_delete)
        conn.execute(f"DELETE FROM wrong_answer_retries WHERE wrong_answer_id IN ({placeholders})", wrong_ids_to_delete)
        summary.cleaned_wrong_answer_retries += conn.total_changes
        before_changes = conn.total_changes
        conn.execute(f"DELETE FROM wrong_answers_v2 WHERE id IN ({placeholders})", wrong_ids_to_delete)
        summary.cleaned_wrong_answers += conn.total_changes - before_changes

    question_rows = conn.execute(
        """
        SELECT id, device_id, session_id, question_text, key_point
        FROM question_records
        """
    ).fetchall()
    question_ids_to_delete = [int(row["id"]) for row in question_rows if is_test_question_record(row)]
    if question_ids_to_delete:
        placeholders = ", ".join("?" for _ in question_ids_to_delete)
        before_changes = conn.total_changes
        conn.execute(f"DELETE FROM question_records WHERE id IN ({placeholders})", question_ids_to_delete)
        summary.cleaned_question_records += conn.total_changes - before_changes

    learning_rows = conn.execute(
        """
        SELECT id, device_id, title, chapter_id
        FROM learning_sessions
        """
    ).fetchall()
    learning_ids_to_delete = [str(row["id"]) for row in learning_rows if is_test_learning_session(row)]
    if learning_ids_to_delete:
        placeholders = ", ".join("?" for _ in learning_ids_to_delete)
        before_changes = conn.total_changes
        conn.execute(f"DELETE FROM learning_sessions WHERE id IN ({placeholders})", learning_ids_to_delete)
        summary.cleaned_learning_sessions += conn.total_changes - before_changes


def import_recent_learning_sessions(
    current_conn: sqlite3.Connection,
    output_conn: sqlite3.Connection,
    summary: RestoreSummary,
) -> set[str]:
    cutoff = iso_max(output_conn, "SELECT MAX(started_at) FROM learning_sessions")
    imported_session_ids: set[str] = set()

    rows = current_conn.execute(
        """
        SELECT *
        FROM learning_sessions
        ORDER BY started_at, id
        """
    ).fetchall()

    for row in rows:
        started_at = normalize_text(row["started_at"])
        session_id = normalize_text(row["id"])
        if not started_at or started_at <= cutoff or is_test_learning_session(row):
            continue
        exists = output_conn.execute("SELECT 1 FROM learning_sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone()
        if exists:
            continue
        insert_row(output_conn, "learning_sessions", row)
        imported_session_ids.add(session_id)
        summary.imported_learning_sessions += 1

    return imported_session_ids


def import_recent_question_records(
    current_conn: sqlite3.Connection,
    output_conn: sqlite3.Connection,
    imported_session_ids: set[str],
    summary: RestoreSummary,
) -> None:
    cutoff = iso_max(output_conn, "SELECT MAX(answered_at) FROM question_records")
    rows = current_conn.execute(
        """
        SELECT *
        FROM question_records
        ORDER BY answered_at, id
        """
    ).fetchall()

    for row in rows:
        answered_at = normalize_text(row["answered_at"])
        if is_test_question_record(row):
            continue
        if normalize_text(row["session_id"]) not in imported_session_ids and (not answered_at or answered_at <= cutoff):
            continue
        before_changes = output_conn.total_changes
        insert_row(output_conn, "question_records", row, omit={"id"})
        if output_conn.total_changes > before_changes:
            summary.imported_question_records += 1


def import_recent_wrong_answers(
    current_conn: sqlite3.Connection,
    output_conn: sqlite3.Connection,
    summary: RestoreSummary,
) -> dict[int, int]:
    cutoff = iso_max(
        output_conn,
        "SELECT MAX(COALESCE(last_wrong_at, first_wrong_at, created_at)) FROM wrong_answers_v2",
    )
    rows = current_conn.execute(
        """
        SELECT *
        FROM wrong_answers_v2
        ORDER BY COALESCE(last_wrong_at, first_wrong_at, created_at), id
        """
    ).fetchall()

    id_map: dict[int, int] = {}
    for row in rows:
        ts = normalize_text(row["last_wrong_at"] or row["first_wrong_at"] or row["created_at"])
        if not ts or ts <= cutoff or is_test_wrong_answer(row):
            continue
        fingerprint = normalize_text(row["question_fingerprint"])
        device_id = normalize_text(row["device_id"])
        if fingerprint:
            exists = output_conn.execute(
                """
                SELECT id
                FROM wrong_answers_v2
                WHERE question_fingerprint = ? AND COALESCE(device_id, '') = ?
                LIMIT 1
                """,
                (fingerprint, device_id),
            ).fetchone()
            if exists:
                id_map[int(row["id"])] = int(exists["id"])
                continue
        new_id = insert_row(output_conn, "wrong_answers_v2", row, omit={"id"})
        id_map[int(row["id"])] = new_id
        summary.imported_wrong_answers += 1

    return id_map


def import_recent_wrong_answer_retries(
    current_conn: sqlite3.Connection,
    output_conn: sqlite3.Connection,
    wrong_answer_id_map: dict[int, int],
    summary: RestoreSummary,
) -> None:
    rows = current_conn.execute(
        """
        SELECT *
        FROM wrong_answer_retries
        ORDER BY retried_at, id
        """
    ).fetchall()

    for row in rows:
        old_wrong_answer_id = int(row["wrong_answer_id"])
        if old_wrong_answer_id not in wrong_answer_id_map or is_test_device(row["device_id"]):
            continue
        payload = dict(row)
        payload["wrong_answer_id"] = wrong_answer_id_map[old_wrong_answer_id]
        before_changes = output_conn.total_changes
        columns = list(payload.keys())
        columns.remove("id")
        placeholders = ", ".join("?" for _ in columns)
        output_conn.execute(
            f"INSERT INTO wrong_answer_retries ({', '.join(columns)}) VALUES ({placeholders})",
            [payload[column] for column in columns],
        )
        if output_conn.total_changes > before_changes:
            summary.imported_wrong_answer_retries += 1


def import_recent_quiz_sessions(
    current_conn: sqlite3.Connection,
    output_conn: sqlite3.Connection,
    summary: RestoreSummary,
) -> None:
    cutoff = iso_max(output_conn, "SELECT MAX(started_at) FROM quiz_sessions")
    rows = current_conn.execute(
        """
        SELECT *
        FROM quiz_sessions
        ORDER BY started_at, id
        """
    ).fetchall()

    for row in rows:
        started_at = normalize_text(row["started_at"])
        total_questions = int(row["total_questions"] or 0)
        correct_count = int(row["correct_count"] or 0)
        score = int(row["score"] or 0)
        session_type = normalize_text(row["session_type"])
        if not started_at or started_at <= cutoff or is_test_quiz_session(row):
            continue
        if total_questions <= 0 and correct_count <= 0 and score <= 0:
            continue
        if not session_type.startswith("exam"):
            continue
        before_changes = output_conn.total_changes
        insert_row(output_conn, "quiz_sessions", row, omit={"id"})
        if output_conn.total_changes > before_changes:
            summary.imported_quiz_sessions += 1


def print_counts(label: str, conn: sqlite3.Connection) -> None:
    stats = {
        "wrong_answers_v2": conn.execute("SELECT COUNT(*) FROM wrong_answers_v2").fetchone()[0],
        "wrong_answer_retries": conn.execute("SELECT COUNT(*) FROM wrong_answer_retries").fetchone()[0],
        "question_records": conn.execute("SELECT COUNT(*) FROM question_records").fetchone()[0],
        "learning_sessions": conn.execute("SELECT COUNT(*) FROM learning_sessions").fetchone()[0],
        "quiz_sessions": conn.execute("SELECT COUNT(*) FROM quiz_sessions").fetchone()[0],
    }
    parts = " ".join(f"{key}={value}" for key, value in stats.items())
    print(f"[{label}] {parts}")


def main() -> None:
    args = parse_args()
    current_path = args.current.resolve()
    source_path = args.source.resolve()
    output_path = args.output.resolve()

    if not current_path.exists():
        raise FileNotFoundError(f"Current DB not found: {current_path}")
    if not source_path.exists():
        raise FileNotFoundError(f"Source DB not found: {source_path}")
    if output_path.exists():
        raise FileExistsError(f"Output DB already exists: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, output_path)

    summary = RestoreSummary()
    current_conn = connect(current_path)
    output_conn = connect(output_path)

    try:
        print(f"[current] {current_path}")
        print(f"[source ] {source_path}")
        print(f"[output ] {output_path}")
        print_counts("source-copy-before-clean", output_conn)

        cleanup_output(output_conn, summary)
        imported_session_ids = import_recent_learning_sessions(current_conn, output_conn, summary)
        import_recent_question_records(current_conn, output_conn, imported_session_ids, summary)
        wrong_answer_id_map = import_recent_wrong_answers(current_conn, output_conn, summary)
        import_recent_wrong_answer_retries(current_conn, output_conn, wrong_answer_id_map, summary)
        import_recent_quiz_sessions(current_conn, output_conn, summary)

        output_conn.commit()
        print_counts("restored-output", output_conn)
        print(
            "[summary]",
            {
                "cleaned_wrong_answers": summary.cleaned_wrong_answers,
                "cleaned_question_records": summary.cleaned_question_records,
                "cleaned_learning_sessions": summary.cleaned_learning_sessions,
                "cleaned_wrong_answer_retries": summary.cleaned_wrong_answer_retries,
                "imported_learning_sessions": summary.imported_learning_sessions,
                "imported_question_records": summary.imported_question_records,
                "imported_wrong_answers": summary.imported_wrong_answers,
                "imported_wrong_answer_retries": summary.imported_wrong_answer_retries,
                "imported_quiz_sessions": summary.imported_quiz_sessions,
            },
        )
    finally:
        current_conn.close()
        output_conn.close()


if __name__ == "__main__":
    main()
