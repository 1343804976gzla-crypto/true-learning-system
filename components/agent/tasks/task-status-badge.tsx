'use client'

import React from 'react'
import type { TaskStatus } from '@/lib/agent/types'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const statusConfig: Record<TaskStatus, { label: string; style: string }> = {
  pending: { label: '待处理', style: 'bg-gray-500/10 text-gray-400 border-gray-500/20' },
  ready: { label: '就绪', style: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20' },
  running: { label: '执行中', style: 'bg-blue-500/10 text-blue-400 border-blue-500/20' },
  verifying: { label: '验证中', style: 'bg-purple-500/10 text-purple-400 border-purple-500/20' },
  paused: { label: '已暂停', style: 'bg-orange-500/10 text-orange-400 border-orange-500/20' },
  completed: { label: '已完成', style: 'bg-green-500/10 text-green-400 border-green-500/20' },
  failed: { label: '失败', style: 'bg-red-500/10 text-red-400 border-red-500/20' },
  cancelled: { label: '已取消', style: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
}

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const config = statusConfig[status] ?? { label: status, style: '' }
  return (
    <Badge variant="outline" className={cn('text-[10px] px-1.5 py-0 h-4', config.style)}>
      {config.label}
    </Badge>
  )
}
