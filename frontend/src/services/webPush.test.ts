import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const webPushMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: webPushMocks.apiFetch,
}))

function responseOf(payload: unknown, ok = true) {
  return {
    ok,
    json: vi.fn(async () => payload),
  }
}

describe('webPush service', () => {
  let originalServiceWorker: unknown
  let originalIsSecureContext: boolean | undefined

  beforeEach(() => {
    vi.resetModules()
    webPushMocks.apiFetch.mockReset()
    originalServiceWorker = navigator.serviceWorker
    originalIsSecureContext = window.isSecureContext
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: {
            getSubscription: vi.fn(),
            subscribe: vi.fn(),
          },
        }),
      },
    })
    vi.stubGlobal('PushManager', class PushManager {})
  })

  afterEach(() => {
    Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: originalServiceWorker })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: originalIsSecureContext })
    vi.unstubAllGlobals()
  })

  it('does not request browser permission when the server reports Web Push disabled', async () => {
    const requestPermission = vi.fn(async () => 'granted')
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission,
    })
    webPushMocks.apiFetch.mockResolvedValueOnce(responseOf({ enabled: false, public_key: null }))

    const { promptAndEnableWebPushNotifications } = await import('./webPush')
    const result = await promptAndEnableWebPushNotifications()

    expect(result).toEqual({
      state: 'server-disabled',
      config: { enabled: false, public_key: null },
    })
    expect(webPushMocks.apiFetch).toHaveBeenCalledWith('/api/notifications/push/public-key')
    expect(requestPermission).not.toHaveBeenCalled()
  })

  it('fetches server config before requesting permission when Web Push is enabled', async () => {
    const requestPermission = vi.fn(async () => 'granted')
    const subscription = {
      endpoint: 'https://push.example/subscription/1',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/1',
        keys: {
          p256dh: 'p256dh-key',
          auth: 'auth-key',
        },
      }),
    }
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: {
            getSubscription: vi.fn(async () => subscription),
            subscribe: vi.fn(),
          },
        }),
      },
    })
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission,
    })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({ id: 1 }))

    const { promptAndEnableWebPushNotifications } = await import('./webPush')
    const result = await promptAndEnableWebPushNotifications()

    expect(result.state).toBe('subscribed')
    expect(webPushMocks.apiFetch).toHaveBeenNthCalledWith(1, '/api/notifications/push/public-key')
    expect(webPushMocks.apiFetch).toHaveBeenNthCalledWith(2, '/api/notifications/push/subscription', expect.objectContaining({
      method: 'POST',
    }))
    expect(webPushMocks.apiFetch.mock.invocationCallOrder[0]).toBeLessThan(
      requestPermission.mock.invocationCallOrder[0],
    )
  })
})
