from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from learning_tracking_models import (
    ChapterReviewChapter,
    ChapterReviewTask,
    ChapterReviewTaskQuestion,
    ChapterReviewUnit,
    INVALID_CHAPTER_IDS,
)
from models import Chapter, DailyUpload
from services.ai_client import get_ai_client


REVIEW_INTERVAL_DAYS = [1, 3, 7, 14]
DEFAULT_REVIEW_TIME_BUDGET_MINUTES = 40
QUESTIONS_PER_REVIEW_UNIT = 10
UNIT_TARGET_CHARS = 900
UNIT_MAX_CHARS = 1350
OPEN_TASK_STATUSES = {"pending", "in_progress", "awaiting_choice"}


@dataclass
class ReviewUnitDraft:
    unit_index: int
    unit_title: str
    raw_text: str
    cleaned_text: str
    excerpt: str
    char_count: int
    estimated_minutes: int


def _normalize_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_review_content(raw_text: str) -> str:
    cleaned = _normalize_text(raw_text)
    lines = [line.strip() for line in cleaned.split("\n")]
    normalized_lines: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                normalized_lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        normalized_lines.append(line)
    return "\n".join(normalized_lines).strip()


def _split_large_segment(segment: str, max_chars: int) -> list[str]:
    cleaned = segment.strip()
    if len(cleaned) <= max_chars:
        return [cleaned] if cleaned else []

    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?；;])", cleaned)
        if item and item.strip()
    ]
    if len(sentences) <= 1:
        return [cleaned[i:i + max_chars] for i in range(0, len(cleaned), max_chars) if cleaned[i:i + max_chars].strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and current_chars + len(sentence) > max_chars:
            chunks.append("".join(current).strip())
            current = [sentence]
            current_chars = len(sentence)
        else:
            current.append(sentence)
            current_chars += len(sentence)
    if current:
        chunks.append("".join(current).strip())
    return [item for item in chunks if item]


def _extract_segments(cleaned_text: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", cleaned_text) if item and item.strip()]
    if not paragraphs:
        return [cleaned_text.strip()] if cleaned_text.strip() else []

    segments: list[str] = []
    for paragraph in paragraphs:
        segments.extend(_split_large_segment(paragraph, max_chars=UNIT_MAX_CHARS))
    return [item for item in segments if item]


def estimate_unit_minutes(text: str, *, question_count: int = QUESTIONS_PER_REVIEW_UNIT) -> int:
    char_count = max(len(text.strip()), 1)
    reading_minutes = max(4, math.ceil(char_count / 320))
    answer_minutes = max(8, math.ceil(question_count * 1.2))
    return min(28, reading_minutes + answer_minutes)


def build_review_units(cleaned_text: str, *, chapter_title: str) -> list[ReviewUnitDraft]:
    segments = _extract_segments(cleaned_text)
    if not segments:
        return []

    units: list[ReviewUnitDraft] = []
    current_parts: list[str] = []
    current_chars = 0

    def flush_current() -> None:
        nonlocal current_parts, current_chars
        if not current_parts:
            return
        raw_text = "\n\n".join(current_parts).strip()
        unit_number = len(units) + 1
        units.append(
            ReviewUnitDraft(
                unit_index=unit_number,
                unit_title=f"{chapter_title} · 单元 {unit_number}",
                raw_text=raw_text,
                cleaned_text=raw_text,
                excerpt=raw_text[:180].strip(),
                char_count=len(raw_text),
                estimated_minutes=estimate_unit_minutes(raw_text),
            )
        )
        current_parts = []
        current_chars = 0

    for segment in segments:
        segment_chars = len(segment)
        if current_parts and current_chars + segment_chars > UNIT_MAX_CHARS:
            flush_current()
        current_parts.append(segment)
        current_chars += segment_chars
        if current_chars >= UNIT_TARGET_CHARS:
            flush_current()

    flush_current()
    return units


def _append_merged_content(existing: str, addition: str, *, upload_date: date) -> str:
    incoming = str(addition or "").strip()
    if not incoming:
        return str(existing or "").strip()

    existing_text = str(existing or "").strip()
    if not existing_text:
        return incoming
    if incoming in existing_text:
        return existing_text

    divider = f"\n\n--- 上传补充 {upload_date.isoformat()} ---\n"
    return f"{existing_text}{divider}{incoming}".strip()


def _chapter_review_query(db: Session, *, actor_key: str, chapter_id: str):
    return (
        db.query(ChapterReviewChapter)
        .options(joinedload(ChapterReviewChapter.units), joinedload(ChapterReviewChapter.tasks))
        .filter(
            ChapterReviewChapter.actor_key == actor_key,
            ChapterReviewChapter.chapter_id == chapter_id,
        )
    )


def _recompute_chapter_due(review_chapter: ChapterReviewChapter) -> None:
    active_units = [unit for unit in review_chapter.units if unit.is_active]
    due_dates = [unit.next_due_date for unit in active_units if unit.next_due_date]
    review_chapter.total_units = len(active_units)
    review_chapter.total_estimated_minutes = int(sum(unit.estimated_minutes or 0 for unit in active_units))
    review_chapter.next_due_date = min(due_dates) if due_dates else None
    if not active_units:
        review_chapter.review_status = "completed"
        return
    if any(unit.review_status == "weak" for unit in active_units):
        review_chapter.review_status = "weak"
    elif any(unit.next_due_date and unit.next_due_date <= date.today() for unit in active_units):
        review_chapter.review_status = "due"
    else:
        review_chapter.review_status = "pending"


def sync_review_chapter_from_upload(
    db: Session,
    *,
    actor_key: str,
    upload_record: DailyUpload,
    chapter: Optional[Chapter],
    extracted: Dict[str, Any],
) -> Optional[ChapterReviewChapter]:
    chapter_id = str((extracted or {}).get("chapter_id") or "").strip()
    if not chapter_id or chapter_id in INVALID_CHAPTER_IDS or chapter_id.endswith("_ch0"):
        return None

    chapter_title = str((extracted or {}).get("chapter_title") or getattr(chapter, "chapter_title", "") or "未识别章节").strip()
    book = str((extracted or {}).get("book") or getattr(chapter, "book", "") or "未识别").strip()
    summary = str((extracted or {}).get("summary") or getattr(chapter, "content_summary", "") or "").strip()
    upload_date = upload_record.date or date.today()

    review_chapter = _chapter_review_query(db, actor_key=actor_key, chapter_id=chapter_id).first()
    is_update = review_chapter is not None

    if review_chapter is None:
        review_chapter = ChapterReviewChapter(
            actor_key=actor_key,
            chapter_id=chapter_id,
            book=book,
            chapter_number=str(getattr(chapter, "chapter_number", "") or (extracted or {}).get("chapter_number") or "").strip(),
            chapter_title=chapter_title,
            ai_summary=summary or None,
            merged_raw_content=str(upload_record.raw_content or "").strip(),
            cleaned_content="",
            content_version=1,
            first_uploaded_date=upload_date,
            last_uploaded_date=upload_date,
            next_due_date=upload_date + timedelta(days=REVIEW_INTERVAL_DAYS[0]),
            review_status="pending",
        )
        db.add(review_chapter)
        db.flush()
    else:
        review_chapter.book = book or review_chapter.book
        review_chapter.chapter_number = str(getattr(chapter, "chapter_number", "") or review_chapter.chapter_number or "").strip()
        review_chapter.chapter_title = chapter_title or review_chapter.chapter_title
        review_chapter.ai_summary = summary or review_chapter.ai_summary
        review_chapter.merged_raw_content = _append_merged_content(
            review_chapter.merged_raw_content,
            upload_record.raw_content,
            upload_date=upload_date,
        )
        review_chapter.last_uploaded_date = upload_date
        review_chapter.content_version = int(review_chapter.content_version or 0) + 1
        review_chapter.next_due_date = upload_date + timedelta(days=REVIEW_INTERVAL_DAYS[0])
        review_chapter.review_status = "pending"

    if not is_update:
        review_chapter.merged_raw_content = str(upload_record.raw_content or "").strip()

    review_chapter.cleaned_content = clean_review_content(review_chapter.merged_raw_content)
    unit_drafts = build_review_units(review_chapter.cleaned_content, chapter_title=review_chapter.chapter_title)
    if not unit_drafts:
        cleaned = review_chapter.cleaned_content or str(upload_record.raw_content or "").strip()
        unit_drafts = [
            ReviewUnitDraft(
                unit_index=1,
                unit_title=f"{review_chapter.chapter_title} · 单元 1",
                raw_text=cleaned,
                cleaned_text=cleaned,
                excerpt=cleaned[:180].strip(),
                char_count=len(cleaned),
                estimated_minutes=estimate_unit_minutes(cleaned),
            )
        ]

    if is_update:
        for unit in review_chapter.units:
            if unit.is_active:
                unit.is_active = False
                unit.review_status = "archived"
        for task in review_chapter.tasks:
            if task.status in OPEN_TASK_STATUSES:
                task.status = "cancelled"
                task.updated_at = datetime.now()

    version = int(review_chapter.content_version or 1)
    first_due_date = upload_date + timedelta(days=REVIEW_INTERVAL_DAYS[0])
    for draft in unit_drafts:
        review_chapter.units.append(
            ChapterReviewUnit(
                content_version=version,
                unit_index=draft.unit_index,
                unit_title=draft.unit_title,
                raw_text=draft.raw_text,
                cleaned_text=draft.cleaned_text,
                excerpt=draft.excerpt,
                char_count=draft.char_count,
                estimated_minutes=draft.estimated_minutes,
                next_round=1,
                completed_rounds=0,
                next_due_date=first_due_date,
                review_status="pending",
                carry_over_count=0,
                is_active=True,
            )
        )

    _recompute_chapter_due(review_chapter)
    return review_chapter


def _serialize_task_summary(task: ChapterReviewTask, *, today: date) -> Dict[str, Any]:
    review_chapter = task.review_chapter
    unit = task.unit
    answered_count = sum(1 for question in task.questions if str(question.user_answer or "").strip())
    total_questions = int(task.question_count or len(task.questions) or QUESTIONS_PER_REVIEW_UNIT)
    remaining_questions = max(total_questions - answered_count, 0)
    carry_over_days = max((today - task.scheduled_for).days, 0)
    if carry_over_days > 0 and task.status != "completed":
        due_reason = f"昨日未完成，顺延 {carry_over_days} 天"
    else:
        due_reason = task.due_reason

    mastery_status = unit.review_status or "pending"
    if task.status == "in_progress":
        mastery_status = "in_progress"
    elif task.status == "awaiting_choice":
        mastery_status = "awaiting_choice"

    return {
        "task_id": int(task.id),
        "chapter_id": review_chapter.chapter_id,
        "book": review_chapter.book,
        "chapter_title": review_chapter.chapter_title,
        "unit_id": int(unit.id),
        "unit_title": unit.unit_title,
        "unit_index": int(unit.unit_index),
        "excerpt": unit.excerpt or unit.cleaned_text[:180],
        "summary": review_chapter.ai_summary or "",
        "estimated_minutes": int(task.estimated_minutes or unit.estimated_minutes or 0),
        "due_reason": due_reason,
        "mastery_status": mastery_status,
        "next_round": int(unit.next_round or 1),
        "answered_count": answered_count,
        "question_count": total_questions,
        "remaining_questions": remaining_questions,
        "resume_position": int(task.resume_position or 0),
        "scheduled_for": task.scheduled_for.isoformat(),
        "carry_over_days": carry_over_days,
        "status": task.status,
        "ai_recommended_status": task.ai_recommended_status,
        "user_selected_status": task.user_selected_status,
        "grading_score": task.grading_score,
    }


def _candidate_bucket(unit: ChapterReviewUnit, *, target_date: date) -> tuple[str, int]:
    if unit.next_due_date and unit.next_due_date <= target_date:
        overdue_days = (target_date - unit.next_due_date).days
        return "due", overdue_days

    if unit.last_reviewed_at:
        dormant_days = (target_date - unit.last_reviewed_at).days
        if dormant_days >= 21:
            return "stale", dormant_days

    return "new", 0


def _create_task_for_unit(
    db: Session,
    *,
    actor_key: str,
    unit: ChapterReviewUnit,
    target_date: date,
    bucket: str,
    extra_score: int,
) -> ChapterReviewTask:
    due_reason_map = {
        "due": f"第 {int(unit.next_round or 1)} 轮到期复习",
        "stale": "很久没复习，重新唤醒",
        "new": "最近新上传，首次进入复习",
    }
    task = ChapterReviewTask(
        actor_key=actor_key,
        review_chapter_id=int(unit.review_chapter_id),
        unit_id=int(unit.id),
        content_version=int(unit.content_version or 1),
        scheduled_for=target_date,
        due_reason=due_reason_map[bucket],
        priority_bucket=bucket,
        priority_score=float(extra_score),
        estimated_minutes=int(unit.estimated_minutes or 0),
        question_count=QUESTIONS_PER_REVIEW_UNIT,
        status="pending",
        source_label=due_reason_map[bucket],
    )
    db.add(task)
    return task


def ensure_daily_review_plan(
    db: Session,
    *,
    actor_key: str,
    target_date: Optional[date] = None,
    time_budget_minutes: int = DEFAULT_REVIEW_TIME_BUDGET_MINUTES,
) -> Dict[str, Any]:
    review_date = target_date or date.today()
    budget = max(15, int(time_budget_minutes or DEFAULT_REVIEW_TIME_BUDGET_MINUTES))

    open_tasks = (
        db.query(ChapterReviewTask)
        .options(
            joinedload(ChapterReviewTask.review_chapter),
            joinedload(ChapterReviewTask.unit),
            joinedload(ChapterReviewTask.questions),
        )
        .filter(
            ChapterReviewTask.actor_key == actor_key,
            ChapterReviewTask.status.in_(sorted(OPEN_TASK_STATUSES)),
        )
        .order_by(ChapterReviewTask.status.desc(), ChapterReviewTask.scheduled_for.asc(), ChapterReviewTask.id.asc())
        .all()
    )

    open_by_unit = {int(task.unit_id): task for task in open_tasks}
    selected_tasks: list[ChapterReviewTask] = list(open_tasks)
    used_minutes = int(sum(task.estimated_minutes or 0 for task in selected_tasks))

    active_units = (
        db.query(ChapterReviewUnit)
        .options(joinedload(ChapterReviewUnit.review_chapter))
        .join(ChapterReviewChapter, ChapterReviewChapter.id == ChapterReviewUnit.review_chapter_id)
        .filter(
            ChapterReviewChapter.actor_key == actor_key,
            ChapterReviewUnit.is_active.is_(True),
        )
        .order_by(ChapterReviewChapter.updated_at.desc(), ChapterReviewUnit.unit_index.asc())
        .all()
    )

    candidates: list[tuple[str, int, ChapterReviewUnit]] = []
    for unit in active_units:
        if int(unit.id) in open_by_unit:
            continue
        if int(unit.completed_rounds or 0) >= len(REVIEW_INTERVAL_DAYS):
            continue
        bucket, score = _candidate_bucket(unit, target_date=review_date)
        candidates.append((bucket, score, unit))

    bucket_priority = {"due": 0, "stale": 1, "new": 2}
    candidates.sort(
        key=lambda item: (
            bucket_priority.get(item[0], 9),
            -int(item[1] or 0),
            item[2].next_due_date or date.max,
            -(item[2].char_count or 0),
            int(item[2].id),
        )
    )

    for bucket, score, unit in candidates:
        estimated = int(unit.estimated_minutes or 0)
        if selected_tasks and used_minutes + estimated > budget:
            continue
        if not selected_tasks and estimated > budget:
            task = _create_task_for_unit(db, actor_key=actor_key, unit=unit, target_date=review_date, bucket=bucket, extra_score=score)
            selected_tasks.append(task)
            used_minutes += estimated
            break
        if used_minutes + estimated <= budget:
            task = _create_task_for_unit(db, actor_key=actor_key, unit=unit, target_date=review_date, bucket=bucket, extra_score=score)
            selected_tasks.append(task)
            used_minutes += estimated

    db.flush()

    refreshed_tasks = (
        db.query(ChapterReviewTask)
        .options(
            joinedload(ChapterReviewTask.review_chapter),
            joinedload(ChapterReviewTask.unit),
            joinedload(ChapterReviewTask.questions),
        )
        .filter(ChapterReviewTask.id.in_([int(task.id) for task in selected_tasks]) if selected_tasks else False)
        .order_by(ChapterReviewTask.scheduled_for.asc(), ChapterReviewTask.id.asc())
        .all()
        if selected_tasks
        else []
    )

    completed_tasks = (
        db.query(ChapterReviewTask)
        .filter(
            ChapterReviewTask.actor_key == actor_key,
            ChapterReviewTask.status == "completed",
            ChapterReviewTask.completed_at.isnot(None),
        )
        .all()
    )
    completed_today_count = sum(
        1
        for task in completed_tasks
        if task.completed_at and task.completed_at.date() == review_date
    )

    carry_over_count = sum(1 for task in refreshed_tasks if task.scheduled_for < review_date)
    remaining_minutes = max(budget - used_minutes, 0)
    tasks_payload = [_serialize_task_summary(task, today=review_date) for task in refreshed_tasks]

    return {
        "date": review_date.isoformat(),
        "time_budget_minutes": budget,
        "estimated_total_minutes": used_minutes,
        "remaining_minutes": remaining_minutes,
        "task_count": len(tasks_payload),
        "carry_over_count": carry_over_count,
        "completed_today_count": completed_today_count,
        "tasks": tasks_payload,
    }


def _fallback_questions_from_text(unit: ChapterReviewUnit, *, question_count: int) -> list[dict[str, Any]]:
    source_text = unit.cleaned_text or unit.raw_text or unit.excerpt or unit.unit_title
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?；;\n])", source_text)
        if item and item.strip()
    ]
    if not sentences:
        sentences = [source_text.strip()]

    question_bank: list[dict[str, Any]] = []
    for index in range(question_count):
        sentence = sentences[index % len(sentences)].strip()
        clue = sentence[:52] if len(sentence) > 52 else sentence
        key_points = [part.strip() for part in re.split(r"[，、；;。]", sentence) if part.strip()][:3]
        if not key_points:
            key_points = [sentence[:24]]
        question_bank.append(
            {
                "prompt": f"请根据复习材料，概述以下内容的核心要点：{clue}",
                "reference_answer": sentence,
                "key_points": key_points,
                "explanation": "作答时尽量覆盖原文中的核心事实、概念关系和结论。",
                "source_excerpt": sentence[:120],
            }
        )
    return question_bank


