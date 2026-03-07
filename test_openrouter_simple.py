"""
简单测试OpenRouter保底模型
直接测试连接性和基本功能
"""
import asyncio
import time
from services.quiz_service_v2 import get_quiz_service

async def test_openrouter_simple():
    """简单测试OpenRouter"""

    print("=" * 80)
    print("测试OpenRouter保底模型")
    print("=" * 80)

    quiz_service = get_quiz_service()

    # 测试内容（短内容，5题）
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
    """

    print(f"\n内容长度: {len(test_content)} 字符")
    print(f"题目数量: 5 道")

    try:
        print("\n开始生成...")
        start_time = time.time()

        result = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=5
        )

        elapsed = time.time() - start_time

        print(f"\n✅ 生成成功")
        print(f"耗时: {elapsed:.2f} 秒")
        print(f"试卷标题: {result.get('paper_title')}")
        print(f"题目数量: {len(result.get('questions', []))}")

        # 检查题目质量
        questions = result.get('questions', [])
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
        import json
        with open('test_openrouter_simple.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到 test_openrouter_simple.json")

        # 最终结论
        print(f"\n{'='*80}")
        print("测试结论")
        print(f"{'='*80}")

        if elapsed < 60 and incomplete == 0:
            print(f"✅ OpenRouter保底模型工作正常")
            print(f"  - 性能: {elapsed:.2f}秒 < 60秒")
            print(f"  - 质量: {len(questions) - incomplete}/{len(questions)} 完整")
        elif elapsed < 120:
            print(f"✅ OpenRouter保底模型基本正常")
            print(f"  - 性能: {elapsed:.2f}秒 < 120秒")
            print(f"  - 质量: {len(questions) - incomplete}/{len(questions)} 完整")
        else:
            print(f"⚠️ OpenRouter保底模型响应较慢")
            print(f"  - 耗时: {elapsed:.2f}秒")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_openrouter_simple())
