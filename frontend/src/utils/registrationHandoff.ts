export const REGISTRATION_HANDOFF_STORAGE_KEY = 'web_registration_handoff_v1'
export const REGISTRATION_HANDOFF_MAX_AGE_MS = 10 * 60 * 1000
export const REGISTRATION_EXCHANGE_STORAGE_KEY = 'web_registration_exchange_v1'

export const REGISTRATION_HANDOFF_KIND = {
  INVITATION: 'invitation',
  REGISTRATION: 'registration',
} as const

export type RegistrationHandoffKind =
  (typeof REGISTRATION_HANDOFF_KIND)[keyof typeof REGISTRATION_HANDOFF_KIND]

export interface RegistrationHandoff {
  readonly kind: RegistrationHandoffKind
  readonly token: string
}

interface InMemoryRegistrationHandoff extends RegistrationHandoff {
  readonly createdAt: number
}

interface RegistrationExchangeBinding {
  readonly exchangeId: string
  readonly createdAt: number
}

type RegistrationQuery = Readonly<Record<string, unknown>>

const CANONICAL_REGISTRATION_QUERY_KEYS = new Set(['token', 'registration_token'])
let inMemoryHandoff: InMemoryRegistrationHandoff | null = null
let inMemoryExchangeBinding: RegistrationExchangeBinding | null = null

function isFreshTimestamp(createdAt: number, now: number): boolean {
  return (
    Number.isFinite(createdAt) &&
    createdAt <= now &&
    now - createdAt <= REGISTRATION_HANDOFF_MAX_AGE_MS
  )
}

function isValidExchangeId(value: unknown): value is string {
  return typeof value === 'string' && /^exchange_[a-f0-9]{64}$/u.test(value)
}

function sessionStorageOrNull(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function generateExchangeId(): string | null {
  const cryptoApi = globalThis.crypto
  if (!cryptoApi?.getRandomValues) return null
  const bytes = new Uint8Array(32)
  cryptoApi.getRandomValues(bytes)
  return `exchange_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`
}

/**
 * Returns a tab-bound, high-entropy idempotency binding. The fixed storage
 * record contains no invitation code, route, phone, OTP, or bearer token.
 */
export function getOrCreateRegistrationExchangeId(now = Date.now()): string | null {
  if (
    inMemoryExchangeBinding &&
    isValidExchangeId(inMemoryExchangeBinding.exchangeId) &&
    isFreshTimestamp(inMemoryExchangeBinding.createdAt, now)
  ) {
    return inMemoryExchangeBinding.exchangeId
  }
  inMemoryExchangeBinding = null

  const storage = sessionStorageOrNull()
  if (storage) {
    try {
      const raw = storage.getItem(REGISTRATION_EXCHANGE_STORAGE_KEY)
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<RegistrationExchangeBinding>
        if (
          isValidExchangeId(parsed.exchangeId) &&
          typeof parsed.createdAt === 'number' &&
          isFreshTimestamp(parsed.createdAt, now)
        ) {
          inMemoryExchangeBinding = {
            exchangeId: parsed.exchangeId,
            createdAt: parsed.createdAt,
          }
          return parsed.exchangeId
        }
      }
      storage.removeItem(REGISTRATION_EXCHANGE_STORAGE_KEY)
    } catch {
      // Hardened/private contexts fall back to this module instance only.
    }
  }

  if (!Number.isFinite(now)) return null
  const exchangeId = generateExchangeId()
  if (!exchangeId) return null
  inMemoryExchangeBinding = { exchangeId, createdAt: now }
  if (storage) {
    try {
      storage.setItem(REGISTRATION_EXCHANGE_STORAGE_KEY, JSON.stringify(inMemoryExchangeBinding))
    } catch {
      // Memory-only binding remains fail-safe for the current page lifetime.
    }
  }
  return exchangeId
}

export function clearRegistrationExchangeId() {
  inMemoryExchangeBinding = null
  const storage = sessionStorageOrNull()
  if (!storage) return
  try {
    storage.removeItem(REGISTRATION_EXCHANGE_STORAGE_KEY)
  } catch {
    // Best-effort cleanup of non-bearer idempotency metadata.
  }
}

