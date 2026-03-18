from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, inspect
from sqlalchemy.orm import Session

from agent_models import AgentActionLog, AgentSession
from learning_tracking_models import (
    DailyReviewPaper,
    DailyReviewPaperItem,
    LearningSession,
    QuestionRecord,
    WrongAnswerV2,
)
from models import Chapter, ConceptMastery, QuizSession, TestRecord, engine
from routers.wrong_answers_v2 import (
    ReviewCandidate,
    _build_daily_review_config,
    _candidate_from_wrong_answer,
    _get_recent_daily_review_stems,
    _select_daily_review_candidates,
    _sort_due_candidates,
    _sort_supplement_candidates,
)
from services.data_identity import (
    build_actor_key_aliases,
    build_actor_key,
    ensure_learning_identity_schema,
    resolve_actor_identity,
    resolve_query_identity,
)
from utils.agent_contracts import (
    AgentActionExecuteRequest,
    AgentActionExecuteResponse,
    AgentActionListResponse,
    AgentActionLogItem,
    AgentToolDefinition,
)
from utils.data_contracts import canonicalize_quiz_answers, canonicalize_quiz_questions, normalize_confidence, normalize_option_map


class AgentActionNotFoundError(LookupError):
    pass


class _ActionArgsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateDailyReviewPaperArgs(_ActionArgsModel):
    paper_date: Optional[date] = None
    wrong_answer_ids: List[int] = Field(default_factory=list, max_length=20)
    target_count: int = Field(default=10, ge=1, le=20)
    allow_replace: bool = True


class UpdateWrongAnswerStatusArgs(_ActionArgsModel):
    wrong_answer_ids: List[int] = Field(min_length=1, max_length=20)
    target_status: Literal["active", "archived", "mastered"] = "archived"
    reason: str = Field(default="", max_length=500)


class UpdateConceptMasteryArgs(_ActionArgsModel):
    concept_ids: List[str] = Field(min_length=1, max_length=12)
    review_in_days: Optional[int] = Field(default=None, ge=0, le=30)
    reason: str = Field(default="", max_length=500)


class GenerateQuizSetArgs(_ActionArgsModel):
    concept_ids: List[str] = Field(min_length=1, max_length=8)
    target_count: int = Field(default=6, ge=1, le=20)
    session_type: Literal["practice", "chapter_test", "wrong_answer_review"] = "practice"
    title: str = Field(default="", max_length=120)


class LogAgentDecisionArgs(_ActionArgsModel):
    decision_type: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(default="", max_length=4000)
    related_turn_id: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


ACTION_TOOL_DEFINITIONS: List[AgentToolDefinition] = [
    AgentToolDefinition(
        name="create_daily_review_paper",
        description="根据当前 active 错题生成每日复习卷，并写入 daily_review_papers / items。",
        default_limit=1,
        keywords=["复习卷", "每日复习", "组卷", "错题卷"],
        tool_type="write",
        risk_level="medium",
        requires_confirmation=True,
    ),
    AgentToolDefinition(
        name="update_wrong_answer_status",
        description="批量更新错题状态，支持 active / archived；mastered 会映射为 archived。",
        default_limit=20,
        keywords=["归档错题", "恢复错题", "错题状态", "mastered", "archived"],
        tool_type="write",
        risk_level="medium",
        requires_confirmation=True,
    ),
    AgentToolDefinition(
        name="update_concept_mastery",
        description="根据近期做题和错题数据，批量回写 ConceptMastery 掌握度与下次复习时间。",
        default_limit=12,
        keywords=["知识点掌握", "掌握度", "concept mastery", "复习排期"],
        tool_type="write",
        risk_level="medium",
        requires_confirmation=True,
    ),
    AgentToolDefinition(
        name="generate_quiz_set",
        description="围绕指定知识点生成题目集，并写入 QuizSession + LearningSession + QuestionRecord。",
        default_limit=8,
        keywords=["生成题目", "题组", "quiz set", "巩固练习"],
        tool_type="write",
        risk_level="high",
        requires_confirmation=True,
    ),
    AgentToolDefinition(
        name="log_agent_decision",
        description="写入 Agent 决策日志，沉淀本轮规划、判断依据和执行说明。",
        default_limit=1,
        keywords=["决策记录", "规划日志", "执行依据", "审计"],
        tool_type="write",
        risk_level="low",
        requires_confirmation=False,
    ),
]

_ACTION_TOOL_MAP = {tool.name: tool for tool in ACTION_TOOL_DEFINITIONS}
_ROLLBACK_SUPPORTED_TOOLS = {
    "create_daily_review_paper",
    "update_wrong_answer_status",
    "update_concept_mastery",
    "generate_quiz_set",
}


@dataclass
class ActionPreparation:
    normalized_args: Dict[str, Any]
    preview_summary: str
    context: Dict[str, Any]


@dataclass
class ActionExecutionResult:
    affected_ids: List[Any]
    result: Dict[str, Any]
    verification_status: str
    error_message: Optional[str] = None


def ensure_agent_action_schema() -> None:
    AgentActionLog.__table__.create(bind=engine, checkfirst=True)
    existing_columns = {
        str(column.get("name") or "").lower()
        for column in inspect(engine).get_columns("agent_action_logs")
    }
    if "preview_context" not in existing_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE agent_action_logs ADD COLUMN preview_context JSON")
    if "related_task_id" not in existing_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE agent_action_logs ADD COLUMN related_task_id VARCHAR")


def _serialize_action_preview_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return _jsonify_action_preview_value(context)


def _deserialize_action_preview_context(tool_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
    restored = dict(context or {})
    if tool_name == "create_daily_review_paper":
        restored["paper_date"] = _parse_iso_date(restored.get("paper_date"))
        restored["selected_candidates"] = [
            _deserialize_review_candidate(item)
            for item in (restored.get("selected_candidates") or [])
        ]
    return restored


def _deserialize_review_candidate(payload: Any) -> ReviewCandidate:
    item = dict(payload or {})
    return ReviewCandidate(
        wrong_answer_id=int(item.get("wrong_answer_id") or 0),
        stem_fingerprint=str(item.get("stem_fingerprint") or ""),
        normalized_stem=str(item.get("normalized_stem") or ""),
        source_bucket=str(item.get("source_bucket") or "supplement"),
        next_review_date=_parse_iso_date(item.get("next_review_date")),
        severity_tag=str(item.get("severity_tag") or ""),
        question_type=str(item.get("question_type") or ""),
        difficulty=str(item.get("difficulty") or ""),
        knowledge_key=str(item.get("knowledge_key") or ""),
        is_multi=bool(item.get("is_multi")),
        is_hard=bool(item.get("is_hard")),
        error_count=int(item.get("error_count") or 0),
        first_wrong_at=_parse_iso_datetime(item.get("first_wrong_at")),
        last_wrong_at=_parse_iso_datetime(item.get("last_wrong_at")),
        recently_used=bool(item.get("recently_used")),
        snapshot=dict(item.get("snapshot") or {}),
    )


def _jsonify_action_preview_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonify_action_preview_value(value.model_dump(mode="json"))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return _jsonify_action_preview_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonify_action_preview_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify_action_preview_value(item) for item in value]
    return value


def list_action_tool_definitions() -> List[AgentToolDefinition]:
    return ACTION_TOOL_DEFINITIONS


def list_session_actions(db: Session, session_id: str, limit: int = 50) -> List[AgentActionLog]:
    ensure_agent_action_schema()
    return (
        db.query(AgentActionLog)
        .filter(AgentActionLog.session_id == session_id)
        .order_by(desc(AgentActionLog.created_at), desc(AgentActionLog.id))
        .limit(limit)
        .all()
    )


def list_task_actions(db: Session, task_id: str, limit: int = 20) -> List[AgentActionLog]:
    ensure_agent_action_schema()
    return (
        db.query(AgentActionLog)
        .filter(AgentActionLog.related_task_id == task_id)
        .order_by(desc(AgentActionLog.updated_at), desc(AgentActionLog.created_at), desc(AgentActionLog.id))
        .limit(limit)
        .all()
    )


def serialize_action_log(action_log: AgentActionLog) -> AgentActionLogItem:
    can_rollback = (
        action_log.tool_name in _ROLLBACK_SUPPORTED_TOOLS
        and action_log.execution_status == "success"
        and action_log.approval_status in {"auto", "approved"}
    )
    return AgentActionLogItem(
        id=action_log.id,
        session_id=action_log.session_id,
        related_task_id=action_log.related_task_id,
        user_id=action_log.user_id,
        device_id=action_log.device_id,
        tool_name=action_log.tool_name,
        tool_type=action_log.tool_type,
        tool_args=action_log.tool_args or {},
        risk_level=action_log.risk_level,
        approval_status=action_log.approval_status,
        execution_status=action_log.execution_status,
        triggered_by=action_log.triggered_by,
        preview_summary=action_log.preview_summary,
        affected_ids=list(action_log.affected_ids or []),
        result=action_log.result or {},
        verification_status=action_log.verification_status,
        error_message=action_log.error_message,
        can_rollback=can_rollback,
        rollback_hint=_action_rollback_hint(action_log.tool_name) if can_rollback else None,
        confirmed_at=_iso_datetime(action_log.confirmed_at),
        executed_at=_iso_datetime(action_log.executed_at),
        created_at=_iso_datetime(action_log.created_at) or datetime.now().isoformat(),
        updated_at=_iso_datetime(action_log.updated_at) or datetime.now().isoformat(),
    )