async def _ai_generate_questions(unit: ChapterReviewUnit, summary: str, *, question_count: int) -> list[dict[str, Any]]:
    prompt = f"""你是医学复习教练。请严格基于给定复习材料，生成 {question_count} 道以简答题为主的复习题。

【章节】{unit.unit_title}
【章节摘要】{summary or "无"}
【复习材料】
{unit.cleaned_text}

要求：
1. 每道题都必须可以从复习材料直接回答，不要引入材料外知识。
2. 题目以简答题为主，聚焦定义、机制、鉴别点、流程、因果关系。
3. 参考答案必须简洁准确，长度控制在 60-140 字。
4. key_points 只保留 2-4 个关键点。
5. explanation 用一句话说明为什么这样答。
6. source_excerpt 必须摘自原文，方便回看。
"""

    schema = {
        "questions": [
            {
                "prompt": "题目",
                "reference_answer": "参考答案",
                "key_points": ["要点1", "要点2"],
                "explanation": "解析",
                "source_excerpt": "原文定位片段",
            }
        ]
    }

    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=5200,
        temperature=0.25,
        timeout=150,
        use_heavy=True,
    )
    return list(result.get("questions") or [])[:question_count]


async def ensure_task_questions(db: Session, *, actor_key: str, task_id: int) -> ChapterReviewTask:
    task = (
        db.query(ChapterReviewTask)
        .options(
            joinedload(ChapterReviewTask.review_chapter),
            joinedload(ChapterReviewTask.unit),
            joinedload(ChapterReviewTask.questions),
        )
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    if task.questions:
        return task

    question_count = int(task.question_count or QUESTIONS_PER_REVIEW_UNIT)
    try:
        generated = await _ai_generate_questions(task.unit, task.review_chapter.ai_summary or "", question_count=question_count)
        if len(generated) < question_count:
            raise ValueError("AI 返回题目数量不足")
    except Exception:
        generated = _fallback_questions_from_text(task.unit, question_count=question_count)

    for index, item in enumerate(generated[:question_count], start=1):
        task.questions.append(
            ChapterReviewTaskQuestion(
                position=index,
                prompt=str(item.get("prompt") or f"请概述 {task.review_chapter.chapter_title} 的关键要点").strip(),
                reference_answer=str(item.get("reference_answer") or "").strip() or (task.unit.excerpt or task.unit.cleaned_text[:120]),
                key_points=list(item.get("key_points") or []),
                explanation=str(item.get("explanation") or "").strip() or "请结合原文关键事实作答。",
                source_excerpt=str(item.get("source_excerpt") or "").strip() or (task.unit.excerpt or task.unit.cleaned_text[:120]),
            )
        )

    db.flush()
    return task


def serialize_task_detail(task: ChapterReviewTask) -> Dict[str, Any]:
    payload = _serialize_task_summary(task, today=date.today())
    payload["content_version"] = int(
        task.content_version
        or getattr(task.unit, "content_version", 0)
        or getattr(task.review_chapter, "content_version", 0)
        or 1
    )
    payload["source_content"] = task.unit.raw_text or task.unit.cleaned_text or ""
    payload["questions"] = [
        {
            "id": int(question.id),
            "position": int(question.position),
            "prompt": question.prompt,
            "reference_answer": question.reference_answer,
            "key_points": list(question.key_points or []),
            "explanation": question.explanation or "",
            "source_excerpt": question.source_excerpt or "",
            "user_answer": question.user_answer or "",
            "ai_score": question.ai_score,
            "ai_feedback": question.ai_feedback or "",
            "good_points": list(question.good_points or []),
            "missing_points": list(question.missing_points or []),
            "improvement_suggestion": question.improvement_suggestion or "",
        }
        for question in sorted(task.questions, key=lambda item: item.position)
    ]
    return payload


def save_task_progress(
    db: Session,
    *,
    actor_key: str,
    task_id: int,
    answers: List[Dict[str, Any]],
    resume_position: int,
) -> Dict[str, Any]:
    task = (
        db.query(ChapterReviewTask)
        .options(joinedload(ChapterReviewTask.questions), joinedload(ChapterReviewTask.review_chapter), joinedload(ChapterReviewTask.unit))
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    if task.status == "cancelled":
        raise HTTPException(status_code=409, detail="该任务已因章节更新而失效")

    question_map = {int(question.id): question for question in task.questions}
    position_map = {int(question.position): question for question in task.questions}
    for item in answers:
        question = None
        if item.get("question_id") is not None:
            question = question_map.get(int(item["question_id"]))
        if question is None and item.get("position") is not None:
            question = position_map.get(int(item["position"]))
        if question is None:
            continue
        question.user_answer = str(item.get("user_answer") or "").strip()
        question.updated_at = datetime.now()

    task.resume_position = max(int(resume_position or 0), 0)
    task.answered_count = sum(1 for question in task.questions if str(question.user_answer or "").strip())
    task.status = "in_progress" if task.answered_count else "pending"
    if task.started_at is None and task.answered_count:
        task.started_at = datetime.now()
    task.updated_at = datetime.now()
    task.unit.review_status = "in_progress" if task.answered_count else task.unit.review_status
    db.flush()
    return serialize_task_detail(task)


def _key_point_coverage_score(question: ChapterReviewTaskQuestion) -> tuple[int, list[str], list[str]]:
    answer = str(question.user_answer or "").strip()
    key_points = [str(item or "").strip() for item in list(question.key_points or []) if str(item or "").strip()]
    if not answer:
        return 0, [], key_points
    if not key_points:
        if len(answer) >= max(8, len(question.reference_answer or "") // 4):
            return 70, [answer[:24]], []
        return 30, [], []

    matched: list[str] = []
    missing: list[str] = []
    for point in key_points:
        if point and point in answer:
            matched.append(point)
        else:
            missing.append(point)
    score = int(round(len(matched) / max(len(key_points), 1) * 100))
    return score, matched, missing


async def _ai_grade_questions(task: ChapterReviewTask) -> Dict[str, Any]:
    question_payload = []
    for question in sorted(task.questions, key=lambda item: item.position):
        question_payload.append(
            {
                "position": int(question.position),
                "prompt": question.prompt,
                "reference_answer": question.reference_answer,
                "key_points": list(question.key_points or []),
                "source_excerpt": question.source_excerpt or "",
                "user_answer": question.user_answer or "",
            }
        )

    prompt = f"""你是医学简答题批改老师。请严格根据给定参考答案和原文要点，对学生回答进行逐题打分，并给出整体建议。

【章节】{task.review_chapter.chapter_title}
【单元】{task.unit.unit_title}
【题目列表】
{json.dumps(question_payload, ensure_ascii=False, indent=2)}

要求：
1. score 取 0-100。
2. good_points / missing_points 都尽量引用参考答案中的要点。
3. feedback 用一句话指出当前题的判断。
4. suggestion 给一句可执行建议。
5. recommended_status 只能是 weak / normal / mastered。
"""

    schema = {
        "results": [
            {
                "position": 1,
                "score": 80,
                "good_points": ["答到了什么"],
                "missing_points": ["漏掉了什么"],
                "feedback": "这一题的判断",
                "suggestion": "改进建议",
            }
        ],
        "recommended_status": "normal",
        "overall_feedback": "整体建议",
    }

    return await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=4200,
        temperature=0.15,
        timeout=150,
        use_heavy=False,
    )


async def grade_task_answers(db: Session, *, actor_key: str, task_id: int) -> Dict[str, Any]:
    task = (
        db.query(ChapterReviewTask)
        .options(joinedload(ChapterReviewTask.questions), joinedload(ChapterReviewTask.review_chapter), joinedload(ChapterReviewTask.unit))
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    if not task.questions:
        raise HTTPException(status_code=400, detail="请先生成复习题目")

    missing_positions = [
        int(question.position)
        for question in task.questions
        if not str(question.user_answer or "").strip()
    ]
    if missing_positions:
        raise HTTPException(status_code=400, detail=f"还有 {len(missing_positions)} 道题未作答，不能完成本轮复习")

    try:
        grading = await _ai_grade_questions(task)
        ai_results = {int(item.get("position")): item for item in list(grading.get("results") or [])}
        recommended_status = str(grading.get("recommended_status") or "normal").strip().lower()
        overall_feedback = str(grading.get("overall_feedback") or "").strip()
    except Exception:
        ai_results = {}
        overall_feedback = "AI 批改不可用，已切换到本地匹配规则。"
        recommended_status = "normal"

    total_score = 0
    for question in task.questions:
        payload = ai_results.get(int(question.position))
        if payload is None:
            score, matched, missing = _key_point_coverage_score(question)
            feedback = "回答覆盖了较多原文要点。" if score >= 70 else "回答还不够完整，建议回看原文定位片段。"
            suggestion = "优先补全漏掉的关键点，再重新组织成完整表述。"
            good_points = matched
            missing_points = missing
        else:
            score = int(payload.get("score") or 0)
            feedback = str(payload.get("feedback") or "").strip()
            suggestion = str(payload.get("suggestion") or "").strip()
            good_points = [str(item).strip() for item in list(payload.get("good_points") or []) if str(item).strip()]
            missing_points = [str(item).strip() for item in list(payload.get("missing_points") or []) if str(item).strip()]

        question.ai_score = max(0, min(score, 100))
        question.ai_feedback = feedback
        question.good_points = good_points
        question.missing_points = missing_points
        question.improvement_suggestion = suggestion
        question.judged_at = datetime.now()
        total_score += int(question.ai_score or 0)

    average_score = round(total_score / max(len(task.questions), 1), 1)
    if recommended_status not in {"weak", "normal", "mastered"}:
        if average_score >= 85:
            recommended_status = "mastered"
        elif average_score >= 60:
            recommended_status = "normal"
        else:
            recommended_status = "weak"

    task.grading_score = average_score
    task.ai_recommended_status = recommended_status
    task.graded_at = datetime.now()
    task.status = "awaiting_choice"
    task.updated_at = datetime.now()
    db.flush()

    payload = serialize_task_detail(task)
    payload["overall_feedback"] = overall_feedback
    payload["ai_recommended_status"] = recommended_status
    payload["grading_score"] = average_score
    return payload


def complete_task_with_status(
    db: Session,
    *,
    actor_key: str,
    task_id: int,
    selected_status: str,
) -> Dict[str, Any]:
    normalized_status = str(selected_status or "").strip().lower()
    if normalized_status not in {"weak", "normal", "mastered"}:
        raise HTTPException(status_code=400, detail="复习状态只能是 weak / normal / mastered")

    task = (
        db.query(ChapterReviewTask)
        .options(joinedload(ChapterReviewTask.questions), joinedload(ChapterReviewTask.review_chapter), joinedload(ChapterReviewTask.unit))
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    if task.status == "cancelled":
        raise HTTPException(status_code=409, detail="该任务已因章节更新而失效")
    if any(not str(question.user_answer or "").strip() for question in task.questions):
        raise HTTPException(status_code=400, detail="还有未完成的题目，不能结束本轮复习")

    today = date.today()
    unit = task.unit
    current_round = int(unit.next_round or 1)
    unit.last_reviewed_at = today
    unit.last_status_label = normalized_status
    unit.carry_over_count = 0

    if normalized_status == "weak":
        unit.review_status = "weak"
        unit.next_due_date = today + timedelta(days=1)
    else:
        unit.completed_rounds = max(int(unit.completed_rounds or 0), current_round)
        if current_round >= len(REVIEW_INTERVAL_DAYS):
            unit.review_status = "completed"
            unit.next_due_date = None
        else:
            next_round = current_round + 1
            unit.next_round = next_round
            unit.next_due_date = today + timedelta(days=REVIEW_INTERVAL_DAYS[next_round - 1])
            unit.review_status = "pending" if normalized_status == "normal" else "mastered"

    task.user_selected_status = normalized_status
    task.status = "completed"
    task.completed_at = datetime.now()
    task.updated_at = datetime.now()
    task.resume_position = 0
    task.answered_count = sum(1 for question in task.questions if str(question.user_answer or "").strip())

    task.review_chapter.last_reviewed_at = today
    _recompute_chapter_due(task.review_chapter)
    db.flush()
    return serialize_task_detail(task)


def _serialize_pdf_task_block(task: ChapterReviewTask) -> Dict[str, Any]:
    return {
        "chapter_title": task.review_chapter.chapter_title,
        "book": task.review_chapter.book,
        "unit_title": task.unit.unit_title,
        "due_reason": task.due_reason,
        "summary": task.review_chapter.ai_summary or "",
        "excerpt": task.unit.excerpt or "",
        "questions": [
            {
                "position": int(question.position),
                "prompt": question.prompt,
                "reference_answer": question.reference_answer,
                "explanation": question.explanation or "",
                "source_excerpt": question.source_excerpt or "",
            }
            for question in sorted(task.questions, key=lambda item: item.position)
        ],
    }


def build_review_pdf(
    *,
    review_date: date,
    tasks: List[ChapterReviewTask],
    time_budget_minutes: int,
) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF 依赖不可用: {exc}") from exc

    from routers.wrong_answers_v2 import _get_embedded_pdf_font_name

    font_name = _get_embedded_pdf_font_name()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReviewPdfTitle", parent=styles["Title"], fontName=font_name, fontSize=16, leading=20)
    meta_style = ParagraphStyle("ReviewPdfMeta", parent=styles["Normal"], fontName=font_name, fontSize=9, leading=12, textColor=colors.HexColor("#475569"))
    section_style = ParagraphStyle("ReviewPdfSection", parent=styles["Heading2"], fontName=font_name, fontSize=12, leading=15, textColor=colors.HexColor("#0F172A"))
    question_style = ParagraphStyle("ReviewPdfQuestion", parent=styles["BodyText"], fontName=font_name, fontSize=9.6, leading=13.5, textColor=colors.HexColor("#111827"))
    answer_style = ParagraphStyle("ReviewPdfAnswer", parent=styles["BodyText"], fontName=font_name, fontSize=8.8, leading=12.2, textColor=colors.HexColor("#0F766E"))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=16 * mm,
        bottomMargin=12 * mm,
        title=f"今日复习计划 {review_date.isoformat()}",
    )

    story: list[Any] = [
        Paragraph("今日复习题单", title_style),
        Paragraph(
            f"日期：{review_date.isoformat()}　预计总时长：{time_budget_minutes} 分钟　章节单元：{len(tasks)} 个",
            meta_style,
        ),
        Spacer(1, 4 * mm),
    ]

    appendix: list[Any] = [
        PageBreak(),
        Paragraph("答案解析", title_style),
        Paragraph(f"日期：{review_date.isoformat()}　用于打印后自行批改。", meta_style),
        Spacer(1, 4 * mm),
    ]

    for index, task in enumerate(tasks, start=1):
        block = _serialize_pdf_task_block(task)
        story.extend([
            Paragraph(f"{index}. {block['chapter_title']} / {block['unit_title']}", section_style),
            Paragraph(f"到期原因：{block['due_reason']}　科目：{block['book']}", meta_style),
        ])
        if block["summary"]:
            story.append(Paragraph(f"摘要：{block['summary']}", meta_style))
        if block["excerpt"]:
            story.append(Paragraph(f"原文定位：{block['excerpt']}", meta_style))
        story.append(Spacer(1, 2 * mm))

        appendix.extend([
            Paragraph(f"{index}. {block['chapter_title']} / {block['unit_title']}", section_style),
            Paragraph(f"到期原因：{block['due_reason']}", meta_style),
            Spacer(1, 1.5 * mm),
        ])

        for question in block["questions"]:
            story.extend([
                Paragraph(f"{index}.{question['position']}　{question['prompt']}", question_style),
                Paragraph("答题区：" + "＿" * 52, meta_style),
                Spacer(1, 2.2 * mm),
            ])
            appendix.extend([
                Paragraph(f"{index}.{question['position']}　{question['prompt']}", question_style),
                Paragraph(f"参考答案：{question['reference_answer']}", answer_style),
                Paragraph(f"解析：{question['explanation']}", meta_style),
            ])
            if question["source_excerpt"]:
                appendix.append(Paragraph(f"原文定位：{question['source_excerpt']}", meta_style))
            appendix.append(Spacer(1, 1.8 * mm))

        story.extend([HRFlowable(width="100%", color=colors.HexColor("#CBD5E1")), Spacer(1, 3 * mm)])
        appendix.extend([HRFlowable(width="100%", color=colors.HexColor("#CBD5E1")), Spacer(1, 3 * mm)])

    doc.build(story + appendix)
    return buffer.getvalue()


async def export_today_review_pdf(
    db: Session,
    *,
    actor_key: str,
    target_date: Optional[date] = None,
    time_budget_minutes: int = DEFAULT_REVIEW_TIME_BUDGET_MINUTES,
) -> bytes:
    plan = ensure_daily_review_plan(
        db,
        actor_key=actor_key,
        target_date=target_date,
        time_budget_minutes=time_budget_minutes,
    )
    task_ids = [int(item["task_id"]) for item in plan["tasks"]]
    if not task_ids:
        raise HTTPException(status_code=404, detail="今天没有可导出的复习内容")

    tasks: list[ChapterReviewTask] = []
    for task_id in task_ids:
        task = await ensure_task_questions(db, actor_key=actor_key, task_id=task_id)
        tasks.append(task)

    return build_review_pdf(
        review_date=target_date or date.today(),
        tasks=tasks,
        time_budget_minutes=plan["estimated_total_minutes"] or time_budget_minutes,
    )
