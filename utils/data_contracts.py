from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from learning_tracking_models import (
    LearningActivity,
    LearningSession,
    QuestionRecord,
    WrongAnswerRetry,
    WrongAnswerV2,
)
from models import Chapter
from utils.answer import normalize_answer

SCHEMA_VERSION = "llm-ready.v1"
OPTION_KEYS: Tuple[str, ...] = ("A", "B", "C", "D", "E")

CONFIDENCE_ALIASES = {
    "sure": "sure",
    "unsure": "unsure",
    "no": "no",
    "dont_know": "no",
    "don't_know": "no",
    "dontknow": "no",
}

DIFFICULTY_ALIASES = {
    "基础": "basic",
    "basic": "basic",
    "提高": "advanced",
    "advanced": "advanced",
    "难题": "hard",
    "hard": "hard",
}

QUESTION_TYPE_ALIASES = {
    "A1": "A1",
    "A2": "A2",
    "A3": "A3",
    "X": "X",
}

SESSION_TYPE_ALIASES = {
    "exam": "exam",
    "chapter_test": "exam",
    "practice": "practice",
    "detail_practice": "detail_practice",
    "wrong_answer_review": "wrong_answer_review",
}

SESSION_STATUS_ALIASES = {
    "in_progress": "in_progress",
    "completed": "completed",
    "abandoned": "abandoned",
    "paused": "paused",
}

SEVERITY_ALIASES = {
    "critical": "critical",
    "stubborn": "stubborn",
    "landmine": "landmine",
    "normal": "normal",
}

MASTERY_STATUS_ALIASES = {
    "active": "active",
    "archived": "archived",
}

