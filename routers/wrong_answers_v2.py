"""
错题本 V2.0 API
基于 QuestionRecord 自动收录，急救标签分级，盲测重做
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Form, File, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, or_, case
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from pathlib import Path
from difflib import SequenceMatcher
import io
import math
import re

from api_contracts import (
    BooksResponse,
    ExternalImportConfirmResponse,
    ExternalImportParseResponse,
    MarkdownExportResponse,
    RecognizeChaptersResponse,
    WrongAnswerDashboardResponse,
    WrongAnswerDetailResponse,
    WrongAnswerEmptyListResponse,
    WrongAnswerMutationResponse,
    WrongAnswerRetryBatchResponse,
    WrongAnswerRetryResponse,
    WrongAnswerSeverityListResponse,
    WrongAnswerStatsResponse,
    WrongAnswerSyncResponse,
    WrongAnswerTimelineListResponse,
    WrongAnswerVariantGenerateResponse,
    WrongAnswerVariantJudgeResponse,
    WrongAnswerChapterListResponse,
)
from services.content_parser_v2 import get_content_parser

from models import get_db, Chapter
from learning_tracking_models import (
    QuestionRecord, LearningSession, WrongAnswerV2, WrongAnswerRetry,
    make_fingerprint, INVALID_CHAPTER_IDS
)
from utils.data_contracts import (
    canonicalize_ai_evaluation,
    canonicalize_linked_record_ids,
    canonicalize_variant_data,
    coerce_confidence,
)

router = APIRouter(prefix="/api/wrong-answers", tags=["wrong_answers_v2"])


def _coerce_to_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _build_retry_streaks(retry_dates: List[date], today_value: date) -> Dict[str, int]:
    unique_dates = sorted({d for d in retry_dates if d})
    if not unique_dates:
        return {"streak_days": 0, "max_streak_days": 0}

    date_set = set(unique_dates)
    check_date = today_value if today_value in date_set else today_value - timedelta(days=1)
    streak_days = 0
    while check_date in date_set:
        streak_days += 1
        check_date -= timedelta(days=1)

    max_streak = 0
    current = 0
    previous = None
    for item in unique_dates:
        if previous and item == previous + timedelta(days=1):
            current += 1
        else:
            current = 1
        max_streak = max(max_streak, current)
        previous = item

    return {"streak_days": streak_days, "max_streak_days": max_streak}


def _trend_description(direction: str) -> str:
    mapping = {
        "accelerating": "加速好转中",
        "improving": "稳步好转",
        "stable": "基本持平",
        "worsening": "仍在恶化",
    }
    return mapping.get(direction, "趋势待观察")


def _normalize_confidence_value(value: Optional[str]) -> str:
    return coerce_confidence(value, default="unsure")


# ========== Pydantic Models ==========

class RetryRequest(BaseModel):
    user_answer: str
    confidence: str = "unsure"
    time_spent_seconds: int = 0
    recall_text: str = ""              # 回忆阶段文本
    is_landmine_recall: bool = False   # 是否地雷盲测（兼容旧接口）
    is_variant: bool = False           # 是否做的变式题
    skip_recall: bool = False          # 是否跳过了回忆
    skipped_rationale: bool = False    # 是否跳过了自证


class VariantJudgeRequest(BaseModel):
    user_answer: str
    confidence: str = "unsure"
    rationale_text: str = ""
    time_spent_seconds: int = 0


class ExternalImportItem(BaseModel):
    question_text: str
    options: Dict[str, str]
    correct_answer: str
    chapter_name: Optional[str] = None
    chapter_id: Optional[str] = None
    book_name: Optional[str] = None
    key_point: Optional[str] = None
    explanation: Optional[str] = None
    question_type: Optional[str] = "A1"
    difficulty: Optional[str] = "基础"


class ExternalImportConfirmRequest(BaseModel):
    items: List[ExternalImportItem]
    default_severity: str = "normal"


# ========== Severity Algorithm ==========

def compute_severity(error_count: int, confidences: list, correctness: list) -> str:
    """
    计算急救标签，优先级: critical > stubborn > landmine > normal
    - critical: 自信但答错 (confidence=sure AND is_correct=False)
    - stubborn: 同题错>=2次
    - landmine: 不确定但答对 (confidence in unsure/no AND is_correct=True)
    - normal: 其他
    """
    # Check critical: any record where sure + wrong
    for conf, correct in zip(confidences, correctness):
        conf = _normalize_confidence_value(conf)
        if conf == "sure" and not correct:
            return "critical"

    # Check stubborn: error_count >= 2
    if error_count >= 2:
        return "stubborn"

    # Check landmine: unsure/no but correct
    for conf, correct in zip(confidences, correctness):
        conf = _normalize_confidence_value(conf)
        if conf in ("unsure", "no") and correct:
            return "landmine"

    return "normal"


# ========== External Import Helpers ==========

def _decode_text_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "big5"):
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode("latin-1", errors="ignore")


def _extract_pdf_text(raw: bytes) -> str:
    reader_cls = None
    try:
        from pypdf import PdfReader as _PdfReader
        reader_cls = _PdfReader
    except Exception:
        try:
            from PyPDF2 import PdfReader as _PdfReader
            reader_cls = _PdfReader
        except Exception:
            reader_cls = None

    if reader_cls is None:
        raise HTTPException(status_code=400, detail="未安装 PDF 解析依赖（pypdf/PyPDF2），请先粘贴文本或安装依赖")

    reader = reader_cls(io.BytesIO(raw))
    texts = []
    for page in reader.pages:
        texts.append((page.extract_text() or "").strip())
    text = "\n".join([t for t in texts if t]).strip()
    if not text:
        raise HTTPException(status_code=400, detail="PDF 文本提取为空，请尝试复制文本粘贴导入")
    return text


def _normalize_option_map(options: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(options, dict):
        return {}

    normalized = {}
    for k, v in options.items():
        key = re.sub(r"[^A-E]", "", str(k or "").upper())[:1]
        val = str(v or "").strip()
        if key and val:
            normalized[key] = val
    return {k: normalized[k] for k in ["A", "B", "C", "D", "E"] if k in normalized}


from utils.answer import normalize_answer as _normalize_answer
from utils.answer import answers_match as _answers_match
from utils.sm2 import sm2_update, quality_from_result


def _build_import_fingerprint(question_text: str, options: Dict[str, str]) -> str:
    opts = "||".join(f"{k}:{options.get(k, '').strip()}" for k in ["A", "B", "C", "D", "E"] if k in options)
    return make_fingerprint(f"{(question_text or '').strip()}||{opts}")


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[\s\-—_:：，,。.;；（）()【】\[\]/\\]+", "", str(text or "").lower())


def _resolve_chapter_id(db: Session, chapter_name: Optional[str], book_name: Optional[str]) -> Optional[str]:
    chapter_name = (chapter_name or "").strip()
    book_name = (book_name or "").strip()
    if not chapter_name and not book_name:
        return None

    query = db.query(Chapter)
    if book_name:
        query = query.filter(Chapter.book.contains(book_name))
    chapters = query.all()
    if not chapters:
        chapters = db.query(Chapter).all()

    chapter_norm = _normalize_match_text(chapter_name)
    if chapter_norm:
        # 1) 子串直接命中
        for ch in chapters:
            cands = [f"{ch.chapter_number} {ch.chapter_title}", ch.chapter_title]
            for cand in cands:
                cand_norm = _normalize_match_text(cand)
                if cand_norm and (chapter_norm in cand_norm or cand_norm in chapter_norm):
                    return ch.id

        # 2) 相似度回退
        best_id = None
        best_score = 0.0
        for ch in chapters:
            cand = f"{ch.chapter_number} {ch.chapter_title}"
            score = SequenceMatcher(None, chapter_norm, _normalize_match_text(cand)).ratio()
            if score > best_score:
                best_score = score
                best_id = ch.id
        if best_id and best_score >= 0.58:
            return best_id

    if book_name and chapters:
        return chapters[0].id
    return None


# ========== POST /sync ==========

@router.post("/sync", response_model=WrongAnswerSyncResponse)
async def sync_wrong_answers(db: Session = Depends(get_db)):
    """
    全量同步：扫描 QuestionRecord，按指纹分组，upsert WrongAnswerV2
    收录规则: is_correct=False OR confidence IN ('unsure','no')
    """
    # 获取所有符合条件的题目记录
    records = db.query(QuestionRecord, LearningSession.chapter_id).join(
        LearningSession, QuestionRecord.session_id == LearningSession.id
    ).filter(
        (QuestionRecord.is_correct == False) |
        (QuestionRecord.confidence.in_(["unsure", "no"]))
    ).all()

    # 按指纹分组
    fp_groups: Dict[str, Dict] = {}
    for qr, chapter_id in records:
        fp = make_fingerprint(qr.question_text or "")
        if fp not in fp_groups:
            fp_groups[fp] = {
                "question_text": qr.question_text,
                "options": qr.options,
                "correct_answer": qr.correct_answer,
                "explanation": qr.explanation,
                "key_point": qr.key_point,
                "question_type": qr.question_type,
                "difficulty": qr.difficulty,
                "chapter_id": chapter_id,
                "record_ids": [],
                "error_count": 0,
                "encounter_count": 0,
                "confidences": [],
                "correctness": [],
                "timestamps": [],
            }
        g = fp_groups[fp]
        g["record_ids"].append(qr.id)
        g["encounter_count"] += 1
        if not qr.is_correct:
            g["error_count"] += 1
        g["confidences"].append(_normalize_confidence_value(qr.confidence))
        g["correctness"].append(qr.is_correct)
        if qr.answered_at:
            g["timestamps"].append(qr.answered_at)
        # 用最新的解析和知识点
        if qr.explanation:
            g["explanation"] = qr.explanation
        if qr.key_point:
            g["key_point"] = qr.key_point

    # Upsert
    created = 0
    updated = 0
    for fp, g in fp_groups.items():
        existing = db.query(WrongAnswerV2).filter(
            WrongAnswerV2.question_fingerprint == fp
        ).first()

        severity = compute_severity(g["error_count"], g["confidences"], g["correctness"])
        sorted_ts = sorted(g["timestamps"]) if g["timestamps"] else []

        if existing:
            existing.error_count = g["error_count"]
            existing.encounter_count = g["encounter_count"]
            existing.linked_record_ids = canonicalize_linked_record_ids(g["record_ids"])
            # severity 只升不降
            severity_order = {"normal": 0, "landmine": 1, "stubborn": 2, "critical": 3}
            if severity_order.get(severity, 0) > severity_order.get(existing.severity_tag, 0):
                existing.severity_tag = severity
            if sorted_ts:
                existing.first_wrong_at = sorted_ts[0]
                existing.last_wrong_at = sorted_ts[-1]
            # 更新快照
            existing.options = _normalize_option_map(g["options"] or {})
            existing.explanation = g["explanation"]
            existing.key_point = g["key_point"]
            # chapter_id: 只补齐，不覆盖已识别的章节（避免冲掉AI分类结果）
            if (not existing.chapter_id or existing.chapter_id == '0') and g["chapter_id"] and g["chapter_id"] != '0':
                existing.chapter_id = g["chapter_id"]
            existing.updated_at = datetime.now()
            updated += 1
        else:
            wa = WrongAnswerV2(
                question_fingerprint=fp,
                question_text=g["question_text"],
                options=_normalize_option_map(g["options"] or {}),
                correct_answer=g["correct_answer"],
                explanation=g["explanation"],
                key_point=g["key_point"],
                question_type=g["question_type"],
                difficulty=g["difficulty"],
                chapter_id=g["chapter_id"],
                error_count=g["error_count"],
                encounter_count=g["encounter_count"],
                severity_tag=severity,
                linked_record_ids=canonicalize_linked_record_ids(g["record_ids"]),
                first_wrong_at=sorted_ts[0] if sorted_ts else datetime.now(),
                last_wrong_at=sorted_ts[-1] if sorted_ts else datetime.now(),
            )
            db.add(wa)
            created += 1

    db.commit()
    total = db.query(WrongAnswerV2).filter(WrongAnswerV2.mastery_status == "active").count()
    return {"created": created, "updated": updated, "total_active": total}


# ========== POST /import/parse ==========

@router.post("/import/parse", response_model=ExternalImportParseResponse)
async def parse_external_wrong_questions(
    text: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    max_items: int = Form(default=200),
    db: Session = Depends(get_db)
):
    """
    解析外部错题文本（支持粘贴文本或上传 PDF/TXT）
    返回可确认入库的预览列表
    """
    if not text and not file:
        raise HTTPException(status_code=400, detail="请提供文本或上传文件")

    raw_text = ""
    source_name = "pasted_text"

    if file is not None:
        source_name = file.filename or "uploaded_file"
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="上传文件为空")

        suffix = Path(source_name).suffix.lower()
        if suffix == ".pdf":
            raw_text = _extract_pdf_text(raw)
        else:
            raw_text = _decode_text_bytes(raw)
    else:
        raw_text = str(text or "")

    raw_text = raw_text.strip()
    if len(raw_text) < 30:
        raise HTTPException(status_code=400, detail="可解析文本过短，请检查输入内容")

    parser = get_content_parser()
    parsed = await parser.parse_external_wrong_questions(raw_text, max_items=max_items)

    raw_questions = parsed.get("questions", [])
    normalized_items: List[Dict[str, Any]] = []
    chapter_cache: Dict[str, Optional[str]] = {}

    for item in raw_questions:
        q_text = str(item.get("question_text") or "").strip()
        options = _normalize_option_map(item.get("options") or {})
        answer = _normalize_answer(item.get("correct_answer"))

        if not q_text or len(options) < 2 or not answer:
            continue

        if answer not in options:
            # 允许模型漏掉某个选项文本时仍保留，方便前端二次人工修正
            pass

        chapter_name = str(item.get("chapter_name") or parsed.get("chapter_name") or "").strip()
        book_name = str(item.get("book_name") or parsed.get("book_name") or "").strip()

        cache_key = f"{book_name}||{chapter_name}"
        if cache_key not in chapter_cache:
            chapter_cache[cache_key] = _resolve_chapter_id(db, chapter_name, book_name)

        chapter_id = chapter_cache[cache_key]
        fingerprint = _build_import_fingerprint(q_text, options)

        normalized_items.append({
            "question_no": item.get("question_no"),
            "question_text": q_text,
            "options": options,
            "correct_answer": answer,
            "chapter_name": chapter_name,
            "chapter_id": chapter_id,
            "book_name": book_name,
            "fingerprint": fingerprint,
        })

    fingerprints = list({it["fingerprint"] for it in normalized_items})
    existing_map: Dict[str, int] = {}
    if fingerprints:
        rows = db.query(WrongAnswerV2.question_fingerprint, WrongAnswerV2.id).filter(
            WrongAnswerV2.question_fingerprint.in_(fingerprints)
        ).all()
        existing_map = {fp: wid for fp, wid in rows}

    chapter_ids = list({it["chapter_id"] for it in normalized_items if it.get("chapter_id")})
    chapter_label_map: Dict[str, str] = {}
    if chapter_ids:
        for ch in db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all():
            chapter_label_map[ch.id] = f"{ch.book} - {ch.chapter_number} {ch.chapter_title}"

    for it in normalized_items:
        wid = existing_map.get(it["fingerprint"])
        it["exists"] = bool(wid)
        it["existing_wrong_id"] = wid
        if it.get("chapter_id"):
            it["chapter_label"] = chapter_label_map.get(it["chapter_id"])

    duplicates = sum(1 for it in normalized_items if it.get("exists"))

    return {
        "source_name": source_name,
        "book_name": parsed.get("book_name", ""),
        "chapter_name": parsed.get("chapter_name", ""),
        "total_parsed": len(raw_questions),
        "total_valid": len(normalized_items),
        "duplicate_count": duplicates,
        "new_count": len(normalized_items) - duplicates,
        "items": normalized_items,
    }


# ========== POST /import/confirm ==========

@router.post("/import/confirm", response_model=ExternalImportConfirmResponse)
async def confirm_external_wrong_import(
    body: ExternalImportConfirmRequest,
    db: Session = Depends(get_db)
):
    """
    确认导入外部错题：
    - 指纹去重
    - 统一初始化为 active + SM-2 立即可复习
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="导入列表为空")

    allowed_severity = {"normal", "landmine", "stubborn", "critical"}
    severity = body.default_severity if body.default_severity in allowed_severity else "normal"

    prepared = []
    errors = []
    for idx, item in enumerate(body.items, start=1):
        q_text = str(item.question_text or "").strip()
        options = _normalize_option_map(item.options or {})
        answer = _normalize_answer(item.correct_answer)

        if not q_text:
            errors.append({"index": idx, "reason": "题干为空"})
            continue
        if len(options) < 2:
            errors.append({"index": idx, "reason": "选项少于2个"})
            continue
        if not answer:
            errors.append({"index": idx, "reason": "答案无效"})
            continue

        chapter_id = item.chapter_id
        if not chapter_id:
            chapter_id = _resolve_chapter_id(db, item.chapter_name, item.book_name)

        fp = _build_import_fingerprint(q_text, options)
        prepared.append({
            "index": idx,
            "fingerprint": fp,
            "question_text": q_text,
            "options": options,
            "correct_answer": answer,
            "chapter_id": chapter_id,
            "chapter_name": (item.chapter_name or "").strip(),
            "key_point": (item.key_point or "").strip(),
            "explanation": (item.explanation or "").strip(),
            "question_type": (item.question_type or "A1").strip() or "A1",
            "difficulty": (item.difficulty or "基础").strip() or "基础",
        })

    if not prepared:
        raise HTTPException(status_code=400, detail={"message": "无可导入题目", "errors": errors[:20]})

    fingerprints = list({it["fingerprint"] for it in prepared})
    existing_items = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.question_fingerprint.in_(fingerprints)
    ).all()
    existing_map = {wa.question_fingerprint: wa for wa in existing_items}

    now = datetime.now()
    today = date.today()

    created = 0
    skipped = 0
    updated = 0
    created_ids = []

    for it in prepared:
        existing = existing_map.get(it["fingerprint"])
        if existing:
            changed = False
            # 保守更新：只补缺失字段，不覆盖已有历史数据
            if not existing.chapter_id and it["chapter_id"]:
                existing.chapter_id = it["chapter_id"]
                changed = True
            if not existing.key_point and it["key_point"]:
                existing.key_point = it["key_point"]
                changed = True
            if not existing.explanation and it["explanation"]:
                existing.explanation = it["explanation"]
                changed = True
            if changed:
                existing.updated_at = now
                updated += 1
            else:
                skipped += 1
            continue

        wa = WrongAnswerV2(
            question_fingerprint=it["fingerprint"],
            question_text=it["question_text"],
            options=_normalize_option_map(it["options"] or {}),
            correct_answer=it["correct_answer"],
            explanation=it["explanation"] or None,
            key_point=it["key_point"] or it["chapter_name"] or None,
            question_type=it["question_type"],
            difficulty=it["difficulty"],
            chapter_id=it["chapter_id"],
            error_count=1,
            encounter_count=1,
            retry_count=0,
            severity_tag=severity,
            mastery_status="active",
            linked_record_ids=canonicalize_linked_record_ids([]),
            sm2_ef=2.5,
            sm2_interval=0,
            sm2_repetitions=0,
            next_review_date=today,
            first_wrong_at=now,
            last_wrong_at=now,
        )
        db.add(wa)
        db.flush()
        created += 1
        created_ids.append(wa.id)

    db.commit()

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
        "created_ids": created_ids[:50],
        "message": f"导入完成：新增{created}，补全更新{updated}，跳过{skipped}",
    }


