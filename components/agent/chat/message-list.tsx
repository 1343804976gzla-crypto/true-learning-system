'use client'

import React, { useRef, useEffect } from 'react'
import type { AgentMessageItem, SSEMessageStartEvent } from '@/lib/agent/types'
import { MessageBubble } from './message-bubble'
import { StreamingIndicator } from './streaming-indicator'
import { SourceCards } from '@/components/agent/structured/source-cards'
import { PlanView } from '@/components/agent/structured/plan-view'
import { ScrollArea } from '@/components/ui/scroll-area'

interface MessageListProps {
  messages: AgentMessageItem[]
  streamStatus: string
  streamingContent: string
  streamingMeta: SSEMessageStartEvent | null
}

export function MessageList({
  messages,
  streamStatus,
  streamingContent,
  streamingMeta,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, streamingContent])

  const isActive = streamStatus !== 'idle' && streamStatus !== 'completed' && streamStatus !== 'error'

  if (messages.length === 0 && !isActive) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6">
        <div className="space-y-3 max-w-sm">
          <p className="text-[15px] font-medium text-secondary-content">
            有什么可以帮你的？
          </p>
          <div className="flex flex-wrap justify-center gap-2">
            {['分析薄弱知识点', '制定复习计划', '生成练习题'].map(tag => (
              <span
                key={tag}
                className="px-3 py-1.5 rounded-xl text-[12px] text-tertiary-content bg-white/[0.04] border border-subtle"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="py-6 space-y-1">
        {messages
          .filter(m => m.role !== 'system')
          .map(msg => (
            <MessageBubble key={msg.id} message={msg} />
          ))}

        {/* Streaming bubble */}
        {isActive && (
          <div className="max-w-[720px] mx-auto px-5 py-2">
            <div className="space-y-3">
              {/* Pre-stream structured data */}
              {streamingMeta?.sources && streamingMeta.sources.length > 0 && (
                <SourceCards sources={streamingMeta.sources} />
              )}
              {streamingMeta?.plan && (
                <PlanView plan={streamingMeta.plan} />
              )}

              {/* Streaming text or indicator */}
              {streamingContent ? (
                <div className="text-[14px] leading-[1.75] text-primary-content">
                  <div className="whitespace-pre-wrap break-words">{streamingContent}</div>
                  <StreamingIndicator />
                </div>
              ) : (
                <StreamingIndicator />
              )}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  )
}
