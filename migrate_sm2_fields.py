"""
迁移脚本：为 wrong_answers_v2 表添加 SM-2 间隔重复字段
"""
import sqlite3
import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def resolve_db_path() -> str:
    db_setting = (os.getenv("DATABASE_PATH") or "./data/learning.db").strip()

    if db_setting.startswith("sqlite:///"):
        parsed = urlparse(db_setting)
        raw_path = parsed.path
        # Windows sqlite URL: /C:/path/to/file.db -> C:/path/to/file.db
        if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        db_path = Path(raw_path)
    else:
        db_path = Path(db_setting)
        if not db_path.is_absolute():
            db_path = (BASE_DIR / db_path).resolve()

    return str(db_path)

MIGRATIONS = [
    "ALTER TABLE wrong_answers_v2 ADD COLUMN sm2_ef REAL DEFAULT 2.5",
    "ALTER TABLE wrong_answers_v2 ADD COLUMN sm2_interval INTEGER DEFAULT 0",
    "ALTER TABLE wrong_answers_v2 ADD COLUMN sm2_repetitions INTEGER DEFAULT 0",
    "ALTER TABLE wrong_answers_v2 ADD COLUMN next_review_date DATE",
]

def migrate():
    db_path = resolve_db_path()
    print(f"Using DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for sql in MIGRATIONS:
        col_name = sql.split("ADD COLUMN ")[1].split(" ")[0]
        try:
            cursor.execute(sql)
            print(f"  ✅ 添加字段: {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  ⏭️ 字段已存在: {col_name}")
            else:
                print(f"  ❌ 失败: {col_name} - {e}")
    conn.commit()
    conn.close()
    print("\n✅ SM-2 字段迁移完成")

if __name__ == "__main__":
    migrate()
