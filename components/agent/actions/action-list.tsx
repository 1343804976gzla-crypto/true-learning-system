'use client'

import React, { useEffect, useState } from 'react'
import { useSession } from '@/components/agent/providers/session-provider'
import type { AgentActionLogItem } from '@/lib/agent/types'
import * as api from '@/lib/agent/api-client'
import { ActionCard } from './action-card'
import { Zap } from 'lucide-react'

export function ActionList() {
  const { activeSessionId } = useSession()
  const [actions, setActions] = useState<AgentActionLogItem[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!activeSessionId) { setActions([]); return }
    setLoading(true)
    api.listActions(activeSessionId)
      .then(res => setActions(res.actions))
      .catch(() => setActions([]))
      .finally(() => setLoading(false))
  }, [activeSessionId])

  const handleConfirm = async (actionId: string, sessionId: string) => {
    const res = await api.executeAction({ session_id: sessionId, action_id: actionId, confirm: true })
    setActions(prev => prev.map(a => (a.id === actionId ? res.action : a)))
  }

  const handleRollback = async (actionId: string, sessionId: string) => {
    const res = await api.executeAction({ session_id: sessionId, action_id: actionId, rollback: true })
    setActions(prev => prev.map(a => (a.id === actionId ? res.action : a)))
  }

  if (!activeSessionId) {
    return <div className="text-xs text-[#5a5a5f] text-center py-4">选择会话查看动作</div>
  }

  if (loading) {
    return <div className="text-xs text-[#5a5a5f] text-center py-4">加载中...</div>
  }

  if (actions.length === 0) {
    return (
      <div className="text-center py-8">
        <Zap className="size-8 text-[#3a3a3f] mx-auto mb-2" />
        <p className="text-xs text-[#5a5a5f]">暂无动作</p>
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {actions.map(action => (
        <ActionCard
          key={action.id}
          action={action}
          onConfirm={() => handleConfirm(action.id, action.session_id)}
          onRollback={() => handleRollback(action.id, action.session_id)}
        />
      ))}
    </div>
  )
}