JSON_COLUMN_REGISTRY: List[Dict[str, str]] = [
    {
        "model": "DailyUpload",
        "field": "ai_extracted",
        "shape": "object",
        "notes": "book/chapter/concepts/summary payload generated during content parsing",
    },
    {
        "model": "Chapter",
        "field": "concepts",
        "shape": "array<object>",
        "notes": "chapter-level concept list; shape is not strictly enforced today",
    },
    {
        "model": "TestRecord",
        "field": "ai_options",
        "shape": "object",
        "notes": "single-question option map",
    },
    {
        "model": "TestRecord",
        "field": "weak_points",
        "shape": "array<string>",
        "notes": "AI-generated weakness labels",
    },
    {
        "model": "FeynmanSession",
        "field": "dialogue",
        "shape": "array<object>",
        "notes": "chat transcript with role/content/time",
    },
    {
        "model": "WrongAnswer",
        "field": "options",
        "shape": "object",
        "notes": "legacy wrong answer option map",
    },
    {
        "model": "WrongAnswer",
        "field": "weak_points",
        "shape": "array<string>",
        "notes": "legacy weakness labels",
    },
    {
        "model": "QuizSession",
        "field": "questions",
        "shape": "array<object>",
        "notes": "legacy batch quiz question snapshots",
    },
    {
        "model": "QuizSession",
        "field": "answers",
        "shape": "array<object>",
        "notes": "legacy batch quiz answer snapshots",
    },
    {
        "model": "LearningActivity",
        "field": "data",
        "shape": "object",
        "notes": "activity-specific event payload",
    },
    {
        "model": "QuestionRecord",
        "field": "options",
        "shape": "object",
        "notes": "question option map",
    },
    {
        "model": "QuestionRecord",
        "field": "answer_changes",
        "shape": "array<object>",
        "notes": "intermediate answer edits",
    },
    {
        "model": "DailyLearningLog",
        "field": "knowledge_points_covered",
        "shape": "array<string>",
        "notes": "daily knowledge point coverage",
    },
    {
        "model": "DailyLearningLog",
        "field": "weak_knowledge_points",
        "shape": "array<string>",
        "notes": "daily weak area list",
    },
    {
        "model": "DailyLearningLog",
        "field": "session_ids",
        "shape": "array<string>",
        "notes": "daily linked session ids",
    },
    {
        "model": "LearningInsight",
        "field": "related_data",
        "shape": "object",
        "notes": "free-form insight support data",
    },
    {
        "model": "WrongAnswerV2",
        "field": "options",
        "shape": "object",
        "notes": "canonical wrong answer option map used by current UI",
    },
    {
        "model": "WrongAnswerV2",
        "field": "linked_record_ids",
        "shape": "array<int>",
        "notes": "linked question record ids",
    },
    {
        "model": "WrongAnswerV2",
        "field": "variant_data",
        "shape": "object",
        "notes": "cached transformed question payload",
    },
    {
        "model": "WrongAnswerV2",
        "field": "parent_ids",
        "shape": "array<int>",
        "notes": "fusion parent wrong answer ids",
    },
    {
        "model": "WrongAnswerV2",
        "field": "fusion_data",
        "shape": "object",
        "notes": "fusion evaluation cache",
    },
    {
        "model": "WrongAnswerRetry",
        "field": "ai_evaluation",
        "shape": "object",
        "notes": "AI rationale judgement payload",
    },
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OptionItem(ContractModel):
    key: str
    text: str


class ChapterRef(ContractModel):
    id: Optional[str] = None
    book: Optional[str] = None
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    label: Optional[str] = None


class QuestionSnapshot(ContractModel):
    question_id: str
    source_type: str
    source_record_id: str
    fingerprint: Optional[str] = None
    stem: str
    question_type: Optional[str] = None
    difficulty_code: Optional[str] = None
    difficulty_label: Optional[str] = None
    key_point: Optional[str] = None
    options: List[OptionItem] = Field(default_factory=list)
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None


class AttemptSnapshot(ContractModel):
    user_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    confidence: Optional[str] = None
    time_spent_seconds: int = 0
    answered_at: Optional[str] = None
    rationale_text: Optional[str] = None
    answer_changes: List[Dict[str, Any]] = Field(default_factory=list)
    ai_evaluation: Optional[Dict[str, Any]] = None


class SessionStatsSnapshot(ContractModel):
    total_questions: int = 0
    answered_questions: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    accuracy_percent: float = 0.0
    score: Optional[int] = None
    sure_count: int = 0
    unsure_count: int = 0
    no_count: int = 0
    duration_seconds: int = 0


class ActivitySnapshot(ContractModel):
    activity_type: Optional[str] = None
    activity_name: Optional[str] = None
    timestamp: Optional[str] = None
    relative_time_ms: int = 0
    data: Dict[str, Any] = Field(default_factory=dict)


class SessionQuestionSnapshot(ContractModel):
    question: QuestionSnapshot
    attempt: AttemptSnapshot


class SessionSnapshot(ContractModel):
    session_id: str
    session_type: Optional[str] = None
    session_type_raw: Optional[str] = None
    status: Optional[str] = None
    status_raw: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    chapter: ChapterRef = Field(default_factory=ChapterRef)
    knowledge_point: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    stats: SessionStatsSnapshot
    questions: List[SessionQuestionSnapshot] = Field(default_factory=list)
    activities: List[ActivitySnapshot] = Field(default_factory=list)


class SRSState(ContractModel):
    easiness_factor: Optional[float] = None
    interval_days: Optional[int] = None
    repetitions: Optional[int] = None
    next_review_date: Optional[str] = None


class VariantSnapshot(ContractModel):
    question: Optional[str] = None
    options: List[OptionItem] = Field(default_factory=list)
    answer: Optional[str] = None
    explanation: Optional[str] = None
    transform_type: Optional[str] = None
    core_knowledge: Optional[str] = None
    generated_at: Optional[str] = None


class WrongAnswerSnapshot(ContractModel):
    wrong_answer_id: int
    question: QuestionSnapshot
    chapter: ChapterRef = Field(default_factory=ChapterRef)
    severity_tag: Optional[str] = None
    severity_tag_raw: Optional[str] = None
    mastery_status: Optional[str] = None
    mastery_status_raw: Optional[str] = None
    error_count: int = 0
    encounter_count: int = 0
    retry_count: int = 0
    linked_record_ids: List[int] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    first_wrong_at: Optional[str] = None
    last_wrong_at: Optional[str] = None
    last_retried_at: Optional[str] = None
    latest_retry: Optional[AttemptSnapshot] = None
    srs: SRSState = Field(default_factory=SRSState)
    variant: Optional[VariantSnapshot] = None


class DistributionItem(ContractModel):
    key: str
    label: str
    count: int = 0
    percent: float = 0.0
    accuracy: Optional[float] = None


class DailyMetricPoint(ContractModel):
    date: str
    questions: int = 0
    correct: int = 0
    sessions: int = 0
    duration_seconds: int = 0
    accuracy: float = 0.0


class WrongAnswerTrendPoint(ContractModel):
    date: str
    new: int = 0
    archived: int = 0
    net: int = 0


class WeakPointSnapshot(ContractModel):
    name: str
    total: int = 0
    correct: int = 0
    wrong: int = 0
    accuracy: float = 0.0
    avg_confidence: float = 0.0


class WeakChapterSnapshot(ContractModel):
    chapter_id: str = ""
    chapter_name: str
    active_count: int = 0
    critical_count: int = 0
    stubborn_count: int = 0
    mastery_percent: float = 0.0


class AnalyticsSnapshot(ContractModel):
    learning_overview: Dict[str, Any]
    wrong_answer_overview: Dict[str, Any]
    confidence_distribution: List[DistributionItem] = Field(default_factory=list)
    difficulty_distribution: List[DistributionItem] = Field(default_factory=list)
    question_type_distribution: List[DistributionItem] = Field(default_factory=list)
    daily_learning_trend: List[DailyMetricPoint] = Field(default_factory=list)
    wrong_answer_daily_trend: List[WrongAnswerTrendPoint] = Field(default_factory=list)
    weak_points: List[WeakPointSnapshot] = Field(default_factory=list)
    weak_chapters: List[WeakChapterSnapshot] = Field(default_factory=list)


class DatasetSummary(ContractModel):
    daily_uploads: int = 0
    chapters: int = 0
    concept_mastery: int = 0
    test_records: int = 0
    quiz_sessions: int = 0
    learning_sessions: int = 0
    question_records: int = 0
    wrong_answers_v2: int = 0
    wrong_answer_retries: int = 0


class RouteCoverage(ContractModel):
    router_file: str
    total_routes: int
    typed_routes: int
    untyped_routes: int


class EnumCatalog(ContractModel):
    field: str
    values: List[str] = Field(default_factory=list)


class JsonColumnDefinition(ContractModel):
    model: str
    field: str
    shape: str
    notes: str


class ContractAuditSummary(ContractModel):
    schema_version: str
    generated_at: str
    dataset_summary: DatasetSummary
    total_routes: int
    typed_routes: int
    untyped_routes: int
    typed_route_percent: float
    router_coverage: List[RouteCoverage] = Field(default_factory=list)
    enum_catalog: List[EnumCatalog] = Field(default_factory=list)
    json_columns: List[JsonColumnDefinition] = Field(default_factory=list)
    primary_risks: List[str] = Field(default_factory=list)


class LLMContextBundle(ContractModel):
    schema_version: str
    generated_at: str
    dataset_summary: DatasetSummary
    analytics: AnalyticsSnapshot
    wrong_answers: List[WrongAnswerSnapshot] = Field(default_factory=list)
    recent_sessions: List[SessionSnapshot] = Field(default_factory=list)


def to_iso_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat()
    text = str(value).strip()
    return text or None


def to_iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return text[:10]


def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_option_map(options: Any) -> Dict[str, str]:
    if isinstance(options, str):
        text = options.strip()
        if not text:
            return {}
        try:
            options = json.loads(text)
        except Exception:
            return {}

    if not isinstance(options, dict):
        return {}

    normalized: Dict[str, str] = {}
    for key, value in options.items():
        option_key = clean_text(key)
        option_value = clean_text(value)
        if not option_key or not option_value:
            continue

        normalized_key = "".join(ch for ch in option_key.upper() if ch in OPTION_KEYS)[:1]
        if normalized_key:
            normalized[normalized_key] = option_value

    return {key: normalized[key] for key in OPTION_KEYS if key in normalized}


def normalize_option_list(options: Any) -> List[OptionItem]:
    return [OptionItem(key=key, text=text) for key, text in normalize_option_map(options).items()]


def normalize_confidence(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return CONFIDENCE_ALIASES.get(text.lower(), text.lower())


def coerce_confidence(value: Any, default: str = "unsure") -> str:
    normalized = normalize_confidence(value)
    if normalized in {"sure", "unsure", "no"}:
        return normalized
    return default


def normalize_difficulty_code(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return DIFFICULTY_ALIASES.get(text.lower(), DIFFICULTY_ALIASES.get(text, text.lower()))


def normalize_question_type(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return QUESTION_TYPE_ALIASES.get(text.upper(), text.upper())


def normalize_session_type(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return SESSION_TYPE_ALIASES.get(text, text)


def normalize_session_status(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return SESSION_STATUS_ALIASES.get(text, text)


def normalize_severity(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return SEVERITY_ALIASES.get(text, text)


def normalize_mastery_status(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return MASTERY_STATUS_ALIASES.get(text, text)


def build_chapter_ref(
    chapter_id: Optional[str] = None,
    chapter: Optional[Chapter] = None,
) -> ChapterRef:
    if chapter is None and not chapter_id:
        return ChapterRef()

    book = clean_text(getattr(chapter, "book", None))
    chapter_number = clean_text(getattr(chapter, "chapter_number", None))
    chapter_title = clean_text(getattr(chapter, "chapter_title", None))
    label = None
    if book or chapter_number or chapter_title:
        label = " - ".join(
            part
            for part in [
                book,
                " ".join(part for part in [chapter_number, chapter_title] if part).strip() or None,
            ]
            if part
        )

    return ChapterRef(
        id=clean_text(chapter_id or getattr(chapter, "id", None)),
        book=book,
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        label=label,
    )


def _question_snapshot_id(source_type: str, source_record_id: Any) -> str:
    return f"{source_type}:{source_record_id}"


def build_question_snapshot(
    *,
    source_type: str,
    source_record_id: Any,
    stem: Any,
    question_type: Any,
    difficulty: Any,
    key_point: Any,
    options: Any,
    correct_answer: Any,
    explanation: Any,
    fingerprint: Optional[str] = None,
) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=_question_snapshot_id(source_type, source_record_id),
        source_type=source_type,
        source_record_id=str(source_record_id),
        fingerprint=clean_text(fingerprint),
        stem=clean_text(stem) or "",
        question_type=normalize_question_type(question_type),
        difficulty_code=normalize_difficulty_code(difficulty),
        difficulty_label=clean_text(difficulty),
        key_point=clean_text(key_point),
        options=normalize_option_list(options),
        correct_answer=clean_text(normalize_answer(correct_answer) or correct_answer),
        explanation=clean_text(explanation),
    )


def build_attempt_snapshot(
    *,
    user_answer: Any = None,
    is_correct: Optional[bool] = None,
    confidence: Any = None,
    time_spent_seconds: Any = 0,
    answered_at: Any = None,
    rationale_text: Any = None,
    answer_changes: Any = None,
    ai_evaluation: Optional[Dict[str, Any]] = None,
) -> AttemptSnapshot:
    normalized_changes = canonicalize_answer_changes(answer_changes)
    spent = _coerce_int(time_spent_seconds, default=0)

    return AttemptSnapshot(
        user_answer=clean_text(normalize_answer(user_answer) or user_answer),
        is_correct=is_correct,
        confidence=normalize_confidence(confidence),
        time_spent_seconds=max(spent, 0),
        answered_at=to_iso_datetime(answered_at),
        rationale_text=clean_text(rationale_text),
        answer_changes=normalized_changes,
        ai_evaluation=canonicalize_ai_evaluation(ai_evaluation),
    )


def load_latest_question_records(
    db: Session,
    session_ids: Optional[Sequence[str]] = None,
) -> List[QuestionRecord]:
    query = db.query(QuestionRecord)
    if session_ids is not None:
        if not session_ids:
            return []
        query = query.filter(QuestionRecord.session_id.in_(list(session_ids)))

    latest_by_key: Dict[Tuple[str, int], QuestionRecord] = {}
    for record in query.all():
        key = (record.session_id, int(record.question_index or 0))
        current = latest_by_key.get(key)
        current_sort = (
            current.answered_at if current else datetime.min,
            int(current.id or 0) if current else 0,
        )
        record_sort = (record.answered_at or datetime.min, int(record.id or 0))
        if current is None or record_sort >= current_sort:
            latest_by_key[key] = record

    return sorted(
        latest_by_key.values(),
        key=lambda item: (str(item.session_id), int(item.question_index or 0)),
    )


def group_question_records(records: Iterable[QuestionRecord]) -> Dict[str, List[QuestionRecord]]:
    grouped: Dict[str, List[QuestionRecord]] = {}
    for record in records:
        grouped.setdefault(record.session_id, []).append(record)
    return grouped


def build_session_stats(
    session: LearningSession,
    records: Sequence[QuestionRecord],
) -> SessionStatsSnapshot:
    total_questions = max(int(session.total_questions or 0), len(records))
    answered_questions = len(records)
    correct_count = sum(1 for record in records if record.is_correct)
    wrong_count = sum(1 for record in records if record.is_correct is False)
    sure_count = sum(1 for record in records if normalize_confidence(record.confidence) == "sure")
    unsure_count = sum(1 for record in records if normalize_confidence(record.confidence) == "unsure")
    no_count = sum(1 for record in records if normalize_confidence(record.confidence) == "no")
    accuracy_percent = round(correct_count / total_questions * 100, 1) if total_questions > 0 else 0.0
    return SessionStatsSnapshot(
        total_questions=total_questions,
        answered_questions=answered_questions,
        correct_count=correct_count,
        wrong_count=wrong_count,
        accuracy_percent=accuracy_percent,
        score=int(session.score) if session.score is not None else None,
        sure_count=sure_count,
        unsure_count=unsure_count,
        no_count=no_count,
        duration_seconds=int(session.duration_seconds or 0),
    )


def build_session_snapshot(
    session: LearningSession,
    records: Sequence[QuestionRecord],
    activities: Sequence[LearningActivity],
    chapter: Optional[Chapter],
) -> SessionSnapshot:
    question_items = [
        SessionQuestionSnapshot(
            question=build_question_snapshot(
                source_type="question_record",
                source_record_id=record.id,
                stem=record.question_text,
                question_type=record.question_type,
                difficulty=record.difficulty,
                key_point=record.key_point,
                options=record.options,
                correct_answer=record.correct_answer,
                explanation=record.explanation,
            ),
            attempt=build_attempt_snapshot(
                user_answer=record.user_answer,
                is_correct=record.is_correct,
                confidence=record.confidence,
                time_spent_seconds=record.time_spent_seconds,
                answered_at=record.answered_at,
                answer_changes=record.answer_changes,
            ),
        )
        for record in records
    ]

    activity_items = [
        ActivitySnapshot(
            activity_type=clean_text(activity.activity_type),
            activity_name=clean_text(activity.activity_name),
            timestamp=to_iso_datetime(activity.timestamp),
            relative_time_ms=int(activity.relative_time_ms or 0),
            data=canonicalize_learning_activity_data(activity.data),
        )
        for activity in activities
    ]

    return SessionSnapshot(
        session_id=session.id,
        session_type=normalize_session_type(session.session_type),
        session_type_raw=clean_text(session.session_type),
        status=normalize_session_status(session.status),
        status_raw=clean_text(session.status),
        title=clean_text(session.title),
        description=clean_text(session.description),
        chapter=build_chapter_ref(session.chapter_id, chapter),
        knowledge_point=clean_text(session.knowledge_point),
        started_at=to_iso_datetime(session.started_at),
        completed_at=to_iso_datetime(session.completed_at),
        stats=build_session_stats(session, records),
        questions=question_items,
        activities=activity_items,
    )


def build_variant_snapshot(variant_data: Any) -> Optional[VariantSnapshot]:
    normalized_variant = canonicalize_variant_data(variant_data)
    if not normalized_variant:
        return None

    return VariantSnapshot(
        question=clean_text(normalized_variant.get("variant_question")),
        options=normalize_option_list(normalized_variant.get("variant_options")),
        answer=clean_text(
            normalize_answer(normalized_variant.get("variant_answer"))
            or normalized_variant.get("variant_answer")
        ),
        explanation=clean_text(normalized_variant.get("variant_explanation")),
        transform_type=clean_text(normalized_variant.get("transform_type")),
        core_knowledge=clean_text(normalized_variant.get("core_knowledge")),
        generated_at=to_iso_datetime(normalized_variant.get("generated_at")),
    )


def build_wrong_answer_snapshot(
    wrong_answer: WrongAnswerV2,
    chapter: Optional[Chapter],
    latest_retry: Optional[WrongAnswerRetry],
) -> WrongAnswerSnapshot:
    question = build_question_snapshot(
        source_type="wrong_answer_v2",
        source_record_id=wrong_answer.id,
        stem=wrong_answer.question_text,
        question_type=wrong_answer.question_type,
        difficulty=wrong_answer.difficulty,
        key_point=wrong_answer.key_point,
        options=wrong_answer.options,
        correct_answer=wrong_answer.correct_answer,
        explanation=wrong_answer.explanation,
        fingerprint=wrong_answer.question_fingerprint,
    )

    linked_record_ids = canonicalize_int_list(wrong_answer.linked_record_ids)

    return WrongAnswerSnapshot(
        wrong_answer_id=wrong_answer.id,
        question=question,
        chapter=build_chapter_ref(wrong_answer.chapter_id, chapter),
        severity_tag=normalize_severity(wrong_answer.severity_tag),
        severity_tag_raw=clean_text(wrong_answer.severity_tag),
        mastery_status=normalize_mastery_status(wrong_answer.mastery_status),
        mastery_status_raw=clean_text(wrong_answer.mastery_status),
        error_count=int(wrong_answer.error_count or 0),
        encounter_count=int(wrong_answer.encounter_count or 0),
        retry_count=int(wrong_answer.retry_count or 0),
        linked_record_ids=linked_record_ids,
        created_at=to_iso_datetime(wrong_answer.created_at),
        updated_at=to_iso_datetime(wrong_answer.updated_at),
        first_wrong_at=to_iso_datetime(wrong_answer.first_wrong_at),
        last_wrong_at=to_iso_datetime(wrong_answer.last_wrong_at),
        last_retried_at=to_iso_datetime(wrong_answer.last_retried_at),
        latest_retry=(
            build_attempt_snapshot(
                user_answer=latest_retry.user_answer,
                is_correct=latest_retry.is_correct,
                confidence=latest_retry.confidence,
                time_spent_seconds=latest_retry.time_spent_seconds,
                answered_at=latest_retry.retried_at,
                rationale_text=latest_retry.rationale_text,
                ai_evaluation=latest_retry.ai_evaluation if isinstance(latest_retry.ai_evaluation, dict) else None,
            )
            if latest_retry
            else None
        ),
        srs=SRSState(
            easiness_factor=float(wrong_answer.sm2_ef) if wrong_answer.sm2_ef is not None else None,
            interval_days=int(wrong_answer.sm2_interval or 0),
            repetitions=int(wrong_answer.sm2_repetitions or 0),
            next_review_date=to_iso_date(wrong_answer.next_review_date),
        ),
        variant=build_variant_snapshot(wrong_answer.variant_data),
    )


def _distribution_label_map() -> Dict[str, Dict[str, str]]:
    return {
        "confidence": {
            "sure": "Sure",
            "unsure": "Unsure",
            "no": "No",
        },
        "difficulty": {
            "basic": "Basic",
            "advanced": "Advanced",
            "hard": "Hard",
        },
        "question_type": {
            "A1": "A1",
            "A2": "A2",
            "A3": "A3",
            "X": "X",
        },
    }


def _build_distribution_list(
    source: Dict[str, Any],
    category: str,
) -> List[DistributionItem]:
    labels = _distribution_label_map().get(category, {})
    items: List[DistributionItem] = []

    for key, payload in source.items():
        if isinstance(payload, dict):
            count = int(payload.get("count") or 0)
            percent = float(payload.get("pct") or payload.get("percent") or 0.0)
            accuracy = payload.get("accuracy")
        else:
            count = int(payload or 0)
            percent = 0.0
            accuracy = None

        normalized_key = key
        if category == "difficulty":
            normalized_key = normalize_difficulty_code(key) or str(key)
        elif category == "question_type":
            normalized_key = normalize_question_type(key) or str(key)
        elif category == "confidence":
            normalized_key = normalize_confidence(key) or str(key)

        items.append(
            DistributionItem(
                key=str(normalized_key),
                label=labels.get(str(normalized_key), str(key)),
                count=count,
                percent=round(percent, 1),
                accuracy=round(float(accuracy), 1) if isinstance(accuracy, (int, float)) else None,
            )
        )

    return sorted(items, key=lambda item: (-item.count, item.key))


def build_analytics_snapshot(
    tracking_stats: Dict[str, Any],
    wrong_answer_dashboard: Dict[str, Any],
) -> AnalyticsSnapshot:
    summary = tracking_stats.get("summary", {})
    overview = wrong_answer_dashboard.get("overview", {})

    confidence_source = {
        item.get("key"): {
            "count": item.get("count", 0),
            "percent": item.get("pct", 0.0),
        }
        for item in tracking_stats.get("confidence_distribution", [])
        if isinstance(item, dict) and item.get("key")
    }

    weak_points = [
        WeakPointSnapshot(
            name=str(item.get("name") or ""),
            total=int(item.get("total") or 0),
            correct=int(item.get("correct") or 0),
            wrong=int(item.get("wrong") or 0),
            accuracy=round(float(item.get("accuracy") or 0.0), 1),
            avg_confidence=round(float(item.get("avg_confidence") or 0.0), 2),
        )
        for item in tracking_stats.get("weak_points", [])
        if isinstance(item, dict) and item.get("name")
    ]

    weak_chapters = [
        WeakChapterSnapshot(
            chapter_id=str(item.get("chapter_id") or ""),
            chapter_name=str(item.get("chapter_name") or ""),
            active_count=int(item.get("active_count") or 0),
            critical_count=int(item.get("critical_count") or 0),
            stubborn_count=int(item.get("stubborn_count") or 0),
            mastery_percent=round(float(item.get("mastery_percent") or 0.0), 1),
        )
        for item in wrong_answer_dashboard.get("weak_chapters", [])
        if isinstance(item, dict) and item.get("chapter_name")
    ]

    daily_learning_trend = [
        DailyMetricPoint(
            date=str(item.get("date")),
            questions=int(item.get("questions") or 0),
            correct=int(item.get("correct") or 0),
            sessions=int(item.get("sessions") or 0),
            duration_seconds=int(item.get("duration_seconds") or 0),
            accuracy=round(float(item.get("accuracy") or 0.0), 1),
        )
        for item in tracking_stats.get("daily_trend_30", [])
        if isinstance(item, dict) and item.get("date")
    ]

    wrong_answer_daily_trend = [
        WrongAnswerTrendPoint(
            date=str(item.get("date")),
            new=int(item.get("new") or 0),
            archived=int(item.get("archived") or 0),
            net=int(item.get("net") or 0),
        )
        for item in wrong_answer_dashboard.get("daily_trend", [])
        if isinstance(item, dict) and item.get("date")
    ]

    return AnalyticsSnapshot(
        learning_overview={
            "total_sessions": int(summary.get("total_sessions") or 0),
            "total_questions": int(summary.get("total_questions") or 0),
            "total_correct": int(summary.get("total_correct") or 0),
            "avg_accuracy": round(float(summary.get("avg_accuracy") or 0.0), 1),
            "total_duration_seconds": int(summary.get("total_duration") or 0),
        },
        wrong_answer_overview={
            "active_count": int(overview.get("active_count") or 0),
            "archived_count": int(overview.get("archived_count") or 0),
            "total_count": int(overview.get("total_count") or 0),
            "mastery_percent": round(float(overview.get("mastery_percent") or 0.0), 1),
            "retry_correct_rate": round(float(overview.get("retry_correct_rate") or 0.0), 1),
            "retry_rate_delta_vs_last_week": round(float(overview.get("retry_rate_delta_vs_last_week") or 0.0), 1),
            "streak_days": int(overview.get("streak_days") or 0),
            "max_streak_days": int(overview.get("max_streak_days") or 0),
            "active_delta_vs_yesterday": int(overview.get("active_delta_vs_yesterday") or 0),
        },
        confidence_distribution=_build_distribution_list(confidence_source, "confidence"),
        difficulty_distribution=_build_distribution_list(
            tracking_stats.get("difficulty_distribution", {}),
            "difficulty",
        ),
        question_type_distribution=_build_distribution_list(
            tracking_stats.get("type_distribution", {}),
            "question_type",
        ),
        daily_learning_trend=daily_learning_trend,
        wrong_answer_daily_trend=wrong_answer_daily_trend,
        weak_points=weak_points,
        weak_chapters=weak_chapters,
    )


def build_dataset_summary(
    *,
    daily_uploads: int,
    chapters: int,
    concept_mastery: int,
    test_records: int,
    quiz_sessions: int,
    learning_sessions: int,
    question_records: int,
    wrong_answers_v2: int,
    wrong_answer_retries: int,
) -> DatasetSummary:
    return DatasetSummary(
        daily_uploads=daily_uploads,
        chapters=chapters,
        concept_mastery=concept_mastery,
        test_records=test_records,
        quiz_sessions=quiz_sessions,
        learning_sessions=learning_sessions,
        question_records=question_records,
        wrong_answers_v2=wrong_answers_v2,
        wrong_answer_retries=wrong_answer_retries,
    )


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = clean_text(value)
    if text is None:
        return default

    lowered = text.lower()
    if lowered in {"true", "1", "yes", "y", "on"}:
        return True
    if lowered in {"false", "0", "no", "n", "off"}:
        return False
    return default


def _json_safe_value(value: Any, *, depth: int = 4) -> Any:
    if depth <= 0:
        return clean_text(value)

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_safe_value(value.model_dump(), depth=depth - 1)
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = clean_text(key)
            if not normalized_key:
                continue
            normalized_value = _json_safe_value(item, depth=depth - 1)
            if normalized_value is None:
                continue
            normalized[normalized_key] = normalized_value
        return normalized
    if isinstance(value, (list, tuple, set)):
        normalized_list: List[Any] = []
        for item in value:
            normalized_item = _json_safe_value(item, depth=depth - 1)
            if normalized_item is None:
                continue
            normalized_list.append(normalized_item)
        return normalized_list
    return clean_text(value)


def _coerce_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return [text]
        return parsed if isinstance(parsed, list) else [parsed]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def canonicalize_string_list(values: Any) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in _coerce_list(values):
        text = clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def canonicalize_int_list(values: Any, *, sort_values: bool = True) -> List[int]:
    normalized: List[int] = []
    seen = set()
    for item in _coerce_list(values):
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number in seen:
            continue
        seen.add(number)
        normalized.append(number)
    return sorted(normalized) if sort_values else normalized


def canonicalize_linked_record_ids(values: Any) -> List[int]:
    return canonicalize_int_list(values)


def canonicalize_parent_ids(values: Any) -> List[int]:
    return canonicalize_int_list(values)


def canonicalize_ai_evaluation(data: Any) -> Optional[Dict[str, Any]]:
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except Exception:
            return {"feedback": text}

    if not isinstance(data, dict):
        return None

    raw = _json_safe_value(data)
    if not isinstance(raw, dict):
        return None

    normalized: Dict[str, Any] = {
        "verdict": clean_text(raw.get("verdict")),
        "reasoning_score": max(_coerce_int(raw.get("reasoning_score"), default=0), 0),
        "diagnosis": clean_text(raw.get("diagnosis")),
        "weak_links": canonicalize_string_list(raw.get("weak_links")),
        "feedback": clean_text(raw.get("feedback")),
    }

    for key, value in raw.items():
        if key in normalized:
            continue
        normalized[key] = value

    compact = {key: value for key, value in normalized.items() if value not in (None, [], {})}
    return compact or None


def _canonicalize_answer_change(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    raw = _json_safe_value(item)
    if not isinstance(raw, dict):
        return None

    source_answer = (
        raw.get("from")
        or raw.get("from_answer")
        or raw.get("previous_answer")
        or raw.get("previous")
    )
    target_answer = (
        raw.get("to")
        or raw.get("to_answer")
        or raw.get("current_answer")
        or raw.get("user_answer")
    )

    normalized: Dict[str, Any] = {
        "from": clean_text(normalize_answer(source_answer) or source_answer),
        "to": clean_text(normalize_answer(target_answer) or target_answer),
        "at": to_iso_datetime(raw.get("at") or raw.get("changed_at") or raw.get("timestamp")),
        "confidence": normalize_confidence(raw.get("confidence")),
        "is_correct": raw.get("is_correct") if isinstance(raw.get("is_correct"), bool) else None,
        "note": clean_text(raw.get("note") or raw.get("reason") or raw.get("comment")),
    }

    for key, value in raw.items():
        if key in {
            "from",
            "from_answer",
            "previous_answer",
            "previous",
            "to",
            "to_answer",
            "current_answer",
            "user_answer",
            "at",
            "changed_at",
            "timestamp",
            "confidence",
            "is_correct",
            "note",
            "reason",
            "comment",
        }:
            continue
        normalized[key] = value

    compact = {key: value for key, value in normalized.items() if value is not None}
    return compact or None


def canonicalize_answer_changes(answer_changes: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in _coerce_list(answer_changes):
        normalized_item = _canonicalize_answer_change(item)
        if normalized_item:
            normalized.append(normalized_item)
    return normalized


def canonicalize_learning_activity_data(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    raw = _json_safe_value(data)
    if not isinstance(raw, dict):
        return {}

    normalized = dict(raw)

    for field in ["chapter_id", "knowledge_point", "answer", "user_answer", "activity_name"]:
        if field in raw:
            text = clean_text(raw.get(field))
            if text is not None:
                normalized[field] = text

    for field in ["question_index", "score", "correct", "wrong", "time_spent", "time_spent_seconds"]:
        if field in raw:
            normalized[field] = max(_coerce_int(raw.get(field), default=0), 0)

    if "confidence" in raw:
        normalized["confidence"] = normalize_confidence(raw.get("confidence"))
    if "options" in raw:
        normalized["options"] = normalize_option_map(raw.get("options"))
    if "selected_options" in raw:
        normalized["selected_options"] = canonicalize_string_list(raw.get("selected_options"))
    if "answer_changes" in raw:
        normalized["answer_changes"] = canonicalize_answer_changes(raw.get("answer_changes"))
    if "weak_points" in raw:
        normalized["weak_points"] = canonicalize_string_list(raw.get("weak_points"))

    return normalized


def canonicalize_quiz_question(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    raw = _json_safe_value(item)
    if not isinstance(raw, dict):
        return {}

    normalized = dict(raw)

    if "question" in raw:
        normalized["question"] = clean_text(raw.get("question")) or ""
    if "question_text" in raw:
        normalized["question_text"] = clean_text(raw.get("question_text")) or ""
    if "options" in raw:
        normalized["options"] = normalize_option_map(raw.get("options"))
    if "correct_answer" in raw:
        normalized["correct_answer"] = clean_text(
            normalize_answer(raw.get("correct_answer")) or raw.get("correct_answer")
        ) or ""
    if "user_answer" in raw:
        normalized["user_answer"] = clean_text(
            normalize_answer(raw.get("user_answer")) or raw.get("user_answer")
        ) or ""
    if "key_point" in raw:
        normalized["key_point"] = clean_text(raw.get("key_point")) or ""
    if "key_points" in raw:
        normalized["key_points"] = canonicalize_string_list(raw.get("key_points"))
    if "common_mistakes" in raw:
        normalized["common_mistakes"] = canonicalize_string_list(raw.get("common_mistakes"))
    if "weak_points" in raw:
        normalized["weak_points"] = canonicalize_string_list(raw.get("weak_points"))

    for field in ["question_id", "concept_id", "concept_name", "explanation", "difficulty", "type", "question_type"]:
        if field in raw:
            text = clean_text(raw.get(field))
            if text is not None:
                normalized[field] = text

    for field in ["test_id", "wrong_answer_id", "score", "question_index"]:
        if field in raw:
            normalized[field] = max(_coerce_int(raw.get(field), default=0), 0)

    for field in ["is_wrong_answer", "is_correct"]:
        if field in raw:
            normalized[field] = _coerce_bool(raw.get(field))

    return normalized


def canonicalize_quiz_questions(items: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in _coerce_list(items):
        normalized_item = canonicalize_quiz_question(item)
        if normalized_item:
            normalized.append(normalized_item)
    return normalized


def canonicalize_quiz_answer(item: Any, *, question_index: Optional[int] = None) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    raw = _json_safe_value(item)
    if not isinstance(raw, dict):
        return {}

    normalized = dict(raw)

    if question_index is not None or "question_index" in raw:
        normalized["question_index"] = max(
            _coerce_int(raw.get("question_index"), default=question_index or 0),
            0,
        )

    if "user_answer" in raw:
        normalized["user_answer"] = clean_text(
            normalize_answer(raw.get("user_answer")) or raw.get("user_answer")
        ) or ""
    if "correct_answer" in raw:
        normalized["correct_answer"] = clean_text(
            normalize_answer(raw.get("correct_answer")) or raw.get("correct_answer")
        ) or ""
    if "confidence" in raw or question_index is not None:
        normalized["confidence"] = normalize_confidence(raw.get("confidence"))

    for field in ["feedback", "explanation", "error_type", "confidence_analysis", "rationale_text"]:
        if field in raw:
            text = clean_text(raw.get(field))
            if text is not None:
                normalized[field] = text

    for field in ["test_id", "score"]:
        if field in raw:
            normalized[field] = max(_coerce_int(raw.get(field), default=0), 0)

    if "time_spent_seconds" in raw:
        normalized["time_spent_seconds"] = max(_coerce_int(raw.get("time_spent_seconds"), default=0), 0)
    if "time_spent" in raw:
        normalized["time_spent"] = max(_coerce_int(raw.get("time_spent"), default=0), 0)
    elif "time_spent_seconds" in normalized:
        normalized["time_spent"] = normalized["time_spent_seconds"]

    if "is_correct" in raw:
        normalized["is_correct"] = _coerce_bool(raw.get("is_correct"))
    if "weak_points" in raw:
        normalized["weak_points"] = canonicalize_string_list(raw.get("weak_points"))
    if "answer_changes" in raw:
        normalized["answer_changes"] = canonicalize_answer_changes(raw.get("answer_changes"))
    if "ai_evaluation" in raw:
        normalized["ai_evaluation"] = canonicalize_ai_evaluation(raw.get("ai_evaluation"))

    return normalized


def canonicalize_quiz_answers(items: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(_coerce_list(items)):
        normalized_item = canonicalize_quiz_answer(item, question_index=index)
        if normalized_item:
            normalized.append(normalized_item)
    return normalized


def canonicalize_variant_data(
    data: Any,
    *,
    fallback_generated_at: Any = None,
) -> Optional[Dict[str, Any]]:
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    raw = _json_safe_value(data)
    if not isinstance(raw, dict):
        return None

    generated_at = raw.get("generated_at")
    if generated_at is None and fallback_generated_at is not None:
        generated_at = fallback_generated_at

    normalized: Dict[str, Any] = {
        "variant_question": clean_text(raw.get("variant_question") or raw.get("question")),
        "variant_options": normalize_option_map(raw.get("variant_options") or raw.get("options")),
        "variant_answer": clean_text(
            normalize_answer(raw.get("variant_answer") or raw.get("answer"))
            or raw.get("variant_answer")
            or raw.get("answer")
        ),
        "variant_explanation": clean_text(raw.get("variant_explanation") or raw.get("explanation")),
        "transform_type": clean_text(raw.get("transform_type")),
        "core_knowledge": clean_text(raw.get("core_knowledge")),
        "generated_at": to_iso_datetime(generated_at),
    }

    for key, value in raw.items():
        if key in {
            "variant_question",
            "question",
            "variant_options",
            "options",
            "variant_answer",
            "answer",
            "variant_explanation",
            "explanation",
            "transform_type",
            "core_knowledge",
            "generated_at",
        }:
            continue
        normalized[key] = value

    return {
        key: value
        for key, value in normalized.items()
        if value not in (None, [], {}) or key == "variant_options"
    }


def _canonicalize_scoring_criteria(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    normalized: Dict[str, Any] = {}
    for key, item in value.items():
        label = clean_text(key)
        if not label:
            continue
        if isinstance(item, (int, float)) or str(item).strip().lstrip("-").isdigit():
            normalized[label] = _coerce_int(item, default=0)
        else:
            safe_value = _json_safe_value(item)
            if safe_value is not None:
                normalized[label] = safe_value
    return normalized


def _canonicalize_fusion_judgement(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    raw = _json_safe_value(value)
    if not isinstance(raw, dict):
        return None

    normalized: Dict[str, Any] = {
        "verdict": clean_text(raw.get("verdict")),
        "score": max(_coerce_int(raw.get("score"), default=0), 0),
        "feedback": clean_text(raw.get("feedback")),
        "weak_links": canonicalize_string_list(raw.get("weak_links")),
        "judged_at": to_iso_datetime(raw.get("judged_at")),
    }

    for key, item in raw.items():
        if key in {"verdict", "score", "feedback", "weak_links", "judged_at"}:
            continue
        normalized[key] = item

    compact = {key: value for key, value in normalized.items() if value not in (None, [], {})}
    return compact or None


def _canonicalize_fusion_diagnosis_item(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    raw = _json_safe_value(value)
    if not isinstance(raw, dict):
        return None

    normalized: Dict[str, Any] = {
        "diagnosis_type": clean_text(raw.get("diagnosis_type")),
        "affected_parent_ids": canonicalize_parent_ids(
            raw.get("affected_parent_ids") or raw.get("affected_concept_ids")
        ),
        "reflection": clean_text(raw.get("reflection")),
        "analysis": clean_text(raw.get("analysis")),
        "recommendation": clean_text(raw.get("recommendation")),
        "created_at": to_iso_datetime(raw.get("created_at")),
    }

    for key, item in raw.items():
        if key in {
            "diagnosis_type",
            "affected_parent_ids",
            "affected_concept_ids",
            "reflection",
            "analysis",
            "recommendation",
            "created_at",
        }:
            continue
        normalized[key] = item

    compact = {key: value for key, value in normalized.items() if value not in (None, [], {})}
    return compact or None


def canonicalize_fusion_data(data: Any) -> Dict[str, Any]:
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except Exception:
            return {}

    if not isinstance(data, dict):
        return {}

    raw = _json_safe_value(data)
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[str, Any] = {
        "expected_key_points": canonicalize_string_list(raw.get("expected_key_points")),
        "scoring_criteria": _canonicalize_scoring_criteria(raw.get("scoring_criteria")),
        "difficulty_level": clean_text(raw.get("difficulty_level")),
        "parent_key_points": canonicalize_string_list(raw.get("parent_key_points")),
        "judgement_pending": _coerce_bool(raw.get("judgement_pending"), default=False),
        "user_answer_cache": clean_text(raw.get("user_answer_cache")),
        "diagnosis_history": [],
    }

    last_judgement = _canonicalize_fusion_judgement(raw.get("last_judgement"))
    if last_judgement:
        normalized["last_judgement"] = last_judgement

    diagnosis_history: List[Dict[str, Any]] = []
    for item in _coerce_list(raw.get("diagnosis_history")):
        normalized_item = _canonicalize_fusion_diagnosis_item(item)
        if normalized_item:
            diagnosis_history.append(normalized_item)
    normalized["diagnosis_history"] = diagnosis_history

    for key, value in raw.items():
        if key in {
            "expected_key_points",
            "scoring_criteria",
            "difficulty_level",
            "parent_key_points",
            "judgement_pending",
            "user_answer_cache",
            "last_judgement",
            "diagnosis_history",
        }:
            continue
        normalized[key] = value

    return {
        key: value
        for key, value in normalized.items()
        if value not in (None, [], {}) or key in {"expected_key_points", "scoring_criteria", "parent_key_points", "diagnosis_history"}
    }
