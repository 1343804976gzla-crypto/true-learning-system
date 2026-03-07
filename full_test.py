import requests
import json
import time

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api"

print("="*70)
print("True Learning System - 全面测试报告")
print("="*70)

results = []


def _extract_chapters(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("chapters", [])
    return []

def test(name, func):
    """运行单个测试"""
    print(f"\n[TEST] {name}")
    try:
        result = func()
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
        results.append((name, result, None))
        return result
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        results.append((name, False, str(e)))
        return False

# ==================== 测试1: 基础功能 ====================
def test_health():
    r = requests.get(f"{BASE_URL}/health", timeout=5)
    return r.status_code == 200 and r.json().get('status') == 'healthy'

def test_homepage():
    pages = ['/', '/upload', '/history', '/wrong-answers', '/graph']
    for path in pages:
        r = requests.get(f"{BASE_URL}{path}", timeout=5)
        if r.status_code != 200:
            print(f"    Page {path}: {r.status_code}")
            return False
    print(f"    All {len(pages)} pages OK")
    return True

def test_api_chapters():
    r = requests.get(f"{API_URL}/chapters", timeout=5)
    if r.status_code == 200:
        data = r.json()
        chapters = _extract_chapters(data)
        print(f"    Chapters: {len(chapters)}")
        return len(chapters) > 0
    return False

def test_api_dashboard():
    r = requests.get(f"{API_URL}/dashboard", timeout=5)
    if r.status_code == 200:
        stats = r.json().get('stats', {})
        print(f"    Total concepts: {stats.get('total_concepts', 0)}")
        return True
    return False

# ==================== 测试2: 错题本功能 ====================
def test_wrong_answers_api():
    # 先获取章节
    r = requests.get(f"{API_URL}/chapters", timeout=5)
    if r.status_code != 200:
        return False
    
    chapters = _extract_chapters(r.json())
    if not chapters:
        print("    No chapters found")
        return False
    
    chapter_id = chapters[0].get('id', '')
    prefix = '_'.join(chapter_id.split('_')[:2]) if '_' in chapter_id else chapter_id
    
    r2 = requests.get(f"{API_URL}/quiz/wrong-answers/{prefix}?include_mastered=true", timeout=5)
    if r2.status_code == 200:
        data = r2.json()
        print(f"    Wrong answers: {data.get('total', 0)}")
        return True
    return False

def test_quiz_stats_api():
    r = requests.get(f"{API_URL}/chapters", timeout=5)
    if r.status_code != 200:
        return False
    
    chapters = _extract_chapters(r.json())
    if not chapters:
        return False
    
    chapter_id = chapters[0].get('id', '')
    r2 = requests.get(f"{API_URL}/quiz/stats/{chapter_id}", timeout=5)
    if r2.status_code == 200:
        data = r2.json()
        print(f"    Sessions: {data.get('total_sessions', 0)}, Due: {data.get('due_for_review', 0)}")
        return True
    return False

# ==================== 测试3: 测验功能 ====================
def test_quiz_generation():
    print("    Getting chapter...")
    r = requests.get(f"{API_URL}/chapters", timeout=5)
    if r.status_code != 200:
        return False
    
    chapters = _extract_chapters(r.json())
    if not chapters:
        return False
    
    chapter_id = chapters[0].get('id')
    print(f"    Chapter: {chapter_id}")
    print("    Generating quiz (this may take 10-30s)...")
    
    start = time.time()
    r2 = requests.post(f"{API_URL}/quiz/start/{chapter_id}?mode=practice", timeout=60)
    elapsed = time.time() - start
    
    if r2.status_code == 200:
        data = r2.json()
        session_id = data.get('session_id')
        questions = data.get('questions', [])
        print(f"    Session ID: {session_id}")
        print(f"    Questions: {len(questions)}")
        print(f"    Time: {elapsed:.1f}s")
        
        if questions and len(questions) == 10:
            q = questions[0]
            print(f"    Q1: {q.get('question', '')[:50]}...")
            return True
    else:
        print(f"    Error: {r2.status_code}")
        print(f"    Response: {r2.text[:200]}")
    return False

# ==================== 运行所有测试 ====================
print("\n" + "="*70)
print("PHASE 1: 基础功能测试")
print("="*70)

test("Health Check", test_health)
test("Page Access", test_homepage)
test("API - Chapters", test_api_chapters)
test("API - Dashboard", test_api_dashboard)

print("\n" + "="*70)
print("PHASE 2: 错题本功能测试")
print("="*70)

test("Wrong Answers API", test_wrong_answers_api)
test("Quiz Stats API", test_quiz_stats_api)

print("\n" + "="*70)
print("PHASE 3: 测验功能测试 (AI生成)")
print("="*70)

test("Quiz Generation", test_quiz_generation)

# ==================== 汇总 ====================
print("\n" + "="*70)
print("TEST SUMMARY")
print("="*70)

passed = sum(1 for _, p, _ in results if p)
total = len(results)

print(f"\nTotal: {passed}/{total} passed ({passed/total*100:.1f}%)")
print("\nDetails:")
for name, passed, error in results:
    status = "[PASS]" if passed else "[FAIL]"
    print(f"  {status} {name}")
    if error:
        print(f"         Error: {error}")

if passed == total:
    print("\n" + "="*70)
    print("ALL TESTS PASSED!")
    print("="*70)
else:
    print(f"\n{total - passed} test(s) failed.")
