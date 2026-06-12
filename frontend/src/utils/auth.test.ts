import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type FetchMock = ReturnType<typeof vi.fn>

const authModulePath = './auth'

function makeJwt(exp: number): string {
  const payload = Buffer.from(JSON.stringify({ exp })).toString('base64url')
  return `header.${payload}.sig`
}

function mockLocation() {
  const location = { href: 'http://localhost/', pathname: '/' }
  Object.defineProperty(window, 'location', {
    value: location,
    configurable: true,
    writable: true,
  })
  return location
}

function makeJsonResponse(payload: unknown, status = 200, ok = status >= 200 && status < 300) {
  return {
    ok,
    status,
    json: async () => payload,
    clone() {
      return makeJsonResponse(payload, status, ok)
    },
  }
}

describe('auth utils', () => {
  let fetchMock: FetchMock

  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    mockLocation()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('tryRefreshToken stores refreshed tokens on success', async () => {
    localStorage.setItem('refresh_token', 'old-refresh')
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ access_token: 'new-auth', refresh_token: 'new-refresh' }),
    })

    const { tryRefreshToken } = await import(authModulePath)
    await expect(tryRefreshToken()).resolves.toBe('success')
    expect(localStorage.getItem('auth_token')).toBe('new-auth')
    expect(localStorage.getItem('refresh_token')).toBe('new-refresh')
  })

  it('tryRefreshToken treats 401 as auth_error and 5xx as network_error', async () => {
    localStorage.setItem('refresh_token', 'refresh')
    const { tryRefreshToken } = await import(authModulePath)

    fetchMock.mockResolvedValueOnce({ ok: false, status: 401 })
    await expect(tryRefreshToken()).resolves.toBe('auth_error')

    fetchMock.mockResolvedValueOnce({ ok: false, status: 503 })
    await expect(tryRefreshToken()).resolves.toBe('network_error')
  })

  it('handles missing refresh tokens, malformed JWTs, and concurrent refresh reuse', async () => {
    const { isAuthenticated, tryRefreshToken } = await import(authModulePath)

    await expect(tryRefreshToken()).resolves.toBe('auth_error')
    localStorage.setItem('auth_token', 'not-a-jwt')
    await expect(isAuthenticated()).resolves.toBe(false)

    localStorage.setItem('refresh_token', 'shared-refresh')
    let resolveRefresh!: (value: unknown) => void
    fetchMock.mockReturnValueOnce(new Promise((resolve) => {
      resolveRefresh = resolve
    }))

    const first = tryRefreshToken()
    const second = tryRefreshToken()
    resolveRefresh({
      ok: true,
      json: async () => ({ access_token: 'shared-auth', refresh_token: 'shared-next' }),
    })

    await expect(first).resolves.toBe('success')
    await expect(second).resolves.toBe('success')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('auth_token')).toBe('shared-auth')
  })

  it('treats JWT decode exceptions as unauthenticated', async () => {
    localStorage.setItem('auth_token', makeJwt(Math.floor(Date.now() / 1000) + 3600))
    const atobSpy = vi.spyOn(window, 'atob').mockImplementation(() => {
      throw new Error('decode failed')
    })

    const { isAuthenticated } = await import(authModulePath)

    await expect(isAuthenticated()).resolves.toBe(false)
    atobSpy.mockRestore()
  })

  it('isAuthenticated returns true for a still-valid access token', async () => {
    const futureExp = Math.floor(Date.now() / 1000) + 3600
    localStorage.setItem('auth_token', makeJwt(futureExp))
    const { isAuthenticated } = await import(authModulePath)

    await expect(isAuthenticated()).resolves.toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('isAuthenticated keeps the session on network refresh failures and suspends on auth failures', async () => {
    const expiredExp = Math.floor(Date.now() / 1000) - 60
    const expiredToken = makeJwt(expiredExp)
    localStorage.setItem('auth_token', expiredToken)
    localStorage.setItem('refresh_token', 'refresh')
    const location = mockLocation()

    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const { isAuthenticated } = await import(authModulePath)
    await expect(isAuthenticated()).resolves.toBe(false)
    expect(localStorage.getItem('auth_token')).toBe(expiredToken)
    expect(localStorage.getItem('refresh_token')).toBe('refresh')
    expect(localStorage.getItem('suspended_refresh_token')).toBeNull()
    expect(location.href).toBe('http://localhost/')

    localStorage.setItem('auth_token', makeJwt(expiredExp))
    localStorage.setItem('refresh_token', 'refresh')
    fetchMock.mockResolvedValueOnce({ ok: false, status: 401 })
    await expect(isAuthenticated()).resolves.toBe(false)
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('suspended_refresh_token')).toBe('refresh')
    expect(location.href).toBe('/login')
  })

  it('suspendSession and forceLogout clear the expected tokens and redirect to login', async () => {
    localStorage.setItem('auth_token', 'auth')
    localStorage.setItem('refresh_token', 'refresh')
    localStorage.setItem('suspended_refresh_token', 'old-suspended')
    const location = mockLocation()
    const { suspendSession, forceLogout } = await import(authModulePath)

    suspendSession()
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('suspended_refresh_token')).toBe('refresh')
    expect(location.href).toBe('/login')

    localStorage.setItem('auth_token', 'auth')
    localStorage.setItem('refresh_token', 'refresh')
    forceLogout()
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('suspended_refresh_token')).toBeNull()
    expect(location.href).toBe('/login')
  })

  it('logout delegates to forceLogout', async () => {
    localStorage.setItem('auth_token', 'auth')
    localStorage.setItem('refresh_token', 'refresh')
    localStorage.setItem('suspended_refresh_token', 'suspended')
    const location = mockLocation()

    const { logout } = await import(authModulePath)

    logout()

    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('suspended_refresh_token')).toBeNull()
    expect(location.href).toBe('/login')
  })

  it('authGuard redirects according to auth and admin requirements', async () => {
    const { authGuard } = await import(authModulePath)

    const next = vi.fn()
    localStorage.setItem('auth_token', makeJwt(Math.floor(Date.now() / 1000) + 3600))
    await authGuard({ path: '/login', meta: {} } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith('/')

    next.mockClear()
    localStorage.clear()
    await authGuard({ path: '/private', meta: { requiresAuth: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith('/login')

    next.mockClear()
    localStorage.setItem('auth_token', makeJwt(Math.floor(Date.now() / 1000) + 3600))
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    await authGuard({ path: '/admin', meta: { requiresAdmin: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith()

    next.mockClear()
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'عادی' }))
    await authGuard({ path: '/admin', meta: { requiresAdmin: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith('/')

    next.mockClear()
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'عادی', account_status: 'inactive' }))
    await authGuard({ path: '/market', meta: { requiresAuth: true, requiresMarketAccess: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith('/')

    next.mockClear()
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'عادی', account_status: 'active', is_accountant: true }))
    await authGuard({ path: '/market', meta: { requiresAuth: true, requiresMarketAccess: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith('/')
  })

  it('authGuard fetches /api/auth/me when admin cache is missing', async () => {
    const token = makeJwt(Math.floor(Date.now() / 1000) + 3600)
    localStorage.setItem('auth_token', token)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({
      id: 7,
      role: 'مدیر میانی',
      account_name: 'manager7',
      is_customer: true,
      customer_tier: 'tier2',
    }))

    const { authGuard } = await import(authModulePath)
    const next = vi.fn()

    await authGuard({ path: '/admin', meta: { requiresAuth: true, requiresAdmin: true } } as any, {} as any, next)

    expect(next).toHaveBeenLastCalledWith()
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
    }))
    expect(JSON.parse(localStorage.getItem('current_user_summary') || '{}')).toMatchObject({
      role: 'مدیر میانی',
      account_name: 'manager7',
      is_customer: true,
      customer_tier: 'tier2',
    })
  })

  it('authGuard fetches /api/auth/me when market access cache is missing and blocks inactive users', async () => {
    const token = makeJwt(Math.floor(Date.now() / 1000) + 3600)
    localStorage.setItem('auth_token', token)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({
      id: 9,
      role: 'عادی',
      account_name: 'inactive9',
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: null,
    }))

    const { authGuard } = await import(authModulePath)
    const next = vi.fn()

    await authGuard({ path: '/market', meta: { requiresAuth: true, requiresMarketAccess: true } } as any, {} as any, next)

    expect(next).toHaveBeenLastCalledWith('/')
    expect(JSON.parse(localStorage.getItem('current_user_summary') || '{}')).toMatchObject({
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: null,
    })
  })

  it('authGuard blocks accountants from market routes when /api/auth/me resolves accountant context', async () => {
    const token = makeJwt(Math.floor(Date.now() / 1000) + 3600)
    localStorage.setItem('auth_token', token)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({
      id: 11,
      role: 'عادی',
      account_name: 'accountant11',
      account_status: 'active',
      is_accountant: true,
    }))

    const { authGuard } = await import(authModulePath)
    const next = vi.fn()

    await authGuard({ path: '/market', meta: { requiresAuth: true, requiresMarketAccess: true } } as any, {} as any, next)

    expect(next).toHaveBeenLastCalledWith('/')
    expect(JSON.parse(localStorage.getItem('current_user_summary') || '{}')).toMatchObject({
      account_status: 'active',
      is_accountant: true,
    })
  })

  it('authGuard tolerates broken cache, failed profile fetches, and non-admin fallback responses', async () => {
    const token = makeJwt(Math.floor(Date.now() / 1000) + 3600)
    localStorage.setItem('auth_token', token)
    localStorage.setItem('current_user_summary', '{broken-json')
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ id: 10, role: 'عادی', account_status: 'active' }))

    const { authGuard } = await import(authModulePath)
    const next = vi.fn()

    await authGuard({ path: '/admin', meta: { requiresAuth: true, requiresAdmin: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith('/')

    next.mockClear()
    localStorage.removeItem('current_user_summary')
    fetchMock.mockResolvedValueOnce(makeJsonResponse({}, 400, false))
    await authGuard({ path: '/market', meta: { requiresAuth: true, requiresMarketAccess: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith()

    next.mockClear()
    fetchMock.mockRejectedValueOnce(new Error('profile unavailable'))
    await authGuard({ path: '/admin', meta: { requiresAuth: true, requiresAdmin: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith('/')

    next.mockClear()
    localStorage.removeItem('current_user_summary')
    fetchMock.mockRejectedValueOnce(new Error('market profile unavailable'))
    await authGuard({ path: '/market', meta: { requiresAuth: true, requiresMarketAccess: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith()
  })

  it('apiFetch refreshes on 401 and retries the original request with the new token', async () => {
    localStorage.setItem('auth_token', 'old-auth')
    localStorage.setItem('refresh_token', 'refresh-token')
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({}, 401, false))
      .mockResolvedValueOnce(makeJsonResponse({ access_token: 'new-auth', refresh_token: 'new-refresh' }))
      .mockResolvedValueOnce(makeJsonResponse({ result: 'ok' }))

    const { apiFetch } = await import(authModulePath)
    const response = await apiFetch('/api/test')

    expect(response.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/test')
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({ Authorization: 'Bearer old-auth' })
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/auth/refresh')
    expect(fetchMock.mock.calls[2]?.[1]?.headers).toMatchObject({ Authorization: 'Bearer new-auth' })
    expect(localStorage.getItem('auth_token')).toBe('new-auth')
    expect(localStorage.getItem('refresh_token')).toBe('new-refresh')
  })

  it('apiFetch redirects to setup-password when the backend requires a password change', async () => {
    localStorage.setItem('auth_token', 'auth-token')
    const location = mockLocation()
    location.pathname = '/chat'
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: 'REQUIRES_PASSWORD_CHANGE' }, 403, false))

    const { apiFetch } = await import(authModulePath)
    await expect(apiFetch('/api/private')).rejects.toThrow('شما باید رمز عبور خود را تغییر دهید')
    expect(location.href).toBe('/setup-password')
  })

  it('apiFetch logs out blocked users and logs out after a refreshed request returns 401 again', async () => {
    localStorage.setItem('auth_token', 'auth-token')
    localStorage.setItem('refresh_token', 'refresh-token')
    const location = mockLocation()
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: 'User is blocked' }, 403, false))

    const { apiFetch } = await import(authModulePath)
    await expect(apiFetch('/api/private')).rejects.toThrow('حساب کاربری شما غیرفعال شده است')
    expect(location.href).toBe('/login')
    expect(localStorage.getItem('auth_token')).toBeNull()

    localStorage.setItem('auth_token', 'old-auth')
    localStorage.setItem('refresh_token', 'refresh-token')
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({}, 401, false))
      .mockResolvedValueOnce(makeJsonResponse({ access_token: 'new-auth', refresh_token: 'new-refresh' }))
      .mockResolvedValueOnce(makeJsonResponse({}, 401, false))

    await expect(apiFetch('/api/private')).rejects.toThrow('Unauthorized')
    expect(location.href).toBe('/login')
    expect(localStorage.getItem('auth_token')).toBeNull()
  })

  it('apiFetch returns plain 403 responses when the error payload cannot be parsed', async () => {
    localStorage.setItem('auth_token', 'auth-token')
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ detail: 'forbidden' }),
      clone() {
        return {
          json: async () => {
            throw new Error('broken clone json')
          },
        }
      },
    })

    const { apiFetch } = await import(authModulePath)
    const response = await apiFetch('/api/private')

    expect(response.status).toBe(403)
  })

  it('setupExpiryTimer refreshes soon-expiring tokens and keeps sessions intact on transient refresh failures', async () => {
    vi.useFakeTimers()
    const location = mockLocation()
    const expiringToken = makeJwt(Math.floor(Date.now() / 1000) + 30)
    localStorage.setItem('auth_token', expiringToken)
    localStorage.setItem('refresh_token', 'refresh-token')
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503 })

    const { setupExpiryTimer } = await import(authModulePath)
    setupExpiryTimer()
    await vi.advanceTimersByTimeAsync(30000)

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/refresh', expect.objectContaining({ method: 'POST' }))
    expect(localStorage.getItem('auth_token')).toBe(expiringToken)
    expect(localStorage.getItem('refresh_token')).toBe('refresh-token')
    expect(localStorage.getItem('suspended_refresh_token')).toBeNull()
    expect(location.href).toBe('http://localhost/')
  })

  it('apiFetch marks the app as reconnecting on network errors and clears it after a successful retry', async () => {
    vi.useFakeTimers()
    localStorage.setItem('auth_token', 'auth-token')
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    fetchMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(makeJsonResponse({ ok: true }))

    const { apiFetch, isAppConnecting } = await import(authModulePath)
    const responsePromise = apiFetch('/api/retry')

    await Promise.resolve()
    expect(isAppConnecting.value).toBe(true)

    await vi.advanceTimersByTimeAsync(1500)
    const response = await responsePromise
    expect(response.ok).toBe(true)
    expect(isAppConnecting.value).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    warnSpy.mockRestore()
  })

  it('apiFetch suspends the session when refresh returns auth_error after a 401', async () => {
    localStorage.setItem('auth_token', 'expired-auth')
    localStorage.setItem('refresh_token', 'refresh-token')
    const location = mockLocation()
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({}, 401, false))
      .mockResolvedValueOnce({ ok: false, status: 401 })

    const { apiFetch } = await import(authModulePath)

    await expect(apiFetch('/api/private')).rejects.toThrow('نشست شما منقضی شده است. لطفا مجددا وارد شوید')
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('suspended_refresh_token')).toBe('refresh-token')
    expect(location.href).toBe('/login')
  })

  it('apiFetch keeps the session intact when refresh returns a network_error and retries once refresh succeeds', async () => {
    vi.useFakeTimers()
    localStorage.setItem('auth_token', 'expired-auth')
    localStorage.setItem('refresh_token', 'refresh-token')
    const location = mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({}, 401, false))
      .mockResolvedValueOnce({ ok: false, status: 503 })
      .mockResolvedValueOnce(makeJsonResponse({}, 401, false))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: 'fresh-auth', refresh_token: 'fresh-refresh' }),
      })
      .mockResolvedValueOnce(makeJsonResponse({ recovered: true }))

    const { apiFetch, isAppConnecting } = await import(authModulePath)
    const responsePromise = apiFetch('/api/refresh-retry')

    await vi.advanceTimersByTimeAsync(1500)
    const response = await responsePromise
    expect(response.ok).toBe(true)
    expect(localStorage.getItem('auth_token')).toBe('fresh-auth')
    expect(localStorage.getItem('refresh_token')).toBe('fresh-refresh')
    expect(localStorage.getItem('suspended_refresh_token')).toBeNull()
    expect(location.href).toBe('http://localhost/')
    expect(isAppConnecting.value).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(5)

    fetchMock.mockReset()
    localStorage.setItem('auth_token', 'auth-token')
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ detail: 'bad gateway' }, 502, false))
      .mockResolvedValueOnce(makeJsonResponse({ recovered: true }))

    const directRetryPromise = apiFetch('/api/direct-5xx')
    await vi.advanceTimersByTimeAsync(1500)
    const directRetryResponse = await directRetryPromise
    expect(directRetryResponse.ok).toBe(true)
    expect(isAppConnecting.value).toBe(false)
    warnSpy.mockRestore()
  })

  it('apiFetch can disable network retry for non-idempotent mutations', async () => {
    localStorage.setItem('auth_token', 'auth-token')
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: 'temporary failure' }, 502, false))

    const { apiFetch, isAppConnecting } = await import(authModulePath)

    await expect(apiFetch('/api/mutation', {
      method: 'PATCH',
      body: JSON.stringify({ value: 1 }),
      retryNetwork: false,
    })).rejects.toThrow('NetworkError')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(isAppConnecting.value).toBe(false)
  })

  it('apiFetchJson returns null for 204 and surfaces response details on failures', async () => {
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse(null, 204))
      .mockResolvedValueOnce(makeJsonResponse({ detail: 'validation failed' }, 422, false))

    const { apiFetchJson } = await import(authModulePath)

    await expect(apiFetchJson('/api/no-content')).resolves.toBeNull()
    await expect(apiFetchJson('/api/fail')).rejects.toThrow('validation failed')
  })

  it('apiFetchJson returns parsed JSON for successful non-204 responses', async () => {
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ ok: true, value: 7 }))

    const { apiFetchJson } = await import(authModulePath)

    await expect(apiFetchJson('/api/json')).resolves.toEqual({ ok: true, value: 7 })
  })
})
