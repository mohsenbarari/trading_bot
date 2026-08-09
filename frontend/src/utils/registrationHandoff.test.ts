import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  REGISTRATION_HANDOFF_KIND,
  REGISTRATION_HANDOFF_MAX_AGE_MS,
  REGISTRATION_HANDOFF_STORAGE_KEY,
  REGISTRATION_EXCHANGE_STORAGE_KEY,
  captureLegacyRegistrationHandoff,
  clearRegistrationExchangeId,
  clearRegistrationHandoff,
  getOrCreateRegistrationExchangeId,
  readRegistrationHandoff,
  replaceWithScrubbedRegistrationUrl,
  scrubRegistrationSecretsFromBrowserUrl,
  writeRegistrationHandoff,
} from './registrationHandoff'

function serializedStorage(storage: Storage): string {
  return Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index) ?? ''
    return `${key}:${storage.getItem(key) ?? ''}`
  }).join('|')
}

describe('registration handoff', () => {
  beforeEach(() => {
    clearRegistrationHandoff()
    clearRegistrationExchangeId()
    sessionStorage.clear()
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps a bounded typed handoff in module memory and never serializes its bearer', () => {
    const bearer = 'REG-memory-only-123'
    const storageWrite = vi.spyOn(Storage.prototype, 'setItem')
    const consoleLog = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    const consoleInfo = vi.spyOn(console, 'info').mockImplementation(() => undefined)
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    expect(
      writeRegistrationHandoff(
        {
          kind: REGISTRATION_HANDOFF_KIND.REGISTRATION,
          token: ` ${bearer} `,
        },
        1_000,
      ),
    ).toBe(true)

    expect(readRegistrationHandoff(1_001)).toEqual({
      kind: REGISTRATION_HANDOFF_KIND.REGISTRATION,
      token: bearer,
    })
    expect(storageWrite).not.toHaveBeenCalled()
    expect(serializedStorage(sessionStorage)).not.toContain(bearer)
    expect(serializedStorage(localStorage)).not.toContain(bearer)
    expect(consoleLog).not.toHaveBeenCalled()
    expect(consoleInfo).not.toHaveBeenCalled()
    expect(consoleWarn).not.toHaveBeenCalled()
    expect(consoleError).not.toHaveBeenCalled()

    clearRegistrationHandoff()
    expect(readRegistrationHandoff(1_001)).toBeNull()
  })

  it('keeps a high-entropy exchange binding in one fixed tab-local record', async () => {
    const consoleLog = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    const consoleInfo = vi.spyOn(console, 'info').mockImplementation(() => undefined)
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const exchangeId = getOrCreateRegistrationExchangeId(1_000)

    expect(exchangeId).toMatch(/^exchange_[a-f0-9]{64}$/u)
    expect(getOrCreateRegistrationExchangeId(1_001)).toBe(exchangeId)
    expect(sessionStorage.length).toBe(1)
    expect(sessionStorage.key(0)).toBe(REGISTRATION_EXCHANGE_STORAGE_KEY)
    const storedRecord = sessionStorage.getItem(REGISTRATION_EXCHANGE_STORAGE_KEY) ?? ''
    expect(storedRecord).toContain(String(exchangeId))
    expect(storedRecord).not.toContain('abc123')
    expect(storedRecord).not.toContain('token')
    expect(storedRecord).not.toContain('phone')
    expect(storedRecord).not.toContain('otp')
    expect(consoleLog).not.toHaveBeenCalled()
    expect(consoleInfo).not.toHaveBeenCalled()
    expect(consoleWarn).not.toHaveBeenCalled()
    expect(consoleError).not.toHaveBeenCalled()

    vi.resetModules()
    const freshModule = await import('./registrationHandoff')
    expect(freshModule.getOrCreateRegistrationExchangeId(1_002)).toBe(exchangeId)

    freshModule.clearRegistrationExchangeId()
    expect(sessionStorage.getItem(REGISTRATION_EXCHANGE_STORAGE_KEY)).toBeNull()
    const nextBrowserId = freshModule.getOrCreateRegistrationExchangeId(1_003)
    expect(nextBrowserId).toMatch(/^exchange_[a-f0-9]{64}$/u)
    expect(nextBrowserId).not.toBe(exchangeId)
  })

  it('removes residue written by the retired storage implementation without reading it', () => {
    const staleBearer = 'INV-retired-storage-secret'
    sessionStorage.setItem(
      REGISTRATION_HANDOFF_STORAGE_KEY,
      JSON.stringify({ version: 1, token: staleBearer }),
    )
    localStorage.setItem(REGISTRATION_HANDOFF_STORAGE_KEY, staleBearer)

    expect(readRegistrationHandoff(2_000)).toBeNull()
    expect(sessionStorage.getItem(REGISTRATION_HANDOFF_STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(REGISTRATION_HANDOFF_STORAGE_KEY)).toBeNull()
  })

  it('consumes a navigation handoff once and fails safely on a second mount', () => {
    expect(
      writeRegistrationHandoff(
        { kind: REGISTRATION_HANDOFF_KIND.INVITATION, token: 'INV-one-shot' },
        3_000,
      ),
    ).toBe(true)

    expect(captureLegacyRegistrationHandoff({}, 3_001).handoff).toEqual({
      kind: REGISTRATION_HANDOFF_KIND.INVITATION,
      token: 'INV-one-shot',
    })
    expect(readRegistrationHandoff(3_001)).toBeNull()
    expect(captureLegacyRegistrationHandoff({}, 3_002).handoff).toBeNull()
  })

  it('does not survive a module reload boundary', async () => {
    expect(
      writeRegistrationHandoff(
        { kind: REGISTRATION_HANDOFF_KIND.INVITATION, token: 'INV-refresh-boundary' },
        4_000,
      ),
    ).toBe(true)

    vi.resetModules()
    const freshModule = await import('./registrationHandoff')
    expect(freshModule.readRegistrationHandoff(4_001)).toBeNull()
  })

  it('rejects future and expired in-memory handoffs', () => {
    writeRegistrationHandoff(
      { kind: REGISTRATION_HANDOFF_KIND.INVITATION, token: 'INV-future' },
      5_001,
    )
    expect(readRegistrationHandoff(5_000)).toBeNull()

    writeRegistrationHandoff(
      { kind: REGISTRATION_HANDOFF_KIND.INVITATION, token: 'INV-expired' },
      5_000,
    )
    expect(readRegistrationHandoff(5_000 + REGISTRATION_HANDOFF_MAX_AGE_MS + 1)).toBeNull()
  })

  it('consumes one canonical legacy secret and preserves only safe query state', () => {
    const bearer = 'INV-canonical-123'
    expect(
      captureLegacyRegistrationHandoff(
        {
          token: bearer,
          source: 'sms',
        },
        6_000,
      ),
    ).toEqual({
      handoff: { kind: REGISTRATION_HANDOFF_KIND.INVITATION, token: bearer },
      hadSensitiveQuery: true,
      sanitizedQuery: { source: 'sms' },
    })
    expect(readRegistrationHandoff(6_001)).toBeNull()
    expect(serializedStorage(sessionStorage)).not.toContain(bearer)
    expect(serializedStorage(localStorage)).not.toContain(bearer)
  })

  it('fails closed for ambiguous canonical URLs and drops stale module state', () => {
    writeRegistrationHandoff(
      { kind: REGISTRATION_HANDOFF_KIND.REGISTRATION, token: 'REG-stale' },
      7_000,
    )

    expect(
      captureLegacyRegistrationHandoff(
        {
          token: 'INV-123',
          registration_token: 'REG-123',
          source: 'legacy',
        },
        7_001,
      ),
    ).toEqual({
      handoff: null,
      hadSensitiveQuery: true,
      sanitizedQuery: { source: 'legacy' },
    })
    expect(readRegistrationHandoff(7_001)).toBeNull()
  })

  it('scrubs case variants but never consumes them as a handoff', () => {
    expect(
      captureLegacyRegistrationHandoff(
        {
          Token: 'INV-variant',
          Registration_Token: 'REG-variant',
          source: 'legacy',
        },
        8_000,
      ),
    ).toEqual({
      handoff: null,
      hadSensitiveQuery: true,
      sanitizedQuery: { source: 'legacy' },
    })
  })

  it('rejects a canonical secret when a case variant makes the URL ambiguous', () => {
    expect(
      captureLegacyRegistrationHandoff(
        {
          token: 'INV-canonical',
          ToKeN: 'INV-variant',
          source: 'legacy',
        },
        8_100,
      ),
    ).toEqual({
      handoff: null,
      hadSensitiveQuery: true,
      sanitizedQuery: { source: 'legacy' },
    })
  })

  it('scrubs normalized separator variants without treating them as consumable canonicals', () => {
    expect(
      captureLegacyRegistrationHandoff(
        {
          'registration-token': 'REG-hyphen-secret',
          registrationtoken: 'REG-joined-secret',
          source: 'legacy',
        },
        8_200,
      ),
    ).toEqual({
      handoff: null,
      hadSensitiveQuery: true,
      sanitizedQuery: { source: 'legacy' },
    })
  })

  it('fails closed and removes nested single/double-encoded registration bearers', () => {
    const nestedBearer = 'REG-nested-secret'
    expect(
      captureLegacyRegistrationHandoff(
        {
          next: `%252Fregister%253Fregistration_token%253D${nestedBearer}`,
          source: 'sms',
        },
        8_300,
      ),
    ).toEqual({
      handoff: null,
      hadSensitiveQuery: true,
      sanitizedQuery: { source: 'sms' },
    })

    const replaceState = vi.fn()
    expect(
      scrubRegistrationSecretsFromBrowserUrl(
        {
          href: `https://example.test/register?next=%252Fregister%253Fregistration-token%253D${nestedBearer}&source=sms`,
        },
        { replaceState },
      ),
    ).toBe(true)
    expect(replaceState).toHaveBeenCalledWith(null, '', '/register?source=sms')
    expect(JSON.stringify(replaceState.mock.calls)).not.toContain(nestedBearer)
  })

  it('replaces a secret-bearing URL with null history state and preserves safe query/hash', () => {
    const bearer = 'INV-history-secret'
    const replaceState = vi.fn()
    expect(
      scrubRegistrationSecretsFromBrowserUrl(
        { href: `https://example.test/register?token=${bearer}&source=sms#form` },
        { replaceState },
      ),
    ).toBe(true)
    expect(replaceState).toHaveBeenCalledWith(null, '', '/register?source=sms#form')
    expect(JSON.stringify(replaceState.mock.calls)).not.toContain(bearer)
  })

  it('removes every case variant from the replacement URL', () => {
    const replaceState = vi.fn()
    expect(
      scrubRegistrationSecretsFromBrowserUrl(
        {
          href: 'https://example.test/register?Token=INV-123&Registration_Token=REG-123&source=sms#form',
        },
        { replaceState },
      ),
    ).toBe(true)
    expect(replaceState).toHaveBeenCalledWith(null, '', '/register?source=sms#form')
  })

  it('removes a secret-bearing fragment while preserving unrelated safe query values', () => {
    const bearer = 'INV-fragment-secret'
    const replaceState = vi.fn()

    expect(
      scrubRegistrationSecretsFromBrowserUrl(
        {
          href: `https://example.test/register?token=${bearer}&source=sms#token=${bearer}`,
        },
        { replaceState },
      ),
    ).toBe(true)
    expect(replaceState).toHaveBeenCalledWith(null, '', '/register?source=sms')
    expect(JSON.stringify(replaceState.mock.calls)).not.toContain(bearer)
  })

  it('scrubs a double-encoded secret fragment even without a query handoff', () => {
    const replaceState = vi.fn()

    expect(
      scrubRegistrationSecretsFromBrowserUrl(
        {
          href: 'https://example.test/register#registration_token%253DREG-fragment',
        },
        { replaceState },
      ),
    ).toBe(true)
    expect(replaceState).toHaveBeenCalledWith(null, '', '/register')
  })

  it('scrubs a route-shaped encoded secret fragment', () => {
    const replaceState = vi.fn()

    expect(
      scrubRegistrationSecretsFromBrowserUrl(
        {
          href: 'https://example.test/register#/legacy/registration_token%253DREG-fragment',
        },
        { replaceState },
      ),
    ).toBe(true)
    expect(replaceState).toHaveBeenCalledWith(null, '', '/register')
  })

  it('hard-reloads only a scrubbed same-origin registration URL', () => {
    const replace = vi.fn()
    replaceWithScrubbedRegistrationUrl({
      pathname: '/register',
      search: '?source=otp',
      hash: '#safe',
      replace,
    })
    expect(replace).toHaveBeenCalledWith('/register?source=otp#safe')

    replace.mockClear()
    replaceWithScrubbedRegistrationUrl({
      pathname: '/register',
      search: '',
      hash: '#/legacy/registration_token=REG-secret',
      replace,
    })
    expect(replace).toHaveBeenCalledWith('/register')
  })
})
