import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AUTH_INTENDED_ROUTE_MAX_AGE_MS,
  AUTH_INTENDED_ROUTE_STORAGE_KEY,
  SYSTEM_RECOVERY_OUTCOME,
  consumeIntendedRoute,
  forbiddenSystemRecoveryLocation,
  readIntendedRoute,
  storeIntendedRoute,
  unavailableSystemRecoveryLocation,
  validateIntendedRoute,
} from './authNavigation'

describe('auth navigation policy', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('stores and consumes one same-origin internal route with a non-sensitive query', () => {
    expect(
      storeIntendedRoute(
        {
          name: 'messenger',
          path: '/chat',
          fullPath: '/chat?user_id=7',
        },
        sessionStorage,
        1_000,
      ),
    ).toBe(true)

    expect(readIntendedRoute(sessionStorage, 1_001)).toBe('/chat?user_id=7')
    expect(consumeIntendedRoute(sessionStorage, 1_001)).toBe('/chat?user_id=7')
    expect(sessionStorage.getItem(AUTH_INTENDED_ROUTE_STORAGE_KEY)).toBeNull()
  })

  it.each([
    'https://evil.example/market',
    'http://evil.example/market',
    '//evil.example/market',
    '/\\evil.example/market',
  ])('rejects external and protocol-relative return target %s', (fullPath) => {
    expect(validateIntendedRoute({ fullPath })).toBeNull()
  })

  it.each([
    { name: 'login', fullPath: '/login' },
    { name: 'invite-landing', fullPath: '/i/abc' },
    { name: 'web-register', fullPath: '/register' },
    { name: 'system-recovery', fullPath: '/missing' },
    { fullPath: '/LOGIN/' },
    { fullPath: '/i/abc' },
    { fullPath: '/__system/recovery?outcome=deep-link-failure' },
  ])('rejects public auth and recovery-loop target $fullPath', (candidate) => {
    expect(validateIntendedRoute(candidate)).toBeNull()
  })

  it.each([
    '/chat?registration_token=REG-secret',
    '/profile?otp_code=12345',
    '/profile?code=12345',
    '/account?session_id=secret',
    '/settings?signed_url=secret',
    '/settings?next=https%3A%2F%2Fevil.example',
    '/profile?next=%2Fregister%3Fregistration_token%3DREG-secret',
    '/profile?next=%252Fregister%253Ftoken%253DINV-secret',
    '/profile?next=%2Faccount%3Fsession_id%3Dsecret',
    '/operations#access_token=secret',
  ])('rejects secret-bearing query or fragment %s', (fullPath) => {
    expect(validateIntendedRoute({ fullPath })).toBeNull()
  })

  it('rejects forbidden recovery outcomes so a consumed return cannot loop', () => {
    expect(
      validateIntendedRoute({
        fullPath: '/missing?outcome=forbidden',
      }),
    ).toBeNull()
    expect(storeIntendedRoute({ fullPath: '/missing?outcome=forbidden' }, sessionStorage)).toBe(
      false,
    )
  })

  it('removes malformed, future, and expired storage records', () => {
    sessionStorage.setItem(AUTH_INTENDED_ROUTE_STORAGE_KEY, '{not-json')
    expect(readIntendedRoute(sessionStorage, 2_000)).toBeNull()
    expect(sessionStorage.getItem(AUTH_INTENDED_ROUTE_STORAGE_KEY)).toBeNull()

    sessionStorage.setItem(
      AUTH_INTENDED_ROUTE_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        path: '/profile',
        createdAt: 2_001,
      }),
    )
    expect(readIntendedRoute(sessionStorage, 2_000)).toBeNull()

    storeIntendedRoute({ fullPath: '/profile' }, sessionStorage, 2_000)
    expect(readIntendedRoute(sessionStorage, 2_000 + AUTH_INTENDED_ROUTE_MAX_AGE_MS + 1)).toBeNull()
    expect(sessionStorage.getItem(AUTH_INTENDED_ROUTE_STORAGE_KEY)).toBeNull()
  })

  it('fails closed when sessionStorage cannot persist the route', () => {
    const storage = {
      getItem: vi.fn(() => null),
      setItem: vi.fn(() => {
        throw new Error('storage denied')
      }),
      removeItem: vi.fn(),
    } as unknown as Storage

    expect(storeIntendedRoute({ fullPath: '/profile' }, storage)).toBe(false)
    expect(storage.removeItem).toHaveBeenCalledWith(AUTH_INTENDED_ROUTE_STORAGE_KEY)
  })

  it('uses a non-secret forbidden outcome without disclosing the denied target', () => {
    const location = forbiddenSystemRecoveryLocation()
    expect(location).toEqual({
      name: 'system-recovery',
      params: { pathMatch: ['__system', 'recovery'] },
      query: { outcome: SYSTEM_RECOVERY_OUTCOME.FORBIDDEN },
      replace: true,
    })
    expect(JSON.stringify(location)).not.toMatch(/\/admin|\/market|target|return/i)
  })

  it('uses the cause-neutral deep-link outcome for unavailable access checks', () => {
    const location = unavailableSystemRecoveryLocation()
    expect(location).toEqual({
      name: 'system-recovery',
      params: { pathMatch: ['__system', 'recovery'] },
      query: { outcome: SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE },
      replace: true,
    })
    expect(JSON.stringify(location)).not.toMatch(/\/admin|\/market|target|return|network/i)
  })
})
