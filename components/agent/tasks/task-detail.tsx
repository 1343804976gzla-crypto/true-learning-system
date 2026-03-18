'use client'

import React, { useEffect, useState } from 'react'
import type { AgentTaskDetailResponse } from '@/lib/agent/types'
import * as api from '@/lib/agent/api-client'
import { TaskStatusBadge } from './task-status-badge'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArrowLeft } from 'lucide-react'

export function TaskDetail({ taskId, onBack }: { taskId: string; onBack: () => void }) {
  const [detail, setDetail] = useState<AgentTaskDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.getTask(taskId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false))
  }, [taskId])

  const handleTransition = async (status: string) => {
    const res = await api.updateTaskStatus(taskId, { status: status as never })
    setDetail(res)
  }

  if (loading) return <div className="text-xs text-[#5a5a5f] text-center py-4">加载中...</div>
  if (!detail) return <div className="text-xs text-red-400 text-center py-4">任务不存在</div>

  const { task, events } = detail

  return (
    <div className="space-y-3">
      <button onClick={onBack} className="flex items-center gap-1 text-xs text-[#6a6a6f] hover:text-white transition-colors">
        <ArrowLeft className="size-3" />
        返回列表
      </button>

      <div>
        <div className="flex items-center gap-2 mb-1">
          <h3 className="text-sm font-medium text-white/90">{task.title}</h3>
          <TaskStatusBadge status={task.status} />
        </div>
        {task.goal && <p className="text-xs text-[#6a6a6f]">{task.goal}</p>}
      </div>

      {/* Status transitions */}
      {task.available_transitions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {task.available_transitions.map(status => (
            <Button
              key={status}
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[10px] border-white/10 text-[#8a8a8f] hover:text-white"
              onClick={() => handleTransition(status)}
            >
              → {status}
            </Button>
          ))}
        </div>
      )}

      {/* Plan summary */}
      {task.plan_summary && (
        <div>
          <span className="text-[10px] text-[#5a5a5f] uppercase tracking-wider">计划</span>
          <p className="text-xs text-[#8a8a8f] mt-0.5">{task.plan_summary}</p>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2">
        {[
          { label: '子任务', value: `${task.completed_subtask_count}/${task.subtask_count}` },
          { label: '动作', value: `${task.completed_action_count}/${task.suggested_action_count}` },
          { label: '失败', value: task.failed_action_count },
        ].map(s => (
          <div key={s.label} className="text-center">
            <div className="text-sm font-medium text-white/80">{s.value}</div>
            <div className="text-[10px] text-[#5a5a5f]">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Events timeline */}
      {events.length > 0 && (
        <div>
          <span className="text-[10px] text-[#5a5a5f] uppercase tracking-wider">事件</span>
          <div className="mt-1 space-y-1">
            {events.map(evt => (
              <div key={evt.id} className="flex items-center gap-2 text-[11px]">
                <span className="text-[#4a4a4f]">
                  {new Date(evt.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                </span>
                <span className="text-[#6a6a6f]">{evt.event_type}</span>
                {evt.from_status && evt.to_status && (
                  <span className="text-[#5a5a5f]">
                    {evt.from_status} → {evt.to_status}
                  </span>
                )}
                {evt.note && <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-white/10 text-[#5a5a5f]">{evt.note}</Badge>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
