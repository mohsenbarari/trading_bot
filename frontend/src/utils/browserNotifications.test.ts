import { afterEach, describe, expect, it, vi } from 'vitest'

const notificationDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'Notification')

type MockNotificationInstance = {
  title: string
  options: NotificationOptions
  onclick: (() => void) | null
  close: ReturnType<typeof vi.fn>
}

class MockNotification {
  static permission: NotificationPermission = 'default'
  static requestPermission = vi.fn<() => Promise<NotificationPermission>>(async () => 'granted')
  static shouldThrow = false
  static instances: MockNotificationInstance[] = []

  onclick: (() => void) | null = null
  close = vi.fn()
  title: string
  options: NotificationOptions

  constructor(title: string, options: NotificationOptions = {}) {
    if (MockNotification.shouldThrow) {
      throw new Error('notification constructor failed')
    }
    this.title = title
    this.options = options
    MockNotification.instances.push(this)
  }

  static reset() {
    MockNotification.permission = 'default'
    MockNotification.requestPermission.mockReset()
    MockNotification.requestPermission.mockResolvedValue('granted')
    MockNotification.shouldThrow = false
    MockNotification.instances = []
  }
}

function restoreNotification() {
  if (notificationDescriptor) {
    Object.defineProperty(globalThis, 'Notification', notificationDescriptor)
    return
  }
  Reflect.deleteProperty(globalThis, 'Notification')
}

afterEach(() => {
  vi.restoreAllMocks()
  MockNotification.reset()
  restoreNotification()
})

describe('browserNotifications', () => {
  it('requests permission only when notifications are supported and not already decided', async () => {
    const notifications = await import('./browserNotifications')

    restoreNotification()
    await expect(notifications.requestNotificationPermission()).resolves.toBe(false)

    Object.defineProperty(globalThis, 'Notification', {
      configurable: true,
      value: MockNotification,
    })

    MockNotification.permission = 'granted'
    await expect(notifications.requestNotificationPermission()).resolves.toBe(true)
    expect(MockNotification.requestPermission).not.toHaveBeenCalled()

    MockNotification.permission = 'default'
    MockNotification.requestPermission.mockResolvedValueOnce('granted')
    await expect(notifications.requestNotificationPermission()).resolves.toBe(true)
    expect(MockNotification.requestPermission).toHaveBeenCalledTimes(1)

    MockNotification.permission = 'denied'
    await expect(notifications.requestNotificationPermission()).resolves.toBe(false)
  })

  it('returns false when notifications cannot be shown', async () => {
    const notifications = await import('./browserNotifications')

    restoreNotification()
    expect(notifications.showBrowserNotification('Title', 'Body')).toBe(false)

    Object.defineProperty(globalThis, 'Notification', {
      configurable: true,
      value: MockNotification,
    })
    MockNotification.permission = 'default'

    expect(notifications.showBrowserNotification('Title', 'Body')).toBe(false)
  })

  it('shows routed notifications, truncates long bodies, and dispatches click routing', async () => {
    Object.defineProperty(globalThis, 'Notification', {
      configurable: true,
      value: MockNotification,
    })
    MockNotification.permission = 'granted'
    const focusSpy = vi.spyOn(window, 'focus').mockImplementation(() => undefined)
    const notifications = await import('./browserNotifications')
    const clickHandler = vi.fn()
    window.addEventListener(notifications.BROWSER_NOTIFICATION_CLICK_EVENT, clickHandler as EventListener)

    const body = 'a'.repeat(350)
    expect(notifications.showBrowserNotification('Messenger', body, { route: '/chat?user_id=7' })).toBe(true)

    expect(MockNotification.instances).toHaveLength(1)
    const created = MockNotification.instances[0]!
    expect(created.title).toBe('Messenger')
    expect(created.options.body).toHaveLength(300)
    expect(created.options.body?.endsWith('...')).toBe(true)
    expect(created.options.icon).toBe('/pwa-192x192.png')
    expect((created.options as any).vibrate).toEqual([200, 100, 200])

    created.onclick?.()

    expect(focusSpy).toHaveBeenCalledTimes(1)
    expect(created.close).toHaveBeenCalledTimes(1)
    expect(clickHandler).toHaveBeenCalledTimes(1)
    expect((clickHandler.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({ route: '/chat?user_id=7' })

    window.removeEventListener(notifications.BROWSER_NOTIFICATION_CLICK_EVENT, clickHandler as EventListener)
  })

  it('returns false when the Notification constructor throws', async () => {
    Object.defineProperty(globalThis, 'Notification', {
      configurable: true,
      value: MockNotification,
    })
    MockNotification.permission = 'granted'
    MockNotification.shouldThrow = true

    const notifications = await import('./browserNotifications')
    expect(notifications.showBrowserNotification('Messenger', 'Body')).toBe(false)
  })
})