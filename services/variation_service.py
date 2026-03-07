"""
变式题生成服务
3层难度变式题生成
"""

from typing import Dict, Any, List
from services.ai_client import get_ai_client


class VariationService:
    """变式题生成服务"""
    
    def __init__(self):
        self.ai = get_ai_client()
    
    async def generate_variation(
        self,
        concept_name: str,
        level: int
    ) -> Dict[str, Any]:
        """
        生成变式题
        
        Args:
            concept_name: 知识点名称
            level: 难度层级 (1=直接变式, 2=情境应用, 3=综合)
        
        Returns:
            {
                "question": "题目",
                "answer": "答案",
                "explanation": "解析"
            }
        """
        level_prompts = {
            1: """
生成【直接变式】题：
- 只是换一种问法，难度相同
- 考查相同的知识点，但表述不同
- 不要增加新的情境
- 适合巩固基础概念
""",
            2: """
生成【情境应用】题：
- 给出一个临床场景或病例
- 要求应用该概念分析问题
- 增加简单的情境信息
- 适合训练应用能力
""",
            3: """
生成【综合变式】题：
- 需要结合其他相关知识
- 可能是多步骤推理
- 或与其他概念对比
- 适合提升综合思维能力
"""
        }
        
        prompt = f"""基于医学知识点生成变式题。

知识点: {concept_name}
难度层级: {level}
{level_prompts.get(level, level_prompts[1])}

要求：
1. 医学考研（西综306）难度
2. 4个选项，单选题
3. 干扰项有迷惑性
4. 解析要详细

输出JSON格式。"""
        
        schema = {
            "question": "题目内容（如果是情境题，要有完整的背景）",
            "options": {
                "A": "选项A",
                "B": "选项B",
                "C": "选项C",
                "D": "选项D"
            },
            "correct_answer": "正确选项（A/B/C/D）",
            "answer": "答案详细解释",
            "explanation": "完整解析，包括解题思路"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=1500, use_heavy=True, timeout=300)
            return result
        except Exception as e:
            print(f"生成变式题错误: {e}")
            return {
                "question": f"关于{concept_name}的变式题（难度{level}）",
                "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
                "correct_answer": "A",
                "answer": "答案",
                "explanation": "解析生成失败"
            }


# 单例实例
_variation_service: VariationService = None


def get_variation_service() -> VariationService:
    """获取变式题服务"""
    global _variation_service
    if _variation_service is None:
        _variation_service = VariationService()
    return _variation_service
