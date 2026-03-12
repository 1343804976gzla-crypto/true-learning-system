"""
错题章节分类 v2 - 修复版
核心改进：
1. 停服务器后运行，避免 SQLAlchemy 覆盖
2. 提供真实章节列表给 AI，确保匹配到正确的 chapter_id
3. 精准匹配 chapter_id='0' 的未分类记录
"""
import sqlite3
import asyncio
import sys
import json
from pathlib import Path
from typing import List, Tuple, Dict

sys.path.insert(0, str(Path(__file__).parent))

from services.ai_client import get_ai_client

DB_PATH = Path("C:/Users/35456/true-learning-system/data/learning.db")


def get_real_chapters() -> List[Dict]:
    """获取数据库中所有真实章节（排除自动补齐的占位符）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, book, chapter_number, chapter_title
        FROM chapters
        WHERE chapter_title NOT LIKE '%自动补齐%'
          AND id != '0'
          AND id NOT LIKE '%未分类%'
        ORDER BY book, CAST(chapter_number AS INTEGER)
    """)
    chapters = []
    for row in c.fetchall():
        chapters.append({
            "id": row[0],
            "book": row[1],
            "number": row[2],
            "title": row[3]
        })
    conn.close()
    return chapters


def build_chapter_list_text(chapters: List[Dict]) -> str:
    """构建章节列表文本供 AI 参考"""
    lines = []
    current_book = ""
    for ch in chapters:
        if ch["book"] != current_book:
            current_book = ch["book"]
            lines.append(f"\n【{current_book}】")
        lines.append(f"  {ch['id']} → {ch['title']}")
    return "\n".join(lines)


def get_uncategorized_wrongs() -> List[Tuple]:
    """获取 chapter_id='0' 的未分类错题"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, question_text, key_point, chapter_id, severity_tag
        FROM wrong_answers_v2
        WHERE chapter_id = '0'
    """)
    results = c.fetchall()
    conn.close()
    return results


async def match_chapter(ai_client, question_text: str, key_point: str, chapter_list_text: str) -> Dict:
    """让 AI 从真实章节列表中选择最匹配的章节"""
    prompt = f"""你是医学教材章节匹配专家。根据以下错题的考点和题目内容，从章节列表中选择最匹配的章节ID。

## 错题信息
考点：{key_point or '(无)'}
题目：{question_text[:400]}

## 可选章节列表
{chapter_list_text}

## 要求
1. 仔细分析题目涉及的核心知识点
2. 从上面的章节列表中选择最匹配的 chapter_id
3. 只返回一个JSON对象，格式如下：

{{"chapter_id": "选中的章节ID", "reason": "简短匹配理由"}}

注意：chapter_id 必须是上面列表中出现的值（如 physio_ch02、biochem_ch10 等），不要编造新的ID。"""

    try:
        result = await ai_client.generate_json(
            prompt,
            {"chapter_id": "string", "reason": "string"},
            max_tokens=200,
            temperature=0.1,
            use_heavy=False,
            timeout=30
        )
        chapter_id = result.get("chapter_id", "")
        reason = result.get("reason", "")
        return {"success": True, "chapter_id": chapter_id, "reason": reason}
    except Exception as e:
        return {"success": False, "reason": str(e)}


def update_chapter(wrong_id: int, chapter_id: str) -> bool:
    """更新错题的章节ID"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE wrong_answers_v2 SET chapter_id = ? WHERE id = ?", (chapter_id, wrong_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  ❌ 更新失败: {e}")
        return False


def verify_chapter_id(chapter_id: str, valid_ids: set) -> bool:
    """验证 chapter_id 是否在真实章节列表中"""
    return chapter_id in valid_ids


async def main():
    print("=" * 70)
    print("错题章节分类 v2 - 精准匹配版")
    print("=" * 70)

    # 1. 加载真实章节
    chapters = get_real_chapters()
    valid_ids = {ch["id"] for ch in chapters}
    chapter_list_text = build_chapter_list_text(chapters)
    print(f"已加载 {len(chapters)} 个真实章节")

    # 2. 获取未分类错题
    wrongs = get_uncategorized_wrongs()
    print(f"找到 {len(wrongs)} 条未分类错题 (chapter_id='0')")

    if not wrongs:
        print("\n✅ 没有未分类错题")
        return

    # 3. 初始化 AI
    ai_client = get_ai_client()
    print("AI 客户端就绪\n")

    # 4. 逐条处理
    success = 0
    failed = 0
    invalid = 0

    for i, (wrong_id, question, key_point, old_cid, severity) in enumerate(wrongs, 1):
        print(f"[{i}/{len(wrongs)}] ID={wrong_id} | {severity} | {key_point or '(无考点)'}...")

        result = await match_chapter(ai_client, question, key_point, chapter_list_text)

        if result["success"]:
            cid = result["chapter_id"]
            if verify_chapter_id(cid, valid_ids):
                if update_chapter(wrong_id, cid):
                    ch_info = next((c for c in chapters if c["id"] == cid), None)
                    title = ch_info["title"] if ch_info else "?"
                    print(f"  ✅ {cid} ({title}) - {result['reason']}")
                    success += 1
                else:
                    failed += 1
            else:
                print(f"  ⚠️ AI返回无效ID: {cid}, 跳过")
                invalid += 1
        else:
            print(f"  ❌ 识别失败: {result['reason']}")
            failed += 1

        # 每15题暂停
        if i % 15 == 0 and i < len(wrongs):
            print(f"\n--- 已处理 {i}/{len(wrongs)}，暂停1秒 ---\n")
            await asyncio.sleep(1)

    # 5. 汇总
    print("\n" + "=" * 70)
    print(f"处理完成！")
    print(f"  成功: {success} 题")
    print(f"  失败: {failed} 题")
    print(f"  无效ID: {invalid} 题")
    print(f"  总计: {len(wrongs)} 题")

    # 6. 验证
    remaining = len(get_uncategorized_wrongs())
    print(f"\n剩余未分类: {remaining} 题")
    if remaining == 0:
        print("✅ 全部分类完成！请重启服务器。")
    else:
        print(f"⚠️ 还有 {remaining} 题未成功分类")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
