"""
学习轨迹记录 API
用于记录和查询详细的学习过程
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
import os
import re
import uuid

from models import get_db, Chapter
from learning_tracking_models import (
    LearningSession, LearningActivity, QuestionRecord,
    DailyLearningLog, LearningInsight, SessionStatus, ActivityType,
    WrongAnswerV2, make_fingerprint, INVALID_CHAPTER_IDS
)

router = APIRouter(prefix="/api/tracking", tags=["learning_tracking"])


from pydantic import BaseModel

class StartSessionRequest(BaseModel):
    session_type: str
    chapter_id: Optional[str] = None
    title: Optional[str] = None
    uploaded_content: Optional[str] = None
    knowledge_point: Optional[str] = None

class RecordActivityRequest(BaseModel):
    activity_type: str
    activity_name: str
    data: Dict[str, Any]

class RecordQuestionRequest(BaseModel):
    question_index: int
    question_type: str
    difficulty: str
    question_text: str
    options: Dict[str, str]
    correct_answer: str
    user_answer: str
    is_correct: bool
    confidence: Optional[str] = None
    explanation: Optional[str] = None
    key_point: Optional[str] = None
    time_spent_seconds: int = 0

class CompleteSessionRequest(BaseModel):
    score: int
    total_questions: int


DEFAULT_OCR_PLAN_DIR = Path(r"C:\Users\35456\Desktop\学习相关\01.每日计划\ocr")

OCR_CATEGORY_KEYWORDS = {
    "live": ["直播"],
    "preview": ["预习"],
    "review": ["复习"],
    "quiz": ["做题", "真题"],
    "rolling": ["滚动复习"],
    "exam": ["考试", "阶段考试"],
}

OCR_IGNORED_PREFIXES = (
    "Source:",
    "Time:",
    "=================================================="
)

MASTER_PLAN_PHASES = [
    {
        "id": "basic-physiology",
        "stage": "基础强化阶段",
        "type": "range",
        "start": "02-24",
        "end": "04-17",
        "title": "生理",
        "details": "含思维导图、分章节真题、专题串联",
    },
    {
        "id": "basic-pathology",
        "stage": "基础强化阶段",
        "type": "range",
        "start": "04-21",
        "end": "05-07",
        "title": "病理",
        "details": "含思维导图、分章节真题、专题串联",
    },
    {
        "id": "basic-internal",
        "stage": "基础强化阶段",
        "type": "range",
        "start": "05-11",
        "end": "06-30",
        "title": "内科含诊断+部分外科",
        "details": "含思维导图、分章节真题、专题串联",
    },
    {
        "id": "basic-surgery",
        "stage": "基础强化阶段",
        "type": "range",
        "start": "07-05",
        "end": "08-03",
        "title": "外科",
        "details": "含思维导图、分章节真题、专题串联",
    },
    {
        "id": "basic-biochem",
        "stage": "基础强化阶段",
        "type": "range",
        "start": "08-06",
        "end": "08-24",
        "title": "生化",
        "details": "含思维导图、分章节真题、专题串联",
    },
    {
        "id": "basic-weak-review",
        "stage": "基础强化阶段",
        "type": "range",
        "start": "08-28",
        "end": "08-29",
        "title": "各学科易错小结",
        "details": "",
    },
    {
        "id": "sprint-10y",
        "stage": "冲刺押题阶段",
        "type": "range",
        "start": "09-15",
        "end": "10-24",
        "title": "冲刺十年真题（按年份）",
        "details": "",
    },
    {
        "id": "sprint-case",
        "stage": "冲刺押题阶段",
        "type": "range",
        "start": "11-02",
        "end": "11-22",
        "title": "各学科病例分析 & 狂背",
        "details": "",
    },
    {
        "id": "sprint-sets",
        "stage": "冲刺押题阶段",
        "type": "range",
        "start": "11-27",
        "end": "12-13",
        "title": "四套卷",
        "details": "",
    },
]

MASTER_PLAN_MILESTONES = [
    {"id": "exam-physiology", "stage": "基础强化阶段", "date": "04-20", "title": "生理阶段考试"},
    {"id": "exam-path-internal", "stage": "基础强化阶段", "date": "07-04", "title": "病理 & 内科阶段考试"},
    {"id": "humanities", "stage": "基础强化阶段", "date": "08-25", "title": "人文"},
    {"id": "exam-surgery-biochem", "stage": "基础强化阶段", "date": "08-30", "title": "外科 & 生化阶段考试"},
    {"id": "exam-basic-mock", "stage": "基础强化阶段", "date": "09-11", "title": "基础强化阶段摸底考试"},
    {"id": "doc-top-up", "stage": "冲刺押题阶段", "date": "09-30", "title": "精选执业医师真题（上）"},
    {"id": "exam-sprint-mock", "stage": "冲刺押题阶段", "date": "10-27", "title": "冲刺押题阶段摸底考试"},
    {"id": "doc-bottom-up", "stage": "冲刺押题阶段", "date": "11-24", "title": "精选执业医师真题（下）"},
    {"id": "five-hour-1", "stage": "冲刺押题阶段", "date": "12-17", "title": "五小时（第一场）"},
    {"id": "five-hour-2", "stage": "冲刺押题阶段", "date": "12-20", "title": "五小时（第二场）"},
]


def _normalize_valid_chapter_id(chapter_id: Optional[str]) -> Optional[str]:
    normalized = str(chapter_id or "").strip()
    if not normalized or normalized in INVALID_CHAPTER_IDS:
        return None
    return normalized


def _normalize_question_options(options: Any) -> Dict[str, str]:
    if not isinstance(options, dict):
        return {}

    normalized: Dict[str, str] = {}
    for key, value in options.items():
        option_key = re.sub(r"[^A-E]", "", str(key or "").upper())[:1]
        option_value = str(value or "").strip()
        if option_key and option_value:
            normalized[option_key] = option_value

    return {key: normalized[key] for key in ["A", "B", "C", "D", "E"] if key in normalized}


def _build_question_record_fingerprint(record: QuestionRecord) -> str:
    return make_fingerprint((record.question_text or "").strip())


def _build_question_record_fingerprint_candidates(record: QuestionRecord) -> List[str]:
    candidates: List[str] = []
    primary = _build_question_record_fingerprint(record)
    if primary:
        candidates.append(primary)

    question_text = (record.question_text or "").strip()
    options = _normalize_question_options(record.options)
    if question_text and options:
        option_snapshot = "||".join(
            f"{key}:{options.get(key, '').strip()}"
            for key in ["A", "B", "C", "D", "E"]
            if key in options
        )
        option_fingerprint = make_fingerprint(f"{question_text}||{option_snapshot}")
        if option_fingerprint not in candidates:
            candidates.append(option_fingerprint)

    return candidates


def _display_key_point(record: QuestionRecord) -> str:
    key_point = (record.key_point or "").strip()
    if key_point:
        return key_point

    question_text = re.sub(r"\s+", " ", (record.question_text or "").strip())
    if question_text:
        return f"考点待提取：{question_text[:20]}"

    return "考点待提取：未命名题目"


def _question_record_sort_key(record: QuestionRecord) -> Tuple[datetime, int]:
    return (record.answered_at or datetime.min, record.id or 0)


def _group_records_by_session(records: List[QuestionRecord]) -> Dict[str, List[QuestionRecord]]:
    grouped: Dict[str, List[QuestionRecord]] = {}
    for record in records:
        grouped.setdefault(record.session_id, []).append(record)
    return grouped


def _load_unique_question_records(
    db: Session,
    session_ids: Optional[List[str]] = None
) -> List[QuestionRecord]:
    query = db.query(QuestionRecord)
    if session_ids is not None:
        if not session_ids:
            return []
        query = query.filter(QuestionRecord.session_id.in_(session_ids))

    latest_by_key: Dict[Tuple[str, int], QuestionRecord] = {}
    for record in query.all():
        key = (record.session_id, record.question_index)
        existing = latest_by_key.get(key)
        if existing is None or _question_record_sort_key(record) >= _question_record_sort_key(existing):
            latest_by_key[key] = record

    return sorted(latest_by_key.values(), key=lambda item: (item.session_id, item.question_index))


def _build_record_stats(records: List[QuestionRecord]) -> Dict[str, int]:
    stats = {
        "total_questions": 0,
        "answered_questions": 0,
        "correct_count": 0,
        "wrong_count": 0,
        "sure_count": 0,
        "unsure_count": 0,
        "no_count": 0,
    }

    for record in records:
        stats["total_questions"] += 1
        stats["answered_questions"] += 1
        if record.is_correct:
            stats["correct_count"] += 1
        else:
            stats["wrong_count"] += 1

        confidence = (record.confidence or "").strip()
        if confidence == "sure":
            stats["sure_count"] += 1
        elif confidence == "unsure":
            stats["unsure_count"] += 1
        elif confidence == "no":
            stats["no_count"] += 1

    return stats


def _sync_session_question_stats(
    session: LearningSession,
    db: Session
) -> Tuple[List[QuestionRecord], Dict[str, int]]:
    records = _load_unique_question_records(db, session_ids=[session.id])
    stats = _build_record_stats(records)

    session.answered_questions = stats["answered_questions"]
    session.correct_count = stats["correct_count"]
    session.wrong_count = stats["wrong_count"]
    session.sure_count = stats["sure_count"]
    session.unsure_count = stats["unsure_count"]
    session.no_count = stats["no_count"]
    session.updated_at = datetime.now()

    return records, stats


def _normalize_lookup_token(value: Optional[str]) -> str:
    return re.sub(r"[\s_\-:：]+", "", str(value or "").strip().lower())


_CHAPTER_PREFIX_ALIASES: Dict[str, List[str]] = {
    "physio": ["physiology", "physio"],
    "physiology": ["physiology", "physio"],
    "patho": ["pathology", "patho"],
    "pathology": ["pathology", "patho"],
    "internal": ["internal_medicine", "internal"],
    "internal_medicine": ["internal_medicine", "internal"],
    "biochem": ["biochemistry", "biochem"],
    "biochemistry": ["biochemistry", "biochem"],
    "surgery": ["surgery"],
    "diagnostics": ["diagnostics"],
}


def _expand_chapter_id_variants(chapter_id: str) -> List[str]:
    """将 physio_ch10 展开为 [physio_ch10, physiology_ch10] 等变体"""
    match = re.match(r"^([a-z_]+?)_ch(.+)$", chapter_id.lower())
    if not match:
        return [chapter_id]
    prefix, suffix = match.group(1), match.group(2)
    aliases = _CHAPTER_PREFIX_ALIASES.get(prefix, [prefix])
    return [f"{alias}_ch{suffix}" for alias in aliases]


def _resolve_chapter_id_from_map(
    chapter_id: Optional[str],
    chapter_map: Dict[str, Chapter]
) -> Optional[str]:
    normalized = _normalize_valid_chapter_id(chapter_id)
    if not normalized:
        return None
    if normalized in chapter_map:
        return normalized

    # 尝试缩写展开：physio_ch10 → physiology_ch10
    for variant in _expand_chapter_id_variants(normalized):
        if variant in chapter_map:
            return variant

    # 最后兜底：token 级别模糊匹配
    needle = _normalize_lookup_token(normalized)
    for candidate_id in chapter_map.keys():
        if _normalize_lookup_token(candidate_id) == needle:
            return candidate_id

    return None


def _load_wrong_answer_chapter_lookup(
    db: Session,
    records: List[QuestionRecord]
) -> Dict[Tuple[str, int], str]:
    fingerprint_candidates: Dict[Tuple[str, int], List[str]] = {}
    fingerprints = set()

    for record in records:
        key = (record.session_id, record.question_index)
        candidates = _build_question_record_fingerprint_candidates(record)
        if not candidates:
            continue
        fingerprint_candidates[key] = candidates
        fingerprints.update(candidates)

    if not fingerprints:
        return {}

    rows = db.query(
        WrongAnswerV2.question_fingerprint,
        WrongAnswerV2.chapter_id
    ).filter(
        WrongAnswerV2.question_fingerprint.in_(list(fingerprints))
    ).all()

    fingerprint_map: Dict[str, str] = {}
    for fingerprint, chapter_id in rows:
        normalized = _normalize_valid_chapter_id(chapter_id)
        if normalized:
            fingerprint_map[fingerprint] = normalized

    lookup: Dict[Tuple[str, int], str] = {}
    for key, candidates in fingerprint_candidates.items():
        for fingerprint in candidates:
            chapter_id = fingerprint_map.get(fingerprint)
            if chapter_id:
                lookup[key] = chapter_id
                break

    return lookup


def _resolve_period_bounds(
    period: str,
    date_str: Optional[str]
) -> Tuple[Optional[datetime], Optional[datetime]]:
    now = datetime.now()
    start_date = None
    end_date = None

    if period == "day":
        target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else now.date()
        start_date = datetime.combine(target, datetime.min.time())
        end_date = datetime.combine(target, datetime.max.time())
    elif period == "week":
        target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else now.date()
        start_of_week = target - timedelta(days=target.weekday())
        start_date = datetime.combine(start_of_week, datetime.min.time())
        end_date = datetime.combine(start_of_week + timedelta(days=6), datetime.max.time())
    elif period == "month":
        target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else now.date()
        start_date = datetime.combine(target.replace(day=1), datetime.min.time())
        next_month = target.replace(day=28) + timedelta(days=4)
        last_day = next_month - timedelta(days=next_month.day)
        end_date = datetime.combine(last_day, datetime.max.time())

    return start_date, end_date


def _normalize_ocr_line(line: str) -> str:
    text = (line or "").strip()
    if not text:
        return ""
    if text.startswith(OCR_IGNORED_PREFIXES):
        return ""

    # 去掉 OCR 行号前缀：如 "12. 内容"
    text = re.sub(r"^\s*\d+\.\s*", "", text)
    text = text.strip()
    return text


def _extract_plan_title(lines: List[str], month: int, day: int) -> str:
    for raw in lines:
        line = _normalize_ocr_line(raw)
        if not line:
            continue
        if "计划&答疑" in line:
            # 保留“计划&答疑”之后的核心主题作为标题
            idx = line.find("计划&答疑")
            tail = line[idx + len("计划&答疑"):].strip(" ：:，,。")
            if tail:
                return tail
            return line
    return f"{month:02d}.{day:02d} 日计划"


def _extract_focus_topics(content: str, title: str) -> List[str]:
    topics = []

    # 从标题中抽主题
    title_clean = title.strip()
    if title_clean and title_clean not in topics:
        topics.append(title_clean)

    # 提取引号内短语
    for m in re.findall(r"[\"“”']([^\"“”'\n]{2,40})[\"“”']", content):
        t = m.strip(" ：:，,。;；")
        if len(t) >= 2:
            topics.append(t)

    # 去重并过滤无意义词
    blacklist = {"直播", "讲义", "导图", "真题", "计划", "答疑", "复习", "预习", "做题"}
    dedup = []
    seen = set()
    for t in topics:
        if not t or t in blacklist:
            continue
        if t in seen:
            continue
        seen.add(t)
        dedup.append(t)

    return dedup[:8]


def _parse_month_day_from_filename(name: str) -> Optional[Dict[str, int]]:
    match = re.search(r"(\d{1,2})\.(\d{1,2})", name)
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    if month < 1 or month > 12 or day < 1 or day > 31:
        return None
    return {"month": month, "day": day}


def _analyze_plan_file(file_path: Path) -> Optional[Dict[str, Any]]:
    md = _parse_month_day_from_filename(file_path.stem)
    if not md:
        return None

    content = file_path.read_text(encoding="utf-8", errors="ignore")
    raw_lines = content.splitlines()
    cleaned_lines = [_normalize_ocr_line(line) for line in raw_lines]
    cleaned_lines = [line for line in cleaned_lines if line]
    joined = "\n".join(cleaned_lines)

    month = md["month"]
    day = md["day"]
    title = _extract_plan_title(raw_lines, month, day)

    categories = {}
    for key, keywords in OCR_CATEGORY_KEYWORDS.items():
        categories[key] = any(k in joined for k in keywords)

    if "今天没有直播" in joined or "今日没有直播" in joined or "没有直播" in joined:
        live_status = "no_live"
    elif categories["live"]:
        live_status = "live"
    else:
        live_status = "unknown"

    focus_topics = _extract_focus_topics(joined, title)
    preview_lines = []
    for line in cleaned_lines:
        if line.startswith("来自课程"):
            break
        preview_lines.append(line)
        if len(preview_lines) >= 4:
            break
    preview = " ".join(preview_lines)[:220]

    return {
        "entry_id": f"{month:02d}-{day:02d}:{file_path.name}",
        "month": month,
        "day": day,
        "date_key": f"{month:02d}-{day:02d}",
        "display_date": f"{month:02d}.{day:02d}",
        "filename": file_path.name,
        "title": title,
        "live_status": live_status,
        "categories": categories,
        "focus_topics": focus_topics,
        "preview": preview,
        "line_count": len(cleaned_lines),
        "updated_at": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
    }


def _month_progress_snapshot(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    month_map: Dict[int, Dict[str, Any]] = {}
    for item in entries:
        m = item["month"]
        if m not in month_map:
            month_map[m] = {
                "month": m,
                "plan_days": 0,
                "live_days": 0,
                "review_days": 0,
                "quiz_days": 0,
                "rolling_days": 0,
                "exam_days": 0,
                "preview_days": 0,
            }
        month_map[m]["plan_days"] += 1
        month_map[m]["live_days"] += 1 if item["live_status"] == "live" else 0
        month_map[m]["review_days"] += 1 if item["categories"].get("review") else 0
        month_map[m]["quiz_days"] += 1 if item["categories"].get("quiz") else 0
        month_map[m]["rolling_days"] += 1 if item["categories"].get("rolling") else 0
        month_map[m]["exam_days"] += 1 if item["categories"].get("exam") else 0
        month_map[m]["preview_days"] += 1 if item["categories"].get("preview") else 0

    rows = []
    for m in sorted(month_map.keys()):
        row = month_map[m]
        base = row["plan_days"] or 1
        row["live_ratio"] = round(row["live_days"] / base * 100, 1)
        row["quiz_ratio"] = round(row["quiz_days"] / base * 100, 1)
        row["review_ratio"] = round(row["review_days"] / base * 100, 1)
        rows.append(row)
    return rows


def _today_timeline_progress(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not entries:
        return {
            "today": datetime.now().strftime("%m-%d"),
            "past_days": 0,
            "total_days": 0,
            "progress_pct": 0.0,
            "next_plan_day": None
        }

    unique_md = sorted({(e["month"], e["day"]) for e in entries})
    today = datetime.now().date()
    today_md = (today.month, today.day)
    past_days = sum(1 for md in unique_md if md <= today_md)
    total_days = len(unique_md)
    next_day = next((md for md in unique_md if md > today_md), None)

    return {
        "today": today.strftime("%m-%d"),
        "past_days": past_days,
        "total_days": total_days,
        "progress_pct": round(past_days / total_days * 100, 1) if total_days > 0 else 0.0,
        "next_plan_day": f"{next_day[0]:02d}-{next_day[1]:02d}" if next_day else None
    }


def _to_date(plan_year: int, mmdd: str) -> date:
    month, day = [int(x) for x in mmdd.split("-")]
    return date(plan_year, month, day)


def _build_master_plan(plan_year: int) -> Dict[str, Any]:
    real_today = datetime.now().date()
    # 如果 plan_year 是过去的年份，用该年1月1日作为参考日期
    # 这样所有阶段都显示为"未开始"，避免误标为"已完成"
    if plan_year < real_today.year:
        today = date(plan_year, 1, 1)
    else:
        today = real_today
    phases = []
    milestones = []

    for item in MASTER_PLAN_PHASES:
        start_date = _to_date(plan_year, item["start"])
        end_date = _to_date(plan_year, item["end"])
        total_days = (end_date - start_date).days + 1

        if today < start_date:
            status = "pending"
            progress_pct = 0.0
        elif today > end_date:
            status = "completed"
            progress_pct = 100.0
        else:
            status = "active"
            elapsed = (today - start_date).days + 1
            progress_pct = round(elapsed / total_days * 100, 1) if total_days > 0 else 0.0

        phases.append({
            "id": item["id"],
            "stage": item["stage"],
            "title": item["title"],
            "details": item["details"],
            "type": "range",
            "start": item["start"],
            "end": item["end"],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_days": total_days,
            "status": status,
            "progress_pct": progress_pct,
        })

    for item in MASTER_PLAN_MILESTONES:
        milestone_date = _to_date(plan_year, item["date"])
        if today < milestone_date:
            status = "pending"
            days_delta = (milestone_date - today).days
        elif today > milestone_date:
            status = "completed"
            days_delta = (today - milestone_date).days
        else:
            status = "today"
            days_delta = 0

        milestones.append({
            "id": item["id"],
            "stage": item["stage"],
            "title": item["title"],
            "type": "milestone",
            "date": item["date"],
            "date_iso": milestone_date.isoformat(),
            "status": status,
            "days_delta": days_delta,
        })

    phases.sort(key=lambda x: x["start_date"])
    milestones.sort(key=lambda x: x["date_iso"])

    all_items = []
    for p in phases:
        all_items.append({
            "id": p["id"],
            "stage": p["stage"],
            "type": p["type"],
            "title": p["title"],
            "status": p["status"],
            "start_date": p["start_date"],
            "end_date": p["end_date"],
            "sort_key": p["start_date"],
        })
    for m in milestones:
        all_items.append({
            "id": m["id"],
            "stage": m["stage"],
            "type": m["type"],
            "title": m["title"],
            "status": m["status"],
            "date_iso": m["date_iso"],
            "sort_key": m["date_iso"],
        })
    all_items.sort(key=lambda x: x["sort_key"])

    completed_count = sum(1 for i in all_items if i["status"] == "completed")
    active_count = sum(1 for i in all_items if i["status"] in ("active", "today"))
    pending_count = sum(1 for i in all_items if i["status"] == "pending")
    total_count = len(all_items)

    next_item = next((i for i in all_items if i["status"] in ("pending", "active", "today")), None)

    return {
        "plan_year": plan_year,
        "today": today.isoformat(),
        "summary": {
            "total_items": total_count,
            "completed_items": completed_count,
            "active_items": active_count,
            "pending_items": pending_count,
            "completion_pct": round(completed_count / total_count * 100, 1) if total_count > 0 else 0.0,
        },
        "next_item": next_item,
        "phases": phases,
        "milestones": milestones,
    }


@router.post("/session/start")
async def start_learning_session(
    request: Request,
    body: StartSessionRequest,
    db: Session = Depends(get_db)
):
    """
    开始一个新的学习会话
    """
    session_id = str(uuid.uuid4())
    
    # 自动生成标题
    title = body.title
    if not title:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if body.session_type == "exam":
            title = f"{now} 整卷测试"
        else:
            title = f"{now} 细节练习"
    
    # 仅保存可映射的有效章节 ID，避免后续统计落入“未分类”
    valid_chapter_id = _normalize_valid_chapter_id(body.chapter_id)
    if valid_chapter_id:
        chapter_map = {chapter.id: chapter for chapter in db.query(Chapter).all()}
        valid_chapter_id = _resolve_chapter_id_from_map(valid_chapter_id, chapter_map)

    # 创建会话记录
    learning_session = LearningSession(
        id=session_id,
        session_type=body.session_type,
        chapter_id=valid_chapter_id,
        title=title,
        description=title,
        uploaded_content=body.uploaded_content[:10000] if body.uploaded_content else None,
        knowledge_point=body.knowledge_point,
        status=SessionStatus.IN_PROGRESS,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else None
    )
    
    db.add(learning_session)
    
    # 记录活动
    activity = LearningActivity(
        session_id=session_id,
        activity_type=ActivityType.EXAM_START if body.session_type == "exam" else ActivityType.DETAIL_PRACTICE_START,
        activity_name="开始学习" if body.session_type == "exam" else "开始细节练习",
        data={"chapter_id": body.chapter_id, "knowledge_point": body.knowledge_point},
        timestamp=datetime.now(),
        relative_time_ms=0
    )
    db.add(activity)
    
    db.commit()
    
    print(f"[Tracking] 开始学习会话: {session_id}, 类型: {body.session_type}")
    
    return {
        "session_id": session_id,
        "started_at": learning_session.started_at,
        "message": "学习会话已开始"
    }


@router.post("/session/{session_id}/activity")
async def record_activity(
    session_id: str,
    body: RecordActivityRequest,
    db: Session = Depends(get_db)
):
    """
    记录学习活动
    """
    session = db.query(LearningSession).filter(LearningSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    # 计算相对时间
    relative_ms = 0
    if session.started_at:
        relative_ms = int((datetime.now() - session.started_at).total_seconds() * 1000)
    
    activity = LearningActivity(
        session_id=session_id,
        activity_type=body.activity_type,
        activity_name=body.activity_name,
        data=body.data,
        timestamp=datetime.now(),
        relative_time_ms=relative_ms
    )
    db.add(activity)
    db.commit()
    
    return {"success": True, "activity_id": activity.id}


@router.post("/session/{session_id}/question")
async def record_question_answer(
    session_id: str,
    body: RecordQuestionRequest,
    db: Session = Depends(get_db)
):
    """
    记录单道题的答题情况
    """
    session = db.query(LearningSession).filter(LearningSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    # 服务端重新计算 is_correct（不信任前端的 naive === 比较）
    user_ans = (body.user_answer or "").strip().upper()
    correct_ans = (body.correct_answer or "").strip().upper()
    if body.question_type == "X":
        # 多选题：按字母排序后比较，兼容 "ACD" vs "DCA" 等不同顺序
        is_correct = sorted(user_ans) == sorted(correct_ans)
    else:
        is_correct = user_ans == correct_ans

    now = datetime.now()
    existing_records = db.query(QuestionRecord).filter(
        QuestionRecord.session_id == session_id,
        QuestionRecord.question_index == body.question_index
    ).order_by(
        desc(QuestionRecord.answered_at),
        desc(QuestionRecord.id)
    ).all()

    updated = bool(existing_records)
    question_record = existing_records[0] if existing_records else QuestionRecord(
        session_id=session_id,
        question_index=body.question_index,
        first_viewed_at=now,
    )
    if not existing_records:
        db.add(question_record)

    for stale_record in existing_records[1:]:
        db.delete(stale_record)

    question_record.question_type = body.question_type
    question_record.difficulty = body.difficulty
    question_record.question_text = body.question_text[:2000]
    question_record.options = _normalize_question_options(body.options)
    question_record.correct_answer = body.correct_answer
    question_record.user_answer = body.user_answer
    question_record.is_correct = is_correct
    question_record.confidence = body.confidence
    question_record.explanation = body.explanation[:2000] if body.explanation else None
    question_record.key_point = body.key_point
    question_record.answered_at = now
    question_record.time_spent_seconds = body.time_spent_seconds
    if not question_record.first_viewed_at:
        question_record.first_viewed_at = now

    db.flush()
    _sync_session_question_stats(session, db)
    db.commit()
    
    return {"success": True, "record_id": question_record.id, "updated": updated}


@router.post("/session/{session_id}/complete")
async def complete_learning_session(
    session_id: str,
    body: CompleteSessionRequest,
    db: Session = Depends(get_db)
):
    """
    完成学习会话
    """
    session = db.query(LearningSession).filter(LearningSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    _, stats = _sync_session_question_stats(session, db)

    # 更新会话信息；重复提交时保留首次完成时间，避免每日统计跨天漂移
    already_completed = session.status == SessionStatus.COMPLETED and session.completed_at is not None
    session.status = SessionStatus.COMPLETED
    session.completed_at = session.completed_at or datetime.now()
    session.score = body.score
    session.total_questions = body.total_questions
    actual_correct = min(stats["correct_count"], body.total_questions)
    session.accuracy = actual_correct / body.total_questions if body.total_questions > 0 else 0
    
    # 计算总用时
    if session.started_at and session.completed_at:
        session.duration_seconds = int((session.completed_at - session.started_at).total_seconds())
    
    # 记录完成活动
    if not already_completed:
        activity = LearningActivity(
            session_id=session_id,
            activity_type=ActivityType.EXAM_SUBMIT if session.session_type == "exam" else ActivityType.DETAIL_PRACTICE_SUBMIT,
            activity_name="完成测试" if session.session_type == "exam" else "完成细节练习",
            data={"score": body.score, "correct": stats["correct_count"], "wrong": stats["wrong_count"]},
            timestamp=datetime.now(),
            relative_time_ms=session.duration_seconds * 1000 if session.duration_seconds else 0
        )
        db.add(activity)
    
    db.commit()

    # 更新每日日志（不影响主流程）
    try:
        await update_daily_log(session, db)
    except Exception as e:
        print(f"[Tracking] 更新每日日志失败（不影响主流程）: {e}")
        import traceback
        traceback.print_exc()

    print(f"[Tracking] 完成学习会话: {session_id}, 得分: {body.score}")
    
    return {
        "success": True,
        "session_id": session_id,
        "score": body.score,
        "accuracy": round(session.accuracy * 100, 1),
        "duration": session.duration_seconds
    }


async def update_daily_log(session: LearningSession, db: Session):
    """更新每日学习日志"""
    try:
        log_date = (session.completed_at or datetime.now()).date()
        start_of_day = datetime.combine(log_date, datetime.min.time())
        end_of_day = datetime.combine(log_date, datetime.max.time())

        log = db.query(DailyLearningLog).filter(DailyLearningLog.date == log_date).first()
        if not log:
            log = DailyLearningLog(date=log_date)
            db.add(log)

        day_sessions = db.query(LearningSession).filter(
            LearningSession.completed_at >= start_of_day,
            LearningSession.completed_at <= end_of_day
        ).order_by(LearningSession.completed_at.asc()).all()

        unique_records = _load_unique_question_records(
            db,
            session_ids=[item.id for item in day_sessions]
        )
        record_stats = _build_record_stats(unique_records)

        scores = [item.score for item in day_sessions if item.score is not None]
        started_candidates = [item.started_at for item in day_sessions if item.started_at]
        completed_candidates = [item.completed_at for item in day_sessions if item.completed_at]
        knowledge_points = sorted({_display_key_point(record) for record in unique_records})
        weak_points = sorted({_display_key_point(record) for record in unique_records if not record.is_correct})

        log.total_sessions = len(day_sessions)
        log.total_questions = record_stats["total_questions"]
        log.total_correct = record_stats["correct_count"]
        log.total_wrong = record_stats["wrong_count"]
        log.total_duration_seconds = sum(item.duration_seconds or 0 for item in day_sessions)
        log.average_score = round(sum(scores) / len(scores), 1) if scores else 0
        log.first_session_at = min(started_candidates) if started_candidates else session.started_at
        log.last_session_at = max(completed_candidates) if completed_candidates else session.completed_at
        log.session_ids = [item.id for item in day_sessions]
        log.knowledge_points_covered = knowledge_points
        log.weak_knowledge_points = weak_points

        db.commit()
        print(f"[Tracking] 每日日志更新成功: {log_date}, 会话数: {log.total_sessions}")
    except Exception as e:
        db.rollback()
        print(f"[Tracking] 更新每日日志失败: {e}")
        import traceback
        traceback.print_exc()
        # 不影响主流程，不抛出异常


@router.get("/sessions")
async def get_learning_sessions(
    limit: int = 20,
    offset: int = 0,
    session_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    获取学习会话列表
    """
    query = db.query(LearningSession).order_by(desc(LearningSession.started_at))
    
    if session_type:
        query = query.filter(LearningSession.session_type == session_type)
    
    total = query.count()
    sessions = query.offset(offset).limit(limit).all()
    session_ids = [item.id for item in sessions]
    session_records = _group_records_by_session(
        _load_unique_question_records(db, session_ids=session_ids)
    )

    session_items = []
    for item in sessions:
        stats = _build_record_stats(session_records.get(item.id, []))
        total_questions = max(item.total_questions or 0, len(session_records.get(item.id, [])))
        session_items.append({
            "id": item.id,
            "session_type": item.session_type,
            "title": item.title,
            "score": item.score,
            "accuracy": round(stats["correct_count"] / total_questions * 100, 1) if total_questions > 0 else None,
            "correct_count": stats["correct_count"],
            "wrong_count": stats["wrong_count"],
            "total_questions": total_questions,
            "sure_count": stats["sure_count"],
            "unsure_count": stats["unsure_count"],
            "no_count": stats["no_count"],
            "duration_seconds": item.duration_seconds,
            "started_at": item.started_at.isoformat() if item.started_at else None,
            "status": item.status
        })
    
    return {
        "total": total,
        "sessions": session_items
    }


