"""
出题与批改服务
AI生成题目、自动批改、分析薄弱点
"""

from typing import Dict, Any, List, Optional
from services.ai_client import get_ai_client
from utils.helpers import calculate_next_review, analyze_confidence_accuracy


class QuizService:
    """出题与批改服务"""
    
    def __init__(self):
        self.ai = get_ai_client()
    
    async def generate_quiz(
        self, 
        concept_name: str, 
        concept_description: str = ""
    ) -> Dict[str, Any]:
        """
        生成选择题
        
        Args:
            concept_name: 知识点名称
            concept_description: 知识点描述
        
        Returns:
            {
                "question": "题干",
                "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
                "correct_answer": "A",
                "explanation": "详细解析"
            }
        """
        prompt = f"""基于医学知识点生成一道考研风格的选择题。

知识点: {concept_name}
{concept_description}

要求：
1. 难度适中（西医综合306水平）
2. 4个选项，只有1个正确答案
3. 干扰项要有迷惑性
4. 题干要清晰，避免歧义
5. 解析简洁明了（100字以内）

【输出JSON格式】
{{
    "question": "题目内容",
    "options": {{"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"}},
    "correct_answer": "A/B/C/D",
    "explanation": "解析"
}}"""
        
        schema = {
            "question": "题目",
            "options": {"A": "", "B": "", "C": "", "D": ""},
            "correct_answer": "A/B/C/D",
            "explanation": "解析"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=2000, temperature=0.3, timeout=60)
            # 验证返回结果包含有效选项
            if not result.get("options") or not all(result["options"].values()):
                raise ValueError("AI返回了无效选项")
            return result
        except Exception as e:
            print(f"出题错误: {e}")
            # 返回有意义的默认题目，而不是占位符
            return {
                "question": f"关于{concept_name}，以下说法正确的是？",
                "options": {
                    "A": f"{concept_name}是考试重点内容",
                    "B": f"{concept_name}与临床表现密切相关",
                    "C": f"{concept_name}的机制尚不明确",
                    "D": f"{concept_name}不需要掌握"
                },
                "correct_answer": "A",
                "explanation": f"本题考查{concept_name}。{concept_name}是西医综合的重要考点，需要重点掌握。"
            }
    
    async def grade_answer(
        self,
        question: str,
        options: Dict[str, str],
        correct_answer: str,
        user_answer: str,
        confidence: str
    ) -> Dict[str, Any]:
        """
        批改答案并给出反馈

        Args:
            question: 题目
            options: 选项
            correct_answer: 正确答案
            user_answer: 用户答案
            confidence: 信心度 (sure/unsure/no)

        Returns:
            {
                "is_correct": True/False,
                "score": 0-100,
                "feedback": "反馈建议",
                "weak_points": ["薄弱点1", "薄弱点2"],
                "suggestion": "学习建议"
            }
        """
        # 清理答案：去除空格、点号、逗号等，只保留字母A-E
        import re
        clean_user = re.sub(r'[^A-E]', '', (user_answer or "").strip().upper())
        clean_correct = re.sub(r'[^A-E]', '', (correct_answer or "").strip().upper())
        is_correct = clean_user == clean_correct
        
        # 基础得分
        score = 100 if is_correct else 0
        
        # 信心度调整
        confidence_analysis = analyze_confidence_accuracy(is_correct, confidence)
        
        # 如果有特殊问题，调用AI分析
        if not is_correct or confidence in {"unsure", "no"}:
            return await self._analyze_mistake(
                question, options, correct_answer, 
                user_answer, confidence, score
            )
        
        # 正确且自信，简单反馈
        return {
            "is_correct": True,
            "score": score,
            "feedback": "回答正确！你对这个知识点掌握得很好。",
            "weak_points": [],
            "suggestion": "继续保持，按计划复习即可。"
        }
    
    async def _analyze_mistake(
        self,
        question: str,
        options: Dict[str, str],
        correct_answer: str,
        user_answer: str,
        confidence: str,
        score: int
    ) -> Dict[str, Any]:
        """深度分析错误原因"""
        
        prompt = f"""分析学生的答题情况，找出薄弱环节。

题目: {question}
选项:
A. {options['A']}
B. {options['B']}
C. {options['C']}
D. {options['D']}

正确答案: {correct_answer}
学生答案: {user_answer}
学生信心度: {confidence}

请分析：
1. 学生为什么会选错？（是概念混淆？记忆不清？审题错误？）
2. 哪些具体知识点没有掌握？
3. 给出针对性的学习建议

输出JSON格式，包含:
- feedback: 总体反馈（鼓励性）
- weak_points: 薄弱环节列表（2-4个）
- suggestion: 具体学习建议"""
        
        schema = {
            "feedback": "鼓励性反馈，指出问题但保持积极",
            "weak_points": ["薄弱知识点1", "薄弱知识点2"],
            "suggestion": "具体可操作的学习建议"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=1000, timeout=60)
            result["is_correct"] = (user_answer or "").strip().upper() == (correct_answer or "").strip().upper()
            result["score"] = score
            return result
        except Exception as e:
            print(f"分析错误: {e}")
            return {
                "is_correct": False,
                "score": score,
                "feedback": "回答错误，建议回顾相关知识点。",
                "weak_points": ["概念理解不清晰"],
                "suggestion": "建议重新学习该知识点的基础内容。"
            }


# 单例实例
_quiz_service: QuizService = None


def get_quiz_service() -> QuizService:
    """获取出题服务"""
    global _quiz_service
    if _quiz_service is None:
        _quiz_service = QuizService()
    return _quiz_service
