'use client'

import { SessionProvider } from '@/components/agent/providers/session-provider'
import { WorkspaceLayout } from '@/components/agent/layout/workspace-layout'

export default function AgentLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <WorkspaceLayout>{children}</WorkspaceLayout>
    </SessionProvider>
  )
}
