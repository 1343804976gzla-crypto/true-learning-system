import sqlite3
import os

# 检查数据库文件
db_path = os.path.join('data', 'learning.db')
print(f'数据库路径: {db_path}')
print(f'数据库存在: {os.path.exists(db_path)}')

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 检查所有表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f'\n数据库中的表:')
    for t in tables:
        print(f'  - {t[0]}')
    
    # 检查学习轨迹表是否存在
    tracking_tables = ['learning_sessions', 'learning_activities', 'question_records', 'daily_learning_logs']
    for table in tracking_tables:
        cursor.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='{table}';")
        exists = cursor.fetchone()[0] > 0
        status = "存在" if exists else "不存在"
        print(f'\n  {table}: {status}')
        
        if exists:
            cursor.execute(f'SELECT COUNT(*) FROM {table};')
            count = cursor.fetchone()[0]
            print(f'    记录数: {count}')
    
    conn.close()
else:
    print('数据库文件不存在！')
