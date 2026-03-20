from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class StartSessionResponse(BaseModel):
    session_id: str
    started_at: Optional[str] = None
    message: str


class ActivityRecordedResponse(BaseModel):
    success: bool
    activity_id: int


class QuestionRecordedResponse(BaseModel):
    success: bool
    record_id: int
    updated: bool


class SessionCompletedResponse(BaseModel):
    success: bool
    session_id: str
    score: int
    accuracy: float
    duration: int


class TrackingSessionListItem(BaseModel):
    id: str
    session_type: str
    title: Optional[str] = None
    score: Optional[int] = None
    accuracy: Optional[float] = None
    correct_count: int
    wrong_count: int
    total_questions: int
    sure_count: int
    unsure_count: int
    no_count: int
    duration_seconds: Optional[int] = None
    started_at: Optional[str] = None
    status: Optional[str] = None


class TrackingSessionListResponse(BaseModel):
    total: int
    sessions: List[TrackingSessionListItem]


class TrackingActivityItem(BaseModel):
    type: Optional[str] = None
    name: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[str] = None
    relative_time_ms: int = 0


class TrackingQuestionItem(BaseModel):
    index: int
    type: Optional[str] = None
    difficulty: Optional[str] = None
    question: Optional[str] = None
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: Optional[str] = None
    user_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    confidence: Optional[str] = None
    key_point: Optional[str] = None
    time_spent_seconds: Optional[int] = None
    explanation: Optional[str] = None
    answer_changes: List[Dict[str, Any]] = Field(default_factory=list)


class TrackingSessionDetailResponse(BaseModel):
    id: str
    session_type: str
    title: Optional[str] = None
    description: Optional[str] = None
    score: Optional[int] = None
    accuracy: Optional[float] = None
    total_questions: int
    answered_questions: int
    correct_count: int
    wrong_count: int
    sure_count: int
    unsure_count: int
    no_count: int
    duration_seconds: Optional[int] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: Optional[str] = None
    activities: List[TrackingActivityItem] = Field(default_factory=list)
    questions: List[TrackingQuestionItem] = Field(default_factory=list)


class TrackingReviewDataSession(BaseModel):
    id: str
    session_type: str
    title: Optional[str] = None
    score: Optional[int] = None
    accuracy: Optional[float] = None
    correct_count: int
    wrong_count: int
    total_questions: int
    sure_count: int
    unsure_count: int
    no_count: int
    duration_seconds: Optional[int] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    knowledge_point: Optional[str] = None
    questions: List[TrackingQuestionItem] = Field(default_factory=list)


class TrackingReviewDataResponse(BaseModel):
    sessions: List[TrackingReviewDataSession] = Field(default_factory=list)


class ChallengeQueueItem(BaseModel):
    id: int
    question_text: str
    options: Dict[str, str] = Field(default_factory=dict)
    key_point: Optional[str] = None
    question_type: Optional[str] = None
    difficulty: Optional[str] = None
    severity_tag: Optional[str] = None
    error_count: int = 0
    sm2_interval: int = 0
    sm2_repetitions: int = 0
    next_review_date: Optional[str] = None
    is_overdue: bool
    has_variant: bool


class ChallengeQueuePoolStats(BaseModel):
    critical: int = 0
    core: int = 0
    review: int = 0
    shovel: int = 0
    total: int = 0
    today_answered: int = 0
    target_core: Optional[int] = None
    target_review: Optional[int] = None
    target_shovel: Optional[int] = None


class ChallengeQueueResponse(BaseModel):
    count: int
    date: str
    items: List[ChallengeQueueItem] = Field(default_factory=list)
    pool_stats: ChallengeQueuePoolStats


class ChallengeVariantResponse(BaseModel):
    variant_question: Optional[str] = None
    variant_options: Dict[str, str] = Field(default_factory=dict)
    transform_type: Optional[str] = None
    core_knowledge: Optional[str] = None
    cached: Optional[bool] = None
    error: Optional[str] = None
    fallback: bool = False


class ChallengeCheckAnswerResponse(BaseModel):
    is_correct: bool


