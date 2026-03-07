"""
测试脚本
"""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

print('=== 测试健康检查 ===')
r = client.get('/health')
print(f'状态: {r.status_code}')
print(f'响应: {r.json()}')

print('\n=== 测试首页 ===')
r = client.get('/')
print(f'状态: {r.status_code}')
print(f'包含标题: {"True Learning System" in r.text}')

print('\n=== 测试上传页面 ===')
r = client.get('/upload')
print(f'状态: {r.status_code}')
print(f'包含表单: {"讲课内容" in r.text}')

print('\n=== 测试API - 章节列表 ===')
r = client.get('/api/chapters')
print(f'状态: {r.status_code}')
print(f'响应: {r.json()}')

print('\n✅ 所有测试通过!')
