import { beforeEach, describe, expect, it, vi } from 'vitest'

const beforeEachSpy = vi.fn()
const onErrorSpy = vi.fn()
const createWebHistorySpy = vi.fn(() => ({ history: true }))
const authGuardMock = vi.fn()
const createRouterSpy = vi.fn(() => ({
  beforeEach: beforeEachSpy,
  onError: onErrorSpy,
}))

vi.mock('vue-router', () => ({
  createRouter: createRouterSpy,
  createWebHistory: createWebHistorySpy,
}))

vi.mock('../utils/auth', () => ({
  authGuard: authGuardMock,
}))

function mockLocation() {
  const location = { href: 'http://localhost/' }
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
    onErrorSpy.mockReset()
    createRouterSpy.mockClear()
    createWebHistorySpy.mockClear()
    authGuardMock.mockClear()
    mockLocation()
    vi.resetModules()
  })

  it('registers authGuard as the global beforeEach hook', async () => {
    await import('./index')

    expect(beforeEachSpy).toHaveBeenCalledTimes(1)
    expect(beforeEachSpy).toHaveBeenCalledWith(authGuardMock)
    expect(createWebHistorySpy).toHaveBeenCalledTimes(1)
  })

  it('registers heavy non-messenger workspace routes and remaining compatibility redirects', async () => {
    await import('./index')

    const options = createRouterSpy.mock.calls[0]?.[0] as any
    const routes = options.routes as Array<any>
    const routeByName = new Map(routes.map((route) => [route.name, route]))

    expect(routeByName.get('operations-customers')?.path).toBe('/operations/customers')
    expect(routeByName.get('operations-customers-detail')?.path).toBe('/operations/customers/:relationId')
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
    expect(routeByName.get('admin-system')?.meta).toEqual({ requiresAuth: true, requiresAdmin: true })
  })

  it('forces a hard reload for dynamic chunk load failures only', async () => {
    const location = mockLocation()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await import('./index')
    expect(onErrorSpy).toHaveBeenCalledTimes(1)

    const handler = onErrorSpy.mock.calls[0]?.[0]
    expect(handler).toBeTypeOf('function')

    handler?.(new Error('Failed to fetch dynamically imported module'), { fullPath: '/chat?user_id=7' })
    expect(location.href).toBe('/chat?user_id=7')
    expect(warnSpy).toHaveBeenCalledWith(
      'Chunk load failed in router, forcing a hard reload for:',
      '/chat?user_id=7',
    )

    location.href = 'http://localhost/'
    handler?.(new Error('ordinary failure'), { fullPath: '/market' })
    expect(location.href).toBe('http://localhost/')

    warnSpy.mockRestore()
  })
})
