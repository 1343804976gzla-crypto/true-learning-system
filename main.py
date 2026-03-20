"""
True Learning System - 主应用
AI驱动的学习系统：上传→识别→测试→批改
"""

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime, timedelta
import os
import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Optional


# 自定义 JSONResponse 类，确保 UTF-8 编码
class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

# 导入模型
from models import (
    init_db, get_db, DailyUpload, Chapter, ConceptMastery,
    TestRecord, FeynmanSession, ConceptLink, Variation, DATABASE_URL
)
from schemas import (
    ContentUpload, UploadResponse, ChapterInfo, ChapterDetail,
    ConceptInfo, DashboardResponse, DashboardStats, DailyTask
)
from services.data_identity import (
    ensure_learning_identity_schema,
    reset_request_identity,
    resolve_request_identity,
    set_request_identity,
)
from services.llm_audit import (
    new_llm_audit_id,
    reset_llm_audit_request_context,
    set_llm_audit_request_context,
)
from services.openmanus_bridge import sync_openmanus_config
from services.openviking_sync import install_openviking_sync_hooks
from knowledge_upload_models import create_knowledge_upload_tables
from routers import api_router
from utils.chapter_catalog import clean_batch_chapter_rows
from routers.quiz import router as quiz_router
from routers.feynman import router as feynman_router
from routers.quiz_concurrent import router as concurrent_quiz_router
from routers.quiz_fast import router as fast_quiz_router

# 初始化FastAPI
app = FastAPI(
    title="True Learning System",
    description="AI驱动的学习系统 - 上传、识别、测试、批改",
    version="1.0.0",
    default_response_class=UTF8JSONResponse
)

# 注册API路由
app.include_router(api_router)
app.include_router(quiz_router)
app.include_router(feynman_router)
app.include_router(concurrent_quiz_router)
app.include_router(fast_quiz_router)

# 注册批量测验路由（整卷模式，支持题目数量选择）
from routers.quiz_batch import router as batch_quiz_router
app.include_router(batch_quiz_router)

# 注册学习轨迹记录路由
from routers.learning_tracking import router as tracking_router
app.include_router(tracking_router)

# 注册变式题生成路由
from routers.quiz_variations import router as variations_router
app.include_router(variations_router)

# 注册错题本V2路由
from routers.wrong_answers_v2 import router as wrong_answers_v2_router
app.include_router(wrong_answers_v2_router)

# 注册错题靶向闯关路由
from routers.challenge import router as challenge_router
app.include_router(challenge_router)

# 注册融合升级路由
from routers.fusion import router as fusion_router
app.include_router(fusion_router)

# 注册数据看板路由
from routers.dashboard import router as dashboard_router
app.include_router(dashboard_router)

# 注册 LLM 数据合同路由
from routers.llm import router as llm_router
app.include_router(llm_router)
from routers.agent import router as agent_router
app.include_router(agent_router)

# 模板和静态文件
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

_STARTUP_ONCE_LOCK = Lock()
_STARTUP_ONCE_COMPLETE = False

# ============================================
# 初始化
# ============================================

@app.on_event("startup")
async def startup():
    """应用启动时初始化数据库"""
    global _STARTUP_ONCE_COMPLETE
    if _STARTUP_ONCE_COMPLETE:
        return

    with _STARTUP_ONCE_LOCK:
        if _STARTUP_ONCE_COMPLETE:
            return

        init_db()
        create_knowledge_upload_tables()
        ensure_learning_identity_schema()
        try:
            from models import SessionLocal
            from routers.learning_tracking import rebuild_daily_logs

            with SessionLocal() as db:
                rebuild_daily_logs(db)
        except Exception as exc:
            print(f"[WARN] rebuild daily logs failed on startup: {exc}")
        install_openviking_sync_hooks()
        try:
            openmanus_status = sync_openmanus_config()
            if openmanus_status.get("available"):
                print(
                    "[INFO] OpenManus bridge ready: "
                    f"synced={openmanus_status.get('synced')} model={openmanus_status.get('model')}"
                )
        except Exception as exc:
            print(f"[WARN] OpenManus bridge sync failed: {exc}")
        _warn_if_split_db()
        _STARTUP_ONCE_COMPLETE = True
    print("🚀 True Learning System 启动完成")


