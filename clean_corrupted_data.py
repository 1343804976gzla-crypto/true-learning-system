"""清理数据库中的乱码记录"""
import sqlite3
from pathlib import Path

db_path = Path("C:/Users/35456/true-learning-system/data/learning.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 60)
print("清理数据库中的乱码记录")
print("=" * 60)

# 1. 查找乱码上传记录
cursor.execute("""
    SELECT id, date, raw_content
    FROM daily_uploads
    WHERE LENGTH(raw_content) > 0
""")

corrupted_ids = []
for row in cursor.fetchall():
    upload_id, date, raw_content = row
    if not raw_content:
        continue

    # 检查是否为乱码
    question_mark_ratio = raw_content.count('?') / len(raw_content)
    chinese_chars = sum(1 for c in raw_content if '\u4e00' <= c <= '\u9fff')

    if question_mark_ratio > 0.5 or chinese_chars < 10:
        corrupted_ids.append(upload_id)
        print(f"发现乱码记录: 上传ID {upload_id}, 日期 {date}")
        print(f"  问号比例: {question_mark_ratio:.2%}")
        print(f"  中文字符数: {chinese_chars}")

print(f"\n共发现 {len(corrupted_ids)} 条乱码记录")

if corrupted_ids:
    # 询问是否删除
    print("\n是否删除这些乱码记录? (y/n): ", end="")
    choice = input().strip().lower()

    if choice == 'y':
        # 删除乱码上传记录
        placeholders = ','.join('?' * len(corrupted_ids))
        cursor.execute(f"""
            DELETE FROM daily_uploads
            WHERE id IN ({placeholders})
        """, corrupted_ids)

        deleted_count = cursor.rowcount
        print(f"✅ 已删除 {deleted_count} 条乱码上传记录")

        # 删除对应的无效章节
        cursor.execute("""
            DELETE FROM chapters
            WHERE id LIKE '%无法识别%' OR id LIKE '%未分类%'
        """)

        deleted_chapters = cursor.rowcount
        print(f"✅ 已删除 {deleted_chapters} 个无效章节")

        # 删除对应的知识点掌握记录
        cursor.execute("""
            DELETE FROM concept_mastery
            WHERE chapter_id LIKE '%无法识别%' OR chapter_id LIKE '%未分类%'
        """)

        deleted_concepts = cursor.rowcount
        print(f"✅ 已删除 {deleted_concepts} 条无效知识点记录")

        conn.commit()
        print("\n清理完成!")
    else:
        print("取消删除")
else:
    print("\n没有发现乱码记录")

conn.close()
