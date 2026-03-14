"""为未分类的错题识别章节"""
import sqlite3
import asyncio
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from services.content_parser_v2 import get_content_parser
from models import get_db

db_path = Path("C:/Users/35456/true-learning-system/data/learning.db")

async def recognize_chapters_for_uncategorized():
    """为未分类的错题识别章节"""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 获取未分类的错题
    cursor.execute("""
        SELECT id, question_text, key_point
        FROM wrong_answers_v2
        WHERE chapter_id LIKE '%未分类%' OR chapter_id LIKE '%ch0%'
        LIMIT 10
    """)

    uncategorized = cursor.fetchall()
    print(f"找到 {len(uncategorized)} 条未分类错题（显示前10条）")
    print("=" * 60)

    parser = get_content_parser()

    for wrong_id, question, key_point in uncategorized:
        print(f"\n错题ID: {wrong_id}")
        print(f"考点: {key_point or '(无)'}")
        print(f"题目: {question[:150]}...")

        # 构建识别内容
        content = f"{key_point or ''}\n\n{question}"

        try:
            # 使用章节识别功能
            result = await parser.parse_content(content)

            print(f"  识别结果:")
            print(f"    科目: {result.get('book')}")
            print(f"    章节: {result.get('chapter_number')} - {result.get('chapter_title')}")
            print(f"    章节ID: {result.get('chapter_id')}")

        except Exception as e:
            print(f"  识别失败: {e}")

    conn.close()

if __name__ == "__main__":
    asyncio.run(recognize_chapters_for_uncategorized())