# ========== GET /stats ==========

@router.get("/stats", response_model=WrongAnswerStatsResponse)
async def get_wrong_answer_stats(db: Session = Depends(get_db)):
    """统计概览"""
    active = db.query(WrongAnswerV2).filter(WrongAnswerV2.mastery_status == "active")
    archived = db.query(WrongAnswerV2).filter(WrongAnswerV2.mastery_status == "archived")

    total_active = active.count()
    total_archived = archived.count()

    # severity 分布
    severity_counts = {}
    for tag in ["critical", "stubborn", "landmine", "normal"]:
        severity_counts[tag] = active.filter(WrongAnswerV2.severity_tag == tag).count()

    # Top 薄弱知识点 (按 error_count 降序)
    top_weak = db.query(
        WrongAnswerV2.key_point,
        func.count(WrongAnswerV2.id).label("cnt"),
        func.sum(WrongAnswerV2.error_count).label("errors")
    ).filter(
        WrongAnswerV2.mastery_status == "active",
        WrongAnswerV2.key_point.isnot(None)
    ).group_by(WrongAnswerV2.key_point).order_by(
        desc("errors")
    ).limit(5).all()

    top_weak_points = [
        {"name": kp, "count": int(cnt), "errors": int(errs)}
        for kp, cnt, errs in top_weak
    ]

    # 重做正确率
    total_retries = db.query(WrongAnswerRetry).count()
    correct_retries = db.query(WrongAnswerRetry).filter(WrongAnswerRetry.is_correct == True).count()
    retry_correct_rate = round(correct_retries / total_retries * 100, 1) if total_retries > 0 else 0

    return {
        "total_active": total_active,
        "total_archived": total_archived,
        "severity_counts": severity_counts,
        "top_weak_points": top_weak_points,
        "retry_correct_rate": retry_correct_rate,
        "total_retries": total_retries,
    }


