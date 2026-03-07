"""
测试数据看板 API
"""

import requests
import json

BASE_URL = "http://localhost:8000"

def test_dashboard_stats():
    """测试数据看板统计接口"""
    print("=" * 60)
    print("测试数据看板 API")
    print("=" * 60)

    # 测试不同的每日计划复习量
    test_cases = [10, 20, 30, 50]

    for daily_plan in test_cases:
        print(f"\n📊 测试每日计划复习量: {daily_plan} 题/天")
        print("-" * 60)

        try:
            response = requests.get(
                f"{BASE_URL}/api/dashboard/stats",
                params={"daily_planned_review": daily_plan},
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()

                print(f"✅ API 调用成功")
                print(f"\n核心指标:")
                print(f"  - 今日消除量: {data['today_eliminated']} 题")
                print(f"  - 今日重做量: {data['today_retried']} 题")
                print(f"  - 7天日均新增: {data['avg_new_per_day']} 题/天")
                print(f"  - 当前错题积压: {data['current_backlog']} 题")
                print(f"  - 预计清仓天数: {data['estimated_days_to_clear']}")
                print(f"  - 每日需做错题: {data['daily_required_reviews']} 题")
                print(f"  - 净日均进度: {data['net_daily_progress']} 题/天")
                print(f"  - 是否可清仓: {'✅ 是' if data['can_clear'] else '❌ 否'}")
                print(f"  - 清仓提示: {data['clear_message']}")

                print(f"\n严重度分布:")
                for severity, count in data['severity_counts'].items():
                    emoji = {
                        'critical': '🚨',
                        'stubborn': '🚑',
                        'landmine': '⚠️',
                        'normal': '📋'
                    }.get(severity, '❓')
                    print(f"  {emoji} {severity}: {count} 题")

                print(f"\n本周趋势 (最近3天):")
                for day in data['weekly_trend'][-3:]:
                    net_symbol = '+' if day['net'] > 0 else ''
                    print(f"  {day['date']}: 新增 {day['new']}, 消除 {day['eliminated']}, 净变化 {net_symbol}{day['net']}")

            else:
                print(f"❌ API 调用失败: HTTP {response.status_code}")
                print(f"响应内容: {response.text}")

        except requests.exceptions.ConnectionError:
            print(f"❌ 连接失败: 服务器未启动或地址错误")
            print(f"请确保服务器运行在 {BASE_URL}")
            break
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            break

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


def test_dashboard_page():
    """测试数据看板页面"""
    print("\n📄 测试数据看板页面")
    print("-" * 60)

    try:
        response = requests.get(f"{BASE_URL}/dashboard/stats", timeout=10)

        if response.status_code == 200:
            print(f"✅ 页面访问成功")
            print(f"页面大小: {len(response.text)} 字节")

            # 检查关键元素
            if "数据看板" in response.text:
                print(f"✅ 页面标题正确")
            if "今日消除量" in response.text:
                print(f"✅ 包含核心指标")
            if "Neumorphism" in response.text or "neuro-card" in response.text:
                print(f"✅ 包含 Neumorphism 样式")
        else:
            print(f"❌ 页面访问失败: HTTP {response.status_code}")

    except Exception as e:
        print(f"❌ 测试失败: {e}")


if __name__ == "__main__":
    print("\n🚀 开始测试数据看板功能\n")

    # 测试 API
    test_dashboard_stats()

    # 测试页面
    test_dashboard_page()

    print("\n✨ 所有测试完成！")
    print("\n访问地址:")
    print(f"  - 数据看板页面: {BASE_URL}/dashboard/stats")
    print(f"  - API 接口: {BASE_URL}/api/dashboard/stats?daily_planned_review=20")
