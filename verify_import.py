import sqlite3
import json

conn = sqlite3.connect('data/learning.db')
cursor = conn.cursor()

# 查看示例章节
cursor.execute("SELECT * FROM chapters WHERE id = 'internal_medicine_ch11'")
row = cursor.fetchone()
print('示例章节:')
print(f'  ID: {row[0]}')
print(f'  书名: {row[1]}')
print(f'  章节号: {row[3]}')
print(f'  章节标题: {row[4]}')
print(f'  知识点数量: {len(json.loads(row[6]))}')

# 查看前5个知识点
cursor.execute("SELECT concept_id, name FROM concept_mastery WHERE chapter_id = 'internal_medicine_ch11' LIMIT 5")
print('\n前5个知识点:')
for r in cursor.fetchall():
    print(f'  - {r[0][:60]}')
    print(f'    名称: {r[1]}')

# 统计总数
cursor.execute("SELECT COUNT(*) FROM chapters")
chapters = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM concept_mastery")
concepts = cursor.fetchone()[0]
print(f'\n总计: {chapters} 章节, {concepts} 个知识点')

conn.close()
