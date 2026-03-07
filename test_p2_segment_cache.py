"""
测试P2级优化 - 分段结果缓存
验证：
1. 分段缓存是否生效
2. 用户调整题目数量时是否能复用分段
3. 性能提升效果
"""
import asyncio
import time
import json
from services.quiz_service_v2 import get_quiz_service

async def test_segment_cache():
    """测试分段结果缓存"""

    print("=" * 80)
    print("测试P2级优化 - 分段结果缓存")
    print("=" * 80)

    quiz_service = get_quiz_service()

    # 测试内容（约15000字，触发分段）
    base_content = """
    第六章 消化系统

    一、胃液的分泌

    胃液的主要成分包括：
    1. 盐酸（HCl）：由壁细胞分泌，pH值1.5-2.0
       - 激活胃蛋白酶原
       - 杀死细菌
       - 促进铁的吸收
       - 提供酸性环境

    2. 胃蛋白酶原：由主细胞分泌
       - 在酸性环境下被激活为胃蛋白酶
       - 分解蛋白质
       - 最适pH为1.5-2.5

    3. 内因子：由壁细胞分泌
       - 促进维生素B12吸收
       - 缺乏会导致恶性贫血
       - 是唯一能促进B12吸收的物质

    4. 黏液：由黏液细胞分泌
       - 保护胃黏膜
       - 中和胃酸
       - 形成黏液-碳酸氢盐屏障

    二、胃液分泌的调节

    1. 神经调节
       - 迷走神经兴奋促进胃液分泌
       - 交感神经抑制胃液分泌
       - 条件反射和非条件反射

    2. 体液调节
       - 胃泌素：促进胃酸和胃蛋白酶原分泌
       - 组胺：促进壁细胞分泌盐酸
       - 生长抑素：抑制胃液分泌

    3. 分泌的三个时相
       - 头期：条件反射和非条件反射
       - 胃期：食物刺激胃壁
       - 肠期：食物进入小肠

    三、胃的运动

    1. 胃的容受性舒张
       - 食物进入胃时，胃底和胃体舒张
       - 由迷走神经介导
       - 使胃容积增大而压力不升高

    2. 胃的蠕动
       - 从胃体开始，向幽门方向推进
       - 频率约3次/分钟
       - 混合和推进食物

    3. 胃排空
       - 液体排空快于固体
       - 糖类排空快于蛋白质，蛋白质快于脂肪
       - 受十二指肠内容物的反馈调节

    四、小肠的消化与吸收

    1. 胰液的分泌
       - 胰淀粉酶：消化淀粉
       - 胰脂肪酶：消化脂肪
       - 胰蛋白酶：消化蛋白质
       - 碳酸氢盐：中和胃酸

    2. 胆汁的作用
       - 乳化脂肪
       - 促进脂溶性维生素吸收
       - 中和胃酸
       - 促进胰脂肪酶活性

    3. 小肠的吸收
       - 糖类：主动转运
       - 蛋白质：主动转运
       - 脂肪：被动扩散
       - 水和电解质：渗透和主动转运

    五、大肠的功能

    1. 吸收水分和电解质
    2. 储存和排泄粪便
    3. 细菌发酵产生维生素K和B族维生素
    4. 形成粪便

    六、消化系统疾病

    1. 消化性溃疡
       - 胃酸分泌过多
       - 黏膜保护因素减弱
       - 幽门螺杆菌感染

    2. 胃食管反流病
       - 下食管括约肌功能障碍
       - 胃酸反流入食管
       - 引起烧心、反酸

    3. 炎症性肠病
       - 克罗恩病
       - 溃疡性结肠炎
       - 自身免疫性疾病

    4. 肝硬化
       - 肝细胞坏死
       - 纤维组织增生
       - 肝功能衰竭
       - 门静脉高压

    七、临床意义

    1. 胃镜检查
       - 直接观察胃黏膜
       - 活检病理检查
       - 治疗性操作

    2. 幽门螺杆菌检测
       - 尿素呼气试验
       - 粪便抗原检测
       - 血清抗体检测

    3. 消化道出血
       - 呕血和黑便
       - 失血性休克
       - 内镜止血治疗

    4. 肠梗阻
       - 机械性梗阻
       - 动力性梗阻
       - 腹痛、呕吐、腹胀、停止排气排便
    """

    # 重复内容以达到15000字
    repeat_times = max(1, (15000 // len(base_content)) + 1)
    test_content = base_content * repeat_times

    print(f"\n内容长度: {len(test_content)} 字符")
    print(f"预期分段: {(len(test_content) + 5999) // 6000} 段（20题场景，阈值6000）")

    # ========== 测试1：首次生成20题 ==========
    print(f"\n{'='*80}")
    print("【测试1】首次生成20题（触发分段，建立分段缓存）")
    print(f"{'='*80}")

    try:
        start_time = time.time()

        result1 = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=20
        )

        elapsed1 = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed1:.2f} 秒")
        print(f"试卷标题: {result1.get('paper_title')}")
        print(f"题目数量: {len(result1.get('questions', []))}")

        # 保存结果
        with open('test_p2_first_20.json', 'w', encoding='utf-8') as f:
            json.dump(result1, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_p2_first_20.json")

    except Exception as e:
        print(f"\n❌ 测试1失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 测试2：生成15题（应该复用部分分段缓存） ==========
    print(f"\n{'='*80}")
    print("【测试2】生成15题（相同内容，应该复用分段缓存）")
    print(f"{'='*80}")

    try:
        start_time = time.time()

        result2 = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=15
        )

        elapsed2 = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed2:.2f} 秒")
        print(f"试卷标题: {result2.get('paper_title')}")
        print(f"题目数量: {len(result2.get('questions', []))}")

        # 保存结果
        with open('test_p2_second_15.json', 'w', encoding='utf-8') as f:
            json.dump(result2, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_p2_second_15.json")

    except Exception as e:
        print(f"\n❌ 测试2失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 测试3：生成10题（应该复用更多分段缓存） ==========
    print(f"\n{'='*80}")
    print("【测试3】生成10题（相同内容，应该复用更多分段缓存）")
    print(f"{'='*80}")

    try:
        start_time = time.time()

        result3 = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=10
        )

        elapsed3 = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed3:.2f} 秒")
        print(f"试卷标题: {result3.get('paper_title')}")
        print(f"题目数量: {len(result3.get('questions', []))}")

        # 保存结果
        with open('test_p2_third_10.json', 'w', encoding='utf-8') as f:
            json.dump(result3, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_p2_third_10.json")

    except Exception as e:
        print(f"\n❌ 测试3失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 性能对比 ==========
    print(f"\n{'='*80}")
    print("性能对比总结")
    print(f"{'='*80}")

    print(f"\n首次生成（20题）: {elapsed1:.2f} 秒")
    print(f"第二次生成（15题）: {elapsed2:.2f} 秒")
    print(f"第三次生成（10题）: {elapsed3:.2f} 秒")

    # 计算加速比
    if elapsed2 < elapsed1:
        speedup2 = (elapsed1 - elapsed2) / elapsed1 * 100
        print(f"\n✅ 第二次生成加速: {speedup2:.1f}%")
    else:
        print(f"\n⚠️ 第二次生成未加速（可能分段缓存未生效）")

    if elapsed3 < elapsed1:
        speedup3 = (elapsed1 - elapsed3) / elapsed1 * 100
        print(f"✅ 第三次生成加速: {speedup3:.1f}%")
    else:
        print(f"⚠️ 第三次生成未加速（可能分段缓存未生效）")

    # 分段缓存效果评估
    print(f"\n{'='*80}")
    print("分段缓存效果评估")
    print(f"{'='*80}")

    print(f"\n分段缓存功能:")
    print(f"  - 状态: {'启用' if quiz_service.segment_cache_enabled else '禁用'}")
    print(f"  - 缓存条目数: {len(quiz_service._segment_cache)}")

    if quiz_service.segment_cache_enabled and len(quiz_service._segment_cache) > 0:
        print(f"\n✅ 分段缓存已建立")
        print(f"  - 理论效果: 用户调整题目数量时可复用分段")
        print(f"  - 实际效果: 第二次生成耗时 {elapsed2:.2f}秒 vs 首次 {elapsed1:.2f}秒")
    else:
        print(f"\n⚠️ 分段缓存未建立或已禁用")

    # 最终结论
    print(f"\n{'='*80}")
    print("最终结论")
    print(f"{'='*80}")

    if elapsed2 < elapsed1 * 0.8:
        print(f"✅ P2级优化成功！")
        print(f"  - 分段缓存生效")
        print(f"  - 性能提升显著")
    elif elapsed2 < elapsed1:
        print(f"✅ P2级优化部分成功")
        print(f"  - 有一定加速效果")
        print(f"  - 可能需要调整分段策略")
    else:
        print(f"⚠️ P2级优化效果不明显")
        print(f"  - 可能是网络波动")
        print(f"  - 或分段缓存未命中")

if __name__ == "__main__":
    asyncio.run(test_segment_cache())
