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

  it('cacheCurrentUserSummary persists the accountant flag', async () => {
    const { cacheCurrentUserSummary, readCachedCurrentUserSummary } = await import('./currentUser')

    const result = cacheCurrentUserSummary({
      id: 15,
      role: 'عادی',
      full_name: 'علی',
      account_name: 'ali',
      is_accountant: true,
    })

    expect(result).toMatchObject({
      id: 15,
      role: 'عادی',
      full_name: 'علی',
      account_name: 'ali',
      is_accountant: true,
    })
    expect(readCachedCurrentUserSummary()).toMatchObject({
      is_accountant: true,
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
        is_accountant: false,
      }),
    })

    const { primeCurrentUserSummary } = await import('./currentUser')
    const result = await primeCurrentUserSummary(true)

    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(result).toMatchObject({
      id: 9,
      role: 'مدیر ارشد',
      is_accountant: false,
    })
  })
})