"""
融合升级服务 - 基于认知科学的 AI 服务

核心理念：
1. 防认知卸载 - 苏格拉底式引导，不直接给出答案
2. 层级学习 - 基础牢固后才能挑战高阶融合
3. 元认知停顿 - 强制延迟评判，促进自我反思
"""

import json
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from learning_tracking_models import WrongAnswerV2, WrongAnswerRetry
from services.ai_client import get_ai_client
from utils.data_contracts import canonicalize_fusion_data


class FusionService:
    """融合升级服务 - 苏格拉底式学习导师"""

    # ===== 动态惩罚系数配置 =====
    # L1=1.5, L2=2.0, L3+=2.5
    # 答错时：interval = max(MIN_INTERVAL, interval / penalty_factor)
    PENALTY_FACTORS = {1: 1.5, 2: 2.0}
    DEFAULT_PENALTY = 2.5  # L3+
    MIN_INTERVAL_HOURS = 12  # 保底阈值：最小12小时复习间隔

    # ===== 解锁条件配置 =====
    UNLOCK_REQUIRED_CORRECT = 3  # 连续正确次数
    UNLOCK_REQUIRED_CONFIDENCE = "sure"  # 必须100%确定

    def __init__(self):
        self.ai_client = get_ai_client()

    # ========== 1. 苏格拉底引导 ==========

    async def generate_socratic_hint(
        self,
        question_id: int,
        db: Session
    ) -> Dict[str, Any]:
        """
        生成苏格拉底式引导，帮助用户发现概念联系
        不直接推荐融合伙伴，而是通过提问引导用户自己思考
        """
        # 获取题目详情
        wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == question_id).first()
        if not wa:
            return {"error": "题目不存在"}

        # 构建引导提示
        prompt = f"""你是一位苏格拉底式的学习导师。用户想要寻找可以融合升级的错题伙伴。

原题信息：
- 题目：{wa.question_text[:200]}...
- 知识点：{wa.key_point or "未标注"}
- 核心概念：{wa.question_type or "未分类"}题型，难度{wa.difficulty or "未知"}

请给出苏格拉底式的引导，帮助用户思考：
1. 这道题的核心概念是什么？
2. 在医学知识体系中，哪些其他概念可能与这个概念产生因果/关联/对比关系？
3. 在真实临床场景中，这些概念如何同时出现？

**重要**：不要直接推荐具体题目或概念名称。只通过提问引导用户自己去归档库中搜索和匹配。

请返回3-5个引导性问题，每个问题都要迫使用户主动回忆和建立联系。"""

        try:
            response = await self.ai_client.generate_content(prompt, use_heavy=True, timeout=240)
            # 解析响应，提取问题列表
            questions = self._extract_questions(response)

            return {
                "guide_questions": questions,
                "hint_text": response,
                "source_question_id": question_id,
                "source_key_point": wa.key_point
            }
        except Exception as e:
            # 降级处理：返回预设的苏格拉底问题
            return {
                "guide_questions": self._get_fallback_questions(wa.key_point),
                "hint_text": None,
                "source_question_id": question_id,
                "source_key_point": wa.key_point,
                "error": str(e)
            }

    def _extract_questions(self, text: str) -> List[str]:
        """从AI响应中提取问题列表"""
        questions = []
        lines = text.strip().split('\n')
        for line in lines:
            line = line.strip()
            # 匹配数字开头的行（如 "1." "1、" "- " "* "）
            if line and (line[0].isdigit() or line.startswith('-') or line.startswith('*')):
                # 去除序号前缀
                for prefix in ['1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.', '0.',
                               '1、', '2、', '3、', '4、', '5、', '6、', '7、', '8、', '9、', '0、',
                               '- ', '* ', '• ']:
                    if line.startswith(prefix):
                        line = line[len(prefix):].strip()
                        break
                if line and len(line) > 10:  # 过滤太短的内容
                    questions.append(line)
        return questions[:5]  # 最多5个问题

    def _get_fallback_questions(self, key_point: Optional[str]) -> List[str]:
        """获取备用的苏格拉底问题"""
        base_questions = [
            "这道题的核心概念是什么？能否用一句话概括其本质？",
            "在临床实践中，这个概念通常与哪些其他概念同时出现？",
            "如果改变这道题中的某个关键变量，会发生什么连锁反应？",
            "在你的归档库中，是否有涉及'原因'、'机制'或'影响'的类似概念？",
            "想象一下：如果两个概念同时出现在一个复杂病例中，你会如何分析？"
        ]
        if key_point:
            base_questions[0] = f"关于'{key_point}'，它的核心机制是什么？哪些因素会影响它？"
        return base_questions

    # ========== 2. 融合题生成 ==========

    async def generate_fusion_question(
        self,
        parent_ids: List[int],
        db: Session
    ) -> Dict[str, Any]:
        """
        基于2-4道原题生成高阶融合题
        要求：自由作答形式，考察概念间的相互关系
        """
        if len(parent_ids) < 2 or len(parent_ids) > 4:
            return {"error": "融合题必须由2-4道原题组成"}

        # 获取原题详情
        parents = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.id.in_(parent_ids)
        ).all()

        if len(parents) != len(parent_ids):
            return {"error": "部分原题不存在"}

        # 构建融合提示
        parent_texts = []
        key_points = []
        for i, p in enumerate(parents, 1):
            parent_texts.append(f"""
原题{i}:
- 知识点: {p.key_point or '未标注'}
- 题型: {p.question_type or 'A1'}
- 核心概念: {p.question_text[:150]}...""")
            if p.key_point:
                key_points.append(p.key_point)

        parents_description = "\n".join(parent_texts)

        prompt = f"""基于以下{len(parents)}道已掌握的题目，生成一道更高阶的融合题：

{parents_description}

要求：
1. 这道题必须同时涉及上述多个概念，考察它们之间的相互关系（因果/影响/对比/协同）
2. 设置一个复杂的临床场景，需要综合运用这些概念进行分析
3. 题目形式为**自由作答**（不是选择题），要求用户写出完整的推理过程
4. 难度要显著高于原题，但基于相同的知识基础
5. 题目应该考察"如果A变化，会对B和C产生什么影响？"这类高阶思维

请返回JSON格式：
{{
    "fusion_question": "题目文本（包含临床场景和具体问题）",
    "expected_key_points": ["预期回答要点1", "要点2", "要点3"],
    "scoring_criteria": {{
        "逻辑严密性": 30,
        "概念准确性": 40,
        "综合应用": 30
    }},
    "difficulty_level": "L{len(parents)}"
}}"""

        try:
            response = await self.ai_client.generate_content(prompt, use_heavy=True, timeout=300)
            # 解析JSON响应
            result = self._extract_json(response)

            if "error" in result:
                return result

            return {
                "fusion_question": result.get("fusion_question", ""),
                "expected_key_points": result.get("expected_key_points", []),
                "scoring_criteria": result.get("scoring_criteria", {}),
                "difficulty_level": result.get("difficulty_level", f"L{len(parents)}"),
                "parent_ids": parent_ids,
                "parent_key_points": key_points
            }
        except Exception as e:
            return {"error": f"生成融合题失败: {str(e)}"}

    def _extract_json(self, text: str) -> Dict:
        """从文本中提取JSON"""
        import re
        # 尝试找到JSON块
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {"error": "无法解析JSON", "raw": text[:500]}

    # ========== 3. 答案评判（手动触发） ==========

    async def judge_fusion_answer(
        self,
        fusion_id: int,
        user_answer: str,
        db: Session
    ) -> Dict[str, Any]:
        """
        评判融合题答案
        返回：verdict, score, feedback, needs_diagnosis
        """
        fusion = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.id == fusion_id,
            WrongAnswerV2.is_fusion == True
        ).first()

        if not fusion:
            return {"error": "融合题不存在"}

        # 获取融合题的预期要点
        fusion_data = canonicalize_fusion_data(fusion.fusion_data)
        expected_points = fusion_data.get("expected_key_points", [])
        scoring_criteria = fusion_data.get("scoring_criteria", {})

        # 构建评判提示
        criteria_text = "\n".join([f"- {k}: {v}分" for k, v in scoring_criteria.items()])

        prompt = f"""请评判以下融合题的答案：

【题目】
{fusion.question_text}

【预期回答要点】
{chr(10).join([f"- {p}" for p in expected_points]) if expected_points else "（无预设要点，请基于医学知识评判）"}

【评分标准】
{criteria_text if criteria_text else "- 逻辑严密性: 30分\\n- 概念准确性: 40分\\n- 综合应用: 30分"}

【用户答案】
{user_answer}

请进行严格评判，关注：
1. 逻辑是否严密？是否存在漏洞或跳跃？
2. 概念使用是否准确？是否有误解？
3. 是否真正理解了概念间的关系，而不仅仅是堆砌术语？

返回JSON格式：
{{
    "verdict": "correct/partial/incorrect",
    "score": 0-100,
    "feedback": "详细的评判反馈，指出优点和不足",
    "weak_links": ["薄弱环节1", "薄弱环节2"],
    "needs_diagnosis": true/false  // 是否需要进入诊断模式
}}

verdict判定标准：
- correct (>=70分): 答案正确且逻辑完整
- partial (40-69分): 部分正确但有缺陷
- incorrect (<40分): 明显错误，需要诊断"""

        try:
            response = await self.ai_client.generate_content(prompt, use_heavy=True, timeout=240)
            result = self._extract_json(response)

            if "error" in result:
                return result

            verdict = result.get("verdict", "incorrect")
            score = result.get("score", 0)

            # 确保 needs_diagnosis 正确设置
            needs_diagnosis = result.get("needs_diagnosis")
            if needs_diagnosis is None:
                needs_diagnosis = verdict != "correct" or score < 80

            return {
                "verdict": verdict,
                "score": score,
                "feedback": result.get("feedback", ""),
                "weak_links": result.get("weak_links", []),
                "needs_diagnosis": needs_diagnosis
            }
        except Exception as e:
            return {"error": f"评判失败: {str(e)}"}

    # ========== 4. 苏格拉底式诊断 ==========

    async def diagnose_error(
        self,
        fusion_id: int,
        user_answer: str,
        reflection: str,
        db: Session
    ) -> Dict[str, Any]:
        """
        答错后的苏格拉底式诊断
        判断是：概念遗忘 还是 关系理解错误
        """
        fusion = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.id == fusion_id,
            WrongAnswerV2.is_fusion == True
        ).first()

        if not fusion:
            return {"error": "融合题不存在"}

        # 获取原题信息
        parent_ids = fusion.parent_ids or []
        parents = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.id.in_(parent_ids)
        ).all()

        parent_concepts = []
        for p in parents:
            parent_concepts.append({
                "id": p.id,
                "key_point": p.key_point,
                "question_text": p.question_text[:100] + "..."
            })

        prompt = f"""请对用户的高阶融合题错误进行诊断：

【融合题】
{fusion.question_text}

【原题概念】
{chr(10).join([f"- {p['key_point']}: {p['question_text']}" for p in parent_concepts])}

【用户答案】
{user_answer}

【用户的自我反思】
{reflection}

请分析：
1. 用户是否真的忘记了某个基础概念（陈述性知识遗忘）？
2. 还是记得基础概念，但搞错了它们之间的关系（关系性知识错误）？

返回JSON格式：
{{
    "diagnosis_type": "concept_forgot" | "relation_error" | "both",
    "affected_concept_ids": [原题ID列表，如果是概念遗忘],
    "analysis": "详细分析用户错误的根源",
    "recommendation": "针对性的改进建议"
}}

diagnosis_type判定：
- concept_forgot: 用户似乎忘记了某个基础概念的定义或机制
- relation_error: 用户记得基础概念，但错误理解了它们如何相互作用
- both: 两者都有问题"""

        try:
            response = await self.ai_client.generate_content(prompt, use_heavy=True, timeout=240)
            result = self._extract_json(response)

            if "error" in result:
                return result

            diagnosis_type = result.get("diagnosis_type", "relation_error")
            affected_ids = result.get("affected_concept_ids", [])

            # 验证 affected_ids 是否合法
            valid_parent_ids = [p.id for p in parents]
            affected_ids = [aid for aid in affected_ids if aid in valid_parent_ids]

            return {
                "diagnosis_type": diagnosis_type,
                "affected_parent_ids": affected_ids,
                "analysis": result.get("analysis", ""),
                "recommendation": result.get("recommendation", ""),
                "fusion_id": fusion_id
            }
        except Exception as e:
            return {"error": f"诊断失败: {str(e)}"}

    # ========== 5. 解锁条件检查 ==========

    def check_unlock_status(
        self,
        question_id: int,
        db: Session
    ) -> Dict[str, Any]:
        """
        检查某题是否满足融合解锁条件：
        - 状态为 archived
        - 连续3次正确 + 信心度100%
        """
        wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == question_id).first()
        if not wa:
            return {"can_unlock": False, "reason": "题目不存在"}

        # 检查状态
        if wa.mastery_status != "archived":
            return {
                "can_unlock": False,
                "reason": "题目尚未归档，请先掌握基础题",
                "consecutive_correct": 0,
                "confidence_sure": False
            }

        # 获取最近的重做记录
        retries = db.query(WrongAnswerRetry).filter(
            WrongAnswerRetry.wrong_answer_id == question_id
        ).order_by(WrongAnswerRetry.retried_at.desc()).all()

        # 检查连续正确次数和信心度
        consecutive_correct = 0
        confidence_sure = True

        for retry in retries:
            if retry.is_correct and retry.confidence == self.UNLOCK_REQUIRED_CONFIDENCE:
                consecutive_correct += 1
            else:
                break  # 连续性中断

            if consecutive_correct >= self.UNLOCK_REQUIRED_CORRECT:
                break

        # 检查是否满足条件
        can_unlock = (
            consecutive_correct >= self.UNLOCK_REQUIRED_CORRECT and
            wa.mastery_status == "archived"
        )

        if can_unlock:
            return {
                "can_unlock": True,
                "consecutive_correct": consecutive_correct,
                "confidence_sure": True
            }
        else:
            reason = f"需要连续{self.UNLOCK_REQUIRED_CORRECT}次正确且信心度100%，目前连续正确{consecutive_correct}次"
            return {
                "can_unlock": False,
                "reason": reason,
                "consecutive_correct": consecutive_correct,
                "confidence_sure": consecutive_correct > 0
            }

    # ========== 6. 严格模式 SM-2 更新 ==========

    def apply_strict_sm2(
        self,
        fusion: WrongAnswerV2,
        is_correct: bool,
        quality: int  # 0-5，答案质量
    ) -> None:
        """
        严格模式 SM-2 算法
        融合题答错时惩罚更重（基于 fusion_level）
        保底阈值：最小复习间隔不小于 MIN_INTERVAL_HOURS
        """
        # 获取当前值
        ef = fusion.sm2_ef or 2.5
        interval = fusion.sm2_interval or 0
        repetitions = fusion.sm2_repetitions or 0
        level = fusion.fusion_level or 1

        # 计算惩罚系数
        penalty = self.PENALTY_FACTORS.get(level, self.DEFAULT_PENALTY)

        # 将最小间隔转换为天数（向上取整）
        min_interval_days = max(1, self.MIN_INTERVAL_HOURS // 24)

        if is_correct and quality >= 3:
            # 答对：正常 SM-2 流程
            if repetitions == 0:
                interval = 1
            elif repetitions == 1:
                interval = 6
            else:
                interval = round(interval * ef)

            repetitions += 1
            ef = max(1.3, ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        else:
            # 答错：应用惩罚系数
            repetitions = 0
            ef = max(1.3, ef - 0.2)

            # 严格模式：interval 除以惩罚系数，但有保底
            new_interval = max(min_interval_days, round(interval / penalty))
            interval = new_interval

        # 更新融合题
        fusion.sm2_ef = ef
        fusion.sm2_interval = interval
        fusion.sm2_repetitions = repetitions
        fusion.sm2_penalty_factor = penalty

        # 计算下次复习日期
        from datetime import date, timedelta
        fusion.next_review_date = date.today() + timedelta(days=interval)


# ========== 单例模式 ==========
_fusion_service: Optional[FusionService] = None


def get_fusion_service() -> FusionService:
    """获取融合服务单例"""
    global _fusion_service
    if _fusion_service is None:
        _fusion_service = FusionService()
    return _fusion_service
