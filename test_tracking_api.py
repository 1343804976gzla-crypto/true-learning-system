import requests
import json

BASE_URL = "http://localhost:8000"

print("=" * 60)
print("测试学习轨迹追踪API")
print("=" * 60)

# 1. 测试开始会话
print("\n1. 测试开始会话...")
try:
    response = requests.post(
        f"{BASE_URL}/api/tracking/session/start",
        json={
            "session_type": "exam",
            "chapter_id": "test_chapter",
            "title": "测试整卷",
            "uploaded_content": "测试内容"
        },
        timeout=5
    )
    print(f"   状态码: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        session_id = data.get("session_id")
        print(f"   会话ID: {session_id}")
    else:
        print(f"   错误: {response.text}")
        session_id = None
except Exception as e:
    print(f"   请求失败: {e}")
    session_id = None

if session_id:
    # 2. 测试记录题目
    print("\n2. 测试记录题目...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/tracking/session/{session_id}/question",
            json={
                "question_index": 0,
                "question_type": "A1",
                "difficulty": "基础",
                "question_text": "测试题目",
                "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D", "E": "选项E"},
                "correct_answer": "A",
                "user_answer": "B",
                "is_correct": False,
                "confidence": "unsure",
                "key_point": "测试知识点"
            },
            timeout=5
        )
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            print(f"   成功: {response.json()}")
        else:
            print(f"   错误: {response.text}")
    except Exception as e:
        print(f"   请求失败: {e}")
    
    # 3. 测试完成会话
    print("\n3. 测试完成会话...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/tracking/session/{session_id}/complete",
            json={
                "score": 80,
                "total_questions": 10
            },
            timeout=5
        )
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            print(f"   成功: {response.json()}")
        else:
            print(f"   错误: {response.text}")
    except Exception as e:
        print(f"   请求失败: {e}")

# 4. 测试获取会话列表
print("\n4. 测试获取会话列表...")
try:
    response = requests.get(f"{BASE_URL}/api/tracking/sessions", timeout=5)
    print(f"   状态码: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   总会话数: {data.get('total', 0)}")
        print(f"   会话列表: {len(data.get('sessions', []))} 条")
    else:
        print(f"   错误: {response.text}")
except Exception as e:
    print(f"   请求失败: {e}")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
