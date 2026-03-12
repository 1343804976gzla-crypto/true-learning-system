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
from routers import api_router
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

# 模板和静态文件
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ============================================
# 初始化
# ============================================

@app.on_event("startup")
async def startup():
    """应用启动时初始化数据库"""
    init_db()
    _warn_if_split_db()
    print("🚀 True Learning System 启动完成")


def _warn_if_split_db() -> None:
    """
    启动时检测是否存在并行 SQLite 库（常见为 data/learning.db 与项目根 learning.db）。
    仅输出告警，不阻塞启动。
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
        if any(active_counts.get(k) != root_counts.get(k) for k in active_counts.keys()):
            print("[WARN] 检测到双数据库且数据不一致：")
            print(f"       active={active_db} counts={active_counts}")
            print(f"       other ={root_db} counts={root_counts}")
            print("       建议仅保留 DATABASE_PATH 指向的单一库，避免读写分叉。")
    except Exception as e:
        print(f"[WARN] 启动数据库分叉检测失败: {e}")


# ============================================
# 页面路由
# ============================================

@app.get("/index", response_class=RedirectResponse)
@app.get("/home", response_class=RedirectResponse)
async def index_redirect():
    """首页重定向"""
    return "/"


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """仪表盘 - 学习总览"""
    from learning_tracking_models import LearningSession, QuestionRecord
    from sqlalchemy import desc as sa_desc

    # 最近5次学习会话
    recent_sessions = db.query(LearningSession).order_by(
        sa_desc(LearningSession.started_at)
    ).limit(5).all()

    # 总体统计
    all_sessions = db.query(LearningSession).all()
    total_sessions = len(all_sessions)
    total_questions = sum((s.correct_count or 0) + (s.wrong_count or 0) for s in all_sessions)
    total_correct = sum(s.correct_count or 0 for s in all_sessions)
    total_duration = sum(s.duration_seconds or 0 for s in all_sessions)
    avg_accuracy = round(total_correct / total_questions * 100, 1) if total_questions > 0 else 0

    # 知识点统计
    all_qr = db.query(QuestionRecord).all()
    kp_stats = {}
    for qr in all_qr:
        kp = qr.key_point or "未分类"
        if kp not in kp_stats:
            kp_stats[kp] = {"total": 0, "correct": 0}
        kp_stats[kp]["total"] += 1
        if qr.is_correct:
            kp_stats[kp]["correct"] += 1
    weak_count = sum(1 for st in kp_stats.values() if st["total"] >= 3 and st["correct"] / st["total"] < 0.6)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_sessions": recent_sessions,
        "stats": {
            "total_sessions": total_sessions,
            "total_questions": total_questions,
            "avg_accuracy": avg_accuracy,
            "total_duration": total_duration,
            "total_kps": len(kp_stats),
            "weak_kps": weak_count,
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
    chapters = db.query(Chapter).order_by(Chapter.book, Chapter.chapter_number).all()
    grouped = {}

    def _is_selectable_chapter(chapter: Chapter) -> bool:
        chapter_id = (chapter.id or "").strip()
        chapter_title = (chapter.chapter_title or "").strip()
        chapter_number = (chapter.chapter_number or "").strip()
        book = (chapter.book or "").strip()
        return not (
            chapter_id in {"", "0", "unknown_ch0", "未知_ch0", "无法识别_ch0", "未分类_ch0", "uncategorized_ch0"}
            or chapter_id.endswith("_ch0")
            or chapter_number == "0"
            or book in {"未分类", "unknown"}
            or chapter_title.startswith("自动补齐章节")
            or chapter_title in {"待人工归类", "未知章节"}
        )

    for c in chapters:
        if not _is_selectable_chapter(c):
            continue
        if c.book not in grouped:
            grouped[c.book] = []
        grouped[c.book].append({
            "id": c.id,
            "number": c.chapter_number,
            "title": c.chapter_title,
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


@app.get("/wrong-answers", response_class=HTMLResponse)
async def wrong_answers_page(request: Request):
    """错题本页面"""
    return templates.TemplateResponse("wrong_answers.html", {
        "request": request
    })


@app.get("/dashboard/stats", response_class=HTMLResponse)
async def dashboard_stats_page(request: Request):
    """数据看板页面 - 错题消耗进度与预期清仓时间"""
    return templates.TemplateResponse("dashboard_stats.html", {
        "request": request
    })


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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
