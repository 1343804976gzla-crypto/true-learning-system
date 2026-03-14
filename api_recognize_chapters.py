"""为错题本添加批量章节识别API"""

# 在 routers/wrong_answers_v2.py 中添加以下代码

@router.post("/recognize-chapters")
async def recognize_chapters_for_wrong_answers(
    batch_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    批量为未分类错题识别章节

    Args:
        batch_size: 每批处理的数量（1-100）
    """
    from services.content_parser_v2 import get_content_parser

    # 获取未分类的错题
    uncategorized = db.query(WrongAnswerV2).filter(
        (WrongAnswerV2.chapter_id.like('%未分类%')) |
        (WrongAnswerV2.chapter_id.like('%ch0%')) |
        (WrongAnswerV2.chapter_id == None)
    ).limit(batch_size).all()

    if not uncategorized:
        return {
            "success": True,
            "message": "没有需要识别的错题",
            "total": 0,
            "recognized": 0,
            "failed": 0
        }

    parser = get_content_parser()
    recognized_count = 0
    failed_count = 0

    for wrong in uncategorized:
        # 构建识别内容
        content = f"{wrong.key_point or ''}\n\n{wrong.question_text[:500]}"

        try:
            # 使用章节识别功能
            result = await parser.parse_content(content)

            chapter_id = result.get('chapter_id', '')

            # 检查识别结果是否有效
            if chapter_id and chapter_id not in ['unknown_ch0', '未知_ch0', '无法识别_ch0']:
                # 更新章节ID
                wrong.chapter_id = chapter_id
                recognized_count += 1
            else:
                failed_count += 1

        except Exception as e:
            print(f"[RecognizeChapters] 错题ID {wrong.id} 识别失败: {e}")
            failed_count += 1

    db.commit()

    return {
        "success": True,
        "message": f"识别完成：成功 {recognized_count} 题，失败 {failed_count} 题",
        "total": len(uncategorized),
        "recognized": recognized_count,
        "failed": failed_count
    }
