import type {
  SSEReadyEvent,
  SSESessionEvent,
  SSEToolCallEvent,
  SSEMessageStartEvent,
  SSEDeltaEvent,
  SSEDoneEvent,
  SSEErrorEvent,
} from './types'
import { SSE_EVENTS } from './constants'

export interface SSEHandlers {
  onReady?: (data: SSEReadyEvent) => void
  onSession?: (data: SSESessionEvent) => void
  onToolCall?: (data: SSEToolCallEvent) => void
  onMessageStart?: (data: SSEMessageStartEvent) => void
  onDelta?: (data: SSEDeltaEvent) => void
  onDone?: (data: SSEDoneEvent) => void
  onError?: (data: SSEErrorEvent) => void
}

export async function parseSSEStream(
  response: Response,
  handlers: SSEHandlers,
  signal?: AbortSignal
): Promise<void> {
  const body = response.body
  if (!body) throw new Error('Response body is null')

  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (signal?.aborted) {
        reader.cancel()
        return
      }

      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      const events = buffer.split('\n\n')
      buffer = events.pop() ?? ''

      for (const raw of events) {
        if (!raw.trim()) continue
        const parsed = parseSSEEvent(raw)
        if (!parsed) continue
        dispatchEvent(parsed.event, parsed.data, handlers)
      }
    }

    if (buffer.trim()) {
      const parsed = parseSSEEvent(buffer)
      if (parsed) dispatchEvent(parsed.event, parsed.data, handlers)
    }
  } finally {
    reader.releaseLock()
  }
}

function parseSSEEvent(raw: string): { event: string; data: string } | null {
  let event = ''
  let data = ''

  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      data += line.slice(5).trim()
    }
  }

  if (!event || !data) return null
  return { event, data }
}

function dispatchEvent(event: string, data: string, handlers: SSEHandlers): void {
  try {
    const parsed = JSON.parse(data)

    switch (event) {
      case SSE_EVENTS.READY:
        handlers.onReady?.(parsed)
        break
      case SSE_EVENTS.SESSION:
        handlers.onSession?.(parsed)
        break
      case SSE_EVENTS.TOOL_CALL:
        handlers.onToolCall?.(parsed)
        break
      case SSE_EVENTS.MESSAGE_START:
        handlers.onMessageStart?.(parsed)
        break
      case SSE_EVENTS.DELTA:
        handlers.onDelta?.(parsed)
        break
      case SSE_EVENTS.DONE:
        handlers.onDone?.(parsed)
        break
      case SSE_EVENTS.ERROR:
        handlers.onError?.(parsed)
        break
    }
  } catch {
    console.error(`[SSE] Failed to parse event "${event}":`, data)
  }
}
