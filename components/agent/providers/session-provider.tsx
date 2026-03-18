'use client'

import React, { createContext, useContext, useReducer, useCallback, useRef } from 'react'
import type {
  AgentSessionItem,
  AgentMessageItem,
  AgentToolCallItem,
  SSEMessageStartEvent,
} from '@/lib/agent/types'
import * as api from '@/lib/agent/api-client'
import { parseSSEStream } from '@/lib/agent/sse-parser'

// ─── State ───────────────────────────────────────────────────────

type StreamStatus = 'idle' | 'connecting' | 'prepared' | 'streaming' | 'completed' | 'error'

interface SessionState {
  sessions: AgentSessionItem[]
  activeSessionId: string | null
  messages: AgentMessageItem[]
  streamStatus: StreamStatus
  streamingContent: string
  streamingMeta: SSEMessageStartEvent | null
  streamingToolCalls: AgentToolCallItem[]
  error: string | null
}

const initialState: SessionState = {
  sessions: [],
  activeSessionId: null,
  messages: [],
  streamStatus: 'idle',
  streamingContent: '',
  streamingMeta: null,
  streamingToolCalls: [],
  error: null,
}

// ─── Actions ─────────────────────────────────────────────────────

type Action =
  | { type: 'SET_SESSIONS'; sessions: AgentSessionItem[] }
  | { type: 'ADD_SESSION'; session: AgentSessionItem }
  | { type: 'UPDATE_SESSION'; session: AgentSessionItem }
  | { type: 'SET_ACTIVE_SESSION'; id: string | null }
  | { type: 'SET_MESSAGES'; messages: AgentMessageItem[] }
  | { type: 'APPEND_MESSAGE'; message: AgentMessageItem }
  | { type: 'STREAM_CONNECTING' }
  | { type: 'STREAM_PREPARED'; meta: SSEMessageStartEvent }
  | { type: 'STREAM_TOOL_CALL'; toolCall: AgentToolCallItem }
  | { type: 'STREAM_DELTA'; content: string }
  | { type: 'STREAM_DONE'; message: AgentMessageItem; session: AgentSessionItem }
  | { type: 'STREAM_ERROR'; error: string }
  | { type: 'CLEAR_ERROR' }

function reducer(state: SessionState, action: Action): SessionState {
  switch (action.type) {
    case 'SET_SESSIONS':
      return { ...state, sessions: action.sessions }
    case 'ADD_SESSION':
      return { ...state, sessions: [action.session, ...state.sessions] }
    case 'UPDATE_SESSION':
      return {
        ...state,
        sessions: state.sessions.map(s => (s.id === action.session.id ? action.session : s)),
      }
    case 'SET_ACTIVE_SESSION':
      return {
        ...state,
        activeSessionId: action.id,
        messages: [],
        streamStatus: 'idle',
        streamingContent: '',
        streamingMeta: null,
        streamingToolCalls: [],
        error: null,
      }
    case 'SET_MESSAGES':
      return { ...state, messages: action.messages }
    case 'APPEND_MESSAGE':
      return { ...state, messages: [...state.messages, action.message] }
    case 'STREAM_CONNECTING':
      return { ...state, streamStatus: 'connecting', streamingContent: '', streamingMeta: null, streamingToolCalls: [], error: null }
    case 'STREAM_PREPARED':
      return { ...state, streamStatus: 'prepared', streamingMeta: action.meta }
    case 'STREAM_TOOL_CALL':
      return { ...state, streamingToolCalls: [...state.streamingToolCalls, action.toolCall] }
    case 'STREAM_DELTA':
      return { ...state, streamStatus: 'streaming', streamingContent: state.streamingContent + action.content }
    case 'STREAM_DONE':
      return {
        ...state,
        streamStatus: 'completed',
        streamingContent: '',
        streamingMeta: null,
        streamingToolCalls: [],
        messages: [...state.messages, action.message],
        sessions: state.sessions.map(s => (s.id === action.session.id ? action.session : s)),
      }
    case 'STREAM_ERROR':
      return { ...state, streamStatus: 'error', error: action.error }
    case 'CLEAR_ERROR':
      return { ...state, error: null }
    default:
      return state
  }
}

// ─── Context ─────────────────────────────────────────────────────

interface SessionContextValue extends SessionState {
  loadSessions: () => Promise<void>
  switchSession: (id: string) => Promise<void>
  createNewSession: (agentType?: 'tutor' | 'qa' | 'task') => Promise<AgentSessionItem>
  sendMessage: (text: string, sessionId?: string) => Promise<void>
  cancelStream: () => void
  clearError: () => void
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function useSession() {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used within SessionProvider')
  return ctx
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const abortRef = useRef<AbortController | null>(null)

  const loadSessions = useCallback(async () => {
    const res = await api.listSessions({ status: 'active', limit: 50 })
    dispatch({ type: 'SET_SESSIONS', sessions: res.sessions })
  }, [])

  const switchSession = useCallback(async (id: string) => {
    abortRef.current?.abort()
    dispatch({ type: 'SET_ACTIVE_SESSION', id })
    const res = await api.getMessages(id)
    dispatch({ type: 'SET_MESSAGES', messages: res.messages })
  }, [])

  const createNewSession = useCallback(async (agentType: 'tutor' | 'qa' | 'task' = 'tutor') => {
    const session = await api.createSession({ agent_type: agentType })
    dispatch({ type: 'ADD_SESSION', session })
    dispatch({ type: 'SET_ACTIVE_SESSION', id: session.id })
    return session
  }, [])

  const sendMessage = useCallback(async (text: string, sessionId?: string) => {
    if (state.streamStatus === 'connecting' || state.streamStatus === 'streaming') return

    const targetSessionId = sessionId ?? state.activeSessionId
    abortRef.current = new AbortController()
    dispatch({ type: 'STREAM_CONNECTING' })

    try {
      const response = await api.chatStream(
        { session_id: targetSessionId, message: text },
        abortRef.current.signal
      )

      await parseSSEStream(response, {
        onSession: (data) => {
          if (!state.activeSessionId) {
            dispatch({ type: 'ADD_SESSION', session: data.session })
            dispatch({ type: 'SET_ACTIVE_SESSION', id: data.session.id })
          }
          dispatch({ type: 'APPEND_MESSAGE', message: data.user_message })
        },
        onToolCall: (data) => {
          dispatch({ type: 'STREAM_TOOL_CALL', toolCall: data })
        },
        onMessageStart: (data) => {
          dispatch({ type: 'STREAM_PREPARED', meta: data })
        },
        onDelta: (data) => {
          dispatch({ type: 'STREAM_DELTA', content: data.content })
        },
        onDone: (data) => {
          dispatch({
            type: 'STREAM_DONE',
            message: data.assistant_message,
            session: data.session,
          })
        },
        onError: (data) => {
          dispatch({ type: 'STREAM_ERROR', error: data.detail })
        },
      }, abortRef.current.signal)
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        dispatch({ type: 'STREAM_ERROR', error: (err as Error).message })
      }
    }
  }, [state.streamStatus, state.activeSessionId])

  const cancelStream = useCallback(() => {
    abortRef.current?.abort()
    dispatch({ type: 'STREAM_ERROR', error: '已取消' })
  }, [])

  const clearError = useCallback(() => {
    dispatch({ type: 'CLEAR_ERROR' })
  }, [])

  return (
    <SessionContext.Provider
      value={{
        ...state,
        loadSessions,
        switchSession,
        createNewSession,
        sendMessage,
        cancelStream,
        clearError,
      }}
    >
      {children}
    </SessionContext.Provider>
  )
}
