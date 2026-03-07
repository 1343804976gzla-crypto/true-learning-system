"""迁移脚本：为地雷盲测添加字段"""
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
        if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        db_path = Path(raw_path)
    else:
        db_path = Path(db_setting)
        if not db_path.is_absolute():
            db_path = (BASE_DIR / db_path).resolve()

    return str(db_path)


def migrate():
    db_path = resolve_db_path()
    print(f"Using DB: {db_path}")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    try:
        c.execute("ALTER TABLE wrong_answer_retries ADD COLUMN is_landmine_recall BOOLEAN DEFAULT 0")
        print("  + wrong_answer_retries.is_landmine_recall")
    except Exception as e:
        print(f"  ~ wrong_answer_retries.is_landmine_recall 已存在: {e}")

    conn.commit()
    conn.close()
    print("✅ 地雷盲测字段迁移完成")


if __name__ == "__main__":
    migrate()
