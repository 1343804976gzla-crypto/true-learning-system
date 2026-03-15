from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from learning_tracking_models import (
    LearningActivity,
    LearningSession,
    QuestionRecord,
    WrongAnswerRetry,
    WrongAnswerV2,
)
from models import Chapter, ConceptMastery, DailyUpload, QuizSession, TestRecord, get_db
from routers.learning_tracking import get_progress_board
from routers.wrong_answers_v2 import get_wrong_answer_dashboard
from utils.data_contracts import (
    JSON_COLUMN_REGISTRY,
    SCHEMA_VERSION,
    ContractAuditSummary,
    DatasetSummary,
    EnumCatalog,
    JsonColumnDefinition,
    LLMContextBundle,
    RouteCoverage,
    SessionSnapshot,
    WrongAnswerSnapshot,
    build_analytics_snapshot,
    build_dataset_summary,
    build_session_snapshot,
    build_wrong_answer_snapshot,
    group_question_records,
    load_latest_question_records,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])


def _build_dataset_summary(db: Session) -> DatasetSummary:
    return build_dataset_summary(
        daily_uploads=db.query(DailyUpload).count(),
        chapters=db.query(Chapter).count(),
        concept_mastery=db.query(ConceptMastery).count(),
        test_records=db.query(TestRecord).count(),
        quiz_sessions=db.query(QuizSession).count(),
        learning_sessions=db.query(LearningSession).count(),
        question_records=db.query(QuestionRecord).count(),
        wrong_answers_v2=db.query(WrongAnswerV2).count(),
        wrong_answer_retries=db.query(WrongAnswerRetry).count(),
    )


def _build_chapter_map(db: Session, chapter_ids: List[str]) -> Dict[str, Chapter]:
    ids = [chapter_id for chapter_id in chapter_ids if chapter_id]
    if not ids:
        return {}
    rows = db.query(Chapter).filter(Chapter.id.in_(ids)).all()
    return {row.id: row for row in rows}


def _build_latest_retry_map(db: Session, wrong_answer_ids: List[int]) -> Dict[int, WrongAnswerRetry]:
    if not wrong_answer_ids:
        return {}

    retries = (
        db.query(WrongAnswerRetry)
        .filter(WrongAnswerRetry.wrong_answer_id.in_(wrong_answer_ids))
        .order_by(desc(WrongAnswerRetry.retried_at), desc(WrongAnswerRetry.id))
        .all()
    )

    latest_map: Dict[int, WrongAnswerRetry] = {}
    for retry in retries:
        latest_map.setdefault(retry.wrong_answer_id, retry)
    return latest_map


def _collect_route_coverage() -> tuple[int, int, int, List[RouteCoverage]]:
    root = Path(__file__).resolve().parent
    pattern = re.compile(r'@router\.(get|post|put|delete|patch)\("([^"]+)"(?:,\s*response_model\s*=\s*([^\)]+))?')

    total_routes = 0
    typed_routes = 0
    coverage: List[RouteCoverage] = []

    for path in sorted(root.glob("*.py")):
        if path.name == "__init__.py":
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        matches = list(pattern.finditer(text))
        router_total = len(matches)
        router_typed = sum(1 for match in matches if (match.group(3) or "").strip())

        total_routes += router_total
        typed_routes += router_typed
        coverage.append(
            RouteCoverage(
                router_file=path.name,
                total_routes=router_total,
                typed_routes=router_typed,
                untyped_routes=router_total - router_typed,
            )
        )

    coverage.sort(key=lambda item: (-item.untyped_routes, item.router_file))
    return total_routes, typed_routes, total_routes - typed_routes, coverage


def _enum_value_label(value: object) -> str:
    if value is None:
        return "<null>"
    text = str(value)
    if text == "":
        return "<empty>"
    return text


def _collect_enum_catalog(db: Session) -> List[EnumCatalog]:
    specs = [
        ("learning_sessions.session_type", db.query(LearningSession.session_type).distinct().all()),
        ("learning_sessions.status", db.query(LearningSession.status).distinct().all()),
        ("question_records.question_type", db.query(QuestionRecord.question_type).distinct().all()),
        ("question_records.difficulty", db.query(QuestionRecord.difficulty).distinct().all()),
        ("question_records.confidence", db.query(QuestionRecord.confidence).distinct().all()),
        ("wrong_answers_v2.severity_tag", db.query(WrongAnswerV2.severity_tag).distinct().all()),
        ("wrong_answers_v2.mastery_status", db.query(WrongAnswerV2.mastery_status).distinct().all()),
        ("wrong_answer_retries.confidence", db.query(WrongAnswerRetry.confidence).distinct().all()),
        ("test_records.confidence", db.query(TestRecord.confidence).distinct().all()),
    ]

    catalog: List[EnumCatalog] = []
    for field_name, rows in specs:
        values = sorted({_enum_value_label(row[0]) for row in rows})
        catalog.append(EnumCatalog(field=field_name, values=values))

    return catalog


