"""
测试AI内容解析
"""
import asyncio
import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from services.content_parser import get_content_parser

async def test_parse():
    print("=== 测试AI内容解析 ===\n")
    
    # 测试内容 - 心力衰竭
    content = """
    今天讲心力衰竭。心力衰竭是各种心脏结构或功能性疾病导致心室充盈和/或射血功能受损，
    心排血量不能满足机体组织代谢需要，以肺循环和/或体循环淤血，器官、组织血液灌注不足
    为临床表现的一组综合征。
    
    心衰的分类：
    1. 按部位分：左心衰、右心衰、全心衰
    2. 按速度分：急性心衰、慢性心衰
    3. 按射血分数分：射血分数降低型（HFrEF）、射血分数保留型（HFpEF）
    
    慢性心衰的病因主要是冠心病、高血压、瓣膜病等。
    临床表现包括呼吸困难、乏力、液体潴留等。
    """
    
    try:
        parser = get_content_parser()
        result = await parser.parse_content(content)
        
        print("✅ 解析成功！\n")
        print(f"📚 教材: {result['book']}")
        print(f"📖 章节: {result['chapter_title']}")
        print(f"🔢 章节号: {result['chapter_number']}")
        print(f"🆔 章节ID: {result['chapter_id']}")
        print(f"\n📝 摘要: {result['summary']}")
        print(f"\n📌 知识点 ({len(result['concepts'])}个):")
        for i, concept in enumerate(result['concepts'], 1):
            print(f"  {i}. {concept['name']} ({concept['id']})")
        
    except Exception as e:
        print(f"❌ 解析失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_parse())
