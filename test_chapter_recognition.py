"""测试章节识别功能"""
import asyncio
from services.content_parser import get_content_parser

async def test_chapter_recognition():
    parser = get_content_parser()

    # 测试内容
    test_content = """
    第六章 胃内消化

    胃液的分泌是消化系统的重要功能之一。胃液主要由胃黏膜的壁细胞、主细胞和黏液细胞分泌。

    壁细胞分泌盐酸，主细胞分泌胃蛋白酶原，黏液细胞分泌黏液和碳酸氢盐。

    胃液的分泌受神经和体液因素的调节。
    """

    print("开始测试章节识别...")
    print(f"测试内容长度: {len(test_content)} 字符")
    print("-" * 50)

    try:
        result = await parser.parse_content(test_content)

        print("识别结果:")
        print(f"科目: {result.get('book')}")
        print(f"章节号: {result.get('chapter_number')}")
        print(f"章节标题: {result.get('chapter_title')}")
        print(f"章节ID: {result.get('chapter_id')}")
        print(f"知识点数量: {len(result.get('concepts', []))}")
        print(f"摘要: {result.get('summary', '')[:100]}...")

        if result.get('error'):
            print(f"\n错误信息: {result.get('error')}")

    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_chapter_recognition())
