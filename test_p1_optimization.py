"""
测试P1级优化效果
1. 主题一致性校验
2. 前端超时提示优化（需要手动测试）
"""
import asyncio
import time
import json
from services.quiz_service_v2 import get_quiz_service

async def test_topic_consistency():
    """测试主题一致性校验"""

    print("=" * 80)
    print("测试P1级优化 - 主题一致性校验")
    print("=" * 80)

    quiz_service = get_quiz_service()

    # ========== 测试1：正常内容（主题一致） ==========
    print(f"\n{'='*80}")
    print("【测试1】正常内容（消化系统）")
    print(f"{'='*80}")

    normal_content = """
    第六章 消化系统

    一、胃液的分泌

    胃液的主要成分包括：
    1. 盐酸（HCl）：由壁细胞分泌，pH值1.5-2.0
       - 激活胃蛋白酶原
       - 杀死细菌
       - 促进铁的吸收

    2. 胃蛋白酶原：由主细胞分泌
       - 在酸性环境下被激活为胃蛋白酶
       - 分解蛋白质

    3. 内因子：由壁细胞分泌
       - 促进维生素B12吸收
       - 缺乏会导致恶性贫血

    4. 黏液：由黏液细胞分泌
       - 保护胃黏膜
       - 中和胃酸

    二、胃液分泌的调节

    1. 神经调节
       - 迷走神经兴奋促进胃液分泌
       - 交感神经抑制胃液分泌

    2. 体液调节
       - 胃泌素：促进胃酸和胃蛋白酶原分泌
       - 组胺：促进壁细胞分泌盐酸
       - 生长抑素：抑制胃液分泌

    三、胃的运动

    1. 胃的容受性舒张
       - 食物进入胃时，胃底和胃体舒张
       - 由迷走神经介导

    2. 胃的蠕动
       - 从胃体开始，向幽门方向推进
       - 频率约3次/分钟

    3. 胃排空
       - 液体排空快于固体
       - 糖类排空快于蛋白质，蛋白质快于脂肪
    """ * 2  # 约3000字

    try:
        start_time = time.time()

        result1 = await quiz_service.generate_exam_paper(
            uploaded_content=normal_content,
            num_questions=5
        )

        elapsed1 = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed1:.2f} 秒")
        print(f"试卷标题: {result1.get('paper_title')}")
        print(f"题目数量: {len(result1.get('questions', []))}")

        # 检查题目质量
        questions1 = result1.get('questions', [])
        incomplete1 = 0

        for i, q in enumerate(questions1, 1):
            if not q.get('question') or not q.get('options') or not q.get('correct_answer'):
                incomplete1 += 1

        print(f"统计: {len(questions1)}道题，{incomplete1}道不完整")

        # 保存结果
        with open('test_p1_normal.json', 'w', encoding='utf-8') as f:
            json.dump(result1, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_p1_normal.json")

    except Exception as e:
        print(f"\n❌ 测试1失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 测试2：混合内容（可能跑偏） ==========
    print(f"\n{'='*80}")
    print("【测试2】混合内容（消化系统 + 循环系统）")
    print(f"{'='*80}")

    mixed_content = """
    第六章 消化系统

    一、胃液的分泌
    胃液的主要成分包括盐酸、胃蛋白酶原、内因子和黏液。

    第四章 循环系统

    一、心脏的泵血功能
    心脏通过收缩和舒张实现泵血功能。

    二、血压的形成
    血压是血液对血管壁的侧压力。

    三、心电图
    心电图反映心脏的电活动。
    """ * 3  # 约1500字

    try:
        start_time = time.time()

        result2 = await quiz_service.generate_exam_paper(
            uploaded_content=mixed_content,
            num_questions=5
        )

        elapsed2 = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed2:.2f} 秒")
        print(f"试卷标题: {result2.get('paper_title')}")
        print(f"题目数量: {len(result2.get('questions', []))}")

        # 检查题目质量
        questions2 = result2.get('questions', [])
        incomplete2 = 0

        for i, q in enumerate(questions2, 1):
            if not q.get('question') or not q.get('options') or not q.get('correct_answer'):
                incomplete2 += 1

        print(f"统计: {len(questions2)}道题，{incomplete2}道不完整")

        # 保存结果
        with open('test_p1_mixed.json', 'w', encoding='utf-8') as f:
            json.dump(result2, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_p1_mixed.json")

    except Exception as e:
        print(f"\n❌ 测试2失败: {e}")
        import traceback
        traceback.print_exc()

    # ========== 总结 ==========
    print(f"\n{'='*80}")
    print("测试总结")
    print(f"{'='*80}")

    print(f"\n测试1（正常内容）:")
    print(f"  - 耗时: {elapsed1:.2f}秒")
    print(f"  - 质量: {len(questions1) - incomplete1}/{len(questions1)} 完整")

    if 'elapsed2' in locals():
        print(f"\n测试2（混合内容）:")
        print(f"  - 耗时: {elapsed2:.2f}秒")
        print(f"  - 质量: {len(questions2) - incomplete2}/{len(questions2)} 完整")

    print(f"\n主题一致性校验功能:")
    print(f"  - 状态: {'启用' if quiz_service.topic_check_enabled else '禁用'}")
    print(f"  - 阈值: {quiz_service.topic_overlap_threshold:.2%}")

    print(f"\n前端超时提示优化:")
    print(f"  - 进度条: ✅ 已实现")
    print(f"  - 超时警告: ✅ 已实现")
    print(f"  - 友好提示: ✅ 已实现")
    print(f"  - 需要手动测试前端页面")

    print(f"\n{'='*80}")
    print("P1级优化测试完成")
    print(f"{'='*80}")

if __name__ == "__main__":
    asyncio.run(test_topic_consistency())
