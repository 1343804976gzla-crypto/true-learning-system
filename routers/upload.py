"""
上传与识别路由
处理讲课内容上传和AI识别
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional

from models import get_db, DailyUpload, Chapter, ConceptMastery
from schemas import ContentUpload, UploadResponse
from services.content_parser_v2 import get_content_parser

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("", response_model=UploadResponse)
async def upload_content(
    data: ContentUpload,
    db: Session = Depends(get_db)
):
    """
    上传讲课内容，AI自动识别章节和知识点
    注意：大内容解析可能需要30-120秒，请耐心等待
    """
    import time
    start_time = time.time()
    content_length = len(data.content)
    
    print(f"[Upload] 开始处理上传，内容长度: {content_length} 字符")

    try:
        # 0. 内容质量检查
        if not data.content or not data.content.strip():
            raise HTTPException(status_code=400, detail="上传内容不能为空")

        # 检查是否为乱码(大量问号或特殊字符)
        question_mark_ratio = data.content.count('?') / max(len(data.content), 1)
        if question_mark_ratio > 0.5:
            raise HTTPException(
                status_code=400,
                detail="上传内容疑似乱码,请检查文本编码或重新复制内容"
            )

        # 检查是否有足够的中文字符
        chinese_chars = sum(1 for c in data.content if '\u4e00' <= c <= '\u9fff')
        if chinese_chars < 10:
            raise HTTPException(
                status_code=400,
                detail="上传内容中中文字符过少,请确认内容是否正确"
            )

        # 1. 解析日期
        upload_date = date.today()
        if data.date:
            try:
                upload_date = date.fromisoformat(data.date)
            except ValueError:
                pass

        print(f"[Upload] 日期: {upload_date}")
        
        # 2. AI识别内容（使用知识库匹配）
        print(f"[Upload] 开始AI识别...")
        parser = get_content_parser()
        extracted = await parser.parse_content_with_knowledge(data.content, db=db)
        
        parse_time = time.time() - start_time
        print(f"[Upload] AI识别完成，耗时: {parse_time:.1f}秒")
        print(f"[Upload] 识别结果: {extracted.get('book')} - {extracted.get('chapter_title')}")
        print(f"[Upload] 知识点数: {len(extracted.get('concepts', []))}")
        
        # 3. 保存到daily_uploads（日期轨道）
        upload_record = DailyUpload(
            date=upload_date,
            raw_content=data.content,
            ai_extracted=extracted
        )
        db.add(upload_record)
        db.flush()  # 获取ID
        
        # 4. 保存/更新chapters（章节轨道）
        chapter_id = extracted.get("chapter_id")
        
        existing_chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        
        if existing_chapter:
            print(f"[Upload] 更新已有章节: {chapter_id}")
            # 合并知识点
            existing_concepts = existing_chapter.concepts or []
            new_concepts = extracted.get("concepts", [])
            
            # 合并，去重
            concept_dict = {c["id"]: c for c in existing_concepts}
            for c in new_concepts:
                concept_dict[c["id"]] = c
            
            existing_chapter.concepts = list(concept_dict.values())

            # 为新增知识点补建 ConceptMastery，避免“章节有概念但题库查不到知识点”
            existing_mastery_ids = {
                x[0] for x in db.query(ConceptMastery.concept_id).filter(
                    ConceptMastery.chapter_id == chapter_id
                ).all()
            }
            for concept in new_concepts:
                concept_id = concept.get("id")
                concept_name = (concept.get("name") or "").strip()
                if not concept_id or not concept_name:
                    continue
                if concept_id in existing_mastery_ids:
                    continue
                db.add(ConceptMastery(
                    concept_id=concept_id,
                    chapter_id=chapter_id,
                    name=concept_name,
                    retention=0.0,
                    understanding=0.0,
                    application=0.0
                ))
                existing_mastery_ids.add(concept_id)
            
            # 更新摘要（如果有新的）
            if extracted.get("summary"):
                existing_chapter.content_summary = extracted["summary"]
        else:
            print(f"[Upload] 创建新章节: {chapter_id}")
            # 创建新章节
            new_chapter = Chapter(
                id=chapter_id,
                book=extracted["book"],
                edition=extracted.get("edition"),
                chapter_number=extracted["chapter_number"],
                chapter_title=extracted["chapter_title"],
                content_summary=extracted.get("summary"),
                concepts=extracted.get("concepts", []),
                first_uploaded=upload_date
            )
            db.add(new_chapter)
            
            # 创建知识点掌握记录
            for concept in extracted.get("concepts", []):
                concept_record = ConceptMastery(
                    concept_id=concept["id"],
                    chapter_id=chapter_id,
                    name=concept["name"],
                    retention=0.0,
                    understanding=0.0,
                    application=0.0
                )
                db.add(concept_record)
        
        db.commit()
        
        total_time = time.time() - start_time
        print(f"[Upload] 处理完成，总耗时: {total_time:.1f}秒")
        
        return UploadResponse(
            upload_id=upload_record.id,
            date=upload_date,
            extracted=extracted,
            message=f"成功识别：{extracted['book']} - {extracted['chapter_title']}（耗时{total_time:.1f}秒）"
        )
        
    except ValueError as e:
        db.rollback()
        if "DEEPSEEK_API_KEY" in str(e) or "GEMINI_API_KEY" in str(e):
            raise HTTPException(
                status_code=503,
                detail="AI服务未配置，请设置 DEEPSEEK_API_KEY"
            )
        raise HTTPException(status_code=500, detail=str(e))
    except RuntimeError as e:
        db.rollback()
        if "DEEPSEEK_API_KEY" in str(e) or "GEMINI_API_KEY" in str(e):
            raise HTTPException(
                status_code=503,
                detail=str(e)
            )
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        db.rollback()
        print(f"[Upload] 处理失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")