@router.get("/dashboard", response_model=WrongAnswerDashboardResponse)
async def get_wrong_answer_dashboard(db: Session = Depends(get_db)):
    """错题本数据看板"""
    today_value = date.today()
    tomorrow_value = today_value + timedelta(days=1)
    week_start = today_value - timedelta(days=today_value.weekday())
    week_end = week_start + timedelta(days=6)
    trend_dates = [today_value - timedelta(days=offset) for offset in range(6, -1, -1)]
    trend_start = trend_dates[0]

    active_query = db.query(WrongAnswerV2).filter(WrongAnswerV2.mastery_status == "active")
    archived_query = db.query(WrongAnswerV2).filter(WrongAnswerV2.mastery_status == "archived")

    active_count = active_query.count()
    archived_count = archived_query.count()
    total_count = active_count + archived_count
    mastery_percent = round(archived_count / total_count * 100, 1) if total_count > 0 else 0.0

    total_retries = db.query(WrongAnswerRetry).count()
    correct_retries = db.query(WrongAnswerRetry).filter(WrongAnswerRetry.is_correct == True).count()
    retry_correct_rate = round(correct_retries / total_retries * 100, 1) if total_retries > 0 else 0.0

    def _retry_rate_between(start_date: date, end_date: date) -> float:
        rows = db.query(WrongAnswerRetry).filter(
            func.date(WrongAnswerRetry.retried_at) >= start_date.isoformat(),
            func.date(WrongAnswerRetry.retried_at) <= end_date.isoformat(),
        )
        total = rows.count()
        if total == 0:
            return 0.0
        correct = rows.filter(WrongAnswerRetry.is_correct == True).count()
        return round(correct / total * 100, 1)

    retry_rate_current_week = _retry_rate_between(today_value - timedelta(days=6), today_value)
    retry_rate_previous_week = _retry_rate_between(today_value - timedelta(days=13), today_value - timedelta(days=7))
    retry_rate_delta_vs_last_week = round(retry_rate_current_week - retry_rate_previous_week, 1)

    severity_counts: Dict[str, int] = {}
    severity_distribution: Dict[str, Dict[str, float]] = {}
    for tag in ["critical", "stubborn", "landmine", "normal"]:
        count = active_query.filter(WrongAnswerV2.severity_tag == tag).count()
        severity_counts[tag] = count
        severity_distribution[tag] = {
            "count": count,
            "percent": round(count / active_count * 100, 1) if active_count > 0 else 0.0,
        }

    today_due = active_query.filter(
        WrongAnswerV2.next_review_date.isnot(None),
        WrongAnswerV2.next_review_date <= today_value,
    ).count()
    tomorrow_due = active_query.filter(
        WrongAnswerV2.next_review_date.isnot(None),
        WrongAnswerV2.next_review_date <= tomorrow_value,
    ).count()
    week_due = active_query.filter(
        WrongAnswerV2.next_review_date.isnot(None),
        WrongAnswerV2.next_review_date <= week_end,
    ).count()

    created_rows = db.query(
        func.date(WrongAnswerV2.created_at).label("day"),
        func.count(WrongAnswerV2.id).label("count"),
    ).filter(
        WrongAnswerV2.created_at.isnot(None),
        func.date(WrongAnswerV2.created_at) >= trend_start.isoformat(),
        func.date(WrongAnswerV2.created_at) <= today_value.isoformat(),
    ).group_by("day").all()
    created_map = {str(day): int(count or 0) for day, count in created_rows if day}

    archived_rows = db.query(
        func.date(WrongAnswerV2.archived_at).label("day"),
        func.count(WrongAnswerV2.id).label("count"),
    ).filter(
        WrongAnswerV2.archived_at.isnot(None),
        func.date(WrongAnswerV2.archived_at) >= trend_start.isoformat(),
        func.date(WrongAnswerV2.archived_at) <= today_value.isoformat(),
    ).group_by("day").all()
    archived_map = {str(day): int(count or 0) for day, count in archived_rows if day}

    retried_rows = db.query(
        func.date(WrongAnswerRetry.retried_at).label("day"),
        func.count(WrongAnswerRetry.id).label("count"),
    ).filter(
        WrongAnswerRetry.retried_at.isnot(None),
        func.date(WrongAnswerRetry.retried_at) >= trend_start.isoformat(),
        func.date(WrongAnswerRetry.retried_at) <= today_value.isoformat(),
    ).group_by("day").all()
    retried_map = {str(day): int(count or 0) for day, count in retried_rows if day}

    today_key = today_value.isoformat()
    today_new_count = created_map.get(today_key, 0)
    today_archived_count = archived_map.get(today_key, 0)
    today_retried_count = retried_map.get(today_key, 0)
    today_net_change = today_archived_count - today_new_count

    week_keys = {
        (week_start + timedelta(days=offset)).isoformat()
        for offset in range((today_value - week_start).days + 1)
    }
    this_week_new_count = sum(created_map.get(key, 0) for key in week_keys)
    this_week_archived_count = sum(archived_map.get(key, 0) for key in week_keys)
    this_week_net_change = this_week_archived_count - this_week_new_count

    daily_trend: List[Dict[str, Any]] = []
    for item_date in trend_dates:
        day_key = item_date.isoformat()
        new_count = created_map.get(day_key, 0)
        archived_day_count = archived_map.get(day_key, 0)
        daily_trend.append({
            "date": day_key,
            "new": new_count,
            "archived": archived_day_count,
            "net": archived_day_count - new_count,
        })

    sum_archived = sum(item["archived"] for item in daily_trend)
    sum_new = sum(item["new"] for item in daily_trend)
    avg_daily_archived_raw = sum_archived / len(daily_trend) if daily_trend else 0.0
    avg_daily_new_raw = sum_new / len(daily_trend) if daily_trend else 0.0
    net_daily_rate_raw = avg_daily_archived_raw - avg_daily_new_raw

    recent_3d = daily_trend[-3:]
    previous_4d = daily_trend[:-3]
    recent_3d_rate = (
        sum(item["archived"] - item["new"] for item in recent_3d) / len(recent_3d)
        if recent_3d else 0.0
    )
    prev_4d_rate = (
        sum(item["archived"] - item["new"] for item in previous_4d) / len(previous_4d)
        if previous_4d else 0.0
    )

    if recent_3d_rate > prev_4d_rate + 0.5:
        trend_direction = "accelerating"
    elif recent_3d_rate > 0:
        trend_direction = "improving"
    elif abs(recent_3d_rate) < 1e-9:
        trend_direction = "stable"
    else:
        trend_direction = "worsening"

    estimated_days_to_clear = None
    estimated_clear_date = None
    projection_message = "当前速度无法清零，需加大复习量"
    if net_daily_rate_raw > 0:
        estimated_days_to_clear = int(math.ceil(active_count / net_daily_rate_raw)) if active_count > 0 else 0
        estimated_clear_date = (today_value + timedelta(days=estimated_days_to_clear)).isoformat()
        projection_message = f"约 {estimated_days_to_clear} 天后可清零"

    retry_date_rows = db.query(func.date(WrongAnswerRetry.retried_at)).filter(
        WrongAnswerRetry.retried_at.isnot(None)
    ).distinct().all()
    retry_dates = [_coerce_to_date(item[0]) for item in retry_date_rows]
    streak_stats = _build_retry_streaks([d for d in retry_dates if d], today_value)

    chapter_totals_rows = db.query(
        WrongAnswerV2.chapter_id.label("chapter_id"),
        func.count(WrongAnswerV2.id).label("total_count"),
        func.sum(case((WrongAnswerV2.mastery_status == "archived", 1), else_=0)).label("archived_count"),
    ).group_by(WrongAnswerV2.chapter_id).all()
    chapter_totals = {
        row.chapter_id: {
            "total_count": int(row.total_count or 0),
            "archived_count": int(row.archived_count or 0),
        }
        for row in chapter_totals_rows
    }

    weak_chapter_rows = db.query(
        WrongAnswerV2.chapter_id.label("chapter_id"),
        func.count(WrongAnswerV2.id).label("active_count"),
        func.sum(case((WrongAnswerV2.severity_tag == "critical", 1), else_=0)).label("critical_count"),
        func.sum(case((WrongAnswerV2.severity_tag == "stubborn", 1), else_=0)).label("stubborn_count"),
    ).filter(
        WrongAnswerV2.mastery_status == "active"
    ).group_by(WrongAnswerV2.chapter_id).order_by(
        desc("active_count")
    ).limit(5).all()

    chapter_ids = [row.chapter_id for row in weak_chapter_rows if row.chapter_id]
    chapter_map = {}
    if chapter_ids:
        for chapter in db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all():
            chapter_map[chapter.id] = chapter

    weak_chapters: List[Dict[str, Any]] = []
    for row in weak_chapter_rows:
        chapter_id = row.chapter_id
        chapter = chapter_map.get(chapter_id)
        total_info = chapter_totals.get(chapter_id, {"total_count": int(row.active_count or 0), "archived_count": 0})
        total_for_chapter = int(total_info["total_count"] or 0)
        archived_for_chapter = int(total_info["archived_count"] or 0)
        mastery_for_chapter = round(archived_for_chapter / total_for_chapter * 100, 1) if total_for_chapter > 0 else 0.0

        if chapter:
            chapter_name = chapter.chapter_title
        elif chapter_id:
            chapter_name = chapter_id
        else:
            chapter_name = "未分类"

        weak_chapters.append({
            "chapter_id": chapter_id or "",
            "chapter_name": chapter_name,
            "active_count": int(row.active_count or 0),
            "critical_count": int(row.critical_count or 0),
            "stubborn_count": int(row.stubborn_count or 0),
            "mastery_percent": mastery_for_chapter,
        })

    return {
        "overview": {
            "active_count": active_count,
            "archived_count": archived_count,
            "total_count": total_count,
            "mastery_percent": mastery_percent,
            "retry_correct_rate": retry_correct_rate,
            "retry_rate_delta_vs_last_week": retry_rate_delta_vs_last_week,
            "streak_days": streak_stats["streak_days"],
            "max_streak_days": streak_stats["max_streak_days"],
            "active_delta_vs_yesterday": today_new_count - today_archived_count,
        },
        "today": {
            "new_count": today_new_count,
            "archived_count": today_archived_count,
            "retried_count": today_retried_count,
            "net_change": today_net_change,
            "trend": "improving" if today_net_change > 0 else ("worsening" if today_net_change < 0 else "stable"),
        },
        "this_week": {
            "new_count": this_week_new_count,
            "archived_count": this_week_archived_count,
            "net_change": this_week_net_change,
        },
        "severity_distribution": severity_distribution,
        "review_pressure": {
            "today_due": today_due,
            "tomorrow_due": tomorrow_due,
            "week_due": week_due,
        },
        "projection": {
            "avg_daily_archived": round(avg_daily_archived_raw, 1),
            "avg_daily_new": round(avg_daily_new_raw, 1),
            "net_daily_rate": round(net_daily_rate_raw, 1),
            "estimated_days_to_clear": estimated_days_to_clear,
            "estimated_clear_date": estimated_clear_date,
            "trend_direction": trend_direction,
            "trend_description": _trend_description(trend_direction),
            "projection_message": projection_message,
        },
        "daily_trend": daily_trend,
        "weak_chapters": weak_chapters,
    }


