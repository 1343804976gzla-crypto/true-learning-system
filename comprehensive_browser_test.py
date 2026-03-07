"""
全面自动化测试 - 模拟浏览器完整流程
测试所有核心功能
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api"

print("="*70)
print("🧪 True Learning System - 全面自动化测试")
print("="*70)
print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("-"*70)

results = []


def _extract_chapters(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("chapters", [])
    return []

def test(name, func):
    """运行单个测试"""
    print(f"\n📋 {name}")
    print("-"*50)
    try:
        result = func()
        if result:
            print(f"  ✅ 通过")
            results.append((name, True, None))
            return True
        else:
            print(f"  ❌ 失败")
            results.append((name, False, "返回False"))
            return False
    except Exception as e:
        print(f"  ❌ 错误: {e}")
        results.append((name, False, str(e)))
        return False

# ==================== 测试1: 健康检查 ====================
def test_health():
    r = requests.get(f"{BASE_URL}/health", timeout=10)
    if r.status_code == 200:
        data = r.json()
        print(f"  状态: {data.get('status')}")
        return True
    return False

# ==================== 测试2: 首页访问 ====================
def test_homepage():
    r = requests.get(BASE_URL, timeout=10)
    if r.status_code == 200:
        print(f"  页面加载成功")
        return True
    return False

# ==================== 测试3: 上传页面 ====================
def test_upload_page():
    r = requests.get(f"{BASE_URL}/upload", timeout=10)
    if r.status_code == 200:
        print(f"  上传页面加载成功")
        return True
    return False

# ==================== 测试4: 历史页面 ====================
def test_history_page():
    r = requests.get(f"{BASE_URL}/history", timeout=10)
    if r.status_code == 200:
        print(f"  历史页面加载成功")
        return True
    return False

# ==================== 测试5: 错题本页面 ====================
def test_wrong_answers_page():
    r = requests.get(f"{BASE_URL}/wrong-answers", timeout=10)
    if r.status_code == 200:
        print(f"  错题本页面加载成功")
        return True
    return False

# ==================== 测试6: 知识图谱页面 ====================
def test_graph_page():
    r = requests.get(f"{BASE_URL}/graph", timeout=10)
    if r.status_code == 200:
        print(f"  知识图谱页面加载成功")
        return True
    return False

# ==================== 测试7: 获取章节列表 ====================
def test_get_chapters():
    r = requests.get(f"{API_URL}/chapters", timeout=10)
    if r.status_code == 200:
        data = r.json()
        chapters = _extract_chapters(data)
        print(f"  获取到 {len(chapters)} 个章节")
        if chapters:
            print(f"  示例: {chapters[0].get('title', 'N/A')}")
        return len(chapters) > 0
    return False

# ==================== 测试8: 获取仪表盘数据 ====================
def test_dashboard():
    r = requests.get(f"{API_URL}/dashboard", timeout=10)
    if r.status_code == 200:
        data = r.json()
        stats = data.get('stats', {})
        print(f"  总知识点: {stats.get('total_concepts', 0)}")
        print(f"  已掌握: {stats.get('mastered_concepts', 0)}")
        return True
    return False

# ==================== 测试9: 生成题目 ====================
def test_generate_quiz():
    print("  正在调用AI生成题目（可能需要10-30秒）...")
    # 使用第一个可用的知识点
    r = requests.get(f"{API_URL}/chapters", timeout=10)
    if r.status_code == 200:
        data = r.json()
        chapters = _extract_chapters(data)
        if chapters:
            chapter_id = chapters[0].get('id')
            
            # 开始测验
            r2 = requests.post(
                f"{API_URL}/quiz/start/{chapter_id}?mode=practice",
                timeout=60
            )
            if r2.status_code == 200:
                quiz_data = r2.json()
                session_id = quiz_data.get('session_id')
                questions = quiz_data.get('questions', [])
                print(f"  测验会话ID: {session_id}")
                print(f"  题目数量: {len(questions)}")
                if questions and questions[0].get('question'):
                    print(f"  第一题: {questions[0]['question'][:50]}...")
                return len(questions) == 10
    return False

# ==================== 测试10: 错题本API ====================
def test_wrong_answers_api():
    # 获取第一个章节ID
    r = requests.get(f"{API_URL}/chapters", timeout=10)
    if r.status_code == 200:
        data = r.json()
        chapters = _extract_chapters(data)
        if chapters:
            chapter_id = chapters[0].get('id', '').split('_')[0] + '_' + chapters[0].get('id', '').split('_')[1] if '_' in chapters[0].get('id', '') else chapters[0].get('id')
            
            r2 = requests.get(
                f"{API_URL}/quiz/wrong-answers/{chapter_id}?include_mastered=true",
                timeout=10
            )
            if r2.status_code == 200:
                data = r2.json()
                wrong_count = data.get('total', 0)
                print(f"  错题数量: {wrong_count}")
                return True
    return False

# ==================== 测试11: 测验统计API ====================
def test_quiz_stats():
    r = requests.get(f"{API_URL}/chapters", timeout=10)
    if r.status_code == 200:
        data = r.json()
        chapters = _extract_chapters(data)
        if chapters:
            chapter_id = chapters[0].get('id')
            
            r2 = requests.get(f"{API_URL}/quiz/stats/{chapter_id}", timeout=10)
            if r2.status_code == 200:
                data = r2.json()
                print(f"  测验次数: {data.get('total_sessions', 0)}")
                print(f"  待复习错题: {data.get('due_for_review', 0)}")
                return True
    return False

# ==================== 测试12: 上传历史API ====================
def test_upload_history():
    r = requests.get(f"{API_URL}/uploads", timeout=10)
    if r.status_code == 200:
        data = r.json()
        uploads = data.get('uploads', [])
        print(f"  上传记录数: {len(uploads)}")
        return True
    return False

# ==================== 运行所有测试 ====================
print("\n" + "="*70)
print("🔍 开始测试前端页面")
print("="*70)

test("健康检查", test_health)
test("首页访问", test_homepage)
test("上传页面", test_upload_page)
test("历史页面", test_history_page)
test("错题本页面", test_wrong_answers_page)
test("知识图谱页面", test_graph_page)

print("\n" + "="*70)
print("🔍 开始测试API接口")
print("="*70)

test("获取章节列表", test_get_chapters)
test("仪表盘数据", test_dashboard)
test("生成题目", test_generate_quiz)
test("错题本API", test_wrong_answers_api)
test("测验统计API", test_quiz_stats)
test("上传历史API", test_upload_history)

# ==================== 测试结果汇总 ====================
print("\n" + "="*70)
print("📊 测试结果汇总")
print("="*70)

passed = sum(1 for _, p, _ in results if p)
total = len(results)

print(f"\n总计: {passed}/{total} 通过 ({passed/total*100:.1f}%)")
print("\n详细结果:")

for name, passed, error in results:
    status = "✅ 通过" if passed else "❌ 失败"
    print(f"  {status} - {name}")
    if error:
        print(f"       错误: {error}")

if passed == total:
    print("\n🎉 所有测试通过！系统运行正常！")
else:
    print(f"\n⚠️  {total - passed} 个测试失败，需要检查")