@app.middleware("http")
async def bind_request_identity(request: Request, call_next):
    user_id, device_id = resolve_request_identity(request)
    identity_tokens = set_request_identity(user_id=user_id, device_id=device_id)
    request_id = request.headers.get("X-Request-ID") or new_llm_audit_id("req")
    audit_token = set_llm_audit_request_context(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        query=request.url.query,
        referer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
        user_id=user_id,
        device_id=device_id,
    )
    try:
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request_id)
        return response
    finally:
        reset_llm_audit_request_context(audit_token)
        reset_request_identity(identity_tokens)


def _warn_if_split_db() -> None:
    """
    启动时检测是否存在并行 SQLite 库（常见为 data/learning.db 与项目根 learning.db）。
    若发现项目根目录残留旧库，则自动改名隔离，避免继续读写分叉。
    """
    try:
        if not DATABASE_URL.startswith("sqlite:///"):
            return

        active_db = Path(DATABASE_URL.replace("sqlite:///", "", 1)).resolve()
        root_db = (Path(__file__).resolve().parent / "learning.db").resolve()
        if not root_db.exists() or root_db == active_db:
            return

        def _counts(db_path: Path) -> dict:
            out = {}
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            for t in ("chapters", "concept_mastery", "quiz_sessions", "wrong_answers_v2"):
                try:
                    out[t] = int(cur.execute(f"select count(*) from {t}").fetchone()[0])
                except Exception:
                    out[t] = -1
            conn.close()
            return out

        active_counts = _counts(active_db)
        root_counts = _counts(root_db)
        diverged = any(active_counts.get(k) != root_counts.get(k) for k in active_counts.keys())

        detached_name = f"learning.db.legacy.detached.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        detached_path = root_db.with_name(detached_name)

        root_db.rename(detached_path)

        if diverged:
            print("[WARN] 检测到项目根目录残留旧数据库，且它与当前 active DB 已分叉；已自动隔离：")
        else:
            print("[INFO] 检测到项目根目录残留旧数据库副本；已自动隔离：")
        print(f"       active={active_db} counts={active_counts}")
        print(f"       legacy={detached_path} counts={root_counts}")
        print("       当前唯一生效数据库仍为 DATABASE_PATH 指向的文件。")
    except Exception as e:
        print(f"[WARN] 启动数据库分叉检测/隔离失败: {e}")


# ============================================
# 页面路由
# ============================================

@app.get("/index", response_class=RedirectResponse)
@app.get("/home", response_class=RedirectResponse)
async def index_redirect():
    """首页重定向"""
    return "/"


def _format_dashboard_accuracy(raw_accuracy: Optional[float]) -> Optional[float]:
    if raw_accuracy is None:
        return None
    try:
        numeric = float(raw_accuracy)
    except (TypeError, ValueError):
        return None
    if numeric <= 1:
        numeric *= 100
    return round(numeric, 1)


def _format_dashboard_day(value: Optional[date]) -> str:
    if not value:
        return "暂无记录"
    normalized = value.date() if isinstance(value, datetime) else value
    delta_days = (date.today() - normalized).days
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "昨天"
    if 1 < delta_days < 7:
        return f"{delta_days} 天前"
    return normalized.strftime("%m-%d")