function isSensitiveRegistrationQueryKey(key: string): boolean {
  return /^(?:token|registration[-_]?token)$/iu.test(key.trim())
}

function hasSensitiveRegistrationMaterial(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasSensitiveRegistrationMaterial)
  if (typeof value !== 'string') return false

  let candidate = value
  for (let pass = 0; pass < 3; pass += 1) {
    if (/(?:^|[?&#/])(?:token|registration[-_]?token)\s*=/iu.test(candidate)) return true
    try {
      const decoded = decodeURIComponent(candidate)
      if (decoded === candidate) break
      candidate = decoded
    } catch {
      break
    }
  }
  return false
}

function hasSensitiveRegistrationFragment(hash: string): boolean {
  return hasSensitiveRegistrationMaterial(hash.replace(/^#/, ''))
}

export interface LegacyRegistrationHandoffCapture {
  readonly handoff: RegistrationHandoff | null
  readonly hadSensitiveQuery: boolean
  readonly sanitizedQuery: Record<string, unknown>
}

function normalizedToken(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const token = value.trim()
  return token ? token : null
}

function isRegistrationHandoffKind(value: unknown): value is RegistrationHandoffKind {
  return (
    value === REGISTRATION_HANDOFF_KIND.INVITATION ||
    value === REGISTRATION_HANDOFF_KIND.REGISTRATION
  )
}

function browserStorages(): Storage[] {
  if (typeof window === 'undefined') return []
  const storages: Storage[] = []
  try {
    storages.push(window.sessionStorage)
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
  try {
    storages.push(window.localStorage)
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
  return storages
}

function removeLegacyStorageResidue() {
  for (const storage of browserStorages()) {
    try {
      storage.removeItem(REGISTRATION_HANDOFF_STORAGE_KEY)
    } catch {
      // Best-effort removal of data written by the retired storage handoff.
    }
  }
}

function isFresh(handoff: InMemoryRegistrationHandoff, now: number): boolean {
  return isFreshTimestamp(handoff.createdAt, now)
}

export function clearRegistrationHandoff() {
  inMemoryHandoff = null
  removeLegacyStorageResidue()
}

/**
 * Places a bearer handoff in module memory only. The secret is deliberately not
 * serialised into Web Storage, router state, the document, or a URL.
 */
export function writeRegistrationHandoff(handoff: RegistrationHandoff, now = Date.now()): boolean {
  removeLegacyStorageResidue()
  const token = normalizedToken(handoff.token)
  if (!token || !isRegistrationHandoffKind(handoff.kind) || !Number.isFinite(now)) {
    inMemoryHandoff = null
    return false
  }

  inMemoryHandoff = {
    kind: handoff.kind,
    token,
    createdAt: now,
  }
  return true
}

/** Testable bounded peek; application consumers use the one-shot capture below. */
export function readRegistrationHandoff(now = Date.now()): RegistrationHandoff | null {
  removeLegacyStorageResidue()
  const handoff = inMemoryHandoff
  if (!handoff || !isFresh(handoff, now)) {
    inMemoryHandoff = null
    return null
  }
  return { kind: handoff.kind, token: handoff.token }
}

function consumeRegistrationHandoff(now: number): RegistrationHandoff | null {
  const handoff = readRegistrationHandoff(now)
  inMemoryHandoff = null
  return handoff
}

export function captureLegacyRegistrationHandoff(
  query: RegistrationQuery,
  now = Date.now(),
): LegacyRegistrationHandoffCapture {
  removeLegacyStorageResidue()
  const sanitizedQuery = Object.fromEntries(
    Object.entries(query).filter(
      ([key, value]) =>
        !isSensitiveRegistrationQueryKey(key) && !hasSensitiveRegistrationMaterial(value),
    ),
  )
  const sensitiveQueryKeys = Object.keys(query).filter(isSensitiveRegistrationQueryKey)
  const hasNestedSensitiveValue = Object.entries(query).some(
    ([key, value]) =>
      !isSensitiveRegistrationQueryKey(key) && hasSensitiveRegistrationMaterial(value),
  )
  const hadInvitationQuery = Object.prototype.hasOwnProperty.call(query, 'token')
  const hadRegistrationQuery = Object.prototype.hasOwnProperty.call(query, 'registration_token')
  const hadSensitiveQuery = sensitiveQueryKeys.length > 0 || hasNestedSensitiveValue
  const hasNonCanonicalSensitiveKey = sensitiveQueryKeys.some(
    (key) => !CANONICAL_REGISTRATION_QUERY_KEYS.has(key),
  )

  if (!hadSensitiveQuery) {
    return {
      handoff: consumeRegistrationHandoff(now),
      hadSensitiveQuery,
      sanitizedQuery,
    }
  }

  // A query handoff is consumed by this mount only; stale module state must not
  // win over an explicit legacy URL, even when that URL is malformed.
  inMemoryHandoff = null
  const invitationToken = normalizedToken(query.token)
  const registrationToken = normalizedToken(query.registration_token)

  // Only exact canonical keys are consumable. Every case variant is still
  // scrubbed, and ambiguity/malformed values fail closed.
  if (
    hasNonCanonicalSensitiveKey ||
    hasNestedSensitiveValue ||
    (hadInvitationQuery && hadRegistrationQuery) ||
    (!invitationToken && !registrationToken)
  ) {
    return { handoff: null, hadSensitiveQuery, sanitizedQuery }
  }

  const handoff: RegistrationHandoff = registrationToken
    ? { kind: REGISTRATION_HANDOFF_KIND.REGISTRATION, token: registrationToken }
    : { kind: REGISTRATION_HANDOFF_KIND.INVITATION, token: invitationToken! }

  return { handoff, hadSensitiveQuery, sanitizedQuery }
}

export function scrubRegistrationSecretsFromBrowserUrl(
  location: Pick<Location, 'href'> = window.location,
  history: Pick<History, 'replaceState'> = window.history,
): boolean {
  try {
    const url = new URL(location.href)
    const sensitiveQueryKeys = [
      ...new Set(Array.from(url.searchParams.keys()).filter(isSensitiveRegistrationQueryKey)),
    ]
    const nestedSensitiveQueryKeys = [
      ...new Set(
        Array.from(url.searchParams.entries())
          .filter(
            ([key, value]) =>
              !isSensitiveRegistrationQueryKey(key) && hasSensitiveRegistrationMaterial(value),
          )
          .map(([key]) => key),
      ),
    ]
    const hasSensitiveHash = hasSensitiveRegistrationFragment(url.hash)
    if (
      sensitiveQueryKeys.length === 0 &&
      nestedSensitiveQueryKeys.length === 0 &&
      !hasSensitiveHash
    ) {
      return false
    }

    sensitiveQueryKeys.forEach((key) => url.searchParams.delete(key))
    nestedSensitiveQueryKeys.forEach((key) => url.searchParams.delete(key))
    // Do not carry router/browser state from a secret-bearing entry into the
    // replacement entry. Vue Router is synchronised immediately afterwards.
    history.replaceState(
      null,
      '',
      `${url.pathname}${url.search}${hasSensitiveHash ? '' : url.hash}`,
    )
    return true
  } catch {
    return false
  }
}

interface BrowserLocationReplacement {
  readonly pathname: string
  readonly search: string
  readonly hash: string
  replace(url: string): void
}

export function replaceWithScrubbedRegistrationUrl(
  locationLike: BrowserLocationReplacement = window.location,
): void {
  const pathname =
    locationLike.pathname.startsWith('/') && !locationLike.pathname.startsWith('//')
      ? locationLike.pathname
      : '/register'
  const candidate = `${pathname}${locationLike.search}${locationLike.hash}`
  locationLike.replace(hasSensitiveRegistrationMaterial(candidate) ? pathname : candidate)
}
