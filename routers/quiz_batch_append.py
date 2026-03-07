

# 单独存储用于细节练习的数据（不删除）
_detail_cache = {}

class GenerateVariationRequest(BaseModel):
    key_point: str
    base_question: dict
    uploaded_content: str = ""
    num_variations: int = 5

@router.post("/generate-variations")
async def generate_variation_questions(
    request: GenerateVariationRequest,
    db: Session = Depends(get_db)
):
    """
    基于知识点生成变式题
    
    Args:
        request: 包含知识点、原题、内容等
        
    Returns:
        5道变式题
    """
    print(f"[Variation] 生成变式题: {request.key_point}")
    
    quiz_service = get_quiz_service()
    
    try:
        # 调用服务生成变式题
        variations = await quiz_service.generate_variation_questions(
            key_point=request.key_point,
            base_question=request.base_question,
            uploaded_content=request.uploaded_content,
            num_variations=request.num_variations
        )
        
        print(f"[Variation] 生成成功: {len(variations)} 道变式题")
        return {"variations": variations}
        
    except Exception as e:
        print(f"[Variation] 生成失败: {e}")
        import traceback
        traceback.print_exc()
        
        # 返回默认变式（简单修改）
        default_variations = []
        for i in range(5):
            default_variations.append({
                "id": i + 1,
                "type": request.base_question.get("type", "A1"),
                "difficulty": request.base_question.get("difficulty", "基础"),
                "question": f"【变式{i+1}】{request.base_question.get('question', '')}",
                "options": request.base_question.get("options", {}),
                "correct_answer": request.base_question.get("correct_answer", "A"),
                "explanation": request.base_question.get("explanation", "")
            })
        return {"variations": default_variations}
