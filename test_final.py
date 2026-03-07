import urllib.request
import json
import sys

def test(url, name):
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url), timeout=5)
        data = resp.read().decode('utf-8')
        print(f'OK {name}: {resp.status}')
        return data
    except Exception as e:
        print(f'FAIL {name}: {e}')
        return None

BASE = 'http://localhost:8000'
print('='*50)
print('True Learning System - 完整测试')
print('='*50)

# 1. 首页
test(f'{BASE}/', '首页')

# 2. 统计
data = test(f'{BASE}/api/stats', 'API 统计')
if data:
    stats = json.loads(data)
    print(f'  知识点: {stats["total_concepts"]} | 章节: {stats["total_chapters"]}')

# 3. 章节列表
data = test(f'{BASE}/api/chapters', 'API 章节列表')
if data:
    chapters = json.loads(data)
    print(f'  章节数: {len(chapters)}')
    if chapters:
        print(f'  示例: {chapters[0]["book"]} - {chapters[0]["chapter_title"]}')

# 4. 章节详情
data = test(f'{BASE}/api/chapter/pathology_ch01', 'API 章节详情')
if data:
    detail = json.loads(data)
    ch = detail.get('chapter', {})
    print(f'  {ch.get("book")} - {ch.get("chapter_title")}')

# 5. 测验统计
data = test(f'{BASE}/api/quiz/stats/pathology_ch01', 'API 测验统计')

# 6. 错题本
data = test(f'{BASE}/api/quiz/wrong-answers/pathology_ch01', 'API 错题本')

# 7. 知识图谱
data = test(f'{BASE}/api/graph/concepts', 'API 知识图谱')

# 8. 页面
test(f'{BASE}/upload', '上传页面')
test(f'{BASE}/graph', '图谱页面')
test(f'{BASE}/chapter/pathology_ch01', '章节页面')

print('='*50)
print('测试完成')
