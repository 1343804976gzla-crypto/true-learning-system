"""迁移脚本：为变式手术添加字段"""
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

    # WrongAnswerV2: variant_data
    try:
        c.execute("ALTER TABLE wrong_answers_v2 ADD COLUMN variant_data TEXT")
        print("  + wrong_answers_v2.variant_data")
    except Exception as e:
        print(f"  ~ wrong_answers_v2.variant_data 已存在: {e}")

    # WrongAnswerRetry: is_variant, rationale_text, ai_evaluation
    for col, typ in [
        ("is_variant", "BOOLEAN DEFAULT 0"),
        ("rationale_text", "TEXT"),
        ("ai_evaluation", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE wrong_answer_retries ADD COLUMN {col} {typ}")
            print(f"  + wrong_answer_retries.{col}")
        except Exception as e:
            print(f"  ~ wrong_answer_retries.{col} 已存在: {e}")

    conn.commit()
    conn.close()
    print("✅ 变式字段迁移完成")


if __name__ == "__main__":
    migrate()
