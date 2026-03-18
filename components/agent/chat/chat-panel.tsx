'use client'

import React from 'react'
import { useSession } from '@/components/agent/providers/session-provider'
import { MessageList } from './message-list'
import { ChatInput } from './chat-input'
import { X } from 'lucide-react'

export function ChatPanel() {
  const {
    messages,
    streamStatus,
    streamingContent,
    streamingMeta,
    activeSessionId,
    sendMessage,
    cancelStream,
    error,
    clearError,
  } = useSession()

  const isStreaming = streamStatus === 'connecting' || streamStatus === 'streaming' || streamStatus === 'prepared'

  return (
    <div className="flex flex-col h-full">
      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-red-500/[0.06] border-b border-red-500/10">
          <span className="text-[12px] text-red-400/80 flex-1">{error}</span>
          <button
            onClick={clearError}
            className="p-1 rounded-md text-red-400/40 hover:text-red-400/70 hover:bg-red-500/10 transition-colors"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-hidden">
        <MessageList
          messages={messages}
          streamStatus={streamStatus}
          streamingContent={streamingContent}
          streamingMeta={streamingMeta}
        />
      </div>

      {/* Input */}
      <ChatInput
        onSend={(text) => sendMessage(text, activeSessionId ?? undefined)}
        onCancel={cancelStream}
        isStreaming={isStreaming}
      />
    </div>
  )
}
