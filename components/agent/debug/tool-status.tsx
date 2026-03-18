'use client'

import React, { useEffect, useState } from 'react'
import type { AgentToolDefinition, ReferenceStatusResponse } from '@/lib/agent/types'
import * as api from '@/lib/agent/api-client'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { RiskBadge } from '@/components/agent/actions/risk-badge'
import { Wrench, Radio } from 'lucide-react'

export function ToolStatus() {
  const [tools, setTools] = useState<AgentToolDefinition[]>([])
  const [refStatus, setRefStatus] = useState<ReferenceStatusResponse | null>(null)

  useEffect(() => {
    api.listTools().then(setTools).catch(() => {})
    api.getReferenceStatus().then(setRefStatus).catch(() => {})
  }, [])

  return (
    <div className="space-y-4">
      {/* Bridge status */}
      {refStatus && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Radio className="size-3.5 text-cyan-400" />
            <span className="text-xs font-medium text-white/80">桥接状态</span>
          </div>
          <div className="space-y-1">
            {Object.entries(refStatus).map(([name, info]) => (
              <div key={name} className="flex items-center gap-2 px-2 py-1.5 rounded bg-[#1a1a1e]">
                <div className={`size-2 rounded-full ${info.available ? 'bg-green-400' : 'bg-red-400'}`} />
                <span className="text-xs text-white/70">{name}</span>
                <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-white/10 text-[#5a5a5f]">
                  {info.available ? '在线' : '离线'}
                </Badge>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tools */}
      {tools.length > 0 && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Wrench className="size-3.5 text-purple-400" />
            <span className="text-xs font-medium text-white/80">可用工具 ({tools.length})</span>
          </div>
          <div className="space-y-1">
            {tools.map(tool => (
              <Card key={tool.name} className="bg-[#1a1a1e] border-white/[0.06] px-2.5 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-white/80">{tool.name}</span>
                  <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-white/10 text-[#5a5a5f]">
                    {tool.tool_type}
                  </Badge>
                  <RiskBadge level={tool.risk_level} />
                  {tool.requires_confirmation && (
                    <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-yellow-500/20 text-yellow-400">
                      需确认
                    </Badge>
                  )}
                </div>
                <p className="text-[10px] text-[#5a5a5f] mt-0.5">{tool.description}</p>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
