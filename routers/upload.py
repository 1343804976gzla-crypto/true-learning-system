"""
Upload routes.

This file keeps the legacy chapter-recognition upload endpoint for backward
compatibility and adds the new knowledge-upload workspace API used by
`/upload`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from models import Chapter, ConceptMastery, DailyUpload, get_db
from schemas import ContentUpload, UploadResponse
from services.chapter_review_service import sync_review_chapter_from_upload
from services.content_parser_v2 import get_content_parser
from services.data_identity import resolve_request_actor_scope
from services.knowledge_upload_service import get_knowledge_upload_service

router = APIRouter(prefix="/api/upload", tags=["upload"])
logger = logging.getLogger(__name__)


@router.post("", response_model=UploadResponse)
async def upload_content(
    data: ContentUpload,
    db: Session = Depends(get_db),
):
    """
    Legacy upload endpoint.

    It still parses one block of text into chapter/concept data and stores the
    result in `daily_uploads`, `chapters`, and `concept_mastery`.
    """
    import time

    start_time = time.time()
    content_length = len(data.content)
    logger.info("[Upload] start legacy parse, content_length=%d", content_length)

    try:
        if not data.content or not data.content.strip():
            raise HTTPException(status_code=400, detail="上传内容不能为空")

        question_mark_ratio = data.content.count("?") / max(len(data.content), 1)
        if question_mark_ratio > 0.5:
            raise HTTPException(status_code=400, detail="上传内容疑似乱码，请检查编码后重试")

        chinese_chars = sum(1 for c in data.content if "\u4e00" <= c <= "\u9fff")
        if chinese_chars < 10:
            raise HTTPException(status_code=400, detail="内容中的中文字符过少，无法可靠识别")

        upload_date = date.today()
        if data.date:
            try:
                upload_date = date.fromisoformat(data.date)
            except ValueError:
                pass

        parser = get_content_parser()
        extracted = await parser.parse_content_with_knowledge(data.content, db=db)

        upload_record = DailyUpload(
            date=upload_date,
            raw_content=data.content,
            ai_extracted=extracted,
        )
        db.add(upload_record)
        db.flush()

        chapter_id = extracted.get("chapter_id")
        new_chapter: Optional[Chapter] = None
        existing_chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()

        if existing_chapter:
            existing_concepts = existing_chapter.concepts or []
            new_concepts = extracted.get("concepts", [])
            concept_dict = {c["id"]: c for c in existing_concepts if c.get("id")}
            for concept in new_concepts:
                if concept.get("id"):
                    concept_dict[concept["id"]] = concept
            existing_chapter.concepts = list(concept_dict.values())

            existing_mastery_ids = {
                row[0]
                for row in db.query(ConceptMastery.concept_id)
                .filter(ConceptMastery.chapter_id == chapter_id)
                .all()
            }
            for concept in new_concepts:
                concept_id = str(concept.get("id") or "").strip()
                concept_name = str(concept.get("name") or "").strip()
                if not concept_id or not concept_name or concept_id in existing_mastery_ids:
                    continue
                db.add(
                    ConceptMastery(
                        concept_id=concept_id,
                        chapter_id=chapter_id,
                        name=concept_name,
                        retention=0.0,
                        understanding=0.0,
                        application=0.0,
                    )
                )
                existing_mastery_ids.add(concept_id)

            if extracted.get("summary"):
                existing_chapter.content_summary = extracted["summary"]
        else:
            new_chapter = Chapter(
                id=chapter_id,
                book=extracted["book"],
                edition=extracted.get("edition"),
                chapter_number=extracted["chapter_number"],
                chapter_title=extracted["chapter_title"],
                content_summary=extracted.get("summary"),
                concepts=extracted.get("concepts", []),
                first_uploaded=upload_date,
            )
            db.add(new_chapter)
            for concept in extracted.get("concepts", []):
                concept_id = str(concept.get("id") or "").strip()
                concept_name = str(concept.get("name") or "").strip()
                if not concept_id or not concept_name:
                    continue
                db.add(
                    ConceptMastery(
                        concept_id=concept_id,
                        chapter_id=chapter_id,
                        name=concept_name,
                        retention=0.0,
                        understanding=0.0,
                        application=0.0,
                    )
                )

        actor = resolve_request_actor_scope()
        sync_review_chapter_from_upload(
            db,
            actor_key=actor["actor_key"],
            upload_record=upload_record,
            chapter=existing_chapter or new_chapter,
            extracted=extracted,
        )

        db.commit()

        total_time = time.time() - start_time
        return UploadResponse(
            upload_id=upload_record.id,
            date=upload_date,
            extracted=extracted,
            message=f"成功识别：{extracted['book']} - {extracted['chapter_title']}（耗时 {total_time:.1f} 秒）",
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"处理失败: {exc}") from exc


def _decode_text_file(file_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="ignore")


async def _read_preview_input(
    *,
    source_mode: str,
    source_name: Optional[str],
    content_text: Optional[str],
    file: Optional[UploadFile],
    upload_service,
) -> tuple[str, str, str]:
    normalized_mode = str(source_mode or "text_paste").strip() or "text_paste"
    normalized_name = str(source_name or "").strip()

    if normalized_mode == "text_paste":
        raw_text = str(content_text or "").strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="请输入要整理的课程内容。")
        return normalized_mode, normalized_name or "pasted-text", raw_text

    if file is None:
        raise HTTPException(status_code=400, detail="请上传文件。")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空。")

    filename = str(file.filename or normalized_name or "upload").strip() or "upload"
    content_type = str(file.content_type or "").strip().lower()

    if normalized_mode == "file":
        raw_text = _decode_text_file(file_bytes).strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="文件中没有可用文本。")
        return "file", filename, raw_text

    if normalized_mode == "image_ocr":
        raw_text = await upload_service.extract_text_from_image(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
        )
        raw_text = str(raw_text or "").strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="图片 OCR 没有提取到文本。")
        return "image_ocr", filename, raw_text

    raise HTTPException(status_code=400, detail="不支持的上传模式。")


@router.get("/workspace", response_model=dict)
async def get_upload_workspace(db: Session = Depends(get_db)):
    actor = resolve_request_actor_scope()
    service = get_knowledge_upload_service()
    return service.build_workspace(actor=actor, db=db)


@router.post("/knowledge-preview", response_model=dict)
async def build_knowledge_preview(
    source_mode: str = Form("text_paste"),
    source_name: Optional[str] = Form(default=None),
    content_text: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    service = get_knowledge_upload_service()
    try:
        resolved_type, resolved_name, raw_text = await _read_preview_input(
            source_mode=source_mode,
            source_name=source_name,
            content_text=content_text,
            file=file,
            upload_service=service,
        )
        return await service.build_preview(
            source_type=resolved_type,
            source_name=resolved_name,
            raw_text=raw_text,
            db=db,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/knowledge-save", response_model=dict)
async def save_knowledge_preview(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    preview_id = str(payload.get("preview_id") or "").strip()
    items = payload.get("items") or []
    if not preview_id:
        raise HTTPException(status_code=400, detail="缺少 preview_id。")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="没有可保存的知识点。")

    actor = resolve_request_actor_scope()
    service = get_knowledge_upload_service()
    try:
        return await service.save_preview(
            preview_id=preview_id,
            submitted_items=items,
            actor=actor,
            db=db,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/pending/{pending_id}/resolve", response_model=dict)
async def resolve_pending_upload_item(
    pending_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    chapter_id = str(payload.get("chapter_id") or "").strip()
    if not chapter_id:
        raise HTTPException(status_code=400, detail="缺少目标章节。")

    actor = resolve_request_actor_scope()
    service = get_knowledge_upload_service()
    try:
        return await service.resolve_pending_item(
            pending_id=pending_id,
            chapter_id=chapter_id,
            actor=actor,
            db=db,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/knowledge-points/{note_id}", response_model=dict)
async def update_knowledge_point(
    note_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    service = get_knowledge_upload_service()
    try:
        return await service.update_note(
            note_id=note_id,
            chapter_id=str(payload.get("chapter_id") or "").strip(),
            concept_name=str(payload.get("concept_name") or "").strip(),
            note_summary=str(payload.get("note_summary") or "").strip(),
            note_body=str(payload.get("note_body") or "").strip(),
            actor=actor,
            db=db,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/daily-report", response_model=dict)
async def get_daily_report(
    target_date: Optional[str] = None,
    force_regenerate: bool = False,
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    service = get_knowledge_upload_service()
    parsed_date = date.today()
    if target_date:
        try:
            parsed_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期格式必须是 YYYY-MM-DD。") from exc
    return await service.generate_daily_report(
        actor=actor,
        db=db,
        target_date=parsed_date,
        force_regenerate=force_regenerate,
    )


@router.post("/knowledge-points/{note_id}/practice", response_model=dict)
async def start_note_practice(
    note_id: int,
    db: Session = Depends(get_db),
):
    actor = resolve_request_actor_scope()
    service = get_knowledge_upload_service()
    try:
        return await service.start_practice(note_id=note_id, actor=actor, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/practice/grade", response_model=dict)
async def grade_note_practice(payload: Dict[str, Any] = Body(...)):
    practice_id = str(payload.get("practice_id") or "").strip()
    user_answer = str(payload.get("user_answer") or "").strip()
    if not practice_id:
        raise HTTPException(status_code=400, detail="缺少练习题 ID。")
    if not user_answer:
        raise HTTPException(status_code=400, detail="请先作答。")

    service = get_knowledge_upload_service()
    try:
        return service.grade_practice(practice_id=practice_id, user_answer=user_answer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
