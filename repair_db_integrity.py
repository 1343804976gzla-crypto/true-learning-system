"""
Repair data integrity for the active SQLite database.

What it does:
1) Backup DB file.
2) Ensure every concept_mastery.chapter_id exists in chapters.
3) Ensure every test_records/wrong_answers concept_id exists in concept_mastery.
4) Patch chapters.concepts JSON to include newly created concept IDs.

Usage:
  python repair_db_integrity.py
  python repair_db_integrity.py --db C:\\path\\to\\learning.db --no-backup
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

from dotenv import load_dotenv


BOOK_MAP = {
    "physiology": "生理学",
    "internal_medicine": "内科学",
    "pathology": "病理学",
    "surgery": "外科学",
    "diagnostics": "诊断学",
    "biochemistry": "生物化学",
    "pharmacology": "药理学",
    "microbiology": "医学微生物学",
    "immunology": "医学免疫学",
    "anatomy": "解剖学",
    "histology": "组织胚胎学",
    "pathophysiology": "病理生理学",
}


def resolve_db_path(project_dir: Path) -> Path:
    load_dotenv(project_dir / ".env")
    setting = (os.getenv("DATABASE_PATH") or "").strip()

    if setting.startswith("sqlite:///"):
        return Path(setting.replace("sqlite:///", "", 1)).resolve()

    if setting:
        p = Path(setting)
        if not p.is_absolute():
            p = (project_dir / p).resolve()
        return p

    return (project_dir / "data" / "learning.db").resolve()


def count_orphans(cur: sqlite3.Cursor) -> Dict[str, int]:
    queries = {
        "orphan_cm_chapter": """
            select count(*)
            from concept_mastery cm
            left join chapters c on cm.chapter_id = c.id
            where c.id is null
        """,
        "orphan_test_concept": """
            select count(*)
            from test_records t
            left join concept_mastery cm on t.concept_id = cm.concept_id
            where t.concept_id is not null and cm.concept_id is null
        """,
        "orphan_wrong_concept": """
            select count(*)
            from wrong_answers w
            left join concept_mastery cm on w.concept_id = cm.concept_id
            where cm.concept_id is null
        """,
    }
    out = {}
    for key, q in queries.items():
        out[key] = int(cur.execute(q).fetchone()[0])
    return out


def infer_book(chapter_id: str) -> str:
    prefix = chapter_id.split("_ch", 1)[0]
    for k, v in BOOK_MAP.items():
        if prefix.startswith(k):
            return v
    if chapter_id.startswith("unknown") or chapter_id.startswith("无法识别"):
        return "未分类"
    return "未分类"


def infer_chapter_number(chapter_id: str) -> str:
    if "_ch" not in chapter_id:
        return "0"
    tail = chapter_id.split("_ch", 1)[1]
    return (tail.split("_", 1)[0] or "0").strip()


def ensure_chapter(cur: sqlite3.Cursor, chapter_id: str) -> bool:
    exists = cur.execute("select 1 from chapters where id = ? limit 1", (chapter_id,)).fetchone()
    if exists:
        return False

    book = infer_book(chapter_id)
    chapter_number = infer_chapter_number(chapter_id)
    chapter_title = f"自动补齐章节({chapter_id})"
    concepts = json.dumps([], ensure_ascii=False)

    cur.execute(
        """
        insert into chapters (
            id, book, edition, chapter_number, chapter_title,
            content_summary, concepts, first_uploaded, last_reviewed
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chapter_id,
            book,
            "auto-repair",
            chapter_number,
            chapter_title,
            "Auto-created to repair orphan references.",
            concepts,
            None,
            None,
        ),
    )
    return True


def infer_chapter_id_for_concept(concept_id: str, chapter_ids: Set[str]) -> str:
    base = concept_id
    if "_repeat_" in base:
        base = base.split("_repeat_", 1)[0]

    # longest existing prefix match
    candidates = [cid for cid in chapter_ids if base == cid or base.startswith(cid + "_")]
    if candidates:
        return max(candidates, key=len)

    # infer from *_ch*
    idx = base.find("_ch")
    if idx != -1:
        prefix = base[:idx]
        rest = base[idx + 3 :]
        first = rest.split("_", 1)[0]
        if first:
            return f"{prefix}_ch{first}"

    # infer from *_qNN
    m = re.match(r"(.+)_q\d+$", base)
    if m:
        return m.group(1)

    # fallback: drop one tail segment
    if "_" in base:
        return base.rsplit("_", 1)[0]

    return "unknown_ch0"


