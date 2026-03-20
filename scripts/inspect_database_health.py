from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.audit import audit_targets

CHECK_TABLES = {
    "content": ["daily_uploads", "chapters", "concept_mastery", "knowledge_upload_records", "audit_change_log"],
    "runtime": ["learning_sessions", "question_records", "quiz_sessions", "test_records", "audit_change_log"],
    "review": ["wrong_answers_v2", "wrong_answer_retries", "daily_review_papers", "audit_change_log"],
    "agent": ["agent_sessions", "agent_messages", "agent_action_logs", "audit_change_log"],
    "legacy": ["wrong_answers", "audit_change_log"],
    "shadow": ["wrong_answers", "daily_uploads", "chapters", "concept_mastery", "learning_sessions", "question_records", "wrong_answers_v2", "agent_sessions"],
}

SHADOW_EXPECTED_DUPLICATES = {
    "daily_uploads": "content",
    "chapters": "content",
    "concept_mastery": "content",
    "learning_sessions": "runtime",
    "question_records": "runtime",
    "wrong_answers_v2": "review",
    "agent_sessions": "agent",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect active SQLite databases and report table/file health.")
    parser.add_argument("--include-shadow", action="store_true", help="Inspect the shadow learning.db as well.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser.parse_args()


def _safe_count(conn: sqlite3.Connection, table_name: str) -> int | None:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
    except Exception:
        return None


def _db_report(name: str, path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "name": name,
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "wal_bytes": path.with_name(path.name + "-wal").stat().st_size if path.with_name(path.name + "-wal").exists() else 0,
        "shm_bytes": path.with_name(path.name + "-shm").stat().st_size if path.with_name(path.name + "-shm").exists() else 0,
        "tables": {},
    }
    if not path.exists():
        return report

    conn = sqlite3.connect(str(path))
    try:
        for table_name in CHECK_TABLES.get(name, []):
            report["tables"][table_name] = _safe_count(conn, table_name)
    finally:
        conn.close()
    return report


def _shadow_comparison(reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    shadow = reports.get("shadow")
    if not shadow:
        return []

    comparisons: list[dict[str, Any]] = []
    for table_name, domain_name in SHADOW_EXPECTED_DUPLICATES.items():
        shadow_count = (shadow.get("tables") or {}).get(table_name)
        active_count = ((reports.get(domain_name) or {}).get("tables") or {}).get(table_name)
        comparisons.append(
            {
                "table": table_name,
                "shadow_count": shadow_count,
                "active_domain": domain_name,
                "active_count": active_count,
                "matches": shadow_count == active_count,
            }
        )
    return comparisons


def main() -> int:
    args = _parse_args()
    reports: dict[str, dict[str, Any]] = {}

    for target in audit_targets():
        if target.name == "shadow" and not args.include_shadow:
            continue
        if target.path is None:
            continue
        reports[target.name] = _db_report(target.name, target.path)

    payload = {
        "databases": reports,
        "shadow_comparisons": _shadow_comparison(reports),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for name, report in reports.items():
        print(f"[{name}] {report['path']}")
        print(f"  exists={report['exists']} size={report['size_bytes']} wal={report['wal_bytes']} shm={report['shm_bytes']}")
        for table_name, count in (report.get("tables") or {}).items():
            print(f"  - {table_name}: {count if count is not None else 'missing'}")

    if payload["shadow_comparisons"]:
        print("[shadow]")
        for row in payload["shadow_comparisons"]:
            print(
                f"  - {row['table']}: shadow={row['shadow_count']} active({row['active_domain']})={row['active_count']} match={row['matches']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
