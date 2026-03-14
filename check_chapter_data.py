"""检查数据库中的章节数据"""
import sqlite3
from pathlib import Path

# 连接数据库
db_path = Path("C:/Users/35456/true-learning-system/data/learning.db")
if not db_path.exists():
    print(f"❌ 数据库不存在: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 60)
print("检查章节数据")
print("=" * 60)

# 检查章节表
cursor.execute("SELECT COUNT(*) FROM chapters")
chapter_count = cursor.fetchone()[0]
print(f"\n章节总数: {chapter_count}")

# 查看最近的章节
cursor.execute("""
    SELECT id, book, chapter_number, chapter_title, first_uploaded
    FROM chapters
    ORDER BY first_uploaded DESC
    LIMIT 10
""")

print("\n最近的10个章节:")
print("-" * 60)
for row in cursor.fetchall():
    print(f"ID: {row[0]}")
    print(f"  科目: {row[1]}")
    print(f"  章节号: {row[2]}")
    print(f"  标题: {row[3]}")
    print(f"  上传日期: {row[4]}")
    print()

# 检查是否有 unknown_ch0
cursor.execute("""
    SELECT COUNT(*) FROM chapters
    WHERE id LIKE '%unknown%' OR chapter_number = '0'
""")
unknown_count = cursor.fetchone()[0]
print(f"未识别章节数量: {unknown_count}")

# 检查最近的上传记录
cursor.execute("""
    SELECT id, date, json_extract(ai_extracted, '$.book') as book,
           json_extract(ai_extracted, '$.chapter_title') as chapter_title
    FROM daily_uploads
    ORDER BY date DESC
    LIMIT 5
""")

print("\n最近的5次上传:")
print("-" * 60)
for row in cursor.fetchall():
    print(f"上传ID: {row[0]}, 日期: {row[1]}")
    print(f"  识别科目: {row[2]}")
    print(f"  识别章节: {row[3]}")
    print()

conn.close()
