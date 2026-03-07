"""
费曼讲解路由
多轮对话验证理解
"""

from fastapi import APIRouter, Depends, HTTPException, Form
from sqlalchemy.orm import Session

from models import get_db, ConceptMastery, FeynmanSession
from schemas import (
    FeynmanStartResponse,
    FeynmanRespondResponse
)
from services import get_feynman_service

router = APIRouter(prefix="/api/feynman", tags=["feynman"])


@router.post("/start/{concept_id}", response_model=FeynmanStartResponse)
async def start_feynman(
    concept_id: str,
    db: Session = Depends(get_db)
):
    """
    开始费曼讲解会话
    """
    # 获取知识点
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == concept_id
    ).first()
    
    if not concept:
        raise HTTPException(status_code=404, detail="知识点不存在")
    
    # 启动会话
    feynman_service = get_feynman_service()
    result = await feynman_service.start_session(
        concept_id=concept_id,
        concept_name=concept.name
    )
    
    return FeynmanStartResponse(
        session_id=result["session_id"],
        concept_name=concept.name,
        ai_message=result["message"]
    )


@router.post("/respond/{session_id}", response_model=FeynmanRespondResponse)
async def respond_feynman(
    session_id: int,
    message: str = Form(..., min_length=1),
    db: Session = Depends(get_db)
):
    """
    用户回复，继续费曼讲解对话
    """
    feynman_service = get_feynman_service()
    
    result = await feynman_service.process_response(
        session_id=session_id,
        user_message=message
    )
    
    # 如果完成且通过，更新知识点掌握度
    if result["finished"] and result["passed"]:
        # 从session_id反查concept_id
        # 这里简化处理，实际应该从session中保存concept_id
        pass
    
    return FeynmanRespondResponse(
        session_id=session_id,
        finished=result["finished"],
        passed=result["passed"],
        message=result["message"],
        round=result["round"],
        terminology_detected=result.get("terminology_detected")
    )
