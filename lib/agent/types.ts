// ─── Literal Types ───────────────────────────────────────────────

export type AgentType = 'tutor' | 'qa' | 'task'
export type SessionStatus = 'active' | 'archived' | 'deleted'
export type MessageRole = 'system' | 'user' | 'assistant' | 'tool'
export type MessageStatus = 'completed' | 'error' | 'pending'
export type ToolType = 'read' | 'write'
export type RiskLevel = 'low' | 'medium' | 'high'
export type ActionApprovalStatus = 'auto' | 'pending' | 'approved' | 'rejected'
export type ActionExecutionStatus = 'pending' | 'success' | 'failed' | 'rolled_back'
export type ActionVerificationStatus = 'verified' | 'mismatch' | 'skipped' | 'failed'
export type ActionTriggeredBy = 'user_request' | 'agent_plan' | 'event'
export type TaskStatus = 'pending' | 'ready' | 'running' | 'verifying' | 'paused' | 'completed' | 'failed' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high'
export type TaskSource = 'manual' | 'plan' | 'event'

// ─── Request Models ──────────────────────────────────────────────

export interface AgentSessionCreateRequest {
  user_id?: string | null
  device_id?: string | null
  title?: string | null
  agent_type?: AgentType
  model?: string
  provider?: string
  prompt_template_id?: string | null
}

export interface AgentChatRequest {
  session_id?: string | null
  user_id?: string | null
  device_id?: string | null
  client_request_id?: string | null
  message: string
  agent_type?: AgentType
  model?: string
  provider?: string
  prompt_template_id?: string | null
  requested_tools?: string[]
  tool_overrides?: Record<string, Record<string, unknown>>
}

export interface AgentActionExecuteRequest {
  session_id: string
  user_id?: string | null
  device_id?: string | null
  task_id?: string | null
  action_id?: string | null
  tool_name?: string | null
  tool_args?: Record<string, unknown>
  confirm?: boolean
  rollback?: boolean
  triggered_by?: ActionTriggeredBy
}

export interface AgentTaskCreateRequest {
  session_id: string
  user_id?: string | null
  device_id?: string | null
  related_turn_state_id?: number | null
  title?: string | null
  goal?: string | null
  priority?: TaskPriority
  source?: TaskSource
  initial_status?: TaskStatus
  plan_summary?: string | null
  plan_bundle?: Record<string, unknown>
  action_suggestions?: Record<string, unknown>[]
  note?: string | null
}

export interface AgentTaskStatusUpdateRequest {
  user_id?: string | null
  device_id?: string | null
  status: TaskStatus
  note?: string | null
}

// ─── Response Models ─────────────────────────────────────────────

export interface AgentContextUsage {
  system_prompt_tokens: number
  session_summary_tokens: number
  memory_tokens: number
  recent_messages_tokens: number
  learning_data_tokens: number
  request_analysis_tokens: number
  plan_outline_tokens: number
  response_strategy_tokens: number
  reserved_output_tokens: number
  total_estimated_tokens: number
}

export interface AgentSourceStat {
  label: string
  value: string
}

export interface AgentSourceCard {
  tool_name: string
  title: string
  summary: string
  count: number
  stats: AgentSourceStat[]
  bullets: string[]
}

export interface AgentPlanSubtask {
  id: string
  title: string
  description: string
  status: string
  priority: string
  tools: string[]
}

export interface AgentPlanTask {
  id: string
  title: string
  description: string
  status: string
  priority: string
  level: number
  dependencies: string[]
  subtasks: AgentPlanSubtask[]
}

export interface AgentPlanBundle {
  summary: string
  tasks: AgentPlanTask[]
}

export interface AgentSessionItem {
  id: string
  user_id: string | null
  device_id: string | null
  title: string
  agent_type: AgentType
  status: SessionStatus
  model: string
  provider: string
  prompt_template_id: string
  context_summary: string | null
  message_count: number
  last_message_preview: string | null
  last_message_at: string | null
  created_at: string
  updated_at: string
}

export interface AgentSessionListResponse {
  total: number
  sessions: AgentSessionItem[]
}

export interface AgentMessageItem {
  id: number
  session_id: string
  role: MessageRole
  content: string
  content_structured: Record<string, unknown>
  tool_name: string | null
  tool_input: Record<string, unknown> | null
  tool_output: Record<string, unknown> | null
  message_status: MessageStatus
  token_input: number
  token_output: number
  latency_ms: number
  trace_id: string | null
  created_at: string
}

export interface AgentMessageListResponse {
  total: number
  messages: AgentMessageItem[]
}

export interface AgentToolDefinition {
  name: string
  description: string
  default_limit: number
  keywords: string[]
  tool_type: ToolType
  risk_level: RiskLevel
  requires_confirmation: boolean
}

export interface AgentToolCallItem {
  id: number
  session_id: string
  message_id: number | null
  tool_name: string
  tool_args: Record<string, unknown>
  tool_result: Record<string, unknown> | null
  success: boolean
  error_message: string | null
  duration_ms: number
  created_at: string
}