class ChallengeSubmitResponse(BaseModel):
    is_correct: bool
    correct_answer: str
    user_answer: str
    confidence: str
    severity_tag: Optional[str] = None
    error_count: int
    retry_count: int
    sm2_ef: Optional[float] = None
    sm2_interval: Optional[int] = None
    sm2_repetitions: Optional[int] = None
    next_review_date: Optional[str] = None
    auto_archived: bool
    can_archive: bool
    explanation: Optional[str] = None
    key_point: Optional[str] = None
    recall_text: str = ""
    variant_explanation: Optional[str] = None
    variant_answer: Optional[str] = None
    core_knowledge: Optional[str] = None


class ChallengeEvaluateRationaleResponse(BaseModel):
    is_correct: bool
    correct_answer: str
    verdict: str
    reasoning_score: int
    diagnosis: str = ""
    weak_links: List[str] = Field(default_factory=list)
    severity_tag: Optional[str] = None
    error_count: int
    retry_count: int
    sm2_ef: Optional[float] = None
    sm2_interval: Optional[int] = None
    sm2_repetitions: Optional[int] = None
    next_review_date: Optional[str] = None
    auto_archived: bool
    variant_explanation: Optional[str] = None
    variant_answer: Optional[str] = None
    core_knowledge: Optional[str] = None
    explanation: Optional[str] = None
    key_point: Optional[str] = None


class ChallengeStatsResponse(BaseModel):
    total_active: int
    overdue_count: int
    today_done: int
    today_correct: int
    today_total: int
    today_accuracy: float
    mastered_count: int


class WrongAnswerTopWeakPoint(BaseModel):
    name: Optional[str] = None
    count: int
    errors: int


class WrongAnswerStatsResponse(BaseModel):
    total_active: int
    total_archived: int
    severity_counts: Dict[str, int] = Field(default_factory=dict)
    top_weak_points: List[WrongAnswerTopWeakPoint] = Field(default_factory=list)
    retry_correct_rate: float
    total_retries: int


class WrongAnswerSeverityDistributionItem(BaseModel):
    count: int = 0
    percent: float = 0.0


class WrongAnswerDashboardOverview(BaseModel):
    active_count: int
    archived_count: int
    total_count: int
    mastery_percent: float
    retry_correct_rate: float
    retry_rate_delta_vs_last_week: float
    streak_days: int
    max_streak_days: int
    active_delta_vs_yesterday: int


class WrongAnswerDashboardToday(BaseModel):
    new_count: int
    archived_count: int
    retried_count: int
    net_change: int
    trend: str


class WrongAnswerDashboardWeek(BaseModel):
    new_count: int
    archived_count: int
    net_change: int


class WrongAnswerReviewPressure(BaseModel):
    today_due: int
    tomorrow_due: int
    week_due: int


class WrongAnswerProjection(BaseModel):
    avg_daily_archived: float
    avg_daily_new: float
    net_daily_rate: float
    estimated_days_to_clear: Optional[int] = None
    estimated_clear_date: Optional[str] = None
    trend_direction: str
    trend_description: str
    projection_message: str


class WrongAnswerTrendItem(BaseModel):
    date: str
    new: int
    archived: int
    net: int


class WrongAnswerWeakChapterItem(BaseModel):
    chapter_id: str = ""
    chapter_name: str
    active_count: int
    critical_count: int
    stubborn_count: int
    mastery_percent: float


class WrongAnswerDashboardResponse(BaseModel):
    overview: WrongAnswerDashboardOverview
    today: WrongAnswerDashboardToday
    this_week: WrongAnswerDashboardWeek
    severity_distribution: Dict[str, WrongAnswerSeverityDistributionItem] = Field(default_factory=dict)
    review_pressure: WrongAnswerReviewPressure
    projection: WrongAnswerProjection
    daily_trend: List[WrongAnswerTrendItem] = Field(default_factory=list)
    weak_chapters: List[WrongAnswerWeakChapterItem] = Field(default_factory=list)


class WrongAnswerListItem(BaseModel):
    id: int
    question_preview: str
    key_point: Optional[str] = None
    question_type: Optional[str] = None
    difficulty: Optional[str] = None
    severity_tag: Optional[str] = None
    error_count: Optional[int] = None
    encounter_count: Optional[int] = None
    retry_count: Optional[int] = None
    last_retry_correct: Optional[bool] = None
    mastery_status: Optional[str] = None
    is_fusion: bool = False
    fusion_level: int = 0
    first_wrong_at: Optional[str] = None
    last_wrong_at: Optional[str] = None
    last_retried_at: Optional[str] = None


