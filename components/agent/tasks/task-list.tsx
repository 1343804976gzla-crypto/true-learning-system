'use client'

import React, { useEffect, useState } from 'react'
import { useSession } from '@/components/agent/providers/session-provider'
import type { AgentTaskItem } from '@/lib/agent/types'
import * as api from '@/lib/agent/api-client'
import { TaskStatusBadge } from './task-status-badge'
import { TaskDetail } from './task-detail'
import { Badge } from '@/components/ui/badge'
import { ListChecks } from 'lucide-react'

export function TaskList() {
  const { activeSessionId } = useSession()
  const [tasks, setTasks] = useState<AgentTaskItem[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!activeSessionId) { setTasks([]); return }
    setLoading(true)
    api.listTasks(activeSessionId)
      .then(res => setTasks(res.tasks))
      .catch(() => setTasks([]))
      .finally(() => setLoading(false))
  }, [activeSessionId])

  if (selectedTaskId) {
    return <TaskDetail taskId={selectedTaskId} onBack={() => setSelectedTaskId(null)} />
  }

  if (!activeSessionId) {
    return <div className="text-xs text-[#5a5a5f] text-center py-4">选择会话查看任务</div>
  }

  if (loading) {
    return <div className="text-xs text-[#5a5a5f] text-center py-4">加载中...</div>
  }

  if (tasks.length === 0) {
    return (
      <div className="text-center py-8">
        <ListChecks className="size-8 text-[#3a3a3f] mx-auto mb-2" />
        <p className="text-xs text-[#5a5a5f]">暂无任务</p>
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {tasks.map(task => (
        <button
          key={task.id}
          onClick={() => setSelectedTaskId(task.id)}
          className="w-full text-left px-3 py-2 rounded-lg bg-[#1a1a1e] hover:bg-white/[0.04] transition-colors"
        >
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-white/80 truncate flex-1">{task.title}</span>
            <TaskStatusBadge status={task.status} />
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-[9px] px-1 py-0 h-3.5 border-white/10 text-[#5a5a5f]">
              {task.priority}
            </Badge>
            {task.subtask_count > 0 && (
              <span className="text-[10px] text-[#5a5a5f]">
                {task.completed_subtask_count}/{task.subtask_count}
              </span>
            )}
          </div>
        </button>
      ))}
    </div>
  )
}
