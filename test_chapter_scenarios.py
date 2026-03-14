"""测试章节识别功能 - 多种场景"""
import asyncio
from services.content_parser import get_content_parser

async def test_multiple_scenarios():
    parser = get_content_parser()

    test_cases = [
        {
            "name": "标准格式 - 第X章",
            "content": """
            第六章 胃内消化

            胃液的分泌是消化系统的重要功能之一。
            """
        },
        {
            "name": "无章节号 - 只有标题",
            "content": """
            胃内消化

            胃液的分泌是消化系统的重要功能之一。壁细胞分泌盐酸。
            """
        },
        {
            "name": "长文本 - 章节信息在后面",
            "content": """
            今天我们来学习一个重要的内容。

            """ + "这是一些填充内容。" * 500 + """

            第十二章 肾脏的排泄功能

            肾脏是人体重要的排泄器官。
            """
        },
        {
            "name": "空内容",
            "content": ""
        },
        {
            "name": "无明确章节信息",
            "content": """
            今天讲一些医学知识。

            人体有很多器官，每个器官都有自己的功能。
            """
        }
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"测试 {i}: {test_case['name']}")
        print(f"内容长度: {len(test_case['content'])} 字符")
        print("-" * 60)

        try:
            result = await parser.parse_content(test_case['content'])

            print(f"✅ 识别成功")
            print(f"  科目: {result.get('book')}")
            print(f"  章节号: {result.get('chapter_number')}")
            print(f"  章节标题: {result.get('chapter_title')}")
            print(f"  章节ID: {result.get('chapter_id')}")

            if result.get('error'):
                print(f"  ⚠️ 错误: {result.get('error')}")

        except Exception as e:
            print(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_multiple_scenarios())
