"""
测试HTTP响应内容
"""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

print('=== 测试首页响应 ===')
r = client.get('/')
print(f'状态码: {r.status_code}')
print(f'Content-Type: {r.headers.get("content-type")}')
print(f'内容长度: {len(r.text)} 字符')
print()
print('=== 前500字符 ===')
print(r.text[:500])
print()
print('=== 是否包含关键元素 ===')
print(f'<html>: {"<html" in r.text}')
print(f'<body>: {"<body" in r.text}')
print(f'导航栏: {"仪表盘" in r.text}')
print(f'统计卡片: {"总知识点" in r.text}')
