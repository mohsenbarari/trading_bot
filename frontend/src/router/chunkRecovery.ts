export const CHUNK_RELOAD_MARKER_KEY = 'uiux-v2:chunk-reload-attempt'
export const CHUNK_RELOAD_MARKER_TTL_MS = 2 * 60 * 1000

const INTERNAL_ORIGIN = 'https://chunk-recovery.invalid'

export type ChunkReloadDecision =
  | { readonly kind: 'reload'; readonly path: string }
  | { readonly kind: 'recover' }

function getSessionStorage(): Storage | null {
  if (typeof window === 'undefined') return null

  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/**
 * Returns a same-origin pathname only. Query strings and fragments are always
 * dropped so registration, OTP, invitation, and other sensitive values can
 * never cross the hard-reload boundary.
 */
export function sanitizeChunkReloadPath(value: unknown): string | null {
  if (typeof value !== 'string') return null

  const raw = value.trim()
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return null
  if (/[\u0000-\u001f\u007f]/u.test(raw)) return null

  try {
    const url = new URL(raw, INTERNAL_ORIGIN)
    if (url.origin !== INTERNAL_ORIGIN || url.username || url.password) return null

    const decodedPath = decodeURIComponent(url.pathname)
    if (/[\u0000-\u001f\u007f\\]/u.test(decodedPath)) return null
    return url.pathname || '/'
  } catch {
    return null
  }
}

export function decideChunkReload(
  candidatePath: unknown,
  storage: Storage | null = getSessionStorage(),
  now = Date.now(),
): ChunkReloadDecision {
  const path = sanitizeChunkReloadPath(candidatePath)
  if (!path || !storage || !Number.isFinite(now)) return { kind: 'recover' }

  try {
    const markedAt = Number.parseInt(storage.getItem(CHUNK_RELOAD_MARKER_KEY) ?? '', 10)
    const hasRecentAttempt =
      Number.isFinite(markedAt) && markedAt <= now && now - markedAt < CHUNK_RELOAD_MARKER_TTL_MS

    if (hasRecentAttempt) return { kind: 'recover' }

    storage.setItem(CHUNK_RELOAD_MARKER_KEY, String(now))
    if (storage.getItem(CHUNK_RELOAD_MARKER_KEY) !== String(now)) return { kind: 'recover' }
    return { kind: 'reload', path }
  } catch {
    return { kind: 'recover' }
  }
}

export function clearChunkReloadAttempt(storage: Storage | null = getSessionStorage()) {
  if (!storage) return

  try {
    storage.removeItem(CHUNK_RELOAD_MARKER_KEY)
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
}
