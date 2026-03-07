"""
Normalize chapter IDs and clean unknown chapters in active DB.

Rules:
1) Merge IDs like `xxx_ch第六章` -> `xxx_ch6` (if canonical exists).
2) Merge/redirect unknown chapters:
   - `unknown_ch*`, `无法识别_ch*`, `book in ('无法识别','未分类')`
   -> unified `uncategorized_ch0`.
3) Update references in:
   - concept_mastery.chapter_id
   - quiz_sessions.chapter_id
   - wrong_answers_v2.chapter_id
   - learning_sessions.chapter_id
4) Merge chapters.concepts JSON, then delete redundant chapter rows.

Safety:
- Auto backup active DB before modifying.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv


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


CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def chinese_to_int(text: str) -> int | None:
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    cur = 0
    has_unit = False
    for ch in text:
        if ch in CN_NUM:
            cur = CN_NUM[ch]
        elif ch == "十":
            has_unit = True
            if cur == 0:
                cur = 1
            total += cur * 10
            cur = 0
        elif ch == "百":
            has_unit = True
            if cur == 0:
                cur = 1
            total += cur * 100
            cur = 0
        else:
            return None
    total += cur
    if total == 0 and has_unit:
        return None
    return total if total > 0 else None


def normalize_cn_chapter_id(chapter_id: str) -> str | None:
    m = re.match(r"^(.*_ch)第([一二三四五六七八九十百千0-9]+)章$", chapter_id)
    if not m:
        return None
    prefix = m.group(1)
    raw_num = m.group(2)
    n = chinese_to_int(raw_num)
    if n is None:
        return None
    return f"{prefix}{n}"


def load_chapter(cur: sqlite3.Cursor, chapter_id: str):
    row = cur.execute(
        """
        select id, book, edition, chapter_number, chapter_title, content_summary, concepts, first_uploaded, last_reviewed
        from chapters where id = ?
        """,
        (chapter_id,),
    ).fetchone()
    return row


def parse_concepts(raw) -> List[Dict]:
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


def merge_chapter_payload(cur: sqlite3.Cursor, src_id: str, dst_id: str) -> None:
    src = load_chapter(cur, src_id)
    dst = load_chapter(cur, dst_id)
    if not src or not dst:
        return

    # merge concepts
    src_concepts = parse_concepts(src[6])
    dst_concepts = parse_concepts(dst[6])
    ids = {str(x.get("id")) for x in dst_concepts if isinstance(x, dict)}
    for c in src_concepts:
        cid = str(c.get("id"))
        if cid and cid not in ids:
            dst_concepts.append(c)
            ids.add(cid)

    # choose better summary/title when dst empty
    dst_summary = dst[5] or ""
    src_summary = src[5] or ""
    dst_title = dst[4] or ""
    src_title = src[4] or ""

    new_summary = dst_summary if dst_summary else src_summary
    new_title = dst_title if dst_title else src_title

    cur.execute(
        "update chapters set chapter_title = ?, content_summary = ?, concepts = ? where id = ?",
        (new_title, new_summary, json.dumps(dst_concepts, ensure_ascii=False), dst_id),
    )


def remap_refs(cur: sqlite3.Cursor, src_id: str, dst_id: str) -> Dict[str, int]:
    counts = {}
    for table, col in [
        ("concept_mastery", "chapter_id"),
        ("quiz_sessions", "chapter_id"),
        ("wrong_answers_v2", "chapter_id"),
        ("learning_sessions", "chapter_id"),
    ]:
        cur.execute(f"update {table} set {col} = ? where {col} = ?", (dst_id, src_id))
        counts[f"{table}.{col}"] = cur.rowcount
    return counts


def ensure_uncategorized(cur: sqlite3.Cursor) -> str:
    cid = "uncategorized_ch0"
    exists = cur.execute("select 1 from chapters where id = ? limit 1", (cid,)).fetchone()
    if exists:
        return cid
    cur.execute(
        """
        insert into chapters (
            id, book, edition, chapter_number, chapter_title,
            content_summary, concepts, first_uploaded, last_reviewed
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cid,
            "未分类",
            "auto-normalize",
            "0",
            "待人工归类",
            "Automatically unified unknown chapters.",
            json.dumps([], ensure_ascii=False),
            None,
            None,
        ),
    )
    return cid


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    db_path = resolve_active_db(project_dir)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = db_path.with_suffix(db_path.suffix + f".normalize.bak.{ts}")
    shutil.copy2(db_path, bak)
    print(f"[backup] {bak}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("BEGIN")
    try:
        chapter_ids = [r[0] for r in cur.execute("select id from chapters").fetchall()]

        remap_log = []
        deleted = 0

        # 1) Chinese-number chapter id normalization
        for src_id in chapter_ids:
            dst_id = normalize_cn_chapter_id(src_id)
            if not dst_id or src_id == dst_id:
                continue

            dst_exists = cur.execute("select 1 from chapters where id = ?", (dst_id,)).fetchone()
            if not dst_exists:
                # rename in-place if target absent
                cur.execute("update chapters set id = ? where id = ?", (dst_id, src_id))
                counts = remap_refs(cur, src_id, dst_id)
                remap_log.append((src_id, dst_id, "rename", counts))
                continue

            merge_chapter_payload(cur, src_id, dst_id)
            counts = remap_refs(cur, src_id, dst_id)
            cur.execute("delete from chapters where id = ?", (src_id,))
            deleted += cur.rowcount
            remap_log.append((src_id, dst_id, "merge", counts))

        # reload ids after potential renames
        chapter_rows = cur.execute("select id, book from chapters").fetchall()
        unknown_target = ensure_uncategorized(cur)

        # 2) unknown chapter unification
        for src_id, book in chapter_rows:
            is_unknown = (
                src_id.startswith("unknown_ch")
                or src_id.startswith("无法识别_ch")
                or book in ("无法识别", "未分类")
            )
            if not is_unknown or src_id == unknown_target:
                continue

            merge_chapter_payload(cur, src_id, unknown_target)
            counts = remap_refs(cur, src_id, unknown_target)
            cur.execute("delete from chapters where id = ?", (src_id,))
            deleted += cur.rowcount
            remap_log.append((src_id, unknown_target, "unknown-merge", counts))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"[done] remaps={len(remap_log)} deleted_chapters={deleted}")
    for src_id, dst_id, mode, counts in remap_log[:50]:
        print(f"  {mode}: {src_id} -> {dst_id} | {counts}")


if __name__ == "__main__":
    main()

