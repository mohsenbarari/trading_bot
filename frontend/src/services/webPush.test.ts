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
    localStorage.clear()
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
    vi.useRealTimers()
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: originalServiceWorker,
    })
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: originalIsSecureContext,
    })
    vi.unstubAllGlobals()
    localStorage.clear()
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
    expect(webPushMocks.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/notifications/push/subscription',
      expect.objectContaining({
        method: 'POST',
      }),
    )
    expect(webPushMocks.apiFetch.mock.invocationCallOrder[0]).toBeLessThan(
      requestPermission.mock.invocationCallOrder[0],
    )
  })

  it('authoritatively rebinds an existing browser subscription for each authenticated account', async () => {
    const requestPermission = vi.fn()
    const postMessage = vi.fn()
    const subscription = {
      endpoint: 'https://push.example/subscription/shared-browser',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/shared-browser',
        keys: { p256dh: 'shared-key', auth: 'shared-auth' },
      }),
    }
    const getSubscription = vi.fn(async () => subscription)
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        controller: { postMessage },
        ready: Promise.resolve({
          pushManager: { getSubscription, subscribe: vi.fn() },
        }),
      },
    })
    vi.stubGlobal('Notification', {
      permission: 'granted',
      requestPermission,
    })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({ id: 1, enabled: true }))
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({ id: 2, enabled: true }))

    const { getWebPushStatus } = await import('./webPush')

    localStorage.setItem('auth_token', 'account-a-token')
    await expect(getWebPushStatus()).resolves.toMatchObject({ state: 'subscribed' })
    localStorage.setItem('auth_token', 'account-b-token')
    await expect(getWebPushStatus()).resolves.toMatchObject({ state: 'subscribed' })

    const registrationCalls = webPushMocks.apiFetch.mock.calls.filter(
      ([url]) => url === '/api/notifications/push/subscription',
    )
    expect(registrationCalls).toHaveLength(2)
    expect(registrationCalls[0]?.[1]).toMatchObject({ method: 'POST' })
    expect(registrationCalls[1]?.[1]).toMatchObject({ method: 'POST' })
    expect(getSubscription).toHaveBeenCalledTimes(2)
    expect(requestPermission).not.toHaveBeenCalled()
    expect(postMessage.mock.calls.map(([message]) => message)).toEqual([
      { type: 'web-push:delivery-gate', enabled: false },
      { type: 'web-push:delivery-gate', enabled: true },
      { type: 'web-push:delivery-gate', enabled: false },
      { type: 'web-push:delivery-gate', enabled: true },
    ])
  })

  it('deduplicates concurrent status reconciliation and allows a fresh check after settle', async () => {
    const subscription = {
      endpoint: 'https://push.example/subscription/single-flight',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/single-flight',
        keys: { p256dh: 'single-flight-key', auth: 'single-flight-auth' },
      }),
    }
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        controller: { postMessage: vi.fn() },
        ready: Promise.resolve({
          pushManager: {
            getSubscription: vi.fn(async () => subscription),
            subscribe: vi.fn(),
          },
        }),
      },
    })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })

    let resolveRegistration!: (response: ReturnType<typeof responseOf>) => void
    const registrationResponse = new Promise<ReturnType<typeof responseOf>>((resolve) => {
      resolveRegistration = resolve
    })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockReturnValueOnce(registrationResponse)

    const { getWebPushStatus } = await import('./webPush')
    localStorage.setItem('auth_token', 'single-flight-account-token')
    const first = getWebPushStatus()
    const concurrent = getWebPushStatus()

    expect(concurrent).toBe(first)
    resolveRegistration(responseOf({ id: 1, enabled: true }))
    await expect(first).resolves.toMatchObject({ state: 'subscribed' })
    await expect(concurrent).resolves.toMatchObject({ state: 'subscribed' })
    expect(webPushMocks.apiFetch).toHaveBeenCalledTimes(2)
    expect(webPushMocks.apiFetch).toHaveBeenNthCalledWith(1, '/api/notifications/push/public-key')
    expect(webPushMocks.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/notifications/push/subscription',
      expect.objectContaining({ method: 'POST' }),
    )

    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({ id: 1, enabled: true }))
    const refreshed = getWebPushStatus()

    expect(refreshed).not.toBe(first)
    await expect(refreshed).resolves.toMatchObject({ state: 'subscribed' })
    expect(webPushMocks.apiFetch).toHaveBeenCalledTimes(4)
  })

  it('queues a new account behind an in-flight rebind without reopening the stale session', async () => {
    const postMessage = vi.fn()
    const unsubscribe = vi.fn(async () => true)
    const subscription = {
      endpoint: 'https://push.example/subscription/account-switch',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/account-switch',
        keys: { p256dh: 'account-switch-key', auth: 'account-switch-auth' },
      }),
      unsubscribe,
    }
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        controller: { postMessage },
        ready: Promise.resolve({
          pushManager: {
            getSubscription: vi.fn(async () => subscription),
            subscribe: vi.fn(),
          },
        }),
      },
    })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })

    let resolveStaleRegistration!: (response: ReturnType<typeof responseOf>) => void
    const staleRegistration = new Promise<ReturnType<typeof responseOf>>((resolve) => {
      resolveStaleRegistration = resolve
    })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockReturnValueOnce(staleRegistration)

    const { getWebPushStatus } = await import('./webPush')
    localStorage.setItem('auth_token', 'account-a-token')
    const staleRequest = getWebPushStatus()
    await vi.waitFor(() => {
      expect(webPushMocks.apiFetch).toHaveBeenCalledTimes(2)
    })
    localStorage.setItem('auth_token', 'account-b-token')
    const currentRequest = getWebPushStatus()

    expect(currentRequest).not.toBe(staleRequest)
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({ id: 2, enabled: true }))
    resolveStaleRegistration(responseOf({ id: 1, enabled: true }))

    await expect(staleRequest).rejects.toThrow('push_session_changed')
    await expect(currentRequest).resolves.toMatchObject({ state: 'subscribed' })
    expect(unsubscribe).not.toHaveBeenCalled()
    expect(postMessage.mock.calls.map(([message]) => message)).toEqual([
      { type: 'web-push:delivery-gate', enabled: false },
      { type: 'web-push:delivery-gate', enabled: false },
      { type: 'web-push:delivery-gate', enabled: false },
      { type: 'web-push:delivery-gate', enabled: true },
    ])
  })

  it('closes delivery and removes the old local subscription when account rebind fails', async () => {
    let activeSubscription: Record<string, unknown> | null
    const postMessage = vi.fn()
    const unsubscribe = vi.fn(async () => {
      activeSubscription = null
      return true
    })
    const subscription = {
      endpoint: 'https://push.example/subscription/unbound',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/unbound',
        keys: { p256dh: 'unbound-key', auth: 'unbound-auth' },
      }),
      unsubscribe,
    }
    activeSubscription = subscription
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        controller: { postMessage },
        ready: Promise.resolve({
          pushManager: {
            getSubscription: vi.fn(async () => activeSubscription),
            subscribe: vi.fn(),
          },
        }),
      },
    })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({}, false))

    const { getWebPushStatus } = await import('./webPush')

    await expect(getWebPushStatus()).rejects.toThrow('push_subscription_rejected')
    expect(unsubscribe).toHaveBeenCalledTimes(1)
    expect(activeSubscription).toBeNull()
    expect(postMessage).toHaveBeenCalledTimes(1)
    expect(postMessage).toHaveBeenCalledWith({
      type: 'web-push:delivery-gate',
      enabled: false,
    })
  })

  it('bounds a Service Worker ready promise that never resolves', async () => {
    vi.useFakeTimers()
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { ready: new Promise(() => undefined) },
    })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })
    webPushMocks.apiFetch.mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))

    const { getWebPushStatus, WEB_PUSH_SERVICE_WORKER_READY_TIMEOUT_MS } = await import('./webPush')
    const statusPromise = getWebPushStatus()
    const timeoutExpectation = expect(statusPromise).rejects.toThrow('service_worker_ready_timeout')

    await vi.advanceTimersByTimeAsync(WEB_PUSH_SERVICE_WORKER_READY_TIMEOUT_MS)
    await timeoutExpectation
  })

  it('does not create a replacement when the original subscription refuses to unsubscribe', async () => {
    const originalUnsubscribe = vi.fn(async () => false)
    const subscribe = vi.fn()
    const original = {
      endpoint: 'https://push.example/subscription/original-stuck',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/original-stuck',
        keys: { p256dh: 'original-key', auth: 'original-auth' },
      }),
      unsubscribe: originalUnsubscribe,
    }
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: { getSubscription: vi.fn(async () => original), subscribe },
        }),
      },
    })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({}, false))

    const { promptAndEnableWebPushNotifications } = await import('./webPush')

    await expect(promptAndEnableWebPushNotifications()).rejects.toThrow('push_unsubscribe_failed')
    expect(originalUnsubscribe).toHaveBeenCalledTimes(1)
    expect(subscribe).not.toHaveBeenCalled()
  })

  it('does not report success when a failed replacement remains locally subscribed', async () => {
    let activeSubscription: Record<string, unknown> | null
    const originalUnsubscribe = vi.fn(async () => {
      activeSubscription = null
      return true
    })
    const replacementUnsubscribe = vi.fn(async () => false)
    const original = {
      endpoint: 'https://push.example/subscription/original-replaced',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/original-replaced',
        keys: { p256dh: 'original-key', auth: 'original-auth' },
      }),
      unsubscribe: originalUnsubscribe,
    }
    const replacement = {
      endpoint: 'https://push.example/subscription/replacement-stuck',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/replacement-stuck',
        keys: { p256dh: 'replacement-key', auth: 'replacement-auth' },
      }),
      unsubscribe: replacementUnsubscribe,
    }
    activeSubscription = original
    const pushManager = {
      getSubscription: vi.fn(async () => activeSubscription),
      subscribe: vi.fn(async () => {
        activeSubscription = replacement
        return replacement
      }),
    }
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { ready: Promise.resolve({ pushManager }) },
    })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({}, false))
      .mockResolvedValueOnce(responseOf({}, false))
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({}, false))

    const { getWebPushStatus, promptAndEnableWebPushNotifications } = await import('./webPush')

    await expect(promptAndEnableWebPushNotifications()).rejects.toThrow('push_unsubscribe_failed')
    expect(replacementUnsubscribe).toHaveBeenCalledTimes(1)
    await expect(getWebPushStatus()).rejects.toThrow('push_unsubscribe_failed')
    expect(replacementUnsubscribe).toHaveBeenCalledTimes(2)
  })

  it('does not complete disablement when the local subscription remains active', async () => {
    const unsubscribe = vi.fn(async () => false)
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: {
            getSubscription: vi.fn(async () => ({
              endpoint: 'https://push.example/subscription/stuck',
              unsubscribe,
            })),
          },
        }),
      },
    })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })

    const { disableWebPushNotifications } = await import('./webPush')

    await expect(disableWebPushNotifications()).rejects.toThrow('push_unsubscribe_failed')
    expect(webPushMocks.apiFetch).not.toHaveBeenCalled()
  })

  it('rolls back both local subscriptions when server registration fails twice', async () => {
    let activeSubscription: Record<string, unknown> | null
    const originalUnsubscribe = vi.fn(async () => {
      activeSubscription = null
      return true
    })
    const replacementUnsubscribe = vi.fn(async () => {
      activeSubscription = null
      return true
    })
    const original = {
      endpoint: 'https://push.example/subscription/original',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/original',
        keys: { p256dh: 'original-key', auth: 'original-auth' },
      }),
      unsubscribe: originalUnsubscribe,
    }
    const replacement = {
      endpoint: 'https://push.example/subscription/replacement',
      toJSON: () => ({
        endpoint: 'https://push.example/subscription/replacement',
        keys: { p256dh: 'replacement-key', auth: 'replacement-auth' },
      }),
      unsubscribe: replacementUnsubscribe,
    }
    activeSubscription = original
    const pushManager = {
      getSubscription: vi.fn(async () => activeSubscription),
      subscribe: vi.fn(async () => {
        activeSubscription = replacement
        return replacement
      }),
    }
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { ready: Promise.resolve({ pushManager }) },
    })
    vi.stubGlobal('Notification', {
      permission: 'granted',
      requestPermission: vi.fn(),
    })
    webPushMocks.apiFetch
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))
      .mockResolvedValueOnce(responseOf({}, false))
      .mockResolvedValueOnce(responseOf({}, false))
      .mockResolvedValueOnce(responseOf({ enabled: true, public_key: 'AQID' }))

    const { getWebPushStatus, promptAndEnableWebPushNotifications } = await import('./webPush')

    await expect(promptAndEnableWebPushNotifications()).rejects.toThrow(
      'push_subscription_rejected',
    )
    expect(originalUnsubscribe).toHaveBeenCalledTimes(1)
    expect(replacementUnsubscribe).toHaveBeenCalledTimes(1)
    await expect(getWebPushStatus()).resolves.toMatchObject({ state: 'unsubscribed' })
  })
})