@router.get("/session/{session_id}")
async def get_session_detail(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    获取会话详情（包含所有活动和题目记录）
    """
    session = db.query(LearningSession).filter(LearningSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    # 获取活动记录
    activities = db.query(LearningActivity).filter(
        LearningActivity.session_id == session_id
    ).order_by(LearningActivity.timestamp).all()
    
    # 获取题目记录（同一题重复提交时仅保留最新一次）
    questions = _load_unique_question_records(db, session_ids=[session_id])
    stats = _build_record_stats(questions)
    total_questions = max(session.total_questions or 0, len(questions))
    
    return {
        "id": session.id,
        "session_type": session.session_type,
        "title": session.title,
        "description": session.description,
        "score": session.score,
        "accuracy": round(stats["correct_count"] / total_questions * 100, 1) if total_questions > 0 else None,
        "total_questions": total_questions,
        "answered_questions": stats["answered_questions"],
        "correct_count": stats["correct_count"],
        "wrong_count": stats["wrong_count"],
        "sure_count": stats["sure_count"],
        "unsure_count": stats["unsure_count"],
        "no_count": stats["no_count"],
        "duration_seconds": session.duration_seconds,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "status": session.status,
        "activities": [
            {
                "type": a.activity_type,
                "name": a.activity_name,
                "data": a.data,
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
                "relative_time_ms": a.relative_time_ms
            }
            for a in activities
        ],
        "questions": [
            {
                "index": q.question_index,
                "type": q.question_type,
                "difficulty": q.difficulty,
                "question": q.question_text,
                "options": q.options,
                "correct_answer": q.correct_answer,
                "user_answer": q.user_answer,
                "is_correct": q.is_correct,
                "confidence": q.confidence,
                "key_point": _display_key_point(q),
                "time_spent_seconds": q.time_spent_seconds,
                "explanation": q.explanation,
                "answer_changes": q.answer_changes or []
            }
            for q in questions
        ]
    }


@router.get("/review-data")
async def get_review_data(
    ids: str = "",
    db: Session = Depends(get_db)
):
    """
    获取多个会话的完整复盘数据（用于沉浸式复盘页面）
    ids: 逗号分隔的session_id列表
    """
    session_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not session_ids:
        return {"sessions": []}

    records_by_session = _group_records_by_session(
        _load_unique_question_records(db, session_ids=session_ids)
    )

    results = []
    for sid in session_ids:
        session = db.query(LearningSession).filter(LearningSession.id == sid).first()
        if not session:
            continue

        questions = records_by_session.get(sid, [])
        stats = _build_record_stats(questions)
        total_questions = max(session.total_questions or 0, len(questions))

        results.append({
            "id": session.id,
            "session_type": session.session_type,
            "title": session.title,
            "score": session.score,
            "accuracy": round(stats["correct_count"] / total_questions * 100, 1) if total_questions > 0 else None,
            "correct_count": stats["correct_count"],
            "wrong_count": stats["wrong_count"],
            "total_questions": total_questions,
            "sure_count": stats["sure_count"],
            "unsure_count": stats["unsure_count"],
            "no_count": stats["no_count"],
            "duration_seconds": session.duration_seconds,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            "knowledge_point": session.knowledge_point,
            "questions": [
                {
                    "index": q.question_index,
                    "type": q.question_type,
                    "difficulty": q.difficulty,
                    "question": q.question_text,
                    "options": q.options,
                    "correct_answer": q.correct_answer,
                    "user_answer": q.user_answer,
                    "is_correct": q.is_correct,
                    "confidence": q.confidence,
                    "key_point": _display_key_point(q),
                    "explanation": q.explanation,
                    "time_spent_seconds": q.time_spent_seconds,
                    "answer_changes": q.answer_changes or []
                }
                for q in questions
            ]
        })

    return {"sessions": results}


@router.get("/knowledge-archive")
async def get_knowledge_archive(
    db: Session = Depends(get_db)
):
    """
    获取全局知识点归档数据（树状结构）
    按 学科 → 系统/章节 → 知识点 组织，包含所有题目
    """
    # 获取所有题目记录（按 session_id + question_index 去重）
    all_questions = sorted(
        _load_unique_question_records(db),
        key=lambda record: _question_record_sort_key(record),
        reverse=True
    )

    # 获取session信息用于补充上下文
    session_map = {}
    session_ids = set(q.session_id for q in all_questions)
    if session_ids:
        sessions = db.query(LearningSession).filter(LearningSession.id.in_(session_ids)).all()
        for s in sessions:
            session_map[s.id] = {
                "title": s.title,
                "session_type": s.session_type,
                "started_at": s.started_at.isoformat() if s.started_at else None
            }

    # 按知识点聚合
    kp_map = {}
    for q in all_questions:
        kp = _display_key_point(q)
        if kp not in kp_map:
            kp_map[kp] = {"total": 0, "correct": 0, "wrong": 0, "questions": []}
        kp_map[kp]["total"] += 1
        if q.is_correct:
            kp_map[kp]["correct"] += 1
        else:
            kp_map[kp]["wrong"] += 1

        sess_info = session_map.get(q.session_id, {})
        kp_map[kp]["questions"].append({
            "question": q.question_text,
            "options": q.options,
            "correct_answer": q.correct_answer,
            "user_answer": q.user_answer,
            "is_correct": q.is_correct,
            "confidence": q.confidence,
            "difficulty": q.difficulty,
            "question_type": q.question_type,
            "explanation": q.explanation,
            "key_point": kp,
            "time_spent_seconds": q.time_spent_seconds,
            "answered_at": q.answered_at.isoformat() if q.answered_at else None,
            "session_type": sess_info.get("session_type", ""),
            "session_title": sess_info.get("title", ""),
        })

    # 计算错误率并排序
    kp_list = []
    for kp, data in kp_map.items():
        error_rate = round(data["wrong"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        accuracy = round(data["correct"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        kp_list.append({
            "name": kp,
            "total": data["total"],
            "correct": data["correct"],
            "wrong": data["wrong"],
            "error_rate": error_rate,
            "accuracy": accuracy,
            "questions": data["questions"]
        })

    # 按错误率降序排序
    kp_list.sort(key=lambda x: x["error_rate"], reverse=True)

    return {
        "total_knowledge_points": len(kp_list),
        "total_questions": len(all_questions),
        "knowledge_points": kp_list
    }


@router.get("/daily-logs")
async def get_daily_logs(
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    获取每日学习日志
    """
    start_date = date.today() - timedelta(days=days)
    
    logs = db.query(DailyLearningLog).filter(
        DailyLearningLog.date >= start_date
    ).order_by(desc(DailyLearningLog.date)).all()
    
    return {
        "logs": [
            {
                "date": log.date.isoformat(),
                "total_sessions": log.total_sessions,
                "total_questions": log.total_questions,
                "accuracy": round(log.total_correct / log.total_questions * 100, 1) if log.total_questions > 0 else 0,
                "average_score": log.average_score,
                "duration_minutes": log.total_duration_seconds // 60,
                "knowledge_points": len(log.knowledge_points_covered or []),
                "weak_points": log.weak_knowledge_points
            }
            for log in logs
        ]
    }


@router.get("/export/{session_id}")
async def export_session_report(
    session_id: str,
    format: str = "text",  # 'text', 'json', 'markdown'
    db: Session = Depends(get_db)
):
    """
    导出学习报告
    """
    session_detail = await get_session_detail(session_id, db)
    
    if format == "json":
        return session_detail
    
    elif format == "markdown":
        md_lines = [
            f"# {session_detail['title']}",
            "",
            "## 基本信息",
            f"- **类型**: {session_detail['session_type']}",
            f"- **得分**: {session_detail['score']} 分",
            f"- **正确率**: {session_detail['accuracy']}%",
            f"- **用时**: {session_detail['duration_seconds'] // 60} 分 {session_detail['duration_seconds'] % 60} 秒",
            f"- **时间**: {session_detail['started_at']}",
            "",
            "## 统计",
            f"- **总题数**: {session_detail['total_questions']}",
            f"- **正确**: {session_detail['correct_count']}",
            f"- **错误**: {session_detail['wrong_count']}",
            f"- **确定**: {session_detail['sure_count']} | **模糊**: {session_detail['unsure_count']} | **不会**: {session_detail['no_count']}",
            "",
            "## 题目详情",
        ]
        
        for q in session_detail['questions']:
            status = "✅" if q['is_correct'] else "❌"
            conf_map = {'sure': '✓', 'unsure': '?', 'no': '✗', None: '○'}
            conf = conf_map.get(q['confidence'], '○')
            md_lines.append(f"### 第 {q['index'] + 1} 题 {status} {conf}")
            md_lines.append(f"**{q['question']}**")
            md_lines.append("")
            md_lines.append(f"你的答案: {q['user_answer']} | 正确答案: {q['correct_answer']}")
            md_lines.append("")
            if q['key_point']:
                md_lines.append(f"知识点: {q['key_point']}")
            md_lines.append("---")
        
        return {"content": "\n".join(md_lines), "format": "markdown"}
    
    else:  # text
        text_lines = [
            "╔════════════════════════════════════════════════════════════╗",
            "║              📋 医学考研学习报告                          ║",
            "╚════════════════════════════════════════════════════════════╝",
            "",
            "【基本信息】",
            f"类型: {session_detail['session_type']}",
            f"得分: {session_detail['score']} 分",
            f"正确率: {session_detail['accuracy']}%",
            f"用时: {session_detail['duration_seconds'] // 60} 分 {session_detail['duration_seconds'] % 60} 秒",
            f"时间: {session_detail['started_at']}",
            "",
            "【统计】",
            f"总题数: {session_detail['total_questions']} | 正确: {session_detail['correct_count']} | 错误: {session_detail['wrong_count']}",
            f"自信度: 确定 {session_detail['sure_count']} | 模糊 {session_detail['unsure_count']} | 不会 {session_detail['no_count']}",
            "",
            "【题目详情】",
            "════════════════════════════════════════════════════════════════",
        ]
        
        for q in session_detail['questions']:
            status = "✓ 正确" if q['is_correct'] else "✗ 错误"
            conf_map = {'sure': '✓ 确定', 'unsure': '? 模糊', 'no': '✗ 不会', None: '○ 未标记'}
            conf = conf_map.get(q['confidence'], '○ 未标记')
            
            text_lines.append(f"")
            text_lines.append(f"第 {q['index'] + 1} 题 [{q['type']}] [{q['difficulty']}]")
            text_lines.append(f"{q['question']}")
            text_lines.append("")
            
            opts = q.get('options') or {}
            for opt, val in opts.items():
                marker = ""
                if opt == q['correct_answer']:
                    marker = " ✓"
                elif opt == q['user_answer'] and not q['is_correct']:
                    marker = " ✗"
                text_lines.append(f"{opt}. {val}{marker}")
            
            text_lines.append("")
            text_lines.append(f"你的答案: {q['user_answer']} | 正确答案: {q['correct_answer']}")
            text_lines.append(f"自信度: {conf}")
            text_lines.append(f"结果: {status}")
            text_lines.append(f"知识点: {q.get('key_point') or '无'}")
            text_lines.append("────────────────────────────────────────────────────────────────")
        
        return {"content": "\n".join(text_lines), "format": "text"}


@router.get("/stats")
async def get_stats(
    period: str = "all",  # 'day', 'week', 'month', 'all'
    date_str: Optional[str] = None,  # 具体日期 YYYY-MM-DD
    db: Session = Depends(get_db)
):
    """
    获取指定时间范围的统计数据（统一数据源）
    """
    start_date, end_date = _resolve_period_bounds(period, date_str)

    session_query = db.query(LearningSession)
    if start_date and end_date:
        session_query = session_query.filter(
            LearningSession.started_at >= start_date,
            LearningSession.started_at <= end_date
        )
    sessions = session_query.order_by(desc(LearningSession.started_at)).all()
    session_ids = [session.id for session in sessions]
    session_lookup = {session.id: session for session in sessions}

    question_records = _load_unique_question_records(db, session_ids=session_ids)
    session_records = _group_records_by_session(question_records)
    overall_stats = _build_record_stats(question_records)
    total_sessions = len(sessions)
    total_duration = sum(session.duration_seconds or 0 for session in sessions)

    type_dist: Dict[str, int] = {}
    diff_dist: Dict[str, int] = {}
    knowledge_points: Dict[str, Dict[str, Any]] = {}
    type_correct: Dict[str, int] = {}
    diff_correct: Dict[str, int] = {}
    kp_confidence: Dict[str, List[float]] = {}

    for qr in question_records:
        question_type = qr.question_type or "A1"
        difficulty = qr.difficulty or "基础"
        key_point = _display_key_point(qr)

        type_dist[question_type] = type_dist.get(question_type, 0) + 1
        diff_dist[difficulty] = diff_dist.get(difficulty, 0) + 1
        type_correct[question_type] = type_correct.get(question_type, 0) + (1 if qr.is_correct else 0)
        diff_correct[difficulty] = diff_correct.get(difficulty, 0) + (1 if qr.is_correct else 0)

        if key_point not in knowledge_points:
            knowledge_points[key_point] = {"total": 0, "correct": 0, "wrong": 0}
        knowledge_points[key_point]["total"] += 1
        if qr.is_correct:
            knowledge_points[key_point]["correct"] += 1
        else:
            knowledge_points[key_point]["wrong"] += 1

        conf_score = 1.0 if qr.confidence == "sure" else (0.5 if qr.confidence == "unsure" else (0.0 if qr.confidence == "no" else None))
        if conf_score is not None:
            kp_confidence.setdefault(key_point, []).append(conf_score)

    total_qr = len(question_records)

    daily_map: Dict[str, Dict[str, int]] = {}
    for session in sessions:
        if not session.started_at:
            continue
        day_key = session.started_at.strftime("%Y-%m-%d")
        daily_map.setdefault(day_key, {"questions": 0, "correct": 0, "sessions": 0, "duration": 0})
        daily_map[day_key]["sessions"] += 1
        daily_map[day_key]["duration"] += session.duration_seconds or 0

    session_questions_map: Dict[str, List[Dict[str, Any]]] = {}
    for session_id, records in session_records.items():
        session = session_lookup.get(session_id)
        if session and session.started_at:
            day_key = session.started_at.strftime("%Y-%m-%d")
            daily_map.setdefault(day_key, {"questions": 0, "correct": 0, "sessions": 0, "duration": 0})
            daily_map[day_key]["questions"] += len(records)
            daily_map[day_key]["correct"] += sum(1 for record in records if record.is_correct)

        session_questions_map[session_id] = [
            {
                "key_point": _display_key_point(record),
                "is_correct": record.is_correct,
                "confidence": record.confidence,
                "time_spent_seconds": record.time_spent_seconds or 0,
                "answer_changes": record.answer_changes or [],
                "question_type": record.question_type or "A1",
                "difficulty": record.difficulty or "基础",
            }
            for record in records
        ]

    session_list = []
    for session in sessions:
        records = session_records.get(session.id, [])
        session_stats = _build_record_stats(records)
        total_questions = max(session.total_questions or 0, len(records))
        session_list.append({
            "id": session.id,
            "session_type": session.session_type,
            "title": session.title,
            "score": session.score,
            "accuracy": round(session_stats["correct_count"] / total_questions * 100, 1) if total_questions > 0 else None,
            "correct_count": session_stats["correct_count"],
            "wrong_count": session_stats["wrong_count"],
            "total_questions": total_questions,
            "sure_count": session_stats["sure_count"],
            "unsure_count": session_stats["unsure_count"],
            "no_count": session_stats["no_count"],
            "duration_seconds": session.duration_seconds,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            "status": session.status,
            "knowledge_point": session.knowledge_point,
            "chapter_id": session.chapter_id,
            "question_details": session_questions_map.get(session.id, []),
        })

    for key_point, data in knowledge_points.items():
        confidences = kp_confidence.get(key_point, [])
        data["avg_confidence"] = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        data["has_confidence_data"] = len(confidences) > 0

    now_date = datetime.now().date()
    this_week_start = now_date - timedelta(days=now_date.weekday())
    last_week_start = this_week_start - timedelta(days=7)

    wow_this_sessions = db.query(LearningSession).filter(
        LearningSession.started_at >= datetime.combine(this_week_start, datetime.min.time())
    ).all()
    wow_last_sessions = db.query(LearningSession).filter(
        LearningSession.started_at >= datetime.combine(last_week_start, datetime.min.time()),
        LearningSession.started_at < datetime.combine(this_week_start, datetime.min.time())
    ).all()
    wow_this_records = _load_unique_question_records(db, session_ids=[session.id for session in wow_this_sessions])
    wow_last_records = _load_unique_question_records(db, session_ids=[session.id for session in wow_last_sessions])

    this_acc = round(sum(1 for q in wow_this_records if q.is_correct) / len(wow_this_records) * 100, 1) if wow_this_records else None
    last_acc = round(sum(1 for q in wow_last_records if q.is_correct) / len(wow_last_records) * 100, 1) if wow_last_records else None

    if this_acc is not None and last_acc is not None:
        delta = round(this_acc - last_acc, 1)
        wow_delta = {"current_accuracy": this_acc, "previous_accuracy": last_acc, "delta": delta, "direction": "up" if delta > 0 else ("down" if delta < 0 else "flat")}
    elif this_acc is not None:
        wow_delta = {"current_accuracy": this_acc, "previous_accuracy": None, "delta": None, "direction": "flat"}
    else:
        wow_delta = {"current_accuracy": None, "previous_accuracy": None, "delta": None, "direction": "flat"}

    weakest_area = None
    min_acc = 101.0
    for key_point, data in knowledge_points.items():
        if data["total"] >= 3:
            accuracy = round(data["correct"] / data["total"] * 100, 1) if data["total"] > 0 else 0
            if accuracy < min_acc:
                min_acc = accuracy
                weakest_area = {
                    "name": key_point,
                    "accuracy": accuracy,
                    "total": data["total"],
                    "correct": data["correct"]
                }

    return {
        "period": period,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "summary": {
            "total_sessions": total_sessions,
            "total_questions": overall_stats["total_questions"],
            "total_correct": overall_stats["correct_count"],
            "avg_accuracy": round(overall_stats["correct_count"] / overall_stats["total_questions"] * 100, 1) if overall_stats["total_questions"] > 0 else 0,
            "total_duration": total_duration,
            "sure_count": overall_stats["sure_count"],
            "unsure_count": overall_stats["unsure_count"],
            "no_count": overall_stats["no_count"],
        },
        "type_distribution": {
            key: {
                "count": value,
                "pct": round(value / total_qr * 100, 1) if total_qr > 0 else 0,
                "correct": type_correct.get(key, 0),
                "accuracy": round(type_correct.get(key, 0) / value * 100, 1) if value > 0 else 0
            } for key, value in type_dist.items()
        },
        "difficulty_distribution": {
            key: {
                "count": value,
                "pct": round(value / total_qr * 100, 1) if total_qr > 0 else 0,
                "correct": diff_correct.get(key, 0),
                "accuracy": round(diff_correct.get(key, 0) / value * 100, 1) if value > 0 else 0
            } for key, value in diff_dist.items()
        },
        "knowledge_points": knowledge_points,
        "daily_trend": daily_map,
        "sessions": session_list,
        "wow_delta": wow_delta,
        "weakest_area": weakest_area,
    }


@router.get("/ocr-plan-board")
async def get_ocr_plan_board(
    plan_dir: Optional[str] = Query(default=None, description="OCR计划目录路径（可选）"),
    plan_year: Optional[int] = Query(default=None, description="整体规划年份（默认当前年）")
):
    """
    OCR 年度计划看板接口
    数据源：OCR 文本目录（默认桌面路径）
    """
    root = Path(
        plan_dir
        or os.getenv("OCR_PLAN_DIR")
        or str(DEFAULT_OCR_PLAN_DIR)
    ).expanduser()

    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail=f"OCR计划目录不存在: {root}")

    txt_files = sorted(
        root.glob("*.txt"),
        key=lambda p: (p.name,)
    )

    timeline_entries: List[Dict[str, Any]] = []
    special_docs: List[Dict[str, Any]] = []

    for fp in txt_files:
        if "日计划" in fp.stem:
            analyzed = _analyze_plan_file(fp)
            if analyzed:
                timeline_entries.append(analyzed)
                continue

        content = fp.read_text(encoding="utf-8", errors="ignore")
        lines = [_normalize_ocr_line(line) for line in content.splitlines()]
        lines = [line for line in lines if line]
        preview = " ".join(lines[:2])[:140]
        md = _parse_month_day_from_filename(fp.stem)
        special_docs.append({
            "name": fp.name,
            "month": md["month"] if md else None,
            "day": md["day"] if md else None,
            "date_key": f"{md['month']:02d}-{md['day']:02d}" if md else None,
            "preview": preview,
            "updated_at": datetime.fromtimestamp(fp.stat().st_mtime).isoformat(),
        })

    timeline_entries.sort(key=lambda x: (x["month"], x["day"], x["filename"]))
    special_docs.sort(key=lambda x: ((x["month"] or 99), (x["day"] or 99), x["name"]))

    month_summary = _month_progress_snapshot(timeline_entries)

    category_totals = {k: 0 for k in OCR_CATEGORY_KEYWORDS.keys()}
    for item in timeline_entries:
        for k in category_totals:
            category_totals[k] += 1 if item["categories"].get(k) else 0

    total_days = len(timeline_entries)
    months = sorted({item["month"] for item in timeline_entries})
    live_days = sum(1 for item in timeline_entries if item["live_status"] == "live")
    no_live_days = sum(1 for item in timeline_entries if item["live_status"] == "no_live")
    quiz_days = category_totals.get("quiz", 0)
    review_days = category_totals.get("review", 0)
    preview_days = category_totals.get("preview", 0)
    rolling_days = category_totals.get("rolling", 0)
    exam_days = category_totals.get("exam", 0)

    all_topics = set()
    for item in timeline_entries:
        for t in item.get("focus_topics", []):
            all_topics.add(t)

    progress_snapshot = _today_timeline_progress(timeline_entries)
    busiest_month = max(month_summary, key=lambda x: x["plan_days"])["month"] if month_summary else None
    target_year = plan_year or datetime.now().year
    master_plan = _build_master_plan(target_year)

    return {
        "source_dir": str(root),
        "generated_at": datetime.now().isoformat(),
        "plan_year": target_year,
        "overview": {
            "total_plan_days": total_days,
            "covered_months": len(months),
            "month_list": months,
            "first_plan_day": timeline_entries[0]["date_key"] if timeline_entries else None,
            "last_plan_day": timeline_entries[-1]["date_key"] if timeline_entries else None,
            "live_days": live_days,
            "no_live_days": no_live_days,
            "quiz_days": quiz_days,
            "review_days": review_days,
            "preview_days": preview_days,
            "rolling_days": rolling_days,
            "exam_days": exam_days,
            "topic_count": len(all_topics),
            "busiest_month": busiest_month,
        },
        "timeline_progress": progress_snapshot,
        "category_totals": category_totals,
        "month_summary": month_summary,
        "master_plan": master_plan,
        "timeline": timeline_entries,
        "special_docs": special_docs,
    }


