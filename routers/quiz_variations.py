"""
变式题生成API
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict

router = APIRouter(prefix="/api/quiz", tags=["quiz_variations"])


class VariationRequest(BaseModel):
    key_point: str
    original_question: str
    original_options: Dict[str, str]
    correct_answer: str
    explanation: str
    content: str
    num_variations: int = 5


@router.post("/variations")
async def generate_variations(request: VariationRequest):
    """
    为指定知识点生成变式题
    """
    from services.quiz_service_v2 import get_quiz_service
    from services.ai_client import get_ai_client

    try:
        quiz_service = get_quiz_service()
        ai = quiz_service.ai

        prompt = f"""【任务】基于以下原题和知识点，生成{request.num_variations}道变式题。

【核心考点】{request.key_point}

【原题】
{request.original_question}
A. {request.original_options.get('A', '')}
B. {request.original_options.get('B', '')}
C. {request.original_options.get('C', '')}
D. {request.original_options.get('D', '')}
E. {request.original_options.get('E', '')}
答案: {request.correct_answer}

【解析】
{request.explanation}

【相关知识点内容】
{request.content[:5000]}

【变式题要求】
1. 每道题都要围绕同一核心考点{request.key_point}
2. 但要变换出题角度（病例情境、数据变化、鉴别诊断、机制分析等）
3. 选项要重新设计，保持干扰性
4. 题型保持一致
5. 答案和解析要正确

【输出格式 - 严格JSON】
{{
    "questions": [
        {{
            "id": 1,
            "type": "A1/A2/A3/X",
            "question": "变式题题目内容",
            "options": {{"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}},
            "correct_answer": "A/B/C/D/E",
            "explanation": "解析内容"
        }}
    ]
}}"""

        schema = {
            "questions": [
                {
                    "id": "题号",
                    "type": "题型",
                    "question": "题目",
                    "options": {"A": "", "B": "", "C": "", "D": "", "E": ""},
                    "correct_answer": "答案",
                    "explanation": "解析"
                }
            ]
        }

        result = await ai.generate_json(prompt, schema, max_tokens=4000, temperature=0.4, use_heavy=True, timeout=360)

        # 确保选项完整
        for q in result.get("questions", []):
            for opt in ["A", "B", "C", "D", "E"]:
                if opt not in q.get("options", {}) or not q["options"][opt]:
                    q["options"][opt] = f"选项{opt}"

        return result

    except Exception as e:
        print(f"[Variations] 生成变式题失败: {e}")
        import traceback
        traceback.print_exc()
        return {"questions": []}
