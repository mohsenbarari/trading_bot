import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.fn()
const routeRequestJsonMock = vi.fn()

function authoritativeUser(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    role: 'عادی',
    account_name: 'authoritative-user',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    ...overrides,
  }
}

vi.mock('./auth', () => ({
  apiFetch: apiFetchMock,
}))

vi.mock('./routeRequest', () => ({
  routeRequestJson: routeRequestJsonMock,
}))

describe('currentUser utils', () => {
  beforeEach(() => {
    vi.resetModules()
    apiFetchMock.mockReset()
    routeRequestJsonMock.mockReset()
    localStorage.clear()
  })

  it('cacheCurrentUserSummary persists the accountant and customer flags', async () => {
    const { cacheCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')

    const result = cacheCurrentUserSummary({
      id: 15,
      role: 'عادی',
      full_name: 'علی',
      account_name: 'ali',
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: '2026-05-21T12:00:00Z',
      is_accountant: true,
      is_customer: true,
      customer_tier: 'tier2',
      customer_management_name: 'مشتری ویژه',
      trading_restricted_until: '2026-05-22T12:00:00Z',
    })

    expect(result).toMatchObject({
      id: 15,
      role: 'عادی',
      full_name: 'علی',
      account_name: 'ali',
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: '2026-05-21T12:00:00Z',
      is_accountant: true,
      is_customer: true,
      customer_tier: 'tier2',
      customer_management_name: 'مشتری ویژه',
      trading_restricted_until: '2026-05-22T12:00:00Z',
    })
    expect(result?.cached_at).toMatch(/^\d{4}-\d{2}-\d{2}T/)
    expect(readCachedCurrentUserSummary()).toMatchObject({
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: '2026-05-21T12:00:00Z',
      is_accountant: true,
      is_customer: true,
      customer_tier: 'tier2',
      customer_management_name: 'مشتری ویژه',
      trading_restricted_until: '2026-05-22T12:00:00Z',
    })
  })

  it('primeCurrentUserSummary keeps additive accountant state from /api/auth/me', async () => {
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 9,
        role: 'مدیر ارشد',
        full_name: 'مینا',
        account_name: 'mina',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        is_accountant: false,
        is_customer: true,
        customer_tier: 'tier1',
        customer_management_name: 'مشتری بازار',
      }),
    })

    const { primeCurrentUserSummary } = await import('./currentUser')
    const result = await primeCurrentUserSummary(true)

    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(result).toMatchObject({
      id: 9,
      role: 'مدیر ارشد',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      is_accountant: false,
      is_customer: true,
      customer_tier: 'tier1',
      customer_management_name: 'مشتری بازار',
    })
  })

  it('requires a valid status and both role flags for an authoritative identity', async () => {
    const { isAuthoritativeCurrentUserSummary } = await import('./currentUser')

    expect(isAuthoritativeCurrentUserSummary(authoritativeUser())).toBe(true)
    expect(
      isAuthoritativeCurrentUserSummary(authoritativeUser({ account_status: 'inactive' })),
    ).toBe(true)
    expect(isAuthoritativeCurrentUserSummary({ role: 'مدیر ارشد' })).toBe(false)
    expect(
      isAuthoritativeCurrentUserSummary({
        ...authoritativeUser(),
        is_customer: undefined,
      }),
    ).toBe(false)
    expect(
      isAuthoritativeCurrentUserSummary({
        ...authoritativeUser(),
        account_status: 'unknown',
      }),
    ).toBe(false)
  })

  it('normalizes invalid cache values, refreshes partial summaries, and clears state', async () => {
    const {
      cacheCurrentUserSummary,
      clearCurrentUserSummary,
      currentUserSummary,
      isAdminRole,
      isAuthoritativeCurrentUserSummary,
      primeCurrentUserSummary,
      readCachedCurrentUserSummary,
    } = await import('./currentUser')

    localStorage.setItem('current_user_summary', '{broken-json')
    expect(readCachedCurrentUserSummary()).toBeNull()

    expect(cacheCurrentUserSummary({ role: '' })).toBeNull()
    expect(localStorage.getItem('current_user_summary')).toBeNull()

    const normalized = cacheCurrentUserSummary({ id: '42', role: 'مدیر میانی', account_name: 99 })
    expect(normalized).toMatchObject({ id: 42, role: 'مدیر میانی', account_name: null })
    expect(normalized?.is_accountant).toBeUndefined()
    expect(normalized?.is_customer).toBeUndefined()
    expect(isAuthoritativeCurrentUserSummary(normalized)).toBe(false)
    expect(isAdminRole(normalized?.role)).toBe(true)

    const cached = await primeCurrentUserSummary(false)
    expect(cached).toEqual(normalized)
    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')

    clearCurrentUserSummary()
    expect(currentUserSummary.value).toBeNull()
    expect(readCachedCurrentUserSummary()).toBeNull()
    expect(isAdminRole('عادی')).toBe(false)
    expect(isAdminRole(undefined)).toBe(false)
  })

  it('shares in-flight prime requests and keeps or clears cache on failure responses', async () => {
    let resolveProfile!: (response: unknown) => void
    apiFetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveProfile = resolve
      }),
    )

    const { cacheCurrentUserSummary, primeCurrentUserSummary, readCachedCurrentUserSummary } =
      await import('./currentUser')
    const first = primeCurrentUserSummary(true)
    const second = primeCurrentUserSummary(true)
    resolveProfile({
      ok: true,
      json: async () => authoritativeUser({ id: 5, full_name: 'User Five' }),
    })

    await expect(first).resolves.toMatchObject({ id: 5, role: 'عادی' })
    await expect(second).resolves.toMatchObject({ id: 5, role: 'عادی' })
    expect(apiFetchMock).toHaveBeenCalledTimes(1)

    cacheCurrentUserSummary(authoritativeUser({ id: 6, account_name: 'kept' }))
    apiFetchMock.mockResolvedValueOnce({ ok: false, status: 500 })
    await expect(primeCurrentUserSummary(true)).resolves.toMatchObject({
      id: 6,
      account_name: 'kept',
    })
    expect(readCachedCurrentUserSummary()).toMatchObject({ id: 6 })

    apiFetchMock.mockResolvedValueOnce({ ok: false, status: 403 })
    await expect(primeCurrentUserSummary(true)).resolves.toBeNull()
    expect(readCachedCurrentUserSummary()).toBeNull()

    cacheCurrentUserSummary(authoritativeUser({ id: 7, account_name: 'fallback' }))
    apiFetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(primeCurrentUserSummary(true)).resolves.toMatchObject({
      id: 7,
      account_name: 'fallback',
    })
  })

  it('does not share or apply an in-flight profile request after the auth token changes', async () => {
    let resolveOldProfile!: (response: unknown) => void
    let resolveNewProfile!: (response: unknown) => void
    apiFetchMock
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOldProfile = resolve
        }),
      )
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveNewProfile = resolve
        }),
      )

    const { primeCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')

    localStorage.setItem('auth_token', 'old-token')
    const oldRequest = primeCurrentUserSummary(true)

    localStorage.setItem('auth_token', 'new-token')
    const newRequest = primeCurrentUserSummary(true)

    expect(apiFetchMock).toHaveBeenCalledTimes(2)

    resolveOldProfile({
      ok: true,
      json: async () => authoritativeUser({ id: 1, account_name: 'old-user' }),
    })
    await expect(oldRequest).resolves.toBeNull()
    expect(readCachedCurrentUserSummary()).toBeNull()

    resolveNewProfile({
      ok: true,
      json: async () => authoritativeUser({ id: 2, role: 'مدیر ارشد', account_name: 'new-admin' }),
    })
    await expect(newRequest).resolves.toMatchObject({
      id: 2,
      role: 'مدیر ارشد',
      account_name: 'new-admin',
    })
    expect(readCachedCurrentUserSummary()).toMatchObject({
      id: 2,
      role: 'مدیر ارشد',
      account_name: 'new-admin',
    })
  })

  it('returns structured cache and network success without changing prime semantics', async () => {
    const { cacheCurrentUserSummary, loadCurrentUserSummary, primeCurrentUserSummary } =
      await import('./currentUser')
    const cached = cacheCurrentUserSummary(authoritativeUser({ id: 21, account_name: 'cached' }))

    await expect(loadCurrentUserSummary()).resolves.toEqual({
      state: 'ready',
      source: 'cache',
      user: cached,
      error: null,
    })
    expect(routeRequestJsonMock).not.toHaveBeenCalled()
    expect(apiFetchMock).not.toHaveBeenCalled()

    routeRequestJsonMock.mockResolvedValueOnce(
      authoritativeUser({ id: 22, role: 'مدیر میانی', account_name: 'fresh' }),
    )
    const controller = new AbortController()
    await expect(
      loadCurrentUserSummary({ force: true, signal: controller.signal, timeoutMs: 900 }),
    ).resolves.toMatchObject({
      state: 'ready',
      source: 'network',
      user: { id: 22, role: 'مدیر میانی', account_name: 'fresh' },
      error: null,
    })
    expect(routeRequestJsonMock).toHaveBeenCalledWith(
      '/api/auth/me',
      expect.objectContaining({
        signal: controller.signal,
        timeoutMs: 900,
      }),
    )

    await expect(primeCurrentUserSummary(false)).resolves.toMatchObject({
      id: 22,
      account_name: 'fresh',
    })
    expect(apiFetchMock).not.toHaveBeenCalled()
  })

  it('never treats persisted or prior-account identity as authority for a new auth token', async () => {
    localStorage.setItem('auth_token', 'token-a')
    localStorage.setItem(
      'current_user_summary',
      JSON.stringify(authoritativeUser({ id: 81, role: 'مدیر ارشد', account_name: 'persisted-a' })),
    )
    routeRequestJsonMock
      .mockResolvedValueOnce(authoritativeUser({ id: 82, account_name: 'network-a' }))
      .mockResolvedValueOnce(
        authoritativeUser({ id: 83, is_customer: true, account_name: 'network-b' }),
      )

    const { cacheCurrentUserSummary, loadCurrentUserSummary } = await import('./currentUser')

    await expect(loadCurrentUserSummary()).resolves.toMatchObject({
      state: 'ready',
      source: 'network',
      user: { id: 82, account_name: 'network-a' },
    })

    cacheCurrentUserSummary(authoritativeUser({ id: 84, account_name: 'bound-a' }))
    localStorage.setItem('auth_token', 'token-b')
    await expect(loadCurrentUserSummary()).resolves.toMatchObject({
      state: 'ready',
      source: 'network',
      user: { id: 83, is_customer: true, account_name: 'network-b' },
    })
    expect(routeRequestJsonMock).toHaveBeenCalledTimes(2)
  })

  it('retains cached identity as stale on structured load failure', async () => {
    const { AppHttpError } = await import('./httpErrorPolicy')
    const { cacheCurrentUserSummary, loadCurrentUserSummary } = await import('./currentUser')
    const cached = cacheCurrentUserSummary(authoritativeUser({ id: 31, account_name: 'retained' }))
    routeRequestJsonMock.mockRejectedValueOnce(
      new AppHttpError({
        status: null,
        errorCode: 'NETWORK_ERROR',
        detail: 'offline',
      }),
    )

    const result = await loadCurrentUserSummary({ force: true })

    expect(result).toMatchObject({ state: 'stale', source: 'cache', user: cached })
    expect(result.error).toBeInstanceOf(AppHttpError)
    expect(result.error?.errorCode).toBe('NETWORK_ERROR')
  })

  it('clears assumed identity on structured authorization failure', async () => {
    const { AppHttpError } = await import('./httpErrorPolicy')
    const {
      cacheCurrentUserSummary,
      currentUserSummary,
      loadCurrentUserSummary,
      readCachedCurrentUserSummary,
    } = await import('./currentUser')
    cacheCurrentUserSummary(
      authoritativeUser({
        id: 41,
        role: 'مدیر ارشد',
        account_name: 'no-longer-authorized',
      }),
    )
    routeRequestJsonMock.mockRejectedValueOnce(
      new AppHttpError({
        status: 403,
        detail: 'forbidden',
      }),
    )

    await expect(loadCurrentUserSummary({ force: true })).resolves.toMatchObject({
      state: 'unauthorized',
      source: 'network',
      user: null,
      error: { status: 403 },
    })
    expect(currentUserSummary.value).toBeNull()
    expect(readCachedCurrentUserSummary()).toBeNull()
  })

  it('reports invalid current-user payloads as errors instead of false ready state', async () => {
    routeRequestJsonMock.mockResolvedValueOnce({ id: 51, account_name: 'missing-role' })
    const { loadCurrentUserSummary } = await import('./currentUser')

    const result = await loadCurrentUserSummary({ force: true })

    expect(result).toMatchObject({
      state: 'error',
      source: 'network',
      user: null,
      error: { errorCode: 'CURRENT_USER_INVALID_RESPONSE' },
    })
  })

  it('lets only the latest structured same-token request update the cache', async () => {
    let resolveOlder!: (value: unknown) => void
    let resolveLatest!: (value: unknown) => void
    routeRequestJsonMock
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOlder = resolve
        }),
      )
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveLatest = resolve
        }),
      )

    const { loadCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')
    localStorage.setItem('auth_token', 'same-token')
    const older = loadCurrentUserSummary({ force: true })
    const latest = loadCurrentUserSummary({ force: true })

    resolveLatest(authoritativeUser({ id: 62, role: 'مدیر میانی', account_name: 'latest' }))
    await expect(latest).resolves.toMatchObject({
      state: 'ready',
      user: { id: 62, account_name: 'latest' },
    })

    resolveOlder(authoritativeUser({ id: 61, account_name: 'older' }))
    await expect(older).resolves.toMatchObject({
      state: 'stale',
      source: 'cache',
      user: { id: 62, account_name: 'latest' },
      error: { errorCode: 'CURRENT_USER_REQUEST_SUPERSEDED' },
    })
    expect(readCachedCurrentUserSummary()).toMatchObject({ id: 62, account_name: 'latest' })
  })

  it('does not let an older prime response overwrite newer structured authority', async () => {
    let resolvePrime!: (value: unknown) => void
    apiFetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePrime = resolve
      }),
    )
    routeRequestJsonMock.mockResolvedValueOnce(
      authoritativeUser({
        id: 72,
        role: 'مدیر میانی',
        account_name: 'new-authority',
        account_status: 'inactive',
      }),
    )

    const { loadCurrentUserSummary, primeCurrentUserSummary, readCachedCurrentUserSummary } =
      await import('./currentUser')
    localStorage.setItem('auth_token', 'same-token')
    const olderPrime = primeCurrentUserSummary(true)
    const latestStructured = loadCurrentUserSummary({ force: true })

    await expect(latestStructured).resolves.toMatchObject({
      state: 'ready',
      user: { id: 72, account_name: 'new-authority', account_status: 'inactive' },
    })
    resolvePrime({
      ok: true,
      json: async () =>
        authoritativeUser({
          id: 71,
          role: 'مدیر ارشد',
          account_name: 'old-authority',
          account_status: 'active',
        }),
    })

    await expect(olderPrime).resolves.toMatchObject({ id: 72, account_name: 'new-authority' })
    expect(readCachedCurrentUserSummary()).toMatchObject({
      id: 72,
      role: 'مدیر میانی',
      account_name: 'new-authority',
      account_status: 'inactive',
    })
  })

  it('coalesces ordinary page loads and shell priming into the active fresh authority request', async () => {
    let resolve!: (value: unknown) => void
    routeRequestJsonMock.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const { loadCurrentUserSummary, primeCurrentUserSummary } = await import('./currentUser')
    localStorage.setItem('auth_token', 'same-token')
    const first = loadCurrentUserSummary({ force: true })
    const second = loadCurrentUserSummary()
    const prime = primeCurrentUserSummary(true)
    expect(second).toBe(first)
    expect(routeRequestJsonMock).toHaveBeenCalledTimes(1)
    expect(apiFetchMock).not.toHaveBeenCalled()
    resolve(authoritativeUser({ id: 81 }))
    await expect(first).resolves.toMatchObject({ state: 'ready', user: { id: 81 } })
    await expect(second).resolves.toMatchObject({ state: 'ready', user: { id: 81 } })
    await expect(prime).resolves.toMatchObject({ id: 81 })

    routeRequestJsonMock.mockResolvedValueOnce(authoritativeUser({ id: 82 }))
    await expect(loadCurrentUserSummary({ force: true })).resolves.toMatchObject({ state: 'ready', source: 'network', user: { id: 82 } })
    expect(routeRequestJsonMock).toHaveBeenCalledTimes(2)
  })

  it('preserves real error semantics for every consumer of a coalesced load', async () => {
    let reject!: (error: unknown) => void
    routeRequestJsonMock.mockReturnValueOnce(new Promise((_done, fail) => { reject = fail }))
    const { AppHttpError } = await import('./httpErrorPolicy')
    const { loadCurrentUserSummary, primeCurrentUserSummary } = await import('./currentUser')
    const first = loadCurrentUserSummary()
    const second = loadCurrentUserSummary()
    const prime = primeCurrentUserSummary()
    reject(new AppHttpError({ status: 503, detail: 'unavailable' }))
    for (const request of [first, second]) {
      await expect(request).resolves.toMatchObject({ state: 'error', user: null, error: { status: 503 } })
    }
    await expect(prime).resolves.toBeNull()
    expect(routeRequestJsonMock).toHaveBeenCalledTimes(1)
    expect(apiFetchMock).not.toHaveBeenCalled()
  })

  it('cache clear fences old same-token structured responses and starts a separate fresh request', async () => {
    let resolveOld!: (value: unknown) => void
    let resolveNew!: (value: unknown) => void
    routeRequestJsonMock
      .mockReturnValueOnce(new Promise((done) => { resolveOld = done }))
      .mockReturnValueOnce(new Promise((done) => { resolveNew = done }))
    const { loadCurrentUserSummary, clearCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')
    localStorage.setItem('auth_token', 'same-token')
    const old = loadCurrentUserSummary()
    clearCurrentUserSummary()
    const fresh = loadCurrentUserSummary()
    expect(routeRequestJsonMock).toHaveBeenCalledTimes(2)
    resolveNew(authoritativeUser({ id: 92, role: 'عادی' }))
    await expect(fresh).resolves.toMatchObject({ state: 'ready', user: { id: 92 } })
    resolveOld(authoritativeUser({ id: 91, role: 'مدیر ارشد' }))
    await expect(old).resolves.toMatchObject({ state: 'stale', user: { id: 92 }, error: { errorCode: 'CURRENT_USER_REQUEST_SUPERSEDED' } })
    expect(readCachedCurrentUserSummary()).toMatchObject({ id: 92, role: 'عادی' })
  })

  it('logout cache clear prevents a delayed structured response from restoring old identity', async () => {
    let resolve!: (value: unknown) => void
    routeRequestJsonMock.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const { loadCurrentUserSummary, clearCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')
    const pending = loadCurrentUserSummary({ force: true })
    clearCurrentUserSummary()
    resolve(authoritativeUser({ id: 101, role: 'مدیر ارشد' }))
    await expect(pending).resolves.toMatchObject({ state: 'error', user: null, error: { errorCode: 'CURRENT_USER_REQUEST_SUPERSEDED' } })
    expect(readCachedCurrentUserSummary()).toBeNull()
  })

  it.each(['clear', 'token-change'])('fences an old prime response if %s occurs while JSON decoding awaits', async (action) => {
    let resolveBody!: (value: unknown) => void
    apiFetchMock.mockResolvedValueOnce({ ok: true, json: () => new Promise((done) => { resolveBody = done }) })
    const { primeCurrentUserSummary, clearCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')
    localStorage.setItem('auth_token', 'old-token')
    const pending = primeCurrentUserSummary(true)
    await Promise.resolve()
    expect(resolveBody).toBeTypeOf('function')
    if (action === 'clear') clearCurrentUserSummary()
    else localStorage.setItem('auth_token', 'new-token')
    resolveBody(authoritativeUser({ id: 111, role: 'مدیر ارشد' }))
    await expect(pending).resolves.toBeNull()
    expect(readCachedCurrentUserSummary()).toBeNull()
  })
})
