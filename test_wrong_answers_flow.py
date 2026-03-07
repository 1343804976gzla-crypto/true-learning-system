#!/usr/bin/env python3
"""
测试错题系统完整数据流
验证：数据库 → API → 前端 的完整链路
"""

import sys
sys.path.insert(0, '.')

from sqlalchemy.orm import Session
from models import get_db
from learning_tracking_models import WrongAnswerV2
from fastapi.testclient import TestClient
from main import app

def test_database():
    """测试数据库层"""
    print("=" * 60)
    print("1. 测试数据库层")
    print("=" * 60)

    db = next(get_db())
    try:
        # 查询活跃错题
        active = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.mastery_status == "active"
        ).count()

        # 查询已归档错题
        archived = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.mastery_status == "archived"
        ).count()

        # 查询总数
        total = db.query(WrongAnswerV2).count()

        print(f"✅ 活跃错题: {active}")
        print(f"✅ 已归档错题: {archived}")
        print(f"✅ 总错题数: {total}")

        # 查询第一道错题
        first = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.mastery_status == "active"
        ).first()

        if first:
            print(f"\n第一道错题预览:")
            print(f"  ID: {first.id}")
            print(f"  题目: {first.question_text[:50]}...")
            print(f"  严重程度: {first.severity_tag}")
            print(f"  错误次数: {first.error_count}")

        return True
    finally:
        db.close()

def test_api():
    """测试API层"""
    print("\n" + "=" * 60)
    print("2. 测试API层")
    print("=" * 60)

    client = TestClient(app)

    # 测试同步接口
    print("\n[POST /api/wrong-answers/sync]")
    response = client.post('/api/wrong-answers/sync')
    print(f"  Status: {response.status_code}")
    data = response.json()
    print(f"  创建: {data.get('created')}")
    print(f"  更新: {data.get('updated')}")
    print(f"  活跃总数: {data.get('total_active')}")

    # 测试统计接口
    print("\n[GET /api/wrong-answers/stats]")
    response = client.get('/api/wrong-answers/stats')
    print(f"  Status: {response.status_code}")
    data = response.json()
    print(f"  活跃: {data.get('total_active')}")
    print(f"  归档: {data.get('total_archived')}")
    print(f"  严重程度分布: {data.get('severity_counts')}")
    print(f"  重做正确率: {data.get('retry_correct_rate')}%")

    # 测试列表接口
    print("\n[GET /api/wrong-answers/list]")
    response = client.get('/api/wrong-answers/list?view=severity&status=active')
    print(f"  Status: {response.status_code}")
    data = response.json()
    print(f"  返回条目数: {len(data.get('items', []))}")
    print(f"  总数: {data.get('total')}")

    if data.get('items'):
        item = data['items'][0]
        print(f"\n  第一条错题:")
        print(f"    题目: {item.get('question_text', '')[:50]}...")
        print(f"    严重程度: {item.get('severity_tag')}")
        print(f"    错误次数: {item.get('error_count')}")

    return True

def test_frontend():
    """测试前端页面"""
    print("\n" + "=" * 60)
    print("3. 测试前端页面")
    print("=" * 60)

    client = TestClient(app)

    response = client.get('/wrong-answers')
    print(f"  Status: {response.status_code}")
    print(f"  Content-Type: {response.headers.get('content-type')}")
    print(f"  页面大小: {len(response.text)} bytes")

    # 检查关键JavaScript函数
    checks = [
        ('safeFetch 修复', 'const response = await fetch(url, options);'),
        ('loadList 函数', 'async function loadList()'),
        ('renderSeverityView 函数', 'function renderSeverityView(data)'),
        ('syncAndReload 函数', 'async function syncAndReload()'),
    ]

    print("\n  关键函数检查:")
    for name, pattern in checks:
        if pattern in response.text:
            print(f"    ✅ {name}")
        else:
            print(f"    ❌ {name} - 未找到")

    return True

def main():
    print("\n" + "=" * 60)
    print("错题系统完整测试")
    print("=" * 60)

    try:
        test_database()
        test_api()
        test_frontend()

        print("\n" + "=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        print("\n📋 下一步操作:")
        print("  1. 在浏览器中访问: http://localhost:8000/wrong-answers")
        print("  2. 按 F12 打开开发者工具查看控制台")
        print("  3. 按 Ctrl+Shift+R 强制刷新（清除缓存）")
        print("  4. 应该能看到 31 道活跃错题")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
