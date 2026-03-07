"""
融合升级功能数据库迁移脚本
为 WrongAnswerV2 表添加融合相关字段

运行方式: python migrate_fusion_fields.py
"""

from sqlalchemy import create_engine, text
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "learning.db")


def migrate():
    """执行数据库迁移"""
    engine = create_engine(f"sqlite:///{DB_PATH}")

    with engine.connect() as conn:
        # 检查表是否存在
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='wrong_answers_v2'"))
        table_exists = result.fetchone() is not None

        if not table_exists:
            print("⚠️  表 wrong_answers_v2 不存在！")
            print("📝 可能的原因：")
            print("   1. 数据库是全新的，需要先运行应用创建基础表")
            print("   2. 数据库文件路径不正确")
            print(f"\n📂 数据库路径: {DB_PATH}")
            print("\n💡 建议操作：")
            print("   1. 先启动应用一次，让 SQLAlchemy 创建基础表")
            print("   2. 然后再运行此迁移脚本")
            print("   3. 或者删除空数据库文件，让应用重新创建包含新字段的表")
            return

        # 检查字段是否已存在
        result = conn.execute(text("PRAGMA table_info(wrong_answers_v2)"))
        existing_columns = {row[1] for row in result}

        print("🔍 检查现有字段...")

        # 添加 parent_ids 字段 (JSON)
        if "parent_ids" not in existing_columns:
            conn.execute(text("""
                ALTER TABLE wrong_answers_v2
                ADD COLUMN parent_ids JSON
            """))
            print("✅ 添加 parent_ids 字段")
        else:
            print("⏭️  parent_ids 字段已存在，跳过")

        # 添加 is_fusion 字段 (Boolean) + 索引
        if "is_fusion" not in existing_columns:
            conn.execute(text("""
                ALTER TABLE wrong_answers_v2
                ADD COLUMN is_fusion BOOLEAN DEFAULT 0
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_wrong_answers_v2_is_fusion
                ON wrong_answers_v2(is_fusion)
            """))
            print("✅ 添加 is_fusion 字段和索引")
        else:
            print("⏭️  is_fusion 字段已存在，跳过")

        # 添加 fusion_level 字段 (Integer) + 索引
        if "fusion_level" not in existing_columns:
            conn.execute(text("""
                ALTER TABLE wrong_answers_v2
                ADD COLUMN fusion_level INTEGER DEFAULT 0
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_wrong_answers_v2_fusion_level
                ON wrong_answers_v2(fusion_level)
            """))
            print("✅ 添加 fusion_level 字段和索引")
        else:
            print("⏭️  fusion_level 字段已存在，跳过")

        # 添加 sm2_penalty_factor 字段 (Float)
        if "sm2_penalty_factor" not in existing_columns:
            conn.execute(text("""
                ALTER TABLE wrong_answers_v2
                ADD COLUMN sm2_penalty_factor FLOAT DEFAULT 1.0
            """))
            print("✅ 添加 sm2_penalty_factor 字段")
        else:
            print("⏭️  sm2_penalty_factor 字段已存在，跳过")

        # 添加 fusion_data 字段 (JSON)
        if "fusion_data" not in existing_columns:
            conn.execute(text("""
                ALTER TABLE wrong_answers_v2
                ADD COLUMN fusion_data JSON
            """))
            print("✅ 添加 fusion_data 字段")
        else:
            print("⏭️  fusion_data 字段已存在，跳过")

        conn.commit()

        # 验证迁移结果
        result = conn.execute(text("PRAGMA table_info(wrong_answers_v2)"))
        all_columns = {row[1] for row in result}

        required_columns = {
            "parent_ids", "is_fusion", "fusion_level",
            "sm2_penalty_factor", "fusion_data"
        }

        if required_columns.issubset(all_columns):
            print("\n🎉 融合升级字段迁移完成！")
            print("📊 新增字段:")
            for col in sorted(required_columns):
                print(f"   • {col}")
        else:
            missing = required_columns - all_columns
            print(f"\n⚠️  迁移可能不完整，缺少字段: {missing}")


def rollback():
    """
    回滚迁移（SQLite 不支持 DROP COLUMN，需要重建表）
    警告：此操作会重建表，请确保有备份
    """
    print("⚠️  SQLite 不支持直接删除列，回滚需要重建表")
    print("如需回滚，请手动执行以下步骤：")
    print("1. 备份数据库")
    print("2. 创建新表（不含融合字段）")
    print("3. 迁移数据")
    print("4. 删除旧表，重命名新表")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        rollback()
    else:
        migrate()
