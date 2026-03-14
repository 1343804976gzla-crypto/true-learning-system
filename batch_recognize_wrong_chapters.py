"""批量为未分类错题识别章节"""
import sqlite3
import asyncio
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from services.content_parser_v2 import get_content_parser

db_path = Path("C:/Users/35456/true-learning-system/data/learning.db")

async def batch_recognize_chapters(batch_size: int = 10, dry_run: bool = True):
    """
    批量为未分类错题识别章节

    Args:
        batch_size: 每批处理的数量
        dry_run: 是否为试运行（不实际更新数据库）
    """

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 获取未分类的错题
    cursor.execute("""
        SELECT id, question_text, key_point, chapter_id
        FROM wrong_answers_v2
        WHERE chapter_id LIKE '%未分类%' OR chapter_id LIKE '%ch0%'
    """)

    uncategorized = cursor.fetchall()
    total = len(uncategorized)

    print("=" * 70)
    print(f"批量章节识别 - {'试运行模式' if dry_run else '正式运行'}")
    print("=" * 70)
    print(f"找到 {total} 条未分类错题")
    print(f"批次大小: {batch_size}")
    print()

    if total == 0:
        print("没有需要处理的错题")
        conn.close()
        return

    parser = get_content_parser()
    success_count = 0
    fail_count = 0
    updates: List[Tuple[str, int]] = []

    for i, (wrong_id, question, key_point, old_chapter_id) in enumerate(uncategorized, 1):
        print(f"[{i}/{total}] 处理错题ID: {wrong_id}")

        # 构建识别内容
        content = f"{key_point or ''}\n\n{question[:500]}"

        try:
            # 使用章节识别功能
            result = await parser.parse_content(content)

            book = result.get('book', '')
            chapter_id = result.get('chapter_id', '')
            chapter_title = result.get('chapter_title', '')

            # 检查识别结果是否有效
            if chapter_id and chapter_id not in ['unknown_ch0', '未知_ch0', '无法识别_ch0']:
                print(f"  ✅ 识别成功: {book} - {chapter_title}")
                print(f"     章节ID: {chapter_id}")
                updates.append((chapter_id, wrong_id))
                success_count += 1
            else:
                print(f"  ⚠️ 识别失败: 无法确定章节")
                fail_count += 1

        except Exception as e:
            print(f"  ❌ 识别错误: {e}")
            fail_count += 1

        # 每批次后暂停一下
        if i % batch_size == 0:
            print(f"\n已处理 {i}/{total}，暂停2秒...")
            await asyncio.sleep(2)

    print("\n" + "=" * 70)
    print("识别完成")
    print("=" * 70)
    print(f"成功: {success_count}")
    print(f"失败: {fail_count}")
    print(f"总计: {total}")

    if not dry_run and updates:
        print(f"\n开始更新数据库...")

        for chapter_id, wrong_id in updates:
            cursor.execute("""
                UPDATE wrong_answers_v2
                SET chapter_id = ?
                WHERE id = ?
            """, (chapter_id, wrong_id))

        conn.commit()
        print(f"✅ 已更新 {len(updates)} 条记录")
    elif dry_run:
        print(f"\n试运行模式，未实际更新数据库")
        print(f"如需正式运行，请使用: python {Path(__file__).name} --run")

    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量为未分类错题识别章节")
    parser.add_argument("--run", action="store_true", help="正式运行（实际更新数据库）")
    parser.add_argument("--batch-size", type=int, default=10, help="每批处理的数量")

    args = parser.parse_args()

    asyncio.run(batch_recognize_chapters(
        batch_size=args.batch_size,
        dry_run=not args.run
    ))
