'use client'

import React from 'react'
import type { AgentActionLogItem } from '@/lib/agent/types'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { RiskBadge } from './risk-badge'
import { Check, RotateCcw, AlertCircle, Clock } from 'lucide-react'
import { cn } from '@/lib/utils'

const executionStyles: Record<string, { icon: React.ReactNode; style: string }> = {
  pending: { icon: <Clock className="size-3" />, style: 'text-yellow-400' },
  success: { icon: <Check className="size-3" />, style: 'text-green-400' },
  failed: { icon: <AlertCircle className="size-3" />, style: 'text-red-400' },
  rolled_back: { icon: <RotateCcw className="size-3" />, style: 'text-orange-400' },
}

export function ActionCard({
  action,
  onConfirm,
  onRollback,
}: {
  action: AgentActionLogItem
  onConfirm?: () => void
  onRollback?: () => void
}) {
  const execInfo = executionStyles[action.execution_status]
  const needsConfirm = action.approval_status === 'pending' && action.execution_status === 'pending'

  return (
    <Card className="bg-[#1a1a1e] border-white/[0.06] px-3 py-2 space-y-1.5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={cn('flex items-center gap-1', execInfo?.style)}>
            {execInfo?.icon}
          </span>
          <span className="text-xs font-medium text-white/80">{action.tool_name}</span>
          <RiskBadge level={action.risk_level} />
          <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-white/10 text-[#5a5a5f]">
            {action.tool_type}
          </Badge>
        </div>
        <span className="text-[10px] text-[#4a4a4f]">
          {new Date(action.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>

      {/* Preview summary */}
      {action.preview_summary && (
        <p className="text-[11px] text-[#6a6a6f]">{action.preview_summary}</p>
      )}

      {/* Error */}
      {action.error_message && (
        <p className="text-[11px] text-red-400">{action.error_message}</p>
      )}

      {/* Verification */}
      {action.verification_status && (
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-[#5a5a5f]">验证:</span>
          <Badge
            variant="outline"
            className={cn('text-[9px] px-1 py-0 h-3.5', {
              'border-green-500/20 text-green-400': action.verification_status === 'verified',
              'border-red-500/20 text-red-400': action.verification_status === 'mismatch' || action.verification_status === 'failed',
              'border-gray-500/20 text-gray-400': action.verification_status === 'skipped',
            })}
          >
            {action.verification_status}
          </Badge>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-1.5 pt-0.5">
        {needsConfirm && onConfirm && (
          <Button
            size="sm"
            className="h-6 px-2.5 text-[10px] bg-blue-500/20 text-blue-400 hover:bg-blue-500/30"
            onClick={onConfirm}
          >
            <Check className="size-3 mr-1" />
            确认执行
          </Button>
        )}
        {action.can_rollback && action.execution_status === 'success' && onRollback && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2.5 text-[10px] text-orange-400 hover:text-orange-300 hover:bg-orange-500/10"
            onClick={onRollback}
          >
            <RotateCcw className="size-3 mr-1" />
            回滚
          </Button>
        )}
        {action.rollback_hint && (
          <span className="text-[10px] text-[#4a4a4f] italic">{action.rollback_hint}</span>
        )}
      </div>
    </Card>
  )
}
