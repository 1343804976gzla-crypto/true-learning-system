"""
错题变式手术服务
- generate_variant: AI生成变式题
- evaluate_rationale: AI评估用户推理
- build_rescue_report: 生成深水区求助报告
"""

from datetime import datetime
from typing import Dict, Any, Optional
from services.ai_client import get_ai_client
from utils.data_contracts import canonicalize_variant_data


async def generate_variant(wa) -> dict:
    """
    为错题生成变式题。
    核心约束：知识点100%相同，只改表面表达。
    """
    ai = get_ai_client()

    options_text = ""
    if wa.options:
        for k, v in wa.options.items():
            options_text += f"  {k}. {v}\n"

    prompt = f"""【角色】你是资深西医综合（306）考研命题专家，擅长设计变式题。

【任务】基于以下原题，生成一道变式题。

【核心铁律 — 违反则无效】
1. 考察的核心知识点必须与原题100%相同
2. 正确答案的医学原理必须与原题一致
3. 只允许改变：病例场景、提问角度、选项措辞、干扰项内容
4. 变式题的难度应与原题相当或略高

【原题信息】
知识点：{wa.key_point or '未标注'}
题型：{wa.question_type or 'A1'}
难度：{wa.difficulty or '基础'}

题目：{wa.question_text}

选项：
{options_text}
正确答案：{wa.correct_answer}

解析：{wa.explanation or '无'}

【变式策略（随机选一种）】
- 病例变式：换一个临床场景，但考察同一机制
- 选项重组：正确选项不变，更换干扰项使其更具迷惑性
- 反向提问：从"哪个正确"变为"哪个错误"，或反之
- 干扰项升级：加入更接近正确答案的干扰项
- 临床场景迁移：从理论题变为病例题，或反之

【输出格式 — 严格JSON】
{{
    "variant_question": "变式题题目文本（必填，不能为空）",
    "variant_options": {{
        "A": "选项A内容（必填）",
        "B": "选项B内容（必填）",
        "C": "选项C内容（必填）",
        "D": "选项D内容（必填）",
        "E": "选项E内容（必填）"
    }},
    "variant_answer": "正确答案字母（必填，如 A 或 AB）",
    "variant_explanation": "详细解析（必填，至少100字）：为什么对+为什么错+与原题的关联",
    "transform_type": "使用了哪种变式策略（必填）",
    "core_knowledge": "不变的核心考点（必填，一句话）"
}}

【关键要求】
1. variant_options 必须包含 A/B/C/D/E 全部5个选项，每个选项内容不能为空
2. variant_explanation 必须详细，至少100字，包含：正确选项原理+错误选项分析+与原题关联
3. 所有字段都是必填项，不能为空字符串"""

    schema = {
        "variant_question": "变式题文本",
        "variant_options": {"A": "", "B": "", "C": "", "D": "", "E": ""},
        "variant_answer": "A",
        "variant_explanation": "解析",
        "transform_type": "变式类型",
        "core_knowledge": "核心考点",
    }

    try:
        result = await ai.generate_json(prompt, schema, max_tokens=3000, temperature=0.4, use_heavy=True, timeout=300)

        # 验证必填字段
        if not result.get("variant_question") or not result.get("variant_question").strip():
            raise ValueError("variant_question 为空")
        if not result.get("variant_explanation") or len(result.get("variant_explanation", "").strip()) < 50:
            raise ValueError(f"variant_explanation 太短或为空: {len(result.get('variant_explanation', ''))}")
        if not result.get("variant_answer"):
            raise ValueError("variant_answer 为空")

        # 确保5个选项都存在且非空
        opts = result.get("variant_options", {})
        missing_opts = []
        for k in ["A", "B", "C", "D", "E"]:
            if k not in opts or not opts.get(k) or not opts[k].strip():
                missing_opts.append(k)

        if missing_opts:
            raise ValueError(f"选项缺失或为空: {', '.join(missing_opts)}")

        result["variant_options"] = opts

        # 规范化 variant_answer：提取 A-E 字母，支持多选（AI 可能返回 "B. 选项内容" 等格式）
        from utils.answer import normalize_answer
        result["variant_answer"] = normalize_answer(result.get("variant_answer") or "")

        # 加时间戳
        result["generated_at"] = datetime.now().isoformat()

        print(f"[VariantSurgery] 变式生成成功: {result.get('transform_type')}")
        return result

    except Exception as e:
        print(f"[VariantSurgery] 变式生成失败: {e}")
        raise


