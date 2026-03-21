"""
回填脚本：从 daily_uploads 批量生成 ChapterReviewChapter + ChapterReviewUnit

背景：
  chapter_review 功能是后加的，138 条历史上传从未触发过 sync_review_chapter_from_upload。
  本脚本遍历所有有效上传，按 chapter_id 分组，每组取最新一条调用 sync 函数。

用法：
  cd true-learning-system
  python -m scripts.backfill_chapter_review          # dry-run（默认）
  python -m scripts.backfill_chapter_review --apply   # 真正写入
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from database.domains import AppSessionLocal, ReviewBase, review_engine
from database.audit import ensure_audit_tables
from models import DailyUpload, Chapter
from learning_tracking_models import (
    ChapterReviewChapter,
    ChapterReviewUnit,
    INVALID_CHAPTER_IDS,
)
from services.chapter_review_service import sync_review_chapter_from_upload
from services.data_identity import build_actor_key


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 chapter_review 数据")
    parser.add_argument("--apply", action="store_true", help="真正写入数据库（默认 dry-run）")
    parser.add_argument("--skip-seed", action="store_true", default=True, help="跳过 seed 测试数据")
    args = parser.parse_args()

    dry_run = not args.apply

    # 确保表存在
    ReviewBase.metadata.create_all(review_engine)
    ensure_audit_tables()

    db = AppSessionLocal()
    try:
        # 加载所有上传
        uploads = db.query(DailyUpload).order_by(DailyUpload.date.asc()).all()
        print(f"总上传记录: {len(uploads)}")

        # 按 chapter_id 分组，每组保留所有记录（按日期排序）
        groups: dict[str, list[DailyUpload]] = defaultdict(list)
        skipped = 0
        for u in uploads:
            extracted = u.ai_extracted or {}
            chapter_id = str(extracted.get("chapter_id") or "").strip()
            if not chapter_id or chapter_id in INVALID_CHAPTER_IDS or chapter_id.endswith("_ch0"):
                skipped += 1
                continue
            if args.skip_seed and chapter_id.startswith("seed-"):
                skipped += 1
                continue
            groups[chapter_id].append(u)

        print(f"有效 chapter 分组: {len(groups)}, 跳过: {skipped}")
        print(f"模式: {'DRY-RUN（不写入）' if dry_run else '⚡ APPLY（写入数据库）'}")
        print()

        # 检查已有的 review chapters
        existing_ids = set()
        for rc in db.query(ChapterReviewChapter).all():
            existing_ids.add(rc.chapter_id)

        created = 0
        updated = 0
        errors = 0

        for chapter_id, upload_list in sorted(groups.items()):
            # 取最新的上传记录作为主记录
            latest = upload_list[-1]
            extracted = latest.ai_extracted or {}
            book = extracted.get("book", "?")
            title = extracted.get("chapter_title", "?")

            # 查找对应的 Chapter 实体
            chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()

            # 构造 actor_key
            device_id = latest.device_id or "local-default"
            user_id = latest.user_id
            actor_key = build_actor_key(user_id, device_id)

            is_existing = chapter_id in existing_ids
            action = "UPDATE" if is_existing else "CREATE"

            if dry_run:
                raw_len = len(latest.raw_content or "")
                print(f"  [{action}] {book} - {title}")
                print(f"           chapter_id={chapter_id}, uploads={len(upload_list)}, raw_len={raw_len}")
                print(f"           actor_key={actor_key}, date={latest.date}")
                if is_existing:
                    updated += 1
                else:
                    created += 1
                continue

            try:
                result = sync_review_chapter_from_upload(
                    db,
                    actor_key=actor_key,
                    upload_record=latest,
                    chapter=chapter,
                    extracted=extracted,
                )
                if result is not None:
                    if is_existing:
                        updated += 1
                    else:
                        created += 1
                    unit_count = len([u for u in result.units if u.is_active])
                    print(f"  ✅ [{action}] {book} - {title} ({unit_count} units)")
                else:
                    print(f"  ⏭️  [SKIP] {book} - {title} (sync returned None)")
            except Exception as exc:
                errors += 1
                print(f"  ❌ [ERROR] {book} - {title}: {exc}")
                db.rollback()

        if not dry_run:
            db.commit()
            print()
            print(f"✅ 回填完成: 新建 {created}, 更新 {updated}, 错误 {errors}")

            # 验证
            total_chapters = db.query(ChapterReviewChapter).count()
            total_units = db.query(ChapterReviewUnit).filter(ChapterReviewUnit.is_active.is_(True)).count()
            print(f"   当前 review chapters: {total_chapters}, active units: {total_units}")
        else:
            print()
            print(f"DRY-RUN 汇总: 将新建 {created}, 将更新 {updated}")
            print("加 --apply 参数执行真正写入")

        return 1 if errors > 0 else 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