def _compute_consecutive_study_days(study_dates) -> int:
    normalized_dates = {item for item in study_dates if item}
    streak = 0
    cursor = date.today()
    while cursor in normalized_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _truncate_dashboard_text(value: Optional[str], max_length: int = 24) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length].rstrip()}..."


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """仪表盘 - 学习总览"""
    from learning_tracking_models import LearningSession, WrongAnswerV2
    from routers.learning_tracking import _load_unique_question_records, _build_record_stats
    from sqlalchemy import desc as sa_desc

    recent_sessions = db.query(LearningSession).order_by(
        sa_desc(LearningSession.started_at)
    ).limit(5).all()
    all_sessions = db.query(LearningSession).all()

    total_sessions = len(all_sessions)
    total_duration = sum(s.duration_seconds or 0 for s in all_sessions)
    exam_sessions = [s for s in all_sessions if s.session_type == "exam"]
    latest_exam_session = next((s for s in recent_sessions if s.session_type == "exam"), None)

    all_records = _load_unique_question_records(db)
    record_stats = _build_record_stats(all_records)
    total_questions = int(record_stats["total_questions"])
    total_correct = int(record_stats["correct_count"])
    avg_accuracy = round(total_correct / total_questions * 100, 1) if total_questions > 0 else 0

    kp_stats = {}
    for qr in all_records:
        key_point = (qr.key_point or "").strip() or "未分类"
        if key_point not in kp_stats:
            kp_stats[key_point] = {"total": 0, "correct": 0}
        kp_stats[key_point]["total"] += 1
        if qr.is_correct:
            kp_stats[key_point]["correct"] += 1

    weak_candidates = []
    mastered_kps = 0
    for kp_name, kp_data in kp_stats.items():
        total = int(kp_data["total"])
        correct = int(kp_data["correct"])
        accuracy = (correct / total * 100) if total else 0
        if total >= 3 and accuracy < 60:
            weak_candidates.append((accuracy, -total, kp_name))
        if total >= 3 and accuracy >= 80:
            mastered_kps += 1
    weak_candidates.sort()
    weak_count = len(weak_candidates)
    weakest_kp_name = weak_candidates[0][2] if weak_candidates else None
    weakest_kp_accuracy = round(weak_candidates[0][0], 1) if weak_candidates else None
    weakest_kp_short = _truncate_dashboard_text(weakest_kp_name, 16)

    total_uploads = db.query(DailyUpload).count()
    weekly_uploads = db.query(DailyUpload).filter(
        DailyUpload.date >= date.today() - timedelta(days=6)
    ).count()
    latest_upload = db.query(DailyUpload).order_by(
        sa_desc(DailyUpload.created_at),
        sa_desc(DailyUpload.id)
    ).first()
    latest_upload_ai = latest_upload.ai_extracted if latest_upload and isinstance(latest_upload.ai_extracted, dict) else {}
    latest_upload_book = str(latest_upload_ai.get("book") or "").strip()
    latest_upload_title = str(
        latest_upload_ai.get("chapter_title")
        or latest_upload_ai.get("main_topic")
        or ""
    ).strip()
    if latest_upload:
        upload_summary_parts = [part for part in [latest_upload_book, latest_upload_title] if part]
        latest_upload_summary = " · ".join(upload_summary_parts) if upload_summary_parts else _format_dashboard_day(latest_upload.date)
    else:
        latest_upload_summary = "还没有上传记录"
    latest_upload_summary_short = _truncate_dashboard_text(latest_upload_summary, 20)
    latest_upload_title_short = _truncate_dashboard_text(latest_upload_title or latest_upload_book or "新资料", 14)
    latest_exam_title_short = _truncate_dashboard_text(
        latest_exam_session.title if latest_exam_session and latest_exam_session.title else "",
        20
    )

    upload_dates = [row[0] for row in db.query(DailyUpload.date).distinct().all()]
    upload_streak_days = _compute_consecutive_study_days(upload_dates)
    book_count = int(db.query(func.count(func.distinct(Chapter.book))).scalar() or 0)

    active_wrong_count = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active"
    ).count()
    archived_wrong_count = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "archived"
    ).count()
    due_wrong_today = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active",
        WrongAnswerV2.next_review_date.isnot(None),
        WrongAnswerV2.next_review_date <= date.today()
    ).count()
    critical_wrong_count = db.query(WrongAnswerV2).filter(
        WrongAnswerV2.mastery_status == "active",
        WrongAnswerV2.severity_tag == "critical"
    ).count()

    for session in recent_sessions:
        session.dashboard_accuracy_pct = _format_dashboard_accuracy(session.accuracy)
        session.dashboard_answer_total = max(
            int((session.correct_count or 0) + (session.wrong_count or 0)),
            int(session.answered_questions or 0)
        )

    launchpad = {
        "exam": {
            "metric_1_label": "累计整卷",
            "metric_1_value": len(exam_sessions),
            "metric_2_label": "最近测验",
            "metric_2_value": _format_dashboard_day(latest_exam_session.started_at) if latest_exam_session else "未开始",
            "note": latest_exam_title_short if latest_exam_title_short else "从一段资料开始生成整套试卷",
        },
        "wrong": {
            "metric_1_label": "活跃错题",
            "metric_1_value": active_wrong_count,
            "metric_2_label": "今日到期",
            "metric_2_value": due_wrong_today,
            "note": f"{critical_wrong_count} 道高危题待优先处理" if critical_wrong_count else (
                f"已归档 {archived_wrong_count} 道，继续压缩积压" if archived_wrong_count else "先完成一轮重做，建立修复闭环"
            ),
        },
        "upload": {
            "metric_1_label": "累计上传",
            "metric_1_value": total_uploads,
            "metric_2_label": "本周新增",
            "metric_2_value": weekly_uploads,
            "note": f"最近资料：{latest_upload_summary_short}" if latest_upload else "先把讲义、课堂记录或笔记喂给系统",
        },
        "tracking": {
            "metric_1_label": "累计做题",
            "metric_1_value": total_questions,
            "metric_2_label": "平均正确率",
            "metric_2_value": f"{avg_accuracy}%",
            "note": (
                f"当前最薄弱点：{weakest_kp_short} · {weakest_kp_accuracy}%"
                if weakest_kp_short and weakest_kp_accuracy is not None
                else "继续做题后会自动定位最薄弱知识点"
            ),
        },
        "archive": {
            "metric_1_label": "知识点",
            "metric_1_value": len(kp_stats),
            "metric_2_label": "待修补",
            "metric_2_value": weak_count,
            "note": (
                f"已稳定 {mastered_kps} 个知识点，优先补齐 {weakest_kp_short}"
                if weakest_kp_short
                else "归档会随着做题记录自动累积"
            ),
        },
        "history": {
            "metric_1_label": "累计记录",
            "metric_1_value": total_uploads,
            "metric_2_label": "连续学习",
            "metric_2_value": f"{upload_streak_days} 天",
            "note": (
                f"最近上传：{_format_dashboard_day(latest_upload.date)} · {latest_upload_title_short}"
                if latest_upload
                else "上传、练习和复盘记录都会沉淀到这里"
            ),
        },
    }

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_sessions": recent_sessions,
        "launchpad": launchpad,
        "stats": {
            "total_sessions": total_sessions,
            "total_questions": total_questions,
            "avg_accuracy": avg_accuracy,
            "total_duration": total_duration,
            "total_kps": len(kp_stats),
            "weak_kps": weak_count,
            "total_uploads": total_uploads,
            "weekly_uploads": weekly_uploads,
            "book_count": book_count,
            "upload_streak_days": upload_streak_days,
        }
    })


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """上传页面"""
    return templates.TemplateResponse("upload.html", {
        "request": request
    })


