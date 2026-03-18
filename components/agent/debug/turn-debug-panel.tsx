'use client'

import React, { useEffect, useState } from 'react'
import { useSession } from '@/components/agent/providers/session-provider'
import type { AgentTurnStateItem } from '@/lib/agent/types'
import * as api from '@/lib/agent/api-client'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, Bug } from 'lucide-react'
import { cn } from '@/lib/utils'

export function TurnDebugPanel() {
  const { activeSessionId } = useSession()
  const [turns, setTurns] = useState<AgentTurnStateItem[]>([])
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!activeSessionId) { setTurns([]); return }
    setLoading(true)
    api.getTurnStates(activeSessionId)
      .then(res => setTurns(res.turns))
      .catch(() => setTurns([]))
      .finally(() => setLoading(false))
  }, [activeSessionId])

  if (!activeSessionId) {
    return <div className="text-xs text-[#5a5a5f] text-center py-4">选择会话查看调试信息</div>
  }

  if (loading) {
    return <div className="text-xs text-[#5a5a5f] text-center py-4">加载中...</div>
  }

  if (turns.length === 0) {
    return (
      <div className="text-center py-8">
        <Bug className="size-8 text-[#3a3a3f] mx-auto mb-2" />
        <p className="text-xs text-[#5a5a5f]">暂无 Turn 数据</p>
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {turns.map(turn => (
        <Card key={turn.id} className="bg-[#1a1a1e] border-white/[0.06] overflow-hidden">
          <button
            onClick={() => setExpandedId(expandedId === turn.id ? null : turn.id)}
            className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-white/[0.02] transition-colors"
          >
            <span className="text-xs font-mono text-[#6a6a6f]">#{turn.id}</span>
            <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-white/10 text-[#5a5a5f]">
              {turn.status}
            </Badge>
            {turn.goal && <span className="text-[11px] text-[#8a8a8f] truncate flex-1">{turn.goal}</span>}
            <ChevronDown className={cn('size-3 text-[#5a5a5f] transition-transform', expandedId === turn.id && 'rotate-180')} />
          </button>

          {expandedId === turn.id && (
            <div className="px-3 pb-3 space-y-2 border-t border-white/[0.04] pt-2">
              <DebugSection label="trace_id" value={turn.trace_id} />
              <DebugSection label="selected_tools" value={turn.selected_tools.join(', ') || '无'} />
              {turn.error_message && <DebugSection label="error" value={turn.error_message} isError />}
              <JsonSection label="request_analysis" data={turn.request_analysis} />
              <JsonSection label="plan_final" data={turn.plan_final} />
              <JsonSection label="execution_state" data={turn.execution_state} />
              {turn.tool_snapshots.length > 0 && (
                <JsonSection label="tool_snapshots" data={turn.tool_snapshots} />
              )}
            </div>
          )}
        </Card>
      ))}
    </div>
  )
}

function DebugSection({ label, value, isError }: { label: string; value: string; isError?: boolean }) {
  return (
    <div className="text-[11px]">
      <span className="text-[#5a5a5f]">{label}: </span>
      <span className={isError ? 'text-red-400' : 'text-white/70 font-mono'}>{value}</span>
    </div>
  )
}

function JsonSection({ label, data }: { label: string; data: unknown }) {
  const [expanded, setExpanded] = useState(false)
  const isEmpty = !data || (typeof data === 'object' && Object.keys(data as object).length === 0)

  if (isEmpty) return null

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-[11px] text-[#5a5a5f] hover:text-[#8a8a8f]"
      >
        <ChevronDown className={cn('size-2.5 transition-transform', expanded && 'rotate-180')} />
        {label}
      </button>
      {expanded && (
        <pre className="mt-1 p-2 rounded bg-black/30 text-[10px] text-[#6a6a6f] font-mono overflow-x-auto max-h-[200px]">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}