def serialize_action_list(actions: List[AgentActionLog]) -> AgentActionListResponse:
    return AgentActionListResponse(total=len(actions), actions=[serialize_action_log(item) for item in actions])


def _action_rollback_hint(tool_name: str) -> str:
    if tool_name == "create_daily_review_paper":
        return "撤销当前复习卷写入，恢复到动作执行前的题卷状态。"
    if tool_name == "update_wrong_answer_status":
        return "把这批错题的状态恢复到动作执行前。"
    if tool_name == "update_concept_mastery":
        return "把知识点掌握度和复习日期恢复到动作执行前。"
    if tool_name == "generate_quiz_set":
        return "删除这次生成的题组、学习会话和题目记录。"
    return ""


def _resolve_related_task_for_action(
    db: Session,
    *,
    session: AgentSession,
    payload: AgentActionExecuteRequest,
    action_log: AgentActionLog | None,
    tool_name: str,
):
    from services.agent_tasks import get_task_or_none, resolve_task_for_action

    if action_log and action_log.related_task_id:
        return get_task_or_none(db, action_log.related_task_id)
    return resolve_task_for_action(
        db,
        session=session,
        task_id=payload.task_id,
        tool_name=tool_name,
    )


def execute_agent_action(
    db: Session,
    session: AgentSession,
    payload: AgentActionExecuteRequest,
) -> AgentActionExecuteResponse:
    ensure_learning_identity_schema()
    ensure_agent_action_schema()

    action_log = _load_action_log(db, session.id, payload.action_id)
    if payload.rollback:
        if action_log is None:
            raise ValueError("回滚动作需要提供 action_id")
        if payload.tool_name and payload.tool_name != action_log.tool_name:
            raise ValueError("action_id 与 tool_name 不匹配")
        return _rollback_agent_action(db, session=session, action_log=action_log)

    if action_log and action_log.execution_status in {"success", "failed", "rolled_back"}:
        return AgentActionExecuteResponse(
            action=serialize_action_log(action_log),
            executed=action_log.execution_status == "success",
            requires_confirmation=bool(
                _ACTION_TOOL_MAP.get(action_log.tool_name)
                and _ACTION_TOOL_MAP[action_log.tool_name].requires_confirmation
                and action_log.approval_status == "pending"
            ),
            preview_summary=action_log.preview_summary,
        )

    tool_name = payload.tool_name or (action_log.tool_name if action_log else None)
    if not tool_name:
        raise ValueError("缺少 tool_name")

    tool_definition = _ACTION_TOOL_MAP.get(tool_name)
    if tool_definition is None:
        raise ValueError(f"不支持的动作工具: {tool_name}")

    if action_log and action_log.tool_name != tool_name:
        raise ValueError("action_id 与 tool_name 不匹配")

    related_task = _resolve_related_task_for_action(
        db,
        session=session,
        payload=payload,
        action_log=action_log,
        tool_name=tool_name,
    )

    args_model = _action_args_model(tool_name)
    resolved_args = _resolve_action_args(
        args_model,
        action_log=action_log,
        payload=payload,
    )

    preview = _prepare_action(
        tool_name,
        db,
        resolved_args,
        user_id=payload.user_id or session.user_id,
        device_id=payload.device_id or session.device_id,
    )
    serialized_preview_context = _serialize_action_preview_context(preview.context)
    execution_context = _deserialize_action_preview_context(tool_name, serialized_preview_context)
    if action_log and tool_definition.requires_confirmation and payload.confirm:
        stored_preview_context = action_log.preview_context or {}
        if stored_preview_context and serialized_preview_context != stored_preview_context:
            raise ValueError("预览结果已变化，请重新预览后再确认")
        execution_context = _deserialize_action_preview_context(
            tool_name,
            stored_preview_context or serialized_preview_context,
        )

    requires_confirmation = tool_definition.requires_confirmation
    action_log = _upsert_action_log(
        db,
        session=session,
        payload=payload,
        tool_definition=tool_definition,
        normalized_args=preview.normalized_args,
        preview_summary=preview.preview_summary,
        preview_context=serialized_preview_context,
        action_log=action_log,
        pending_confirmation=requires_confirmation and not payload.confirm,
        related_task_id=related_task.id if related_task is not None else None,
    )

    if requires_confirmation and not payload.confirm:
        if related_task is not None:
            from services.agent_tasks import sync_task_after_action_preview

            sync_task_after_action_preview(
                db,
                task=related_task,
                action_log=action_log,
                auto_commit=False,
            )
        db.commit()
        db.refresh(action_log)
        return AgentActionExecuteResponse(
            action=serialize_action_log(action_log),
            executed=False,
            requires_confirmation=True,
            preview_summary=preview.preview_summary,
        )

    db.commit()
    db.refresh(action_log)

    try:
        stored_args = args_model.model_validate(action_log.tool_args or {})
        execution = _execute_action(
            tool_name,
            db,
            stored_args,
            execution_context,
            action_log=action_log,
        )
        action_log.affected_ids = execution.affected_ids
        action_log.result = execution.result
        action_log.verification_status = execution.verification_status
        action_log.error_message = execution.error_message
        action_log.execution_status = "success" if execution.verification_status == "verified" else "failed"
        action_log.executed_at = datetime.now()
        if related_task is not None:
            from services.agent_tasks import sync_task_after_action_execution

            sync_task_after_action_execution(
                db,
                task=related_task,
                action_log=action_log,
                auto_commit=False,
            )
        db.commit()
        db.refresh(action_log)
        return AgentActionExecuteResponse(
            action=serialize_action_log(action_log),
            executed=action_log.execution_status == "success",
            requires_confirmation=False,
            preview_summary=action_log.preview_summary,
        )
    except Exception as exc:
        db.rollback()
        failed_log = _load_action_log(db, session.id, action_log.id)
        if failed_log is None:
            raise
        failed_log.execution_status = "failed"
        failed_log.verification_status = "failed"
        failed_log.error_message = str(exc)[:500]
        failed_log.result = {}
        failed_log.affected_ids = []
        failed_log.executed_at = datetime.now()
        if failed_log.related_task_id:
            from services.agent_tasks import get_task_or_none, sync_task_after_action_execution

            failed_task = get_task_or_none(db, failed_log.related_task_id)
            if failed_task is not None:
                sync_task_after_action_execution(
                    db,
                    task=failed_task,
                    action_log=failed_log,
                    auto_commit=False,
                )
        db.commit()
        db.refresh(failed_log)
        return AgentActionExecuteResponse(
            action=serialize_action_log(failed_log),
            executed=False,
            requires_confirmation=False,
            preview_summary=failed_log.preview_summary,
        )


def _rollback_agent_action(
    db: Session,
    *,
    session: AgentSession,
    action_log: AgentActionLog,
) -> AgentActionExecuteResponse:
    if action_log.tool_name not in _ROLLBACK_SUPPORTED_TOOLS:
        raise ValueError(f"当前动作不支持回滚: {action_log.tool_name}")
    if action_log.execution_status == "rolled_back":
        return AgentActionExecuteResponse(
            action=serialize_action_log(action_log),
            executed=False,
            requires_confirmation=False,
            preview_summary=action_log.preview_summary,
        )
    if action_log.execution_status != "success":
        raise ValueError("只有执行成功的动作才能回滚")

    args_model = _action_args_model(action_log.tool_name)
    stored_args = args_model.model_validate(action_log.tool_args or {})
    rollback_context = _deserialize_action_preview_context(
        action_log.tool_name,
        action_log.preview_context or {},
    )

    try:
        rollback_result = _rollback_action(
            action_log.tool_name,
            db,
            stored_args,
            rollback_context,
            action_log=action_log,
        )
        now = datetime.now()
        merged_result = dict(action_log.result or {})
        merged_result["rollback"] = {
            **rollback_result.result,
            "rolled_back": True,
            "rolled_back_at": now.isoformat(),
        }
        action_log.result = merged_result
        action_log.verification_status = rollback_result.verification_status
        action_log.error_message = rollback_result.error_message
        action_log.execution_status = "rolled_back"
        action_log.updated_at = now
        if action_log.related_task_id:
            from services.agent_tasks import get_task_or_none, sync_task_after_action_rollback

            related_task = get_task_or_none(db, action_log.related_task_id)
            if related_task is not None:
                sync_task_after_action_rollback(
                    db,
                    task=related_task,
                    action_log=action_log,
                    auto_commit=False,
                )
        db.commit()
        db.refresh(action_log)
        return AgentActionExecuteResponse(
            action=serialize_action_log(action_log),
            executed=False,
            requires_confirmation=False,
            preview_summary=action_log.preview_summary,
        )
    except Exception as exc:
        db.rollback()
        raise ValueError(f"回滚失败: {str(exc)[:500]}") from exc


