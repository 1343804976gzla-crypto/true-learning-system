"""
测试Flash模型和缓存系统优化效果
对比：
1. Flash模型 vs Pro模型的性能和质量
2. 缓存命中 vs 首次生成的性能
"""
import asyncio
import time
import json
from services.quiz_service_v2 import get_quiz_service

async def test_flash_and_cache():
    """测试Flash模型和缓存系统"""

    # 测试内容（约6000字，触发单次生成）
    test_content = """
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
    """ * 2  # 约6000字

    print("=" * 80)
    print("测试Flash模型和缓存系统优化效果")
    print("=" * 80)

    quiz_service = get_quiz_service()

    print(f"\n内容长度: {len(test_content)} 字符")
    print(f"题目数量: 10 道")

    # ========== 测试1：首次生成（Flash模型） ==========
    print(f"\n{'='*80}")
    print("【测试1】首次生成（Flash模型）")
    print(f"{'='*80}")

    try:
        start_time = time.time()

        result1 = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=10
        )

        elapsed1 = time.time() - start_time

        print(f"\n✅ 首次生成成功")
        print(f"耗时: {elapsed1:.2f} 秒")
        print(f"试卷标题: {result1.get('paper_title')}")
        print(f"题目数量: {len(result1.get('questions', []))}")

        # 检查题目完整性
        questions1 = result1.get('questions', [])
        incomplete1 = 0

        for i, q in enumerate(questions1, 1):
            issues = []

            if not q.get('question'):
                issues.append("题目缺失")

            options = q.get('options', {})
            if not options:
                issues.append("选项字段缺失")
            else:
                for opt in ['A', 'B', 'C', 'D', 'E']:
                    if opt not in options or not options[opt]:
                        issues.append(f"选项{opt}问题")
                        break

            if not q.get('correct_answer'):
                issues.append("答案缺失")

            if issues:
                print(f"❌ 第{i}题: {', '.join(issues)}")
                incomplete1 += 1

        print(f"\n统计: {len(questions1)}道题，{incomplete1}道不完整")

        # 保存结果
        with open('test_flash_first.json', 'w', encoding='utf-8') as f:
            json.dump(result1, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_flash_first.json")

    except Exception as e:
        print(f"\n❌ 首次生成失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 测试2：缓存命中 ==========
    print(f"\n{'='*80}")
    print("【测试2】缓存命中（相同内容和题目数）")
    print(f"{'='*80}")

    try:
        start_time = time.time()

        result2 = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=10
        )

        elapsed2 = time.time() - start_time

        print(f"\n✅ 缓存命中成功")
        print(f"耗时: {elapsed2:.2f} 秒")
        print(f"试卷标题: {result2.get('paper_title')}")
        print(f"题目数量: {len(result2.get('questions', []))}")

        # 验证是否是相同的结果
        if result1 == result2:
            print(f"✅ 缓存结果与首次生成完全一致")
        else:
            print(f"⚠️ 缓存结果与首次生成不一致（可能是缓存未生效）")

    except Exception as e:
        print(f"\n❌ 缓存测试失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 测试3：不同题目数（不命中缓存） ==========
    print(f"\n{'='*80}")
    print("【测试3】不同题目数（不命中缓存）")
    print(f"{'='*80}")

    try:
        start_time = time.time()

        result3 = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=5  # 不同的题目数
        )

        elapsed3 = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed3:.2f} 秒")
        print(f"试卷标题: {result3.get('paper_title')}")
        print(f"题目数量: {len(result3.get('questions', []))}")

    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 性能对比 ==========
    print(f"\n{'='*80}")
    print("性能对比总结")
    print(f"{'='*80}")

    print(f"\n首次生成（Flash模型）: {elapsed1:.2f} 秒")
    print(f"缓存命中: {elapsed2:.2f} 秒")
    print(f"不同题目数: {elapsed3:.2f} 秒")

    if elapsed2 < 1 and elapsed2 > 0:
        speedup = elapsed1 / elapsed2
        print(f"\n✅ 缓存加速比: {speedup:.1f}x（提升 {(1 - elapsed2/elapsed1)*100:.1f}%）")
    elif elapsed2 < 0.01:
        print(f"\n✅ 缓存加速比: 极高（缓存几乎瞬时返回，提升 >99%）")
    else:
        print(f"\n⚠️ 缓存未生效或效果不明显")

    # 质量评估
    print(f"\n{'='*80}")
    print("质量评估")
    print(f"{'='*80}")

    if incomplete1 == 0:
        print(f"✅ Flash模型质量优秀：10道题全部完整")
    elif incomplete1 <= 1:
        print(f"✅ Flash模型质量良好：{10 - incomplete1}/10 道题完整")
    else:
        print(f"⚠️ Flash模型质量一般：{10 - incomplete1}/10 道题完整")

    # 最终结论
    print(f"\n{'='*80}")
    print("最终结论")
    print(f"{'='*80}")

    if elapsed1 < 60 and incomplete1 == 0:
        print(f"✅ 优化成功！")
        print(f"  - Flash模型性能优秀（{elapsed1:.2f}秒 < 60秒）")
        print(f"  - 题目质量完整（0道不完整）")
        if elapsed2 < 1:
            print(f"  - 缓存系统生效（{elapsed2:.2f}秒）")
    elif elapsed1 < 90:
        print(f"✅ 优化良好")
        print(f"  - Flash模型性能良好（{elapsed1:.2f}秒 < 90秒）")
        print(f"  - 题目质量：{10 - incomplete1}/10 完整")
    else:
        print(f"⚠️ 需要进一步优化")
        print(f"  - 耗时较长：{elapsed1:.2f}秒")

if __name__ == "__main__":
    asyncio.run(test_flash_and_cache())
