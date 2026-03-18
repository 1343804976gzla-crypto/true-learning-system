'use client'

import React, { createContext, useContext, useEffect, useState } from 'react'
import { getOrCreateDeviceId, getUserId, setUserId as storeUserId, clearUserId } from '@/lib/agent/identity'

interface IdentityContextValue {
  deviceId: string
  userId: string | undefined
  ready: boolean
  setUserId: (id: string) => void
  logout: () => void
}

const IdentityContext = createContext<IdentityContextValue>({
  deviceId: '',
  userId: undefined,
  ready: false,
  setUserId: () => {},
  logout: () => {},
})

export function useIdentity() {
  return useContext(IdentityContext)
}

export function IdentityProvider({ children }: { children: React.ReactNode }) {
  const [deviceId, setDeviceId] = useState('')
  const [userId, setUserIdState] = useState<string | undefined>(undefined)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setDeviceId(getOrCreateDeviceId())
    setUserIdState(getUserId())
    setReady(true)
  }, [])

  const handleSetUserId = (id: string) => {
    storeUserId(id)
    setUserIdState(id)
  }

  const handleLogout = () => {
    clearUserId()
    setUserIdState(undefined)
  }

  return (
    <IdentityContext.Provider
      value={{
        deviceId,
        userId,
        ready,
        setUserId: handleSetUserId,
        logout: handleLogout,
      }}
    >
      {children}
    </IdentityContext.Provider>
  )
}
