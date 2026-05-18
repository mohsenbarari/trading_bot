import { ref } from 'vue'
import { apiFetch } from './auth'

export interface CurrentUserSummary {
  id?: number
  role: string
  full_name?: string | null
  account_name?: string | null
  account_status?: string | null
  global_lock_grace_expires_at?: string | null
  global_web_locked_at?: string | null
  is_accountant?: boolean
}

const CURRENT_USER_STORAGE_KEY = 'current_user_summary'

function hasStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function normalizeCurrentUserSummary(raw: unknown): CurrentUserSummary | null {
  if (!raw || typeof raw !== 'object') return null

  const user = raw as Record<string, unknown>
  if (typeof user.role !== 'string' || !user.role.trim()) return null

  return {
    id: typeof user.id === 'number' ? user.id : Number.isFinite(Number(user.id)) ? Number(user.id) : undefined,
    role: user.role,
    full_name: typeof user.full_name === 'string' ? user.full_name : null,
    account_name: typeof user.account_name === 'string' ? user.account_name : null,
    account_status: typeof user.account_status === 'string' ? user.account_status : null,
    global_lock_grace_expires_at:
      typeof user.global_lock_grace_expires_at === 'string' ? user.global_lock_grace_expires_at : null,
    global_web_locked_at:
      typeof user.global_web_locked_at === 'string' ? user.global_web_locked_at : null,
    is_accountant: user.is_accountant === true,
  }
}

export function readCachedCurrentUserSummary(): CurrentUserSummary | null {
  if (!hasStorage()) return null

  try {
    const raw = localStorage.getItem(CURRENT_USER_STORAGE_KEY)
    if (!raw) return null
    return normalizeCurrentUserSummary(JSON.parse(raw))
  } catch {
    return null
  }
}

export const currentUserSummary = ref<CurrentUserSummary | null>(readCachedCurrentUserSummary())

export function cacheCurrentUserSummary(raw: unknown): CurrentUserSummary | null {
  const normalized = normalizeCurrentUserSummary(raw)
  currentUserSummary.value = normalized

  if (hasStorage()) {
    if (normalized) {
      localStorage.setItem(CURRENT_USER_STORAGE_KEY, JSON.stringify(normalized))
    } else {
      localStorage.removeItem(CURRENT_USER_STORAGE_KEY)
    }
  }

  return normalized
}

export function clearCurrentUserSummary() {
  currentUserSummary.value = null
  if (hasStorage()) {
    localStorage.removeItem(CURRENT_USER_STORAGE_KEY)
  }
}

let currentUserRequest: Promise<CurrentUserSummary | null> | null = null

export async function primeCurrentUserSummary(force = false): Promise<CurrentUserSummary | null> {
  if (!force && currentUserSummary.value?.role) {
    return currentUserSummary.value
  }

  if (currentUserRequest) {
    return currentUserRequest
  }

  currentUserRequest = (async () => {
    try {
      const response = await apiFetch('/api/auth/me')
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          clearCurrentUserSummary()
        }
        return currentUserSummary.value
      }

      return cacheCurrentUserSummary(await response.json())
    } catch {
      return currentUserSummary.value
    } finally {
      currentUserRequest = null
    }
  })()

  return currentUserRequest
}

export function isAdminRole(role: string | null | undefined) {
  return role === 'مدیر ارشد' || role === 'مدیر میانی'
}