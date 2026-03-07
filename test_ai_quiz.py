"""
测试AI出题
"""
import asyncio
import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from services.quiz_service import get_quiz_service

async def test_quiz():
    print("=== 测试AI出题 ===\n")
    
    try:
        service = get_quiz_service()
        
        # 测试生成题目
        concept_name = "心力衰竭的定义和分类"
        print(f"知识点: {concept_name}")
        print("正在生成题目...\n")
        
        quiz = await service.generate_quiz(concept_name)
        
        print("✅ 出题成功！\n")
        print(f"❓ 题目: {quiz['question']}\n")
        print("📋 选项:")
        for option, text in quiz['options'].items():
            print(f"  {option}. {text}")
        print(f"\n✅ 正确答案: {quiz['correct_answer']}")
        print(f"\n💡 解析:\n{quiz['explanation'][:300]}...")
        
    except Exception as e:
        print(f"❌ 出题失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_quiz())
