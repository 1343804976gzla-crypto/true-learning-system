"""
快速测试题目生成功能
"""
import asyncio
import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from services.quiz_service import get_quiz_service

async def test():
    print("🧪 测试题目生成...")
    print("-" * 50)
    
    try:
        service = get_quiz_service()
        result = await service.generate_quiz('胃液的成分')
        
        print("✅ 成功!")
        print(f"题目: {result['question'][:60]}...")
        print(f"选项: {result.get('options', {})}")
        print(f"答案: {result.get('correct_answer')}")
        print(f"解析: {result.get('explanation', '')[:50]}...")
        
    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
