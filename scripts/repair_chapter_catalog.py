from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from utils.chapter_catalog import clean_batch_chapter_rows, is_canonical_catalog_chapter_id, normalize_book_name


DEFAULT_SOURCE_DB = PROJECT_DIR / "data" / "learning.cleanup-backup-20260318_181742.db"
DEFAULT_REPORT_PATH = PROJECT_DIR / "data" / "chapter_repair_report.json"

TABLES_WITH_CHAPTER_ID = (
    "concept_mastery",
    "quiz_sessions",
    "wrong_answers_v2",
    "learning_sessions",
    "batch_exam_states",
)

TITLE_REPLACEMENTS = (
    ("心力衰竭", "心衰"),
    ("慢性心力衰竭", "心衰"),
    ("原发性肝细胞癌", "肝癌"),
    ("原发性肝癌", "肝癌"),
    ("胃肿瘤", "胃癌"),
    ("非化脓性", "非化脓"),
    ("四肢骨折和脱位1", "四肢骨折和脱位"),
    ("四肢骨折和脱位2", "四肢骨折和脱位"),
    ("胆系疾病1", "胆系疾病"),
    ("胆系疾病2", "胆系疾病"),
)

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


def resolve_active_db(project_dir: Path) -> Path:
    load_dotenv(project_dir / ".env")
    setting = (os.getenv("DATABASE_PATH") or "").strip()
    if setting.startswith("sqlite:///"):
        return Path(setting.replace("sqlite:///", "", 1)).resolve()
    if setting:
        path = Path(setting)
        if not path.is_absolute():
            path = (project_dir / path).resolve()
        return path
    return (project_dir / "data" / "learning.db").resolve()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def ensure_audit_table(conn: sqlite3.Connection) -> None:
    conn.execute(AUDIT_CHANGE_LOG_DDL)


