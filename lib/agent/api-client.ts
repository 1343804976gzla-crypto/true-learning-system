import { API_BASE } from './constants'
import { getIdentityParams } from './identity'
import type {
  AgentSessionCreateRequest,
  AgentSessionItem,
  AgentSessionListResponse,
  AgentMessageListResponse,
  AgentTurnStateListResponse,
  AgentChatRequest,
  AgentChatResponse,
  AgentSummaryResponse,
  AgentTaskCreateRequest,
  AgentTaskListResponse,
  AgentTaskDetailResponse,
  AgentTaskStatusUpdateRequest,
  AgentActionExecuteRequest,
  AgentActionExecuteResponse,
  AgentActionListResponse,
  AgentToolDefinition,
  ReferenceStatusResponse,
} from './types'

// ─── Error ───────────────────────────────────────────────────────

export class AgentApiError extends Error {
  constructor(
    public status: number,
    public detail: string
  ) {
    super(`[${status}] ${detail}`)
    this.name = 'AgentApiError'
  }
}

// ─── Internal Helpers ────────────────────────────────────────────

function identityQuery(): string {
  const params = getIdentityParams()
  const qs = new URLSearchParams()
  qs.set('device_id', params.device_id)
  if (params.user_id) qs.set('user_id', params.user_id)
  return qs.toString()
}

function injectIdentity<T extends Record<string, unknown>>(body: T): T {
  const params = getIdentityParams()
  return { ...body, ...params }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new AgentApiError(res.status, body.detail ?? res.statusText)
  }
  return res.json()
}

async function get<T>(path: string, extraParams?: Record<string, string>): Promise<T> {
  const qs = new URLSearchParams(identityQuery())
  if (extraParams) {
    for (const [k, v] of Object.entries(extraParams)) qs.set(k, v)
  }
  return fetchJson<T>(`${path}?${qs}`)
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return fetchJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(injectIdentity(body)),
  })
}

// ─── Sessions ────────────────────────────────────────────────────

export async function createSession(
  req: Omit<AgentSessionCreateRequest, 'user_id' | 'device_id'> = {}
): Promise<AgentSessionItem> {
  return post<AgentSessionItem>('/sessions', req as Record<string, unknown>)
}

export async function listSessions(params?: {
  status?: string
  limit?: number
}): Promise<AgentSessionListResponse> {
  const extra: Record<string, string> = {}
  if (params?.status) extra.status = params.status
  if (params?.limit) extra.limit = String(params.limit)
  return get<AgentSessionListResponse>('/sessions', extra)
}

export async function getSession(sessionId: string): Promise<AgentSessionItem> {
  return get<AgentSessionItem>(`/sessions/${sessionId}`)
}

export async function getMessages(
  sessionId: string,
  limit?: number
): Promise<AgentMessageListResponse> {
  const extra: Record<string, string> = {}
  if (limit) extra.limit = String(limit)
  return get<AgentMessageListResponse>(`/sessions/${sessionId}/messages`, extra)
}

export async function getTurnStates(
  sessionId: string,
  limit?: number
): Promise<AgentTurnStateListResponse> {
  const extra: Record<string, string> = {}
  if (limit) extra.limit = String(limit)
  return get<AgentTurnStateListResponse>(`/sessions/${sessionId}/turns`, extra)
}

export async function summarizeSession(sessionId: string): Promise<AgentSummaryResponse> {
  const qs = identityQuery()
  return fetchJson<AgentSummaryResponse>(`/sessions/${sessionId}/summarize?${qs}`, {
    method: 'POST',
  })
}

// ─── Chat ────────────────────────────────────────────────────────

export async function chat(
  req: Omit<AgentChatRequest, 'user_id' | 'device_id'>
): Promise<AgentChatResponse> {
  const body = {
    ...req,
    client_request_id: req.client_request_id ?? crypto.randomUUID(),
  }
  return post<AgentChatResponse>('/chat', body as Record<string, unknown>)
}

export async function chatStream(
  req: Omit<AgentChatRequest, 'user_id' | 'device_id'>,
  signal?: AbortSignal
): Promise<Response> {
  const body = injectIdentity({
    ...req,
    client_request_id: req.client_request_id ?? crypto.randomUUID(),
  })
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new AgentApiError(res.status, err.detail ?? res.statusText)
  }
  return res
}

// ─── Tasks ───────────────────────────────────────────────────────

export async function listTasks(
  sessionId: string,
  limit?: number
): Promise<AgentTaskListResponse> {
  const extra: Record<string, string> = {}
  if (limit) extra.limit = String(limit)
  return get<AgentTaskListResponse>(`/sessions/${sessionId}/tasks`, extra)
}

export async function createTask(
  req: Omit<AgentTaskCreateRequest, 'user_id' | 'device_id'>
): Promise<AgentTaskDetailResponse> {
  return post<AgentTaskDetailResponse>('/tasks', req as Record<string, unknown>)
}

export async function getTask(taskId: string): Promise<AgentTaskDetailResponse> {
  return get<AgentTaskDetailResponse>(`/tasks/${taskId}`)
}

export async function updateTaskStatus(
  taskId: string,
  req: Omit<AgentTaskStatusUpdateRequest, 'user_id' | 'device_id'>
): Promise<AgentTaskDetailResponse> {
  return post<AgentTaskDetailResponse>(`/tasks/${taskId}/status`, req as Record<string, unknown>)
}

// ─── Actions ─────────────────────────────────────────────────────

export async function listActions(
  sessionId: string,
  limit?: number
): Promise<AgentActionListResponse> {
  const extra: Record<string, string> = {}
  if (limit) extra.limit = String(limit)
  return get<AgentActionListResponse>(`/sessions/${sessionId}/actions`, extra)
}

export async function executeAction(
  req: Omit<AgentActionExecuteRequest, 'user_id' | 'device_id'>
): Promise<AgentActionExecuteResponse> {
  return post<AgentActionExecuteResponse>('/actions', req as Record<string, unknown>)
}

// ─── Tools & Reference ───────────────────────────────────────────

export async function listTools(): Promise<AgentToolDefinition[]> {
  return fetchJson<AgentToolDefinition[]>('/tools')
}

export async function getReferenceStatus(): Promise<ReferenceStatusResponse> {
  return fetchJson<ReferenceStatusResponse>('/reference/status')
}
