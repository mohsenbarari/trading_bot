import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.fn()

vi.mock('./auth', () => ({
  apiFetch: apiFetchMock,
}))

describe('currentUser utils', () => {
  beforeEach(() => {
    vi.resetModules()
    apiFetchMock.mockReset()
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
    })
    expect(readCachedCurrentUserSummary()).toMatchObject({
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: '2026-05-21T12:00:00Z',
      is_accountant: true,
      is_customer: true,
      customer_tier: 'tier2',
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
    })
  })

  it('normalizes invalid cache values, clears state, and reuses cached summaries', async () => {
    const {
      cacheCurrentUserSummary,
      clearCurrentUserSummary,
      currentUserSummary,
      isAdminRole,
      primeCurrentUserSummary,
      readCachedCurrentUserSummary,
    } = await import('./currentUser')

    localStorage.setItem('current_user_summary', '{broken-json')
    expect(readCachedCurrentUserSummary()).toBeNull()

    expect(cacheCurrentUserSummary({ role: '' })).toBeNull()
    expect(localStorage.getItem('current_user_summary')).toBeNull()

    const normalized = cacheCurrentUserSummary({ id: '42', role: 'مدیر میانی', account_name: 99 })
    expect(normalized).toMatchObject({ id: 42, role: 'مدیر میانی', account_name: null })
    expect(isAdminRole(normalized?.role)).toBe(true)

    const cached = await primeCurrentUserSummary(false)
    expect(cached).toEqual(normalized)
    expect(apiFetchMock).not.toHaveBeenCalled()

    clearCurrentUserSummary()
    expect(currentUserSummary.value).toBeNull()
    expect(readCachedCurrentUserSummary()).toBeNull()
    expect(isAdminRole('عادی')).toBe(false)
    expect(isAdminRole(undefined)).toBe(false)
  })

  it('shares in-flight prime requests and keeps or clears cache on failure responses', async () => {
    let resolveProfile!: (response: unknown) => void
    apiFetchMock.mockReturnValueOnce(new Promise((resolve) => {
      resolveProfile = resolve
    }))

    const { cacheCurrentUserSummary, primeCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')
    const first = primeCurrentUserSummary(true)
    const second = primeCurrentUserSummary(true)
    resolveProfile({
      ok: true,
      json: async () => ({ id: 5, role: 'عادی', full_name: 'User Five' }),
    })

    await expect(first).resolves.toMatchObject({ id: 5, role: 'عادی' })
    await expect(second).resolves.toMatchObject({ id: 5, role: 'عادی' })
    expect(apiFetchMock).toHaveBeenCalledTimes(1)

    cacheCurrentUserSummary({ id: 6, role: 'عادی', account_name: 'kept' })
    apiFetchMock.mockResolvedValueOnce({ ok: false, status: 500 })
    await expect(primeCurrentUserSummary(true)).resolves.toMatchObject({ id: 6, account_name: 'kept' })
    expect(readCachedCurrentUserSummary()).toMatchObject({ id: 6 })

    apiFetchMock.mockResolvedValueOnce({ ok: false, status: 403 })
    await expect(primeCurrentUserSummary(true)).resolves.toBeNull()
    expect(readCachedCurrentUserSummary()).toBeNull()

    cacheCurrentUserSummary({ id: 7, role: 'عادی', account_name: 'fallback' })
    apiFetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(primeCurrentUserSummary(true)).resolves.toMatchObject({ id: 7, account_name: 'fallback' })
  })
})