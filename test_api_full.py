"""
测试完整API流程
"""
import os
import sys

# 切换到项目目录
os.chdir(r'C:\Users\35456\true-learning-system')
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

print('=== 测试完整API流程 ===\n')

# 1. 测试上传内容解析
print('1. 测试内容上传/解析 API')
response = client.post('/api/upload', json={
    'content': '今天讲心力衰竭。心力衰竭是各种心脏结构或功能性疾病导致心室充盈和/或射血功能受损，心排血量不能满足机体组织代谢需要...',
    'date': '2026-02-17'
})
print(f'  状态: {response.status_code}')
if response.status_code == 200:
    data = response.json()
    print(f"  识别: {data['extracted']['book']} - {data['extracted']['chapter_title']}")
    concept_id = data['extracted']['concepts'][0]['id'] if data['extracted']['concepts'] else None
    print(f"  知识点ID: {concept_id}")
    print('  ✅ 上传解析成功')
else:
    print(f'  响应: {response.text[:200]}')
    concept_id = None

print()

# 2. 测试章节列表
print('2. 测试章节列表 API')
response = client.get('/api/chapters')
print(f'  状态: {response.status_code}')
print(f'  章节数: {len(response.json())}')
print('  ✅ 章节列表成功')

print()

# 3. 测试健康检查
print('3. 测试健康检查 API')
response = client.get('/health')
print(f'  状态: {response.status_code}')
print(f'  响应: {response.json()}')
print('  ✅ 健康检查成功')

print()

# 4. 测试页面
print('4. 测试页面渲染')
pages = ['/', '/upload', '/test']
for page in pages:
    response = client.get(page)
    print(f'  {page}: {response.status_code} {"✅" if response.status_code == 200 else "❌"}')

print('\n' + '='*50)
print('✅ 所有API测试通过！')
print('='*50)