def write_script_audit(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    after_payload: dict[str, Any],
    origin_event_type: str,
    origin_public_id: str,
    domain_name: str = "shadow",
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


def parse_concepts(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except Exception:
            return []
        if isinstance(loaded, list):
            return [item for item in loaded if isinstance(item, dict)]
    return []


def dump_concepts(items: list[dict[str, Any]]) -> str:
    return json.dumps(items, ensure_ascii=False)


def normalize_title(value: Any) -> str:
    text = str(value or "").strip().lower()
    for src, dst in TITLE_REPLACEMENTS:
        text = text.replace(src.lower(), dst.lower())
    text = (
        text.replace("&", "和")
        .replace("与", "和")
        .replace("及", "和")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
    )
    text = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return text


def normalize_number(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if "." in text:
            left, right = text.split(".", 1)
            return f"{int(left)}.{int(right)}"
        return str(int(text))
    except ValueError:
        return text


def title_match_score(left: Any, right: Any) -> int:
    left_text = normalize_title(left)
    right_text = normalize_title(right)
    if not left_text or not right_text:
        return 0
    if left_text == right_text:
        return 200
    if left_text in right_text or right_text in left_text:
        return 100 + min(len(left_text), len(right_text))

    score = 0
    max_window = min(4, len(left_text))
    for size in range(2, max_window + 1):
        for start in range(0, len(left_text) - size + 1):
            token = left_text[start:start + size]
            if token in right_text:
                score += size

    if left_text[:2] and left_text[:2] == right_text[:2]:
        score += 4
    return score


def table_has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    columns = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in columns)


def load_source_canonical_rows(source_db: Path) -> list[dict[str, Any]]:
    conn = connect(source_db)
    try:
        rows = conn.execute(
            """
            select id, book, edition, chapter_number, chapter_title,
                   content_summary, concepts, first_uploaded, last_reviewed
            from chapters
            """
        ).fetchall()
    finally:
        conn.close()

    cleaned = clean_batch_chapter_rows(
        {
            "id": row["id"],
            "book": row["book"],
            "chapter_number": row["chapter_number"],
            "chapter_title": row["chapter_title"],
        }
        for row in rows
    )
    allowed_ids = {row["id"] for row in cleaned if is_canonical_catalog_chapter_id(row["id"])}
    return [
        {
            "id": row["id"],
            "book": normalize_book_name(row["book"]),
            "edition": row["edition"],
            "chapter_number": row["chapter_number"],
            "chapter_title": row["chapter_title"],
            "content_summary": row["content_summary"],
            "concepts": parse_concepts(row["concepts"]),
            "first_uploaded": row["first_uploaded"],
            "last_reviewed": row["last_reviewed"],
        }
        for row in rows
        if row["id"] in allowed_ids
    ]


def count_clean_catalog(cur: sqlite3.Cursor) -> int:
    rows = cur.execute("select id, book, chapter_number, chapter_title from chapters").fetchall()
    cleaned = clean_batch_chapter_rows(
        {
            "id": row["id"],
            "book": row["book"],
            "chapter_number": row["chapter_number"],
            "chapter_title": row["chapter_title"],
        }
        for row in rows
    )
    return len(cleaned)


def upsert_chapter(cur: sqlite3.Cursor, row: dict[str, Any]) -> bool:
    existing = cur.execute("select id, concepts, last_reviewed from chapters where id = ?", (row["id"],)).fetchone()
    merged_concepts = row["concepts"]
    if existing is not None:
        existing_concepts = parse_concepts(existing["concepts"])
        seen = {str(item.get("id")) for item in merged_concepts}
        for item in existing_concepts:
            concept_id = str(item.get("id") or "").strip()
            if concept_id and concept_id not in seen:
                merged_concepts.append(item)
                seen.add(concept_id)
        cur.execute(
            """
            update chapters
            set book = ?, edition = ?, chapter_number = ?, chapter_title = ?,
                content_summary = ?, concepts = ?, first_uploaded = coalesce(first_uploaded, ?),
                last_reviewed = coalesce(last_reviewed, ?)
            where id = ?
            """,
            (
                row["book"],
                row["edition"],
                row["chapter_number"],
                row["chapter_title"],
                row["content_summary"],
                dump_concepts(merged_concepts),
                row["first_uploaded"],
                row["last_reviewed"],
                row["id"],
            ),
        )
        return False

    cur.execute(
        """
        insert into chapters (
            id, book, edition, chapter_number, chapter_title,
            content_summary, concepts, first_uploaded, last_reviewed
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["book"],
            row["edition"],
            row["chapter_number"],
            row["chapter_title"],
            row["content_summary"],
            dump_concepts(merged_concepts),
            row["first_uploaded"],
            row["last_reviewed"],
        ),
    )
    return True


def choose_target_id(row: sqlite3.Row, canonical_by_book: dict[str, list[dict[str, Any]]]) -> str:
    source_id = str(row["id"] or "").strip()
    if not source_id or is_canonical_catalog_chapter_id(source_id):
        return ""

    book = normalize_book_name(row["book"])
    title = str(row["chapter_title"] or "").strip()
    if not book or not title:
        return ""

    candidates = canonical_by_book.get(book, [])
    if not candidates:
        return ""

    normalized_number = normalize_number(row["chapter_number"])
    ranked: list[tuple[int, str]] = []
    for candidate in candidates:
        score = title_match_score(title, candidate["chapter_title"])
        if normalized_number and normalized_number == normalize_number(candidate["chapter_number"]):
            score += 25
        if score > 0:
            ranked.append((score, candidate["id"]))

    if not ranked:
        return ""

    ranked.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_id = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else -1

    if best_score >= 120:
        return best_id
    if best_score >= 18 and best_score >= second_score + 6:
        return best_id
    return ""


def remap_table(cur: sqlite3.Cursor, table: str, id_map: dict[str, str]) -> int:
    if not table_has_column(cur, table, "chapter_id"):
        return 0
    updated = 0
    for source_id, target_id in id_map.items():
        if source_id == target_id:
            continue
        cur.execute(f"update {table} set chapter_id = ? where chapter_id = ?", (target_id, source_id))
        updated += cur.rowcount
    return updated


def sync_chapter_concepts(cur: sqlite3.Cursor, chapter_ids: list[str]) -> None:
    unique_ids = sorted({chapter_id for chapter_id in chapter_ids if chapter_id})
    for chapter_id in unique_ids:
        chapter_row = cur.execute("select concepts from chapters where id = ?", (chapter_id,)).fetchone()
        if chapter_row is None:
            continue
        concepts = parse_concepts(chapter_row["concepts"])
        seen = {str(item.get("id")) for item in concepts if isinstance(item, dict)}

        rows = cur.execute(
            "select concept_id, name from concept_mastery where chapter_id = ? order by concept_id",
            (chapter_id,),
        ).fetchall()
        for row in rows:
            concept_id = str(row["concept_id"] or "").strip()
            if concept_id and concept_id not in seen:
                concepts.append({"id": concept_id, "name": row["name"]})
                seen.add(concept_id)

        cur.execute(
            "update chapters set concepts = ? where id = ?",
            (dump_concepts(concepts), chapter_id),
        )


def load_catalog_summary(catalog_json: Path) -> dict[str, Any]:
    if not catalog_json.exists():
        return {}
    try:
        return json.loads(catalog_json.read_text(encoding="utf-8"))
    except Exception:
        return {}


def repair(active_db: Path, source_db: Path, report_path: Path, catalog_json: Path | None = None) -> dict[str, Any]:
    if not active_db.exists():
        raise FileNotFoundError(active_db)
    if not source_db.exists():
        raise FileNotFoundError(source_db)

    backup_path = active_db.with_suffix(active_db.suffix + f".chapter-repair-backup-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(active_db, backup_path)

    source_rows = load_source_canonical_rows(source_db)
    canonical_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        canonical_by_book[row["book"]].append(row)

    conn = connect(active_db)
    cur = conn.cursor()

    before_clean_count = count_clean_catalog(cur)
    active_rows = cur.execute("select id, book, chapter_number, chapter_title from chapters").fetchall()
    id_map: dict[str, str] = {}
    unmatched: list[dict[str, str]] = []
    inserted = 0

    cur.execute("BEGIN")
    try:
        for row in source_rows:
            if upsert_chapter(cur, row):
                inserted += 1

        for row in active_rows:
            target_id = choose_target_id(row, canonical_by_book)
            if target_id:
                id_map[str(row["id"])] = target_id
            elif not is_canonical_catalog_chapter_id(row["id"]):
                unmatched.append({
                    "id": str(row["id"] or ""),
                    "book": str(row["book"] or ""),
                    "chapter_number": str(row["chapter_number"] or ""),
                    "chapter_title": str(row["chapter_title"] or ""),
                })

        table_updates = {}
        for table in TABLES_WITH_CHAPTER_ID:
            table_updates[table] = remap_table(cur, table, id_map)

        sync_chapter_concepts(cur, [row["id"] for row in source_rows] + list(id_map.values()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        after_clean_count = count_clean_catalog(cur)
        conn.close()

    catalog_payload = load_catalog_summary(catalog_json) if catalog_json else {}
    report = {
        "active_db": str(active_db),
        "source_db": str(source_db),
        "backup_path": str(backup_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "before_clean_catalog_count": before_clean_count,
        "after_clean_catalog_count": after_clean_count,
        "source_canonical_count": len(source_rows),
        "inserted_canonical_rows": inserted,
        "remapped_chapter_ids": id_map,
        "table_update_counts": table_updates,
        "unmatched_noncanonical_rows": unmatched[:100],
        "catalog_subject_summary": catalog_payload.get("subject_summary") if isinstance(catalog_payload, dict) else {},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_conn = connect(active_db)
    try:
        write_script_audit(
            audit_conn,
            entity_type="repair_chapter_catalog",
            entity_id=f"repair:{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            action="script_run",
            after_payload=report,
            origin_event_type="script.repair_chapter_catalog",
            origin_public_id=str(report_path),
        )
        audit_conn.commit()
    finally:
        audit_conn.close()
    return report


def main() -> None:
    active_db = resolve_active_db(PROJECT_DIR)

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB), help="Canonical source sqlite db")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Repair report path")
    parser.add_argument("--catalog-json", default="", help="Optional cleaned catalog json for reporting")
    args = parser.parse_args()

    report = repair(
        active_db=active_db,
        source_db=Path(args.source_db).expanduser().resolve(),
        report_path=Path(args.report).expanduser().resolve(),
        catalog_json=Path(args.catalog_json).expanduser().resolve() if args.catalog_json else None,
    )

    print(f"[active] {report['active_db']}")
    print(f"[source] {report['source_db']}")
    print(f"[backup] {report['backup_path']}")
    print(f"[catalog] {report['before_clean_catalog_count']} -> {report['after_clean_catalog_count']}")
    print(f"[inserted] {report['inserted_canonical_rows']}")
    print(f"[remapped] {len(report['remapped_chapter_ids'])}")
    for table, count in report["table_update_counts"].items():
        print(f"[table] {table}: {count}")
    print(f"[report] {args.report}")


if __name__ == "__main__":
    main()
