import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

type ServiceWorkerListener = (event: Record<string, unknown>) => void

function createGateCacheStorage(values = new Map<string, string>()) {
  return {
    values,
    async open(cacheName: string) {
      return {
        async match(key: string) {
          const value = values.get(`${cacheName}:${key}`)
          return value === undefined ? undefined : new Response(value)
        },
        async put(key: string, response: Response) {
          values.set(`${cacheName}:${key}`, await response.text())
        },
      }
    },
  }
}

function loadServiceWorker(
  options: {
    subscription?: Record<string, unknown> | null
    fetchRequest?: ReturnType<typeof vi.fn>
    gateCacheStorage?: ReturnType<typeof createGateCacheStorage>
  } = {},
) {
  const listeners = new Map<string, ServiceWorkerListener>()
  const showNotification = vi.fn().mockResolvedValue(undefined)
  const openWindow = vi.fn().mockResolvedValue(undefined)
  const getSubscription = vi.fn().mockResolvedValue(options.subscription ?? null)
  const fetchRequest = options.fetchRequest ?? vi.fn().mockResolvedValue({ ok: true })
  const gateCacheStorage = options.gateCacheStorage ?? createGateCacheStorage()
  const scope = {
    location: { origin: 'https://app.example' },
    registration: { showNotification, pushManager: { getSubscription } },
    clients: {
      matchAll: vi.fn().mockResolvedValue([]),
      openWindow,
    },
    addEventListener: (name: string, listener: ServiceWorkerListener) => {
      listeners.set(name, listener)
    },
  }
  const source = readFileSync(resolve(process.cwd(), 'public/push-notifications-sw.js'), 'utf8')
  Function('self', 'fetch', 'caches', source)(scope, fetchRequest, gateCacheStorage)
  return {
    listeners,
    showNotification,
    openWindow,
    fetchRequest,
    getSubscription,
    gateCacheStorage,
    source,
  }
}

async function setDeliveryGate(listeners: Map<string, ServiceWorkerListener>, enabled: boolean) {
  let pending: Promise<unknown> = Promise.resolve()
  listeners.get('message')?.({
    data: { type: 'web-push:delivery-gate', enabled },
    waitUntil: (value: Promise<unknown>) => {
      pending = value
    },
  })
  await pending
}

