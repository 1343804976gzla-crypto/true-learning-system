import urllib.request
import json

def test(url, name):
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url), timeout=5)
        data = resp.read().decode('utf-8')
        print('OK %s: %d' % (name, resp.status))
        return data
    except Exception as e:
        print('FAIL %s: %s' % (name, e))
        return None

BASE = 'http://localhost:8000'
print('='*50)
print('True Learning System - Test')
print('='*50)

# 1. Home
test(BASE + '/', 'Home')

# 2. Stats
data = test(BASE + '/api/stats', 'API Stats')
if data:
    stats = json.loads(data)
    print('  Concepts: %d | Chapters: %d' % (stats.get('total_concepts'), stats.get('total_chapters')))

# 3. Chapters
data = test(BASE + '/api/chapters', 'API Chapters')
if data:
    chapters = json.loads(data)
    print('  Count: %d' % len(chapters))
    if chapters:
        c = chapters[0]
        print('  Sample: %s - %s' % (c.get('book'), c.get('chapter_title')))

# 4. Chapter Detail
data = test(BASE + '/api/chapter/pathology_ch01', 'API Chapter Detail')
if data:
    detail = json.loads(data)
    ch = detail.get('chapter', {})
    print('  Book: %s | Title: %s' % (ch.get('book'), ch.get('chapter_title')))

# 5. Quiz Stats
data = test(BASE + '/api/quiz/stats/pathology_ch01', 'API Quiz Stats')

# 6. Wrong Answers
data = test(BASE + '/api/quiz/wrong-answers/pathology_ch01', 'API Wrong Answers')
if data:
    wrong = json.loads(data)
    print('  Wrong count: %d' % wrong.get('total', 0))

# 7. Graph
data = test(BASE + '/api/graph/concepts', 'API Graph Concepts')
if data:
    concepts = json.loads(data)
    print('  Concepts: %d' % len(concepts))

data = test(BASE + '/api/graph/links', 'API Graph Links')
if data:
    links = json.loads(data)
    print('  Links: %d' % len(links))

# 8. Pages
test(BASE + '/upload', 'Page Upload')
test(BASE + '/graph', 'Page Graph')
test(BASE + '/chapter/pathology_ch01', 'Page Chapter')
test(BASE + '/quiz/pathology_ch01_01_', 'Page Quiz')

# 9. Start Quiz (POST)
try:
    req = urllib.request.Request(BASE + '/api/quiz/start/pathology_ch01', method='POST')
    req.add_header('Content-Type', 'application/json')
    resp = urllib.request.urlopen(req, timeout=5)
    print('OK API Quiz Start: %d' % resp.status)
    quiz_data = json.loads(resp.read().decode('utf-8'))
    print('  Session ID: %d | Questions: %d' % (quiz_data.get('session_id'), len(quiz_data.get('questions', []))))
except Exception as e:
    print('FAIL API Quiz Start: %s' % e)

print('='*50)
print('Test Complete')
