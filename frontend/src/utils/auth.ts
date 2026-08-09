import { ref } from 'vue'
import type { RouteLocationNormalized, NavigationGuardNext } from 'vue-router'
import { isAdminRoleValue, readCachedCurrentUserRole } from './adminAccess'
import {
  cacheCurrentUserSummary,
  clearCurrentUserSummary,
  readCachedCurrentUserSummary,
} from './currentUser'
import { createHttpErrorFromResponse, type ErrorPolicyContext } from './httpErrorPolicy'
import {
  forbiddenSystemRecoveryLocation,
  storeIntendedRoute,
  unavailableSystemRecoveryLocation,
} from './authNavigation'

export const isAppConnecting = ref(false)
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
export const AUTH_ROUTE_GUARD_TIMEOUT_MS = 15_000

async function withAuthRequestTimeout<T>(
  request: (signal: AbortSignal) => Promise<T>,
  externalSignal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController()
  const forwardAbort = () => controller.abort(externalSignal?.reason)
  let timeoutId: ReturnType<typeof setTimeout> | null = null

  if (externalSignal?.aborted) {
    forwardAbort()
  } else {
    externalSignal?.addEventListener('abort', forwardAbort, { once: true })
  }

  const timeoutPromise = new Promise<never>((_resolve, reject) => {
    timeoutId = setTimeout(() => {
      const reason = new DOMException('Authentication request timed out', 'TimeoutError')
      controller.abort(reason)
      reject(reason)
    }, AUTH_ROUTE_GUARD_TIMEOUT_MS)
  })
  const requestPromise = Promise.resolve().then(() => request(controller.signal))

  try {
    return await Promise.race([requestPromise, timeoutPromise])
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId)
    externalSignal?.removeEventListener('abort', forwardAbort)
  }
}

// Helper to decode JWT payload (without validation)
function parseJwt(token: string) {
  try {
    const base64Url = token.split('.')[1]
    if (!base64Url) return null
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/')
    const jsonPayload = decodeURIComponent(
      window
        .atob(base64)
        .split('')
        .map(function (c) {
          return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)
        })
        .join(''),
    )
    return JSON.parse(jsonPayload)
  } catch (e) {
    return null
  }
}

export type RefreshResult = 'success' | 'network_error' | 'auth_error'

let isRefreshing = false
let refreshPromise: Promise<RefreshResult> | null = null
let expiryTimerId: ReturnType<typeof setInterval> | null = null

