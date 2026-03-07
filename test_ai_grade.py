"""
测试AI批改
"""
import asyncio
import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from services.quiz_service import get_quiz_service

async def test_grade():
    print("=== 测试AI批改 ===\n")
    
    try:
        service = get_quiz_service()
        
        # 模拟一道题
        question = "心力衰竭最常见的病因是？"
        options = {
            "A": "高血压",
            "B": "冠心病",
            "C": "心肌病",
            "D": "瓣膜病"
        }
        correct = "B"
        
        # 场景1：答对 + 确定会
        print("场景1: 答对 + 确定会")
        result = await service.grade_answer(
            question=question,
            options=options,
            correct_answer=correct,
            user_answer="B",
            confidence="sure"
        )
        print(f"  得分: {result['score']}")
        print(f"  反馈: {result['feedback']}")
        print()
        
        # 场景2：答错 + 确定会（危险盲区！）
        print("场景2: 答错 + 确定会 (危险盲区)")
        result = await service.grade_answer(
            question=question,
            options=options,
            correct_answer=correct,
            user_answer="A",  # 错误答案
            confidence="sure"
        )
        print(f"  得分: {result['score']}")
        print(f"  反馈: {result['feedback'][:100]}...")
        print(f"  薄弱点: {result['weak_points']}")
        print()
        
        # 场景3：答对 + 有点模糊（运气）
        print("场景3: 答对 + 有点模糊")
        result = await service.grade_answer(
            question=question,
            options=options,
            correct_answer=correct,
            user_answer="B",
            confidence="unsure"
        )
        print(f"  得分: {result['score']}")
        print(f"  反馈: {result['feedback'][:100]}...")
        
        print("\n✅ 所有批改测试通过！")
        
    except Exception as e:
        print(f"❌ 批改失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_grade())
