'use client'

import React, { useState } from 'react'
import Link from 'next/link'
import { motion } from 'framer-motion'
import {
  LayoutDashboard,
  LogOut,
  Settings,
  UserCog,
} from 'lucide-react'

import { Sidebar, SidebarBody, SidebarLink } from '@/components/ui/sidebar'
import { cn } from '@/lib/utils'

export function SidebarDemo() {
  const links = [
    {
      label: 'Dashboard',
      href: '/agent',
      icon: <LayoutDashboard className="h-5 w-5 flex-shrink-0" />,
    },
    {
      label: 'Profile',
      href: '/agent',
      icon: <UserCog className="h-5 w-5 flex-shrink-0" />,
    },
    {
      label: 'Settings',
      href: '/agent',
      icon: <Settings className="h-5 w-5 flex-shrink-0" />,
    },
    {
      label: 'Logout',
      href: '/agent',
      icon: <LogOut className="h-5 w-5 flex-shrink-0" />,
    },
  ]
  const [open, setOpen] = useState(false)

  return (
    <div
      className={cn(
        'mx-auto flex h-[60vh] w-full max-w-7xl flex-col overflow-hidden rounded-md border border-neutral-800 bg-neutral-950 md:flex-row'
      )}
    >
      <Sidebar open={open} setOpen={setOpen}>
        <SidebarBody className="justify-between gap-10 border-r border-neutral-800">
          <div className="flex flex-1 flex-col overflow-x-hidden overflow-y-auto">
            {open ? <Logo /> : <LogoIcon />}
            <div className="mt-8 flex flex-col gap-2">
              {links.map((link) => (
                <SidebarLink
                  key={link.label}
                  link={link}
                  className="text-neutral-200 hover:bg-white/[0.06]"
                />
              ))}
            </div>
          </div>
          <SidebarLink
            link={{
              label: 'Acet Labs',
              href: '/agent',
              icon: (
                <div className="flex h-7 w-7 items-center justify-center rounded-full bg-white/10 text-[10px] font-semibold text-white">
                  AL
                </div>
              ),
            }}
            className="text-neutral-200 hover:bg-white/[0.06]"
          />
        </SidebarBody>
      </Sidebar>

      <div className="flex flex-1">
        <div className="flex h-full w-full flex-1 flex-col gap-2 rounded-tl-2xl border border-neutral-800 bg-neutral-900 p-2 md:p-10">
          <div className="flex gap-2">
            {[...new Array(4)].map((_, index) => (
              <div
                key={`first-${index}`}
                className="h-20 w-full animate-pulse rounded-lg bg-neutral-800"
              />
            ))}
          </div>
          <div className="flex flex-1 gap-2">
            {[...new Array(2)].map((_, index) => (
              <div
                key={`second-${index}`}
                className="h-full w-full animate-pulse rounded-lg bg-neutral-800"
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function Logo() {
  return (
    <Link
      href="/agent"
      className="relative z-20 flex items-center space-x-2 py-1 text-sm font-normal text-white"
    >
      <div className="h-5 w-6 flex-shrink-0 rounded-bl-sm rounded-br-lg rounded-tl-lg rounded-tr-sm bg-white" />
      <motion.span
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="whitespace-pre font-medium text-white"
      >
        Acet Labs
      </motion.span>
    </Link>
  )
}

function LogoIcon() {
  return (
    <Link
      href="/agent"
      className="relative z-20 flex items-center space-x-2 py-1 text-sm font-normal text-white"
    >
      <div className="h-5 w-6 flex-shrink-0 rounded-bl-sm rounded-br-lg rounded-tl-lg rounded-tr-sm bg-white" />
    </Link>
  )
}