def _resolve_action_args(
    args_model,
    *,
    action_log: Optional[AgentActionLog],
    payload: AgentActionExecuteRequest,
) -> BaseModel:
    if action_log is None:
        return args_model.model_validate(payload.tool_args or {})

    stored_args = args_model.model_validate(action_log.tool_args or {})
    if not payload.confirm:
        return args_model.model_validate(payload.tool_args or (action_log.tool_args or {}))

    if payload.tool_args:
        requested_args = args_model.model_validate(payload.tool_args)
        if requested_args.model_dump(mode="json") != stored_args.model_dump(mode="json"):
            raise ValueError("确认执行时不能修改预览参数，请重新预览后再确认")
    return stored_args


def _load_action_log(db: Session, session_id: str, action_id: Optional[str]) -> Optional[AgentActionLog]:
    if not action_id:
        return None
    action_log = (
        db.query(AgentActionLog)
        .filter(AgentActionLog.id == action_id, AgentActionLog.session_id == session_id)
        .first()
    )
    if action_log is None:
        raise AgentActionNotFoundError("动作记录不存在")
    return action_log


def _action_args_model(tool_name: str):
    if tool_name == "create_daily_review_paper":
        return CreateDailyReviewPaperArgs
    if tool_name == "update_wrong_answer_status":
        return UpdateWrongAnswerStatusArgs
    if tool_name == "update_concept_mastery":
        return UpdateConceptMasteryArgs
    if tool_name == "generate_quiz_set":
        return GenerateQuizSetArgs
    if tool_name == "log_agent_decision":
        return LogAgentDecisionArgs
    raise ValueError(f"不支持的动作工具: {tool_name}")


def _prepare_action(
    tool_name: str,
    db: Session,
    args: BaseModel,
    *,
    user_id: Optional[str],
    device_id: Optional[str],
) -> ActionPreparation:
    if tool_name == "create_daily_review_paper":
        return _prepare_create_daily_review_paper(
            db,
            CreateDailyReviewPaperArgs.model_validate(args),
            user_id=user_id,
            device_id=device_id,
        )
    if tool_name == "update_wrong_answer_status":
        return _prepare_update_wrong_answer_status(
            db,
            UpdateWrongAnswerStatusArgs.model_validate(args),
            user_id=user_id,
            device_id=device_id,
        )
    if tool_name == "update_concept_mastery":
        return _prepare_update_concept_mastery(
            db,
            UpdateConceptMasteryArgs.model_validate(args),
            user_id=user_id,
            device_id=device_id,
        )
    if tool_name == "generate_quiz_set":
        return _prepare_generate_quiz_set(
            db,
            GenerateQuizSetArgs.model_validate(args),
            user_id=user_id,
            device_id=device_id,
        )
    if tool_name == "log_agent_decision":
        return _prepare_log_agent_decision(LogAgentDecisionArgs.model_validate(args))
    raise ValueError(f"不支持的动作工具: {tool_name}")


def _execute_action(
    tool_name: str,
    db: Session,
    args: BaseModel,
    context: Dict[str, Any],
    *,
    action_log: AgentActionLog,
) -> ActionExecutionResult:
    if tool_name == "create_daily_review_paper":
        return _execute_create_daily_review_paper(
            db,
            CreateDailyReviewPaperArgs.model_validate(args),
            context,
        )
    if tool_name == "update_wrong_answer_status":
        return _execute_update_wrong_answer_status(
            db,
            UpdateWrongAnswerStatusArgs.model_validate(args),
            context,
        )
    if tool_name == "update_concept_mastery":
        return _execute_update_concept_mastery(
            db,
            UpdateConceptMasteryArgs.model_validate(args),
            context,
        )
    if tool_name == "generate_quiz_set":
        return _execute_generate_quiz_set(
            db,
            GenerateQuizSetArgs.model_validate(args),
            context,
        )
    if tool_name == "log_agent_decision":
        return _execute_log_agent_decision(
            LogAgentDecisionArgs.model_validate(args),
            action_log=action_log,
        )
    raise ValueError(f"不支持的动作工具: {tool_name}")


def _rollback_action(
    tool_name: str,
    db: Session,
    args: BaseModel,
    context: Dict[str, Any],
    *,
    action_log: AgentActionLog,
) -> ActionExecutionResult:
    if tool_name == "create_daily_review_paper":
        return _rollback_create_daily_review_paper(
            db,
            CreateDailyReviewPaperArgs.model_validate(args),
            context,
            action_log=action_log,
        )
    if tool_name == "update_wrong_answer_status":
        return _rollback_update_wrong_answer_status(
            db,
            UpdateWrongAnswerStatusArgs.model_validate(args),
            context,
        )
    if tool_name == "update_concept_mastery":
        return _rollback_update_concept_mastery(
            db,
            UpdateConceptMasteryArgs.model_validate(args),
            context,
        )
    if tool_name == "generate_quiz_set":
        return _rollback_generate_quiz_set(
            db,
            GenerateQuizSetArgs.model_validate(args),
            action_log=action_log,
        )
    raise ValueError(f"当前动作不支持回滚: {tool_name}")


def _prepare_log_agent_decision(args: LogAgentDecisionArgs) -> ActionPreparation:
    return ActionPreparation(
        normalized_args=args.model_dump(mode="json"),
        preview_summary=f"记录一次 {args.decision_type} 决策：{_shorten(args.summary, 48)}",
        context={},
    )


def _execute_log_agent_decision(
    args: LogAgentDecisionArgs,
    *,
    action_log: AgentActionLog,
) -> ActionExecutionResult:
    return ActionExecutionResult(
        affected_ids=[action_log.id],
        result={
            "logged": True,
            "action_id": action_log.id,
            "decision_type": args.decision_type,
            "summary": args.summary,
            "rationale": args.rationale,
            "related_turn_id": args.related_turn_id,
            "metadata": args.metadata,
        },
        verification_status="verified",
    )


def _prepare_update_wrong_answer_status(
    db: Session,
    args: UpdateWrongAnswerStatusArgs,
    *,
    user_id: Optional[str],
    device_id: Optional[str],
) -> ActionPreparation:
    wrong_answer_ids = _dedupe_ints(args.wrong_answer_ids)
    items = (
        _apply_actor_scope(
            db.query(WrongAnswerV2),
            WrongAnswerV2,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(WrongAnswerV2.id.in_(wrong_answer_ids))
        .all()
    )
    if len(items) != len(wrong_answer_ids):
        found_ids = {int(item.id) for item in items}
        missing = [item_id for item_id in wrong_answer_ids if item_id not in found_ids]
        raise ValueError(f"部分错题不存在或无权操作: {missing}")

    target_status = _resolve_wrong_answer_target_status(args.target_status)
    preview_summary = f"将 {len(items)} 道错题更新为 {target_status} 状态"
    if args.target_status == "mastered":
        preview_summary = f"将 {len(items)} 道错题按 mastered 语义归档为 archived"

    return ActionPreparation(
        normalized_args=args.model_dump(mode="json"),
        preview_summary=preview_summary,
        context={
            "wrong_answer_ids": wrong_answer_ids,
            "target_status": target_status,
            "previous_statuses": {int(item.id): item.mastery_status for item in items},
            "previous_archived_at": {int(item.id): _iso_datetime(item.archived_at) for item in items},
        },
    )


def _execute_update_wrong_answer_status(
    db: Session,
    args: UpdateWrongAnswerStatusArgs,
    context: Dict[str, Any],
) -> ActionExecutionResult:
    now = datetime.now()
    wrong_answer_ids = list(context.get("wrong_answer_ids") or [])
    target_status = str(context.get("target_status") or _resolve_wrong_answer_target_status(args.target_status))
    items = db.query(WrongAnswerV2).filter(WrongAnswerV2.id.in_(wrong_answer_ids)).all()
    if len(items) != len(wrong_answer_ids):
        raise ValueError("待更新的错题已发生变化，请重新预览后再执行")

    for item in items:
        item.mastery_status = target_status
        item.archived_at = now if target_status == "archived" else None
        item.updated_at = now

    db.flush()

    refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id.in_(wrong_answer_ids)).all()
    all_verified = all(item.mastery_status == target_status for item in refreshed)
    verification_status = "verified" if all_verified else "mismatch"

    return ActionExecutionResult(
        affected_ids=wrong_answer_ids,
        result={
            "updated_count": len(refreshed),
            "requested_target_status": args.target_status,
            "applied_target_status": target_status,
            "reason": args.reason,
            "statuses": {str(item.id): item.mastery_status for item in refreshed},
        },
        verification_status=verification_status,
        error_message=None if all_verified else "写入后回读状态不一致",
    )


def _rollback_update_wrong_answer_status(
    db: Session,
    args: UpdateWrongAnswerStatusArgs,
    context: Dict[str, Any],
) -> ActionExecutionResult:
    wrong_answer_ids = list(context.get("wrong_answer_ids") or [])
    previous_statuses = {int(key): value for key, value in (context.get("previous_statuses") or {}).items()}
    previous_archived_at = {int(key): value for key, value in (context.get("previous_archived_at") or {}).items()}
    items = db.query(WrongAnswerV2).filter(WrongAnswerV2.id.in_(wrong_answer_ids)).all()
    if len(items) != len(wrong_answer_ids):
        raise ValueError("待回滚的错题已发生变化")

    for item in items:
        item.mastery_status = str(previous_statuses.get(int(item.id)) or "active")
        item.archived_at = _parse_iso_datetime(previous_archived_at.get(int(item.id)))
        item.updated_at = datetime.now()

    db.flush()

    refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id.in_(wrong_answer_ids)).all()
    all_verified = all(
        item.mastery_status == str(previous_statuses.get(int(item.id)) or "active")
        and _iso_datetime(item.archived_at) == previous_archived_at.get(int(item.id))
        for item in refreshed
    )
    if not all_verified:
        raise ValueError("错题状态回滚后回读不一致")

    return ActionExecutionResult(
        affected_ids=wrong_answer_ids,
        result={
            "summary": f"已恢复 {len(refreshed)} 道错题的原始状态。",
            "restored_statuses": {str(item.id): item.mastery_status for item in refreshed},
        },
        verification_status="verified",
    )


