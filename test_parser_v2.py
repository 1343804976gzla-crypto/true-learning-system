from services.content_parser_v2 import ContentParser
import asyncio

async def test():
    parser = ContentParser()
    
    # 测试短内容
    short_content = """
    小肠内物质的吸收及其机制。主要讲解吸收部位和吸收途径，包括水、钠离子、铁、钙的吸收机制。
    重点：小肠是主要吸收部位，糖、蛋白质、脂肪在十二指肠和空肠吸收，胆盐和维生素B12在回肠吸收。
    """
    
    print("=== 测试短内容 ===")
    result = await parser.parse_content_with_knowledge(short_content)
    print(f"Book: {result['book']}")
    print(f"Chapter: {result['chapter_title']}")
    print(f"Concepts: {len(result['concepts'])}")
    
    # 测试中等长度内容
    medium_content = short_content * 50  # 约2500字
    
    print("\n=== 测试中等内容 ===")
    result = await parser.parse_content_with_knowledge(medium_content)
    print(f"Book: {result['book']}")
    print(f"Chapter: {result['chapter_title']}")
    print(f"Concepts: {len(result['concepts'])}")
    
    print("\n=== 测试完成 ===")

asyncio.run(test())
