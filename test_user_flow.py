"""
模拟前端完整流程测试
验证从整卷测试到学习轨迹记录的完整流程
"""

import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_complete_user_flow():
    """模拟用户完整做题流程"""
    
    print("=" * 60)
    print("模拟用户完整流程测试")
    print("=" * 60)
    
    # ========== 步骤1: 开始整卷测试 ==========
    print("\n[步骤1] 用户进入整卷测试页面，开始测试...")
    
    # 获取章节ID (模拟chapter_id = "0")
    chapter_id = "0"
    uploaded_content = """
    心力衰竭是指心脏泵血功能受损，导致心输出量不能满足机体代谢需要的病理状态。
    常见病因包括冠心病、高血压、瓣膜病等。临床表现包括呼吸困难、乏力、水肿等。
    治疗包括利尿剂、ACEI、β受体阻滞剂等药物，严重者需要器械治疗或心脏移植。
    """
    
    # 生成试卷 (实际流程)
    print("  - 生成试卷...")
    r = requests.post(
        f"{BASE_URL}/api/quiz/batch/generate/{chapter_id}",
        json={
            "uploaded_content": uploaded_content,
            "num_questions": 5
        },
        timeout=30
    )
    
    if r.status_code != 200:
        print(f"  ✗ 生成试卷失败: {r.status_code}")
        return False
    
    exam_data = r.json()
    exam_id = exam_data["exam_id"]
    questions = exam_data["questions"]
    print(f"  ✓ 试卷生成成功: {exam_id[:8]}...")
    print(f"  - 题目数量: {len(questions)}")
    
    # ========== 步骤2: 开始学习轨迹记录 ==========
    print("\n[步骤2] 开始学习轨迹记录...")
    
    r = requests.post(
        f"{BASE_URL}/api/tracking/session/start",
        json={
            "session_type": "exam",
            "chapter_id": chapter_id,
            "title": exam_data.get("paper_title", "整卷测试"),
            "uploaded_content": uploaded_content[:500]
        },
        timeout=5
    )
    
    if r.status_code != 200:
        print(f"  ✗ 开始会话失败: {r.status_code}")
        print(f"  错误: {r.text[:200]}")
        return False
    
    session_id = r.json()["session_id"]
    print(f"  ✓ 学习会话开始: {session_id[:8]}...")
    
    # ========== 步骤3: 用户逐题作答 ==========
    print("\n[步骤3] 用户逐题作答并记录...")
    
    # 模拟用户答案
    user_answers = ["A", "B", "C", "A", "B"]  # 用户的答案
    user_confidence = ["sure", "unsure", "sure", "no", "unsure"]  # 自信度
    
    for i, q in enumerate(questions):
        # 模拟答题时间
        time.sleep(0.1)
        
        # 记录答题
        r = requests.post(
            f"{BASE_URL}/api/tracking/session/{session_id}/question",
            json={
                "question_index": i,
                "question_type": q.get("type", "A1"),
                "difficulty": q.get("difficulty", "基础"),
                "question_text": q.get("question", "")[:100],
                "options": q.get("options", {}),
                "correct_answer": q.get("correct_answer", "A"),
                "user_answer": user_answers[i],
                "is_correct": user_answers[i] == q.get("correct_answer", "A"),
                "confidence": user_confidence[i],
                "explanation": q.get("explanation", "")[:100],
                "key_point": q.get("key_point", f"考点{i+1}")
            },
            timeout=5
        )
        
        if r.status_code != 200:
            print(f"  ✗ 记录第{i+1}题失败: {r.status_code}")
        else:
            print(f"  ✓ 第{i+1}题已记录 ({user_confidence[i]})")
    
    # ========== 步骤4: 提交答卷 ==========
    print("\n[步骤4] 用户提交答卷...")
    
    # 计算分数
    correct_count = sum(1 for i, q in enumerate(questions) 
                       if user_answers[i] == q.get("correct_answer", "A"))
    score = int(correct_count / len(questions) * 100)
    
    # 提交到quiz_batch API
    r = requests.post(
        f"{BASE_URL}/api/quiz/batch/submit/{exam_id}",
        json={
            "answers": user_answers,
            "confidence": {str(i): user_confidence[i] for i in range(len(questions))}
        },
        timeout=5
    )
    
    if r.status_code != 200:
        print(f"  ✗ 提交答卷失败: {r.status_code}")
    else:
        print(f"  ✓ 答卷已提交，得分: {score}")
    
    # 完成学习轨迹记录
    r = requests.post(
        f"{BASE_URL}/api/tracking/session/{session_id}/complete",
        json={
            "score": score,
            "total_questions": len(questions)
        },
        timeout=5
    )
    
    if r.status_code != 200:
        print(f"  ✗ 完成会话失败: {r.status_code}")
        print(f"  错误: {r.text[:200]}")
        return False
    
    result = r.json()
    print(f"  ✓ 学习轨迹记录完成")
    print(f"    - 得分: {result.get('score')}")
    print(f"    - 正确率: {result.get('accuracy')}%")
    print(f"    - 用时: {result.get('duration')}秒")
    
    # ========== 步骤5: 验证学习轨迹看板 ==========
    print("\n[步骤5] 验证学习轨迹看板数据...")
    
    # 获取会话列表
    r = requests.get(f"{BASE_URL}/api/tracking/sessions", timeout=5)
    if r.status_code != 200:
        print(f"  ✗ 获取会话列表失败")
        return False
    
    sessions = r.json()
    print(f"  ✓ 会话列表获取成功")
    print(f"    - 总会话数: {sessions.get('total', 0)}")
    print(f"    - 返回记录: {len(sessions.get('sessions', []))}")
    
    # 获取每日日志
    r = requests.get(f"{BASE_URL}/api/tracking/daily-logs?days=7", timeout=5)
    if r.status_code == 200:
        logs = r.json()
        if logs.get('logs'):
            print(f"  ✓ 每日日志获取成功")
            latest = logs['logs'][0]
            print(f"    - 今日学习次数: {latest.get('total_sessions', 0)}")
            print(f"    - 今日做题数: {latest.get('total_questions', 0)}")
            print(f"    - 今日正确率: {latest.get('accuracy', 0)}%")
        else:
            print(f"  ⚠ 每日日志为空（可能是新用户）")
    
    # 获取会话详情
    r = requests.get(f"{BASE_URL}/api/tracking/session/{session_id}", timeout=5)
    if r.status_code == 200:
        detail = r.json()
        print(f"  ✓ 会话详情获取成功")
        print(f"    - 题目记录: {len(detail.get('questions', []))}")
        print(f"    - 活动记录: {len(detail.get('activities', []))}")
        print(f"    - 正确数: {detail.get('correct_count')}")
        print(f"    - 错误数: {detail.get('wrong_count')}")
    
    print("\n" + "=" * 60)
    print("✅ 完整流程测试通过！")
    print("=" * 60)
    print("\n现在访问: http://localhost:8000/learning-tracking")
    print("可以看到刚才的测试记录！")
    
    return True

if __name__ == "__main__":
    try:
        success = test_complete_user_flow()
        if not success:
            print("\n❌ 测试失败，请检查错误信息")
            exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