def _prepare_update_concept_mastery(
    db: Session,
    args: UpdateConceptMasteryArgs,
    *,
    user_id: Optional[str],
    device_id: Optional[str],
) -> ActionPreparation:
    concept_ids = _dedupe_strings(args.concept_ids)
    concepts = _load_scoped_concepts(
        db,
        concept_ids,
        user_id=user_id,
        device_id=device_id,
    )
    updates = [
        _plan_concept_mastery_update(
            db,
            concept,
            review_in_days=args.review_in_days,
            user_id=user_id,
            device_id=device_id,
        )
        for concept in concepts
    ]
    avg_before = round(sum(_concept_mastery_percent(concept) for concept in concepts) / len(concepts), 1) if concepts else 0.0
    avg_after = round(sum(float(item["mastery_score"]) for item in updates) / len(updates), 1) if updates else 0.0
    preview_summary = f"将回写 {len(updates)} 个知识点掌握度，平均掌握度 {avg_before}% → {avg_after}%"

    return ActionPreparation(
        normalized_args={
            **args.model_dump(mode="json"),
            "concept_ids": concept_ids,
        },
        preview_summary=preview_summary,
        context={
            "concept_ids": concept_ids,
            "concept_updates": updates,
            "previous_concepts": [
                {
                    "concept_id": concept.concept_id,
                    "retention": float(concept.retention or 0.0),
                    "understanding": float(concept.understanding or 0.0),
                    "application": float(concept.application or 0.0),
                    "last_tested": _iso_date(concept.last_tested),
                    "next_review": _iso_date(concept.next_review),
                }
                for concept in concepts
            ],
            "avg_mastery_before": avg_before,
            "avg_mastery_after": avg_after,
        },
    )


def _execute_update_concept_mastery(
    db: Session,
    args: UpdateConceptMasteryArgs,
    context: Dict[str, Any],
) -> ActionExecutionResult:
    concept_ids = [str(item) for item in (context.get("concept_ids") or [])]
    updates = list(context.get("concept_updates") or [])
    concepts = db.query(ConceptMastery).filter(ConceptMastery.concept_id.in_(concept_ids)).all()
    if len(concepts) != len(concept_ids):
        found_ids = {concept.concept_id for concept in concepts}
        missing = [concept_id for concept_id in concept_ids if concept_id not in found_ids]
        raise ValueError(f"待更新的知识点已发生变化: {missing}")

    update_map = {str(item["concept_id"]): item for item in updates}
    for concept in concepts:
        plan = update_map.get(concept.concept_id)
        if plan is None:
            raise ValueError(f"缺少知识点更新计划: {concept.concept_id}")
        concept.retention = float(plan["retention"])
        concept.understanding = float(plan["understanding"])
        concept.application = float(plan["application"])
        concept.last_tested = _parse_iso_date(plan.get("last_tested")) or concept.last_tested
        concept.next_review = _parse_iso_date(plan.get("next_review")) or concept.next_review

    db.flush()

    refreshed = db.query(ConceptMastery).filter(ConceptMastery.concept_id.in_(concept_ids)).all()
    refreshed_map = {concept.concept_id: concept for concept in refreshed}
    verified = True
    result_items: List[Dict[str, Any]] = []
    for concept_id in concept_ids:
        plan = update_map[concept_id]
        concept = refreshed_map.get(concept_id)
        if concept is None:
            verified = False
            continue
        matched = (
            _float_matches(concept.retention, plan["retention"])
            and _float_matches(concept.understanding, plan["understanding"])
            and _float_matches(concept.application, plan["application"])
            and _iso_date(concept.next_review) == plan.get("next_review")
            and _iso_date(concept.last_tested) == plan.get("last_tested")
        )
        verified = verified and matched
        result_items.append({**plan, "verified": matched})

    return ActionExecutionResult(
        affected_ids=concept_ids,
        result={
            "updated_count": len(result_items),
            "reason": args.reason,
            "review_in_days": args.review_in_days,
            "avg_mastery_before": context.get("avg_mastery_before", 0.0),
            "avg_mastery_after": context.get("avg_mastery_after", 0.0),
            "concepts": result_items,
        },
        verification_status="verified" if verified else "mismatch",
        error_message=None if verified else "知识点掌握度写入后回读不一致",
    )


def _rollback_update_concept_mastery(
    db: Session,
    args: UpdateConceptMasteryArgs,
    context: Dict[str, Any],
) -> ActionExecutionResult:
    concept_ids = [str(item) for item in (context.get("concept_ids") or [])]
    previous_concepts = list(context.get("previous_concepts") or [])
    previous_map = {str(item.get("concept_id") or ""): item for item in previous_concepts}
    concepts = db.query(ConceptMastery).filter(ConceptMastery.concept_id.in_(concept_ids)).all()
    if len(concepts) != len(concept_ids):
        raise ValueError("待回滚的知识点已发生变化")

    for concept in concepts:
        previous = previous_map.get(concept.concept_id)
        if previous is None:
            raise ValueError(f"缺少知识点回滚快照: {concept.concept_id}")
        concept.retention = float(previous.get("retention") or 0.0)
        concept.understanding = float(previous.get("understanding") or 0.0)
        concept.application = float(previous.get("application") or 0.0)
        concept.last_tested = _parse_iso_date(previous.get("last_tested"))
        concept.next_review = _parse_iso_date(previous.get("next_review"))

    db.flush()

    refreshed = db.query(ConceptMastery).filter(ConceptMastery.concept_id.in_(concept_ids)).all()
    refreshed_map = {concept.concept_id: concept for concept in refreshed}
    restored_items: List[Dict[str, Any]] = []
    for concept_id in concept_ids:
        previous = previous_map[concept_id]
        concept = refreshed_map.get(concept_id)
        if concept is None:
            raise ValueError(f"知识点回滚后缺失: {concept_id}")
        matched = (
            _float_matches(concept.retention, previous.get("retention"))
            and _float_matches(concept.understanding, previous.get("understanding"))
            and _float_matches(concept.application, previous.get("application"))
            and _iso_date(concept.last_tested) == previous.get("last_tested")
            and _iso_date(concept.next_review) == previous.get("next_review")
        )
        if not matched:
            raise ValueError(f"知识点回滚后回读不一致: {concept_id}")
        restored_items.append(
            {
                "concept_id": concept_id,
                "retention": float(concept.retention or 0.0),
                "understanding": float(concept.understanding or 0.0),
                "application": float(concept.application or 0.0),
                "last_tested": _iso_date(concept.last_tested),
                "next_review": _iso_date(concept.next_review),
            }
        )

    return ActionExecutionResult(
        affected_ids=concept_ids,
        result={
            "summary": f"已恢复 {len(restored_items)} 个知识点的原始掌握度。",
            "concepts": restored_items,
        },
        verification_status="verified",
    )


def _prepare_generate_quiz_set(
    db: Session,
    args: GenerateQuizSetArgs,
    *,
    user_id: Optional[str],
    device_id: Optional[str],
) -> ActionPreparation:
    concept_ids = _dedupe_strings(args.concept_ids)
    concepts = _load_scoped_concepts(
        db,
        concept_ids,
        user_id=user_id,
        device_id=device_id,
    )
    chapter_map = _load_chapter_map(db, concepts)
    questions = _build_quiz_question_blueprints(
        db,
        concepts,
        chapter_map=chapter_map,
        target_count=args.target_count,
        user_id=user_id,
        device_id=device_id,
    )
    if not questions:
        raise ValueError("当前没有可用于生成题组的数据")

    source_breakdown = _quiz_source_breakdown(questions)
    session_title = _resolve_quiz_session_title(args, concepts, chapter_map)
    session_chapter_id = _resolve_primary_chapter_id(concepts)
    preview_summary = f"将为 {len(concepts)} 个知识点生成 {len(questions)} 道题"
    source_summary = _format_source_breakdown(source_breakdown)
    if source_summary:
        preview_summary += f"（{source_summary}）"

    return ActionPreparation(
        normalized_args={
            **args.model_dump(mode="json"),
            "concept_ids": concept_ids,
        },
        preview_summary=preview_summary,
        context={
            "concept_ids": concept_ids,
            "session_title": session_title,
            "session_chapter_id": session_chapter_id,
            "question_blueprints": questions,
            "source_breakdown": source_breakdown,
            "user_id": user_id,
            "device_id": device_id,
        },
    )