# ========== GET /list ==========

@router.get(
    "/list",
    response_model=WrongAnswerSeverityListResponse
    | WrongAnswerChapterListResponse
    | WrongAnswerTimelineListResponse
    | WrongAnswerEmptyListResponse,
)
async def get_wrong_answer_list(
    view: str = "severity",  # severity | chapter | timeline
    severity: Optional[str] = None,
    book: Optional[str] = None,
    status: str = "active",  # active | archived | all
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db)
):
    """三视图列表"""
    query = db.query(WrongAnswerV2)

    # 状态筛选
    if status == "active":
        query = query.filter(WrongAnswerV2.mastery_status == "active")
    elif status == "archived":
        query = query.filter(WrongAnswerV2.mastery_status == "archived")

    # severity 筛选
    if severity:
        query = query.filter(WrongAnswerV2.severity_tag == severity)

    # book 筛选 (需要 JOIN Chapter)
    if book:
        query = query.join(Chapter, WrongAnswerV2.chapter_id == Chapter.id).filter(
            Chapter.book == book
        )

    total = query.count()

    if view == "severity":
        # 按 severity 优先级 + error_count 降序
        from sqlalchemy import case
        severity_order = case(
            (WrongAnswerV2.severity_tag == "critical", 0),
            (WrongAnswerV2.severity_tag == "stubborn", 1),
            (WrongAnswerV2.severity_tag == "landmine", 2),
            else_=3
        )
        items = query.order_by(severity_order, desc(WrongAnswerV2.error_count)).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        return {
            "view": "severity",
            "total": total,
            "page": page,
            "items": [_serialize_item(wa) for wa in items]
        }

    elif view == "chapter":
        # 树状: book → chapter → key_point → items，带汇总统计
        all_items = query.order_by(desc(WrongAnswerV2.error_count)).all()
        chapter_ids = set(wa.chapter_id for wa in all_items if wa.chapter_id)
        chapters = {}
        if chapter_ids:
            for ch in db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all():
                chapters[ch.id] = ch

        tree = {}
        for wa in all_items:
            ch = chapters.get(wa.chapter_id)
            book_name = ch.book if ch else "未分类"
            ch_name = f"{ch.chapter_number} {ch.chapter_title}" if ch else "未关联章节"
            kp = wa.key_point or "未标注"

            if book_name not in tree:
                tree[book_name] = {"_stats": {"total": 0, "critical": 0, "error_sum": 0}, "chapters": {}}
            if ch_name not in tree[book_name]["chapters"]:
                tree[book_name]["chapters"][ch_name] = {"_stats": {"total": 0, "critical": 0, "error_sum": 0}, "key_points": {}}

            item = _serialize_item(wa)
            tree[book_name]["chapters"][ch_name]["key_points"].setdefault(kp, []).append(item)

            # 汇总统计
            tree[book_name]["_stats"]["total"] += 1
            tree[book_name]["_stats"]["error_sum"] += wa.error_count or 0
            if wa.severity_tag == "critical":
                tree[book_name]["_stats"]["critical"] += 1

            tree[book_name]["chapters"][ch_name]["_stats"]["total"] += 1
            tree[book_name]["chapters"][ch_name]["_stats"]["error_sum"] += wa.error_count or 0
            if wa.severity_tag == "critical":
                tree[book_name]["chapters"][ch_name]["_stats"]["critical"] += 1

        return {"view": "chapter", "total": total, "tree": tree}

    elif view == "timeline":
        # 按 年月 → 日期 分组
        all_items = query.order_by(desc(WrongAnswerV2.last_wrong_at)).all()
        current_month = date.today().strftime("%Y-%m")

        tree = {}
        for wa in all_items:
            d = wa.last_wrong_at.date() if wa.last_wrong_at else None
            if not d:
                month_key = "未知时间"
                date_key = "未知日期"
            else:
                month_key = d.strftime("%Y-%m")
                date_key = d.isoformat()

            if month_key not in tree:
                tree[month_key] = {"_stats": {"total": 0, "critical": 0}, "dates": {}}

            item = _serialize_item(wa)
            tree[month_key]["dates"].setdefault(date_key, []).append(item)
            tree[month_key]["_stats"]["total"] += 1
            if wa.severity_tag == "critical":
                tree[month_key]["_stats"]["critical"] += 1

        # 月份倒序排列
        sorted_tree = {}
        for k in sorted(tree.keys(), reverse=True):
            sorted_tree[k] = tree[k]

        return {"view": "timeline", "total": total, "tree": sorted_tree, "current_month": current_month}

    return {"view": view, "total": 0, "items": []}


