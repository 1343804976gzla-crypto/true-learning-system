'use client'

import React, { useState } from 'react'
import type { AgentPlanBundle } from '@/lib/agent/types'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ListChecks } from 'lucide-react'
import { cn } from '@/lib/utils'

const statusColors: Record<string, string> = {
  pending: 'text-[#6a6a6f]',
  ready: 'text-yellow-400',
  running: 'text-blue-400',
  completed: 'text-green-400',
  failed: 'text-red-400',
}

export function PlanView({ plan }: { plan: AgentPlanBundle }) {
  const [expanded, setExpanded] = useState(true)

  if (!plan.summary && plan.tasks.length === 0) return null

  return (
    <Card className="bg-[#1a1a1e] border-white/[0.06] overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-white/[0.02] transition-colors"
      >
        <ListChecks className="size-3.5 text-purple-400 flex-shrink-0" />
        <span className="text-xs font-medium text-white/80 flex-1">计划</span>
        <ChevronDown className={cn('size-3.5 text-[#5a5a5f] transition-transform', expanded && 'rotate-180')} />
      </button>

      {expanded && (
        <div className="px-3 pb-2.5 space-y-2 border-t border-white/[0.04] pt-2">
          {plan.summary && (
            <p className="text-[11px] text-[#8a8a8f]">{plan.summary}</p>
          )}
          {plan.tasks.map(task => (
            <div key={task.id} className="space-y-1" style={{ paddingLeft: `${task.level * 12}px` }}>
              <div className="flex items-center gap-2">
                <span className={cn('text-[11px] font-medium', statusColors[task.status] ?? 'text-white/70')}>
                  {task.title}
                </span>
                <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-white/10 text-[#5a5a5f]">
                  {task.priority}
                </Badge>
              </div>
              {task.description && (
                <p className="text-[10px] text-[#5a5a5f]">{task.description}</p>
              )}
              {task.subtasks.length > 0 && (
                <div className="pl-3 space-y-0.5">
                  {task.subtasks.map(sub => (
                    <div key={sub.id} className="text-[10px] text-[#6a6a6f]">
                      - {sub.title}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
