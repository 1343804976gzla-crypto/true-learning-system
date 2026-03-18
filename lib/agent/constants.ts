export const API_BASE = '/api/agent'

export const SSE_EVENTS = {
  READY: 'ready',
  SESSION: 'session',
  TOOL_CALL: 'tool_call',
  MESSAGE_START: 'message_start',
  DELTA: 'delta',
  DONE: 'done',
  ERROR: 'error',
} as const

export const AGENT_TYPES = ['tutor', 'qa', 'task'] as const
export const SESSION_STATUSES = ['active', 'archived', 'deleted'] as const
export const MESSAGE_ROLES = ['system', 'user', 'assistant', 'tool'] as const
export const MESSAGE_STATUSES = ['completed', 'error', 'pending'] as const
export const TOOL_TYPES = ['read', 'write'] as const
export const RISK_LEVELS = ['low', 'medium', 'high'] as const
export const TASK_STATUSES = ['pending', 'ready', 'running', 'verifying', 'paused', 'completed', 'failed', 'cancelled'] as const
export const TASK_PRIORITIES = ['low', 'medium', 'high'] as const
export const TASK_SOURCES = ['manual', 'plan', 'event'] as const
export const ACTION_APPROVAL_STATUSES = ['auto', 'pending', 'approved', 'rejected'] as const
export const ACTION_EXECUTION_STATUSES = ['pending', 'success', 'failed', 'rolled_back'] as const
export const ACTION_VERIFICATION_STATUSES = ['verified', 'mismatch', 'skipped', 'failed'] as const
export const ACTION_TRIGGERED_BY = ['user_request', 'agent_plan', 'event'] as const

export const IDENTITY_STORAGE_KEY = 'tls_device_id'
export const DEVICE_ID_PREFIX = 'local-'
