import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useNotificationRuntime } from './useNotificationRuntime'
import { WS_NOTIFICATION_EVENTS } from '../types/notifications'
import { BROWSER_NOTIFICATION_CLICK_EVENT } from '../utils/browserNotifications'

const notificationRuntimeMocks = vi.hoisted(() => ({
  route: null as any,
  currentRoute: null as any,
  push: vi.fn(),
  store: {
    addAppNotification: vi.fn(),
    addToast: vi.fn(),
    isConversationMuted: vi.fn(),
    incrementChatUnread: vi.fn(),
    fetchInitialCounts: vi.fn(),
  },
  requestNotificationPermission: vi.fn(),
  showBrowserNotification: vi.fn(),
  unlockAudioContext: vi.fn(),
  handlers: new Map<string, Array<(payload?: any) => void>>(),
  connect: vi.fn(),
  on: vi.fn((event: string, callback: (payload?: any) => void) => {
    const current = notificationRuntimeMocks.handlers.get(event) ?? []
    current.push(callback)
    notificationRuntimeMocks.handlers.set(event, current)
  }),
  off: vi.fn(),
  ensureSessionValidation: vi.fn(),
}))

vi.mock('vue-router', async () => {
  const vue = await import('vue')
  notificationRuntimeMocks.route = vue.reactive({
    path: '/dashboard',
    fullPath: '/dashboard',
    query: {},
  })
  notificationRuntimeMocks.currentRoute = vue.ref({ fullPath: '/dashboard' })

  return {
    useRoute: () => notificationRuntimeMocks.route,
    useRouter: () => ({
      push: notificationRuntimeMocks.push,
      currentRoute: notificationRuntimeMocks.currentRoute,
    }),
  }
})

vi.mock('../stores/notifications', () => ({
  useNotificationStore: () => notificationRuntimeMocks.store,
}))

vi.mock('../utils/browserNotifications', () => ({
  BROWSER_NOTIFICATION_CLICK_EVENT: 'browser-notification-click',
  requestNotificationPermission: notificationRuntimeMocks.requestNotificationPermission,
  showBrowserNotification: notificationRuntimeMocks.showBrowserNotification,
}))

vi.mock('../utils/audio', () => ({
  unlockAudioContext: notificationRuntimeMocks.unlockAudioContext,
}))

function emitWsEvent(event: string, payload?: any) {
  for (const handler of notificationRuntimeMocks.handlers.get(event) ?? []) {
    handler(payload)
  }
}

function setRoute(path: string, fullPath = path, query: Record<string, any> = {}) {
  notificationRuntimeMocks.route.path = path
  notificationRuntimeMocks.route.fullPath = fullPath
  notificationRuntimeMocks.route.query = query
  notificationRuntimeMocks.currentRoute.value = { fullPath }
}

function setDocumentHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    value: hidden,
  })
}

function mountRuntime() {
  const Harness = defineComponent({
    setup() {
      useNotificationRuntime({
        connect: notificationRuntimeMocks.connect,
        on: notificationRuntimeMocks.on,
        off: notificationRuntimeMocks.off,
        ensureSessionValidation: notificationRuntimeMocks.ensureSessionValidation,
      })
      return () => h('div')
    },
  })

  return mount(Harness)
}