class WrongAnswerSeverityListResponse(BaseModel):
    view: Literal["severity"]
    total: int
    page: int
    items: List[WrongAnswerListItem] = Field(default_factory=list)


class WrongAnswerChapterListResponse(BaseModel):
    view: Literal["chapter"]
    total: int
    tree: Dict[str, Any] = Field(default_factory=dict)


class WrongAnswerTimelineListResponse(BaseModel):
    view: Literal["timeline"]
    total: int
    tree: Dict[str, Any] = Field(default_factory=dict)
    current_month: str


class WrongAnswerEmptyListResponse(BaseModel):
    view: str
    total: int
    items: List[Any] = Field(default_factory=list)


class WrongAnswerHistoryItem(BaseModel):
    user_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    confidence: Optional[str] = None
    time_spent_seconds: Optional[int] = None
    answered_at: Optional[str] = None
    session_title: Optional[str] = None


class WrongAnswerRetryHistoryItem(BaseModel):
    user_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    confidence: Optional[str] = None
    time_spent_seconds: Optional[int] = None
    retried_at: Optional[str] = None


class WrongAnswerDetailResponse(BaseModel):
    id: int
    question_text: str
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None
    key_point: Optional[str] = None
    question_type: Optional[str] = None
    difficulty: Optional[str] = None
    severity_tag: Optional[str] = None
    error_count: int
    encounter_count: int
    retry_count: int
    last_retry_correct: Optional[bool] = None
    mastery_status: Optional[str] = None
    has_variant: bool
    sm2_ef: Optional[float] = None
    sm2_interval: Optional[int] = None
    sm2_repetitions: Optional[int] = None
    next_review_date: Optional[str] = None
    first_wrong_at: Optional[str] = None
    last_wrong_at: Optional[str] = None
    history: List[WrongAnswerHistoryItem] = Field(default_factory=list)
    retries: List[WrongAnswerRetryHistoryItem] = Field(default_factory=list)


class WrongAnswerPreviousAttempt(BaseModel):
    user_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    confidence: Optional[str] = None
    retried_at: Optional[str] = None


class WrongAnswerRetryResponse(BaseModel):
    is_correct: bool
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None
    key_point: Optional[str] = None
    can_archive: bool
    auto_archived: bool
    severity_tag: Optional[str] = None
    error_count: int
    retry_count: int
    recall_text: str = ""
    sm2_ef: Optional[float] = None
    sm2_interval: Optional[int] = None
    sm2_repetitions: Optional[int] = None
    next_review_date: Optional[str] = None
    variant_answer: Optional[str] = None
    variant_explanation: Optional[str] = None
    previous_attempts: List[WrongAnswerPreviousAttempt] = Field(default_factory=list)


class WrongAnswerMutationResponse(BaseModel):
    success: bool
    id: int
    status: str


class WrongAnswerRetryBatchItem(BaseModel):
    id: int
    question_text: str
    options: Dict[str, str] = Field(default_factory=dict)
    question_type: Optional[str] = None
    difficulty: Optional[str] = None
    severity_tag: Optional[str] = None
    error_count: int
    key_point: Optional[str] = None


class WrongAnswerRetryBatchResponse(BaseModel):
    count: int
    items: List[WrongAnswerRetryBatchItem] = Field(default_factory=list)


class BooksResponse(BaseModel):
    books: List[str] = Field(default_factory=list)


class MarkdownExportResponse(BaseModel):
    content: str
    format: str
    total: Optional[int] = None


class WrongAnswerSyncResponse(BaseModel):
    created: int
    updated: int
    total_active: int


class RecognizeChaptersResponse(BaseModel):
    success: bool
    message: str
    total: int
    recognized: int
    failed: int
    normalized: int
    remaining: int
    process_all: bool


