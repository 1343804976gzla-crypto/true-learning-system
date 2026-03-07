"""
诊断整卷测试题目和选项缺失问题
"""
import asyncio
import json
from services.quiz_service_v2 import get_quiz_service
from services.ai_client import get_ai_client

async def test_quiz_generation():
    """测试出题功能，诊断问题"""

    # 测试内容
    test_content = """
    第六章 消化系统

    今天我们来讲胃液的分泌。

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
    print("开始诊断整卷测试问题")
    print("=" * 80)

    quiz_service = get_quiz_service()
    ai_client = get_ai_client()

    # 检查 AI 客户端配置
    print("\n【1. AI 客户端配置检查】")
    print(f"DeepSeek 客户端: {'✅ 已配置' if ai_client.ds_client else '❌ 未配置'}")
    print(f"Gemini 客户端: {'✅ 已配置' if ai_client.gm_client else '❌ 未配置'}")
    print(f"DeepSeek 模型: {ai_client.ds_model}")
    print(f"Gemini 模型: {ai_client.gm_model}")
    print(f"严格模式: {ai_client.strict_heavy}")

    # 测试生成 5 道题
    print("\n【2. 测试生成 5 道题】")
    try:
        result = await quiz_service.generate_exam_paper(
            uploaded_content=test_content,
            num_questions=5
        )

        print(f"✅ 生成成功")
        print(f"试卷标题: {result.get('paper_title')}")
        print(f"题目数量: {len(result.get('questions', []))}")

        # 检查每道题的完整性
        print("\n【3. 题目完整性检查】")
        questions = result.get('questions', [])

        for i, q in enumerate(questions, 1):
            print(f"\n--- 第 {i} 题 ---")
            print(f"ID: {q.get('id', '❌ 缺失')}")
            print(f"题型: {q.get('type', '❌ 缺失')}")
            print(f"难度: {q.get('difficulty', '❌ 缺失')}")

            # 检查题目
            question_text = q.get('question', '')
            if not question_text:
                print(f"❌ 题目内容缺失")
            else:
                print(f"题目: {question_text[:50]}...")

            # 检查选项
            options = q.get('options', {})
            if not options:
                print(f"❌ 选项字段缺失")
            else:
                missing_opts = []
                empty_opts = []
                for opt in ['A', 'B', 'C', 'D', 'E']:
                    if opt not in options:
                        missing_opts.append(opt)
                    elif not options[opt] or options[opt].strip() == '':
                        empty_opts.append(opt)
                    elif '缺失' in options[opt]:
                        empty_opts.append(opt)

                if missing_opts:
                    print(f"❌ 缺少选项键: {missing_opts}")
                if empty_opts:
                    print(f"❌ 选项内容为空: {empty_opts}")

                if not missing_opts and not empty_opts:
                    print(f"✅ 选项完整")
                else:
                    # 打印所有选项
                    for opt in ['A', 'B', 'C', 'D', 'E']:
                        val = options.get(opt, '(不存在)')
                        print(f"  {opt}: {val[:40]}...")

            # 检查答案
            correct_answer = q.get('correct_answer', '')
            if not correct_answer:
                print(f"❌ 正确答案缺失")
            else:
                print(f"答案: {correct_answer}")

            # 检查解析
            explanation = q.get('explanation', '')
            if not explanation:
                print(f"❌ 解析缺失")
            else:
                print(f"解析: {explanation[:50]}...")

            # 检查考点
            key_point = q.get('key_point', '')
            if not key_point:
                print(f"❌ 考点缺失")
            else:
                print(f"考点: {key_point}")

        # 统计问题
        print("\n【4. 问题统计】")
        total_questions = len(questions)
        incomplete_questions = 0
        missing_options_count = 0
        empty_options_count = 0

        for q in questions:
            has_issue = False

            if not q.get('question'):
                has_issue = True

            options = q.get('options', {})
            if not options:
                has_issue = True
                missing_options_count += 1
            else:
                for opt in ['A', 'B', 'C', 'D', 'E']:
                    if opt not in options or not options[opt] or '缺失' in options[opt]:
                        has_issue = True
                        empty_options_count += 1
                        break

            if not q.get('correct_answer'):
                has_issue = True

            if has_issue:
                incomplete_questions += 1

        print(f"总题目数: {total_questions}")
        print(f"不完整题目数: {incomplete_questions}")
        print(f"缺少选项字段: {missing_options_count}")
        print(f"选项内容为空: {empty_options_count}")

        if incomplete_questions == 0:
            print("\n✅ 所有题目完整！")
        else:
            print(f"\n❌ 发现 {incomplete_questions} 道题目不完整")

        # 保存原始响应用于分析
        print("\n【5. 保存原始响应】")
        with open('quiz_diagnosis_result.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("✅ 已保存到 quiz_diagnosis_result.json")

    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_quiz_generation())
