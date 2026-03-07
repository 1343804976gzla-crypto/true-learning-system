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
print('True Learning System - Test')
print('='*50)

# 1. Home
test(f'{BASE}/', 'Home')

# 2. Stats
data = test(f'{BASE}/api/stats', 'API Stats')
if data:
    stats = json.loads(data)
    print(f'  Concepts: {stats.get("total_concepts")} | Chapters: {stats.get("total_chapters")}')

# 3. Chapters
data = test(f'{BASE}/api/chapters', 'API Chapters')
if data:
    chapters = json.loads(data)
    print(f'  Count: {len(chapters)}')
    if chapters:
        print(f'  Sample: {chapters[0].get("book")} - {chapters[0].get("chapter_title")}')

# 4. Chapter Detail
data = test(f'{BASE}/api/chapter/pathology_ch01', 'API Chapter Detail')
if data:
    detail = json.loads(data)
    ch = detail.get('chapter', {})
    print(f'  Book: {ch.get("book")} | Title: {ch.get("chapter_title")}')

# 5. Quiz Stats
data = test(f'{BASE}/api/quiz/stats/pathology_ch01', 'API Quiz Stats')

# 6. Wrong Answers
data = test(f'{BASE}/api/quiz/wrong-answers/pathology_ch01', 'API Wrong Answers')
if data:
    wrong = json.loads(data)
    print(f'  Wrong count: {wrong.get("total")}')

# 7. Graph
data = test(f'{BASE}/api/graph/concepts', 'API Graph Concepts')
if data:
    concepts = json.loads(data)
    print(f'  Concepts: {len(concepts)}')

data = test(f'{BASE}/api/graph/links', 'API Graph Links')
if data:
    links = json.loads(data)
    print(f'  Links: {len(links)}')

# 8. Pages
test(f'{BASE}/upload', 'Page Upload')
test(f'{BASE}/graph', 'Page Graph')
test(f'{BASE}/chapter/pathology_ch01', 'Page Chapter')
test(f'{BASE}/quiz/pathology_ch01_01_', 'Page Quiz')

print('='*50)
print('Test Complete')
