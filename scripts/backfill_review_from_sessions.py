"""
从 LearningSession 回填 chapter_review 数据

LearningSession 有 uploaded_content 和 chapter_id，
可以构造兼容对象调用 sync_review_chapter_from_upload。

用法：
  python -m scripts.backfill_review_from_sessions          # dry-run
  python -m scripts.backfill_review_from_sessions --apply  # 写入
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import date
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from database.domains import AppSessionLocal, ReviewBase, review_engine
from database.audit import ensure_audit_tables
from models import Chapter
from learning_tracking_models import (
    ChapterReviewChapter,
    ChapterReviewUnit,
    INVALID_CHAPTER_IDS,
    LearningSession,
)
from services.chapter_review_service import sync_review_chapter_from_upload


class _FakeUpload:
    """Duck-type 兼容 DailyUpload，sync 函数只用 .date 和 .raw_content"""
    def __init__(self, raw_content: str, upload_date):
        self.raw_content = raw_content
        self.date = upload_date


def main() -> int:
    parser = argparse.ArgumentParser(description="从 LearningSession 回填 chapter_review")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    ReviewBase.metadata.create_all(review_engine)
    ensure_audit_tables()

    db = AppSessionLocal()
    try:
        sessions = (
            db.query(LearningSession)
            .filter(LearningSession.uploaded_content.isnot(None))
            .order_by(LearningSession.started_at.asc())
            .all()
        )
        print(f"总 LearningSession（有内容）: {len(sessions)}")

        # 按 chapter_id 分组
        groups: dict[str, list[LearningSession]] = defaultdict(list)
        skipped = 0
        for s in sessions:
            cid = (s.chapter_id or "").strip()
            if not cid or cid in INVALID_CHAPTER_IDS or cid.endswith("_ch0") or cid == "0":
                skipped += 1
                continue
            groups[cid].append(s)

        print(f"有效 chapter 分组: {len(groups)}, 跳过: {skipped}")
        print(f"模式: {'DRY-RUN' if dry_run else '⚡ APPLY'}")
        print()

        existing_ids = {rc.chapter_id for rc in db.query(ChapterReviewChapter).all()}
        created = 0
        updated = 0
        errors = 0

        for chapter_id, session_list in sorted(groups.items()):
            latest = session_list[-1]
            chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()

            # 构造 extracted dict
            extracted = {
                "chapter_id": chapter_id,
                "chapter_title": getattr(chapter, "chapter_title", "") or latest.title or "未识别",
                "book": getattr(chapter, "book", "") or "未识别",
                "summary": getattr(chapter, "description", "") or "",
            }

            # 构造 fake upload
            upload_date = latest.started_at.date() if latest.started_at else date.today()
            fake_upload = _FakeUpload(
                raw_content=latest.uploaded_content or "",
                upload_date=upload_date,
            )

            actor_key = "device:local-default"
            is_existing = chapter_id in existing_ids
            action = "UPDATE" if is_existing else "CREATE"

            if dry_run:
                print(f"  [{action}] {extracted['book']} / {extracted['chapter_title']}")
                print(f"           chapter_id={chapter_id}, sessions={len(session_list)}, content_len={len(latest.uploaded_content or '')}")
                if is_existing:
                    updated += 1
                else:
                    created += 1
                continue

            try:
                result = sync_review_chapter_from_upload(
                    db,
                    actor_key=actor_key,
                    upload_record=fake_upload,
                    chapter=chapter,
                    extracted=extracted,
                )
                if result is not None:
                    if is_existing:
                        updated += 1
                    else:
                        created += 1
                    unit_count = len([u for u in result.units if u.is_active])
                    print(f"  ✅ [{action}] {extracted['book']} / {extracted['chapter_title']} ({unit_count} units)")
                else:
                    print(f"  ⏭️  [SKIP] {extracted['book']} / {extracted['chapter_title']}")
            except Exception as exc:
                errors += 1
                print(f"  ❌ [ERROR] {extracted['book']} / {extracted['chapter_title']}: {exc}")
                db.rollback()

        if not dry_run:
            db.commit()
            total_rc = db.query(ChapterReviewChapter).count()
            total_ru = db.query(ChapterReviewUnit).filter(ChapterReviewUnit.is_active.is_(True)).count()
            print(f"\n✅ 回填完成: 新建 {created}, 更新 {updated}, 错误 {errors}")
            print(f"   review chapters: {total_rc}, active units: {total_ru}")
        else:
            print(f"\nDRY-RUN: 将新建 {created}, 将更新 {updated}")
            print("加 --apply 执行写入")

        return 1 if errors > 0 else 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
