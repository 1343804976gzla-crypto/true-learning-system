"""
测试费曼讲解
"""
import asyncio
import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from services.feynman_service import get_feynman_service

async def test_feynman():
    print("=== 测试费曼讲解 ===\n")
    
    try:
        service = get_feynman_service()
        
        # 开始会话
        concept_id = "medicine.ch3-2.HF_Definition"
        concept_name = "心力衰竭定义"
        
        print(f"知识点: {concept_name}\n")
        print("启动费曼讲解会话...")
        
        result = await service.start_session(concept_id, concept_name)
        print(f"✅ 会话ID: {result['session_id']}")
        print(f"🤖 AI: {result['message']}\n")
        
        # 模拟用户回复
        user_response = "心力衰竭就是心脏的泵血功能不行了，血打不出去，身体各个地方就缺血缺氧。"
        print(f"👤 用户: {user_response}\n")
        
        result2 = await service.process_response(
            session_id=result['session_id'],
            user_message=user_response
        )
        
        print(f"🤖 AI: {result2['message']}")
        print(f"📝 轮次: {result2['round']}")
        print(f"✅ 完成: {result2['finished']}")
        print(f"✅ 通过: {result2['passed']}")
        
        if result2['terminology_detected']:
            print(f"⚠️ 检测到的术语: {result2['terminology_detected']}")
        
        print("\n✅ 费曼讲解测试通过！")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_feynman())
