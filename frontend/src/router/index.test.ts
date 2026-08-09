import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CHUNK_RELOAD_MARKER_KEY } from './chunkRecovery'
import { uiRouteContract } from './uiRouteContract'

const beforeEachSpy = vi.fn()
const afterEachSpy = vi.fn()
const onErrorSpy = vi.fn()
const replaceSpy = vi.fn(() => Promise.resolve())
const createWebHistorySpy = vi.fn(() => ({ history: true }))
const authGuardMock = vi.fn()
interface RouteUnderTest {
  name?: string
  path?: string
  component?: unknown
  redirect?: unknown
  meta?: Record<string, unknown>
}

interface RouterOptionsUnderTest {
  routes: RouteUnderTest[]
}

const createRouterSpy = vi.fn((_options: unknown) => ({
  beforeEach: beforeEachSpy,
  afterEach: afterEachSpy,
  onError: onErrorSpy,
  replace: replaceSpy,
}))

vi.mock('vue-router', () => ({
  createRouter: createRouterSpy,
  createWebHistory: createWebHistorySpy,
}))

vi.mock('../utils/auth', () => ({
  authGuard: authGuardMock,
}))

vi.mock('../views/LoginView.vue', () => ({
  default: { name: 'LoginViewStub' },
}))

function mockLocation() {
  const location = {
    href: 'http://localhost/',
    replace: vi.fn((path: string) => {
      location.href = path
    }),
  }
  Object.defineProperty(window, 'location', {
    value: location,
    configurable: true,
    writable: true,
  })
  return location
}

