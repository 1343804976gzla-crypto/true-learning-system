'use client'

import React from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { TaskList } from '@/components/agent/tasks/task-list'
import { ActionList } from '@/components/agent/actions/action-list'
import { TurnDebugPanel } from '@/components/agent/debug/turn-debug-panel'
import { ToolStatus } from '@/components/agent/debug/tool-status'

export function RightPanel() {
  return (
    <div className="flex flex-col h-full surface-raised">
      <Tabs defaultValue="tasks" className="flex flex-col h-full">
        <div className="flex-shrink-0 h-[52px] flex items-center border-b border-subtle px-3">
          <TabsList className="bg-transparent h-8 gap-0.5 p-0">
            {[
              { value: 'tasks', label: '任务' },
              { value: 'actions', label: '动作' },
              { value: 'debug', label: '调试' },
            ].map(tab => (
              <TabsTrigger
                key={tab.value}
                value={tab.value}
                className="text-[12px] px-2.5 h-7 rounded-lg data-[state=active]:bg-white/[0.07] data-[state=active]:text-primary-content text-tertiary-content hover:text-secondary-content transition-colors"
              >
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <ScrollArea className="flex-1">
          <TabsContent value="tasks" className="mt-0 p-3">
            <TaskList />
          </TabsContent>
          <TabsContent value="actions" className="mt-0 p-3">
            <ActionList />
          </TabsContent>
          <TabsContent value="debug" className="mt-0 p-3 space-y-4">
            <TurnDebugPanel />
            <ToolStatus />
          </TabsContent>
        </ScrollArea>
      </Tabs>
    </div>
  )
}
