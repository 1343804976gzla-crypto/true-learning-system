'use client'

import React, { useState, useRef, useEffect } from 'react'
import { ArrowUp, Square } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ChatInputProps {
  onSend: (message: string) => void
  onCancel?: () => void
  isStreaming?: boolean
  placeholder?: string
}

export function ChatInput({
  onSend,
  onCancel,
  isStreaming = false,
  placeholder = '输入你的问题...',
}: ChatInputProps) {
  const [message, setMessage] = useState('')
  const [focused, setFocused] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`
    }
  }, [message])

  const handleSubmit = () => {
    if (!message.trim() || isStreaming) return
    onSend(message.trim())
    setMessage('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const canSend = message.trim().length > 0 && !isStreaming

  return (
    <div className="px-4 pb-4 pt-2">
      <div className="max-w-[720px] mx-auto">
        <div
          className={cn(
            'relative rounded-2xl transition-all duration-200',
            'surface-raised shadow-soft',
            'border border-subtle',
            focused && !isStreaming && 'glow-accent border-white/[0.1]'
          )}
        >
          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={placeholder}
            disabled={isStreaming}
            className={cn(
              'w-full resize-none bg-transparent text-[14px] leading-[1.6] text-primary-content',
              'placeholder:text-tertiary-content',
              'px-4 pt-3.5 pb-2 focus:outline-none',
              'min-h-[48px] max-h-[180px]',
              'disabled:opacity-40 disabled:cursor-not-allowed'
            )}
            rows={1}
          />

          {/* Bottom bar */}
          <div className="flex items-center justify-between px-3 pb-2.5">
            <div className="flex items-center gap-1">
              <span className="text-[11px] text-tertiary-content">
                Shift+Enter 换行
              </span>
            </div>

            {isStreaming ? (
              <button
                onClick={onCancel}
                className={cn(
                  'flex items-center justify-center',
                  'size-8 rounded-xl',
                  'bg-red-500/10 text-red-400',
                  'hover:bg-red-500/20 transition-colors'
                )}
              >
                <Square className="size-3.5" />
              </button>
            ) : (
              <button
                onClick={handleSubmit}
                disabled={!canSend}
                className={cn(
                  'flex items-center justify-center',
                  'size-8 rounded-xl transition-all duration-150',
                  canSend
                    ? 'bg-white text-black hover:bg-white/90 active:scale-95'
                    : 'bg-white/[0.06] text-tertiary-content cursor-not-allowed'
                )}
              >
                <ArrowUp className="size-4" strokeWidth={2} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
