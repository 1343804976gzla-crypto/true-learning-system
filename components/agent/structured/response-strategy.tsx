'use client'

import React from 'react'
import type { ResponseStrategy } from '@/lib/agent/types'
import { AlertTriangle, HelpCircle } from 'lucide-react'

export function ResponseStrategyBanner({ strategy }: { strategy: ResponseStrategy }) {
  if (strategy.strategy === 'answer') return null

  if (strategy.strategy === 'answer_with_caveat') {
    return (
      <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
        <AlertTriangle className="size-3.5 text-yellow-400 mt-0.5 flex-shrink-0" />
        <div>
          {strategy.reason && (
            <p className="text-[11px] text-yellow-300/80">{strategy.reason}</p>
          )}
          {strategy.instruction && (
            <p className="text-[11px] text-yellow-300/60 mt-0.5">{strategy.instruction}</p>
          )}
        </div>
      </div>
    )
  }

  if (strategy.strategy === 'clarify') {
    return (
      <div className="space-y-2">
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-blue-500/10 border border-blue-500/20">
          <HelpCircle className="size-3.5 text-blue-400 mt-0.5 flex-shrink-0" />
          <p className="text-[11px] text-blue-300/80">{strategy.reason ?? '需要更多信息来回答你的问题'}</p>
        </div>
        {strategy.clarifying_questions && strategy.clarifying_questions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {strategy.clarifying_questions.map((q, i) => (
              <span
                key={i}
                className="inline-block px-2.5 py-1 rounded-full text-[11px] bg-blue-500/10 text-blue-300 border border-blue-500/20 cursor-pointer hover:bg-blue-500/20 transition-colors"
              >
                {q}
              </span>
            ))}
          </div>
        )}
      </div>
    )
  }

  return null
}
