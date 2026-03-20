from __future__ import annotations

import base64
import os
import re
import uuid
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.audit import log_audit_change, model_to_audit_dict
from knowledge_upload_models import (
    KnowledgeDailyReport,
    KnowledgePendingClassification,
    KnowledgePointNote,
    KnowledgePointSource,
    KnowledgeUploadRecord,
)
from models import Chapter
from services.ai_client import get_ai_client
from services.content_parser_v2 import get_content_parser
from services.quiz_service import QuizService


def _clean_text(value: Any, *, max_length: Optional[int] = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if max_length is not None:
        return text[:max_length].strip()
    return text


def _normalize_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())[:120]


def _chapter_label(chapter: Chapter) -> str:
    number = str(chapter.chapter_number or "").strip()
    prefix = f"第{number}章 " if number else ""
    return f"{chapter.book} / {prefix}{chapter.chapter_title}".strip()


def _safe_excerpt(value: str, max_length: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "..."


def _vision_pool_entries(ai_client) -> List[tuple[Any, str, str]]:
    parse_pool = getattr(ai_client, "_parse_pool", None)
    default_heavy_pool = getattr(ai_client, "_default_heavy_pool", None)
    if callable(parse_pool) and callable(default_heavy_pool):
        try:
            pool = parse_pool("POOL_VISION", default_heavy_pool())
            if pool:
                return list(pool)
        except Exception:
            pass

    heavy_pool = getattr(ai_client, "_heavy_pool", None)
    if heavy_pool:
        return list(heavy_pool)
    return []


def _vision_entry_model(display: str, model: str) -> tuple[str, str]:
    provider_name = str(display or "").split("/", 1)[0].strip().lower()
    preferred_model = (os.getenv(f"{provider_name.upper()}_VISION_MODEL") or "").strip()
    if preferred_model:
        return provider_name, preferred_model
    global_vision_model = (os.getenv("VISION_MODEL") or "").strip()
    if global_vision_model:
        return provider_name, global_vision_model
    return provider_name, model


class KnowledgeUploadService:
    def __init__(self) -> None:
        self.ai = get_ai_client()
        self.parser = get_content_parser()
        self.quiz_service = QuizService()
        self.preview_cache: dict[str, dict[str, Any]] = {}
        self.practice_cache: dict[str, dict[str, Any]] = {}

    async def build_preview(
        self,
        *,
        source_type: str,
        source_name: str,
        raw_text: str,
        db: Session,
    ) -> Dict[str, Any]:
        items = await self._extract_structured_knowledge(raw_text, db)
        preview_items: List[Dict[str, Any]] = []
        pending_count = 0

        for index, item in enumerate(items):
            preview_item = await self._resolve_preview_item(item, db=db, index=index)
            if preview_item["status"] == "pending":
                pending_count += 1
            preview_items.append(preview_item)

        preview_id = uuid.uuid4().hex
        self.preview_cache[preview_id] = {
            "source_type": source_type,
            "source_name": source_name,
            "raw_text": raw_text,
            "items": preview_items,
            "created_at": datetime.now(),
        }

        return {
            "preview_id": preview_id,
            "source_type": source_type,
            "source_name": source_name,
            "item_count": len(preview_items),
            "pending_count": pending_count,
            "items": preview_items,
        }

    async def save_preview(
        self,
        *,
        preview_id: str,
        submitted_items: List[Dict[str, Any]],
        actor: Dict[str, Any],
        db: Session,
    ) -> Dict[str, Any]:
        cached = self.preview_cache.get(preview_id)
        if not cached:
            raise ValueError("预览已过期，请重新解析。")

        upload_record = KnowledgeUploadRecord(
            actor_key=str(actor["actor_key"]),
            user_id=actor.get("paper_user_id"),
            device_id=actor.get("paper_device_id"),
            source_type=str(cached.get("source_type") or "text_paste"),
            source_name=_clean_text(cached.get("source_name"), max_length=240),
            raw_text_snapshot=str(cached.get("raw_text") or ""),
            preview_snapshot={"items": submitted_items},
        )
        db.add(upload_record)
        db.flush()
        log_audit_change(
            db=db,
            target=upload_record,
            action="create",
            after=upload_record,
            actor=actor,
            origin_event_type="knowledge.upload.save_preview",
            origin_public_id=str(upload_record.id),
        )

        created_notes = 0
        updated_notes = 0
        pending_items = 0

        for item in submitted_items:
            chapter_id = _clean_text(item.get("chapter_id"), max_length=120)
            knowledge_points = self._sanitize_knowledge_points(item.get("knowledge_points") or [])
            if not knowledge_points:
                continue

            if not chapter_id or not db.query(Chapter).filter(Chapter.id == chapter_id).first():
                pending = KnowledgePendingClassification(
                    actor_key=str(actor["actor_key"]),
                    user_id=actor.get("paper_user_id"),
                    device_id=actor.get("paper_device_id"),
                    upload_id=upload_record.id,
                    source_type=upload_record.source_type,
                    source_name=upload_record.source_name,
                    book_hint=_clean_text(item.get("book"), max_length=120),
                    chapter_number_hint=_clean_text(item.get("chapter_number"), max_length=32),
                    chapter_title_hint=_clean_text(item.get("chapter_title"), max_length=240),
                    chapter_candidates=item.get("chapter_candidates") or [],
                    knowledge_points=knowledge_points,
                    source_excerpt=_safe_excerpt(item.get("source_excerpt") or item.get("chapter_summary") or ""),
                )
                db.add(pending)
                db.flush([pending])
                log_audit_change(
                    db=db,
                    target=pending,
                    action="create",
                    after=pending,
                    actor=actor,
                    origin_event_type="knowledge.pending.create",
                    origin_public_id=str(upload_record.id),
                )
                pending_items += 1
                continue

            for kp in knowledge_points:
                _note, was_created = await self._upsert_note(
                    db=db,
                    actor=actor,
                    upload_record=upload_record,
                    chapter_id=chapter_id,
                    concept_name=kp["name"],
                    note_summary=kp["summary"],
                    note_body=kp["note_body"],
                    source_excerpt=item.get("source_excerpt") or kp["note_body"],
                )
                if was_created:
                    created_notes += 1
                else:
                    updated_notes += 1

        before_upload_snapshot = model_to_audit_dict(upload_record)
        upload_record.saved_note_count = created_notes + updated_notes
        upload_record.pending_item_count = pending_items
        db.flush([upload_record])
        log_audit_change(
            db=db,
            target=upload_record,
            action="update",
            before=before_upload_snapshot,
            after=upload_record,
            actor=actor,
            origin_event_type="knowledge.upload.finalize",
            origin_public_id=str(upload_record.id),
        )
        db.commit()
        self.preview_cache.pop(preview_id, None)

        return {
            "upload_id": upload_record.id,
            "created_notes": created_notes,
            "updated_notes": updated_notes,
            "pending_items": pending_items,
            "message": f"已保存 {created_notes + updated_notes} 个知识点，待归类 {pending_items} 项。",
        }

    async def resolve_pending_item(
        self,
        *,
        pending_id: int,
        chapter_id: str,
        actor: Dict[str, Any],
        db: Session,
    ) -> Dict[str, Any]:
        pending = (
            db.query(KnowledgePendingClassification)
            .filter(
                KnowledgePendingClassification.id == pending_id,
                KnowledgePendingClassification.actor_key.in_(actor["actor_keys"]),
                KnowledgePendingClassification.status == "pending",
            )
            .first()
        )
        if not pending:
            raise ValueError("待归类项目不存在。")

        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        if not chapter:
            raise ValueError("目标章节不存在。")

        upload_record = db.query(KnowledgeUploadRecord).filter(KnowledgeUploadRecord.id == pending.upload_id).first()
        created_notes = 0
        updated_notes = 0
        before_pending_snapshot = model_to_audit_dict(pending)

        for kp in self._sanitize_knowledge_points(pending.knowledge_points or []):
            _note, was_created = await self._upsert_note(
                db=db,
                actor=actor,
                upload_record=upload_record,
                chapter_id=chapter_id,
                concept_name=kp["name"],
                note_summary=kp["summary"],
                note_body=kp["note_body"],
                source_excerpt=pending.source_excerpt or kp["note_body"],
            )
            if was_created:
                created_notes += 1
            else:
                updated_notes += 1

        pending.status = "resolved"
        pending.resolved_chapter_id = chapter_id
        pending.resolved_at = datetime.now()
        pending.updated_at = datetime.now()
        db.flush([pending])
        log_audit_change(
            db=db,
            target=pending,
            action="update",
            before=before_pending_snapshot,
            after=pending,
            actor=actor,
            origin_event_type="knowledge.pending.resolve",
            origin_public_id=str(pending.id),
        )
        db.commit()

        return {
            "pending_id": pending_id,
            "chapter_id": chapter_id,
            "created_notes": created_notes,
            "updated_notes": updated_notes,
            "message": f"已归入 {chapter.book} / {chapter.chapter_title}",
        }

    def build_workspace(self, *, actor: Dict[str, Any], db: Session) -> Dict[str, Any]:
        notes = (
            db.query(KnowledgePointNote)
            .filter(KnowledgePointNote.actor_key.in_(actor["actor_keys"]))
            .order_by(KnowledgePointNote.updated_at.desc(), KnowledgePointNote.id.desc())
            .all()
        )
        uploads = (
            db.query(KnowledgeUploadRecord)
            .filter(KnowledgeUploadRecord.actor_key.in_(actor["actor_keys"]))
            .order_by(KnowledgeUploadRecord.created_at.desc(), KnowledgeUploadRecord.id.desc())
            .all()
        )
        pending_items = (
            db.query(KnowledgePendingClassification)
            .filter(
                KnowledgePendingClassification.actor_key.in_(actor["actor_keys"]),
                KnowledgePendingClassification.status == "pending",
            )
            .order_by(KnowledgePendingClassification.created_at.desc(), KnowledgePendingClassification.id.desc())
            .all()
        )

        chapter_ids = sorted({str(note.chapter_id or "") for note in notes if note.chapter_id})
        chapter_map = {
            chapter.id: chapter
            for chapter in db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all()
        } if chapter_ids else {}

        sources = (
            db.query(KnowledgePointSource)
            .filter(KnowledgePointSource.actor_key.in_(actor["actor_keys"]))
            .order_by(KnowledgePointSource.created_at.desc(), KnowledgePointSource.id.desc())
            .all()
        )
        sources_by_note: Dict[int, List[KnowledgePointSource]] = {}
        for source in sources:
            sources_by_note.setdefault(int(source.note_id), []).append(source)

        chapters: Dict[str, Dict[str, Any]] = {}
        for note in notes:
            chapter = chapter_map.get(note.chapter_id)
            label = _chapter_label(chapter) if chapter else note.chapter_id
            group = chapters.setdefault(
                note.chapter_id,
                {
                    "chapter_id": note.chapter_id,
                    "chapter_label": label,
                    "book": getattr(chapter, "book", ""),
                    "chapter_number": getattr(chapter, "chapter_number", ""),
                    "chapter_title": getattr(chapter, "chapter_title", note.chapter_id),
                    "updated_at": note.updated_at.isoformat() if note.updated_at else None,
                    "note_count": 0,
                    "notes": [],
                },
            )
            group["note_count"] += 1
            if note.updated_at and (
                not group.get("updated_at") or note.updated_at.isoformat() > str(group["updated_at"])
            ):
                group["updated_at"] = note.updated_at.isoformat()
            group["notes"].append(
                {
                    "id": note.id,
                    "concept_name": note.concept_name,
                    "concept_key": note.concept_key,
                    "note_summary": note.note_summary or "",
                    "note_body": note.note_body,
                    "source_count": int(note.source_count or 0),
                    "updated_at": note.updated_at.isoformat() if note.updated_at else None,
                    "sources": [
                        {
                            "id": source.id,
                            "source_type": source.source_type,
                            "source_name": source.source_name or "",
                            "source_excerpt": source.source_excerpt or "",
                            "created_at": source.created_at.isoformat() if source.created_at else None,
                        }
                        for source in sources_by_note.get(int(note.id), [])[:5]
                    ],
                }
            )

        chapter_list = sorted(
            chapters.values(),
            key=lambda item: (item.get("updated_at") or "", item.get("chapter_label") or ""),
            reverse=True,
        )

        week_plan = self._build_week_plan(notes)
        total_chapters = len(chapter_list)
        recent_new_notes = sum(
            1 for note in notes if note.created_at and note.created_at.date() >= date.today() - timedelta(days=6)
        )

        recent_uploads = [
            {
                "id": upload.id,
                "source_type": upload.source_type,
                "source_name": upload.source_name or "",
                "saved_note_count": int(upload.saved_note_count or 0),
                "pending_item_count": int(upload.pending_item_count or 0),
                "created_at": upload.created_at.isoformat() if upload.created_at else None,
            }
            for upload in uploads[:8]
        ]

        chapter_options = [
            {
                "id": chapter.id,
                "book": chapter.book,
                "chapter_number": chapter.chapter_number,
                "chapter_title": chapter.chapter_title,
                "label": _chapter_label(chapter),
            }
            for chapter in db.query(Chapter).order_by(Chapter.book.asc(), Chapter.chapter_number.asc()).all()
        ]

        return {
            "stats": {
                "total_uploads": len(uploads),
                "total_chapters": total_chapters,
                "total_knowledge_points": len(notes),
                "recent_new_knowledge_points": recent_new_notes,
                "pending_count": len(pending_items),
                "weekly_review_pressure": week_plan["days"],
                "pace_advice": week_plan["advice"],
                "avg_daily_reviews": week_plan["avg_daily_reviews"],
                "busiest_day": week_plan["busiest_day"],
                "busiest_count": week_plan["busiest_count"],
            },
            "recent_uploads": recent_uploads,
            "pending": [
                {
                    "id": item.id,
                    "book_hint": item.book_hint or "",
                    "chapter_number_hint": item.chapter_number_hint or "",
                    "chapter_title_hint": item.chapter_title_hint or "",
                    "source_excerpt": item.source_excerpt or "",
                    "knowledge_points": item.knowledge_points or [],
                    "chapter_candidates": item.chapter_candidates or [],
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in pending_items
            ],
            "chapters": chapter_list,
            "chapter_options": chapter_options,
        }

    async def update_note(
        self,
        *,
        note_id: int,
        chapter_id: str,
        concept_name: str,
        note_summary: str,
        note_body: str,
        actor: Dict[str, Any],
        db: Session,
    ) -> Dict[str, Any]:
        note = (
            db.query(KnowledgePointNote)
            .filter(
                KnowledgePointNote.id == note_id,
                KnowledgePointNote.actor_key.in_(actor["actor_keys"]),
            )
            .first()
        )
        if not note:
            raise ValueError("知识点不存在。")

        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        if not chapter:
            raise ValueError("章节不存在。")

        new_name = _clean_text(concept_name, max_length=120)
        new_key = _normalize_name_key(new_name)
        if not new_key:
            raise ValueError("知识点名称不能为空。")

        duplicate = (
            db.query(KnowledgePointNote)
            .filter(
                KnowledgePointNote.id != note.id,
                KnowledgePointNote.actor_key == note.actor_key,
                KnowledgePointNote.chapter_id == chapter_id,
                KnowledgePointNote.concept_key == new_key,
            )
            .first()
        )
        if duplicate:
            raise ValueError("目标章节下已存在同名知识点。")

        before_note_snapshot = model_to_audit_dict(note)
        note.chapter_id = chapter_id
        note.concept_name = new_name
        note.concept_key = new_key
        note.note_summary = _clean_text(note_summary, max_length=240)
        note.note_body = str(note_body or "").strip()
        note.updated_at = datetime.now()
        db.flush([note])
        log_audit_change(
            db=db,
            target=note,
            action="update",
            before=before_note_snapshot,
            after=note,
            actor=actor,
            origin_event_type="knowledge.note.update",
            origin_public_id=str(note.id),
        )
        db.commit()

        return {
            "id": note.id,
            "chapter_id": note.chapter_id,
            "chapter_label": _chapter_label(chapter),
            "concept_name": note.concept_name,
            "note_summary": note.note_summary or "",
            "note_body": note.note_body,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }

    async def generate_daily_report(
        self,
        *,
        actor: Dict[str, Any],
        db: Session,
        target_date: Optional[date] = None,
        force_regenerate: bool = False,
    ) -> Dict[str, Any]:
        report_date = target_date or date.today()
        existing = (
            db.query(KnowledgeDailyReport)
            .filter(
                KnowledgeDailyReport.actor_key == actor["actor_key"],
                KnowledgeDailyReport.report_date == report_date,
            )
            .first()
        )
        if existing and not force_regenerate:
            return existing.snapshot or {}

        notes = (
            db.query(KnowledgePointNote)
            .filter(KnowledgePointNote.actor_key.in_(actor["actor_keys"]))
            .order_by(KnowledgePointNote.updated_at.desc(), KnowledgePointNote.id.desc())
            .all()
        )
        uploads = (
            db.query(KnowledgeUploadRecord)
            .filter(KnowledgeUploadRecord.actor_key.in_(actor["actor_keys"]))
            .all()
        )
        pending_count = (
            db.query(KnowledgePendingClassification)
            .filter(
                KnowledgePendingClassification.actor_key.in_(actor["actor_keys"]),
                KnowledgePendingClassification.status == "pending",
            )
            .count()
        )

        chapter_ids = sorted({note.chapter_id for note in notes if note.chapter_id})
        chapter_map = {
            chapter.id: chapter
            for chapter in db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all()
        } if chapter_ids else {}

        created_today = [
            note for note in notes if note.created_at and note.created_at.date() == report_date
        ]
        updated_today = [
            note
            for note in notes
            if note.updated_at
            and note.updated_at.date() == report_date
            and note.created_at
            and note.created_at.date() != report_date
        ]

        week_plan = self._build_week_plan(notes, start_day=report_date)
        practice_seed = created_today or updated_today or notes[:5]
        practice_questions = []
        for note in practice_seed[:5]:
            chapter = chapter_map.get(note.chapter_id)
            question = await self.quiz_service.generate_quiz(
                concept_name=note.concept_name,
                concept_description=note.note_body,
            )
            practice_questions.append(
                {
                    "knowledge_point_id": note.id,
                    "concept_name": note.concept_name,
                    "chapter_label": _chapter_label(chapter) if chapter else note.chapter_id,
                    "question": question.get("question", ""),
                    "options": question.get("options", {}),
                    "correct_answer": question.get("correct_answer", ""),
                    "explanation": question.get("explanation", ""),
                }
            )

        snapshot = {
            "date": report_date.isoformat(),
            "totals": {
                "total_uploads": len(uploads),
                "total_knowledge_points": len(notes),
                "total_chapters": len(chapter_ids),
                "pending_count": pending_count,
            },
            "created_today": [
                {
                    "id": note.id,
                    "concept_name": note.concept_name,
                    "chapter_label": _chapter_label(chapter_map[note.chapter_id]) if note.chapter_id in chapter_map else note.chapter_id,
                    "note_summary": note.note_summary or "",
                }
                for note in created_today
            ],
            "updated_today": [
                {
                    "id": note.id,
                    "concept_name": note.concept_name,
                    "chapter_label": _chapter_label(chapter_map[note.chapter_id]) if note.chapter_id in chapter_map else note.chapter_id,
                    "note_summary": note.note_summary or "",
                }
                for note in updated_today
            ],
            "review_plan": week_plan,
            "practice_questions": practice_questions,
        }

        if existing:
            before_report_snapshot = model_to_audit_dict(existing)
            existing.snapshot = snapshot
            existing.updated_at = datetime.now()
            db.flush([existing])
            log_audit_change(
                db=db,
                target=existing,
                action="update",
                before=before_report_snapshot,
                after=existing,
                actor=actor,
                origin_event_type="knowledge.daily_report.generate",
                origin_public_id=f"{actor['actor_key']}:{report_date.isoformat()}",
            )
        else:
            report = KnowledgeDailyReport(
                actor_key=str(actor["actor_key"]),
                user_id=actor.get("paper_user_id"),
                device_id=actor.get("paper_device_id"),
                report_date=report_date,
                snapshot=snapshot,
            )
            db.add(report)
            db.flush([report])
            log_audit_change(
                db=db,
                target=report,
                action="create",
                after=report,
                actor=actor,
                origin_event_type="knowledge.daily_report.generate",
                origin_public_id=f"{actor['actor_key']}:{report_date.isoformat()}",
            )
        db.commit()
        return snapshot

    async def start_practice(
        self,
        *,
        note_id: int,
        actor: Dict[str, Any],
        db: Session,
    ) -> Dict[str, Any]:
        note = (
            db.query(KnowledgePointNote)
            .filter(
                KnowledgePointNote.id == note_id,
                KnowledgePointNote.actor_key.in_(actor["actor_keys"]),
            )
            .first()
        )
        if not note:
            raise ValueError("知识点不存在。")

        question = await self.quiz_service.generate_quiz(
            concept_name=note.concept_name,
            concept_description=note.note_body,
        )
        practice_id = uuid.uuid4().hex
        self.practice_cache[practice_id] = {
            "correct_answer": str(question.get("correct_answer") or "").strip().upper(),
            "explanation": str(question.get("explanation") or "").strip(),
            "note_id": note.id,
            "created_at": datetime.now(),
        }

        return {
            "practice_id": practice_id,
            "note_id": note.id,
            "concept_name": note.concept_name,
            "question": question.get("question", ""),
            "options": question.get("options", {}),
        }

    def grade_practice(self, *, practice_id: str, user_answer: str) -> Dict[str, Any]:
        cached = self.practice_cache.get(practice_id)
        if not cached:
            raise ValueError("练习题已过期，请重新生成。")

        normalized_user = re.sub(r"[^A-E]", "", str(user_answer or "").upper())[:1]
        correct_answer = re.sub(r"[^A-E]", "", str(cached.get("correct_answer") or "").upper())[:1]
        is_correct = normalized_user == correct_answer

        return {
            "practice_id": practice_id,
            "is_correct": is_correct,
            "correct_answer": correct_answer,
            "explanation": cached.get("explanation") or "",
        }

    async def extract_text_from_image(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        pool_entries = _vision_pool_entries(self.ai)
        if not pool_entries:
            raise RuntimeError("当前未配置可用的 OCR 号池。")

        encoded = base64.b64encode(file_bytes).decode("ascii")
        mime_type = content_type or "image/png"
        data_url = f"data:{mime_type};base64,{encoded}"
        prompt = (
            "请直接提取这张图片中的中文学习内容，输出纯文本。"
            "不要解释，不要总结，不要补充。保留分段和原有结构。"
        )

        for client, default_model, display in pool_entries:
            provider_name, model = _vision_entry_model(display, default_model)
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                    max_tokens=4000,
                    temperature=0.0,
                )
                content = str(response.choices[0].message.content or "").strip()
                if content:
                    return content
            except Exception as exc:
                print(f"[KnowledgeUploadService] OCR failed via {provider_name}/{model}: {exc}")
                continue

        raise RuntimeError(f"图片 OCR 失败：{filename} 当前没有可用的视觉模型。")

    async def _extract_structured_knowledge(self, raw_text: str, db: Session) -> List[Dict[str, Any]]:
        cleaned_text = str(raw_text or "").strip()
        if not cleaned_text:
            return []

        schema = {
            "items": [
                {
                    "book_hint": "教材或科目",
                    "chapter_number_hint": "章节号",
                    "chapter_title_hint": "章节标题",
                    "chapter_summary": "这一组内容在讲什么",
                    "source_excerpt": "原文中的关键片段",
                    "knowledge_points": [
                        {
                            "name": "知识点名",
                            "summary": "一句话摘要",
                            "note_body": "整理后的知识笔记正文",
                        }
                    ],
                }
            ]
        }
        prompt = f"""你是医学学习知识库整理助手。
请把下面的课堂对话、课堂笔记、Markdown 或 OCR 文本，整理成适合入库的知识结构。

要求：
1. 如果内容涉及多个章节，必须拆成多个 items。
2. 每个 item 只对应一个章节。
3. 每个章节下提炼 1 到 5 个知识点。
4. note_body 必须是整理后的知识笔记，不要保留聊天口吻，不要输出原始对话。
5. source_excerpt 只保留能支撑归类的关键片段。
6. 只返回 JSON。

原始内容：
{cleaned_text[:18000]}
"""

        try:
            result = await self.ai.generate_json(
                prompt,
                schema,
                max_tokens=5000,
                temperature=0.1,
                use_heavy=False,
                timeout=90,
            )
            items = result.get("items") if isinstance(result, dict) else []
        except Exception:
            items = []

        sanitized = self._sanitize_extracted_items(items or [])
        if sanitized:
            return sanitized

        fallback = await self.parser.parse_content_with_knowledge(cleaned_text, db=db)
        fallback_points = [
            {
                "name": _clean_text(item.get("name"), max_length=120),
                "summary": _clean_text(item.get("name"), max_length=80),
                "note_body": _clean_text(fallback.get("summary") or cleaned_text[:600], max_length=1200),
            }
            for item in (fallback.get("concepts") or [])[:5]
            if _clean_text(item.get("name"))
        ]
        if not fallback_points:
            fallback_points = [
                {
                    "name": "未命名知识点",
                    "summary": _clean_text(fallback.get("summary") or "待整理"),
                    "note_body": _clean_text(cleaned_text[:1200]),
                }
            ]

        return [
            {
                "book_hint": _clean_text(fallback.get("book"), max_length=120),
                "chapter_number_hint": _clean_text(fallback.get("chapter_number"), max_length=32),
                "chapter_title_hint": _clean_text(fallback.get("chapter_title"), max_length=120),
                "chapter_summary": _clean_text(fallback.get("summary"), max_length=240),
                "source_excerpt": _safe_excerpt(cleaned_text),
                "knowledge_points": fallback_points,
            }
        ]

    def _sanitize_extracted_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized: List[Dict[str, Any]] = []
        for raw_item in items[:6]:
            knowledge_points = self._sanitize_knowledge_points(raw_item.get("knowledge_points") or [])
            if not knowledge_points:
                continue
            sanitized.append(
                {
                    "book_hint": _clean_text(raw_item.get("book_hint"), max_length=120),
                    "chapter_number_hint": _clean_text(raw_item.get("chapter_number_hint"), max_length=32),
                    "chapter_title_hint": _clean_text(raw_item.get("chapter_title_hint"), max_length=120),
                    "chapter_summary": _clean_text(raw_item.get("chapter_summary"), max_length=240),
                    "source_excerpt": _safe_excerpt(raw_item.get("source_excerpt") or ""),
                    "knowledge_points": knowledge_points,
                }
            )
        return sanitized

    def _sanitize_knowledge_points(self, knowledge_points: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        sanitized: List[Dict[str, str]] = []
        seen: set[str] = set()
        for item in knowledge_points[:8]:
            name = _clean_text(item.get("name"), max_length=120)
            key = _normalize_name_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            summary = _clean_text(item.get("summary"), max_length=200) or name
            note_body = _clean_text(item.get("note_body"), max_length=2400) or summary
            sanitized.append({"name": name, "summary": summary, "note_body": note_body})
        return sanitized

    async def _resolve_preview_item(self, item: Dict[str, Any], *, db: Session, index: int) -> Dict[str, Any]:
        knowledge_point_names = [kp["name"] for kp in item["knowledge_points"]]
        parser_payload = "\n".join(
            [
                item.get("book_hint") or "",
                item.get("chapter_number_hint") or "",
                item.get("chapter_title_hint") or "",
                item.get("chapter_summary") or "",
                "；".join(knowledge_point_names),
                " ".join(kp["note_body"] for kp in item["knowledge_points"]),
            ]
        ).strip()
        parser_result = await self.parser.parse_content_with_knowledge(parser_payload, db=db)
        candidates = self._build_chapter_candidates(item, parser_result=parser_result, db=db)

        resolved_candidate = candidates[0] if candidates else None
        resolved = bool(
            resolved_candidate
            and (
                resolved_candidate["score"] >= 0.82
                or resolved_candidate.get("id") == parser_result.get("chapter_id")
            )
        )
        chapter_id = resolved_candidate["id"] if resolved else ""
        chapter_label = resolved_candidate["label"] if resolved else "待归类"

        return {
            "local_id": f"preview-item-{index + 1}",
            "status": "resolved" if resolved else "pending",
            "book": _clean_text(item.get("book_hint") or parser_result.get("book"), max_length=120),
            "chapter_number": _clean_text(item.get("chapter_number_hint") or parser_result.get("chapter_number"), max_length=32),
            "chapter_title": _clean_text(item.get("chapter_title_hint") or parser_result.get("chapter_title"), max_length=120),
            "chapter_summary": _clean_text(item.get("chapter_summary"), max_length=240),
            "source_excerpt": _safe_excerpt(item.get("source_excerpt") or parser_payload, max_length=180),
            "chapter_id": chapter_id,
            "chapter_label": chapter_label,
            "chapter_candidates": candidates[:3],
            "knowledge_points": item["knowledge_points"],
        }

    def _build_chapter_candidates(
        self,
        item: Dict[str, Any],
        *,
        parser_result: Dict[str, Any],
        db: Session,
    ) -> List[Dict[str, Any]]:
        chapters = db.query(Chapter).all()
        item_book = _normalize_name_key(item.get("book_hint") or parser_result.get("book") or "")
        item_number = _normalize_name_key(item.get("chapter_number_hint") or parser_result.get("chapter_number") or "")
        item_title = _normalize_name_key(item.get("chapter_title_hint") or parser_result.get("chapter_title") or "")
        parser_chapter_id = _clean_text(parser_result.get("chapter_id"), max_length=120)
        kp_keys = {
            _normalize_name_key(kp["name"])
            for kp in item.get("knowledge_points") or []
            if _normalize_name_key(kp.get("name"))
        }
        scored: List[Dict[str, Any]] = []

        for chapter in chapters:
            score = 0.0
            book_key = _normalize_name_key(chapter.book)
            number_key = _normalize_name_key(chapter.chapter_number)
            title_key = _normalize_name_key(chapter.chapter_title)

            if item_book and item_book == book_key:
                score += 0.28
            elif item_book and (item_book in book_key or book_key in item_book):
                score += 0.16

            if item_number and item_number == number_key:
                score += 0.34

            if item_title:
                score += SequenceMatcher(None, item_title, title_key).ratio() * 0.34

            chapter_concepts = chapter.concepts or []
            chapter_kp_keys = {
                _normalize_name_key(concept.get("name"))
                for concept in chapter_concepts
                if _normalize_name_key(concept.get("name"))
            }
            if kp_keys and chapter_kp_keys:
                overlap = len(kp_keys & chapter_kp_keys) / max(len(kp_keys), 1)
                score += min(overlap * 0.18, 0.18)

            if parser_chapter_id and parser_chapter_id == chapter.id:
                score += 0.48

            if score <= 0:
                continue

            scored.append(
                {
                    "id": chapter.id,
                    "book": chapter.book,
                    "chapter_number": chapter.chapter_number,
                    "chapter_title": chapter.chapter_title,
                    "label": _chapter_label(chapter),
                    "score": round(score, 3),
                }
            )

        scored.sort(key=lambda row: (row["score"], row["book"], row["chapter_number"]), reverse=True)
        return scored[:3]

    async def _upsert_note(
        self,
        *,
        db: Session,
        actor: Dict[str, Any],
        upload_record: Optional[KnowledgeUploadRecord],
        chapter_id: str,
        concept_name: str,
        note_summary: str,
        note_body: str,
        source_excerpt: str,
    ) -> tuple[KnowledgePointNote, bool]:
        concept_key = _normalize_name_key(concept_name)
        existing = (
            db.query(KnowledgePointNote)
            .filter(
                KnowledgePointNote.actor_key == actor["actor_key"],
                KnowledgePointNote.chapter_id == chapter_id,
                KnowledgePointNote.concept_key == concept_key,
            )
            .first()
        )

        if existing:
            before_note_snapshot = model_to_audit_dict(existing)
            merged_summary, merged_body = await self._merge_note_content(
                concept_name=concept_name,
                existing_summary=existing.note_summary or "",
                existing_body=existing.note_body or "",
                incoming_summary=note_summary,
                incoming_body=note_body,
            )
            existing.concept_name = _clean_text(concept_name, max_length=120)
            existing.note_summary = merged_summary
            existing.note_body = merged_body
            existing.source_count = int(existing.source_count or 0) + 1
            existing.updated_at = datetime.now()
            note = existing
            created = False
        else:
            note = KnowledgePointNote(
                actor_key=str(actor["actor_key"]),
                user_id=actor.get("paper_user_id"),
                device_id=actor.get("paper_device_id"),
                chapter_id=chapter_id,
                concept_key=concept_key,
                concept_name=_clean_text(concept_name, max_length=120),
                note_summary=_clean_text(note_summary, max_length=240),
                note_body=str(note_body or "").strip(),
                source_count=1,
            )
            db.add(note)
            db.flush()
            created = True
            before_note_snapshot = None

        if upload_record is not None:
            db.add(
                KnowledgePointSource(
                    actor_key=str(actor["actor_key"]),
                    user_id=actor.get("paper_user_id"),
                    device_id=actor.get("paper_device_id"),
                    note_id=note.id,
                    upload_id=upload_record.id,
                    source_type=upload_record.source_type,
                    source_name=upload_record.source_name,
                    source_excerpt=_safe_excerpt(source_excerpt, max_length=220),
                )
            )

        db.flush([note])
        log_audit_change(
            db=db,
            target=note,
            action="create" if created else "update",
            before=before_note_snapshot,
            after=note,
            actor=actor,
            origin_event_type="knowledge.note.upsert",
            origin_public_id=str(upload_record.id) if upload_record is not None else str(note.id),
        )

        return note, created

    async def _merge_note_content(
        self,
        *,
        concept_name: str,
        existing_summary: str,
        existing_body: str,
        incoming_summary: str,
        incoming_body: str,
    ) -> tuple[str, str]:
        current_summary = _clean_text(existing_summary, max_length=240)
        current_body = str(existing_body or "").strip()
        new_summary = _clean_text(incoming_summary, max_length=240)
        new_body = str(incoming_body or "").strip()

        if not current_body:
            return new_summary, new_body

        providers = getattr(self.ai, "_providers", {})
        if not providers:
            return self._fallback_merge_text(current_summary, current_body, new_summary, new_body)

        schema = {
            "summary": "合并后的摘要",
            "note_body": "合并后的知识点笔记正文",
        }
        prompt = f"""你是知识整理助手。请把同一知识点的两份笔记合并成一份更完整、不重复的版本。

知识点：{concept_name}

旧摘要：{current_summary}
旧笔记：
{current_body}

新摘要：{new_summary}
新笔记：
{new_body}

要求：
1. 去掉重复内容。
2. 如果新笔记补充了机制、条件、对比点，要吸收进去。
3. 输出中文。
4. 只返回 JSON。
"""
        try:
            result = await self.ai.generate_json(
                prompt,
                schema,
                max_tokens=1800,
                temperature=0.0,
                use_heavy=False,
                timeout=60,
            )
            summary = _clean_text(result.get("summary"), max_length=240) or new_summary or current_summary
            note_body = _clean_text(result.get("note_body"), max_length=2400) or new_body or current_body
            return summary, note_body
        except Exception:
            return self._fallback_merge_text(current_summary, current_body, new_summary, new_body)

    def _fallback_merge_text(
        self,
        current_summary: str,
        current_body: str,
        new_summary: str,
        new_body: str,
    ) -> tuple[str, str]:
        summary_parts = []
        for part in [new_summary, current_summary]:
            if part and part not in summary_parts:
                summary_parts.append(part)
        merged_summary = "；".join(summary_parts)[:240]

        segments = []
        for block in [current_body, new_body]:
            for piece in re.split(r"[。；;\n]", block):
                cleaned = _clean_text(piece, max_length=240)
                if cleaned and cleaned not in segments:
                    segments.append(cleaned)
        merged_body = "。\n".join(segments)[:2400]
        return merged_summary, merged_body

    def _build_week_plan(
        self,
        notes: List[KnowledgePointNote],
        *,
        start_day: Optional[date] = None,
    ) -> Dict[str, Any]:
        begin = start_day or date.today()
        counters = {begin + timedelta(days=offset): 0 for offset in range(7)}
        review_offsets = (1, 3, 7)

        for note in notes:
            base_day = (note.updated_at or note.created_at or datetime.now()).date()
            for offset in review_offsets:
                target = base_day + timedelta(days=offset)
                if target in counters:
                    counters[target] += 1

        busiest_day, busiest_count = max(counters.items(), key=lambda item: (item[1], item[0]), default=(begin, 0))
        total = sum(counters.values())
        avg = round(total / max(len(counters), 1), 1)

        days = []
        for target, count in sorted(counters.items()):
            if count >= max(6, avg + 2):
                label = "高压"
            elif count >= max(3, avg):
                label = "适中"
            else:
                label = "轻量"
            days.append(
                {
                    "date": target.isoformat(),
                    "count": count,
                    "label": label,
                }
            )

        advice = (
            f"未来 7 天预计复习触点 {total} 个，日均 {avg} 个。"
            f"最高峰在 {busiest_day.isoformat()}，建议当天至少安排 {max(busiest_count, 1)} 个知识点回看。"
        )

        return {
            "days": days,
            "avg_daily_reviews": avg,
            "busiest_day": busiest_day.isoformat(),
            "busiest_count": busiest_count,
            "advice": advice,
        }


_knowledge_upload_service: Optional[KnowledgeUploadService] = None


def get_knowledge_upload_service() -> KnowledgeUploadService:
    global _knowledge_upload_service
    if _knowledge_upload_service is None:
        _knowledge_upload_service = KnowledgeUploadService()
    return _knowledge_upload_service
