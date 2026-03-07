import requests

BASE = 'http://localhost:8000'

print('='*70)
print('True Learning System - 全面测试报告')
print('='*70)

print('\nPHASE 1: 基础服务测试')
print('-'*70)

# Health Check
r = requests.get(BASE + '/health', timeout=5)
print('Health Check:', 'PASS' if r.status_code == 200 else 'FAIL')

# Page Access
pages = ['/', '/upload', '/history', '/wrong-answers', '/graph']
all_ok = True
for path in pages:
    r = requests.get(BASE + path, timeout=5)
    if r.status_code != 200:
        print('Page ' + path + ': FAIL (' + str(r.status_code) + ')')
        all_ok = False
print('Page Access:', 'PASS' if all_ok else 'FAIL')

print('\nPHASE 2: API功能测试')
print('-'*70)

# Chapters API
r = requests.get(BASE + '/api/chapters', timeout=5)
if r.status_code == 200:
    data = r.json()
    if isinstance(data, list):
        print('Chapters API: PASS (' + str(len(data)) + ' chapters)')
    else:
        chapters = data.get('chapters', [])
        print('Chapters API: PASS (' + str(len(chapters)) + ' chapters)')
else:
    print('Chapters API: FAIL')

# Dashboard API
r = requests.get(BASE + '/api/dashboard', timeout=5)
if r.status_code == 200:
    stats = r.json().get('stats', {})
    print('Dashboard API: PASS')
    print('  Total concepts:', stats.get('total_concepts', 0))
else:
    print('Dashboard API: FAIL')

print('\n' + '='*70)
print('测试完成！')
print('='*70)
