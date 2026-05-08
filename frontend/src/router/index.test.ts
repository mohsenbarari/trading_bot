import { beforeEach, describe, expect, it, vi } from 'vitest'

const beforeEachSpy = vi.fn()
const onErrorSpy = vi.fn()
const createWebHistorySpy = vi.fn(() => ({ history: true }))
const authGuardMock = vi.fn()

vi.mock('vue-router', () => ({
  createRouter: vi.fn(() => ({
    beforeEach: beforeEachSpy,
    onError: onErrorSpy,
  })),
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