@app.get("/chapter/{chapter_id}", response_class=HTMLResponse)
async def chapter_page(request: Request, chapter_id: str, db: Session = Depends(get_db)):
    """章节详情页"""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    concepts = db.query(ConceptMastery).filter(
        ConceptMastery.chapter_id == chapter_id
    ).all()
    
    return templates.TemplateResponse("chapter.html", {
        "request": request,
        "chapter": chapter,
        "concepts": concepts,
        "today": date.today()
    })


@app.get("/quiz/batch/{chapter_id}", response_class=HTMLResponse)
async def batch_quiz_page(request: Request, chapter_id: str, db: Session = Depends(get_db)):
    """批量10题测验页面 - 整卷测试"""
    chapter_title = "整卷测验"
    if chapter_id != "0":
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        if not chapter:
            raise HTTPException(status_code=404, detail="章节不存在")
        chapter_title = f"{chapter.book} - {chapter.chapter_title}"

    return templates.TemplateResponse("quiz_batch.html", {
        "request": request,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title
    })


@app.get("/exam", response_class=RedirectResponse)
async def exam_page():
    """兼容旧入口，统一跳转到新版整卷页面（含章节检测确认流程）。"""
    return RedirectResponse(url="/quiz/batch/0", status_code=307)


@app.get("/quiz/detail", response_class=HTMLResponse)
async def quiz_detail_page(request: Request):
    """细节练习页面 - 继承整卷测试的内容"""
    return templates.TemplateResponse("quiz_detail.html", {
        "request": request
    })


