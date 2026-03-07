"""
Semi-automatic recategorization for `uncategorized_ch0`.

Current strategy (safe, deterministic):
1) Move `medical_humanities_ch01_*` concepts from uncategorized_ch0 to
   chapter `medical_humanities_ch01`.
2) If target chapter does not exist, create it.
3) Sync chapters.concepts JSON lists for source/target chapter.
4) Keep unrecognized noise entries in uncategorized_ch0 for manual review.

This script is intentionally conservative to avoid wrong auto-classification.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


SOURCE_CHAPTER_ID = "uncategorized_ch0"
TARGET_CHAPTER_ID = "medical_humanities_ch01"
TARGET_BOOK = "医学人文"
TARGET_CHAPTER_NUMBER = "01"
TARGET_CHAPTER_TITLE = "医学职业素养与医事法律"


def resolve_active_db(project_dir: Path) -> Path:
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


def parse_concepts(raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            return []
    return []


def ensure_target_chapter(cur: sqlite3.Cursor) -> None:
    exists = cur.execute("select 1 from chapters where id = ?", (TARGET_CHAPTER_ID,)).fetchone()
    if exists:
        return
    cur.execute(
        """
        insert into chapters (
            id, book, edition, chapter_number, chapter_title,
            content_summary, concepts, first_uploaded, last_reviewed
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TARGET_CHAPTER_ID,
            TARGET_BOOK,
            "auto-recategorize",
            TARGET_CHAPTER_NUMBER,
            TARGET_CHAPTER_TITLE,
            "Auto-created from uncategorized concepts.",
            json.dumps([], ensure_ascii=False),
            None,
            None,
        ),
    )


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    db_path = resolve_active_db(project_dir)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = db_path.with_suffix(db_path.suffix + f".recategorize.bak.{ts}")
    shutil.copy2(db_path, bak)
    print(f"[backup] {bak}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("BEGIN")
    try:
        ensure_target_chapter(cur)

        # select candidates from uncategorized
        rows = cur.execute(
            """
            select concept_id, name
            from concept_mastery
            where chapter_id = ?
            order by concept_id
            """,
            (SOURCE_CHAPTER_ID,),
        ).fetchall()

        candidates = [
            (cid, name)
            for cid, name in rows
            if cid.startswith("medical_humanities_ch01_")
        ]

        moved = 0
        for cid, _ in candidates:
            cur.execute(
                "update concept_mastery set chapter_id = ? where concept_id = ? and chapter_id = ?",
                (TARGET_CHAPTER_ID, cid, SOURCE_CHAPTER_ID),
            )
            moved += cur.rowcount

        # sync chapters.concepts JSON
        src_row = cur.execute("select concepts from chapters where id = ?", (SOURCE_CHAPTER_ID,)).fetchone()
        dst_row = cur.execute("select concepts from chapters where id = ?", (TARGET_CHAPTER_ID,)).fetchone()
        src_concepts = parse_concepts(src_row[0] if src_row else None)
        dst_concepts = parse_concepts(dst_row[0] if dst_row else None)

        move_ids = {cid for cid, _ in candidates}

        # remove from source
        src_concepts_new = [x for x in src_concepts if str(x.get("id")) not in move_ids]

        # add to target
        dst_ids = {str(x.get("id")) for x in dst_concepts}
        for cid, name in candidates:
            if cid not in dst_ids:
                dst_concepts.append({"id": cid, "name": name})
                dst_ids.add(cid)

        cur.execute(
            "update chapters set concepts = ? where id = ?",
            (json.dumps(src_concepts_new, ensure_ascii=False), SOURCE_CHAPTER_ID),
        )
        cur.execute(
            "update chapters set concepts = ? where id = ?",
            (json.dumps(dst_concepts, ensure_ascii=False), TARGET_CHAPTER_ID),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"[done] moved={moved}, target={TARGET_CHAPTER_ID}")


if __name__ == "__main__":
    main()