class WrongAnswerVariantGenerateResponse(BaseModel):
    variant_question: Optional[str] = None
    variant_options: Dict[str, str] = Field(default_factory=dict)
    variant_answer: Optional[str] = None
    transform_type: Optional[str] = None
    core_knowledge: Optional[str] = None
    cached: bool = False


class WrongAnswerVariantJudgeResponse(BaseModel):
    is_correct: bool
    variant_answer: Optional[str] = None
    variant_explanation: Optional[str] = None
    verdict: str
    reasoning_score: int
    diagnosis: str = ""
    weak_links: List[str] = Field(default_factory=list)
    can_archive: bool
    auto_archived: bool
    severity_tag: Optional[str] = None
    error_count: int
    retry_count: int
    sm2_ef: Optional[float] = None
    sm2_interval: Optional[int] = None
    sm2_repetitions: Optional[int] = None
    next_review_date: Optional[str] = None


class QuizQuestionPayload(BaseModel):
    question_id: str
    concept_id: str = ""
    question: str
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None
    is_wrong_answer: bool = False
    wrong_answer_id: Optional[int] = None
    test_id: Optional[int] = None
    concept_name: Optional[str] = None
    difficulty: Optional[str] = None
    key_points: List[str] = Field(default_factory=list)
    common_mistakes: List[str] = Field(default_factory=list)


class QuizAnswerPayload(BaseModel):
    question_index: int
    user_answer: Optional[str] = None
    is_correct: bool
    time_spent: int = 0
    confidence: Optional[str] = None
    test_id: Optional[int] = None
    correct_answer: Optional[str] = None
    score: Optional[int] = None
    feedback: Optional[str] = None
    explanation: Optional[str] = None
    weak_points: List[str] = Field(default_factory=list)
    error_type: Optional[str] = None
    confidence_analysis: Optional[str] = None


class QuizSessionStartResponse(BaseModel):
    session_id: int
    total_questions: int
    questions: List[QuizQuestionPayload] = Field(default_factory=list)
    mode: Optional[str] = None
    generation_method: Optional[str] = None


class QuizSessionSubmitResponse(BaseModel):
    session_id: int
    score: int
    correct_count: int
    wrong_count: int
    answers: List[QuizAnswerPayload] = Field(default_factory=list)
    ai_analysis: Optional[Dict[str, Any]] = None


class QuizAnalysisResponse(BaseModel):
    session_id: int
    score: int
    analysis: Dict[str, Any] = Field(default_factory=dict)


class LegacyWrongAnswerItem(BaseModel):
    id: int
    concept_id: str
    question: str
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: Optional[str] = None
    user_answer: Optional[str] = None
    explanation: Optional[str] = None
    error_type: Optional[str] = None
    review_count: int = 0
    mastery_level: int = 0
    is_mastered: bool = False
    next_review: Optional[str] = None
    created_at: Optional[str] = None


class LegacyWrongAnswerListResponse(BaseModel):
    total: int
    chapter_id: str
    wrong_answers: List[LegacyWrongAnswerItem] = Field(default_factory=list)


class LegacyWrongAnswerReviewResponse(BaseModel):
    id: int
    mastery_level: int
    is_mastered: bool
    next_review: Optional[str] = None
    review_count: int


class LegacyQuizStatsResponse(BaseModel):
    total_sessions: int
    wrong_answer_count: int
    due_for_review: int


class BatchExamQuestionItem(BaseModel):
    id: str
    type: Optional[str] = None
    difficulty: Optional[str] = None
    question: str
    options: Dict[str, str] = Field(default_factory=dict)
    key_point: Optional[str] = None
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None


class BatchExamConfirmChapterResponse(BaseModel):
    success: bool
    chapter_id: str


class BatchExamGenerateResponse(BaseModel):
    exam_id: str
    paper_title: str
    total_questions: int
    difficulty_distribution: Dict[str, int] = Field(default_factory=dict)
    chapter_prediction: Dict[str, Any] = Field(default_factory=dict)
    questions: List[BatchExamQuestionItem] = Field(default_factory=list)
    knowledge_points: List[str] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)


class BatchExamFuzzyOptionItem(BaseModel):
    options: List[str] = Field(default_factory=list)
    option_texts: Dict[str, str] = Field(default_factory=dict)
    key_point: Optional[str] = None