export interface AgentTurnStateItem {
  id: number
  session_id: string
  user_message_id: number
  assistant_message_id: number | null
  trace_id: string
  status: string
  goal: string | null
  request_analysis: Record<string, unknown>
  selected_tools: string[]
  tool_snapshots: Record<string, unknown>[]
  plan_draft: Record<string, unknown>
  plan_final: Record<string, unknown>
  execution_state: Record<string, unknown>
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface AgentTurnStateListResponse {
  total: number
  turns: AgentTurnStateItem[]
}

export interface AgentChatResponse {
  session: AgentSessionItem
  user_message: AgentMessageItem
  assistant_message: AgentMessageItem
  tool_calls: AgentToolCallItem[]
  context_usage: AgentContextUsage
  trace_id: string
  error_message: string | null
}

export interface AgentSummaryResponse {
  session_id: string
  summary: string
  memory_id: number
  message_count: number
}

export interface AgentActionLogItem {
  id: string
  session_id: string
  related_task_id: string | null
  user_id: string | null
  device_id: string | null
  tool_name: string
  tool_type: ToolType
  tool_args: Record<string, unknown>
  risk_level: RiskLevel
  approval_status: ActionApprovalStatus
  execution_status: ActionExecutionStatus
  triggered_by: ActionTriggeredBy
  preview_summary: string | null
  affected_ids: unknown[]
  result: Record<string, unknown>
  verification_status: ActionVerificationStatus | null
  error_message: string | null
  can_rollback: boolean
  rollback_hint: string | null
  confirmed_at: string | null
  executed_at: string | null
  created_at: string
  updated_at: string
}

export interface AgentActionListResponse {
  total: number
  actions: AgentActionLogItem[]
}

export interface AgentActionExecuteResponse {
  action: AgentActionLogItem
  executed: boolean
  requires_confirmation: boolean
  preview_summary: string | null
}

export interface AgentTaskActionSuggestionItem {
  id: string
  tool_name: string
  title: string | null
  summary: string | null
  tool_args: Record<string, unknown>
  risk_level: RiskLevel
  requires_confirmation: boolean
  related_action_id: string | null
  approval_status: ActionApprovalStatus
  execution_status: ActionExecutionStatus
  verification_status: ActionVerificationStatus | null
  preview_summary: string | null
  affected_ids: unknown[]
  error_message: string | null
  confirmed_at: string | null
  executed_at: string | null
  updated_at: string | null
}

export interface AgentTaskEventItem {
  id: number
  task_id: string
  session_id: string
  event_type: string
  from_status: TaskStatus | null
  to_status: TaskStatus | null
  note: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface AgentTaskItem {
  id: string
  session_id: string
  user_id: string | null
  device_id: string | null
  related_turn_state_id: number | null
  title: string
  goal: string | null
  status: TaskStatus
  priority: TaskPriority
  source: TaskSource
  plan_summary: string | null
  plan_bundle: Record<string, unknown>
  action_suggestions: AgentTaskActionSuggestionItem[]
  task_count: number
  completed_task_count: number
  subtask_count: number
  completed_subtask_count: number
  suggested_action_count: number
  pending_action_count: number
  previewed_action_count: number
  completed_action_count: number
  failed_action_count: number
  rolled_back_action_count: number
  latest_action_at: string | null
  available_transitions: TaskStatus[]
  started_at: string | null
  completed_at: string | null
  last_transition_at: string | null
  created_at: string
  updated_at: string
}

export interface AgentTaskListResponse {
  total: number
  tasks: AgentTaskItem[]
}

export interface AgentTaskDetailResponse {
  task: AgentTaskItem
  events: AgentTaskEventItem[]
  linked_actions: AgentActionLogItem[]
}

// ─── SSE Event Payloads ──────────────────────────────────────────

export interface SSEReadyEvent {
  message: string
}

export interface SSESessionEvent {
  session: AgentSessionItem
  user_message: AgentMessageItem
  trace_id: string
}

export interface SSEToolCallEvent extends AgentToolCallItem {}

export interface SSEMessageStartEvent {
  assistant_message_id: number | null
  message_status: MessageStatus
  context_usage: AgentContextUsage
  sources: AgentSourceCard[]
  plan: AgentPlanBundle | null
  action_suggestions: AgentTaskActionSuggestionItem[]
  response_strategy: ResponseStrategy | null
  execution_state: Record<string, unknown>
  turn_state_id: number
}

export interface SSEDeltaEvent {
  content: string
}

export interface SSEDoneEvent extends AgentChatResponse {}

export interface SSEErrorEvent {
  detail: string
}

// ─── Response Strategy ───────────────────────────────────────────

export interface ResponseStrategy {
  strategy: 'answer' | 'answer_with_caveat' | 'clarify'
  reason?: string
  instruction?: string
  clarifying_questions?: string[]
}

// ─── Content Structured ──────────────────────────────────────────

export interface ContentStructured {
  selected_tools?: string[]
  request_analysis?: Record<string, unknown>
  response_strategy?: ResponseStrategy
  context_usage?: AgentContextUsage
  memories?: unknown[]
  sources?: AgentSourceCard[]
  plan?: AgentPlanBundle
  action_suggestions?: AgentTaskActionSuggestionItem[]
  execution_state?: Record<string, unknown>
  turn_state_id?: number
}

// ─── Reference Status ────────────────────────────────────────────

export interface ReferenceStatusResponse {
  [key: string]: {
    available: boolean
    [key: string]: unknown
  }
}