def _execute_generate_quiz_set(
    db: Session,
    args: GenerateQuizSetArgs,
    context: Dict[str, Any],
) -> ActionExecutionResult:
    now = datetime.now()
    learning_session_type = "exam" if args.session_type == "chapter_test" else "detail_practice"
    question_blueprints = canonicalize_quiz_questions(context.get("question_blueprints") or [])
    if not question_blueprints:
        raise ValueError("没有可写入的题目")

    session_chapter_id = context.get("session_chapter_id")
    quiz_session = QuizSession(
        session_type=args.session_type,
        chapter_id=session_chapter_id,
        questions=question_blueprints,
        answers=canonicalize_quiz_answers([]),
        total_questions=len(question_blueprints),
        correct_count=0,
        score=0,
    )
    db.add(quiz_session)
    db.flush()

    learning_session = LearningSession(
        id=f"agent-quiz-{uuid4().hex}",
        user_id=context.get("user_id"),
        device_id=context.get("device_id"),
        session_type=learning_session_type,
        chapter_id=session_chapter_id,
        exam_id=str(quiz_session.id),
        title=context.get("session_title") or f"Agent 题组 {now.strftime('%Y-%m-%d %H:%M')}",
        description="Generated by agent action generate_quiz_set.",
        knowledge_point=_join_concept_ids(context.get("concept_ids") or []),
        total_questions=len(question_blueprints),
        answered_questions=0,
        correct_count=0,
        wrong_count=0,
        score=0,
        accuracy=0.0,
        started_at=now,
        status="in_progress",
    )
    db.add(learning_session)
    db.flush()

    question_record_ids: List[int] = []
    for index, question in enumerate(question_blueprints):
        question_record = QuestionRecord(
            user_id=context.get("user_id"),
            device_id=context.get("device_id"),
            session_id=learning_session.id,
            question_index=index,
            question_type=str(question.get("question_type") or question.get("type") or "A1"),
            difficulty=str(question.get("difficulty") or "basic"),
            question_text=str(question.get("question") or question.get("question_text") or ""),
            options=normalize_option_map(question.get("options")),
            correct_answer=str(question.get("correct_answer") or ""),
            explanation=str(question.get("explanation") or ""),
            key_point=str(question.get("key_point") or question.get("concept_name") or ""),
            time_spent_seconds=0,
        )
        db.add(question_record)
        db.flush()
        question_record_ids.append(int(question_record.id))

    db.flush()

    refreshed_quiz = db.query(QuizSession).filter(QuizSession.id == quiz_session.id).first()
    refreshed_learning_session = db.query(LearningSession).filter(LearningSession.id == learning_session.id).first()
    refreshed_records = (
        db.query(QuestionRecord)
        .filter(QuestionRecord.session_id == learning_session.id)
        .order_by(QuestionRecord.question_index.asc(), QuestionRecord.id.asc())
        .all()
    )
    verified = (
        refreshed_quiz is not None
        and len(refreshed_quiz.questions or []) == len(question_blueprints)
        and refreshed_learning_session is not None
        and int(refreshed_learning_session.total_questions or 0) == len(question_blueprints)
        and len(refreshed_records) == len(question_blueprints)
    )

    return ActionExecutionResult(
        affected_ids=[int(quiz_session.id), learning_session.id, *question_record_ids],
        result={
            "quiz_session_id": int(quiz_session.id),
            "learning_session_id": learning_session.id,
            "title": learning_session.title,
            "session_type": args.session_type,
            "total_questions": len(question_blueprints),
            "concept_ids": list(context.get("concept_ids") or []),
            "question_record_ids": question_record_ids,
            "source_breakdown": context.get("source_breakdown") or {},
        },
        verification_status="verified" if verified else "mismatch",
        error_message=None if verified else "题组写入后回读结果不一致",
    )


def _rollback_generate_quiz_set(
    db: Session,
    args: GenerateQuizSetArgs,
    *,
    action_log: AgentActionLog,
) -> ActionExecutionResult:
    result = dict(action_log.result or {})
    quiz_session_id = result.get("quiz_session_id")
    learning_session_id = result.get("learning_session_id")

    learning_session = (
        db.query(LearningSession)
        .filter(LearningSession.id == learning_session_id)
        .first()
        if learning_session_id
        else None
    )
    if learning_session is not None:
        db.delete(learning_session)

    quiz_session = (
        db.query(QuizSession)
        .filter(QuizSession.id == int(quiz_session_id))
        .first()
        if quiz_session_id is not None
        else None
    )
    if quiz_session is not None:
        db.delete(quiz_session)

    db.flush()

    remaining_learning_session = (
        db.query(LearningSession).filter(LearningSession.id == learning_session_id).first()
        if learning_session_id
        else None
    )
    remaining_quiz_session = (
        db.query(QuizSession).filter(QuizSession.id == int(quiz_session_id)).first()
        if quiz_session_id is not None
        else None
    )
    remaining_question_records = (
        db.query(QuestionRecord).filter(QuestionRecord.session_id == learning_session_id).count()
        if learning_session_id
        else 0
    )
    if remaining_learning_session is not None or remaining_quiz_session is not None or remaining_question_records:
        raise ValueError("题组回滚后仍存在残留数据")

    return ActionExecutionResult(
        affected_ids=[item for item in [quiz_session_id, learning_session_id] if item is not None],
        result={
            "summary": "已删除这次生成的题组、学习会话和题目记录。",
            "quiz_session_id": quiz_session_id,
            "learning_session_id": learning_session_id,
        },
        verification_status="verified",
    )


def _prepare_create_daily_review_paper(
    db: Session,
    args: CreateDailyReviewPaperArgs,
    *,
    user_id: Optional[str],
    device_id: Optional[str],
) -> ActionPreparation:
    paper_date = args.paper_date or date.today()
    paper_user_id, paper_device_id = resolve_actor_identity(user_id, device_id)
    actor_key = build_actor_key(user_id, device_id)
    actor_keys = build_actor_key_aliases(user_id, device_id)
    existing_paper = _get_daily_review_paper_for_actor(
        db,
        paper_date=paper_date,
        actor_key=actor_key,
        actor_keys=actor_keys,
    )
    if existing_paper and not args.allow_replace:
        raise ValueError(f"{paper_date.isoformat()} 的每日复习卷已存在")

    ordered_candidates = _build_daily_review_candidates_for_actor(
        db,
        paper_date=paper_date,
        wrong_answer_ids=_dedupe_ints(args.wrong_answer_ids),
        user_id=user_id,
        device_id=device_id,
    )
    if not ordered_candidates:
        raise ValueError("当前没有可用于生成每日复习卷的 active 错题")

    selected = _select_daily_review_candidates(
        ordered_candidates,
        target_count=min(args.target_count, len(ordered_candidates)),
    )
    if not selected:
        raise ValueError("当前没有满足条件的错题，无法生成每日复习卷")

    preview_summary = f"为 {paper_date.isoformat()} 生成 {len(selected)} 道每日复习题"
    if existing_paper:
        preview_summary += "，并覆盖当天已有题卷"

    return ActionPreparation(
        normalized_args=args.model_dump(mode="json"),
        preview_summary=preview_summary,
        context={
            "paper_date": paper_date,
            "actor_key": actor_key,
            "actor_keys": actor_keys,
            "paper_user_id": paper_user_id,
            "paper_device_id": paper_device_id,
            "target_count": args.target_count,
            "existing_paper_id": int(existing_paper.id) if existing_paper else None,
            "existing_paper_snapshot": _serialize_daily_review_paper_snapshot(existing_paper),
            "selected_candidates": selected,
            "selected_wrong_answer_ids": [int(item.wrong_answer_id) for item in selected],
        },
    )


def _execute_create_daily_review_paper(
    db: Session,
    args: CreateDailyReviewPaperArgs,
    context: Dict[str, Any],
) -> ActionExecutionResult:
    paper_date = context["paper_date"]
    actor_key = str(context.get("actor_key") or "")
    actor_keys = [str(item) for item in (context.get("actor_keys") or []) if str(item or "").strip()]
    selected_candidates = list(context.get("selected_candidates") or [])
    if not selected_candidates:
        raise ValueError("没有可写入的复习卷题目")

    config = _build_daily_review_config(
        paper_date,
        selected_candidates,
        target_count=int(context.get("target_count") or args.target_count),
    )
    paper = _get_daily_review_paper_for_actor(
        db,
        paper_date=paper_date,
        actor_key=actor_key,
        actor_keys=actor_keys,
    )
    if paper is None:
        paper = DailyReviewPaper(
            user_id=context.get("paper_user_id"),
            device_id=context.get("paper_device_id"),
            actor_key=actor_key,
            paper_date=paper_date,
        )
        db.add(paper)
        db.flush()
    else:
        paper.items.clear()
        db.flush()

    paper.user_id = context.get("paper_user_id")
    if not paper.device_id:
        paper.device_id = context.get("paper_device_id")
    if not paper.actor_key:
        paper.actor_key = actor_key
    paper.total_questions = len(selected_candidates)
    paper.config = config
    paper.updated_at = datetime.now()

    for position, candidate in enumerate(selected_candidates, start=1):
        paper.items.append(
            DailyReviewPaperItem(
                wrong_answer_id=candidate.wrong_answer_id,
                position=position,
                stem_fingerprint=candidate.stem_fingerprint,
                source_bucket=candidate.source_bucket,
                snapshot=candidate.snapshot,
            )
        )

    db.flush()

    refreshed_paper = db.query(DailyReviewPaper).filter(DailyReviewPaper.id == paper.id).first()
    actual_items = sorted(refreshed_paper.items, key=lambda item: item.position) if refreshed_paper else []
    expected_wrong_answer_ids = [int(item.wrong_answer_id) for item in selected_candidates]
    actual_wrong_answer_ids = [int(item.wrong_answer_id) for item in actual_items]
    verified = (
        refreshed_paper is not None
        and refreshed_paper.actor_key in (actor_keys or [actor_key])
        and int(refreshed_paper.total_questions or 0) == len(expected_wrong_answer_ids)
        and int((refreshed_paper.config or {}).get("target_count") or 0) == int(config.get("target_count") or 0)
        and actual_wrong_answer_ids == expected_wrong_answer_ids
    )

    return ActionExecutionResult(
        affected_ids=[int(refreshed_paper.id)] if refreshed_paper else [],
        result={
            "paper_id": int(refreshed_paper.id) if refreshed_paper else None,
            "paper_date": paper_date.isoformat(),
            "total_questions": len(actual_wrong_answer_ids),
            "wrong_answer_ids": actual_wrong_answer_ids,
            "config": config,
        },
        verification_status="verified" if verified else "mismatch",
        error_message=None if verified else "复习卷写入后回读结果不一致",
    )


