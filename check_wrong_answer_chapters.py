"""检查错题本中缺失章节信息的错题"""
import sqlite3
from pathlib import Path

db_path = Path("C:/Users/35456/true-learning-system/data/learning.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 60)
print("错题本章节信息检查")
print("=" * 60)

# 检查错题总数
cursor.execute("SELECT COUNT(*) FROM wrong_answers_v2")
total_count = cursor.fetchone()[0]
print(f"\n错题总数: {total_count}")

# 检查没有章节信息的错题
cursor.execute("""
    SELECT COUNT(*) FROM wrong_answers_v2
    WHERE chapter_id IS NULL OR chapter_id = ''
""")
no_chapter_count = cursor.fetchone()[0]
print(f"缺失章节信息: {no_chapter_count} ({no_chapter_count/max(total_count,1)*100:.1f}%)")

# 检查有章节信息的错题
cursor.execute("""
    SELECT COUNT(*) FROM wrong_answers_v2
    WHERE chapter_id IS NOT NULL AND chapter_id != ''
""")
has_chapter_count = cursor.fetchone()[0]
print(f"已有章节信息: {has_chapter_count} ({has_chapter_count/max(total_count,1)*100:.1f}%)")

# 查看一些没有章节信息的错题
print("\n" + "=" * 60)
print("缺失章节信息的错题示例（前5条）")
print("=" * 60)

cursor.execute("""
    SELECT id, question_text, key_point, severity_tag
    FROM wrong_answers_v2
    WHERE chapter_id IS NULL OR chapter_id = ''
    LIMIT 5
""")

for row in cursor.fetchall():
    wrong_id, question, key_point, severity = row
    print(f"\n错题ID: {wrong_id}")
    print(f"  严重度: {severity}")
    print(f"  考点: {key_point or '(无)'}")
    print(f"  题目: {question[:100]}...")

# 按章节统计错题分布
print("\n" + "=" * 60)
print("已归类错题的章节分布（Top 10）")
print("=" * 60)

cursor.execute("""
    SELECT
        w.chapter_id,
        c.book,
        c.chapter_title,
        COUNT(*) as count
    FROM wrong_answers_v2 w
    LEFT JOIN chapters c ON w.chapter_id = c.id
    WHERE w.chapter_id IS NOT NULL AND w.chapter_id != ''
    GROUP BY w.chapter_id
    ORDER BY count DESC
    LIMIT 10
""")

for row in cursor.fetchall():
    chapter_id, book, title, count = row
    print(f"{book or '(未知)'} - {title or '(未知)'}: {count}题")

conn.close()
