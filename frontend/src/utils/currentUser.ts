import { ref } from 'vue'
import { apiFetch } from './auth'
import { AppHttpError } from './httpErrorPolicy'
import { routeRequestJson } from './routeRequest'

export interface CurrentUserSummary {
  id?: number
  role: string
  full_name?: string | null
  account_name?: string | null
  account_status?: string | null
  global_lock_grace_expires_at?: string | null
  global_web_locked_at?: string | null
  is_accountant?: boolean
  accountant_owner_user_id?: number | null
  accountant_owner_account_name?: string | null
  is_customer?: boolean
  customer_tier?: 'tier1' | 'tier2' | null
  customer_owner_user_id?: number | null
  customer_owner_account_name?: string | null
  customer_management_name?: string | null
  telegram_linked?: boolean
  can_connect_telegram?: boolean
  telegram_link_denial_reason?: string | null
}

export type CurrentUserLoadState = 'ready' | 'stale' | 'unauthorized' | 'error'
export type CurrentUserLoadSource = 'cache' | 'network'

export interface CurrentUserLoadResult {
  state: CurrentUserLoadState
  source: CurrentUserLoadSource
  user: CurrentUserSummary | null
  error: AppHttpError | null
}

export interface CurrentUserLoadOptions {
  force?: boolean
  signal?: AbortSignal
  timeoutMs?: number | null
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
    accountant_owner_user_id:
      typeof user.accountant_owner_user_id === 'number'
        ? user.accountant_owner_user_id
        : Number.isFinite(Number(user.accountant_owner_user_id))
          ? Number(user.accountant_owner_user_id)
          : null,
    accountant_owner_account_name: typeof user.accountant_owner_account_name === 'string' ? user.accountant_owner_account_name : null,
    is_customer: user.is_customer === true,
    customer_tier: user.customer_tier === 'tier1' || user.customer_tier === 'tier2' ? user.customer_tier : null,
    customer_owner_user_id:
      typeof user.customer_owner_user_id === 'number'
        ? user.customer_owner_user_id
        : Number.isFinite(Number(user.customer_owner_user_id))
          ? Number(user.customer_owner_user_id)
          : null,
    customer_owner_account_name: typeof user.customer_owner_account_name === 'string' ? user.customer_owner_account_name : null,
    customer_management_name: typeof user.customer_management_name === 'string' ? user.customer_management_name : null,
    telegram_linked: user.telegram_linked === true,
    can_connect_telegram: user.can_connect_telegram === true,
    telegram_link_denial_reason:
      typeof user.telegram_link_denial_reason === 'string' ? user.telegram_link_denial_reason : null,
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

function currentUserLoadError(error: unknown, errorCode = 'CURRENT_USER_LOAD_ERROR') {
  if (error instanceof AppHttpError) return error
  const detail = error instanceof Error && error.message
    ? error.message
    : 'دریافت اطلاعات حساب ممکن نشد.'

  return new AppHttpError({
    status: null,
    errorCode,
    detail,
    context: {
      surface: 'app',
      scope: 'page',
      operation: 'initial-load',
      fallbackMessage: 'دریافت اطلاعات حساب ممکن نشد.',
    },
  })
}

let currentUserStructuredRequestRevision = 0

function supersededCurrentUserResult(): CurrentUserLoadResult {
  const user = currentUserSummary.value
  return {
    state: user ? 'stale' : 'error',
    source: user ? 'cache' : 'network',
    user,
    error: currentUserLoadError(
      new Error('A newer current-user request completed first.'),
      'CURRENT_USER_REQUEST_SUPERSEDED',
    ),
  }
}

/**
 * Structured counterpart to primeCurrentUserSummary for route state machines.
 * The legacy prime helper intentionally keeps its cache-first, fail-soft
 * return semantics; this loader adds explicit error/stale/authorization truth.
 */
export async function loadCurrentUserSummary(
  options: CurrentUserLoadOptions = {},
): Promise<CurrentUserLoadResult> {
  const { force = false, signal, timeoutMs } = options
  const cachedUser = currentUserSummary.value

  if (!force && cachedUser?.role) {
    return { state: 'ready', source: 'cache', user: cachedUser, error: null }
  }

  const requestAuthToken = hasStorage() ? localStorage.getItem('auth_token') : null
  const requestRevision = ++currentUserStructuredRequestRevision

  try {
    const payload = await routeRequestJson<unknown>('/api/auth/me', {
      signal,
      ...(timeoutMs === undefined ? {} : { timeoutMs }),
      errorContext: {
        surface: 'app',
        scope: 'page',
        operation: 'initial-load',
        resourceLabel: 'حساب کاربری',
        fallbackMessage: 'دریافت اطلاعات حساب ممکن نشد.',
      },
    })

    if (requestRevision !== currentUserStructuredRequestRevision) {
      return supersededCurrentUserResult()
    }

    if ((hasStorage() ? localStorage.getItem('auth_token') : null) !== requestAuthToken) {
      const error = currentUserLoadError(
        new Error('Authentication context changed while loading the current user.'),
        'CURRENT_USER_CONTEXT_CHANGED',
      )
      const currentUser = currentUserSummary.value
      return {
        state: currentUser ? 'stale' : 'error',
        source: currentUser ? 'cache' : 'network',
        user: currentUser,
        error,
      }
    }

    const normalized = normalizeCurrentUserSummary(payload)
    if (!normalized) {
      const error = currentUserLoadError(
        new Error('Current-user response is invalid.'),
        'CURRENT_USER_INVALID_RESPONSE',
      )
      return {
        state: cachedUser ? 'stale' : 'error',
        source: cachedUser ? 'cache' : 'network',
        user: cachedUser,
        error,
      }
    }

    const user = cacheCurrentUserSummary(normalized)
    return { state: 'ready', source: 'network', user, error: null }
  } catch (caught) {
    if (requestRevision !== currentUserStructuredRequestRevision) {
      return supersededCurrentUserResult()
    }

    const error = currentUserLoadError(caught)
    if (error.status === 401 || error.status === 403) {
      clearCurrentUserSummary()
      return { state: 'unauthorized', source: 'network', user: null, error }
    }

    const retainedUser = currentUserSummary.value || cachedUser
    return {
      state: retainedUser ? 'stale' : 'error',
      source: retainedUser ? 'cache' : 'network',
      user: retainedUser,
      error,
    }
  }
}

let currentUserRequest: Promise<CurrentUserSummary | null> | null = null
let currentUserRequestAuthToken: string | null = null

export async function primeCurrentUserSummary(force = false): Promise<CurrentUserSummary | null> {
  if (!force && currentUserSummary.value?.role) {
    return currentUserSummary.value
  }

  const requestAuthToken = hasStorage() ? localStorage.getItem('auth_token') : null

  if (currentUserRequest && currentUserRequestAuthToken === requestAuthToken) {
    return currentUserRequest
  }

  let request!: Promise<CurrentUserSummary | null>
  request = (async () => {
    try {
      const response = await apiFetch('/api/auth/me')
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          clearCurrentUserSummary()
        }
        return currentUserSummary.value
      }

      if ((hasStorage() ? localStorage.getItem('auth_token') : null) !== requestAuthToken) {
        return currentUserSummary.value
      }

      return cacheCurrentUserSummary(await response.json())
    } catch {
      return currentUserSummary.value
    } finally {
      if (currentUserRequest === request) {
        currentUserRequest = null
        currentUserRequestAuthToken = null
      }
    }
  })()

  currentUserRequest = request
  currentUserRequestAuthToken = requestAuthToken
  return currentUserRequest
}

export function isAdminRole(role: string | null | undefined) {
  return role === 'مدیر ارشد' || role === 'مدیر میانی'
}
