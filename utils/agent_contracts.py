from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


AgentType = Literal["tutor", "qa", "task"]
SessionStatus = Literal["active", "archived", "deleted"]
MessageRole = Literal["system", "user", "assistant", "tool"]
MessageStatus = Literal["completed", "error", "pending"]
ToolType = Literal["read", "write"]
RiskLevel = Literal["low", "medium", "high"]
ActionApprovalStatus = Literal["auto", "pending", "approved", "rejected"]
ActionExecutionStatus = Literal["pending", "success", "failed", "rolled_back"]
ActionVerificationStatus = Literal["verified", "mismatch", "skipped", "failed"]
ActionTriggeredBy = Literal["user_request", "agent_plan", "event"]
TaskStatus = Literal["pending", "ready", "running", "verifying", "paused", "completed", "failed", "cancelled"]
TaskPriority = Literal["low", "medium", "high"]
TaskSource = Literal["manual", "plan", "event"]


class AgentContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentSessionCreateRequest(AgentContractModel):
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    title: Optional[str] = None
    agent_type: AgentType = "tutor"
    model: str = "deepseek-chat"
    provider: str = "deepseek"
    prompt_template_id: Optional[str] = None


class AgentChatRequest(AgentContractModel):
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    client_request_id: Optional[str] = None
    message: str = Field(min_length=1, max_length=8000)
    agent_type: AgentType = "tutor"
    model: str = "deepseek-chat"
    provider: str = "deepseek"
    prompt_template_id: Optional[str] = None
    requested_tools: List[str] = Field(default_factory=list)
    tool_overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class AgentContextUsage(AgentContractModel):
    system_prompt_tokens: int = 0
    session_summary_tokens: int = 0
    memory_tokens: int = 0
    recent_messages_tokens: int = 0
    learning_data_tokens: int = 0
    request_analysis_tokens: int = 0
    plan_outline_tokens: int = 0
    response_strategy_tokens: int = 0
    reserved_output_tokens: int = 0
    total_estimated_tokens: int = 0


class AgentSourceStat(AgentContractModel):
    label: str
    value: str


class AgentSourceCard(AgentContractModel):
    tool_name: str
    title: str
    summary: str
    count: int = 0
    stats: List[AgentSourceStat] = Field(default_factory=list)
    bullets: List[str] = Field(default_factory=list)


class AgentPlanSubtask(AgentContractModel):
    id: str
    title: str
    description: str
    status: str
    priority: str
    tools: List[str] = Field(default_factory=list)


class AgentPlanTask(AgentContractModel):
    id: str
    title: str
    description: str
    status: str
    priority: str
    level: int = 0
    dependencies: List[str] = Field(default_factory=list)
    subtasks: List[AgentPlanSubtask] = Field(default_factory=list)


class AgentPlanBundle(AgentContractModel):
    summary: str
    tasks: List[AgentPlanTask] = Field(default_factory=list)


class AgentSessionItem(AgentContractModel):
    id: str
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    title: str
    agent_type: AgentType
    status: SessionStatus
    model: str
    provider: str
    prompt_template_id: str
    context_summary: Optional[str] = None
    message_count: int = 0
    last_message_preview: Optional[str] = None
    last_message_at: Optional[str] = None
    created_at: str
    updated_at: str


class AgentSessionListResponse(AgentContractModel):
    total: int
    sessions: List[AgentSessionItem] = Field(default_factory=list)


class AgentMessageItem(AgentContractModel):
    id: int
    session_id: str
    role: MessageRole
    content: str
    content_structured: Dict[str, Any] = Field(default_factory=dict)
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[Dict[str, Any]] = None
    message_status: MessageStatus = "completed"
    token_input: int = 0
    token_output: int = 0
    latency_ms: int = 0
    trace_id: Optional[str] = None
    created_at: str


class AgentMessageListResponse(AgentContractModel):
    total: int
    messages: List[AgentMessageItem] = Field(default_factory=list)


class AgentToolDefinition(AgentContractModel):
    name: str
    description: str
    default_limit: int = 0
    keywords: List[str] = Field(default_factory=list)
    tool_type: ToolType = "read"
    risk_level: RiskLevel = "low"
    requires_confirmation: bool = False


class AgentToolCallItem(AgentContractModel):
    id: int
    session_id: str
    message_id: Optional[int] = None
    tool_name: str
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    tool_result: Optional[Dict[str, Any]] = None
    success: bool
    error_message: Optional[str] = None
    duration_ms: int = 0
    created_at: str


class AgentTurnStateItem(AgentContractModel):
    id: int
    session_id: str
    user_message_id: int
    assistant_message_id: Optional[int] = None
    trace_id: str
    status: str
    goal: Optional[str] = None
    request_analysis: Dict[str, Any] = Field(default_factory=dict)
    selected_tools: List[str] = Field(default_factory=list)
    tool_snapshots: List[Dict[str, Any]] = Field(default_factory=list)
    plan_draft: Dict[str, Any] = Field(default_factory=dict)
    plan_final: Dict[str, Any] = Field(default_factory=dict)
    execution_state: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: str
    updated_at: str


