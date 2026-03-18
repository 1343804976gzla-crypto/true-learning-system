'use client'

import React from 'react'
import type { AgentMessageItem, ContentStructured } from '@/lib/agent/types'
import { SourceCards } from '@/components/agent/structured/source-cards'
import { PlanView } from '@/components/agent/structured/plan-view'
import { ActionSuggestions } from '@/components/agent/structured/action-suggestions'
import { ResponseStrategyBanner } from '@/components/agent/structured/response-strategy'
import { cn } from '@/lib/utils'
import { Wrench } from 'lucide-react'

interface MessageBubbleProps {
  message: AgentMessageItem
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const { role, content, content_structured } = message
  const structured = content_structured as ContentStructured | undefined

  if (role === 'system') return null

  if (role === 'tool') {
    return (
      <div className="flex items-center gap-2 mx-auto max-w-[720px] px-5 py-1">
        <div className="h-px flex-1 bg-white/[0.04]" />
        <div className="flex items-center gap-1.5 text-[11px] text-tertiary-content">
          <Wrench className="size-3" />
          <span>{message.tool_name}</span>
          {message.message_status === 'error' && (
            <span className="text-red-400/70">failed</span>
          )}
        </div>
        <div className="h-px flex-1 bg-white/[0.04]" />
      </div>
    )
  }

  const isUser = role === 'user'

  return (
    <div className={cn(
      'max-w-[720px] mx-auto px-5 py-2',
      isUser ? 'flex justify-end' : ''
    )}>
      {isUser ? (
        /* User message — right-aligned pill */
        <div className="max-w-[85%]">
          <div className="rounded-2xl rounded-br-md px-4 py-2.5 bg-white/[0.08] text-[14px] leading-[1.65] text-primary-content">
            <div className="whitespace-pre-wrap break-words">{content}</div>
          </div>
        </div>
      ) : (
        /* Assistant message — full-width, no bubble background */
        <div className="space-y-3">
          {/* Strategy banner */}
          {structured?.response_strategy && (
            <ResponseStrategyBanner strategy={structured.response_strategy} />
          )}

          {/* Text content */}
          {content && (
            <div className="text-[14px] leading-[1.75] text-primary-content">
              <div className="whitespace-pre-wrap break-words">{content}</div>
            </div>
          )}

          {/* Structured blocks */}
          {structured?.sources && structured.sources.length > 0 && (
            <SourceCards sources={structured.sources} />
          )}
          {structured?.plan && (
            <PlanView plan={structured.plan} />
          )}
          {structured?.action_suggestions && structured.action_suggestions.length > 0 && (
            <ActionSuggestions suggestions={structured.action_suggestions} />
          )}
        </div>
      )}
    </div>
  )
}
