"""
压力测试脚本 - True Learning System
测试内容：
1. API响应时间
2. 大内容上传
3. 批量出题
4. 并发请求
"""

import asyncio
import time
import urllib.request
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "http://localhost:8000"

# 测试数据
SHORT_CONTENT = "病理学第一章：细胞和组织的适应与损伤。讲解萎缩、肥大、增生、化生。"
MEDIUM_CONTENT = SHORT_CONTENT * 100  # ~4400字
LONG_CONTENT = SHORT_CONTENT * 300    # ~13000字

class StressTester:
    def __init__(self):
        self.results = []
    
    def log(self, message):
        print(f"[TEST] {message}")
        self.results.append(message)
    
    def test_endpoint(self, url, name, method="GET", data=None, timeout=30):
        """测试单个端点"""
        start = time.time()
        try:
            req = urllib.request.Request(
                url, 
                data=json.dumps(data).encode('utf-8') if data else None,
                headers={'Content-Type': 'application/json'} if data else {},
                method=method
            )
            resp = urllib.request.urlopen(req, timeout=timeout)
            duration = time.time() - start
            return {
                'name': name,
                'status': resp.status,
                'duration': duration,
                'success': True,
                'size': len(resp.read()) if method == "GET" else 0
            }
        except Exception as e:
            duration = time.time() - start
            return {
                'name': name,
                'status': 0,
                'duration': duration,
                'success': False,
                'error': str(e)
            }
    
    def test_basic_apis(self):
        """测试基础API"""
        self.log("=" * 60)
        self.log("基础API响应测试")
        self.log("=" * 60)
        
        endpoints = [
            (f"{BASE_URL}/api/stats", "API Stats"),
            (f"{BASE_URL}/api/chapters", "API Chapters"),
            (f"{BASE_URL}/api/chapter/pathology_ch01", "API Chapter Detail"),
            (f"{BASE_URL}/", "Homepage"),
        ]
        
        for url, name in endpoints:
            result = self.test_endpoint(url, name)
            status = "✅" if result['success'] else "❌"
            self.log(f"{status} {name}: {result['duration']:.2f}s (Status: {result['status']})")
    
    def test_upload_content(self, content, label):
        """测试内容上传"""
        url = f"{BASE_URL}/api/upload"
        data = {"content": content, "date": "2026-02-18"}
        
        start = time.time()
        result = self.test_endpoint(url, f"Upload {label}", method="POST", data=data, timeout=180)
        duration = time.time() - start
        
        status = "✅" if result['success'] else "❌"
        content_size = len(content)
        self.log(f"{status} Upload {label} ({content_size} chars): {duration:.2f}s")
        return result
    
    def test_upload_sizes(self):
        """测试不同大小内容上传"""
        self.log("")
        self.log("=" * 60)
        self.log("上传内容大小测试")
        self.log("=" * 60)
        
        sizes = [
            (SHORT_CONTENT, "Short (~500字)"),
            (MEDIUM_CONTENT, "Medium (~4000字)"),
            (LONG_CONTENT, "Long (~13000字)"),
        ]
        
        for content, label in sizes:
            self.test_upload_content(content, label)
    
    def test_batch_quiz(self):
        """测试批量出题"""
        self.log("")
        self.log("=" * 60)
        self.log("批量出题测试 (10道题)")
        self.log("=" * 60)
        
        # 生成试卷
        url = f"{BASE_URL}/api/quiz/batch/generate/pathology_ch01"
        result = self.test_endpoint(url, "Generate 10 Questions", method="POST", data={}, timeout=120)
        
        if result['success']:
            self.log(f"✅ Generate 10 Questions: {result['duration']:.2f}s")
            
            # 解析session_id
            try:
                req = urllib.request.Request(url, method="POST", 
                    data=json.dumps({}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'})
                resp = urllib.request.urlopen(req, timeout=120)
                data = json.loads(resp.read().decode('utf-8'))
                session_id = data.get('session_id')
                
                if session_id:
                    # 提交答案
                    answers = ["A", "B", "C", "D", "E", "AB", "AC", "AD", "AE", "BC"]
                    submit_url = f"{BASE_URL}/api/quiz/batch/submit/{session_id}"
                    submit_result = self.test_endpoint(
                        submit_url, "Submit Answers", 
                        method="POST", data=answers, timeout=30
                    )
                    
                    status = "✅" if submit_result['success'] else "❌"
                    self.log(f"{status} Submit Answers: {submit_result['duration']:.2f}s")
            except Exception as e:
                self.log(f"❌ Quiz flow failed: {e}")
        else:
            self.log(f"❌ Generate 10 Questions: {result['duration']:.2f}s - {result.get('error', 'Unknown')}")
    
    def test_concurrent(self, concurrency=5):
        """并发测试"""
        self.log("")
        self.log("=" * 60)
        self.log(f"并发测试 (并发数: {concurrency})")
        self.log("=" * 60)
        
        url = f"{BASE_URL}/api/stats"
        
        def make_request(i):
            start = time.time()
            try:
                resp = urllib.request.urlopen(url, timeout=10)
                return i, time.time() - start, resp.status, True
            except Exception as e:
                return i, time.time() - start, 0, False
        
        start_total = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(make_request, i) for i in range(concurrency)]
            
            results = []
            for future in as_completed(futures):
                i, duration, status, success = future.result()
                results.append((i, duration, status, success))
        
        total_time = time.time() - start_total
        success_count = sum(1 for r in results if r[3])
        avg_time = sum(r[1] for r in results) / len(results)
        
        self.log(f"Total time: {total_time:.2f}s")
        self.log(f"Success rate: {success_count}/{concurrency} ({success_count*100//concurrency}%)")
        self.log(f"Avg response time: {avg_time:.2f}s")
        self.log(f"Max response time: {max(r[1] for r in results):.2f}s")
    
    def run_all_tests(self):
        """运行所有测试"""
        self.log("开始压力测试...")
        self.log(f"Base URL: {BASE_URL}")
        self.log("")
        
        # 1. 基础API测试
        self.test_basic_apis()
        
        # 2. 不同大小内容上传
        self.test_upload_sizes()
        
        # 3. 批量出题
        self.test_batch_quiz()
        
        # 4. 并发测试
        self.test_concurrent(concurrency=5)
        
        # 总结
        self.print_summary()
    
    def print_summary(self):
        """打印测试总结"""
        self.log("")
        self.log("=" * 60)
        self.log("测试总结")
        self.log("=" * 60)
        
        for result in self.results:
            print(result)


if __name__ == "__main__":
    tester = StressTester()
    tester.run_all_tests()
