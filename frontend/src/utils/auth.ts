/**
 * Auth Utility — مدیریت متمرکز احراز هویت
 *
 * - apiFetch: wrapper برای fetch با مدیریت خودکار 401
 * - Token refresh خودکار با refresh_token
 * - تایمر انقضای توکن — ریدایرکت خودکار به لاگین
 */

import router from '../router'

// ─── State ───
let refreshPromise: Promise<boolean> | null = null
let expiryTimer: ReturnType<typeof setTimeout> | null = null

// ─── JWT Helpers ───

function parseJwt(token: string): Record<string, any> | null {
  try {
    const base64 = token.split('.')[1]
    const json = atob(base64.replace(/-/g, '+').replace(/_/g, '/'))
    return JSON.parse(json)
  } catch {
    return null
  }
}

function getTokenExpiryMs(token: string): number | null {
  const payload = parseJwt(token)
  return payload?.exp ? payload.exp * 1000 : null
}

export function isTokenExpired(token: string): boolean {
  const expiry = getTokenExpiryMs(token)
  if (!expiry) return true
  return Date.now() >= expiry
}

// ─── Logout ───

export function forceLogout() {
  localStorage.removeItem('auth_token')
  localStorage.removeItem('refresh_token')
  clearExpiryTimer()
  if (router.currentRoute.value.name !== 'login') {
    router.replace({ name: 'login' })
  }
}

// ─── Token Refresh ───

async function tryRefreshToken(): Promise<boolean> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return false

  try {
    const res = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken })
    })

    if (res.ok) {
      const data = await res.json()
      localStorage.setItem('auth_token', data.access_token)
      if (data.refresh_token) {
        localStorage.setItem('refresh_token', data.refresh_token)
      }
      setupExpiryTimer()
      return true
    }
  } catch (e) {
    console.error('Token refresh failed:', e)
  }

  return false
}

/**
 * تلاش برای refresh توکن — اگر موفق نبود، logout اجباری
 */
export async function refreshOrLogout(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = tryRefreshToken().finally(() => {
      refreshPromise = null
    })
  }
  const ok = await refreshPromise
  if (!ok) forceLogout()
  return ok
}

// ─── Centralized API Fetch ───

/**
 * fetch wrapper با مدیریت خودکار 401 و token refresh
 * - اگر 401 بگیره، ابتدا refresh token امتحان می‌کنه
 * - اگر refresh هم فیل بشه، کاربر رو به لاگین می‌بره
 */
export async function apiFetch(
  endpoint: string,
  options: RequestInit = {}
): Promise<Response> {
  const url = endpoint.startsWith('/api') ? endpoint : `/api${endpoint}`

  const makeHeaders = (): HeadersInit => {
    const token = localStorage.getItem('auth_token')
    return {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {})
    }
  }

  let response = await fetch(url, { ...options, headers: makeHeaders() })

  if (response.status === 401) {
    // Try refresh
    if (!refreshPromise) {
      refreshPromise = tryRefreshToken().finally(() => {
        refreshPromise = null
      })
    }
    const refreshed = await refreshPromise

    if (refreshed) {
      // Retry with new token
      response = await fetch(url, { ...options, headers: makeHeaders() })
    }

    // Still 401 → force logout
    if (response.status === 401) {
      forceLogout()
    }
  }

  return response
}

/**
 * Convenience: apiFetch + JSON parse + error handling
 */
export async function apiFetchJson(
  endpoint: string,
  options: RequestInit = {}
): Promise<any> {
  const response = await apiFetch(endpoint, options)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || `خطا: ${response.status}`)
  }
  return response.json()
}

// ─── Expiry Timer ───

function clearExpiryTimer() {
  if (expiryTimer) {
    clearTimeout(expiryTimer)
    expiryTimer = null
  }
}

/**
 * راه‌اندازی تایمر انقضای توکن
 * - اگر توکن منقضی شده باشه → refresh فوری
 * - اگر توکن هنوز معتبر باشه → تایمر برای refresh قبل از انقضا
 */
export function setupExpiryTimer() {
  clearExpiryTimer()

  const token = localStorage.getItem('auth_token')
  if (!token) return

  const expiry = getTokenExpiryMs(token)
  if (!expiry) return

  const timeUntilExpiry = expiry - Date.now()

  if (timeUntilExpiry <= 0) {
    // Token already expired → try refresh now
    refreshOrLogout()
    return
  }

  // Refresh 30 seconds before expiry; if token is short-lived, at half-life
  const refreshBefore = Math.min(30_000, timeUntilExpiry / 2)
  const delay = Math.max(timeUntilExpiry - refreshBefore, 1000)

  expiryTimer = setTimeout(() => {
    refreshOrLogout()
  }, delay)
}
