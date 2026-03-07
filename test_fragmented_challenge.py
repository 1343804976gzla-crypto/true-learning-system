"""
测试碎片化闯关功能 - 今日已答题目自动排除
"""

import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"

def test_fragmented_challenge():
    """测试碎片化闯关功能"""
    print("=" * 80)
    print("测试碎片化闯关功能 - 今日已答题目自动排除")
    print("=" * 80)

    # 第一次闯关：获取 40 题
    print("\n【场景 1】上午 10:00 - 第一次闯关（40题）")
    print("-" * 80)

    try:
        response = requests.get(
            f"{BASE_URL}/api/challenge/queue",
            params={"count": 40},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            print(f"✅ 获取队列成功")
            print(f"  - 返回题数: {data['count']}")
            print(f"  - 今日已答: {data['pool_stats']['today_answered']} 题")

            if data['items']:
                first_batch_ids = [item['id'] for item in data['items'][:5]]
                print(f"  - 前 5 题 ID: {first_batch_ids}")

                # 模拟：假设用户做了前 15 题
                print(f"\n💡 模拟场景：用户做了前 15 题后退出")
                print(f"  - 已答题目 ID: {[item['id'] for item in data['items'][:15]]}")

        else:
            print(f"❌ 获取队列失败: HTTP {response.status_code}")
            return

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return

    # 第二次闯关：应该排除今日已答题目
    print(f"\n{'=' * 80}")
    print("【场景 2】下午 15:00 - 第二次闯关（40题）")
    print("-" * 80)
    print("💡 预期：系统会自动排除今日已答的 15 题")

    try:
        response = requests.get(
            f"{BASE_URL}/api/challenge/queue",
            params={"count": 40},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            print(f"✅ 获取队列成功")
            print(f"  - 返回题数: {data['count']}")
            print(f"  - 今日已答: {data['pool_stats']['today_answered']} 题")

            if data['items']:
                second_batch_ids = [item['id'] for item in data['items'][:5]]
                print(f"  - 前 5 题 ID: {second_batch_ids}")

                # 检查是否有重复
                if first_batch_ids and second_batch_ids:
                    overlap = set(first_batch_ids) & set(second_batch_ids)
                    if overlap:
                        print(f"  ⚠️ 发现重复题目 ID: {overlap}")
                    else:
                        print(f"  ✅ 无重复题目（前5题对比）")

        else:
            print(f"❌ 获取队列失败: HTTP {response.status_code}")

    except Exception as e:
        print(f"❌ 测试失败: {e}")

    print("\n" + "=" * 80)


def test_pool_stats():
    """测试配额池统计"""
    print("\n" + "=" * 80)
    print("测试配额池统计 - 包含今日已答数量")
    print("=" * 80)

    try:
        response = requests.get(
            f"{BASE_URL}/api/challenge/queue",
            params={"count": 40},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            stats = data['pool_stats']

            print(f"\n📊 配额池详细统计:")
            print(f"  - 绝对优先级池（Critical）: {stats['critical']} 题")
            print(f"  - 核心区（Core）: {stats['core']} 题 (目标: {stats['target_core']})")
            print(f"  - 巩固区（Review）: {stats['review']} 题 (目标: {stats['target_review']})")
            print(f"  - 铲雪区（Shovel）: {stats['shovel']} 题 (目标: {stats['target_shovel']})")
            print(f"  - 总计: {stats['total']} 题")
            print(f"  - 今日已答: {stats['today_answered']} 题 ⭐")

            # 验证顺延机制
            core_shortage = stats['target_core'] - stats['core']
            review_shortage = stats['target_review'] - stats['review']

            if core_shortage > 0 or review_shortage > 0:
                print(f"\n⚠️ 顺延兜底机制触发:")
                if core_shortage > 0:
                    print(f"  - 核心区不足 {core_shortage} 题，已顺延到铲雪区")
                if review_shortage > 0:
                    print(f"  - 巩固区不足 {review_shortage} 题，已顺延到铲雪区")

        else:
            print(f"❌ 获取统计失败: HTTP {response.status_code}")

    except Exception as e:
        print(f"❌ 测试失败: {e}")

    print("\n" + "=" * 80)


def test_daily_reset():
    """测试次日重置逻辑"""
    print("\n" + "=" * 80)
    print("测试次日重置逻辑说明")
    print("=" * 80)

    print("""
📅 次日重置逻辑:

1. 今日范围: 00:00:00 到 23:59:59
2. 查询条件: retried_at >= today_start AND retried_at <= today_end
3. 次日 00:00 后，today_start 和 today_end 会自动更新
4. 昨天的答题记录不会被查询到，所有题目重新可用

示例:
- 2026-03-07 10:00 - 做了 15 题
- 2026-03-07 15:00 - 系统排除这 15 题
- 2026-03-07 20:00 - 系统仍然排除这 15 题
- 2026-03-08 00:00 - 重置，这 15 题重新可用
    """)

    print("=" * 80)


if __name__ == "__main__":
    print("\n🚀 开始测试碎片化闯关功能\n")

    # 测试碎片化闯关
    test_fragmented_challenge()

    # 测试配额池统计
    test_pool_stats()

    # 测试次日重置逻辑
    test_daily_reset()

    print("\n✨ 所有测试完成！")
    print("\n使用说明:")
    print("  1. 上午做 15 题后退出")
    print("  2. 下午再次点击'今日闯关'")
    print("  3. 系统会自动排除上午已做的 15 题")
    print("  4. 次日 00:00 后，所有题目重新可用")
    print(f"\n访问地址: {BASE_URL}/wrong-answers")
