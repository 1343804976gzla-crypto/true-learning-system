'use client'

import React from 'react'
import type { AgentContextUsage } from '@/lib/agent/types'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

const segments: { key: keyof AgentContextUsage; label: string; color: string }[] = [
  { key: 'system_prompt_tokens', label: '系统提示', color: 'bg-blue-500' },
  { key: 'session_summary_tokens', label: '会话摘要', color: 'bg-purple-500' },
  { key: 'memory_tokens', label: '记忆', color: 'bg-pink-500' },
  { key: 'recent_messages_tokens', label: '近期消息', color: 'bg-cyan-500' },
  { key: 'learning_data_tokens', label: '学习数据', color: 'bg-green-500' },
  { key: 'request_analysis_tokens', label: '请求分析', color: 'bg-yellow-500' },
  { key: 'plan_outline_tokens', label: '计划', color: 'bg-orange-500' },
  { key: 'response_strategy_tokens', label: '策略', color: 'bg-red-500' },
  { key: 'reserved_output_tokens', label: '输出预留', color: 'bg-gray-500' },
]

export function ContextUsageBar({ usage }: { usage: AgentContextUsage }) {
  const total = usage.total_estimated_tokens
  if (total === 0) return null

  return (
    <TooltipProvider>
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-[#5a5a5f]">Token 用量</span>
          <span className="text-[10px] text-[#6a6a6f]">{total.toLocaleString()}</span>
        </div>
        <div className="flex h-1.5 rounded-full overflow-hidden bg-white/[0.04]">
          {segments.map(seg => {
            const value = usage[seg.key]
            if (!value) return null
            const pct = (value / total) * 100
            return (
              <Tooltip key={seg.key}>
                <TooltipTrigger asChild>
                  <div className={`${seg.color} opacity-80`} style={{ width: `${pct}%` }} />
                </TooltipTrigger>
                <TooltipContent side="top" className="text-[10px]">
                  {seg.label}: {value.toLocaleString()}
                </TooltipContent>
              </Tooltip>
            )
          })}
        </div>
      </div>
    </TooltipProvider>
  )
}