@app.get("/learning-tracking", response_class=HTMLResponse)
async def learning_tracking_page(request: Request):
    """学习轨迹页面 - 查看历史学习记录"""
    return templates.TemplateResponse("learning_tracking.html", {
        "request": request
    })


@app.get("/progress-board", response_class=HTMLResponse)
async def progress_board_page(request: Request):
    """进度看板页面 - 聚合学习进度可视化"""
    return templates.TemplateResponse("progress_board.html", {
        "request": request
    })


@app.get("/session-review", response_class=HTMLResponse)
async def session_review_page(request: Request):
    """沉浸式复盘页面 - 单次整卷详情"""
    return templates.TemplateResponse("session_review.html", {
        "request": request
    })


@app.get("/knowledge-archive", response_class=HTMLResponse)
async def knowledge_archive_page(request: Request):
    """全局知识点归档看板"""
    return templates.TemplateResponse("knowledge_archive.html", {
        "request": request
    })


@app.get("/quiz/{concept_id}", response_class=HTMLResponse)
async def quiz_page(request: Request, concept_id: str, db: Session = Depends(get_db)):
    """测试页面"""
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == concept_id
    ).first()
    if not concept:
        raise HTTPException(status_code=404, detail="知识点不存在")

    return templates.TemplateResponse("quiz.html", {
        "request": request,
        "concept": concept
    })


@app.get("/feynman/{concept_id}", response_class=HTMLResponse)
async def feynman_page(request: Request, concept_id: str, db: Session = Depends(get_db)):
    """费曼讲解页面"""
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == concept_id
    ).first()
    if not concept:
        raise HTTPException(status_code=404, detail="知识点不存在")
    
    return templates.TemplateResponse("feynman.html", {
        "request": request,
        "concept": concept
    })


