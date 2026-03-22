from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row_to_payload(row: sqlite3.Row, *, omit: set[str] | None = None) -> dict[str, Any]:
    omit = omit or set()
    return {key: row[key] for key in row.keys() if key not in omit}


def _insert_payload(conn: sqlite3.Connection, table: str, payload: dict[str, Any]) -> int:
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    cursor = conn.execute(sql, [payload[column] for column in columns])
    return int(cursor.lastrowid)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row["name"]) for row in rows]


def _parse_json_array(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    text = str(raw).strip()
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return list(value) if isinstance(value, list) else []


@dataclass
class MergeSummary:
    inserted_learning_sessions: int = 0
    inserted_learning_activities: int = 0
    inserted_question_records: int = 0
    inserted_batch_exam_states: int = 0
    inserted_wrong_answers: int = 0
    inserted_wrong_answer_retries: int = 0
    inserted_daily_review_papers: int = 0
    inserted_daily_review_paper_items: int = 0
    remapped_linked_record_ids: int = 0
    skipped_unmapped_linked_record_ids: int = 0
    skipped_retry_missing_wrong_answer: int = 0


def _merge_learning_sessions(source: sqlite3.Connection, target: sqlite3.Connection, summary: MergeSummary) -> None:
    target_ids = {
        str(row["id"])
        for row in target.execute("SELECT id FROM learning_sessions").fetchall()
    }
    rows = source.execute("SELECT * FROM learning_sessions ORDER BY started_at, id").fetchall()
    for row in rows:
        if str(row["id"]) in target_ids:
            continue
        _insert_payload(target, "learning_sessions", _row_to_payload(row))
        summary.inserted_learning_sessions += 1


def _merge_batch_exam_states(source: sqlite3.Connection, target: sqlite3.Connection, summary: MergeSummary) -> None:
    target_ids = {
        str(row["id"])
        for row in target.execute("SELECT id FROM batch_exam_states").fetchall()
    }
    rows = source.execute("SELECT * FROM batch_exam_states ORDER BY created_at, id").fetchall()
    for row in rows:
        if str(row["id"]) in target_ids:
            continue
        _insert_payload(target, "batch_exam_states", _row_to_payload(row))
        summary.inserted_batch_exam_states += 1


def _merge_learning_activities(source: sqlite3.Connection, target: sqlite3.Connection, summary: MergeSummary) -> None:
    target_keys = {
        (
            str(row["session_id"]),
            str(row["activity_type"]),
            str(row["timestamp"]),
            int(row["relative_time_ms"] or 0),
        )
        for row in target.execute(
            "SELECT session_id, activity_type, timestamp, relative_time_ms FROM learning_activities"
        ).fetchall()
    }
    rows = source.execute(
        "SELECT * FROM learning_activities ORDER BY timestamp, session_id, id"
    ).fetchall()
    for row in rows:
        key = (
            str(row["session_id"]),
            str(row["activity_type"]),
            str(row["timestamp"]),
            int(row["relative_time_ms"] or 0),
        )
        if key in target_keys:
            continue
        _insert_payload(target, "learning_activities", _row_to_payload(row, omit={"id"}))
        target_keys.add(key)
        summary.inserted_learning_activities += 1


def _merge_question_records(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    summary: MergeSummary,
) -> dict[int, int]:
    source_rows = source.execute(
        "SELECT * FROM question_records ORDER BY session_id, question_index, answered_at, id"
    ).fetchall()
    target_rows = target.execute(
        "SELECT id, session_id, question_index FROM question_records"
    ).fetchall()
    target_key_to_id = {
        (str(row["session_id"]), int(row["question_index"])): int(row["id"])
        for row in target_rows
    }
    source_id_to_target_id: dict[int, int] = {}

    for row in source_rows:
        source_id = int(row["id"])
        key = (str(row["session_id"]), int(row["question_index"]))
        existing_id = target_key_to_id.get(key)
        if existing_id is not None:
            source_id_to_target_id[source_id] = existing_id
            continue

        payload = _row_to_payload(row, omit={"id"})
        new_id = _insert_payload(target, "question_records", payload)
        source_id_to_target_id[source_id] = new_id
        target_key_to_id[key] = new_id
        summary.inserted_question_records += 1

    return source_id_to_target_id


def _merge_wrong_answers(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    question_id_map: dict[int, int],
    summary: MergeSummary,
) -> dict[int, int]:
    target_rows = target.execute(
        "SELECT id, question_fingerprint FROM wrong_answers_v2"
    ).fetchall()
    target_fp_to_id = {
        str(row["question_fingerprint"]): int(row["id"])
        for row in target_rows
    }
    source_rows = source.execute(
        "SELECT * FROM wrong_answers_v2 ORDER BY created_at, id"
    ).fetchall()
    source_id_to_target_id: dict[int, int] = {}

    for row in source_rows:
        source_id = int(row["id"])
        fingerprint = str(row["question_fingerprint"])
        existing_id = target_fp_to_id.get(fingerprint)
        if existing_id is not None:
            source_id_to_target_id[source_id] = existing_id
            continue

        payload = _row_to_payload(row, omit={"id"})
        remapped_ids: list[int] = []
        for source_question_id in _parse_json_array(row["linked_record_ids"]):
            try:
                mapped = question_id_map.get(int(source_question_id))
            except (TypeError, ValueError):
                mapped = None
            if mapped is None:
                summary.skipped_unmapped_linked_record_ids += 1
                continue
            remapped_ids.append(mapped)
            summary.remapped_linked_record_ids += 1
        payload["linked_record_ids"] = json.dumps(sorted(dict.fromkeys(remapped_ids)))

        new_id = _insert_payload(target, "wrong_answers_v2", payload)
        target_fp_to_id[fingerprint] = new_id
        source_id_to_target_id[source_id] = new_id
        summary.inserted_wrong_answers += 1

    return source_id_to_target_id


def _retry_key(
    row: sqlite3.Row,
    wrong_answer_id_to_fp: dict[int, str],
) -> tuple[str | None, str, str, str, str, str, str]:
    return (
        wrong_answer_id_to_fp.get(int(row["wrong_answer_id"])),
        str(row["retried_at"]),
        str(row["user_answer"]),
        str(row["is_correct"]),
        str(row["confidence"]),
        str(row["is_variant"]),
        str(row["rationale_text"]),
    )


def _recompute_retry_fields(
    target: sqlite3.Connection,
    *,
    wrong_answer_ids: set[int],
) -> None:
    if not wrong_answer_ids:
        return

    for wrong_answer_id in sorted(wrong_answer_ids):
        count_row = target.execute(
            """
            SELECT COUNT(*) AS retry_count
            FROM wrong_answer_retries
            WHERE wrong_answer_id = ?
            """,
            (wrong_answer_id,),
        ).fetchone()
        retry_count = int(count_row["retry_count"] or 0)
        latest = target.execute(
            """
            SELECT is_correct, confidence, retried_at
            FROM wrong_answer_retries
            WHERE wrong_answer_id = ?
            ORDER BY retried_at DESC, id DESC
            LIMIT 1
            """,
            (wrong_answer_id,),
        ).fetchone()
        if latest is None:
            target.execute(
                """
                UPDATE wrong_answers_v2
                SET retry_count = 0,
                    last_retry_correct = NULL,
                    last_retry_confidence = NULL,
                    last_retried_at = NULL
                WHERE id = ?
                """,
                (wrong_answer_id,),
            )
            continue

        target.execute(
            """
            UPDATE wrong_answers_v2
            SET retry_count = ?,
                last_retry_correct = ?,
                last_retry_confidence = ?,
                last_retried_at = ?,
                updated_at = CASE
                    WHEN updated_at IS NULL OR updated_at < ? THEN ?
                    ELSE updated_at
                END
            WHERE id = ?
            """,
            (
                retry_count,
                latest["is_correct"],
                latest["confidence"],
                latest["retried_at"],
                latest["retried_at"],
                latest["retried_at"],
                wrong_answer_id,
            ),
        )


def _merge_wrong_answer_retries(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    source_wa_id_to_target_id: dict[int, int],
    summary: MergeSummary,
) -> None:
    target_wa_rows = target.execute(
        "SELECT id, question_fingerprint FROM wrong_answers_v2"
    ).fetchall()
    target_wa_id_to_fp = {
        int(row["id"]): str(row["question_fingerprint"])
        for row in target_wa_rows
    }
    target_fp_to_wa_id = {
        str(row["question_fingerprint"]): int(row["id"])
        for row in target_wa_rows
    }
    target_retry_rows = target.execute(
        """
        SELECT id, wrong_answer_id, retried_at, user_answer, is_correct, confidence, is_variant, rationale_text
        FROM wrong_answer_retries
        """
    ).fetchall()
    target_retry_keys = {
        _retry_key(row, target_wa_id_to_fp)
        for row in target_retry_rows
    }

    source_wa_rows = source.execute(
        "SELECT id, question_fingerprint FROM wrong_answers_v2"
    ).fetchall()
    source_wa_id_to_fp = {
        int(row["id"]): str(row["question_fingerprint"])
        for row in source_wa_rows
    }
    source_retry_rows = source.execute(
        "SELECT * FROM wrong_answer_retries ORDER BY retried_at, id"
    ).fetchall()
    affected_wrong_answer_ids: set[int] = set()

    for row in source_retry_rows:
        key = _retry_key(row, source_wa_id_to_fp)
        if key in target_retry_keys:
            continue

        fingerprint = key[0]
        if fingerprint is None:
            summary.skipped_retry_missing_wrong_answer += 1
            continue

        target_wrong_answer_id = target_fp_to_wa_id.get(fingerprint)
        if target_wrong_answer_id is None:
            source_wrong_answer_id = int(row["wrong_answer_id"])
            target_wrong_answer_id = source_wa_id_to_target_id.get(source_wrong_answer_id)
        if target_wrong_answer_id is None:
            summary.skipped_retry_missing_wrong_answer += 1
            continue

        payload = _row_to_payload(row, omit={"id"})
        payload["wrong_answer_id"] = target_wrong_answer_id
        _insert_payload(target, "wrong_answer_retries", payload)
        target_retry_keys.add(key)
        affected_wrong_answer_ids.add(target_wrong_answer_id)
        summary.inserted_wrong_answer_retries += 1

    _recompute_retry_fields(target, wrong_answer_ids=affected_wrong_answer_ids)


def _merge_daily_review_papers(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    source_wa_id_to_target_id: dict[int, int],
    summary: MergeSummary,
) -> None:
    target_paper_rows = target.execute(
        "SELECT id, actor_key, paper_date FROM daily_review_papers"
    ).fetchall()
    target_paper_key_to_id = {
        (str(row["actor_key"]), str(row["paper_date"])): int(row["id"])
        for row in target_paper_rows
    }
    source_paper_rows = source.execute(
        "SELECT * FROM daily_review_papers ORDER BY paper_date, id"
    ).fetchall()
    source_paper_id_to_target_id: dict[int, int] = {}

    for row in source_paper_rows:
        source_paper_id = int(row["id"])
        paper_key = (str(row["actor_key"]), str(row["paper_date"]))
        existing_id = target_paper_key_to_id.get(paper_key)
        if existing_id is not None:
            source_paper_id_to_target_id[source_paper_id] = existing_id
            continue

        payload = _row_to_payload(row, omit={"id"})
        new_id = _insert_payload(target, "daily_review_papers", payload)
        source_paper_id_to_target_id[source_paper_id] = new_id
        target_paper_key_to_id[paper_key] = new_id
        summary.inserted_daily_review_papers += 1

    if not source_paper_id_to_target_id:
        return

    target_item_rows = target.execute(
        "SELECT paper_id, position, wrong_answer_id, stem_fingerprint FROM daily_review_paper_items"
    ).fetchall()
    target_item_keys = {
        (
            int(row["paper_id"]),
            int(row["position"]),
            int(row["wrong_answer_id"]),
            str(row["stem_fingerprint"]),
        )
        for row in target_item_rows
    }
    source_item_rows = source.execute(
        "SELECT * FROM daily_review_paper_items ORDER BY paper_id, position, id"
    ).fetchall()

    for row in source_item_rows:
        source_paper_id = int(row["paper_id"])
        target_paper_id = source_paper_id_to_target_id.get(source_paper_id)
        if target_paper_id is None:
            continue

        source_wrong_answer_id = int(row["wrong_answer_id"])
        target_wrong_answer_id = source_wa_id_to_target_id.get(source_wrong_answer_id)
        if target_wrong_answer_id is None:
            continue

        key = (
            target_paper_id,
            int(row["position"]),
            target_wrong_answer_id,
            str(row["stem_fingerprint"]),
        )
        if key in target_item_keys:
            continue

        payload = _row_to_payload(row, omit={"id"})
        payload["paper_id"] = target_paper_id
        payload["wrong_answer_id"] = target_wrong_answer_id
        _insert_payload(target, "daily_review_paper_items", payload)
        target_item_keys.add(key)
        summary.inserted_daily_review_paper_items += 1


def merge(
    *,
    source_runtime: Path,
    source_review: Path,
    target_runtime: Path,
    target_review: Path,
) -> MergeSummary:
    summary = MergeSummary()
    source_rt = _connect(source_runtime)
    source_rv = _connect(source_review)
    target_rt = _connect(target_runtime)
    target_rv = _connect(target_review)

    try:
        target_rt.execute("BEGIN IMMEDIATE")
        target_rv.execute("BEGIN IMMEDIATE")

        _merge_learning_sessions(source_rt, target_rt, summary)
        _merge_batch_exam_states(source_rt, target_rt, summary)
        _merge_learning_activities(source_rt, target_rt, summary)
        question_id_map = _merge_question_records(source_rt, target_rt, summary)

        source_wa_id_to_target_id = _merge_wrong_answers(
            source_rv,
            target_rv,
            question_id_map,
            summary,
        )
        _merge_wrong_answer_retries(
            source_rv,
            target_rv,
            source_wa_id_to_target_id,
            summary,
        )
        _merge_daily_review_papers(
            source_rv,
            target_rv,
            source_wa_id_to_target_id,
            summary,
        )

        target_rt.commit()
        target_rv.commit()
        return summary
    except Exception:
        target_rt.rollback()
        target_rv.rollback()
        raise
    finally:
        source_rt.close()
        source_rv.close()
        target_rt.close()
        target_rv.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge split runtime/review SQLite data into a target database set.")
    parser.add_argument("--source-runtime", type=Path, required=True)
    parser.add_argument("--source-review", type=Path, required=True)
    parser.add_argument("--target-runtime", type=Path, required=True)
    parser.add_argument("--target-review", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path(""))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary = merge(
        source_runtime=args.source_runtime.resolve(),
        source_review=args.source_review.resolve(),
        target_runtime=args.target_runtime.resolve(),
        target_review=args.target_review.resolve(),
    )

    report = json.dumps(asdict(summary), ensure_ascii=False, indent=2, sort_keys=True)
    print(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