class BatchExamSubmitDetailItem(BaseModel):
    id: int
    type: Optional[str] = None
    difficulty: Optional[str] = None
    user_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    is_correct: bool
    confidence: Optional[str] = None
    explanation: Optional[str] = None
    key_point: Optional[str] = None
    related_questions: Optional[str] = None


class BatchExamSubmitResponse(BaseModel):
    score: int
    correct_count: int
    wrong_count: int
    total: int
    wrong_by_difficulty: Dict[str, int] = Field(default_factory=dict)
    confidence_analysis: Dict[str, int] = Field(default_factory=dict)
    details: List[BatchExamSubmitDetailItem] = Field(default_factory=list)
    weak_points: List[str] = Field(default_factory=list)
    analysis: str
    fuzzy_options: Dict[str, BatchExamFuzzyOptionItem] = Field(default_factory=dict)


class BatchExamSessionResponse(BaseModel):
    exam_id: str
    questions: List[BatchExamQuestionItem] = Field(default_factory=list)
    num_questions: int


class BatchExamDetailKnowledgePointStat(BaseModel):
    key_point: str
    error_count: int = 0
    severity_tag: str = ""
    severity_weight: int = 0
    understanding: float = 0.0
    mastery_penalty: float = 0.0
    priority_score: float = 0.0
    original_order: int = 0
    practice_session_count: int = 0
    last_practiced_at: Optional[str] = None


class BatchExamDetailResponse(BaseModel):
    exam_id: str
    chapter_id: str = ""
    questions: List[BatchExamQuestionItem] = Field(default_factory=list)
    knowledge_points: List[str] = Field(default_factory=list)
    knowledge_point_stats: Dict[str, BatchExamDetailKnowledgePointStat] = Field(default_factory=dict)
    fuzzy_options: Dict[str, BatchExamFuzzyOptionItem] = Field(default_factory=dict)
    num_questions: int
    uploadedContent: str = ""


class BatchVariationItem(BaseModel):
    id: int
    type: Optional[str] = None
    difficulty: Optional[str] = None
    variation_type: Optional[str] = None
    question: str
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None


class BatchVariationResponse(BaseModel):
    variations: List[BatchVariationItem] = Field(default_factory=list)
    error: Optional[str] = None
    is_fallback: bool = False


class TrackingStatsSummary(BaseModel):
    total_sessions: int
    total_questions: int
    total_correct: int
    avg_accuracy: float
    total_duration: int
    sure_count: int
    unsure_count: int
    no_count: int


class TrackingDistributionMetric(BaseModel):
    count: int
    pct: float
    correct: int
    accuracy: float


class TrackingKnowledgePointMetric(BaseModel):
    total: int
    correct: int
    wrong: int
    avg_confidence: float = 0.0
    has_confidence_data: bool = False


class TrackingDailyMetric(BaseModel):
    questions: int = 0
    correct: int = 0
    sessions: int = 0
    duration: int = 0


class TrackingSessionQuestionSummary(BaseModel):
    key_point: str
    is_correct: bool
    confidence: Optional[str] = None
    time_spent_seconds: int = 0
    answer_changes: List[Dict[str, Any]] = Field(default_factory=list)
    question_type: str
    difficulty: str


class TrackingStatsSessionItem(BaseModel):
    id: str
    session_type: str
    title: Optional[str] = None
    score: Optional[int] = None
    accuracy: Optional[float] = None
    correct_count: int
    wrong_count: int
    total_questions: int
    sure_count: int
    unsure_count: int
    no_count: int
    duration_seconds: Optional[int] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: Optional[str] = None
    knowledge_point: Optional[str] = None
    chapter_id: Optional[str] = None
    question_details: List[TrackingSessionQuestionSummary] = Field(default_factory=list)


class TrackingWowDelta(BaseModel):
    current_accuracy: Optional[float] = None
    previous_accuracy: Optional[float] = None
    delta: Optional[float] = None
    direction: str


class TrackingWeakestArea(BaseModel):
    name: str
    accuracy: float
    total: int
    correct: int


