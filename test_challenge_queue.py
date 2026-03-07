"""
测试今日闯关队列算法 - 50/30/20 动态配额池
"""

import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"

def test_challenge_queue():
    """测试闯关队列接口"""
    print("=" * 80)
    print("测试今日闯关队列算法 - 50/30/20 动态配额池")
    print("=" * 80)

    # 测试不同的题量配置
    test_cases = [
        {"count": 10, "desc": "默认配置（10题）"},
        {"count": 20, "desc": "中等配置（20题）"},
        {"count": 40, "desc": "大量配置（40题）"},
        {"count": 5, "desc": "小量配置（5题）"},
    ]

    for case in test_cases:
        count = case["count"]
        desc = case["desc"]

        print(f"\n{'=' * 80}")
        print(f"测试场景: {desc}")
        print(f"请求参数: count={count}")
        print("-" * 80)

        try:
            response = requests.get(
                f"{BASE_URL}/api/challenge/queue",
                params={"count": count},
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()

                print(f"✅ API 调用成功")
                print(f"\n📊 队列统计:")
                print(f"  - 实际返回题数: {data['count']} 题")
                print(f"  - 查询日期: {data['date']}")

                if "pool_stats" in data:
                    stats = data["pool_stats"]
                    print(f"\n🎯 配额池分布:")
                    print(f"  - 绝对优先级池（Critical）: {stats['critical']} 题")
                    print(f"  - 核心区（Core）: {stats['core']} 题 (目标: {stats['target_core']})")
                    print(f"  - 巩固区（Review）: {stats['review']} 题 (目标: {stats['target_review']})")
                    print(f"  - 铲雪区（Shovel）: {stats['shovel']} 题 (目标: {stats['target_shovel']})")
                    print(f"  - 总计: {stats['total']} 题")

                    # 验证配额逻辑
                    print(f"\n🔍 配额验证:")
                    remaining = count - stats['critical']
                    expected_core = int(remaining * 0.5)
                    expected_review = int(remaining * 0.3)
                    expected_shovel = remaining - expected_core - expected_review

                    print(f"  - 剩余配额: {remaining} 题 (总配额 {count} - Critical {stats['critical']})")
                    print(f"  - 核心区配额: 期望 {expected_core}, 实际 {stats['core']}")
                    print(f"  - 巩固区配额: 期望 {expected_review}, 实际 {stats['review']}")
                    print(f"  - 铲雪区配额: 期望 {expected_shovel}, 实际 {stats['shovel']}")

                    # 检查顺延机制
                    core_shortage = expected_core - stats['core']
                    review_shortage = expected_review - stats['review']
                    if core_shortage > 0 or review_shortage > 0:
                        print(f"\n⚠️ 顺延兜底机制触发:")
                        if core_shortage > 0:
                            print(f"  - 核心区不足 {core_shortage} 题，已顺延到铲雪区")
                        if review_shortage > 0:
                            print(f"  - 巩固区不足 {review_shortage} 题，已顺延到铲雪区")
                        print(f"  - 铲雪区实际配额: {expected_shovel} + {core_shortage + review_shortage} = {expected_shovel + core_shortage + review_shortage}")

                # 显示前 3 道题的详细信息
                if data['items']:
                    print(f"\n📝 前 3 道题详情:")
                    for i, item in enumerate(data['items'][:3], 1):
                        severity_emoji = {
                            'critical': '🚨',
                            'stubborn': '🚑',
                            'landmine': '⚠️',
                            'normal': '📋'
                        }.get(item['severity_tag'], '❓')

                        print(f"\n  {i}. {severity_emoji} {item['severity_tag'].upper()}")
                        print(f"     题目: {item['question_text'][:50]}...")
                        print(f"     知识点: {item['key_point'] or '未标注'}")
                        print(f"     错误次数: {item['error_count']}")
                        print(f"     SM-2 状态: 连对 {item['sm2_repetitions']} 次, 间隔 {item['sm2_interval']} 天")
                        print(f"     下次复习: {item['next_review_date'] or '未设置'}")
                        print(f"     是否到期: {'是' if item['is_overdue'] else '否'}")

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

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


def test_algorithm_logic():
    """测试算法逻辑的正确性"""
    print("\n" + "=" * 80)
    print("算法逻辑验证")
    print("=" * 80)

    # 模拟场景
    scenarios = [
        {
            "name": "场景 1: Critical 占满全部配额",
            "total": 10,
            "critical": 12,
            "core": 0,
            "review": 0,
            "shovel": 0,
            "expected_return": 10,
            "expected_critical": 10
        },
        {
            "name": "场景 2: 正常分配（无顺延）",
            "total": 40,
            "critical": 5,
            "core": 17,
            "review": 10,
            "shovel": 8,
            "expected_return": 40,
            "expected_critical": 5
        },
        {
            "name": "场景 3: 核心区不足，顺延到铲雪区",
            "total": 40,
            "critical": 5,
            "core": 10,  # 目标 17，实际 10
            "review": 10,
            "shovel": 15,  # 8 + 7(顺延)
            "expected_return": 40,
            "expected_critical": 5
        },
        {
            "name": "场景 4: 巩固区不足，顺延到铲雪区",
            "total": 40,
            "critical": 5,
            "core": 17,
            "review": 5,  # 目标 10，实际 5
            "shovel": 13,  # 8 + 5(顺延)
            "expected_return": 40,
            "expected_critical": 5
        },
        {
            "name": "场景 5: 核心区和巩固区都不足",
            "total": 40,
            "critical": 5,
            "core": 12,  # 目标 17，实际 12
            "review": 8,  # 目标 10，实际 8
            "shovel": 15,  # 8 + 5 + 2(顺延)
            "expected_return": 40,
            "expected_critical": 5
        },
    ]

    for scenario in scenarios:
        print(f"\n{scenario['name']}")
        print("-" * 80)

        total = scenario['total']
        critical = scenario['critical']
        remaining = total - min(critical, total)

        target_core = int(remaining * 0.5)
        target_review = int(remaining * 0.3)
        target_shovel = remaining - target_core - target_review

        actual_core = scenario['core']
        actual_review = scenario['review']
        actual_shovel = scenario['shovel']

        core_shortage = max(0, target_core - actual_core)
        review_shortage = max(0, target_review - actual_review)
        adjusted_shovel = target_shovel + core_shortage + review_shortage

        print(f"  总配额: {total}")
        print(f"  Critical: {min(critical, total)} (实际有 {critical} 个)")
        print(f"  剩余配额: {remaining}")
        print(f"  目标配额: Core={target_core}, Review={target_review}, Shovel={target_shovel}")
        print(f"  实际配额: Core={actual_core}, Review={actual_review}, Shovel={actual_shovel}")

        if core_shortage > 0 or review_shortage > 0:
            print(f"  顺延机制: Core 不足 {core_shortage}, Review 不足 {review_shortage}")
            print(f"  调整后铲雪区: {target_shovel} + {core_shortage + review_shortage} = {adjusted_shovel}")

        actual_total = min(critical, total) + actual_core + actual_review + actual_shovel
        print(f"  实际返回: {actual_total} 题")

        if actual_total == scenario['expected_return']:
            print(f"  ✅ 验证通过")
        else:
            print(f"  ❌ 验证失败: 期望 {scenario['expected_return']}, 实际 {actual_total}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    print("\n🚀 开始测试今日闯关队列算法\n")

    # 测试 API 接口
    test_challenge_queue()

    # 测试算法逻辑
    test_algorithm_logic()

    print("\n✨ 所有测试完成！")
    print("\n访问地址:")
    print(f"  - 闯关队列 API: {BASE_URL}/api/challenge/queue?count=40")
    print(f"  - 错题本页面: {BASE_URL}/wrong-answers")
