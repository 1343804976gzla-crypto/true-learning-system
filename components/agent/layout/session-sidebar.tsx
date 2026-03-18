'use client'

import React, { useEffect } from 'react'
import Link from 'next/link'
import { motion } from 'framer-motion'
import {
  BookOpenText,
  Bot,
  Compass,
  MessageSquareText,
  Plus,
  Sparkles,
} from 'lucide-react'

import { useSession } from '@/components/agent/providers/session-provider'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sidebar, SidebarBody, SidebarLink, useSidebar } from '@/components/ui/sidebar'
import { cn } from '@/lib/utils'

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)

  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins}m ago`

  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`

  return new Date(iso).toLocaleDateString('zh-CN')
}

function collapseOnMobile(setOpen: React.Dispatch<React.SetStateAction<boolean>>) {
  if (typeof window !== 'undefined' && window.innerWidth < 768) {
    setOpen(false)
  }
}

function SidebarBrand() {
  const { open, animate } = useSidebar()

  return (
    <Link
      href="/agent"
      className="flex items-center gap-3 rounded-[28px] border border-white/[0.08] bg-white/[0.04] px-3 py-3 text-primary-content transition hover:bg-white/[0.06]"
    >
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,rgba(108,134,201,0.95),rgba(53,74,128,0.92))] text-white shadow-[0_12px_28px_rgba(32,55,108,0.35)]">
        <BookOpenText className="size-4.5" />
      </div>
      <motion.div
        animate={{
          display: animate ? (open ? 'block' : 'none') : 'block',
          opacity: animate ? (open ? 1 : 0) : 1,
        }}
        className="min-w-0"
      >
        <p className="truncate text-[13px] font-semibold tracking-[0.01em]">
          True Learning
        </p>
        <p className="truncate text-[11px] uppercase tracking-[0.18em] text-secondary-content">
          Agent Workspace
        </p>
      </motion.div>
    </Link>
  )
}

function SidebarSectionLabel({ children }: { children: React.ReactNode }) {
  const { open, animate } = useSidebar()

  return (
    <motion.p
      animate={{
        display: animate ? (open ? 'block' : 'none') : 'block',
        opacity: animate ? (open ? 1 : 0) : 1,
      }}
      className="px-2 text-[10px] font-medium uppercase tracking-[0.24em] text-tertiary-content"
    >
      {children}
    </motion.p>
  )
}

function SessionListItem({
  active,
  title,
  preview,
  timestamp,
  onClick,
}: {
  active: boolean
  title: string
  preview?: string | null
  timestamp: string
  onClick: () => void
}) {
  const { open, animate } = useSidebar()

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full items-start gap-3 rounded-[24px] border px-2.5 py-2.5 text-left transition',
        active
          ? 'border-white/[0.12] bg-white/[0.08] text-primary-content shadow-[0_14px_34px_rgba(0,0,0,0.18)]'
          : 'border-transparent bg-white/[0.03] text-secondary-content hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-primary-content'
      )}
    >
      <div
        className={cn(
          'mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-2xl border',
          active
            ? 'border-white/[0.12] bg-[linear-gradient(135deg,rgba(89,116,189,0.65),rgba(42,57,102,0.5))] text-white'
            : 'border-white/[0.06] bg-white/[0.04] text-secondary-content'
        )}
      >
        <MessageSquareText className="size-4" />
      </div>

      <motion.div
        animate={{
          display: animate ? (open ? 'block' : 'none') : 'block',
          opacity: animate ? (open ? 1 : 0) : 1,
        }}
        className="min-w-0 flex-1"
      >
        <div className="truncate text-[13px] font-medium leading-5 text-primary-content">
          {title}
        </div>
        <div className="mt-1 truncate text-[12px] leading-5 text-secondary-content">
          {preview || 'Open this conversation to continue the thread.'}
        </div>
        <div className="mt-2 text-[11px] uppercase tracking-[0.16em] text-tertiary-content">
          {timestamp}
        </div>
      </motion.div>
    </button>
  )
}

export function SessionSidebar({
  open,
  setOpen,
}: {
  open: boolean
  setOpen: React.Dispatch<React.SetStateAction<boolean>>
}) {
  const {
    sessions,
    activeSessionId,
    loadSessions,
    switchSession,
    createNewSession,
  } = useSession()

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  const activeSession = sessions.find((session) => session.id === activeSessionId) ?? null

  return (
    <Sidebar open={open} setOpen={setOpen}>
      <SidebarBody className="justify-between gap-6 border-r border-subtle shadow-elevated">
        <div className="flex min-h-0 flex-1 flex-col">
          <SidebarBrand />

          <div className="mt-6 flex flex-col gap-1.5">
            <SidebarLink
              link={{
                label: 'Agent Home',
                href: '/agent',
                icon: <Sparkles className="size-4 text-primary-content" />,
              }}
              className="text-secondary-content hover:bg-white/[0.06] hover:text-primary-content"
            />
            <SidebarLink
              link={{
                label: 'Component Demo',
                href: '/demo',
                icon: <Compass className="size-4 text-primary-content" />,
              }}
              className="text-secondary-content hover:bg-white/[0.06] hover:text-primary-content"
            />
            <SidebarLink
              link={{
                label: 'New Session',
                href: '/agent',
                icon: <Plus className="size-4 text-primary-content" />,
              }}
              onClick={(event) => {
                event.preventDefault()
                void createNewSession()
                collapseOnMobile(setOpen)
              }}
              className="bg-white/[0.06] text-primary-content hover:bg-white/[0.1]"
            />
          </div>

          <div className="mt-6 min-h-0 flex-1">
            <SidebarSectionLabel>Recent Sessions</SidebarSectionLabel>
            <div className="mt-3 h-full">
              <ScrollArea className="h-full">
                <div className="space-y-2 pr-1">
                  {sessions.length === 0 ? (
                    <div className="rounded-[24px] border border-dashed border-white/[0.08] bg-white/[0.03] px-3 py-4 text-[12px] leading-6 text-secondary-content">
                      Your first prompt will create a session automatically.
                    </div>
                  ) : (
                    sessions.map((session) => (
                      <SessionListItem
                        key={session.id}
                        active={activeSessionId === session.id}
                        title={session.title || 'New Session'}
                        preview={session.last_message_preview}
                        timestamp={formatRelativeTime(session.last_message_at || session.created_at)}
                        onClick={() => {
                          void switchSession(session.id)
                          collapseOnMobile(setOpen)
                        }}
                      />
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>
          </div>
        </div>

        <div className="rounded-[28px] border border-white/[0.08] bg-white/[0.04] p-3">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.06] text-primary-content">
              <Bot className="size-4.5" />
            </div>
            <motion.div
              animate={{
                display: open ? 'block' : 'none',
                opacity: open ? 1 : 0,
              }}
              className="min-w-0"
            >
              <p className="truncate text-[13px] font-medium text-primary-content">
                {activeSession?.title || 'No active session'}
              </p>
              <p className="mt-1 truncate text-[11px] uppercase tracking-[0.16em] text-secondary-content">
                {sessions.length} session{sessions.length === 1 ? '' : 's'}
              </p>
            </motion.div>
          </div>
        </div>
      </SidebarBody>
    </Sidebar>
  )
}