def _serialize_item(wa: WrongAnswerV2) -> dict:
    """序列化列表项（不含正确答案）"""
    preview = (wa.question_text or "")[:80]
    if len(wa.question_text or "") > 80:
        preview += "..."
    return {
        "id": wa.id,
        "question_preview": preview,
        "key_point": wa.key_point,
        "question_type": wa.question_type,
        "difficulty": wa.difficulty,
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "encounter_count": wa.encounter_count,
        "retry_count": wa.retry_count,
        "last_retry_correct": wa.last_retry_correct,
        "mastery_status": wa.mastery_status,
        "is_fusion": getattr(wa, 'is_fusion', False),
        "fusion_level": getattr(wa, 'fusion_level', 0),
        "first_wrong_at": wa.first_wrong_at.isoformat() if wa.first_wrong_at else None,
        "last_wrong_at": wa.last_wrong_at.isoformat() if wa.last_wrong_at else None,
        "last_retried_at": wa.last_retried_at.isoformat() if wa.last_retried_at else None,
    }


# ========== GET /{id} — 单题详情 ==========

@router.get("/{wrong_id:int}", response_model=WrongAnswerDetailResponse)
async def get_wrong_answer_detail(wrong_id: int, db: Session = Depends(get_db)):
    """手术台用：完整题目 + 历史记录 + 重做记录"""
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    # 历史答题记录
    history = []
    if wa.linked_record_ids:
        records = db.query(QuestionRecord).filter(
            QuestionRecord.id.in_(wa.linked_record_ids)
        ).order_by(QuestionRecord.answered_at).all()
        for qr in records:
            sess = db.query(LearningSession).filter(
                LearningSession.id == qr.session_id
            ).first()
            history.append({
                "user_answer": qr.user_answer,
                "is_correct": qr.is_correct,
                "confidence": _normalize_confidence_value(qr.confidence),
                "time_spent_seconds": qr.time_spent_seconds,
                "answered_at": qr.answered_at.isoformat() if qr.answered_at else None,
                "session_title": sess.title if sess else None,
            })

    # 重做记录
    retries = db.query(WrongAnswerRetry).filter(
        WrongAnswerRetry.wrong_answer_id == wrong_id
    ).order_by(WrongAnswerRetry.retried_at).all()

    return {
        "id": wa.id,
        "question_text": wa.question_text,
        "options": wa.options or {},
        "correct_answer": wa.correct_answer,
        "explanation": wa.explanation,
        "key_point": wa.key_point,
        "question_type": wa.question_type,
        "difficulty": wa.difficulty,
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "encounter_count": wa.encounter_count,
        "retry_count": wa.retry_count,
        "last_retry_correct": wa.last_retry_correct,
        "mastery_status": wa.mastery_status,
        "has_variant": wa.variant_data is not None,
        # SM-2 状态
        "sm2_ef": wa.sm2_ef,
        "sm2_interval": wa.sm2_interval,
        "sm2_repetitions": wa.sm2_repetitions,
        "next_review_date": wa.next_review_date.isoformat() if wa.next_review_date else None,
        "first_wrong_at": wa.first_wrong_at.isoformat() if wa.first_wrong_at else None,
        "last_wrong_at": wa.last_wrong_at.isoformat() if wa.last_wrong_at else None,
        "history": history,
        "retries": [
            {
                "user_answer": r.user_answer,
                "is_correct": r.is_correct,
                "confidence": _normalize_confidence_value(r.confidence),
                "time_spent_seconds": r.time_spent_seconds,
                "retried_at": r.retried_at.isoformat() if r.retried_at else None,
            }
            for r in retries
        ]
    }