describe('useNotificationRuntime', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'token-1')
    notificationRuntimeMocks.handlers.clear()
    notificationRuntimeMocks.push.mockReset()
    notificationRuntimeMocks.connect.mockReset()
    notificationRuntimeMocks.on.mockClear()
    notificationRuntimeMocks.off.mockClear()
    notificationRuntimeMocks.ensureSessionValidation.mockReset()
    notificationRuntimeMocks.requestNotificationPermission.mockReset()
    notificationRuntimeMocks.showBrowserNotification.mockReset()
    notificationRuntimeMocks.unlockAudioContext.mockReset()
    notificationRuntimeMocks.store.addAppNotification.mockReset()
    notificationRuntimeMocks.store.addToast.mockReset()
    notificationRuntimeMocks.store.isConversationMuted.mockReset()
    notificationRuntimeMocks.store.incrementChatUnread.mockReset()
    notificationRuntimeMocks.store.fetchInitialCounts.mockReset()
    notificationRuntimeMocks.store.addAppNotification.mockReturnValue({
      title: 'اعلان جدید',
      body: 'متن اعلان',
      level: 'INFO',
      category: 'SYSTEM',
    })
    notificationRuntimeMocks.store.isConversationMuted.mockReturnValue(false)
    setRoute('/dashboard')
    setDocumentHidden(false)
  })

  it('bootstraps authenticated runtime, handles first interaction and browser click routing, and cleans up on unmount', async () => {
    const removeWindowSpy = vi.spyOn(window, 'removeEventListener')
    const wrapper = mountRuntime()

    expect(notificationRuntimeMocks.connect).toHaveBeenCalledTimes(1)
    expect(notificationRuntimeMocks.store.fetchInitialCounts).toHaveBeenCalledTimes(1)
    expect(notificationRuntimeMocks.ensureSessionValidation).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new Event('click'))
    window.dispatchEvent(new Event('touchstart'))
    expect(notificationRuntimeMocks.requestNotificationPermission).toHaveBeenCalledTimes(1)
    expect(notificationRuntimeMocks.unlockAudioContext).toHaveBeenCalledTimes(1)
    expect(removeWindowSpy).toHaveBeenCalledWith('click', expect.any(Function))
    expect(removeWindowSpy).toHaveBeenCalledWith('touchstart', expect.any(Function))

    emitWsEvent(WS_NOTIFICATION_EVENTS.wsReconnect)
    emitWsEvent(WS_NOTIFICATION_EVENTS.sessionRevoked)
    expect(notificationRuntimeMocks.store.fetchInitialCounts).toHaveBeenCalledTimes(2)
    expect(notificationRuntimeMocks.ensureSessionValidation).toHaveBeenCalledTimes(2)

    setRoute('/market', '/market')
    await nextTick()
    expect(notificationRuntimeMocks.connect).toHaveBeenCalledTimes(2)
    expect(notificationRuntimeMocks.store.fetchInitialCounts).toHaveBeenCalledTimes(2)

    window.dispatchEvent(new CustomEvent(BROWSER_NOTIFICATION_CLICK_EVENT, {
      detail: { route: '/notifications' },
    }))
    expect(notificationRuntimeMocks.push).toHaveBeenCalledWith('/notifications')

    setRoute('/notifications', '/notifications')
    window.dispatchEvent(new CustomEvent(BROWSER_NOTIFICATION_CLICK_EVENT, {
      detail: { route: '/notifications' },
    }))
    expect(notificationRuntimeMocks.push).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    expect(notificationRuntimeMocks.off).toHaveBeenCalledWith(WS_NOTIFICATION_EVENTS.sessionRevoked, expect.any(Function))
    expect(notificationRuntimeMocks.off).toHaveBeenCalledWith(WS_NOTIFICATION_EVENTS.wsReconnect, expect.any(Function))
    expect(notificationRuntimeMocks.off).toHaveBeenCalledWith(WS_NOTIFICATION_EVENTS.appMessage, expect.any(Function))
    expect(notificationRuntimeMocks.off).toHaveBeenCalledWith(WS_NOTIFICATION_EVENTS.chatMessage, expect.any(Function))
  })

  it('normalizes app notifications into toasts and browser notifications, but suppresses toast rendering in the notification center', async () => {
    const wrapper = mountRuntime()
    setDocumentHidden(true)

    emitWsEvent(WS_NOTIFICATION_EVENTS.appMessage, { id: 'n1', message: 'payload' })
    await flushPromises()

    expect(notificationRuntimeMocks.store.addAppNotification).toHaveBeenCalledWith({ id: 'n1', message: 'payload' })
    expect(notificationRuntimeMocks.store.addToast).toHaveBeenCalledWith({
      title: 'اعلان جدید',
      body: 'متن اعلان',
      route: '/notifications',
      kind: 'app',
      level: 'INFO',
      category: 'SYSTEM',
    })
    expect(notificationRuntimeMocks.showBrowserNotification).toHaveBeenCalledWith('اعلان جدید', 'متن اعلان', {
      route: '/notifications',
    })

    setRoute('/notifications', '/notifications')
    emitWsEvent(WS_NOTIFICATION_EVENTS.appMessage, { id: 'n2' })
    await flushPromises()
    expect(notificationRuntimeMocks.store.addAppNotification).toHaveBeenCalledTimes(2)
    expect(notificationRuntimeMocks.store.addToast).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('handles chat notifications for open, direct, muted, and channel conversations correctly', async () => {
    const wrapper = mountRuntime()

    setRoute('/chat', '/chat?user_id=42', { user_id: '42' })
    emitWsEvent(WS_NOTIFICATION_EVENTS.chatMessage, {
      sender_id: 42,
      sender_name: 'علی',
      message_type: 'text',
      content: 'سلام',
    })
    await flushPromises()
    expect(notificationRuntimeMocks.store.incrementChatUnread).not.toHaveBeenCalled()
    expect(notificationRuntimeMocks.store.addToast).not.toHaveBeenCalled()

    setRoute('/dashboard')
    setDocumentHidden(true)
    emitWsEvent(WS_NOTIFICATION_EVENTS.chatMessage, {
      sender_id: 42,
      sender_name: 'علی',
      message_type: 'image',
      content: '',
    })
    await flushPromises()
    expect(notificationRuntimeMocks.store.incrementChatUnread).toHaveBeenCalledWith(42)
    expect(notificationRuntimeMocks.store.addToast).toHaveBeenCalledWith({
      title: 'علی',
      body: 'تصویر',
      route: '/chat?user_id=42&user_name=%D8%B9%D9%84%DB%8C',
      kind: 'chat',
    })
    expect(notificationRuntimeMocks.showBrowserNotification).toHaveBeenCalledWith('علی', 'تصویر', {
      route: '/chat?user_id=42&user_name=%D8%B9%D9%84%DB%8C',
    })

    notificationRuntimeMocks.store.isConversationMuted.mockImplementation((conversationKey: number) => conversationKey === 43)
    emitWsEvent(WS_NOTIFICATION_EVENTS.chatMessage, {
      sender_id: 43,
      sender_name: 'رضا',
      message_type: 'text',
      content: 'بی‌صدا',
    })
    await flushPromises()
    expect(notificationRuntimeMocks.store.incrementChatUnread).toHaveBeenCalledWith(43)
    expect(notificationRuntimeMocks.store.addToast).toHaveBeenCalledTimes(1)

    emitWsEvent(WS_NOTIFICATION_EVENTS.chatMessage, {
      room_kind: 'channel',
      chat_id: 77,
      conversation_title: 'اطلاع‌رسانی',
      sender_id: 99,
      sender_name: 'ادمین',
      message_type: 'video',
      content: '',
    })
    await flushPromises()
    expect(notificationRuntimeMocks.store.incrementChatUnread).toHaveBeenCalledWith(-77)
    expect(notificationRuntimeMocks.store.addToast).toHaveBeenLastCalledWith({
      title: 'اطلاع‌رسانی',
      body: 'ویدئو',
      route: '/chat?user_id=-77&user_name=%D8%A7%D8%B7%D9%84%D8%A7%D8%B9%E2%80%8C%D8%B1%D8%B3%D8%A7%D9%86%DB%8C',
      kind: 'chat',
    })
    expect(notificationRuntimeMocks.showBrowserNotification).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })
})