describe('router/index.ts', () => {
  beforeEach(() => {
    beforeEachSpy.mockReset()
    afterEachSpy.mockReset()
    onErrorSpy.mockReset()
    replaceSpy.mockClear()
    createRouterSpy.mockClear()
    createWebHistorySpy.mockClear()
    authGuardMock.mockClear()
    window.sessionStorage.clear()
    mockLocation()
    vi.resetModules()
  })

  it('registers authGuard as the global beforeEach hook', async () => {
    await import('./index')

    expect(beforeEachSpy).toHaveBeenCalledTimes(1)
    expect(beforeEachSpy).toHaveBeenCalledWith(authGuardMock)
    expect(createWebHistorySpy).toHaveBeenCalledTimes(1)
  }, 10_000)

  it('registers heavy non-messenger workspace routes and remaining compatibility redirects', async () => {
    await import('./index')

    const options = createRouterSpy.mock.calls[0]?.[0] as RouterOptionsUnderTest
    const routes = options.routes
    const routeByName = new Map(routes.map((route) => [route.name, route]))

    expect(routeByName.get('operations-customers')?.path).toBe('/operations/customers')
    expect(routeByName.get('operations-customers-detail')?.path).toBe(
      '/operations/customers/:relationId',
    )
    expect(routeByName.get('operations-accountants')?.path).toBe('/operations/accountants')
    expect(routeByName.get('account-security')?.path).toBe('/account/security')
    expect(routeByName.get('admin-channels')?.path).toBe('/admin/channels')
    expect(routeByName.get('admin-user-profile')?.path).toBe('/admin/users/:id')

    expect(routeByName.get('operations-customers')?.component).toBeTypeOf('function')
    expect(routeByName.get('operations-customers-detail')?.component).toBeTypeOf('function')
    expect(routeByName.get('operations-customers')?.redirect).toBeUndefined()
    expect(routeByName.get('operations-customers-detail')?.redirect).toBeUndefined()
    expect(routeByName.get('operations-accountants')?.component).toBeTypeOf('function')
    expect(routeByName.get('operations-accountants-detail')?.component).toBeTypeOf('function')
    expect(routeByName.get('operations-accountants')?.redirect).toBeUndefined()
    expect(routeByName.get('operations-accountants-detail')?.redirect).toBeUndefined()
    expect(routeByName.get('account-security')?.component).toBeTypeOf('function')
    expect(routeByName.get('account-storage')?.component).toBeTypeOf('function')
    expect(routeByName.get('account-notifications')?.component).toBeTypeOf('function')
    expect(routeByName.get('account-storage')?.redirect).toBeUndefined()
    expect(routeByName.get('admin-invitations')?.component).toBeTypeOf('function')
    expect(routeByName.get('admin-channels')?.component).toBeTypeOf('function')
    expect(routeByName.get('admin-users')?.component).toBeTypeOf('function')
    expect(routeByName.get('admin-user-profile')?.component).toBeTypeOf('function')
    expect(routeByName.get('admin-commodities')?.component).toBeTypeOf('function')
    expect(routeByName.get('admin-messages')?.component).toBeTypeOf('function')
    expect(routeByName.get('admin-system')?.component).toBeTypeOf('function')
    expect(routeByName.get('admin-user-profile')?.redirect).toBeUndefined()
    expect(routeByName.get('admin-system')?.meta).toMatchObject({
      requiresAuth: true,
      requiresAdmin: true,
    })
  })

  it('mirrors the Stage 3 route contract into metadata and keeps eager recovery last', async () => {
    await import('./index')

    const options = createRouterSpy.mock.calls[0]?.[0] as RouterOptionsUnderTest
    const routes = options.routes
    const routeByName = new Map(routes.map((route) => [route.name, route]))

    expect(routes).toHaveLength(30)
    expect(routes.at(-1)).toMatchObject({
      path: '/:pathMatch(.*)*',
      name: 'system-recovery',
    })
    expect(routes.at(-1)?.component).not.toBeTypeOf('function')

    for (const contract of uiRouteContract) {
      expect(routeByName.get(contract.name)?.meta).toMatchObject({
        uiShellClass: contract.shellClass,
        uiV2Scope: contract.v2Scope,
        uiRouteTestId: contract.testId,
      })
    }
  })

  it('performs one bounded path-only hard reload and keeps its shared marker through boot', async () => {
    const location = mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(123_456)

    await import('./index')
    expect(onErrorSpy).toHaveBeenCalledTimes(1)
    expect(afterEachSpy).not.toHaveBeenCalled()

    const handler = onErrorSpy.mock.calls[0]?.[0]
    expect(handler).toBeTypeOf('function')

    handler?.(new Error('Failed to fetch dynamically imported module'), {
      name: 'web-register',
      path: '/register',
      fullPath: '/register?registration_token=secret#continue',
    })
    expect(location.href).toBe('/register')
    expect(location.replace).toHaveBeenCalledWith('/register')
    expect(window.sessionStorage.getItem(CHUNK_RELOAD_MARKER_KEY)).toBe('123456')
    expect(warnSpy).toHaveBeenCalledWith('Chunk load failed; attempting one bounded hard reload')
    expect(replaceSpy).not.toHaveBeenCalled()

    // A successful initial navigation does not prove that the failed lazy
    // feature recovered. The marker must survive the reboot until its TTL.
    expect(window.sessionStorage.getItem(CHUNK_RELOAD_MARKER_KEY)).toBe('123456')

    nowSpy.mockRestore()
    warnSpy.mockRestore()
  })

  it('routes a repeated chunk failure to the eager deep-link recovery outcome', async () => {
    const location = mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await import('./index')
    const handler = onErrorSpy.mock.calls[0]?.[0]
    const target = {
      name: 'invite-landing',
      path: '/i/invite-code',
      fullPath: '/i/invite-code?registration_token=secret',
    }

    handler?.(new Error('Importing a module script failed'), target)
    location.href = 'http://localhost/'
    handler?.(new Error('Importing a module script failed'), target)

    expect(location.href).toBe('http://localhost/')
    expect(window.sessionStorage.getItem(CHUNK_RELOAD_MARKER_KEY)).not.toBeNull()
    expect(replaceSpy).toHaveBeenCalledTimes(1)
    expect(replaceSpy).toHaveBeenCalledWith({
      name: 'system-recovery',
      params: { pathMatch: ['__system', 'recovery'] },
      query: { outcome: 'deep-link-failure' },
    })
    expect(warnSpy).toHaveBeenLastCalledWith(
      'Chunk load failed after the bounded retry; opening system recovery',
    )

    warnSpy.mockRestore()
  })

  it('ignores ordinary router errors without reloading or opening recovery', async () => {
    const location = mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await import('./index')
    const handler = onErrorSpy.mock.calls[0]?.[0]
    handler?.(new Error('ordinary failure'), {
      name: 'market',
      path: '/market',
      fullPath: '/market',
    })

    expect(location.href).toBe('http://localhost/')
    expect(window.sessionStorage.length).toBe(0)
    expect(replaceSpy).not.toHaveBeenCalled()
    expect(warnSpy).not.toHaveBeenCalled()

    warnSpy.mockRestore()
  })

  it('never writes secret-bearing full paths to marker keys or router logs', async () => {
    mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const secret = 'raw-secret-token-123'

    await import('./index')
    const handler = onErrorSpy.mock.calls[0]?.[0]
    const target = {
      name: 'web-register',
      path: '/register',
      fullPath: `/register?registration_token=${secret}#otp`,
    }

    handler?.(new Error('Failed to fetch dynamically imported module'), target)

    const markerKeys = Array.from(
      { length: window.sessionStorage.length },
      (_, index) => window.sessionStorage.key(index) ?? '',
    )
    const serializedLogs = JSON.stringify(warnSpy.mock.calls)
    expect(location.href).toBe('/register')
    expect(markerKeys).toEqual([CHUNK_RELOAD_MARKER_KEY])
    expect(markerKeys.join('')).not.toContain(secret)
    expect(markerKeys.join('')).not.toMatch(/[?#]/)
    expect(serializedLogs).not.toContain(secret)
    expect(serializedLogs).not.toContain('registration_token')
    expect(location.replace).toHaveBeenCalledWith('/register')

    warnSpy.mockRestore()
  })

  it('uses replacement navigation when router recovery itself cannot open', async () => {
    const location = mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    window.sessionStorage.setItem(CHUNK_RELOAD_MARKER_KEY, String(Date.now()))
    replaceSpy.mockRejectedValueOnce(new Error('router recovery unavailable'))

    await import('./index')
    const handler = onErrorSpy.mock.calls[0]?.[0]
    handler?.(new Error('Failed to fetch dynamically imported module'), {
      name: 'web-register',
      path: '/register',
      fullPath: '/register?registration_token=raw-secret',
    })
    await Promise.resolve()
    await Promise.resolve()

    expect(location.replace).toHaveBeenCalledWith('/__system/recovery?outcome=deep-link-failure')
    expect(location.href).toBe('/__system/recovery?outcome=deep-link-failure')
    expect(JSON.stringify(location.replace.mock.calls)).not.toContain('raw-secret')

    warnSpy.mockRestore()
  })

  it('fails closed to eager recovery when the failed target has no safe path', async () => {
    const location = mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await import('./index')
    const handler = onErrorSpy.mock.calls[0]?.[0]

    handler?.(new Error('Failed to fetch dynamically imported module'), {
      name: 'web-register',
      path: '//evil.example/register',
      fullPath: '//evil.example/register?registration_token=secret',
    })

    expect(location.href).toBe('http://localhost/')
    expect(window.sessionStorage.length).toBe(0)
    expect(replaceSpy).toHaveBeenCalledWith({
      name: 'system-recovery',
      params: { pathMatch: ['__system', 'recovery'] },
      query: { outcome: 'deep-link-failure' },
    })
    expect(JSON.stringify(warnSpy.mock.calls)).not.toContain('secret')

    warnSpy.mockRestore()
  })
})
