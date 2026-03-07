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
        return {"ok": True, "time": time.time() - start, "status": resp.status}
    except Exception as e:
        return {"ok": False, "time": time.time() - start, "error": str(e)[:50]}

print("=" * 60)
print("DeepSeek 快速压力测试")
print("=" * 60)

# 1. 基础API
print("\n1. 基础API测试")
for endpoint, name in [("/api/stats", "Stats"), ("/api/chapters", "Chapters")]:
    r = test(name, BASE + endpoint)
    print(f"{'✅' if r['ok'] else '❌'} {name}: {r['time']:.2f}s")

# 2. 短内容上传 (DeepSeek)
print("\n2. 内容上传测试 (DeepSeek解析)")
content = "病理学第一章：细胞适应。讲解萎缩、肥大、增生、化生。"
r = test("Upload 500 chars", BASE + "/api/upload", "POST", 
         {"content": content, "date": "2026-02-18"}, 60)
print(f"{'✅' if r['ok'] else '❌'} Upload 500 chars: {r['time']:.2f}s")

# 3. 批量出题 (DeepSeek生成10题)
print("\n3. 批量出题测试 (DeepSeek生成10题)")
print("Generating 10 questions with DeepSeek (this may take 30-60s)...")
r = test("Generate 10 Questions", BASE + "/api/quiz/batch/generate/pathology_ch01", 
         "POST", {}, 120)
print(f"{'✅' if r['ok'] else '❌'} Generate 10 Questions: {r['time']:.2f}s")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
