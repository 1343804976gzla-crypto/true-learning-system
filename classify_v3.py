"""
错题分类 v3 - 同步直写版
在当前进程中完成所有操作，避免任何文件句柄/连接问题
"""
import sqlite3
import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DB_FILE = os.path.abspath("data/learning.db")


def run():
    print(f"Database: {DB_FILE}")

    # ====== 1. 读取真实章节 ======
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=DELETE")  # 不用WAL，直接写主文件
    c = conn.cursor()

    c.execute("""
        SELECT id, book, chapter_title FROM chapters
        WHERE chapter_title NOT LIKE '%自动补齐%'
          AND id != '0' AND id NOT LIKE '%未分类%'
        ORDER BY book
    """)
    chapters = c.fetchall()
    valid_ids = {ch[0] for ch in chapters}

    chapter_text = ""
    cur_book = ""
    for cid, book, title in chapters:
        if book != cur_book:
            cur_book = book
            chapter_text += f"\n【{book}】\n"
        chapter_text += f"  {cid} → {title}\n"

    print(f"Loaded {len(chapters)} real chapters")

    # ====== 2. 读取未分类错题 ======
    c.execute("""SELECT id, question_text, key_point FROM wrong_answers_v2
                 WHERE chapter_id = '0' ORDER BY id""")
    wrongs = c.fetchall()
    print(f"Found {len(wrongs)} uncategorized wrong answers")

    if not wrongs:
        print("Nothing to do!")
        conn.close()
        return

    # ====== 3. AI匹配 + 即时写入 ======
    from services.ai_client import get_ai_client
    ai = get_ai_client()

    async def do_match(qtext, kp):
        prompt = f"""从以下章节列表中，选择与题目最匹配的一个章节ID。

考点：{kp or '(无)'}
题目：{qtext[:300]}

章节列表：
{chapter_text}

只返回JSON：{{"chapter_id": "xxx"}}
chapter_id必须是列表中的值。"""

        result = await ai.generate_json(
            prompt, {"chapter_id": "string"},
            max_tokens=100, temperature=0.1,
            use_heavy=False, timeout=30
        )
        return result.get("chapter_id", "")

    success = 0
    failed = 0

    for i, (wid, qtext, kp) in enumerate(wrongs, 1):
        try:
            cid = asyncio.run(do_match(qtext, kp))

            if cid in valid_ids:
                c.execute("UPDATE wrong_answers_v2 SET chapter_id = ? WHERE id = ?", (cid, wid))
                conn.commit()

                # 立即验证
                c.execute("SELECT chapter_id FROM wrong_answers_v2 WHERE id = ?", (wid,))
                actual = c.fetchone()[0]
                if actual == cid:
                    ch_title = next((t for i2, b, t in chapters if i2 == cid), "?")
                    print(f"[{i}/{len(wrongs)}] ID={wid} ✅ {cid} ({ch_title})")
                    success += 1
                else:
                    print(f"[{i}/{len(wrongs)}] ID={wid} ⚠️ Write failed! Expected {cid}, got {actual}")
                    failed += 1
            else:
                print(f"[{i}/{len(wrongs)}] ID={wid} ⚠️ Invalid ID: {cid}")
                failed += 1
        except Exception as e:
            print(f"[{i}/{len(wrongs)}] ID={wid} ❌ {e}")
            failed += 1

    # ====== 4. 最终验证 ======
    c.execute("SELECT COUNT(*) FROM wrong_answers_v2 WHERE chapter_id = '0'")
    remaining = c.fetchone()[0]

    print(f"\n{'='*60}")
    print(f"Done! Success: {success}, Failed: {failed}")
    print(f"Remaining uncategorized: {remaining}")

    conn.close()

    # ====== 5. 用全新连接再验证一次 ======
    conn2 = sqlite3.connect(DB_FILE)
    c2 = conn2.cursor()
    c2.execute("SELECT COUNT(*) FROM wrong_answers_v2 WHERE chapter_id = '0'")
    remaining2 = c2.fetchone()[0]
    print(f"Final verify (new connection): {remaining2} uncategorized")

    c2.execute("SELECT chapter_id, COUNT(*) FROM wrong_answers_v2 GROUP BY chapter_id ORDER BY COUNT(*) DESC")
    for cid, cnt in c2.fetchall():
        print(f"  {cid}: {cnt}")
    conn2.close()


if __name__ == "__main__":
    run()
