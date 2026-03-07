"""
并发题目生成服务
批量生成10道题，同时生成答案和解析
"""

import asyncio
from typing import List, Dict, Any
from services.ai_client import get_ai_client

class ConcurrentQuizGenerator:
    """并发题目生成器"""
    
    def __init__(self):
        self.ai = get_ai_client()
    
    async def generate_quiz_batch(
        self, 
        concept_names: List[str],
        concept_descriptions: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        并发生成10道题目
        
        Args:
            concept_names: 知识点名称列表（10个）
            concept_descriptions: 知识点描述列表
        
        Returns:
            10道题目的列表
        """
        if concept_descriptions is None:
            concept_descriptions = [""] * len(concept_names)
        
        # 限制为10个
        concept_names = concept_names[:10]
        concept_descriptions = concept_descriptions[:10]
        
        # 创建并发任务
        tasks = []
        for name, desc in zip(concept_names, concept_descriptions):
            task = self._generate_single_quiz(name, desc)
            tasks.append(task)
        
        # 并发执行所有任务
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        quizzes = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # 生成失败，使用默认题目
                quizzes.append(self._create_default_quiz(concept_names[i]))
            else:
                quizzes.append(result)
        
        return quizzes
    
    async def _generate_single_quiz(
        self, 
        concept_name: str, 
        concept_description: str = ""
    ) -> Dict[str, Any]:
        """生成单道题目"""
        
        # JSON格式的提示词
        prompt = self._build_json_prompt(concept_name, concept_description)
        
        schema = {
            "question": "题目内容，清晰的题干",
            "options": {
                "A": "选项A内容",
                "B": "选项B内容", 
                "C": "选项C内容",
                "D": "选项D内容"
            },
            "correct_answer": "正确答案，只能是A/B/C/D之一",
            "explanation": "详细解析，说明为什么正确、为什么错误",
            "key_points": ["考点1", "考点2"],
            "difficulty": "难度等级: easy/medium/hard"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=1500, use_heavy=True, timeout=240)
            result["concept_name"] = concept_name
            return result
        except Exception as e:
            print(f"生成题目失败 [{concept_name}]: {e}")
            raise
    
    def _build_json_prompt(self, concept_name: str, concept_description: str) -> str:
        """构建JSON格式的提示词"""
        
        prompt = f"""你是一位医学考研出题专家。请为以下知识点生成一道西医综合306风格的选择题。

知识点: {concept_name}
{concept_description}

要求:
1. 难度适中，符合考研水平
2. 4个选项，只有1个正确答案
3. 干扰项要有迷惑性，不能明显错误
4. 题干清晰，避免歧义
5. 解析要详细，必须说明:
   - 为什么正确答案是正确的
   - 为什么其他选项是错误的
   - 涉及的关键知识点

输出格式要求:
必须返回有效的JSON格式，包含以下字段:
- question: 题目内容
- options: 包含A、B、C、D四个选项的对象
- correct_answer: 正确答案（只能是"A"、"B"、"C"或"D"）
- explanation: 详细解析（至少100字）
- key_points: 考点列表（数组）
- difficulty: 难度（"easy"、"medium"或"hard"）

注意: 只返回JSON，不要包含任何其他文字说明。"""
        
        return prompt
    
    def _create_default_quiz(self, concept_name: str) -> Dict[str, Any]:
        """创建默认题目（生成失败时使用）"""
        return {
            "question": f"关于{concept_name}，以下说法正确的是？",
            "options": {
                "A": f"{concept_name}是重要的医学概念",
                "B": f"{concept_name}与疾病无关", 
                "C": f"{concept_name}不需要掌握",
                "D": f"以上说法都不正确"
            },
            "correct_answer": "A",
            "explanation": f"本题考查{concept_name}的基本概念。正确答案是A。{concept_name}是医学学习中的重要知识点，需要重点掌握。",
            "key_points": [concept_name, "基本概念"],
            "difficulty": "medium",
            "concept_name": concept_name,
            "is_default": True
        }


class BatchGrader:
    """批量批改器"""
    
    def __init__(self):
        self.ai = get_ai_client()
    
    async def grade_batch(
        self,
        quizzes: List[Dict[str, Any]],
        answers: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        批量批改10道题的答案
        
        Args:
            quizzes: 10道题目的列表
            answers: 10个答案的列表
        
        Returns:
            10个批改结果
        """
        # 创建并发任务
        tasks = []
        for quiz, answer in zip(quizzes, answers):
            task = self._grade_single(quiz, answer)
            tasks.append(task)
        
        # 并发执行
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        graded_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # 批改失败，使用默认结果
                graded_results.append(self._create_default_grade(quizzes[i], answers[i]))
            else:
                graded_results.append(result)
        
        return graded_results
    
    async def _grade_single(
        self,
        quiz: Dict[str, Any],
        answer: Dict[str, Any]
    ) -> Dict[str, Any]:
        """批改单道题目"""
        _user = (answer.get("user_answer") or "").strip().upper()
        _correct = (quiz.get("correct_answer") or "").strip().upper()
        if quiz.get("type") == "X":
            is_correct = sorted(_user) == sorted(_correct)
        else:
            is_correct = _user == _correct
        
        # 如果答对且确定，不需要AI分析
        if is_correct and answer.get("confidence") == "sure":
            return {
                "is_correct": True,
                "score": 100,
                "feedback": "回答正确！你对这个知识点掌握得很好。",
                "weak_points": [],
                "suggestion": "继续保持",
                "error_type": None,
                "confidence_analysis": "高信心+正确=真正掌握"
            }
        
        # 需要AI深度分析
        prompt = self._build_grade_prompt(quiz, answer)
        
        schema = {
            "is_correct": "是否正确 (true/false)",
            "score": "得分 (0-100)",
            "feedback": "总体反馈（鼓励性）",
            "weak_points": ["薄弱点1", "薄弱点2"],
            "suggestion": "具体学习建议",
            "error_type": "错误类型: knowledge_gap(知识漏洞)/misunderstanding(理解错误)/careless(粗心)",
            "confidence_analysis": "信心度分析"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=1000, use_heavy=True, timeout=240)
            return result
        except Exception as e:
            print(f"批改失败: {e}")
            raise
    
    def _build_grade_prompt(
        self,
        quiz: Dict[str, Any],
        answer: Dict[str, Any]
    ) -> str:
        """构建批改提示词"""
        
        prompt = f"""请分析学生的答题情况。

题目: {quiz.get('question')}
选项:
A. {quiz.get('options', {}).get('A', '')}
B. {quiz.get('options', {}).get('B', '')}
C. {quiz.get('options', {}).get('C', '')}
D. {quiz.get('options', {}).get('D', '')}

正确答案: {quiz.get('correct_answer')}
学生答案: {answer.get('user_answer')}
信心度: {answer.get('confidence', 'unsure')}

题目解析: {quiz.get('explanation', '')}

请分析:
1. 学生为什么选错？（概念混淆？记忆不清？审题错误？）
2. 哪些具体知识点没有掌握？
3. 给出针对性的学习建议
4. 错误类型判断

输出JSON格式:
- is_correct: 是否正确 (true/false)
- score: 得分 (0-100)
- feedback: 鼓励性反馈
- weak_points: 薄弱环节数组
- suggestion: 具体建议
- error_type: 错误类型
- confidence_analysis: 信心度与正确性匹配分析

只返回JSON，不要其他文字。"""
        
        return prompt
    
    def _create_default_grade(
        self,
        quiz: Dict[str, Any],
        answer: Dict[str, Any]
    ) -> Dict[str, Any]:
        """创建默认批改结果"""
        # 清理答案：去除空格、点号、逗号等，只保留字母A-E
        import re
        _user = re.sub(r'[^A-E]', '', (answer.get("user_answer") or "").strip().upper())
        _correct = re.sub(r'[^A-E]', '', (quiz.get("correct_answer") or "").strip().upper())

        if quiz.get("type") == "X":
            is_correct = sorted(_user) == sorted(_correct)
        else:
            is_correct = _user == _correct
        return {
            "is_correct": is_correct,
            "score": 100 if is_correct else 0,
            "feedback": "回答正确！" if is_correct else "回答错误，建议复习相关知识点。",
            "weak_points": [],
            "suggestion": "继续学习",
            "error_type": None if is_correct else "unknown",
            "confidence_analysis": "正常"
        }


class AIAnalyzer:
    """AI总结分析器"""
    
    def __init__(self):
        self.ai = get_ai_client()
    
    async def analyze_session(
        self,
        quizzes: List[Dict[str, Any]],
        graded_results: List[Dict[str, Any]],
        answers: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        分析整个测验会话
        
        Args:
            quizzes: 10道题目
            graded_results: 10个批改结果
            answers: 10个答案
        
        Returns:
            综合分析报告
        """
        # 统计数据
        correct_count = sum(1 for r in graded_results if r.get("is_correct"))
        score = int(correct_count / 10 * 100)
        
        # 收集所有薄弱点
        all_weak_points = []
        for r in graded_results:
            all_weak_points.extend(r.get("weak_points", []))
        
        # 统计信心度
        confidence_stats = {"sure": 0, "unsure": 0, "dont_know": 0}
        for a in answers:
            conf = a.get("confidence", "unsure")
            confidence_stats[conf] = confidence_stats.get(conf, 0) + 1
        
        # 构建分析提示词
        prompt = self._build_analysis_prompt(
            quizzes, graded_results, answers,
            correct_count, score, all_weak_points, confidence_stats
        )
        
        schema = {
            "overall_assessment": "总体评价",
            "strengths": ["优势1", "优势2"],
            "weaknesses": ["劣势1", "劣势2"],
            "study_recommendations": ["建议1", "建议2", "建议3"],
            "priority_topics": ["优先复习1", "优先复习2"],
            "next_steps": "下一步行动计划"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=1500, use_heavy=True, timeout=360)
            result["score"] = score
            result["correct_count"] = correct_count
            result["wrong_count"] = 10 - correct_count
            result["weak_points_summary"] = list(set(all_weak_points))
            return result
        except Exception as e:
            print(f"分析失败: {e}")
            return self._create_default_analysis(score, correct_count, all_weak_points)
    
    def _build_analysis_prompt(
        self,
        quizzes: List[Dict[str, Any]],
        graded_results: List[Dict[str, Any]],
        answers: List[Dict[str, Any]],
        correct_count: int,
        score: int,
        weak_points: List[str],
        confidence_stats: Dict[str, int]
    ) -> str:
        """构建分析提示词"""
        
        # 构建答题情况摘要
        answer_summary = []
        for i, (q, r, a) in enumerate(zip(quizzes, graded_results, answers)):
            status = "正确" if r.get("is_correct") else "错误"
            answer_summary.append(
                f"{i+1}. {q.get('concept_name')} - {status} "
                f"(信心:{a.get('confidence')}, 答案:{a.get('user_answer')})"
            )
        
        prompt = f"""请分析学生的测验表现，给出专业的学习建议。

测验统计:
- 得分: {score}/100
- 正确: {correct_count}/10
- 错误: {10-correct_count}/10

信心度分布:
- 确定会: {confidence_stats.get('sure', 0)}题
- 有点模糊: {confidence_stats.get('unsure', 0)}题
- 完全不会: {confidence_stats.get('dont_know', 0)}题

答题情况:
{chr(10).join(answer_summary)}

薄弱点汇总:
{chr(10).join([f"- {wp}" for wp in set(weak_points)])}

请输出JSON格式分析:
- overall_assessment: 总体评价（100字左右）
- strengths: 优势数组
- weaknesses: 劣势数组
- study_recommendations: 学习建议数组（3-5条）
- priority_topics: 优先复习的知识点
- next_steps: 下一步行动计划

只返回JSON。"""
        
        return prompt
    
    def _create_default_analysis(
        self,
        score: int,
        correct_count: int,
        weak_points: List[str]
    ) -> Dict[str, Any]:
        """创建默认分析报告"""
        return {
            "overall_assessment": f"本次测验得分{score}分，答对{correct_count}题。建议继续努力学习。",
            "strengths": ["完成了测验"],
            "weaknesses": ["存在知识漏洞"],
            "study_recommendations": ["复习错题", "加强薄弱环节"],
            "priority_topics": list(set(weak_points))[:3],
            "next_steps": "复习错题后继续练习",
            "score": score,
            "correct_count": correct_count,
            "wrong_count": 10 - correct_count,
            "weak_points_summary": list(set(weak_points))
        }


# 单例实例
_concurrent_generator = None
_batch_grader = None
_ai_analyzer = None

def get_concurrent_generator():
    global _concurrent_generator
    if _concurrent_generator is None:
        _concurrent_generator = ConcurrentQuizGenerator()
    return _concurrent_generator

def get_batch_grader():
    global _batch_grader
    if _batch_grader is None:
        _batch_grader = BatchGrader()
    return _batch_grader

def get_ai_analyzer():
    global _ai_analyzer
    if _ai_analyzer is None:
        _ai_analyzer = AIAnalyzer()
    return _ai_analyzer
