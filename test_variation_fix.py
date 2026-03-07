"""
测试变式题生成修复
验证：
1. 是否生成完整的5道题
2. 每道题是否有完整的A-E选项
3. 每道题是否有解析
"""
import asyncio
import json
from services.quiz_service_v2 import get_quiz_service

async def test_variation_generation():
    """测试变式题生成"""

    quiz_service = get_quiz_service()

    # 模拟一个基础题目
    base_question = {
        "type": "A1",
        "difficulty": "基础",
        "question": "关于胃液分泌细胞及其主要分泌物的对应关系，下列哪项是正确的？",
        "options": {
            "A": "壁细胞——胃蛋白酶原",
            "B": "主细胞——盐酸",
            "C": "黏液细胞——内因子",
            "D": "壁细胞——内因子",
            "E": "主细胞——黏液"
        },
        "correct_answer": "D",
        "explanation": "胃液中，盐酸和内因子由壁细胞分泌；胃蛋白酶原由主细胞分泌；黏液由黏液细胞分泌。内因子是唯一能促进维生素B12吸收的物质。"
    }

    uploaded_content = """
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

    print("=" * 80)
    print("测试变式题生成修复")
    print("=" * 80)

    try:
        print("\n开始生成5道变式题...")
        variations = await quiz_service.generate_variation_questions(
            key_point="胃液成分的细胞来源",
            base_question=base_question,
            uploaded_content=uploaded_content,
            num_variations=5
        )

        print(f"\n✅ 生成成功，共 {len(variations)} 道题")

        # 验证每道题
        all_valid = True
        for i, v in enumerate(variations, 1):
            print(f"\n{'='*60}")
            print(f"第 {i} 题验证")
            print(f"{'='*60}")

            issues = []

            # 检查题目
            if not v.get("question"):
                issues.append("❌ 题目缺失")
            else:
                print(f"✅ 题目: {v['question'][:50]}...")

            # 检查选项
            options = v.get("options", {})
            missing_opts = []
            for opt in ["A", "B", "C", "D", "E"]:
                if not options.get(opt):
                    missing_opts.append(opt)

            if missing_opts:
                issues.append(f"❌ 选项缺失: {missing_opts}")
                all_valid = False
            else:
                print(f"✅ 选项完整: A-E")

            # 检查答案
            if not v.get("correct_answer"):
                issues.append("❌ 答案缺失")
                all_valid = False
            else:
                print(f"✅ 答案: {v['correct_answer']}")

            # 检查解析
            if not v.get("explanation"):
                issues.append("❌ 解析缺失")
                all_valid = False
            else:
                print(f"✅ 解析: {v['explanation'][:50]}...")

            # 检查变式类型
            if v.get("variation_type"):
                print(f"✅ 变式类型: {v['variation_type']}")

            if issues:
                print("\n问题汇总:")
                for issue in issues:
                    print(f"  {issue}")

        print(f"\n{'='*80}")
        if all_valid and len(variations) == 5:
            print("✅ 所有测试通过！5道题全部完整")
        else:
            print(f"⚠️ 存在问题：")
            if len(variations) < 5:
                print(f"  - 题目数量不足: {len(variations)}/5")
            if not all_valid:
                print(f"  - 部分题目不完整")
        print(f"{'='*80}")

        # 保存结果
        with open('test_variation_result.json', 'w', encoding='utf-8') as f:
            json.dump(variations, f, ensure_ascii=False, indent=2)
        print("\n✅ 结果已保存到 test_variation_result.json")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_variation_generation())
