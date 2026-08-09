import type { RouteLocationRaw } from 'vue-router'
import { createSystemRecoveryLocation, SYSTEM_RECOVERY_OUTCOME } from '../router/systemRecovery'

export { SYSTEM_RECOVERY_OUTCOME } from '../router/systemRecovery'

export const AUTH_INTENDED_ROUTE_STORAGE_KEY = 'auth_intended_route_v1'
export const AUTH_INTENDED_ROUTE_MAX_AGE_MS = 30 * 60 * 1000

export interface IntendedRouteCandidate {
  readonly path?: unknown
  readonly fullPath?: unknown
  readonly name?: unknown
}

interface StoredIntendedRoute {
  readonly version: 1
  readonly path: string
  readonly createdAt: number
}

const INTERNAL_ORIGIN = 'https://safe-return.invalid'
const PUBLIC_AUTH_ROUTE_NAMES = new Set([
  'login',
  'invite-landing',
  'web-register',
  'system-recovery',
])
const SENSITIVE_QUERY_KEY_PARTS = [
  'apikey',
  'authorization',
  'code',
  'cookie',
  'credential',
  'jwt',
  'otp',
  'password',
  'passwd',
  'secret',
  'sessionid',
  'signature',
  'signedurl',
  'token',
]

function getSessionStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function removeStoredIntendedRoute(storage: Storage | null) {
  if (!storage) return
  try {
    storage.removeItem(AUTH_INTENDED_ROUTE_STORAGE_KEY)
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
}

function normalizedQueryKey(key: string): string {
  return key.toLowerCase().replace(/[-_.]/g, '')
}

function isSensitiveQueryKey(key: string): boolean {
  const normalized = normalizedQueryKey(key)
  return SENSITIVE_QUERY_KEY_PARTS.some((part) => normalized.includes(part))
}

function decodedValueVariants(value: string): string[] {
  const variants = [value]
  let current = value

  // URLSearchParams has already decoded once. Two additional bounded passes
  // catch nested/double-encoded return URLs without allowing attacker-driven
  // recursion or unusually large decode work.
  for (let pass = 0; pass < 2; pass += 1) {
    try {
      const decoded = decodeURIComponent(current)
      if (decoded === current) break
      variants.push(decoded)
      current = decoded
    } catch {
      break
    }
  }

  return variants
}

function containsUnsafeNestedQueryValue(value: string): boolean {
  return decodedValueVariants(value).some((variant) => {
    const normalizedValue = variant.trim().toLowerCase()
    if (
      normalizedValue.startsWith('//') ||
      normalizedValue.startsWith('http:') ||
      normalizedValue.startsWith('https:')
    ) {
      return true
    }

    for (const match of variant.matchAll(/(?:^|[?&#])([^?&#=]+)=/gu)) {
      let nestedKey = match[1] ?? ''
      try {
        nestedKey = decodeURIComponent(nestedKey)
      } catch {
        // The undecoded key is still checked below and fails closed if it
        // contains an explicit sensitive marker.
      }
      if (isSensitiveQueryKey(nestedKey)) return true
    }

    return false
  })
}

function isPublicAuthPath(pathname: string): boolean {
  const normalized = (pathname.replace(/\/+$/, '') || '/').toLowerCase()
  return (
    normalized === '/login' ||
    normalized === '/register' ||
    normalized === '/__system/recovery' ||
    normalized === '/i' ||
    normalized.startsWith('/i/')
  )
}

export function validateIntendedRoute(candidate: IntendedRouteCandidate): string | null {
  if (typeof candidate.name === 'string' && PUBLIC_AUTH_ROUTE_NAMES.has(candidate.name)) {
    return null
  }

  const raw =
    typeof candidate.fullPath === 'string'
      ? candidate.fullPath.trim()
      : typeof candidate.path === 'string'
        ? candidate.path.trim()
        : ''
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return null
  if (/[\u0000-\u001f\u007f]/u.test(raw)) return null

  try {
    const url = new URL(raw, INTERNAL_ORIGIN)
    if (url.origin !== INTERNAL_ORIGIN || url.username || url.password || url.hash) return null

    const decodedPath = decodeURIComponent(url.pathname)
    if (/[\u0000-\u001f\u007f\\]/u.test(decodedPath) || isPublicAuthPath(decodedPath)) return null
    if (url.searchParams.get('outcome') === SYSTEM_RECOVERY_OUTCOME.FORBIDDEN) return null
    for (const [key, value] of url.searchParams.entries()) {
      if (isSensitiveQueryKey(key)) return null
      if (containsUnsafeNestedQueryValue(value)) return null
    }

    return `${url.pathname}${url.search}`
  } catch {
    return null
  }
}

export function clearIntendedRoute(storage: Storage | null = getSessionStorage()) {
  removeStoredIntendedRoute(storage)
}

export function storeIntendedRoute(
  candidate: IntendedRouteCandidate,
  storage: Storage | null = getSessionStorage(),
  now = Date.now(),
): boolean {
  const path = validateIntendedRoute(candidate)
  if (!storage || !path) {
    removeStoredIntendedRoute(storage)
    return false
  }

  const payload: StoredIntendedRoute = { version: 1, path, createdAt: now }
  try {
    storage.setItem(AUTH_INTENDED_ROUTE_STORAGE_KEY, JSON.stringify(payload))
    return true
  } catch {
    removeStoredIntendedRoute(storage)
    return false
  }
}

export function readIntendedRoute(
  storage: Storage | null = getSessionStorage(),
  now = Date.now(),
): string | null {
  if (!storage) return null

  try {
    const raw = storage.getItem(AUTH_INTENDED_ROUTE_STORAGE_KEY)
    if (!raw) return null
    const payload = JSON.parse(raw) as Partial<StoredIntendedRoute>
    const isFresh =
      typeof payload.createdAt === 'number' &&
      Number.isFinite(payload.createdAt) &&
      payload.createdAt <= now &&
      now - payload.createdAt <= AUTH_INTENDED_ROUTE_MAX_AGE_MS
    const path =
      payload.version === 1 && typeof payload.path === 'string'
        ? validateIntendedRoute({ fullPath: payload.path })
        : null

    if (!path || !isFresh) {
      removeStoredIntendedRoute(storage)
      return null
    }
    return path
  } catch {
    removeStoredIntendedRoute(storage)
    return null
  }
}

export function consumeIntendedRoute(
  storage: Storage | null = getSessionStorage(),
  now = Date.now(),
): string | null {
  const path = readIntendedRoute(storage, now)
  removeStoredIntendedRoute(storage)
  return path
}

export function forbiddenSystemRecoveryLocation(): RouteLocationRaw {
  return {
    ...createSystemRecoveryLocation(SYSTEM_RECOVERY_OUTCOME.FORBIDDEN),
    replace: true,
  }
}

export function unavailableSystemRecoveryLocation(): RouteLocationRaw {
  return {
    ...createSystemRecoveryLocation(SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE),
    replace: true,
  }
}