@app.get("/test", response_class=HTMLResponse)
async def test_page(request: Request):
    """测试页面 - 用于诊断白屏问题"""
    return templates.TemplateResponse("test.html", {
        "request": request,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


@app.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request, db: Session = Depends(get_db)):
    """知识图谱页面"""
    # 获取所有章节
    books = db.query(Chapter.book).distinct().all()
    books = [b[0] for b in books]
    
    return templates.TemplateResponse("graph.html", {
        "request": request,
        "books": books
    })


# ============================================
# API路由 (基础)
# ============================================

@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    """获取系统统计信息"""
    from sqlalchemy import func
    
    total_concepts = db.query(ConceptMastery).count()
    mastered = db.query(ConceptMastery).filter(ConceptMastery.retention >= 0.8).count()
    weak = db.query(ConceptMastery).filter(ConceptMastery.retention < 0.5).count()
    total_chapters = db.query(Chapter).count()
    
    # 今日待复习
    today = date.today()
    to_review = db.query(ConceptMastery).filter(
        ConceptMastery.next_review <= today
    ).count()
    
    return {
        "total_concepts": total_concepts,
        "mastered": mastered,
        "weak": weak,
        "total_chapters": total_chapters,
        "to_review": to_review,
        "mastery_rate": round(mastered / total_concepts * 100, 1) if total_concepts > 0 else 0
    }


@app.get("/api/chapters")
async def list_chapters(
    book: Optional[str] = None,
    include_empty: bool = False,
    db: Session = Depends(get_db)
):
    """列出所有章节"""
    query = db.query(Chapter)
    if book:
        query = query.filter(Chapter.book == book)
    chapters = query.order_by(Chapter.id).all()

    cm_counts = dict(
        db.query(ConceptMastery.chapter_id, func.count(ConceptMastery.concept_id))
        .group_by(ConceptMastery.chapter_id)
        .all()
    )

    items = []
    for c in chapters:
        chapter_concepts = len(c.concepts) if isinstance(c.concepts, list) else 0
        mastery_concepts = int(cm_counts.get(c.id, 0))
        concept_count = max(chapter_concepts, mastery_concepts)

        if not include_empty and concept_count == 0:
            continue

        items.append({
            "id": c.id,
            "book": c.book,
            "chapter_number": c.chapter_number,
            "chapter_title": c.chapter_title,
            "concept_count": concept_count,
            "last_reviewed": c.last_reviewed
        })

    return items


@app.get("/api/chapters/grouped")
async def list_chapters_grouped(db: Session = Depends(get_db)):
    """按学科分组的章节列表（供大纲确认弹窗用）"""
    chapter_rows = clean_batch_chapter_rows(
        {
            "id": chapter.id,
            "book": chapter.book,
            "chapter_number": chapter.chapter_number,
            "chapter_title": chapter.chapter_title,
        }
        for chapter in db.query(Chapter).all()
    )
    grouped = {}

    for row in chapter_rows:
        if row["book"] not in grouped:
            grouped[row["book"]] = []
        grouped[row["book"]].append({
            "id": row["id"],
            "number": row["chapter_number"],
            "title": row["chapter_title"],
        })
    return grouped


@app.get("/api/chapter/{chapter_id}")
async def get_chapter_detail(chapter_id: str, db: Session = Depends(get_db)):
    """获取章节详情"""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    concepts = db.query(ConceptMastery).filter(
        ConceptMastery.chapter_id == chapter_id
    ).all()
    
    return {
        "chapter": {
            "id": chapter.id,
            "book": chapter.book,
            "chapter_number": chapter.chapter_number,
            "chapter_title": chapter.chapter_title,
            "content_summary": chapter.content_summary,
            "first_uploaded": chapter.first_uploaded,
            "last_reviewed": chapter.last_reviewed
        },
        "concepts": [{
            "concept_id": c.concept_id,
            "name": c.name,
            "retention": c.retention,
            "understanding": c.understanding,
            "application": c.application,
            "next_review": c.next_review
        } for c in concepts]
    }


@app.get("/api/concept/{concept_id}")
async def get_concept_detail(concept_id: str, db: Session = Depends(get_db)):
    """获取知识点详情"""
    concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == concept_id
    ).first()
    if not concept:
        raise HTTPException(status_code=404, detail="知识点不存在")
    
    # 获取测试历史
    test_history = db.query(TestRecord).filter(
        TestRecord.concept_id == concept_id
    ).order_by(TestRecord.tested_at.desc()).limit(10).all()
    
    return {
        "concept_id": concept.concept_id,
        "name": concept.name,
        "retention": concept.retention,
        "understanding": concept.understanding,
        "application": concept.application,
        "last_tested": concept.last_tested,
        "next_review": concept.next_review,
        "test_history": [{
            "id": t.id,
            "test_type": t.test_type,
            "is_correct": t.is_correct,
            "score": t.score,
            "tested_at": t.tested_at
        } for t in test_history]
    }


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """学习历史页面"""
    return templates.TemplateResponse("history.html", {
        "request": request
    })


