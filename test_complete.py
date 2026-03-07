import requests

BASE_URL = "http://localhost:8000"

print("测试完成会话（带调试）...")

# 先开始一个会话
response = requests.post(
    f"{BASE_URL}/api/tracking/session/start",
    json={
        "session_type": "exam",
        "chapter_id": "test_chapter",
        "title": "测试整卷"
    },
    timeout=5
)

session_id = response.json().get("session_id")
print(f"会话ID: {session_id}")

# 完成会话
try:
    response = requests.post(
        f"{BASE_URL}/api/tracking/session/{session_id}/complete",
        json={
            "score": 80,
            "total_questions": 10
        },
        timeout=5
    )
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.text[:500]}")
except Exception as e:
    print(f"错误: {e}")
