import urllib.request
import json

# 测试上传
url = 'http://localhost:8000/api/upload'
data = json.dumps({
    'content': '病理学第一章：细胞和组织的适应。主要讲解萎缩、肥大、增生、化生。',
    'date': '2026-02-18'
}).encode('utf-8')

req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})

try:
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read().decode('utf-8'))
    print('Status:', resp.status)
    print('Book:', result.get('extracted', {}).get('book'))
    print('Chapter:', result.get('extracted', {}).get('chapter_title'))
    print('Concepts:', len(result.get('extracted', {}).get('concepts', [])))
    print('Message:', result.get('message'))
except Exception as e:
    print('Error:', e)
