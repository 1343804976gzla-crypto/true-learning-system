'use client'

import React from 'react'
import type { AgentTaskActionSuggestionItem } from '@/lib/agent/types'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { RiskBadge } from '@/components/agent/actions/risk-badge'
import { Play, Eye } from 'lucide-react'

export function ActionSuggestions({
  suggestions,
  onPreview,
  onExecute,
}: {
  suggestions: AgentTaskActionSuggestionItem[]
  onPreview?: (suggestion: AgentTaskActionSuggestionItem) => void
  onExecute?: (suggestion: AgentTaskActionSuggestionItem) => void
}) {
  if (suggestions.length === 0) return null

  return (
    <div className="space-y-1.5">
      <span className="text-[11px] font-medium text-[#6a6a6f] px-1">建议动作</span>
      {suggestions.map(s => (
        <Card key={s.id} className="bg-[#1a1a1e] border-white/[0.06] px-3 py-2">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-white/80">{s.title ?? s.tool_name}</span>
                <RiskBadge level={s.risk_level} />
              </div>
              {s.summary && (
                <p className="text-[11px] text-[#5a5a5f] mt-0.5">{s.summary}</p>
              )}
            </div>
            <div className="flex items-center gap-1 flex-shrink-0">
              {s.requires_confirmation && onPreview && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-[10px] text-[#8a8a8f] hover:text-white"
                  onClick={() => onPreview(s)}
                >
                  <Eye className="size-3 mr-1" />
                  预览
                </Button>
              )}
              {onExecute && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-[10px] text-blue-400 hover:text-blue-300"
                  onClick={() => onExecute(s)}
                >
                  <Play className="size-3 mr-1" />
                  执行
                </Button>
              )}
            </div>
          </div>
        </Card>
      ))}
    </div>
  )
}