class TrackingStatsResponse(BaseModel):
    period: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    summary: TrackingStatsSummary
    type_distribution: Dict[str, TrackingDistributionMetric] = Field(default_factory=dict)
    difficulty_distribution: Dict[str, TrackingDistributionMetric] = Field(default_factory=dict)
    knowledge_points: Dict[str, TrackingKnowledgePointMetric] = Field(default_factory=dict)
    daily_trend: Dict[str, TrackingDailyMetric] = Field(default_factory=dict)
    sessions: List[TrackingStatsSessionItem] = Field(default_factory=list)
    wow_delta: Optional[TrackingWowDelta] = None
    weakest_area: Optional[TrackingWeakestArea] = None


class TrackingConfidenceDistributionItem(BaseModel):
    key: str
    label: str
    count: int
    pct: float


class TrackingSessionTypeDistributionItem(BaseModel):
    key: str
    label: str
    count: int
    pct: float


class TrackingWeakPointItem(BaseModel):
    name: str
    total: int
    correct: int
    wrong: int
    accuracy: float
    avg_confidence: float
    confidence_level: str


class TrackingRecentSessionItem(BaseModel):
    id: str
    title: Optional[str] = None
    session_type: Optional[str] = None
    accuracy: Optional[float] = None
    correct_count: int
    wrong_count: int
    total_questions: int
    duration_seconds: int = 0
    started_at: Optional[str] = None
    status: Optional[str] = None


class TrackingProgressTrendPoint(BaseModel):
    date: str
    questions: int = 0
    correct: int = 0
    sessions: int = 0
    duration_seconds: int = 0
    accuracy: float = 0.0


class TrackingProgressBoardOverview(BaseModel):
    total_sessions: int
    total_questions: int
    total_correct: int
    total_wrong: int
    avg_accuracy: float
    total_duration_seconds: int
    total_duration_hours: float


class TrackingProgressBoardResponse(BaseModel):
    period: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    generated_at: str
    overview: TrackingProgressBoardOverview
    confidence_distribution: List[TrackingConfidenceDistributionItem] = Field(default_factory=list)
    session_type_distribution: List[TrackingSessionTypeDistributionItem] = Field(default_factory=list)
    daily_trend_7: List[TrackingProgressTrendPoint] = Field(default_factory=list)
    daily_trend_30: List[TrackingProgressTrendPoint] = Field(default_factory=list)
    weak_points: List[TrackingWeakPointItem] = Field(default_factory=list)
    recent_sessions: List[TrackingRecentSessionItem] = Field(default_factory=list)
    wow_delta: Optional[TrackingWowDelta] = None
    weakest_area: Optional[TrackingWeakestArea] = None


class KnowledgeArchiveQuestionItem(BaseModel):
    question: Optional[str] = None
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: Optional[str] = None
    user_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    confidence: Optional[str] = None
    difficulty: Optional[str] = None
    question_type: Optional[str] = None
    explanation: Optional[str] = None
    key_point: Optional[str] = None
    time_spent_seconds: Optional[int] = None
    answered_at: Optional[str] = None
    session_type: Optional[str] = None
    session_title: Optional[str] = None


class KnowledgeArchivePointItem(BaseModel):
    name: str
    total: int
    correct: int
    wrong: int
    error_rate: float
    accuracy: float
    questions: List[KnowledgeArchiveQuestionItem] = Field(default_factory=list)


class KnowledgeArchiveResponse(BaseModel):
    total_knowledge_points: int
    total_questions: int
    knowledge_points: List[KnowledgeArchivePointItem] = Field(default_factory=list)


class TrackingDailyLogItem(BaseModel):
    date: str
    total_sessions: int
    total_questions: int
    accuracy: float
    average_score: Optional[float] = None
    duration_minutes: int
    knowledge_points: int
    weak_points: List[str] = Field(default_factory=list)


class TrackingDailyLogsResponse(BaseModel):
    logs: List[TrackingDailyLogItem] = Field(default_factory=list)


class KnowledgeTreeKeyPointItem(BaseModel):
    name: str
    total: int
    correct: int
    wrong: int
    accuracy: float
    dominant_error_type: Optional[str] = None


class KnowledgeTreeChapterItem(BaseModel):
    name: str
    key_points: List[KnowledgeTreeKeyPointItem] = Field(default_factory=list)
    total: int
    correct: int
    accuracy: float


