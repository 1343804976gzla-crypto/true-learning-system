'use client'

import React from 'react'
import type { RiskLevel } from '@/lib/agent/types'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const riskStyles: Record<RiskLevel, string> = {
  low: 'bg-green-500/10 text-green-400 border-green-500/20',
  medium: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
  high: 'bg-red-500/10 text-red-400 border-red-500/20',
}

export function RiskBadge({ level }: { level: RiskLevel }) {
  return (
    <Badge variant="outline" className={cn('text-[9px] px-1 py-0 h-3.5', riskStyles[level])}>
      {level}
    </Badge>
  )
}