def infer_concept_name(concept_id: str) -> str:
    base = concept_id
    if "_repeat_" in base:
        base = base.split("_repeat_", 1)[0]

    idx = base.find("_ch")
    if idx != -1:
        rest = base[idx + 3 :]
        parts = rest.split("_")
        if len(parts) >= 3 and (parts[1].isdigit() or re.fullmatch(r"q\d+", parts[1] or "")):
            name = "_".join(parts[2:])
            if name:
                return name[:120]
        if len(parts) >= 2:
            name = "_".join(parts[1:])
            if name:
                return name[:120]
    return base[-120:]


def repair(db_path: Path, do_backup: bool = True) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    if do_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = db_path.with_suffix(db_path.suffix + f".bak.{ts}")
        shutil.copy2(db_path, bak)
        print(f"[backup] {bak}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    before = count_orphans(cur)
    print("[before]", before)

    created_chapters = 0
    created_concepts = 0
    concepts_by_chapter: Dict[str, List[Dict[str, str]]] = {}

    try:
        cur.execute("BEGIN")

        # 1) Ensure chapter rows for all concept_mastery.chapter_id
        orphan_chapters = [
            row[0]
            for row in cur.execute(
                """
                select distinct cm.chapter_id
                from concept_mastery cm
                left join chapters c on cm.chapter_id = c.id
                where c.id is null and cm.chapter_id is not null and cm.chapter_id != ''
                """
            ).fetchall()
        ]
        for chapter_id in orphan_chapters:
            if ensure_chapter(cur, chapter_id):
                created_chapters += 1

        # 2) Ensure concept_mastery for all orphans in test_records/wrong_answers
        orphan_concepts = {
            row[0]
            for row in cur.execute(
                """
                select t.concept_id
                from test_records t
                left join concept_mastery cm on t.concept_id = cm.concept_id
                where t.concept_id is not null and t.concept_id != '' and cm.concept_id is null
                union
                select w.concept_id
                from wrong_answers w
                left join concept_mastery cm on w.concept_id = cm.concept_id
                where w.concept_id is not null and w.concept_id != '' and cm.concept_id is null
                """
            ).fetchall()
        }

        chapter_ids = {r[0] for r in cur.execute("select id from chapters").fetchall()}
        for concept_id in sorted(orphan_concepts):
            chapter_id = infer_chapter_id_for_concept(concept_id, chapter_ids)
            if chapter_id not in chapter_ids:
                if ensure_chapter(cur, chapter_id):
                    created_chapters += 1
                chapter_ids.add(chapter_id)

            name = infer_concept_name(concept_id)
            cur.execute(
                """
                insert or ignore into concept_mastery (
                    concept_id, chapter_id, name, retention, understanding, application,
                    last_tested, next_review
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (concept_id, chapter_id, name, 0.0, 0.0, 0.0, None, None),
            )
            if cur.rowcount > 0:
                created_concepts += 1
                concepts_by_chapter.setdefault(chapter_id, []).append(
                    {"id": concept_id, "name": name}
                )

        # 3) Patch chapters.concepts JSON
        for chapter_id, additions in concepts_by_chapter.items():
            row = cur.execute("select concepts from chapters where id = ?", (chapter_id,)).fetchone()
            if not row:
                continue
            raw = row[0]
            existing: List[Dict[str, str]]
            if raw is None:
                existing = []
            elif isinstance(raw, str):
                try:
                    loaded = json.loads(raw)
                    existing = loaded if isinstance(loaded, list) else []
                except Exception:
                    existing = []
            else:
                existing = []

            exists_ids = {str(x.get("id")) for x in existing if isinstance(x, dict)}
            for item in additions:
                if item["id"] not in exists_ids:
                    existing.append(item)
                    exists_ids.add(item["id"])

            cur.execute(
                "update chapters set concepts = ? where id = ?",
                (json.dumps(existing, ensure_ascii=False), chapter_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    after = count_orphans(cur)
    print("[after ]", after)
    print(
        "[done] created_chapters=", created_chapters,
        "created_concepts=", created_concepts,
    )
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="", help="SQLite DB path")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent
    db_path = Path(args.db).resolve() if args.db else resolve_db_path(project_dir)
    print(f"[db] {db_path}")
    repair(db_path, do_backup=not args.no_backup)


if __name__ == "__main__":
    main()
