import { IDENTITY_STORAGE_KEY, DEVICE_ID_PREFIX } from './constants'

function generateDeviceId(): string {
  const hex = Array.from(crypto.getRandomValues(new Uint8Array(4)))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('')
  return `${DEVICE_ID_PREFIX}${hex}`
}

export function getOrCreateDeviceId(): string {
  if (typeof window === 'undefined') return `${DEVICE_ID_PREFIX}ssr`
  const stored = localStorage.getItem(IDENTITY_STORAGE_KEY)
  if (stored) return stored
  const id = generateDeviceId()
  localStorage.setItem(IDENTITY_STORAGE_KEY, id)
  return id
}

export function getUserId(): string | undefined {
  if (typeof window === 'undefined') return undefined
  return localStorage.getItem('tls_user_id') ?? undefined
}

export function setUserId(id: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem('tls_user_id', id)
}

export function clearUserId(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem('tls_user_id')
}

export function getIdentityParams(): { device_id: string; user_id?: string } {
  const device_id = getOrCreateDeviceId()
  const user_id = getUserId()
  return user_id ? { device_id, user_id } : { device_id }
}