@router.get("/progress-board")
async def get_progress_board(
    period: str = "all",
    date_str: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    进度看板聚合接口：
    - 概览KPI
    - 7/30天趋势
    - 自信度分布
    - 会话类型分布
    - 薄弱知识点排行
    """
    stats = await get_stats(period=period, date_str=date_str, db=db)

    summary = stats.get("summary", {})
    sessions = stats.get("sessions", [])
    knowledge_points = stats.get("knowledge_points", {})
    daily_trend = stats.get("daily_trend", {})

    total_sessions = int(summary.get("total_sessions") or 0)
    total_questions = int(summary.get("total_questions") or 0)
    total_correct = int(summary.get("total_correct") or 0)
    total_wrong = max(total_questions - total_correct, 0)
    total_duration_seconds = int(summary.get("total_duration") or 0)
    avg_accuracy = float(summary.get("avg_accuracy") or 0)

    sure_count = int(summary.get("sure_count") or 0)
    unsure_count = int(summary.get("unsure_count") or 0)
    no_count = int(summary.get("no_count") or 0)
    confidence_total = sure_count + unsure_count + no_count

    confidence_distribution = [
        {
            "key": "sure",
            "label": "确定",
            "count": sure_count,
            "pct": round(sure_count / confidence_total * 100, 1) if confidence_total > 0 else 0.0
        },
        {
            "key": "unsure",
            "label": "模糊",
            "count": unsure_count,
            "pct": round(unsure_count / confidence_total * 100, 1) if confidence_total > 0 else 0.0
        },
        {
            "key": "no",
            "label": "不会",
            "count": no_count,
            "pct": round(no_count / confidence_total * 100, 1) if confidence_total > 0 else 0.0
        },
    ]

    session_type_map = {}
    for session in sessions:
        stype = session.get("session_type")
        if stype not in ("exam", "detail_practice"):
            stype = "other"
        session_type_map[stype] = session_type_map.get(stype, 0) + 1

    session_type_distribution = []
    for key, label in [("exam", "整卷测验"), ("detail_practice", "知识点测验"), ("other", "其他")]:
        count = session_type_map.get(key, 0)
        session_type_distribution.append({
            "key": key,
            "label": label,
            "count": count,
            "pct": round(count / total_sessions * 100, 1) if total_sessions > 0 else 0.0
        })

    anchor_date = datetime.now().date()
    if stats.get("end_date"):
        try:
            anchor_date = datetime.fromisoformat(stats["end_date"]).date()
        except Exception:
            pass

    def build_daily_series(days: int):
        rows = []
        for i in range(days - 1, -1, -1):
            d = anchor_date - timedelta(days=i)
            d_key = d.strftime("%Y-%m-%d")
            day_data = daily_trend.get(d_key, {})
            questions = int(day_data.get("questions") or 0)
            correct = int(day_data.get("correct") or 0)
            session_count = int(day_data.get("sessions") or 0)
            duration = int(day_data.get("duration") or 0)
            rows.append({
                "date": d_key,
                "questions": questions,
                "correct": correct,
                "sessions": session_count,
                "duration_seconds": duration,
                "accuracy": round(correct / questions * 100, 1) if questions > 0 else 0.0
            })
        return rows

    weak_points = []
    for kp_name, kp_data in knowledge_points.items():
        total = int(kp_data.get("total") or 0)
        correct = int(kp_data.get("correct") or 0)
        wrong = int(kp_data.get("wrong") or 0)
        if total <= 0 or wrong <= 0:
            continue

        avg_conf = float(kp_data.get("avg_confidence") or 0.0)
        weak_points.append({
            "name": kp_name,
            "total": total,
            "correct": correct,
            "wrong": wrong,
            "accuracy": round(correct / total * 100, 1) if total > 0 else 0.0,
            "avg_confidence": avg_conf,
            "confidence_level": "高自信" if avg_conf >= 0.8 else ("中自信" if avg_conf >= 0.5 else "低自信")
        })

    weak_points.sort(key=lambda x: (-x["wrong"], x["accuracy"], -x["total"]))
    weak_points = weak_points[:10]

    recent_sessions = []
    for session in sessions[:8]:
        recent_sessions.append({
            "id": session.get("id"),
            "title": session.get("title"),
            "session_type": session.get("session_type"),
            "accuracy": session.get("accuracy"),
            "correct_count": session.get("correct_count"),
            "wrong_count": session.get("wrong_count"),
            "total_questions": session.get("total_questions"),
            "duration_seconds": session.get("duration_seconds") or 0,
            "started_at": session.get("started_at"),
            "status": session.get("status")
        })

    return {
        "period": period,
        "start_date": stats.get("start_date"),
        "end_date": stats.get("end_date"),
        "generated_at": datetime.now().isoformat(),
        "overview": {
            "total_sessions": total_sessions,
            "total_questions": total_questions,
            "total_correct": total_correct,
            "total_wrong": total_wrong,
            "avg_accuracy": avg_accuracy,
            "total_duration_seconds": total_duration_seconds,
            "total_duration_hours": round(total_duration_seconds / 3600, 2),
        },
        "confidence_distribution": confidence_distribution,
        "session_type_distribution": session_type_distribution,
        "daily_trend_7": build_daily_series(7),
        "daily_trend_30": build_daily_series(30),
        "weak_points": weak_points,
        "recent_sessions": recent_sessions,
        "wow_delta": stats.get("wow_delta"),
        "weakest_area": stats.get("weakest_area"),
    }


@router.get("/knowledge-tree")
async def get_knowledge_tree(
    period: str = "all",
    date_str: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Build hierarchical knowledge tree: book → chapter → key_point
    """
    start_date, end_date = _resolve_period_bounds(period, date_str)
    chapters = db.query(Chapter).all()
    chapter_map = {ch.id: ch for ch in chapters}
    session_query = db.query(LearningSession)
    if start_date and end_date:
        session_query = session_query.filter(
            LearningSession.started_at >= start_date,
            LearningSession.started_at <= end_date
        )
    sessions = session_query.all()
    session_lookup = {session.id: session for session in sessions}
    records = _load_unique_question_records(db, session_ids=[session.id for session in sessions])
    wrong_answer_chapter_lookup = _load_wrong_answer_chapter_lookup(db, records)

    # 第一轮：为每条记录解析 chapter_id（session → WrongAnswerV2 回退）
    record_chapter: Dict[Tuple[str, int], Optional[str]] = {}
    for qr in records:
        session = session_lookup.get(qr.session_id)
        chapter_id = None
        if session:
            chapter_id = _resolve_chapter_id_from_map(session.chapter_id, chapter_map)
        if not chapter_id:
            fallback_chapter_id = wrong_answer_chapter_lookup.get((qr.session_id, qr.question_index))
            chapter_id = _resolve_chapter_id_from_map(fallback_chapter_id, chapter_map)
        record_chapter[(qr.session_id, qr.question_index)] = chapter_id

    # 第二轮：同 session 多数投票回退（答对的题没进错题本，用同 session 其他题的章节）
    session_chapter_votes: Dict[str, Dict[str, int]] = {}
    for (sid, _qi), ch_id in record_chapter.items():
        if ch_id:
            session_chapter_votes.setdefault(sid, {})
            session_chapter_votes[sid][ch_id] = session_chapter_votes[sid].get(ch_id, 0) + 1

    for key, ch_id in record_chapter.items():
        if ch_id:
            continue
        sid = key[0]
        votes = session_chapter_votes.get(sid)
        if votes:
            record_chapter[key] = max(votes, key=votes.get)

    # Build tree: {book: {chapter_title: {key_point: stats}}}
    tree: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for qr in records:
        chapter_id = record_chapter.get((qr.session_id, qr.question_index))
        ch = chapter_map.get(chapter_id) if chapter_id else None
        book = ch.book if ch else "未分类"
        ch_title = f"{ch.chapter_number} {ch.chapter_title}" if ch else "未关联章节"
        kp = _display_key_point(qr)

        tree.setdefault(book, {}).setdefault(ch_title, {}).setdefault(kp, {
            "total": 0, "correct": 0, "wrong": 0, "error_types": {}
        })
        node = tree[book][ch_title][kp]
        node["total"] += 1
        if qr.is_correct:
            node["correct"] += 1
        else:
            node["wrong"] += 1
            qt = qr.question_type or "A1"
            node["error_types"][qt] = node["error_types"].get(qt, 0) + 1

    # Convert to sorted list
    result = []
    for book_name, chapters_data in tree.items():
        book_node = {"name": book_name, "chapters": [], "total": 0, "correct": 0}
        for ch_title, kps in chapters_data.items():
            ch_node = {"name": ch_title, "key_points": [], "total": 0, "correct": 0}
            for kp_name, stats in kps.items():
                accuracy = round(stats["correct"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
                dominant_error = max(stats["error_types"], key=stats["error_types"].get) if stats["error_types"] else None
                ch_node["key_points"].append({
                    "name": kp_name,
                    "total": stats["total"],
                    "correct": stats["correct"],
                    "wrong": stats["wrong"],
                    "accuracy": accuracy,
                    "dominant_error_type": dominant_error
                })
                ch_node["total"] += stats["total"]
                ch_node["correct"] += stats["correct"]
            ch_node["key_points"].sort(key=lambda x: x["accuracy"])
            ch_node["accuracy"] = round(ch_node["correct"] / ch_node["total"] * 100, 1) if ch_node["total"] > 0 else 0
            book_node["chapters"].append(ch_node)
            book_node["total"] += ch_node["total"]
            book_node["correct"] += ch_node["correct"]
        book_node["chapters"].sort(key=lambda x: x.get("accuracy", 0))
        book_node["accuracy"] = round(book_node["correct"] / book_node["total"] * 100, 1) if book_node["total"] > 0 else 0
        result.append(book_node)

    result.sort(key=lambda x: x["accuracy"])
    return {"tree": result}


@router.get("/export-markdown")
async def export_markdown_report(
    period: str = "all",
    date_str: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    导出指定时间范围的Markdown报告
    """
    stats = await get_stats(period=period, date_str=date_str, db=db)
    s = stats["summary"]

    period_labels = {"day": "今日", "week": "本周", "month": "本月", "all": "全部"}
    period_label = period_labels.get(period, period)
    date_range = ""
    if stats["start_date"] and stats["end_date"]:
        date_range = f"（{stats['start_date'][:10]} ~ {stats['end_date'][:10]}）"

    lines = [
        f"# 学习轨迹报告 - {period_label}{date_range}",
        f"",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"## 核心指标",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 学习次数 | {s['total_sessions']} |",
        f"| 总做题数 | {s['total_questions']} |",
        f"| 正确数 | {s['total_correct']} |",
        f"| 平均正确率 | {s['avg_accuracy']}% |",
        f"| 总学习时长 | {s['total_duration'] // 60}分钟 |",
        f"| 确定 | {s['sure_count']} |",
        f"| 模糊 | {s['unsure_count']} |",
        f"| 不会 | {s['no_count']} |",
        f"",
    ]

    # 题型分布
    if stats["type_distribution"]:
        lines += [
            f"## 题型分布",
            f"",
            f"| 题型 | 数量 | 占比 |",
            f"|------|------|------|",
        ]
        for t, v in stats["type_distribution"].items():
            lines.append(f"| {t} | {v['count']} | {v['pct']}% |")
        lines.append("")

    # 难度分布
    if stats["difficulty_distribution"]:
        lines += [
            f"## 难度分布",
            f"",
            f"| 难度 | 数量 | 占比 |",
            f"|------|------|------|",
        ]
        for d, v in stats["difficulty_distribution"].items():
            lines.append(f"| {d} | {v['count']} | {v['pct']}% |")
        lines.append("")

    # 知识点掌握
    if stats["knowledge_points"]:
        lines += [
            f"## 知识点掌握情况",
            f"",
            f"| 知识点 | 总题数 | 正确 | 错误 | 正确率 |",
            f"|--------|--------|------|------|--------|",
        ]
        for kp, v in sorted(stats["knowledge_points"].items(), key=lambda x: x[1]["wrong"], reverse=True):
            acc = round(v["correct"] / v["total"] * 100, 1) if v["total"] > 0 else 0
            lines.append(f"| {kp} | {v['total']} | {v['correct']} | {v['wrong']} | {acc}% |")
        lines.append("")

    # 每日趋势
    if stats["daily_trend"]:
        lines += [
            f"## 每日学习趋势",
            f"",
            f"| 日期 | 会话数 | 做题数 | 正确数 | 正确率 | 时长(分) |",
            f"|------|--------|--------|--------|--------|----------|",
        ]
        for dk in sorted(stats["daily_trend"].keys()):
            v = stats["daily_trend"][dk]
            acc = round(v["correct"] / v["questions"] * 100, 1) if v["questions"] > 0 else 0
            lines.append(f"| {dk} | {v['sessions']} | {v['questions']} | {v['correct']} | {acc}% | {v['duration'] // 60} |")
        lines.append("")

    # 学习记录列表
    lines += [f"## 学习记录", f""]
    for sess in stats["sessions"]:
        status = "已完成" if sess["status"] == "completed" else "进行中"
        stype = "整卷测试" if sess["session_type"] == "exam" else "细节练习"
        lines.append(f"### {sess['title']}")
        lines.append(f"- 类型：{stype}")
        lines.append(f"- 状态：{status}")
        lines.append(f"- 得分：{sess['score'] or '-'}")
        lines.append(f"- 正确率：{sess['accuracy'] or '-'}%")
        lines.append(f"- 正确/错误：{sess['correct_count'] or 0}/{sess['wrong_count'] or 0}")
        if sess["knowledge_point"]:
            lines.append(f"- 知识点：{sess['knowledge_point']}")
        lines.append(f"- 时间：{sess['started_at'] or '-'}")
        lines.append(f"")

    return {"content": "\n".join(lines), "format": "markdown"}