class KnowledgeTreeBookItem(BaseModel):
    name: str
    chapters: List[KnowledgeTreeChapterItem] = Field(default_factory=list)
    total: int
    correct: int
    accuracy: float


class KnowledgeTreeResponse(BaseModel):
    tree: List[KnowledgeTreeBookItem] = Field(default_factory=list)


class HistoryUploadItem(BaseModel):
    id: int
    date: str
    recorded_at: Optional[str] = None
    book: str
    chapter_title: str
    chapter_id: str = ""
    concept_count: int
    summary: str = ""
    main_topic: str = ""
    source_type: str = "upload"
    source_label: str = ""


class HistoryUploadResponse(BaseModel):
    total: int
    days: int
    active_days: int = 0
    average_uploads_per_active_day: float = 0.0
    peak_date: Optional[str] = None
    peak_count: int = 0
    uploads: List[HistoryUploadItem] = Field(default_factory=list)


class HistoryLearningStatsResponse(BaseModel):
    total_uploads: int
    weekly_uploads: int
    latest_study_date: Optional[str] = None
    streak_days: int = 0
    active_days: int = 0
    average_uploads_per_active_day: float = 0.0
    busiest_day: Optional[str] = None
    busiest_day_count: int = 0
    book_distribution: Dict[str, int] = Field(default_factory=dict)
    source_distribution: Dict[str, int] = Field(default_factory=dict)


class HistoryTimelineDay(BaseModel):
    date: str
    has_study: bool
    upload_count: int


class HistoryTimelineResponse(BaseModel):
    days: int
    timeline: List[HistoryTimelineDay] = Field(default_factory=list)


class HistoryReviewTaskSummary(BaseModel):
    task_id: int
    chapter_id: str
    book: str
    chapter_title: str
    unit_id: int
    unit_title: str
    unit_index: int
    excerpt: str = ""
    summary: str = ""
    estimated_minutes: int = 0
    due_reason: str
    mastery_status: str
    next_round: int = 1
    answered_count: int = 0
    question_count: int = 0
    remaining_questions: int = 0
    resume_position: int = 0
    scheduled_for: str
    carry_over_days: int = 0
    status: str
    ai_recommended_status: Optional[str] = None
    user_selected_status: Optional[str] = None
    grading_score: Optional[float] = None


class HistoryReviewQuestionItem(BaseModel):
    id: int
    position: int
    prompt: str
    reference_answer: str
    key_points: List[str] = Field(default_factory=list)
    explanation: str = ""
    source_excerpt: str = ""
    user_answer: str = ""
    ai_score: Optional[int] = None
    ai_feedback: str = ""
    good_points: List[str] = Field(default_factory=list)
    missing_points: List[str] = Field(default_factory=list)
    improvement_suggestion: str = ""


class HistoryReviewTaskDetailResponse(HistoryReviewTaskSummary):
    content_version: int = 1
    source_content: str = ""
    questions: List[HistoryReviewQuestionItem] = Field(default_factory=list)
    overall_feedback: str = ""


class HistoryReviewPlanResponse(BaseModel):
    date: str
    time_budget_minutes: int = 0
    estimated_total_minutes: int = 0
    remaining_minutes: int = 0
    task_count: int = 0
    carry_over_count: int = 0
    completed_today_count: int = 0
    tasks: List[HistoryReviewTaskSummary] = Field(default_factory=list)


class DashboardWeeklyTrendItem(BaseModel):
    date: str
    new: int
    eliminated: int
    net: int


class DashboardStatsResponse(BaseModel):
    today_eliminated: int
    today_retried: int
    avg_new_per_day: float
    current_backlog: int
    estimated_days_to_clear: Optional[float] = None
    daily_required_reviews: int
    can_clear: bool
    clear_message: str
    severity_counts: Dict[str, int] = Field(default_factory=dict)
    weekly_trend: List[DashboardWeeklyTrendItem] = Field(default_factory=list)
    daily_planned_review: int
    net_daily_progress: float
    calculated_at: str


class QuizVariationQuestionItem(BaseModel):
    id: Union[int, str]
    type: Optional[str] = None
    question: str
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None