async def evaluate_rationale(
    wa, user_answer: str, rationale_text: str, is_correct: bool
) -> dict:
    """
    AI评估用户的推理文本，给出判决。
    三种判决：logic_closed / lucky_guess / failed
    """
    ai = get_ai_client()

    variant = canonicalize_variant_data(wa.variant_data) or {}
    variant_q = variant.get("variant_question", "")
    variant_opts = variant.get("variant_options", {})
    variant_ans = variant.get("variant_answer", "")
    variant_exp = variant.get("variant_explanation", "")
    core_kp = variant.get("core_knowledge", wa.key_point or "")

    opts_text = ""
    for k, v in variant_opts.items():
        opts_text += f"  {k}. {v}\n"

    prompt = f"""【角色】你是医学教育评估专家，负责判断学生是否真正掌握了知识点。

【任务】评估学生对一道变式题的推理过程。

【变式题】
{variant_q}

选项：
{opts_text}
正确答案：{variant_ans}

解析：{variant_exp}

核心知识点：{core_kp}

【学生作答】
选择的答案：{user_answer}
答案是否正确：{"正确" if is_correct else "错误"}

【学生的推理过程】
{rationale_text}

【评估维度】
1. 逻辑完整性：推理链条是否完整，有无跳跃
2. 知识准确性：涉及的医学知识是否正确
3. 因果关系：是否正确建立了从知识点到答案的因果链

【判决规则】
- 如果答案正确 且 推理过程体现了对核心知识点的正确理解（评分≥70）→ verdict = "logic_closed"
- 如果答案正确 但 推理过程有明显漏洞或靠排除法蒙对（评分<70）→ verdict = "lucky_guess"
- 如果答案错误 → verdict = "failed"

【输出格式 — 严格JSON】
{{
    "verdict": "logic_closed 或 lucky_guess 或 failed",
    "reasoning_score": 0到100的整数,
    "diagnosis": "一段话诊断：学生的推理哪里对、哪里错、核心盲区在哪",
    "weak_links": ["薄弱环节1", "薄弱环节2"]
}}"""

    schema = {
        "verdict": "logic_closed",
        "reasoning_score": 80,
        "diagnosis": "诊断文本",
        "weak_links": ["薄弱环节"],
    }

    try:
        # 推理评估改用轻量级模型（DeepSeek），速度提升3-5倍
        result = await ai.generate_json(prompt, schema, max_tokens=2000, temperature=0.2, use_heavy=False, timeout=60)

        # 强制校正 verdict（防止AI不遵守规则）
        score = result.get("reasoning_score", 0)
        if not is_correct:
            result["verdict"] = "failed"
        elif score >= 70:
            result["verdict"] = "logic_closed"
        else:
            result["verdict"] = "lucky_guess"

        print(f"[VariantSurgery] 评估完成: verdict={result['verdict']}, score={score}")
        return result

    except Exception as e:
        print(f"[VariantSurgery] 推理评估失败: {e}")
        # 降级：无AI评估时用简单规则
        if not is_correct:
            return {
                "verdict": "failed",
                "reasoning_score": 0,
                "diagnosis": "AI评估暂时不可用，答案错误。",
                "weak_links": [wa.key_point or "未知"],
            }
        else:
            return {
                "verdict": "lucky_guess",
                "reasoning_score": 50,
                "diagnosis": "AI评估暂时不可用，无法判断推理质量，暂按蒙对处理。",
                "weak_links": [wa.key_point or "未知"],
            }


def build_rescue_report(wa, retry) -> str:
    """
    生成深水区求助报告（Markdown格式），用于复制给外部AI辅导。
    """
    # 原题信息
    orig_opts = ""
    if wa.options:
        for k, v in wa.options.items():
            marker = " ✅" if k == wa.correct_answer else ""
            orig_opts += f"- {k}. {v}{marker}\n"

    # 变式题信息
    variant = canonicalize_variant_data(wa.variant_data) or {}
    var_q = variant.get("variant_question", "（无变式题）")
    var_opts_text = ""
    var_opts = variant.get("variant_options", {})
    var_ans = variant.get("variant_answer", "?")
    for k, v in var_opts.items():
        marker = " ✅" if k == var_ans else ""
        var_opts_text += f"- {k}. {v}{marker}\n"

    # 用户推理
    rationale = retry.rationale_text or "（未填写）"

    # AI评估
    ai_eval = retry.ai_evaluation or {}
    diagnosis = ai_eval.get("diagnosis", "（无AI诊断）")
    score = ai_eval.get("reasoning_score", "?")
    verdict_map = {
        "logic_closed": "✅ 逻辑闭环",
        "lucky_guess": "🍀 蒙对（降级为地雷）",
        "failed": "❌ 未通过",
    }
    verdict_label = verdict_map.get(ai_eval.get("verdict", ""), "未知")
    weak = ai_eval.get("weak_links", [])
    weak_text = "、".join(weak) if weak else "无"

    report = f"""## 🆘 错题深水区求助

### 📋 原题
**知识点**: {wa.key_point or '未标注'}
**题型**: {wa.question_type or 'A1'} | **难度**: {wa.difficulty or '基础'} | **累计错误**: {wa.error_count}次

{wa.question_text}

{orig_opts}
**正确答案**: {wa.correct_answer}

**解析**: {wa.explanation or '无'}

---

### 🧬 AI变式题
{var_q}

{var_opts_text}
**正确答案**: {var_ans}

---

### 🧠 我的推理过程
> 我选了: {retry.user_answer} ({"正确" if retry.is_correct else "错误"})

{rationale}

---

### 🔬 AI诊断
- **判决**: {verdict_label}
- **推理评分**: {score}/100
- **诊断**: {diagnosis}
- **薄弱环节**: {weak_text}

---

### ❓ 求助问题
请帮我分析：
1. 我的推理过程哪里出了问题？
2. 正确的思维链条应该是什么？
3. 这个知识点的核心要点是什么？
4. 有什么记忆技巧可以避免再犯？
"""
    return report