def _build_primary_risks(untyped_routes: int, total_routes: int) -> List[str]:
    percent = round(untyped_routes / total_routes * 100, 1) if total_routes else 0.0
    return [
        f"{untyped_routes}/{total_routes} API routes lack response_model coverage ({percent}%).",
        "Question-like payloads exist in multiple shapes across question_records, quiz_sessions, wrong_answers_v2, and test_records.",
        "Confidence values are partially missing in question_records; current dataset contains a dominant <empty> bucket.",
        "JSON columns are used as free-form storage without a shared serializer contract.",
        "Current frontend endpoints mix display-only fields with analysis fields, which is unsafe for direct LLM ingestion.",
    ]


@router.get("/audit", response_model=ContractAuditSummary)
async def get_llm_contract_audit(db: Session = Depends(get_db)) -> ContractAuditSummary:
    dataset_summary = _build_dataset_summary(db)
    total_routes, typed_routes, untyped_routes, coverage = _collect_route_coverage()
    typed_percent = round(typed_routes / total_routes * 100, 1) if total_routes else 0.0

    return ContractAuditSummary(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now().isoformat(),
        dataset_summary=dataset_summary,
        total_routes=total_routes,
        typed_routes=typed_routes,
        untyped_routes=untyped_routes,
        typed_route_percent=typed_percent,
        router_coverage=coverage,
        enum_catalog=_collect_enum_catalog(db),
        json_columns=[JsonColumnDefinition(**item) for item in JSON_COLUMN_REGISTRY],
        primary_risks=_build_primary_risks(untyped_routes, total_routes),
    )


@router.get("/wrong-answers", response_model=List[WrongAnswerSnapshot])
async def get_llm_wrong_answers(
    status: str = Query(default="active", pattern="^(active|archived|all)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> List[WrongAnswerSnapshot]:
    query = db.query(WrongAnswerV2).order_by(
        desc(WrongAnswerV2.last_wrong_at),
        desc(WrongAnswerV2.updated_at),
        desc(WrongAnswerV2.id),
    )
    if status != "all":
        query = query.filter(WrongAnswerV2.mastery_status == status)

    items = query.limit(limit).all()
    chapter_map = _build_chapter_map(db, [item.chapter_id for item in items if item.chapter_id])
    retry_map = _build_latest_retry_map(db, [int(item.id) for item in items])

    return [
        build_wrong_answer_snapshot(
            wrong_answer=item,
            chapter=chapter_map.get(item.chapter_id or ""),
            latest_retry=retry_map.get(int(item.id)),
        )
        for item in items
    ]


@router.get("/sessions", response_model=List[SessionSnapshot])
async def get_llm_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    session_type: str | None = Query(default=None),
    include_activities: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> List[SessionSnapshot]:
    query = db.query(LearningSession).order_by(desc(LearningSession.started_at), desc(LearningSession.id))
    if session_type:
        query = query.filter(LearningSession.session_type == session_type)

    sessions = query.limit(limit).all()
    session_ids = [session.id for session in sessions]
    record_map = group_question_records(load_latest_question_records(db, session_ids=session_ids))
    chapter_map = _build_chapter_map(db, [session.chapter_id for session in sessions if session.chapter_id])

    activity_map: Dict[str, List[LearningActivity]] = defaultdict(list)
    if include_activities and session_ids:
        activity_rows = (
            db.query(LearningActivity)
            .filter(LearningActivity.session_id.in_(session_ids))
            .order_by(LearningActivity.timestamp, LearningActivity.id)
            .all()
        )
        for activity in activity_rows:
            activity_map[activity.session_id].append(activity)

    return [
        build_session_snapshot(
            session=session,
            records=record_map.get(session.id, []),
            activities=activity_map.get(session.id, []),
            chapter=chapter_map.get(session.chapter_id or ""),
        )
        for session in sessions
    ]


@router.get("/context", response_model=LLMContextBundle)
async def get_llm_context(
    wrong_answer_limit: int = Query(default=30, ge=1, le=200),
    session_limit: int = Query(default=10, ge=1, le=100),
    include_activities: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> LLMContextBundle:
    analytics_source = await get_progress_board(period="all", date_str=None, db=db)
    wrong_answer_dashboard = await get_wrong_answer_dashboard(db=db)

    wrong_answers = await get_llm_wrong_answers(
        status="active",
        limit=wrong_answer_limit,
        db=db,
    )
    sessions = await get_llm_sessions(
        limit=session_limit,
        session_type=None,
        include_activities=include_activities,
        db=db,
    )

    return LLMContextBundle(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now().isoformat(),
        dataset_summary=_build_dataset_summary(db),
        analytics=build_analytics_snapshot(analytics_source, wrong_answer_dashboard),
        wrong_answers=wrong_answers,
        recent_sessions=sessions,
    )