describe('push notification service worker', () => {
  it('sanitizes hostile payload metadata, assets, and protocol-relative routes', async () => {
    const { listeners, showNotification } = loadServiceWorker()
    await setDeliveryGate(listeners, true)
    let pending: Promise<unknown> = Promise.resolve()
    listeners.get('push')?.({
      data: {
        json: () => ({
          title: 'route: /admin/system',
          body: 'backend=iran\rserver：api-01\u2028route=/admin\u2029مسیر＝/market\n📝 توضیحات: سالم',
          route: '//evil.example/collect',
          icon: 'https://tracker.example/icon.png',
          badge: 'https://tracker.example/badge.png',
          data: { route: '//evil.example/collect', secret: 'raw' },
        }),
      },
      waitUntil: (value: Promise<unknown>) => {
        pending = value
      },
    })
    await pending

    expect(showNotification).toHaveBeenCalledWith(
      'اعلان جدید',
      expect.objectContaining({
        body: '📝 توضیحات: سالم',
        icon: '/pwa-192x192.png',
        badge: '/pwa-192x192.png',
        data: { route: '/account/notifications' },
      }),
    )
    expect(JSON.stringify(showNotification.mock.calls)).not.toContain('evil.example')
    expect(JSON.stringify(showNotification.mock.calls)).not.toContain('tracker.example')
    expect(JSON.stringify(showNotification.mock.calls)).not.toContain('api-01')
  })

  it('opens only a same-origin canonical route on notification click', async () => {
    const { listeners, openWindow } = loadServiceWorker()
    let pending: Promise<unknown> = Promise.resolve()
    listeners.get('notificationclick')?.({
      notification: {
        data: { route: '//evil.example/collect' },
        close: vi.fn(),
      },
      waitUntil: (value: Promise<unknown>) => {
        pending = value
      },
    })
    await pending

    expect(openWindow).toHaveBeenCalledWith('https://app.example/account/notifications')
  })

  it('canonicalizes known destinations and never opens recovery or unknown internal routes', async () => {
    const { listeners, openWindow } = loadServiceWorker()
    const click = async (route: string) => {
      let pending: Promise<unknown> = Promise.resolve()
      listeners.get('notificationclick')?.({
        notification: { data: { route }, close: vi.fn() },
        waitUntil: (value: Promise<unknown>) => {
          pending = value
        },
      })
      await pending
    }

    await click('/market?token=raw#secret')
    await click('/chat?user_id=-7&user_name=Ali&token=raw')
    await click('/system-recovery?outcome=forbidden')
    await click('/unknown/internal')

    expect(openWindow.mock.calls.map(([url]) => url)).toEqual([
      'https://app.example/market',
      'https://app.example/chat?user_id=-7&user_name=Ali',
      'https://app.example/account/notifications',
      'https://app.example/account/notifications',
    ])
    expect(JSON.stringify(openWindow.mock.calls)).not.toContain('token')
    expect(JSON.stringify(openWindow.mock.calls)).not.toContain('system-recovery')
  })

  it.each([null, 7, 'raw', []])(
    'fails closed for a non-record JSON payload: %j',
    async (payload) => {
      const { listeners, showNotification } = loadServiceWorker()
      await setDeliveryGate(listeners, true)
      let pending: Promise<unknown> = Promise.resolve()
      listeners.get('push')?.({
        data: { json: () => payload },
        waitUntil: (value: Promise<unknown>) => {
          pending = value
        },
      })
      await pending

      expect(showNotification).toHaveBeenCalledWith(
        'اعلان جدید',
        expect.objectContaining({ data: { route: '/account/notifications' } }),
      )
    },
  )

  it('keeps delivery closed until the current account has an authoritative binding', async () => {
    const { listeners, showNotification } = loadServiceWorker()
    let pending: Promise<unknown> = Promise.resolve()
    listeners.get('push')?.({
      data: { json: () => ({ title: 'اعلان حساب قبلی', body: 'نباید نمایش داده شود' }) },
      waitUntil: (value: Promise<unknown>) => {
        pending = value
      },
    })
    await pending

    expect(showNotification).not.toHaveBeenCalled()
  })

  it('closes delivery and performs authenticated best-effort cleanup on logout', async () => {
    const endpoint = 'https://push.example/subscription/current-browser'
    const unsubscribe = vi.fn().mockResolvedValue(false)
    const fetchRequest = vi.fn().mockResolvedValue({ ok: false, status: 401 })
    const gateCacheStorage = createGateCacheStorage()
    const { listeners, showNotification } = loadServiceWorker({
      subscription: { endpoint, unsubscribe },
      fetchRequest,
      gateCacheStorage,
    })
    await setDeliveryGate(listeners, true)

    let cleanupPending: Promise<unknown> = Promise.resolve()
    listeners.get('message')?.({
      data: { type: 'web-push:cleanup-session', authToken: 'ephemeral-auth-token' },
      waitUntil: (value: Promise<unknown>) => {
        cleanupPending = value
      },
    })
    await cleanupPending

    expect(fetchRequest).toHaveBeenCalledWith(
      '/api/notifications/push/subscription',
      expect.objectContaining({
        method: 'DELETE',
        headers: expect.objectContaining({
          Authorization: 'Bearer ephemeral-auth-token',
        }),
        body: JSON.stringify({ endpoint }),
        credentials: 'same-origin',
        redirect: 'error',
      }),
    )
    expect(unsubscribe).toHaveBeenCalledTimes(1)
    expect([...gateCacheStorage.values.values()]).toEqual(['disabled'])
    expect(JSON.stringify([...gateCacheStorage.values.entries()])).not.toContain(endpoint)
    expect(JSON.stringify([...gateCacheStorage.values.entries()])).not.toContain(
      'ephemeral-auth-token',
    )

    let pushPending: Promise<unknown> = Promise.resolve()
    listeners.get('push')?.({
      data: { json: () => ({ title: 'اعلان قدیمی' }) },
      waitUntil: (value: Promise<unknown>) => {
        pushPending = value
      },
    })
    await pushPending
    expect(showNotification).not.toHaveBeenCalled()
  })

  it('persists only the delivery boolean across worker restarts', async () => {
    const gateCacheStorage = createGateCacheStorage()
    const firstWorker = loadServiceWorker({ gateCacheStorage })
    await setDeliveryGate(firstWorker.listeners, true)

    const restartedAuthorizedWorker = loadServiceWorker({ gateCacheStorage })
    let authorizedPush: Promise<unknown> = Promise.resolve()
    restartedAuthorizedWorker.listeners.get('push')?.({
      data: { json: () => ({ title: 'اعلان معتبر', body: 'متن امن' }) },
      waitUntil: (value: Promise<unknown>) => {
        authorizedPush = value
      },
    })
    await authorizedPush
    expect(restartedAuthorizedWorker.showNotification).toHaveBeenCalledTimes(1)

    await setDeliveryGate(restartedAuthorizedWorker.listeners, false)
    const restartedClosedWorker = loadServiceWorker({ gateCacheStorage })
    let closedPush: Promise<unknown> = Promise.resolve()
    restartedClosedWorker.listeners.get('push')?.({
      data: { json: () => ({ title: 'اعلان حساب قبلی' }) },
      waitUntil: (value: Promise<unknown>) => {
        closedPush = value
      },
    })
    await closedPush

    expect(restartedClosedWorker.showNotification).not.toHaveBeenCalled()
    expect([...gateCacheStorage.values.values()]).toEqual(['disabled'])
  })
})