def _rollback_create_daily_review_paper(
    db: Session,
    args: CreateDailyReviewPaperArgs,
    context: Dict[str, Any],
    *,
    action_log: AgentActionLog,
) -> ActionExecutionResult:
    paper_date = _parse_iso_date(context.get("paper_date")) or args.paper_date or date.today()
    actor_key = str(context.get("actor_key") or "")
    actor_keys = [str(item) for item in (context.get("actor_keys") or []) if str(item or "").strip()]
    existing_snapshot = dict(context.get("existing_paper_snapshot") or {})
    paper = _get_daily_review_paper_for_actor(
        db,
        paper_date=paper_date,
        actor_key=actor_key,
        actor_keys=actor_keys,
    )

    if existing_snapshot:
        if paper is None:
            paper = DailyReviewPaper()
            db.add(paper)
            db.flush()
        else:
            # Flush orphaned items before re-inserting the snapshot so SQLite unique
            # constraints on (paper_id, position) and (paper_id, wrong_answer_id)
            # do not trip during rollback.
            paper.items.clear()
            db.flush()
        _restore_daily_review_paper_snapshot(paper, existing_snapshot)
    elif paper is not None:
        db.delete(paper)

    db.flush()

    restored_paper = _get_daily_review_paper_for_actor(
        db,
        paper_date=paper_date,
        actor_key=actor_key,
        actor_keys=actor_keys,
    )
    if existing_snapshot:
        _verify_daily_review_snapshot(restored_paper, existing_snapshot)
        summary = f"已恢复 {paper_date.isoformat()} 原有的每日复习卷。"
    else:
        if restored_paper is not None:
            raise ValueError("复习卷回滚后仍存在残留题卷")
        summary = f"已撤销 {paper_date.isoformat()} 的每日复习卷写入。"

    return ActionExecutionResult(
        affected_ids=[action_log.id],
        result={"summary": summary},
        verification_status="verified",
    )


def _load_scoped_concepts(
    db: Session,
    concept_ids: List[str],
    *,
    user_id: Optional[str],
    device_id: Optional[str],
) -> List[ConceptMastery]:
    concepts = (
        _apply_actor_scope(
            db.query(ConceptMastery),
            ConceptMastery,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(ConceptMastery.concept_id.in_(concept_ids))
        .all()
    )
    concept_map = {concept.concept_id: concept for concept in concepts}
    missing = [concept_id for concept_id in concept_ids if concept_id not in concept_map]
    if missing:
        raise ValueError(f"部分知识点不存在或无权操作: {missing}")
    return [concept_map[concept_id] for concept_id in concept_ids]


def _load_chapter_map(db: Session, concepts: List[ConceptMastery]) -> Dict[str, Chapter]:
    chapter_ids = list({concept.chapter_id for concept in concepts if concept.chapter_id})
    if not chapter_ids:
        return {}
    chapters = db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).all()
    return {chapter.id: chapter for chapter in chapters}


