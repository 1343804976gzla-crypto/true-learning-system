"""
费曼讲解服务
多轮对话验证理解深度
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from services.ai_client import get_ai_client


class FeynmanService:
    """费曼讲解服务"""
    
    def __init__(self):
        self.ai = get_ai_client()
        self.active_sessions: Dict[int, Dict] = {}  # 内存中保持会话状态

    def _create_session(self, concept_id: str, concept_name: str) -> int:
        """创建并注册会话，保证无论 AI 状态如何都能进入对话流程。"""
        session_id = id(self) + hash(concept_id + str(datetime.now()))
        self.active_sessions[session_id] = {
            "concept_id": concept_id,
            "concept_name": concept_name,
            "history": [],
            "round": 0,
            "max_rounds": 5
        }
        return session_id
    
    async def start_session(self, concept_id: str, concept_name: str) -> Dict[str, Any]:
        """
        开始费曼讲解会话
        
        Returns:
            {
                "session_id": 会话ID,
                "message": AI初始消息
            }
        """
        prompt = f"""你是费曼教练，要用"费曼技巧"帮助用户理解概念。

概念: {concept_name}

规则：
1. 先让用户用大白话解释（禁止专业术语）
2. 如果用户用了术语，追问"能用更简单的话说吗？"
3. 如果解释不清，要求"举个例子"
4. 通过标准：能用10岁小孩听懂的语言讲清楚因果关系

请生成开场白，要求用户解释这个概念。"""
        
        # 先创建会话，避免 AI 初始化失败时前端拿到 0 导致流程中断。
        session_id = self._create_session(concept_id=concept_id, concept_name=concept_name)

        try:
            message = await self.ai.generate_content(prompt, max_tokens=500, temperature=0.7, timeout=60)
            return {
                "session_id": session_id,
                "message": message.strip()
            }
        except Exception as e:
            print(f"启动费曼会话错误: {e}")
            return {
                "session_id": session_id,
                "message": f"请用大白话解释'{concept_name}'，就像在给10岁表弟讲课一样。"
            }
    
    async def process_response(
        self, 
        session_id: int, 
        user_message: str
    ) -> Dict[str, Any]:
        """
        处理用户回复，继续对话
        
        Returns:
            {
                "finished": True/False,
                "passed": True/False,
                "message": AI回复,
                "round": 当前轮次,
                "terminology_detected": [检测到的术语]
            }
        """
        session = self.active_sessions.get(session_id)
        if not session:
            return {
                "finished": True,
                "passed": False,
                "message": "会话已过期，请重新开始。",
                "round": 0
            }
        
        # 更新历史
        session["history"].append({"role": "user", "content": user_message})
        session["round"] += 1
        
        # 评估用户解释
        prompt = f"""评估用户的解释是否通过费曼测试。

概念: {session['concept_name']}

对话历史:
{self._format_history(session['history'])}

用户最新回复: {user_message}

判断标准：
1. 是否完全没有专业术语？
2. 因果关系是否清晰？
3. 是否用了恰当的比喻或例子？
4. 10岁小孩能听懂吗？

如果通过，finished=true。
如果没通过，finished=false，给出具体反馈和追问。

输出JSON:
{{
    "finished": false,
    "passed": false,
    "terminology_detected": ["术语1", "术语2"],
    "feedback": "具体反馈",
    "followup": "追问或建议",
    "round": {session['round']}
}}"""
        
        schema = {
            "finished": "是否完成 (boolean)",
            "passed": "是否通过 (boolean)",
            "terminology_detected": ["检测到的术语列表"],
            "feedback": "对解释的反馈",
            "followup": "下一步追问或建议",
            "round": "当前轮次 (number)"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=800, timeout=60)
            
            # 更新历史
            session["history"].append({
                "role": "assistant", 
                "content": result["followup"]
            })
            
            # 检查是否完成（通过或达到最大轮次）
            if result["finished"] or session["round"] >= session["max_rounds"]:
                if session["round"] >= session["max_rounds"] and not result["finished"]:
                    result["finished"] = True
                    result["passed"] = False
                    result["followup"] = "虽然还没有完全达到标准，但你的努力值得肯定！建议再复习一下基础概念。"
                
                # 清理会话
                del self.active_sessions[session_id]
            
            return {
                "finished": result["finished"],
                "passed": result["passed"],
                "message": result["followup"],
                "round": session["round"],
                "terminology_detected": result.get("terminology_detected", [])
            }
            
        except Exception as e:
            print(f"处理回复错误: {e}")
            return {
                "finished": False,
                "passed": False,
                "message": "请继续解释，试着用更简单的语言。",
                "round": session["round"],
                "terminology_detected": []
            }
    
    def _format_history(self, history: List[Dict]) -> str:
        """格式化对话历史"""
        formatted = []
        for msg in history[-4:]:  # 只取最近4条，避免token过多
            role = "用户" if msg["role"] == "user" else "AI"
            formatted.append(f"{role}: {msg['content'][:200]}")
        return "\n".join(formatted)


# 单例实例
_feynman_service: FeynmanService = None


def get_feynman_service() -> FeynmanService:
    """获取费曼讲解服务"""
    global _feynman_service
    if _feynman_service is None:
        _feynman_service = FeynmanService()
    return _feynman_service
