"""
批量为未分类错题识别章节
支持试运行、进度显示、错误处理
"""
import sqlite3
import asyncio
import sys
from pathlib import Path
from typing import List, Tuple, Dict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from services.content_parser_v2 import get_content_parser

db_path = Path("C:/Users/35456/true-learning-system/data/learning.db")


class ChapterRecognizer:
    """错题章节识别器"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.parser = None
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0
        }

    async def initialize(self):
        """初始化解析器"""
        self.parser = get_content_parser()

    def get_uncategorized_wrongs(self, limit: int = None) -> List[Tuple]:
        """获取未分类的错题（精准匹配，不误伤已分类记录）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = """
            SELECT id, question_text, key_point, chapter_id, severity_tag
            FROM wrong_answers_v2
            WHERE chapter_id = '0'
               OR chapter_id IS NULL
               OR chapter_id = ''
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        results = cursor.fetchall()
        conn.close()

        return results

    async def recognize_chapter(self, question_text: str, key_point: str) -> Dict:
        """识别单个错题的章节"""
        # 构建识别内容
        content = f"{key_point or ''}\n\n{question_text[:500]}"

        try:
            result = await self.parser.parse_content(content)

            book = result.get('book', '')
            chapter_id = result.get('chapter_id', '')
            chapter_number = result.get('chapter_number', '')
            chapter_title = result.get('chapter_title', '')

            # 验证识别结果
            if chapter_id and chapter_id not in ['unknown_ch0', '未知_ch0', '无法识别_ch0', '未分类_ch0']:
                return {
                    'success': True,
                    'chapter_id': chapter_id,
                    'book': book,
                    'chapter_number': chapter_number,
                    'chapter_title': chapter_title
                }
            else:
                return {
                    'success': False,
                    'reason': '无法识别有效章节'
                }

        except Exception as e:
            return {
                'success': False,
                'reason': str(e)
            }

    def update_chapter(self, wrong_id: int, chapter_id: str) -> bool:
        """更新错题的章节ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE wrong_answers_v2
                SET chapter_id = ?
                WHERE id = ?
            """, (chapter_id, wrong_id))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            print(f"  ❌ 更新数据库失败: {e}")
            return False

    async def process_batch(self, wrongs: List[Tuple], dry_run: bool = True) -> None:
        """处理一批错题"""
        total = len(wrongs)
        self.stats['total'] = total

        print(f"\n开始处理 {total} 条未分类错题...")
        print("=" * 70)

        for i, (wrong_id, question, key_point, old_chapter_id, severity) in enumerate(wrongs, 1):
            print(f"\n[{i}/{total}] 错题ID: {wrong_id}")
            print(f"  严重度: {severity}")
            print(f"  考点: {key_point or '(无)'}")
            print(f"  题目: {question[:80]}...")

            # 识别章节
            result = await self.recognize_chapter(question, key_point)

            if result['success']:
                print(f"  ✅ 识别成功: {result['book']} - {result['chapter_title']}")
                print(f"     章节ID: {result['chapter_id']}")

                if not dry_run:
                    # 更新数据库
                    if self.update_chapter(wrong_id, result['chapter_id']):
                        print(f"     ✓ 已更新数据库")
                        self.stats['success'] += 1
                    else:
                        self.stats['failed'] += 1
                else:
                    print(f"     (试运行模式，未更新数据库)")
                    self.stats['success'] += 1

            else:
                print(f"  ⚠️ 识别失败: {result['reason']}")
                self.stats['failed'] += 1

            # 每10题暂停一下
            if i % 10 == 0 and i < total:
                print(f"\n已处理 {i}/{total}，暂停2秒...")
                await asyncio.sleep(2)

    def print_summary(self, dry_run: bool):
        """打印统计摘要"""
        print("\n" + "=" * 70)
        print("处理完成")
        print("=" * 70)
        print(f"总计: {self.stats['total']} 题")
        print(f"成功: {self.stats['success']} 题 ({self.stats['success']/max(self.stats['total'],1)*100:.1f}%)")
        print(f"失败: {self.stats['failed']} 题 ({self.stats['failed']/max(self.stats['total'],1)*100:.1f}%)")

        if dry_run:
            print("\n⚠️ 试运行模式，未实际更新数据库")
            print("如需正式运行，请使用: python classify_wrong_answers.py --run")
        else:
            print("\n✅ 已更新数据库")

    def export_results(self, wrongs: List[Tuple], results: List[Dict], filename: str):
        """导出识别结果到文件"""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("# 错题章节识别结果\n\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"总计: {len(wrongs)} 题\n")
            f.write(f"成功: {self.stats['success']} 题\n")
            f.write(f"失败: {self.stats['failed']} 题\n\n")
            f.write("=" * 70 + "\n\n")

            for (wrong_id, question, key_point, old_chapter_id, severity), result in zip(wrongs, results):
                f.write(f"## 错题ID: {wrong_id}\n\n")
                f.write(f"**考点**: {key_point or '(无)'}\n\n")
                f.write(f"**题目**: {question[:200]}...\n\n")

                if result and result['success']:
                    f.write(f"**识别结果**: ✅ {result['book']} - {result['chapter_title']}\n\n")
                    f.write(f"**章节ID**: {result['chapter_id']}\n\n")
                else:
                    f.write(f"**识别结果**: ❌ 失败\n\n")
                    if result:
                        f.write(f"**原因**: {result.get('reason', '未知')}\n\n")

                f.write("-" * 70 + "\n\n")

        print(f"\n结果已导出到: {filename}")


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="批量为未分类错题识别章节",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 试运行（不更新数据库）
  python classify_wrong_answers.py

  # 正式运行（更新数据库）
  python classify_wrong_answers.py --run

  # 只处理前20题
  python classify_wrong_answers.py --run --limit 20

  # 导出识别结果
  python classify_wrong_answers.py --export results.md
        """
    )

    parser.add_argument('--run', action='store_true', help='正式运行（实际更新数据库）')
    parser.add_argument('--limit', type=int, help='限制处理的错题数量')
    parser.add_argument('--export', type=str, help='导出识别结果到文件')

    args = parser.parse_args()

    # 创建识别器
    recognizer = ChapterRecognizer(db_path)

    print("=" * 70)
    print("错题章节批量识别工具")
    print("=" * 70)
    print(f"模式: {'正式运行' if args.run else '试运行'}")
    print(f"数据库: {db_path}")

    # 初始化
    await recognizer.initialize()

    # 获取未分类错题
    print("\n正在查询未分类错题...")
    wrongs = recognizer.get_uncategorized_wrongs(limit=args.limit)

    if not wrongs:
        print("\n✅ 没有需要处理的未分类错题")
        return

    print(f"找到 {len(wrongs)} 条未分类错题")

    if not args.run:
        print("\n⚠️ 当前为试运行模式，不会实际更新数据库")
        print("如需正式运行，请使用 --run 参数")

    # 确认
    if args.run:
        print(f"\n即将更新 {len(wrongs)} 条错题的章节信息")
        response = input("确认继续？(y/n): ")
        if response.lower() != 'y':
            print("已取消")
            return

    # 处理
    await recognizer.process_batch(wrongs, dry_run=not args.run)

    # 打印摘要
    recognizer.print_summary(dry_run=not args.run)

    # 导出结果
    if args.export:
        # 需要重新识别以获取结果（简化版，实际应该在process_batch中收集）
        print(f"\n导出功能需要重新识别，暂不支持")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n\n错误: {e}")
        import traceback
        traceback.print_exc()
