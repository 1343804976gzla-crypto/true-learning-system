import requests
import sys

BASE_URL = "http://localhost:8000"

print("快速测试学习轨迹API...")

# 测试1: 开始会话
print("\n1. 开始会话...")
r = requests.post(f'{BASE_URL}/api/tracking/session/start', 
    json={'session_type': 'exam', 'chapter_id': '0', 'title': '测试'}, 
    timeout=5)
print(f"   Status: {r.status_code}")
if r.status_code != 200:
    print(f"   Error: {r.text[:100]}")
    sys.exit(1)
session_id = r.json()['session_id']
print(f"   Session: {session_id[:8]}")

# 测试2: 记录题目
print("\n2. 记录题目...")
r = requests.post(f'{BASE_URL}/api/tracking/session/{session_id}/question',
    json={
        'question_index': 0, 
        'question_type': 'A1', 
        'difficulty': '基础',
        'question_text': '测试题目内容',
        'options': {'A': '选项A', 'B': '选项B', 'C': '选项C', 'D': '选项D', 'E': '选项E'},
        'correct_answer': 'A',
        'user_answer': 'B',
        'is_correct': False,
        'confidence': 'sure',
        'key_point': '测试知识点'
    },
    timeout=5)
print(f"   Status: {r.status_code}")

# 测试3: 完成会话
print("\n3. 完成会话...")
r = requests.post(f'{BASE_URL}/api/tracking/session/{session_id}/complete',
    json={'score': 80, 'total_questions': 1},
    timeout=5)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    print(f"   Result: {r.json()}")
else:
    print(f"   Error: {r.text[:100]}")

# 测试4: 获取列表
print("\n4. 获取会话列表...")
r = requests.get(f'{BASE_URL}/api/tracking/sessions', timeout=5)
print(f"   Status: {r.status_code}")
data = r.json()
print(f"   Total sessions: {data.get('total', 0)}")

print("\n✅ API测试完成")