class AgentTurnStateListResponse(AgentContractModel):
    total: int
    turns: List[AgentTurnStateItem] = Field(default_factory=list)


class AgentChatResponse(AgentContractModel):
    session: AgentSessionItem
    user_message: AgentMessageItem
    assistant_message: AgentMessageItem
    tool_calls: List[AgentToolCallItem] = Field(default_factory=list)
    context_usage: AgentContextUsage
    trace_id: str
    error_message: Optional[str] = None


class AgentSummaryResponse(AgentContractModel):
    session_id: str
    summary: str
    memory_id: int
    message_count: int


class AgentActionExecuteRequest(AgentContractModel):
    session_id: str
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    task_id: Optional[str] = None
    action_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False
    rollback: bool = False
    triggered_by: ActionTriggeredBy = "user_request"


class AgentActionLogItem(AgentContractModel):
    id: str
    session_id: str
    related_task_id: Optional[str] = None
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    tool_name: str
    tool_type: ToolType = "write"
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    approval_status: ActionApprovalStatus
    execution_status: ActionExecutionStatus
    triggered_by: ActionTriggeredBy
    preview_summary: Optional[str] = None
    affected_ids: List[Any] = Field(default_factory=list)
    result: Dict[str, Any] = Field(default_factory=dict)
    verification_status: Optional[ActionVerificationStatus] = None
    error_message: Optional[str] = None
    can_rollback: bool = False
    rollback_hint: Optional[str] = None
    confirmed_at: Optional[str] = None
    executed_at: Optional[str] = None
    created_at: str
    updated_at: str


class AgentActionListResponse(AgentContractModel):
    total: int
    actions: List[AgentActionLogItem] = Field(default_factory=list)


class AgentActionExecuteResponse(AgentContractModel):
    action: AgentActionLogItem
    executed: bool
    requires_confirmation: bool = False
    preview_summary: Optional[str] = None


class AgentTaskActionSuggestionItem(AgentContractModel):
    id: str
    tool_name: str
    title: Optional[str] = None
    summary: Optional[str] = None
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = "medium"
    requires_confirmation: bool = False
    related_action_id: Optional[str] = None
    approval_status: ActionApprovalStatus = "pending"
    execution_status: ActionExecutionStatus = "pending"
    verification_status: Optional[ActionVerificationStatus] = None
    preview_summary: Optional[str] = None
    affected_ids: List[Any] = Field(default_factory=list)
    error_message: Optional[str] = None
    confirmed_at: Optional[str] = None
    executed_at: Optional[str] = None
    updated_at: Optional[str] = None


class AgentTaskEventItem(AgentContractModel):
    id: int
    task_id: str
    session_id: str
    event_type: str
    from_status: Optional[TaskStatus] = None
    to_status: Optional[TaskStatus] = None
    note: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AgentTaskItem(AgentContractModel):
    id: str
    session_id: str
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    related_turn_state_id: Optional[int] = None
    title: str
    goal: Optional[str] = None
    status: TaskStatus
    priority: TaskPriority = "medium"
    source: TaskSource = "plan"
    plan_summary: Optional[str] = None
    plan_bundle: Dict[str, Any] = Field(default_factory=dict)
    action_suggestions: List[AgentTaskActionSuggestionItem] = Field(default_factory=list)
    task_count: int = 0
    completed_task_count: int = 0
    subtask_count: int = 0
    completed_subtask_count: int = 0
    suggested_action_count: int = 0
    pending_action_count: int = 0
    previewed_action_count: int = 0
    completed_action_count: int = 0
    failed_action_count: int = 0
    rolled_back_action_count: int = 0
    latest_action_at: Optional[str] = None
    available_transitions: List[TaskStatus] = Field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    last_transition_at: Optional[str] = None
    created_at: str
    updated_at: str


class AgentTaskListResponse(AgentContractModel):
    total: int
    tasks: List[AgentTaskItem] = Field(default_factory=list)


class AgentTaskDetailResponse(AgentContractModel):
    task: AgentTaskItem
    events: List[AgentTaskEventItem] = Field(default_factory=list)
    linked_actions: List[AgentActionLogItem] = Field(default_factory=list)


class AgentTaskCreateRequest(AgentContractModel):
    session_id: str
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    related_turn_state_id: Optional[int] = None
    title: Optional[str] = None
    goal: Optional[str] = None
    priority: TaskPriority = "medium"
    source: TaskSource = "plan"
    initial_status: TaskStatus = "ready"
    plan_summary: Optional[str] = None
    plan_bundle: Dict[str, Any] = Field(default_factory=dict)
    action_suggestions: List[Dict[str, Any]] = Field(default_factory=list)
    note: Optional[str] = None


class AgentTaskStatusUpdateRequest(AgentContractModel):
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    status: TaskStatus
    note: Optional[str] = None
