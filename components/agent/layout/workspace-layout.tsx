'use client'

import React, { useState } from 'react'
import { PanelRight, PanelRightClose } from 'lucide-react'

import { SessionSidebar } from './session-sidebar'
import { RightPanel } from './right-panel'
import { useSession } from '@/components/agent/providers/session-provider'
import { cn } from '@/lib/utils'

export function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [rightPanelOpen, setRightPanelOpen] = useState(false)
  const { activeSessionId, sessions } = useSession()

  const activeSession = sessions.find((session) => session.id === activeSessionId) ?? null

  return (
    <div className="relative flex h-screen w-full flex-col overflow-hidden surface-base md:flex-row">
      <SessionSidebar open={sidebarOpen} setOpen={setSidebarOpen} />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-16 flex-shrink-0 items-center justify-between border-b border-subtle bg-[linear-gradient(180deg,rgba(9,12,19,0.9),rgba(9,12,19,0.72))] px-4 md:px-6">
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-[0.22em] text-tertiary-content">
                Learning Agent
              </div>
              <div className="mt-1 flex min-w-0 items-center gap-2.5">
                <h1 className="truncate text-[14px] font-medium text-primary-content md:text-[15px]">
                  {activeSession?.title || 'Start a new conversation'}
                </h1>
                {activeSession && (
                  <span className="hidden rounded-full border border-white/[0.08] bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase tracking-[0.16em] text-secondary-content md:inline-flex">
                    {activeSession.agent_type}
                  </span>
                )}
              </div>
            </div>

            <button
              type="button"
              onClick={() => setRightPanelOpen((value) => !value)}
              className="rounded-2xl border border-white/[0.08] bg-white/[0.04] p-2 text-secondary-content transition hover:bg-white/[0.08] hover:text-primary-content"
              aria-label="Toggle right panel"
            >
              {rightPanelOpen ? (
                <PanelRightClose className="size-[18px]" />
              ) : (
                <PanelRight className="size-[18px]" />
              )}
            </button>
          </header>

          <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
        </div>

        <div
          className={cn(
            'hidden flex-shrink-0 overflow-hidden border-l border-subtle transition-[width] duration-300 ease-out md:block',
            rightPanelOpen ? 'w-[360px]' : 'w-0'
          )}
        >
          <div
            className={cn(
              'h-full w-[360px] transition-opacity duration-200',
              rightPanelOpen ? 'opacity-100' : 'pointer-events-none opacity-0'
            )}
          >
            <RightPanel />
          </div>
        </div>
      </div>

      {rightPanelOpen && (
        <>
          <button
            type="button"
            className="absolute inset-0 z-40 bg-black/45 md:hidden"
            onClick={() => setRightPanelOpen(false)}
            aria-label="Close right panel backdrop"
          />
          <div className="absolute inset-y-0 right-0 z-50 w-[min(92vw,360px)] border-l border-subtle shadow-elevated md:hidden">
            <RightPanel />
          </div>
        </>
      )}
    </div>
  )
}