export async function tryRefreshToken(): Promise<RefreshResult> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return 'auth_error'

  if (isRefreshing && refreshPromise) {
    return refreshPromise
  }

  isRefreshing = true
  refreshPromise = (async (): Promise<RefreshResult> => {
    try {
      const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
      const res = await withAuthRequestTimeout((signal) =>
        fetch(`${baseUrl}/api/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
          signal,
        }),
      )

      if (res.ok) {
        const data = await res.json()
        localStorage.setItem('auth_token', data.access_token)
        localStorage.setItem('refresh_token', data.refresh_token)
        return 'success'
      }

      // Explicit rejection from the server
      if (res.status === 401 || res.status === 403 || res.status === 404) {
        return 'auth_error'
      }

      // 5xx Server Error or 429 Rate Limit (treat as temporary network issue)
      return 'network_error'
    } catch {
      // Failed to connect to the server (offline, connection drop)
      return 'network_error'
    } finally {
      isRefreshing = false
      refreshPromise = null
    }
  })()

  return refreshPromise
}

type AuthenticationResolution = 'authenticated' | 'unauthenticated' | 'unavailable'

async function resolveAuthentication(): Promise<AuthenticationResolution> {
  const token = localStorage.getItem('auth_token')
  const refresh = localStorage.getItem('refresh_token')
  if (!token && !refresh) return 'unauthenticated'

  if (token) {
    const payload = parseJwt(token)
    if (payload && payload.exp) {
      const now = Math.floor(Date.now() / 1000)
      if (payload.exp > now + 10) {
        return 'authenticated' // Still valid
      }
    }
  }

  // Token expired but we have refresh token - try refreshing
  if (refresh) {
    const result = await tryRefreshToken()
    if (result === 'success') return 'authenticated'

    if (result === 'auth_error') {
      suspendSession()
      return 'unauthenticated'
    }
    return 'unavailable'
  }
  return 'unauthenticated'
}

export async function isAuthenticated(): Promise<boolean> {
  return (await resolveAuthentication()) === 'authenticated'
}

function cacheCurrentUserSummaryFromAuthMe(payload: unknown) {
  cacheCurrentUserSummary(payload)
}

function isInactiveAccountStatus(status: string | null | undefined): boolean {
  return status === 'inactive'
}

export function isAdmin(): boolean {
  return isAdminRoleValue(readCachedCurrentUserRole())
}

type AuthoritativeAccessResult = 'allowed' | 'forbidden' | 'unavailable'
const ROUTE_ACCESS_FETCH_OPTIONS = {
  retryNetwork: false,
  trackConnectionState: false,
} as const

async function ensureAdminAccess(): Promise<AuthoritativeAccessResult> {
  const cachedRole = readCachedCurrentUserRole()
  if (cachedRole) {
    return isAdminRoleValue(cachedRole) ? 'allowed' : 'forbidden'
  }

  try {
    const response = await withAuthRequestTimeout((signal) =>
      apiFetch('/api/auth/me', { ...ROUTE_ACCESS_FETCH_OPTIONS, signal }),
    )
    if (!response.ok) {
      return response.status === 403 ? 'forbidden' : 'unavailable'
    }

    const data = await response.json()
    if (typeof data?.role !== 'string' || !data.role.trim()) {
      return 'unavailable'
    }
    cacheCurrentUserSummaryFromAuthMe(data)
    return isAdminRoleValue(data.role) ? 'allowed' : 'forbidden'
  } catch {
    return 'unavailable'
  }
}

async function ensureMarketAccess(): Promise<AuthoritativeAccessResult> {
  const cachedSummary = readCachedCurrentUserSummary()
  if (cachedSummary) {
    return !isInactiveAccountStatus(cachedSummary.account_status) &&
      cachedSummary.is_accountant !== true
      ? 'allowed'
      : 'forbidden'
  }

  try {
    const response = await withAuthRequestTimeout((signal) =>
      apiFetch('/api/auth/me', { ...ROUTE_ACCESS_FETCH_OPTIONS, signal }),
    )
    if (!response.ok) {
      return response.status === 403 ? 'forbidden' : 'unavailable'
    }

    const data = await response.json()
    if (typeof data?.role !== 'string' || !data.role.trim()) {
      return 'unavailable'
    }
    cacheCurrentUserSummaryFromAuthMe(data)
    return !isInactiveAccountStatus(data.account_status) && data.is_accountant !== true
      ? 'allowed'
      : 'forbidden'
  } catch {
    return 'unavailable'
  }
}

export async function authGuard(
  to: RouteLocationNormalized,
  from: RouteLocationNormalized,
  next: NavigationGuardNext,
) {
  const meta = to.meta

  let authentication: AuthenticationResolution | null = null
  if (to.path === '/login' || meta.requiresAuth) {
    authentication = await resolveAuthentication()
  }

  if (to.path === '/login') {
    if (authentication === 'authenticated') {
      return next('/')
    }
    if (authentication === 'unavailable') {
      return next(unavailableSystemRecoveryLocation())
    }
  }

  if (meta.requiresAuth) {
    if (authentication === 'unavailable') {
      return next(unavailableSystemRecoveryLocation())
    }
    if (authentication !== 'authenticated') {
      storeIntendedRoute(to)
      return next({ name: 'login' })
    }
  }
  if (meta.requiresMarketAccess) {
    const marketAccess = await ensureMarketAccess()
    if (marketAccess === 'forbidden') {
      return next(forbiddenSystemRecoveryLocation())
    }
    if (marketAccess === 'unavailable') {
      return next(unavailableSystemRecoveryLocation())
    }
  }
  if (meta.requiresAdmin) {
    const adminAccess = await ensureAdminAccess()
    if (adminAccess === 'forbidden') {
      return next(forbiddenSystemRecoveryLocation())
    }
    if (adminAccess === 'unavailable') {
      return next(unavailableSystemRecoveryLocation())
    }
  }
  next()
}

export function setupExpiryTimer() {
  if (expiryTimerId !== null) return

  expiryTimerId = setInterval(async () => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      const payload = parseJwt(token)
      if (payload && payload.exp) {
        const now = Math.floor(Date.now() / 1000)
        // Attempt refresh 60 seconds before it actually expires
        if (now >= payload.exp - 60) {
          const result = await tryRefreshToken()
          if (result === 'auth_error') {
            suspendSession()
          }
        }
      }
    }
  }, 30000)
}

export function logout() {
  forceLogout()
}

export function suspendSession() {
  const refreshToken = localStorage.getItem('refresh_token')
  if (refreshToken) {
    localStorage.setItem('suspended_refresh_token', refreshToken)
  }
  localStorage.removeItem('auth_token')
  localStorage.removeItem('refresh_token')
  clearCurrentUserSummary()
  window.location.href = '/login'
}

export function forceLogout() {
  localStorage.removeItem('auth_token')
  localStorage.removeItem('refresh_token')
  localStorage.removeItem('suspended_refresh_token')
  clearCurrentUserSummary()
  window.location.href = '/login'
}

export type ApiFetchOptions = RequestInit & {
  retryNetwork?: boolean
  trackConnectionState?: boolean
}

function normalizeRequestHeaders(headers: HeadersInit | undefined): Record<string, string> {
  const normalized: Record<string, string> = {}
  new Headers(headers).forEach((value, key) => {
    normalized[key] = value
  })
  return normalized
}

const cleanResponseHandler: ProxyHandler<Response> = {
  get(target, property) {
    if (property === 'json') {
      return async (): Promise<unknown> => cleanDeletedSuffixes(await target.json())
    }
    if (property === 'clone') {
      return () => new Proxy(target.clone(), cleanResponseHandler)
    }
    const value = Reflect.get(target, property, target)
    return typeof value === 'function' ? value.bind(target) : value
  },
}

function isErrorPayload(value: unknown): value is { detail?: unknown } {
  return Boolean(value) && typeof value === 'object'
}

function isNetworkError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const message = error.message
  return (
    error.name === 'TypeError' ||
    message.includes('Failed to fetch') ||
    message.toLowerCase().includes('network') ||
    message === 'NetworkError' ||
    message === 'خطا در ارتباط با سرور.' ||
    message.includes('fetch dynamically imported module') ||
    message.includes('Load failed')
  )
}

export async function apiFetch(url: string, options: ApiFetchOptions = {}) {
  const { retryNetwork = true, trackConnectionState = true, ...requestOptions } = options
  let retries = 0
  let didRefresh = false

  while (true) {
    const token = localStorage.getItem('auth_token')

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...normalizeRequestHeaders(requestOptions.headers),
    }

    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }

    const config = {
      ...requestOptions,
      headers,
    }

    const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
    const fullUrl = url.startsWith('http') ? url : `${baseUrl}${url}`

    try {
      const originalResponse = await fetch(fullUrl, config)

      // If we were connecting/retrying, we reconnected successfully
      if (trackConnectionState && isAppConnecting.value) isAppConnecting.value = false

      // Proxy the response to intercept json() and clone()
      const response = new Proxy(originalResponse, cleanResponseHandler)

      // 🔴 403 Forbidden with specific detail
      if (trackConnectionState && isAppConnecting.value) isAppConnecting.value = false

      // 🔴 403 Forbidden with specific detail
      if (response.status === 403) {
        const clone = response.clone()
        let errorData: unknown = null
        try {
          errorData = await clone.json()
        } catch (e) {
          // Ignore parsing errors for other 403s
        }

        if (isErrorPayload(errorData) && errorData.detail === 'REQUIRES_PASSWORD_CHANGE') {
          if (window.location.pathname !== '/setup-password') {
            window.location.href = '/setup-password'
          }
          throw new Error('شما باید رمز عبور خود را تغییر دهید')
        }
        if (
          isErrorPayload(errorData) &&
          (errorData.detail === 'حساب کاربری غیرفعال شده است' ||
            errorData.detail === 'User is blocked')
        ) {
          forceLogout()
          throw new Error('حساب کاربری شما غیرفعال شده است')
        }
      }

      if (response.status === 401) {
        if (didRefresh) {
          forceLogout()
          throw new Error('Unauthorized')
        }

        // Try refresh before logging out
        const result = await tryRefreshToken()

        if (result === 'success') {
          didRefresh = true
          // Start next iteration of the while-loop to retry original request with new token
          continue
        }

        // If specifically failed auth, boot to OTP screen
        if (result === 'auth_error') {
          suspendSession()
          throw new Error('نشست شما منقضی شده است. لطفا مجددا وارد شوید')
        }

        throw new Error('NetworkError')
      }

      if (response.status >= 500) {
        throw new Error('NetworkError') // Trigger auto-reconnect
      }

      return response
    } catch (error: unknown) {
      const isRetryableNetworkError = isNetworkError(error)

      // Is this a network fetch drop?
      if (retryNetwork && isRetryableNetworkError) {
        if (trackConnectionState) isAppConnecting.value = true
        retries++
        console.warn(`[apiFetch] Connection lost. Retrying (${retries})...`)
        await sleep(Math.min(3000, 1000 * Math.pow(1.5, retries))) // Max 3s backoff
        continue
      }
      throw error // Bubble up real app errors (400, validation, etc)
    }
  }
}

import { cleanDeletedSuffixes } from './formatters'

export async function apiFetchJson(
  url: string,
  options: ApiFetchOptions = {},
  errorContext: ErrorPolicyContext = {},
) {
  const response = await apiFetch(url, options)
  if (!response.ok) {
    throw await createHttpErrorFromResponse(response, errorContext)
  }
  if (response.status === 204) return null
  return response.json()
}
