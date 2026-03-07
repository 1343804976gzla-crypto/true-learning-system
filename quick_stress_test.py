import urllib.request
import json
import time

BASE = "http://localhost:8000"

def test(name, url, method="GET", data=None, timeout=30):
    start = time.time()
    try:
        req = urllib.request.Request(url, method=method)
        if data:
            req.data = json.dumps(data).encode('utf-8')
            req.add_header('Content-Type', 'application/json')
        resp = urllib.request.urlopen(req, timeout=timeout)
        duration = time.time() - start
        print(f"✅ {name}: {duration:.2f}s (Status: {resp.status})")
        return True
    except Exception as e:
        duration = time.time() - start
        print(f"❌ {name}: {duration:.2f}s - {str(e)[:50]}")
        return False

print("=" * 60)
print("快速压力测试")
print("=" * 60)

# 1. 基础API
print("\n1. 基础API测试")
test("API Stats", f"{BASE}/api/stats")
test("API Chapters", f"{BASE}/api/chapters")
test("Homepage", f"{BASE}/")

# 2. 上传测试
print("\n2. 上传内容测试")
short = "病理学第一章：细胞和组织的适应。"
medium = short * 100
test("Upload Short (500字)", f"{BASE}/api/upload", "POST", {"content": short, "date": "2026-02-18"}, 60)
test("Upload Medium (4000字)", f"{BASE}/api/upload", "POST", {"content": medium, "date": "2026-02-18"}, 120)

# 3. 批量出题
print("\n3. 批量出题测试")
test("Generate 10 Questions", f"{BASE}/api/quiz/batch/generate/pathology_ch01", "POST", {}, 120)

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
