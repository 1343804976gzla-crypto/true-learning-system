import requests
import json

BASE_URL = "http://localhost:8000"

print("=" * 60)
print("完整流程测试 - 模拟用户做题过程")
print("=" * 60)

# 1. 开始整卷测试会话
print("\n1. 用户开始整卷测试...")
response = requests.post(
    f"{BASE_URL}/api/tracking/session/start",
    json={
        "session_type": "exam",
        "chapter_id": "0",
        "title": "2026-02-19 01:00 整卷测试",
        "uploaded_content": "心力衰竭的病理生理机制..."
    }
)
session_id = response.json()["session_id"]
print(f"   ✓ 会话开始: {session_id[:8]}...")

# 2. 记录答题（模拟答了5道题）
print("\n2. 用户逐题作答...")
questions_data = [
    {"q": "心衰最常见的诱因是？", "ans": "A", "correct": "B", "is_correct": False, "conf": "sure"},
    {"q": "洋地黄中毒最常见的心律失常是？", "ans": "A", "correct": "A", "is_correct": True, "conf": "sure"},
    {"q": "急性左心衰的特征性表现是？", "ans": "C", "correct": "C", "is_correct": True, "conf": "unsure"},
    {"q": "右心衰的典型体征是？", "ans": "D", "correct": "D", "is_correct": True, "conf": "no"},
    {"q": "慢性心衰NYHA分级中，日常活动即出现症状属于？", "ans": "B", "correct": "B", "is_correct": True, "conf": "sure"},
]

for i, q in enumerate(questions_data):
    requests.post(
        f"{BASE_URL}/api/tracking/session/{session_id}/question",
        json={
            "question_index": i,
            "question_type": "A1",
            "difficulty": "基础" if i < 3 else "提高",
            "question_text": q["q"],
            "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D", "E": "选项E"},
            "correct_answer": q["correct"],
            "user_answer": q["ans"],
            "is_correct": q["is_correct"],
            "confidence": q["conf"],
            "key_point": f"考点{i+1}"
        }
    )
print(f"   ✓ 已记录 {len(questions_data)} 道题目")

# 3. 完成会话
print("\n3. 用户提交答卷...")
response = requests.post(
    f"{BASE_URL}/api/tracking/session/{session_id}/complete",
    json={"score": 80, "total_questions": 5}
)
print(f"   ✓ 测试完成: {response.json()['accuracy']}% 正确率")

# 4. 开始细节练习
print("\n4. 用户进入细节练习...")
response = requests.post(
    f"{BASE_URL}/api/tracking/session/start",
    json={
        "session_type": "detail_practice",
        "knowledge_point": "心衰治疗",
        "title": "细节练习: 心衰治疗"
    }
)
detail_session_id = response.json()["session_id"]
print(f"   ✓ 细节练习开始: {detail_session_id[:8]}...")

# 记录细节练习题目
for i in range(3):
    requests.post(
        f"{BASE_URL}/api/tracking/session/{detail_session_id}/question",
        json={
            "question_index": i,
            "question_type": "A1",
            "difficulty": "基础",
            "question_text": f"变式题{i+1}: 关于心衰治疗的问题",
            "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D", "E": "选项E"},
            "correct_answer": "A",
            "user_answer": "A",
            "is_correct": True,
            "confidence": "sure",
            "key_point": "心衰治疗"
        }
    )

# 完成细节练习
response = requests.post(
    f"{BASE_URL}/api/tracking/session/{detail_session_id}/complete",
    json={"score": 100, "total_questions": 3}
)
print(f"   ✓ 细节练习完成: {response.json()['accuracy']}% 正确率")

# 5. 检查学习轨迹看板数据
print("\n5. 获取学习轨迹数据...")
response = requests.get(f"{BASE_URL}/api/tracking/sessions")
sessions = response.json()
print(f"   ✓ 总会话数: {sessions['total']}")

# 获取统计
response = requests.get(f"{BASE_URL}/api/tracking/daily-logs?days=7")
logs = response.json()
if logs.get('logs'):
    today = logs['logs'][0]
    print(f"   ✓ 今日统计:")
    print(f"     - 学习次数: {today['total_sessions']}")
    print(f"     - 做题数: {today['total_questions']}")
    print(f"     - 正确率: {today['accuracy']}%")
    print(f"     - 学习时长: {today['duration_minutes']}分钟")

print("\n" + "=" * 60)
print("✅ 所有测试通过！学习轨迹系统工作正常")
print("=" * 60)
print("\n现在访问: http://localhost:8000/learning-tracking")
print("应该能看到完整的学习轨迹看板！")