def _plan_concept_mastery_update(
    db: Session,
    concept: ConceptMastery,
    *,
    review_in_days: Optional[int],
    user_id: Optional[str],
    device_id: Optional[str],
) -> Dict[str, Any]:
    recent_tests = (
        _apply_actor_scope(
            db.query(TestRecord),
            TestRecord,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(TestRecord.concept_id == concept.concept_id)
        .order_by(desc(TestRecord.tested_at), desc(TestRecord.id))
        .limit(8)
        .all()
    )
    wrong_answer_query = _apply_actor_scope(
        db.query(WrongAnswerV2),
        WrongAnswerV2,
        user_id=user_id,
        device_id=device_id,
    ).filter(WrongAnswerV2.key_point == concept.name)
    active_wrong_count = wrong_answer_query.filter(WrongAnswerV2.mastery_status == "active").count()
    recent_wrong_count = wrong_answer_query.filter(
        WrongAnswerV2.updated_at >= datetime.now() - timedelta(days=30)
    ).count()

    if recent_tests:
        weights = [max(len(recent_tests) - index, 1) for index in range(len(recent_tests))]
        total_weight = float(sum(weights))
        accuracy_ratio = sum(
            (1.0 if bool(record.is_correct) else 0.0) * weight
            for record, weight in zip(recent_tests, weights)
        ) / total_weight
        score_ratio = sum(
            _test_record_score_ratio(record) * weight
            for record, weight in zip(recent_tests, weights)
        ) / total_weight
        confidence_ratio = sum(
            _confidence_ratio(record.confidence) * weight
            for record, weight in zip(recent_tests, weights)
        ) / total_weight
        last_tested = next(
            (record.tested_at.date() for record in recent_tests if record.tested_at),
            concept.last_tested or date.today(),
        )
    else:
        fallback = max(0.0, min(1.0, _concept_mastery_ratio(concept)))
        accuracy_ratio = fallback
        score_ratio = fallback
        confidence_ratio = 0.55 if fallback > 0 else 0.4
        last_tested = concept.last_tested or date.today()

    evidence_bonus = min(len(recent_tests), 4) * 0.03
    active_penalty = min(active_wrong_count * 0.08, 0.28)
    recent_penalty = min(recent_wrong_count * 0.03, 0.12)

    retention = _round_metric(
        accuracy_ratio * 0.12 + score_ratio * 0.48 + confidence_ratio * 0.10 + evidence_bonus
        - active_penalty
        - recent_penalty * 0.4
    )
    understanding = _round_metric(
        accuracy_ratio * 0.45 + score_ratio * 0.25 + confidence_ratio * 0.10 + evidence_bonus
        - active_penalty * 0.7
        - recent_penalty * 0.25
    )
    application = _round_metric(
        accuracy_ratio * 0.38 + score_ratio * 0.22 + confidence_ratio * 0.06 + evidence_bonus
        - active_penalty * 0.85
        - recent_penalty * 0.5
    )

    mastery_score = round((retention + understanding + application) / 3 * 100, 1)
    review_days = review_in_days if review_in_days is not None else _recommended_review_days(
        mastery_score,
        active_wrong_count=active_wrong_count,
        recent_wrong_count=recent_wrong_count,
    )

    return {
        "concept_id": concept.concept_id,
        "name": concept.name,
        "chapter_id": concept.chapter_id,
        "retention": retention,
        "understanding": understanding,
        "application": application,
        "mastery_score": mastery_score,
        "next_review": (date.today() + timedelta(days=review_days)).isoformat(),
        "last_tested": _iso_date(last_tested),
        "test_count": len(recent_tests),
        "active_wrong_count": active_wrong_count,
        "recent_wrong_count": recent_wrong_count,
        "accuracy_ratio": round(accuracy_ratio, 4),
        "score_ratio": round(score_ratio, 4),
    }


def _recommended_review_days(
    mastery_score: float,
    *,
    active_wrong_count: int,
    recent_wrong_count: int,
) -> int:
    if active_wrong_count >= 3 or mastery_score < 45:
        return 1
    if mastery_score < 65:
        return 3
    if mastery_score < 80:
        return 7 if recent_wrong_count <= 1 else 5
    return 14 if recent_wrong_count == 0 else 10


def _test_record_score_ratio(record: TestRecord) -> float:
    try:
        score = float(record.score)
    except (TypeError, ValueError):
        return 1.0 if bool(record.is_correct) else 0.0
    if 0 <= score <= 1:
        return max(0.0, min(1.0, score))
    return max(0.0, min(1.0, score / 100.0))


def _confidence_ratio(value: Any) -> float:
    confidence = normalize_confidence(value)
    if confidence not in {"sure", "unsure", "no"}:
        return 0.5
    if confidence == "sure":
        return 1.0
    if confidence == "no":
        return 0.35
    return 0.65


def _concept_mastery_ratio(concept: ConceptMastery) -> float:
    return (
        float(concept.retention or 0.0)
        + float(concept.understanding or 0.0)
        + float(concept.application or 0.0)
    ) / 3


def _concept_mastery_percent(concept: ConceptMastery) -> float:
    return round(_concept_mastery_ratio(concept) * 100, 1)


def _build_quiz_question_blueprints(
    db: Session,
    concepts: List[ConceptMastery],
    *,
    chapter_map: Dict[str, Chapter],
    target_count: int,
    user_id: Optional[str],
    device_id: Optional[str],
) -> List[Dict[str, Any]]:
    buckets = {
        concept.concept_id: _build_quiz_question_bucket(
            db,
            concept,
            chapter=chapter_map.get(concept.chapter_id or ""),
            user_id=user_id,
            device_id=device_id,
        )
        for concept in concepts
    }
    selected: List[Dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    ordered_ids = [concept.concept_id for concept in concepts]

    while len(selected) < target_count and any(buckets[concept_id] for concept_id in ordered_ids):
        for concept_id in ordered_ids:
            bucket = buckets[concept_id]
            while bucket:
                candidate = bucket.pop(0)
                fingerprint = _question_fingerprint(candidate)
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)
                selected.append(candidate)
                break
            if len(selected) >= target_count:
                break

    fallback_index = 0
    while len(selected) < target_count and concepts:
        concept = concepts[fallback_index % len(concepts)]
        chapter = chapter_map.get(concept.chapter_id or "")
        candidate = _build_fallback_quiz_question(
            concept,
            question_index=len(selected),
            chapter=chapter,
        )
        fingerprint = _question_fingerprint(candidate)
        if fingerprint not in seen_fingerprints:
            seen_fingerprints.add(fingerprint)
            selected.append(candidate)
        fallback_index += 1

    return canonicalize_quiz_questions(selected[:target_count])


def _build_quiz_question_bucket(
    db: Session,
    concept: ConceptMastery,
    *,
    chapter: Optional[Chapter],
    user_id: Optional[str],
    device_id: Optional[str],
) -> List[Dict[str, Any]]:
    bucket: List[Dict[str, Any]] = []
    wrong_answers = (
        _apply_actor_scope(
            db.query(WrongAnswerV2),
            WrongAnswerV2,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(WrongAnswerV2.key_point == concept.name)
        .order_by(desc(WrongAnswerV2.updated_at), desc(WrongAnswerV2.last_wrong_at), desc(WrongAnswerV2.id))
        .limit(4)
        .all()
    )
    for wrong_answer in wrong_answers:
        bucket.append(
            _normalize_question_blueprint(
                {
                    "question_id": f"wa-{wrong_answer.id}",
                    "source_type": "wrong_answer",
                    "source_record_id": int(wrong_answer.id),
                    "fingerprint": f"wrong_answer:{wrong_answer.id}",
                    "question": wrong_answer.question_text,
                    "options": wrong_answer.options,
                    "correct_answer": wrong_answer.correct_answer,
                    "explanation": wrong_answer.explanation or f"复盘 {concept.name} 的易错点。",
                    "key_point": wrong_answer.key_point or concept.name,
                    "difficulty": wrong_answer.difficulty or "advanced",
                    "question_type": wrong_answer.question_type or "A2",
                    "is_wrong_answer": True,
                },
                concept=concept,
                chapter=chapter,
            )
        )

    test_records = (
        _apply_actor_scope(
            db.query(TestRecord),
            TestRecord,
            user_id=user_id,
            device_id=device_id,
        )
        .filter(TestRecord.concept_id == concept.concept_id)
        .order_by(desc(TestRecord.tested_at), desc(TestRecord.id))
        .limit(4)
        .all()
    )
    for record in test_records:
        bucket.append(
            _normalize_question_blueprint(
                {
                    "question_id": f"test-{record.id}",
                    "source_type": "test_record",
                    "source_record_id": int(record.id),
                    "fingerprint": f"test_record:{record.id}",
                    "question": record.ai_question,
                    "options": record.ai_options,
                    "correct_answer": record.ai_correct_answer,
                    "explanation": record.ai_explanation or f"回看 {concept.name} 的题目解析。",
                    "key_point": concept.name,
                    "difficulty": "basic" if bool(record.is_correct) else "advanced",
                    "question_type": "A1",
                    "is_wrong_answer": False,
                },
                concept=concept,
                chapter=chapter,
            )
        )

    if not bucket:
        bucket.append(_build_fallback_quiz_question(concept, question_index=0, chapter=chapter))
    return bucket


def _normalize_question_blueprint(
    payload: Dict[str, Any],
    *,
    concept: ConceptMastery,
    chapter: Optional[Chapter],
) -> Dict[str, Any]:
    question = str(payload.get("question") or payload.get("question_text") or "").strip()
    question = question or _fallback_question_text(concept, chapter=chapter, variant=0)

    options = normalize_option_map(payload.get("options"))
    correct_answer = str(payload.get("correct_answer") or "").strip().upper()
    if correct_answer not in options or len(options) < 2:
        options = _fallback_option_map(concept, chapter=chapter)
        correct_answer = "A"

    return {
        "question_id": str(payload.get("question_id") or f"generated-{concept.concept_id}-{uuid4().hex[:8]}"),
        "concept_id": concept.concept_id,
        "concept_name": concept.name,
        "source_type": str(payload.get("source_type") or "generated"),
        "source_record_id": str(payload.get("source_record_id") or concept.concept_id),
        "fingerprint": str(payload.get("fingerprint") or f"{concept.concept_id}:{question[:48]}"),
        "question": question,
        "options": options,
        "correct_answer": correct_answer,
        "explanation": str(payload.get("explanation") or f"复习 {concept.name} 的核心概念与边界条件。").strip(),
        "key_point": str(payload.get("key_point") or concept.name).strip() or concept.name,
        "difficulty": str(payload.get("difficulty") or "basic").strip() or "basic",
        "question_type": str(payload.get("question_type") or payload.get("type") or "A1").strip() or "A1",
        "is_wrong_answer": bool(payload.get("is_wrong_answer")),
    }


def _build_fallback_quiz_question(
    concept: ConceptMastery,
    *,
    question_index: int,
    chapter: Optional[Chapter],
) -> Dict[str, Any]:
    return _normalize_question_blueprint(
        {
            "question_id": f"fallback-{concept.concept_id}-{question_index}",
            "source_type": "generated_template",
            "source_record_id": f"{concept.concept_id}-{question_index}",
            "fingerprint": f"generated_template:{concept.concept_id}:{question_index}",
            "question": _fallback_question_text(concept, chapter=chapter, variant=question_index),
            "options": _fallback_option_map(concept, chapter=chapter),
            "correct_answer": "A",
            "explanation": f"先回到 {concept.name} 的定义、适用条件和典型陷阱，再做迁移练习。",
            "key_point": concept.name,
            "difficulty": "basic" if question_index % 2 == 0 else "advanced",
            "question_type": "A1" if question_index % 2 == 0 else "A2",
        },
        concept=concept,
        chapter=chapter,
    )


def _fallback_question_text(
    concept: ConceptMastery,
    *,
    chapter: Optional[Chapter],
    variant: int,
) -> str:
    chapter_name = _chapter_label(chapter)
    prompts = [
        f"关于“{concept.name}”，下列哪项最适合作为重新复习的第一步？",
        f"如果要巩固“{concept.name}”，下列哪项最能避免再次出错？",
        f"在复盘“{concept.name}”时，以下哪种做法最合理？",
    ]
    if chapter_name:
        prompts[1] = f"结合 {chapter_name} 的内容，巩固“{concept.name}”时最先应该确认什么？"
    return prompts[variant % len(prompts)]


def _fallback_option_map(
    concept: ConceptMastery,
    *,
    chapter: Optional[Chapter],
) -> Dict[str, str]:
    chapter_hint = _chapter_label(chapter) or "当前章节"
    return {
        "A": f"先确认 {concept.name} 的定义、边界和在 {chapter_hint} 中的典型场景",
        "B": f"跳过 {concept.name} 的解析，只记最后答案",
        "C": f"把 {concept.name} 与相近概念视为完全等价",
        "D": f"只刷新题，不回看与 {concept.name} 相关的错题",
    }


def _resolve_quiz_session_title(
    args: GenerateQuizSetArgs,
    concepts: List[ConceptMastery],
    chapter_map: Dict[str, Chapter],
) -> str:
    title = " ".join((args.title or "").split())
    if title:
        return title[:120]
    if len(concepts) == 1:
        return f"{concepts[0].name} 巩固题组"
    primary_chapter = chapter_map.get(_resolve_primary_chapter_id(concepts) or "")
    if primary_chapter is not None:
        return f"{_chapter_label(primary_chapter)} 巩固题组"
    return f"Agent 题组 {datetime.now().strftime('%m-%d %H:%M')}"


def _resolve_primary_chapter_id(concepts: List[ConceptMastery]) -> Optional[str]:
    counts: Dict[str, int] = {}
    for concept in concepts:
        chapter_id = str(concept.chapter_id or "").strip()
        if not chapter_id:
            continue
        counts[chapter_id] = counts.get(chapter_id, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _chapter_label(chapter: Optional[Chapter]) -> Optional[str]:
    if chapter is None:
        return None
    parts = [chapter.book, chapter.chapter_number, chapter.chapter_title]
    return " / ".join([str(part).strip() for part in parts if str(part or "").strip()]) or None


def _quiz_source_breakdown(questions: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for question in questions:
        source_type = str(question.get("source_type") or "generated_template")
        counts[source_type] = counts.get(source_type, 0) + 1
    return counts


def _format_source_breakdown(source_breakdown: Dict[str, int]) -> str:
    labels = {
        "wrong_answer": "错题改编",
        "test_record": "历史题",
        "generated_template": "模板题",
    }
    parts = []
    for source_type, count in source_breakdown.items():
        label = labels.get(source_type, source_type)
        parts.append(f"{label} {int(count)}")
    return " / ".join(parts)


def _question_fingerprint(question: Dict[str, Any]) -> str:
    raw = str(question.get("fingerprint") or "").strip()
    if raw:
        return raw
    return " ".join(str(question.get("question") or "").split()).lower()


def _build_daily_review_candidates_for_actor(
    db: Session,
    *,
    paper_date: date,
    wrong_answer_ids: List[int],
    user_id: Optional[str],
    device_id: Optional[str],
):
    query = _apply_actor_scope(
        db.query(WrongAnswerV2),
        WrongAnswerV2,
        user_id=user_id,
        device_id=device_id,
    ).filter(WrongAnswerV2.mastery_status == "active")

    if wrong_answer_ids:
        query = query.filter(WrongAnswerV2.id.in_(wrong_answer_ids))

    active_items = query.all()
    if wrong_answer_ids:
        found_ids = {int(item.id) for item in active_items}
        missing = [item_id for item_id in wrong_answer_ids if item_id not in found_ids]
        if missing:
            raise ValueError(f"部分错题不存在、非 active，或无权操作: {missing}")

    recent_stems = _get_recent_daily_review_stems(
        db,
        paper_date,
        actor_key=build_actor_key(user_id, device_id),
        actor_keys=build_actor_key_aliases(user_id, device_id),
    )
    due_candidates = []
    supplement_candidates = []

    for wrong_answer in active_items:
        is_due = bool(wrong_answer.next_review_date and wrong_answer.next_review_date <= paper_date)
        source_bucket = "due" if is_due else "supplement"
        candidate = _candidate_from_wrong_answer(wrong_answer, source_bucket, recent_stems)
        if is_due:
            due_candidates.append(candidate)
        else:
            supplement_candidates.append(candidate)

    due_strict = _sort_due_candidates([item for item in due_candidates if not item.recently_used])
    supplement_strict = _sort_supplement_candidates([item for item in supplement_candidates if not item.recently_used])
    due_relaxed = _sort_due_candidates([item for item in due_candidates if item.recently_used])
    supplement_relaxed = _sort_supplement_candidates([item for item in supplement_candidates if item.recently_used])
    return due_strict + supplement_strict + due_relaxed + supplement_relaxed


def _get_daily_review_paper_for_actor(
    db: Session,
    *,
    paper_date: date,
    actor_key: str,
    actor_keys: Optional[List[str]] = None,
) -> Optional[DailyReviewPaper]:
    query = db.query(DailyReviewPaper).filter(DailyReviewPaper.paper_date == paper_date)
    if actor_keys:
        query = query.filter(DailyReviewPaper.actor_key.in_(actor_keys))
    else:
        query = query.filter(DailyReviewPaper.actor_key == actor_key)
    return query.order_by(desc(DailyReviewPaper.updated_at), desc(DailyReviewPaper.id)).first()


def _serialize_daily_review_paper_snapshot(paper: Optional[DailyReviewPaper]) -> Dict[str, Any]:
    if paper is None:
        return {}
    items = sorted(paper.items, key=lambda item: item.position)
    return {
        "id": int(paper.id),
        "user_id": paper.user_id,
        "device_id": paper.device_id,
        "actor_key": paper.actor_key,
        "paper_date": _iso_date(paper.paper_date),
        "total_questions": int(paper.total_questions or 0),
        "config": dict(paper.config or {}),
        "items": [
            {
                "wrong_answer_id": int(item.wrong_answer_id),
                "position": int(item.position),
                "stem_fingerprint": item.stem_fingerprint,
                "source_bucket": item.source_bucket,
                "snapshot": dict(item.snapshot or {}),
            }
            for item in items
        ],
    }


def _restore_daily_review_paper_snapshot(paper: DailyReviewPaper, snapshot: Dict[str, Any]) -> None:
    paper.user_id = snapshot.get("user_id")
    paper.device_id = snapshot.get("device_id")
    paper.actor_key = str(snapshot.get("actor_key") or "")
    paper.paper_date = _parse_iso_date(snapshot.get("paper_date")) or paper.paper_date
    paper.total_questions = int(snapshot.get("total_questions") or 0)
    paper.config = dict(snapshot.get("config") or {})
    paper.updated_at = datetime.now()
    paper.items.clear()
    for item in sorted(snapshot.get("items") or [], key=lambda entry: int(entry.get("position") or 0)):
        paper.items.append(
            DailyReviewPaperItem(
                wrong_answer_id=int(item.get("wrong_answer_id") or 0),
                position=int(item.get("position") or 0),
                stem_fingerprint=str(item.get("stem_fingerprint") or ""),
                source_bucket=str(item.get("source_bucket") or "due"),
                snapshot=dict(item.get("snapshot") or {}),
            )
        )


def _verify_daily_review_snapshot(paper: Optional[DailyReviewPaper], snapshot: Dict[str, Any]) -> None:
    if paper is None:
        raise ValueError("复习卷回滚后未找到应恢复的题卷")
    expected_items = sorted(snapshot.get("items") or [], key=lambda entry: int(entry.get("position") or 0))
    actual_items = sorted(paper.items, key=lambda item: item.position)
    if paper.actor_key != str(snapshot.get("actor_key") or ""):
        raise ValueError("复习卷回滚后 actor_key 不一致")
    if _iso_date(paper.paper_date) != snapshot.get("paper_date"):
        raise ValueError("复习卷回滚后日期不一致")
    if int(paper.total_questions or 0) != int(snapshot.get("total_questions") or 0):
        raise ValueError("复习卷回滚后题量不一致")
    if len(actual_items) != len(expected_items):
        raise ValueError("复习卷回滚后题目数量不一致")
    for actual, expected in zip(actual_items, expected_items):
        if (
            int(actual.wrong_answer_id) != int(expected.get("wrong_answer_id") or 0)
            or int(actual.position) != int(expected.get("position") or 0)
            or str(actual.stem_fingerprint or "") != str(expected.get("stem_fingerprint") or "")
            or str(actual.source_bucket or "") != str(expected.get("source_bucket") or "")
            or dict(actual.snapshot or {}) != dict(expected.get("snapshot") or {})
        ):
            raise ValueError("复习卷回滚后题目快照不一致")


def _upsert_action_log(
    db: Session,
    *,
    session: AgentSession,
    payload: AgentActionExecuteRequest,
    tool_definition: AgentToolDefinition,
    normalized_args: Dict[str, Any],
    preview_summary: str,
    preview_context: Dict[str, Any],
    action_log: Optional[AgentActionLog],
    pending_confirmation: bool,
    related_task_id: Optional[str],
) -> AgentActionLog:
    now = datetime.now()
    if action_log is None:
        action_log = AgentActionLog(
            id=str(uuid4()),
            session_id=session.id,
            created_at=now,
        )
        db.add(action_log)

    action_log.user_id = payload.user_id or session.user_id
    action_log.device_id = payload.device_id or session.device_id
    action_log.related_task_id = related_task_id
    action_log.tool_name = tool_definition.name
    action_log.tool_type = tool_definition.tool_type
    action_log.tool_args = normalized_args
    action_log.risk_level = tool_definition.risk_level
    action_log.triggered_by = payload.triggered_by
    action_log.preview_summary = preview_summary
    action_log.preview_context = preview_context
    action_log.updated_at = now
    action_log.result = {}
    action_log.affected_ids = []

    if pending_confirmation:
        action_log.approval_status = "pending"
        action_log.execution_status = "pending"
        action_log.verification_status = "skipped"
        action_log.error_message = None
        action_log.executed_at = None
        action_log.confirmed_at = None
        return action_log

    action_log.approval_status = "approved" if tool_definition.requires_confirmation else "auto"
    action_log.confirmed_at = now if tool_definition.requires_confirmation and payload.confirm else None
    action_log.execution_status = "pending"
    action_log.verification_status = "skipped"
    action_log.error_message = None
    action_log.executed_at = None
    return action_log


def _resolve_wrong_answer_target_status(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "mastered":
        return "archived"
    if normalized not in {"active", "archived"}:
        raise ValueError(f"不支持的错题状态: {value}")
    return normalized


def _apply_actor_scope(query, model, *, user_id: Optional[str], device_id: Optional[str]):
    user_id, device_id = resolve_query_identity(user_id, device_id)
    if user_id and hasattr(model, "user_id"):
        query = query.filter(model.user_id == user_id)
    if device_id and hasattr(model, "device_id"):
        query = query.filter(model.device_id == device_id)
    return query


def _dedupe_ints(values: List[int]) -> List[int]:
    deduped: List[int] = []
    seen = set()
    for raw in values:
        value = int(raw)
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _dedupe_strings(values: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for raw in values:
        value = " ".join(str(raw or "").split())
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _join_concept_ids(values: List[str]) -> str:
    return ", ".join([str(item).strip() for item in values if str(item or "").strip()][:6])


def _shorten(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _round_metric(value: float) -> float:
    return round(_clamp_metric(value), 4)


def _clamp_metric(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


def _float_matches(left: Any, right: Any, *, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _parse_iso_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _iso_datetime(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _iso_date(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None
