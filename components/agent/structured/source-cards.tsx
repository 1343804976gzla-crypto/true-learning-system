'use client'

import React, { useState } from 'react'
import type { AgentSourceCard } from '@/lib/agent/types'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, Database } from 'lucide-react'
import { cn } from '@/lib/utils'

export function SourceCards({ sources }: { sources: AgentSourceCard[] }) {
  return (
    <div className="space-y-1.5">
      {sources.map((source, i) => (
        <SourceCard key={i} source={source} />
      ))}
    </div>
  )
}

function SourceCard({ source }: { source: AgentSourceCard }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <Card className="bg-[#1a1a1e] border-white/[0.06] overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-white/[0.02] transition-colors"
      >
        <Database className="size-3.5 text-blue-400 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-white/80 truncate">{source.title}</span>
            {source.count > 0 && (
              <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 border-white/10 text-[#6a6a6f]">
                {source.count}
              </Badge>
            )}
          </div>
          <p className="text-[11px] text-[#5a5a5f] truncate">{source.summary}</p>
        </div>
        <ChevronDown className={cn('size-3.5 text-[#5a5a5f] transition-transform', expanded && 'rotate-180')} />
      </button>

      {expanded && (
        <div className="px-3 pb-2.5 space-y-1.5 border-t border-white/[0.04]">
          {source.stats.length > 0 && (
            <div className="grid grid-cols-2 gap-1 pt-2">
              {source.stats.map((stat, i) => (
                <div key={i} className="text-[11px]">
                  <span className="text-[#5a5a5f]">{stat.label}: </span>
                  <span className="text-white/70">{stat.value}</span>
                </div>
              ))}
            </div>
          )}
          {source.bullets.length > 0 && (
            <ul className="space-y-0.5 pt-1">
              {source.bullets.map((b, i) => (
                <li key={i} className="text-[11px] text-[#8a8a8f] flex gap-1.5">
                  <span className="text-[#5a5a5f]">-</span>
                  {b}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