@app.get("/agent", response_class=HTMLResponse)
async def agent_page(request: Request):
    """Agent 对话页面"""
    return templates.TemplateResponse("agent.html", {
        "request": request
    })


@app.get("/wrong-answers", response_class=HTMLResponse)
async def wrong_answers_page(request: Request):
    """错题本页面"""
    return templates.TemplateResponse("wrong_answers.html", {
        "request": request
    })


@app.get("/dashboard/stats", response_class=HTMLResponse)
async def dashboard_stats_page(request: Request):
    """兼容旧入口：统一跳转到已嵌入错题本的新数据看板。"""
    return RedirectResponse(url="/wrong-answers", status_code=307)


@app.get("/quiz/practice/{chapter_id}", response_class=HTMLResponse)
async def quiz_practice_page(
    request: Request,
    chapter_id: str,
    mode: str = "practice"
):
    """10题练习页面"""
    from sqlalchemy.orm import Session
    from models import get_db, Chapter
    
    db = next(get_db())
    try:
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        chapter_title = chapter.chapter_title if chapter else "未知章节"
    finally:
        db.close()
    
    return templates.TemplateResponse("quiz_practice.html", {
        "request": request,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "mode": mode
    })


@app.get("/quiz/fast/{chapter_id}", response_class=HTMLResponse)
async def quiz_fast_page(
    request: Request,
    chapter_id: str
):
    """极速10题练习页面（并发版）"""
    from sqlalchemy.orm import Session
    from models import get_db, Chapter
    
    db = next(get_db())
    try:
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        chapter_title = chapter.chapter_title if chapter else "未知章节"
    finally:
        db.close()
    
    return templates.TemplateResponse("quiz_fast.html", {
        "request": request,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title
    })


@app.get("/quiz/super/{chapter_id}", response_class=HTMLResponse)
async def quiz_super_page(
    request: Request,
    chapter_id: str
):
    """极速预生成练习页面（预生成+本地批改+AI分析）"""
    from sqlalchemy.orm import Session
    from models import get_db, Chapter
    
    db = next(get_db())
    try:
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        chapter_title = chapter.chapter_title if chapter else "未知章节"
    finally:
        db.close()
    
    return templates.TemplateResponse("quiz_super.html", {
        "request": request,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title
    })


# ============================================
# 错误处理
# ============================================

from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """自定义HTTP错误处理"""
    if request.headers.get("accept") and "application/json" in request.headers.get("accept"):
        # API请求返回JSON
        return await http_exception_handler(request, exc)
    
    # 页面请求返回HTML
    return templates.TemplateResponse("error.html", {
        "request": request,
        "code": exc.status_code,
        "message": exc.detail
    }, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数验证错误处理"""
    if request.headers.get("accept") and "application/json" in request.headers.get("accept"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=422,
            content={"detail": "参数验证错误", "errors": exc.errors()}
        )
    
    return templates.TemplateResponse("error.html", {
        "request": request,
        "code": 422,
        "message": "输入参数有误，请检查表单"
    }, status_code=422)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """通用错误处理"""
    import traceback
    print(f"错误: {exc}")
    print(traceback.format_exc())
    
    if request.headers.get("accept") and "application/json" in request.headers.get("accept"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误"}
        )
    
    return templates.TemplateResponse("error.html", {
        "request": request,
        "code": 500,
        "message": "服务器内部错误"
    }, status_code=500)


# ============================================
# 健康检查
# ============================================

@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ============================================
# 运行应用
# ============================================

if __name__ == "__main__":
    import uvicorn
    reload_enabled = os.getenv("TLS_RELOAD", "").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=reload_enabled)
