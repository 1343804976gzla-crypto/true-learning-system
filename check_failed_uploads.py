"""检查识别失败的上传记录"""
import sqlite3
import json
from pathlib import Path

db_path = Path("C:/Users/35456/true-learning-system/data/learning.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 60)
print("检查识别失败的上传记录")
print("=" * 60)

# 查找识别失败的记录
cursor.execute("""
    SELECT id, date, raw_content, ai_extracted
    FROM daily_uploads
    WHERE json_extract(ai_extracted, '$.book') IN ('未知', '无法识别', '未分类', '')
       OR json_extract(ai_extracted, '$.chapter_title') IN ('未识别章节', '')
    ORDER BY date DESC
    LIMIT 5
""")

for row in cursor.fetchall():
    upload_id, date, raw_content, ai_extracted = row

    print(f"\n上传ID: {upload_id}, 日期: {date}")
    print("-" * 60)

    # 解析AI识别结果
    try:
        extracted = json.loads(ai_extracted)
        print(f"识别结果:")
        print(f"  科目: {extracted.get('book')}")
        print(f"  章节号: {extracted.get('chapter_number')}")
        print(f"  章节标题: {extracted.get('chapter_title')}")
        print(f"  章节ID: {extracted.get('chapter_id')}")
        if extracted.get('error'):
            print(f"  错误: {extracted.get('error')}")
    except:
        print(f"  无法解析AI识别结果")

    # 显示原始内容的前500字符
    print(f"\n原始内容 (前500字符):")
    print(raw_content[:500] if raw_content else "(空)")
    print()

conn.close()
