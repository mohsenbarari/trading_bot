import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type FetchMock = ReturnType<typeof vi.fn>

const authModulePath = './auth'

function makeJwt(exp: number): string {
  const payload = Buffer.from(JSON.stringify({ exp })).toString('base64url')
  return `header.${payload}.sig`
}

function mockLocation() {
  const location = { href: 'http://localhost/' }
  Object.defineProperty(window, 'location', {
    value: location,
    configurable: true,
    writable: true,
  })
  return location
}

describe('auth utils', () => {
  let fetchMock: FetchMock

  beforeEach(() => {
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

  it('isAuthenticated returns true for a still-valid access token', async () => {
    const futureExp = Math.floor(Date.now() / 1000) + 3600
    localStorage.setItem('auth_token', makeJwt(futureExp))
    const { isAuthenticated } = await import(authModulePath)

    await expect(isAuthenticated()).resolves.toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('isAuthenticated returns true on network refresh errors but false on auth errors', async () => {
    const expiredExp = Math.floor(Date.now() / 1000) - 60
    localStorage.setItem('auth_token', makeJwt(expiredExp))
    localStorage.setItem('refresh_token', 'refresh')

    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const { isAuthenticated } = await import(authModulePath)
    await expect(isAuthenticated()).resolves.toBe(true)

    localStorage.setItem('auth_token', makeJwt(expiredExp))
    localStorage.setItem('refresh_token', 'refresh')
    fetchMock.mockResolvedValueOnce({ ok: false, status: 401 })
    await expect(isAuthenticated()).resolves.toBe(false)
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
    await authGuard({ path: '/admin', meta: { requiresAdmin: true } } as any, {} as any, next)
    expect(next).toHaveBeenLastCalledWith()
  })
})