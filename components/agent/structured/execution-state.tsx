'use client'

import React from 'react'
import { Card } from '@/components/ui/card'
import { Activity } from 'lucide-react'

export function ExecutionStateView({ state }: { state: Record<string, unknown> }) {
  const entries = Object.entries(state)
  if (entries.length === 0) return null

  return (
    <Card className="bg-[#1a1a1e] border-white/[0.06] px-3 py-2">
      <div className="flex items-center gap-2 mb-1.5">
        <Activity className="size-3.5 text-emerald-400" />
        <span className="text-xs font-medium text-white/80">执行状态</span>
      </div>
      <div className="space-y-0.5">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-center gap-2 text-[11px]">
            <span className="text-[#5a5a5f]">{key}:</span>
            <span className="text-white/70">{String(value)}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}
