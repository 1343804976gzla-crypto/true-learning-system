"""
清理脚本：删除 daily_uploads 中的垃圾数据 + 对应的 chapter_review 记录

垃圾数据分类：
  A. 失败记录：unknown_ch0, 无法识别, 未分类, 未知_ch未知 (10条)
  B. seed 测试数据 (3条)
  C. 早期测试数据 ≤2月18日 (123条，含大量 raw_len<100 的调试数据)
  D. 重复/格式不一致的 chapter_id（surgery.ch15 vs surgery_ch15 等）

同时清理：
  - chapter_review_chapters 中由这些垃圾上传产生的记录
  - 关联的 chapter_review_units / tasks / task_questions
  - chapters 表中的测试/垃圾条目

用法：
  python -m scripts.cleanup_junk_data          # dry-run
  python -m scripts.cleanup_junk_data --apply  # 真正删除
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from database.domains import AppSessionLocal, ReviewBase, review_engine, ContentBase, content_engine
from models import DailyUpload, Chapter, ConceptMastery
from learning_tracking_models import (
    ChapterReviewChapter,
    ChapterReviewUnit,
)


# ── 要清理的 chapter_review_chapters ──
# 全部 18 条都来自回填脚本，其中大部分源自测试/失败上传
JUNK_REVIEW_CHAPTER_IDS = [
    "internal_medicine_ch3.2",       # 测试数据 raw_len=52
    "internal_medicine_ch循环系统",     # 测试数据 raw_len=60
    "internal_medicine_ch未知",        # chapter_id 含"未知"
    "internal_medicine_ch第三章",       # 测试数据 raw_len=60
    "medicine.ch3-2",                # 测试数据 raw_len=60
    "pathology_ch1",                 # 早期测试
    "pathology_ch第一章",              # 早期测试
    "physiology_ch06",               # 早期测试 raw_len=16
    "physiology_ch08",               # 尿的生成和排出 - 用户确认未上传
    "physiology_ch6",                # 重复 chapter_id
    "physiology_ch未知",              # chapter_id 含"未知"
    "physiology_ch消化系统",            # 早期测试
    "physiology_ch章节号",             # chapter_id 含"章节号"（AI提取失败）
    "physiology_ch第六章",             # 重复 chapter_id
    "surgery.ch15",                  # 腹外疝 - 用户确认未上传
    "surgery_ch15",                  # 腹外疝重复
    "无法识别_ch无法识别",               # 失败记录
    "未知_ch未知",                     # 失败记录
]

# ── 要清理的 chapters 表条目 ──
JUNK_CHAPTER_IDS = [
    "0",                             # 未分类占位
    "uncategorized_ch0",             # 未分类占位
    "未分类_ch0",                     # 未分类占位
    "internal_medicine_ch3.2",
    "internal_medicine_ch循环系统",
    "internal_medicine_ch未知",
    "internal_medicine_ch第三章",
    "medicine.ch3-2",
    "pathology_ch第一章",
    "physiology_ch06",
    "physiology_ch08",
    "physiology_ch未知",
    "physiology_ch消化系统",
    "physiology_ch章节号",
    "physiology_ch第六章",
    "surgery.ch15",
]

# test_ch_ 开头的审计测试章节也要清理
TEST_CHAPTER_PREFIX = "test_ch_"
# chapter-UUID 格式的也是测试数据
UUID_CHAPTER_PREFIX = "chapter-"


def main() -> int:
    parser = argparse.ArgumentParser(description="清理垃圾上传和 review 数据")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认 dry-run）")
    args = parser.parse_args()
    dry_run = not args.apply

    db = AppSessionLocal()
    try:
        print(f"模式: {'DRY-RUN' if dry_run else '⚡ APPLY'}")
        print("=" * 60)

        # ── 1. 清理 chapter_review 级联数据 ──
        print("\n📋 1. 清理 chapter_review_chapters + 级联")
        review_chapters = db.query(ChapterReviewChapter).filter(
            ChapterReviewChapter.chapter_id.in_(JUNK_REVIEW_CHAPTER_IDS)
        ).all()

        total_units = 0
        total_tasks = 0
        total_questions = 0
        for rc in review_chapters:
            units = db.query(ChapterReviewUnit).filter(
                ChapterReviewUnit.review_chapter_id == rc.id
            ).all()
            unit_count = len(units)
            total_units += unit_count
            print(f"  🗑️  review_chapter id={rc.id} [{rc.chapter_id}] {rc.book}/{rc.chapter_title} ({unit_count} units)")

            if not dry_run:
                # 删除 tasks 和 task_questions（通过 unit）
                for unit in units:
                    from learning_tracking_models import ChapterReviewTask, ChapterReviewTaskQuestion
                    tasks = db.query(ChapterReviewTask).filter(
                        ChapterReviewTask.unit_id == unit.id
                    ).all()
                    for task in tasks:
                        q_count = db.query(ChapterReviewTaskQuestion).filter(
                            ChapterReviewTaskQuestion.task_id == task.id
                        ).delete()
                        total_questions += q_count
                        total_tasks += 1
                    db.query(ChapterReviewTask).filter(
                        ChapterReviewTask.unit_id == unit.id
                    ).delete()
                    db.delete(unit)
                db.delete(rc)

        print(f"  小计: {len(review_chapters)} chapters, {total_units} units")

        # ── 2. 清理 daily_uploads ──
        print("\n📋 2. 清理 daily_uploads")

        # 2a. 失败记录
        failed_uploads = db.query(DailyUpload).filter(
            DailyUpload.id.in_([7, 8, 10, 11, 13, 60, 61, 62, 66, 67])
        ).all()
        print(f"  失败/无法识别: {len(failed_uploads)} 条")

        # 2b. seed 数据
        seed_uploads = db.query(DailyUpload).filter(
            DailyUpload.id.in_([115, 116, 117])
        ).all()
        print(f"  seed 测试: {len(seed_uploads)} 条")

        # 2c. 早期测试数据 (≤2月18日，排除已在上面列出的)
        early_uploads = db.query(DailyUpload).filter(
            DailyUpload.date <= "2026-02-18"
        ).all()
        # 去重（有些已在 failed 中）
        failed_ids = {u.id for u in failed_uploads}
        early_only = [u for u in early_uploads if u.id not in failed_ids]
        print(f"  早期测试(≤2/18): {len(early_only)} 条")

        # 2d. 2月18日之后的全部（用户确认全部清除）
        post_uploads = db.query(DailyUpload).filter(
            DailyUpload.date > "2026-02-18"
        ).all()
        post_only = [u for u in post_uploads if u.id not in failed_ids and u.id not in {115, 116, 117}]
        print(f"  2/18后剩余: {len(post_only)} 条（全部删除）")

        to_delete = failed_uploads + seed_uploads + early_only + post_only
        # 去重
        seen = set()
        unique_delete = []
        for u in to_delete:
            if u.id not in seen:
                seen.add(u.id)
                unique_delete.append(u)

        print(f"\n  总计删除: {len(unique_delete)} / 138 条 daily_uploads")

        if not dry_run:
            for u in unique_delete:
                db.delete(u)

        # ── 3. 清理 chapters 表 ──
        print("\n📋 3. 清理 chapters 表垃圾条目")

        junk_chapters = db.query(Chapter).filter(
            Chapter.id.in_(JUNK_CHAPTER_IDS)
        ).all()
        print(f"  固定列表: {len(junk_chapters)} 条")

        test_chapters = db.query(Chapter).filter(
            Chapter.id.like(f"{TEST_CHAPTER_PREFIX}%")
        ).all()
        print(f"  test_ch_ 前缀: {len(test_chapters)} 条")

        uuid_chapters = db.query(Chapter).filter(
            Chapter.id.like(f"{UUID_CHAPTER_PREFIX}%")
        ).all()
        print(f"  chapter-UUID: {len(uuid_chapters)} 条")

        all_junk_ch = junk_chapters + test_chapters + uuid_chapters
        junk_ch_ids = [ch.id for ch in all_junk_ch]
        print(f"  总计删除: {len(all_junk_ch)} 条 chapters")

        # 先删关联的 concept_mastery（外键约束）
        if junk_ch_ids:
            cm_count = db.query(ConceptMastery).filter(
                ConceptMastery.chapter_id.in_(junk_ch_ids)
            ).count()
            print(f"  关联 concept_mastery: {cm_count} 条")

            if not dry_run:
                db.query(ConceptMastery).filter(
                    ConceptMastery.chapter_id.in_(junk_ch_ids)
                ).delete(synchronize_session='fetch')

        if not dry_run:
            for ch in all_junk_ch:
                db.delete(ch)

        # ── 4. 提交 ──
        if not dry_run:
            db.commit()
            print("\n" + "=" * 60)
            remaining_uploads = db.query(DailyUpload).count()
            remaining_chapters = db.query(Chapter).count()
            remaining_rc = db.query(ChapterReviewChapter).count()
            remaining_ru = db.query(ChapterReviewUnit).count()
            print(f"✅ 清理完成")
            print(f"   剩余 daily_uploads: {remaining_uploads}")
            print(f"   剩余 chapters: {remaining_chapters}")
            print(f"   剩余 review_chapters: {remaining_rc}")
            print(f"   剩余 review_units: {remaining_ru}")
        else:
            print("\n" + "=" * 60)
            print("DRY-RUN 完成，加 --apply 执行真正删除")

        return 0

    except Exception as e:
        db.rollback()
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
