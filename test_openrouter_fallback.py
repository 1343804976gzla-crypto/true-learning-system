"""
测试多模型池化路由
验证：
1. Provider 注册和池配置是否正确
2. Fast池模型是否能正常工作
3. 模型池的性能和质量
"""
import asyncio
import time
import json
from services.ai_client import get_ai_client

async def test_openrouter_fallback():
    """测试多模型池化路由"""

    print("=" * 80)
    print("测试多模型池化路由")
    print("=" * 80)

    ai_client = get_ai_client()

    # 检查配置
    print("\n【配置检查】")
    print(f"已注册 Provider: {list(ai_client._providers.keys())}")
    print(f"Heavy池 ({len(ai_client._heavy_pool)}): {[e[2] for e in ai_client._heavy_pool]}")
    print(f"Light池 ({len(ai_client._light_pool)}): {[e[2] for e in ai_client._light_pool]}")
    print(f"Fast池  ({len(ai_client._fast_pool)}): {[e[2] for e in ai_client._fast_pool]}")

    if not ai_client._fast_pool:
        print("\n❌ Fast池为空，无保底模型")
        return

    # ========== 测试1：通过 Fast池 调用保底模型 ==========
    print(f"\n{'='*80}")
    print("【测试1】通过 Fast池 调用保底模型")
    print(f"{'='*80}")

    test_prompt = """请生成一道医学考研题目（消化系统）。

要求：
1. 题型：A1型单选题
2. 难度：基础
3. 必须有A、B、C、D、E五个选项
4. 包含正确答案和详细解析

输出JSON格式：
{
    "question": "题目内容",
    "options": {
        "A": "选项A",
        "B": "选项B",
        "C": "选项C",
        "D": "选项D",
        "E": "选项E"
    },
    "correct_answer": "正确答案（A/B/C/D/E）",
    "explanation": "详细解析"
}

只返回JSON："""

    schema = {
        "question": "题目内容",
        "options": {
            "A": "选项A",
            "B": "选项B",
            "C": "选项C",
            "D": "选项D",
            "E": "选项E"
        },
        "correct_answer": "A",
        "explanation": "解析"
    }

    try:
        print("\n开始调用 Fast池...")
        start_time = time.time()

        # 通过 generate_json 调用（use_heavy=False 走 Light池）
        result = await ai_client.generate_json(
            prompt=test_prompt,
            schema=schema,
            max_tokens=2000,
            temperature=0.3,
            use_heavy=False
        )

        elapsed = time.time() - start_time

        print(f"\n✅ 调用成功")
        print(f"耗时: {elapsed:.2f} 秒")
        print(f"\n生成的题目:")
        print(f"题目: {result.get('question', '无')[:80]}...")
        print(f"选项A: {result.get('options', {}).get('A', '无')[:50]}...")
        print(f"选项B: {result.get('options', {}).get('B', '无')[:50]}...")
        print(f"选项C: {result.get('options', {}).get('C', '无')[:50]}...")
        print(f"选项D: {result.get('options', {}).get('D', '无')[:50]}...")
        print(f"选项E: {result.get('options', {}).get('E', '无')[:50]}...")
        print(f"答案: {result.get('correct_answer', '无')}")
        print(f"解析: {result.get('explanation', '无')[:100]}...")

        # 验证完整性
        issues = []
        if not result.get('question'):
            issues.append("题目缺失")

        options = result.get('options', {})
        for opt in ['A', 'B', 'C', 'D', 'E']:
            if not options.get(opt):
                issues.append(f"选项{opt}缺失")

        if not result.get('correct_answer'):
            issues.append("答案缺失")

        if not result.get('explanation'):
            issues.append("解析缺失")

        if issues:
            print(f"\n⚠️ 质量问题: {', '.join(issues)}")
        else:
            print(f"\n✅ 题目完整，质量良好")

        # 保存结果
        with open('test_openrouter_direct.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 结果已保存到 test_openrouter_direct.json")

    except Exception as e:
        print(f"\n❌ 调用失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========== 测试2：通过整卷生成触发保底 ==========
    print(f"\n{'='*80}")
    print("【测试2】通过整卷生成测试保底机制")
    print(f"{'='*80}")

    from services.quiz_service_v2 import get_quiz_service

    quiz_service = get_quiz_service()

    test_content = """
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
    """ * 2  # 约1000字

    try:
        print("\n开始生成5道题...")
        start_time = time.time()

        result2 = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=5
        )

        elapsed2 = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed2:.2f} 秒")
        print(f"试卷标题: {result2.get('paper_title')}")
        print(f"题目数量: {len(result2.get('questions', []))}")

        # 检查题目质量
        questions = result2.get('questions', [])
        incomplete = 0

        for i, q in enumerate(questions, 1):
            issues = []
            if not q.get('question'):
                issues.append("题目缺失")

            options = q.get('options', {})
            for opt in ['A', 'B', 'C', 'D', 'E']:
                if not options.get(opt):
                    issues.append(f"选项{opt}缺失")
                    break

            if not q.get('correct_answer'):
                issues.append("答案缺失")

            if issues:
                print(f"❌ 第{i}题: {', '.join(issues)}")
                incomplete += 1
            else:
                print(f"✅ 第{i}题完整")

        print(f"\n统计: {len(questions)}道题，{incomplete}道不完整")

        # 保存结果
        with open('test_openrouter_quiz.json', 'w', encoding='utf-8') as f:
            json.dump(result2, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_openrouter_quiz.json")

    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()

    # ========== 性能对比 ==========
    print(f"\n{'='*80}")
    print("性能对比")
    print(f"{'='*80}")

    print(f"\n直接调用OpenRouter: {elapsed:.2f} 秒")
    if 'elapsed2' in locals():
        print(f"整卷生成（5题）: {elapsed2:.2f} 秒")
        print(f"平均每题: {elapsed2/5:.2f} 秒")

    # ========== 最终结论 ==========
    print(f"\n{'='*80}")
    print("最终结论")
    print(f"{'='*80}")

    if elapsed < 30:
        print(f"\n✅ OpenRouter保底模型性能优秀")
        print(f"  - 响应速度快（{elapsed:.2f}秒 < 30秒）")
    elif elapsed < 60:
        print(f"\n✅ OpenRouter保底模型性能良好")
        print(f"  - 响应速度可接受（{elapsed:.2f}秒 < 60秒）")
    else:
        print(f"\n⚠️ OpenRouter保底模型响应较慢")
        print(f"  - 耗时：{elapsed:.2f}秒")

    if 'incomplete' in locals() and incomplete == 0:
        print(f"✅ 题目质量完整（0道不完整）")
    elif 'incomplete' in locals():
        print(f"⚠️ 题目质量一般（{incomplete}道不完整）")

    print(f"\nFast池配置:")
    for entry in ai_client._fast_pool:
        print(f"  - {entry[2]}")
    print(f"  - 状态: {'✅ 可用' if elapsed < 60 else '⚠️ 响应慢'}")

if __name__ == "__main__":
    asyncio.run(test_openrouter_fallback())
