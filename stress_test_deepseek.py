"""
DeepSeek API 压力测试
"""
import asyncio
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request

BASE = "http://localhost:8000"

class StressTest:
    def __init__(self):
        self.results = []
    
    def log(self, msg):
        print(f"[TEST] {msg}")
        self.results.append(msg)
    
    def test_api(self, name, url, method="GET", data=None, timeout=30):
        start = time.time()
        try:
            req = urllib.request.Request(url, method=method)
            if data:
                req.data = json.dumps(data).encode('utf-8')
                req.add_header('Content-Type', 'application/json')
            resp = urllib.request.urlopen(req, timeout=timeout)
            duration = time.time() - start
            return {'name': name, 'status': resp.status, 'duration': duration, 'success': True}
        except Exception as e:
            duration = time.time() - start
            return {'name': name, 'status': 0, 'duration': duration, 'success': False, 'error': str(e)[:50]}
    
    def run_tests(self):
        self.log("=" * 60)
        self.log("DeepSeek API 压力测试")
        self.log("=" * 60)
        
        # 1. 基础API测试
        self.log("\n1. 基础API响应测试")
        apis = [
            (f"{BASE}/api/stats", "Stats"),
            (f"{BASE}/api/chapters", "Chapters"),
            (f"{BASE}/api/chapter/pathology_ch01", "Chapter Detail"),
        ]
        for url, name in apis:
            r = self.test_api(name, url)
            status = "✅" if r['success'] else "❌"
            self.log(f"{status} {name}: {r['duration']:.2f}s")
        
        # 2. 内容上传测试 (不同大小)
        self.log("\n2. 内容上传测试 (DeepSeek解析)")
        contents = [
            ("短内容 (500字)", "病理学第一章：细胞适应。讲解萎缩、肥大、增生、化生。" * 10),
            ("中内容 (2000字)", "病理学第一章：细胞适应。讲解萎缩、肥大、增生、化生。" * 40),
            ("长内容 (5000字)", "病理学第一章：细胞适应。讲解萎缩、肥大、增生、化生。" * 100),
        ]
        for label, content in contents:
            r = self.test_api(f"Upload {label}", f"{BASE}/api/upload", "POST", 
                            {"content": content, "date": "2026-02-18"}, 120)
            status = "✅" if r['success'] else "❌"
            size = len(content)
            self.log(f"{status} {label}: {r['duration']:.2f}s ({size} chars)")
        
        # 3. 批量出题测试
        self.log("\n3. 批量出题测试 (DeepSeek生成10题)")
        r = self.test_api("Generate 10 Questions", 
                         f"{BASE}/api/quiz/batch/generate/pathology_ch01", 
                         "POST", {}, 180)
        status = "✅" if r['success'] else "❌"
        self.log(f"{status} Generate 10 Questions: {r['duration']:.2f}s")
        
        # 4. 并发测试
        self.log("\n4. 并发测试 (10并发)")
        self.test_concurrent(10)
        
        self.log("\n" + "=" * 60)
        self.log("测试完成")
        self.log("=" * 60)
    
    def test_concurrent(self, n):
        url = f"{BASE}/api/stats"
        
        def make_request(i):
            start = time.time()
            try:
                urllib.request.urlopen(url, timeout=10)
                return i, time.time() - start, True
            except:
                return i, time.time() - start, False
        
        start_total = time.time()
        with ThreadPoolExecutor(max_workers=n) as ex:
            futures = [ex.submit(make_request, i) for i in range(n)]
            results = [f.result() for f in as_completed(futures)]
        
        total = time.time() - start_total
        success = sum(1 for r in results if r[2])
        avg = sum(r[1] for r in results) / len(results)
        
        self.log(f"  Total time: {total:.2f}s")
        self.log(f"  Success: {success}/{n} ({success*100//n}%)")
        self.log(f"  Avg response: {avg:.2f}s")

if __name__ == "__main__":
    test = StressTest()
    test.run_tests()