# ========== POST /{id}/retry ==========

@router.post("/{wrong_id:int}/retry", response_model=WrongAnswerRetryResponse)
async def submit_retry(wrong_id: int, body: RetryRequest, db: Session = Depends(get_db)):
    """提交重做结果（统一入口：原题/变式，含 SM-2 更新）"""
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    # 判定对错：变式题用 variant_answer，原题用 correct_answer
    variant_data = canonicalize_variant_data(wa.variant_data) or {}
    if body.is_variant and variant_data:
        correct_raw = variant_data.get("variant_answer") or ""
    else:
        correct_raw = wa.correct_answer or ""
    is_correct = _answers_match(body.user_answer, correct_raw)

    confidence = _normalize_confidence_value(body.confidence)

    # 创建重做记录
    retry = WrongAnswerRetry(
        wrong_answer_id=wrong_id,
        user_answer=body.user_answer,
        is_correct=is_correct,
        confidence=confidence,
        time_spent_seconds=body.time_spent_seconds,
        retried_at=datetime.now(),
        rationale_text=body.recall_text or None,
        is_landmine_recall=body.is_landmine_recall,
        is_variant=body.is_variant,
    )
    db.add(retry)

    # 更新错题统计
    wa.retry_count += 1
    wa.last_retry_correct = is_correct
    wa.last_retry_confidence = confidence
    wa.last_retried_at = datetime.now()
    wa.updated_at = datetime.now()

    if not is_correct:
        wa.error_count += 1
        if wa.severity_tag not in ("critical",):
            if confidence == "sure":
                wa.severity_tag = "critical"
            elif wa.error_count >= 2 and wa.severity_tag not in ("critical", "stubborn"):
                wa.severity_tag = "stubborn"

    # 地雷排除：答对+确定 → 降级为 normal
    if is_correct and confidence == "sure" and wa.severity_tag == "landmine":
        wa.severity_tag = "normal"

    # SM-2 更新（含跳过回忆/跳过自证的降档惩罚）
    quality = quality_from_result(is_correct, confidence)
    if body.skip_recall:
        quality = max(0, quality - 1)
    if body.skipped_rationale:
        quality = max(0, quality - 1)
    sm2_update(wa, quality)
    auto_archived = wa.mastery_status == "archived"

    can_archive = (is_correct and confidence == "sure") and not auto_archived

    db.commit()

    # 获取之前的重做记录用于对比
    previous = db.query(WrongAnswerRetry).filter(
        WrongAnswerRetry.wrong_answer_id == wrong_id
    ).order_by(desc(WrongAnswerRetry.retried_at)).all()

    return {
        "is_correct": is_correct,
        "correct_answer": wa.correct_answer,
        "explanation": wa.explanation,
        "key_point": wa.key_point,
        "can_archive": can_archive,
        "auto_archived": auto_archived,
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "retry_count": wa.retry_count,
        "recall_text": body.recall_text or "",
        # SM-2 状态
        "sm2_ef": wa.sm2_ef,
        "sm2_interval": wa.sm2_interval,
        "sm2_repetitions": wa.sm2_repetitions,
        "next_review_date": wa.next_review_date.isoformat() if wa.next_review_date else None,
        # 变式信息
        "variant_answer": variant_data.get("variant_answer") if body.is_variant and variant_data else None,
        "variant_explanation": variant_data.get("variant_explanation") if body.is_variant and variant_data else None,
        "previous_attempts": [
            {
                "user_answer": r.user_answer,
                "is_correct": r.is_correct,
                "confidence": _normalize_confidence_value(r.confidence),
                "retried_at": r.retried_at.isoformat() if r.retried_at else None,
            }
            for r in previous[:5]
        ]
    }


# ========== POST /{id}/archive + /unarchive ==========

@router.post("/{wrong_id:int}/archive", response_model=WrongAnswerMutationResponse)
async def archive_wrong_answer(wrong_id: int, db: Session = Depends(get_db)):
    """归档错题"""
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")
    wa.mastery_status = "archived"
    wa.archived_at = datetime.now()
    wa.updated_at = datetime.now()
    db.commit()
    return {"success": True, "id": wrong_id, "status": "archived"}


@router.post("/{wrong_id:int}/unarchive", response_model=WrongAnswerMutationResponse)
async def unarchive_wrong_answer(wrong_id: int, db: Session = Depends(get_db)):
    """恢复错题"""
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")
    wa.mastery_status = "active"
    wa.archived_at = None
    wa.updated_at = datetime.now()
    db.commit()
    return {"success": True, "id": wrong_id, "status": "active"}


# ========== GET /retry-batch ==========