class QuizVariationGenerateResponse(BaseModel):
    questions: List[QuizVariationQuestionItem] = Field(default_factory=list)


class GraphLinkMutationResponse(BaseModel):
    status: str
    message: str


class ExternalImportPreviewItem(BaseModel):
    question_no: Optional[Union[int, str]] = None
    question_text: str
    options: Dict[str, str] = Field(default_factory=dict)
    correct_answer: str
    chapter_name: str = ""
    chapter_id: Optional[str] = None
    book_name: str = ""
    fingerprint: str
    exists: bool = False
    existing_wrong_id: Optional[int] = None
    chapter_label: Optional[str] = None


class ExternalImportParseResponse(BaseModel):
    source_name: str
    book_name: str = ""
    chapter_name: str = ""
    total_parsed: int
    total_valid: int
    duplicate_count: int
    new_count: int
    items: List[ExternalImportPreviewItem] = Field(default_factory=list)


class ExternalImportConfirmError(BaseModel):
    index: int
    reason: str


class ExternalImportConfirmResponse(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: List[ExternalImportConfirmError] = Field(default_factory=list)
    created_ids: List[int] = Field(default_factory=list)
    message: str


class OcrPlanOverview(BaseModel):
    total_plan_days: int
    covered_months: int
    month_list: List[int] = Field(default_factory=list)
    first_plan_day: Optional[str] = None
    last_plan_day: Optional[str] = None
    live_days: int
    no_live_days: int
    quiz_days: int
    review_days: int
    preview_days: int
    rolling_days: int
    exam_days: int
    topic_count: int
    busiest_month: Optional[int] = None


class OcrTimelineProgress(BaseModel):
    today: str
    past_days: int
    total_days: int
    progress_pct: float
    next_plan_day: Optional[str] = None


class OcrMonthSummaryItem(BaseModel):
    month: int
    plan_days: int
    live_days: int
    review_days: int
    quiz_days: int
    rolling_days: int
    exam_days: int
    preview_days: int
    live_ratio: float
    quiz_ratio: float
    review_ratio: float


class OcrMasterPlanSummary(BaseModel):
    total_items: int
    completed_items: int
    active_items: int
    pending_items: int
    completion_pct: float


class OcrMasterPlanNextItem(BaseModel):
    id: str
    stage: str
    type: str
    title: str
    status: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    date_iso: Optional[str] = None
    sort_key: str


class OcrMasterPlanPhase(BaseModel):
    id: str
    stage: str
    title: str
    details: str = ""
    type: str
    start: str
    end: str
    start_date: str
    end_date: str
    total_days: int
    status: str
    progress_pct: float


class OcrMasterPlanMilestone(BaseModel):
    id: str
    stage: str
    title: str
    type: str
    date: str
    date_iso: str
    status: str
    days_delta: int


class OcrMasterPlan(BaseModel):
    plan_year: int
    today: str
    summary: OcrMasterPlanSummary
    next_item: Optional[OcrMasterPlanNextItem] = None
    phases: List[OcrMasterPlanPhase] = Field(default_factory=list)
    milestones: List[OcrMasterPlanMilestone] = Field(default_factory=list)


class OcrTimelineEntry(BaseModel):
    entry_id: str
    month: int
    day: int
    date_key: str
    display_date: str
    filename: str
    title: str
    live_status: str
    categories: Dict[str, bool] = Field(default_factory=dict)
    focus_topics: List[str] = Field(default_factory=list)
    preview: str
    line_count: int
    updated_at: str


class OcrSpecialDocItem(BaseModel):
    name: str
    month: Optional[int] = None
    day: Optional[int] = None
    date_key: Optional[str] = None
    preview: str = ""
    updated_at: str


class OcrPlanBoardResponse(BaseModel):
    source_dir: str
    generated_at: str
    plan_year: int
    overview: OcrPlanOverview
    timeline_progress: OcrTimelineProgress
    category_totals: Dict[str, int] = Field(default_factory=dict)
    month_summary: List[OcrMonthSummaryItem] = Field(default_factory=list)
    master_plan: OcrMasterPlan
    timeline: List[OcrTimelineEntry] = Field(default_factory=list)
    special_docs: List[OcrSpecialDocItem] = Field(default_factory=list)