@router.get("/retry-batch/next", response_model=WrongAnswerRetryBatchResponse)
async def get_retry_batch(
    count: int = 5,
    severity: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """批量取题（不含答案），按 severity 排序"""
    from sqlalchemy import case
    query = db.query(WrongAnswerV2).filter(WrongAnswerV2.mastery_status == "active")

    if severity:
        query = query.filter(WrongAnswerV2.severity_tag == severity)

    severity_order = case(
        (WrongAnswerV2.severity_tag == "critical", 0),
        (WrongAnswerV2.severity_tag == "stubborn", 1),
        (WrongAnswerV2.severity_tag == "landmine", 2),
        else_=3
    )
    items = query.order_by(severity_order, desc(WrongAnswerV2.error_count)).limit(count).all()

    return {
        "count": len(items),
        "items": [
            {
                "id": wa.id,
                "question_text": wa.question_text,
                "options": wa.options or {},
                "question_type": wa.question_type,
                "difficulty": wa.difficulty,
                "severity_tag": wa.severity_tag,
                "error_count": wa.error_count,
                "key_point": wa.key_point,
                # 不含 correct_answer
            }
            for wa in items
        ]
    }


# ========== GET /export ==========

@router.get("/export", response_model=MarkdownExportResponse)
async def export_wrong_answers(
    status: str = "active",
    db: Session = Depends(get_db)
):
    """Markdown 导出"""
    query = db.query(WrongAnswerV2)
    if status == "active":
        query = query.filter(WrongAnswerV2.mastery_status == "active")
    elif status == "archived":
        query = query.filter(WrongAnswerV2.mastery_status == "archived")

    all_items = query.order_by(desc(WrongAnswerV2.error_count)).all()

    severity_labels = {
        "critical": "🚨 特级重灾区",
        "stubborn": "🚑 顽固病灶",
        "landmine": "⚠️ 隐形地雷",
        "normal": "📋 普通错题",
    }
    severity_order = ["critical", "stubborn", "landmine", "normal"]

    grouped = {}
    for wa in all_items:
        tag = wa.severity_tag or "normal"
        grouped.setdefault(tag, []).append(wa)

    lines = [
        f"# 错题本导出",
        f"",
        f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"总计：{len(all_items)} 道错题",
        f"",
    ]

    idx = 0
    for tag in severity_order:
        items = grouped.get(tag, [])
        if not items:
            continue
        label = severity_labels.get(tag, tag)
        lines.append(f"## {label} ({len(items)}道)")
        lines.append("")

        for wa in items:
            idx += 1
            qt = wa.question_type or "A1"
            diff = wa.difficulty or "基础"
            kp = wa.key_point or "未标注"
            lines.append(f"### {idx}. [{qt}][{diff}] {kp}")
            lines.append("")
            lines.append(f"**题目**: {wa.question_text}")
            lines.append("")

            if wa.options:
                for opt, val in wa.options.items():
                    marker = " ✅" if opt == wa.correct_answer else ""
                    lines.append(f"- {opt}. {val}{marker}")
                lines.append("")

            lines.append(f"**正确答案**: {wa.correct_answer}")
            lines.append(f"**错误次数**: {wa.error_count} | **遇到次数**: {wa.encounter_count} | **重做次数**: {wa.retry_count}")
            if wa.explanation:
                lines.append(f"**解析**: {wa.explanation}")
            lines.append("")
            lines.append("---")
            lines.append("")

    return {"content": "\n".join(lines), "format": "markdown", "total": len(all_items)}


# ========== GET /books ==========

@router.get("/books", response_model=BooksResponse)
async def get_available_books(db: Session = Depends(get_db)):
    """获取有错题的书籍列表"""
    chapter_ids = db.query(WrongAnswerV2.chapter_id).filter(
        WrongAnswerV2.chapter_id.isnot(None),
        WrongAnswerV2.mastery_status == "active"
    ).distinct().all()
    chapter_ids = [c[0] for c in chapter_ids]

    if not chapter_ids:
        return {"books": []}

    books = db.query(Chapter.book).filter(
        Chapter.id.in_(chapter_ids)
    ).distinct().all()

    return {"books": [b[0] for b in books]}


# ========== Variant Surgery Endpoints ==========

@router.post("/{wrong_id:int}/variant/generate", response_model=WrongAnswerVariantGenerateResponse)
async def generate_variant_question(wrong_id: int, db: Session = Depends(get_db)):
    """生成变式题（所有错题均可）"""
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    # 缓存策略：24h内复用
    cached_variant = canonicalize_variant_data(wa.variant_data)
    if cached_variant and cached_variant.get("generated_at"):
        from datetime import datetime as dt
        try:
            gen_time = dt.fromisoformat(cached_variant["generated_at"])
            if (datetime.now() - gen_time).total_seconds() < 86400:
                # 返回缓存
                return {
                    "variant_question": cached_variant["variant_question"],
                    "variant_options": cached_variant["variant_options"],
                    "variant_answer": cached_variant.get("variant_answer", ""),
                    "transform_type": cached_variant.get("transform_type", ""),
                    "core_knowledge": cached_variant.get("core_knowledge", ""),
                    "cached": True,
                }
        except (ValueError, KeyError):
            pass

    # 调用AI生成
    from services.variant_surgery_service import generate_variant
    try:
        variant = await generate_variant(wa)
        wa.variant_data = canonicalize_variant_data(variant, fallback_generated_at=datetime.now())
        wa.updated_at = datetime.now()
        db.commit()

        stored_variant = canonicalize_variant_data(wa.variant_data) or {}

        return {
            "variant_question": stored_variant["variant_question"],
            "variant_options": stored_variant["variant_options"],
            "variant_answer": stored_variant.get("variant_answer", ""),
            "transform_type": stored_variant.get("transform_type", ""),
            "core_knowledge": stored_variant.get("core_knowledge", ""),
            "cached": False,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"变式生成失败: {str(e)}")


@router.post("/{wrong_id:int}/variant/judge", response_model=WrongAnswerVariantJudgeResponse)
async def judge_variant_answer(
    wrong_id: int, body: VariantJudgeRequest, db: Session = Depends(get_db)
):
    """提交变式题答案 + 推理文本，获取AI判决 + SM-2更新"""
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")
    variant_data = canonicalize_variant_data(wa.variant_data) or {}
    if not variant_data:
        raise HTTPException(status_code=400, detail="尚未生成变式题")

    is_correct = _answers_match(body.user_answer, variant_data.get("variant_answer") or "")

    # AI评估推理
    from services.variant_surgery_service import evaluate_rationale
    ai_eval = canonicalize_ai_evaluation(
        await evaluate_rationale(wa, body.user_answer, body.rationale_text, is_correct)
    ) or {}

    # 创建重做记录
    confidence = _normalize_confidence_value(body.confidence)

    retry = WrongAnswerRetry(
        wrong_answer_id=wrong_id,
        user_answer=body.user_answer,
        is_correct=is_correct,
        confidence=confidence,
        time_spent_seconds=body.time_spent_seconds,
        retried_at=datetime.now(),
        is_variant=True,
        rationale_text=body.rationale_text,
        ai_evaluation=ai_eval,
    )
    db.add(retry)

    # 更新错题统计
    wa.retry_count += 1
    wa.last_retry_correct = is_correct
    wa.last_retry_confidence = confidence
    wa.last_retried_at = datetime.now()
    wa.updated_at = datetime.now()

    # 答错时增加 error_count
    if not is_correct:
        wa.error_count += 1

    verdict = ai_eval.get("verdict", "failed")

    if verdict == "lucky_guess":
        wa.severity_tag = "landmine"
    elif not is_correct and confidence == "sure" and wa.severity_tag != "critical":
        # 答错且自信 → critical
        wa.severity_tag = "critical"
    # 注意：不再根据 verdict 增加 error_count
    # error_count 应该只在答错时增加，而 verdict 是推理评估

    # SM-2 更新
    quality = quality_from_result(is_correct, confidence)
    sm2_update(wa, quality)
    auto_archived = wa.mastery_status == "archived"

    can_archive = (verdict == "logic_closed") and not auto_archived

    db.commit()

    return {
        "is_correct": is_correct,
        "variant_answer": variant_data.get("variant_answer"),
        "variant_explanation": variant_data.get("variant_explanation"),
        "verdict": verdict,
        "reasoning_score": ai_eval.get("reasoning_score", 0),
        "diagnosis": ai_eval.get("diagnosis", ""),
        "weak_links": ai_eval.get("weak_links", []),
        "can_archive": can_archive,
        "auto_archived": auto_archived,
        "severity_tag": wa.severity_tag,
        "error_count": wa.error_count,
        "retry_count": wa.retry_count,
        # SM-2 状态
        "sm2_ef": wa.sm2_ef,
        "sm2_interval": wa.sm2_interval,
        "sm2_repetitions": wa.sm2_repetitions,
        "next_review_date": wa.next_review_date.isoformat() if wa.next_review_date else None,
    }


@router.post("/{wrong_id:int}/variant/rescue-report", response_model=MarkdownExportResponse)
async def get_rescue_report(wrong_id: int, db: Session = Depends(get_db)):
    """生成深水区求助报告"""
    wa = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
    if not wa:
        raise HTTPException(status_code=404, detail="错题不存在")

    # 找最近一次变式重做记录
    retry = db.query(WrongAnswerRetry).filter(
        WrongAnswerRetry.wrong_answer_id == wrong_id,
        WrongAnswerRetry.is_variant == True
    ).order_by(desc(WrongAnswerRetry.retried_at)).first()

    if not retry:
        raise HTTPException(status_code=404, detail="无变式重做记录")

    from services.variant_surgery_service import build_rescue_report
    content = build_rescue_report(wa, retry)

    return {"content": content, "format": "markdown"}


@router.post("/recognize-chapters", response_model=RecognizeChaptersResponse)
async def recognize_chapters_for_wrong_answers(
    batch_size: int = Query(default=20, ge=1, le=100),
    process_all: bool = Query(default=False),
    db: Session = Depends(get_db)
):
    """
    批量为未分类/未关联错题识别章节

    Args:
        batch_size: 每批处理的数量（1-100）
        process_all: 是否循环处理直到没有可修复记录
    """
    def get_chapter(chapter_id: Optional[str]) -> Optional[Chapter]:
        cid = (chapter_id or "").strip()
        if not cid:
            return None
        return db.query(Chapter).filter(Chapter.id == cid).first()

    def is_placeholder_chapter(chapter: Optional[Chapter]) -> bool:
        if not chapter:
            return True
        title = str(chapter.chapter_title or "")
        return "自动补齐" in title or chapter.id == "0"

    def normalize_existing_chapter_id(chapter_id: Optional[str]) -> Optional[str]:
        cid = (chapter_id or "").strip()
        if not cid or cid in INVALID_CHAPTER_IDS:
            return None

        exact = get_chapter(cid)
        if exact and not is_placeholder_chapter(exact):
            return exact.id

        match = re.match(r"^(.+_ch)([0-9]+)$", cid)
        if not match:
            return None

        prefix, number = match.groups()
        number_int = int(number)
        for candidate in (
            f"{prefix}{number_int}",
            f"{prefix}{number_int:02d}",
            f"{prefix}{number}",
        ):
            chapter = get_chapter(candidate)
            if chapter and not is_placeholder_chapter(chapter):
                return chapter.id

        return None

    def build_candidate_query():
        return (
            db.query(WrongAnswerV2)
            .outerjoin(Chapter, WrongAnswerV2.chapter_id == Chapter.id)
            .filter(
                or_(
                    WrongAnswerV2.chapter_id.is_(None),
                    WrongAnswerV2.chapter_id == "",
                    WrongAnswerV2.chapter_id == "0",
                    WrongAnswerV2.chapter_id.like('%未分类%'),
                    WrongAnswerV2.chapter_id.in_(["unknown_ch0", "未知_ch0", "无法识别_ch0", "未分类_ch0"]),
                    Chapter.id.is_(None),
                )
            )
        )

    from services.ai_client import get_ai_client

    real_chapters = db.query(Chapter).filter(
        ~Chapter.chapter_title.like('%自动补齐%'),
        Chapter.id != '0',
        ~Chapter.id.like('%未分类%')
    ).all()
    valid_ids = {ch.id for ch in real_chapters}

    chapter_text_parts = []
    current_book = ""
    for chapter in sorted(real_chapters, key=lambda ch: (ch.book, ch.chapter_number)):
        if chapter.book != current_book:
            current_book = chapter.book
            chapter_text_parts.append(f"\n【{current_book}】")
        chapter_text_parts.append(f"  {chapter.id} → {chapter.chapter_title}")
    chapter_list_text = "\n".join(chapter_text_parts)

    ai = get_ai_client()
    total_processed = 0
    recognized_count = 0
    failed_count = 0
    normalized_count = 0
    loop_count = 0
    max_loops = 50 if process_all else 1

    while loop_count < max_loops:
        candidates = (
            build_candidate_query()
            .order_by(WrongAnswerV2.id.asc())
            .limit(batch_size)
            .all()
        )

        if not candidates:
            break

        loop_count += 1
        batch_updated = 0

        for wrong in candidates:
            total_processed += 1

            normalized_current = normalize_existing_chapter_id(wrong.chapter_id)
            if normalized_current and normalized_current != wrong.chapter_id:
                wrong.chapter_id = normalized_current
                recognized_count += 1
                normalized_count += 1
                batch_updated += 1
                continue

            content = f"{wrong.key_point or ''}\n\n{wrong.question_text[:500]}"

            try:
                result = await ai.generate_json(
                    f"""从以下章节列表中，选择与题目最匹配的一个章节ID。

考点：{wrong.key_point or '(无)'}
题目：{(wrong.question_text or '')[:300]}

章节列表：
{chapter_list_text}

只返回JSON：{{"chapter_id": "xxx"}}
chapter_id必须是列表中的值。""",
                    {"chapter_id": "string"},
                    max_tokens=100,
                    temperature=0.1,
                    use_heavy=False,
                    timeout=30,
                )
                target_chapter_id = str(result.get("chapter_id") or "").strip()

                if target_chapter_id in valid_ids and target_chapter_id != wrong.chapter_id:
                    wrong.chapter_id = target_chapter_id
                    recognized_count += 1
                    batch_updated += 1
                else:
                    failed_count += 1
            except Exception as e:
                print(f"[RecognizeChapters] 错题ID {wrong.id} 识别失败: {e}")
                failed_count += 1

        db.commit()

        if not process_all or batch_updated == 0:
            break

    remaining = build_candidate_query().count()

    if total_processed == 0:
        message = "没有需要识别的错题"
    elif remaining == 0:
        message = f"识别完成：成功 {recognized_count} 题，失败 {failed_count} 题"
    elif process_all:
        message = (
            f"本轮处理完成：成功 {recognized_count} 题，失败 {failed_count} 题，"
            f"仍剩余 {remaining} 题待处理"
        )
    else:
        message = (
            f"本批处理完成：成功 {recognized_count} 题，失败 {failed_count} 题，"
            f"仍剩余 {remaining} 题待处理"
        )

    return {
        "success": True,
        "message": message,
        "total": total_processed,
        "recognized": recognized_count,
        "failed": failed_count,
        "normalized": normalized_count,
        "remaining": remaining,
        "process_all": process_all,
    }
