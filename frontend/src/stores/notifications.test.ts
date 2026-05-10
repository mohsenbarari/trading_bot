import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const apiFetchMock = vi.fn()
const playNotificationSoundMock = vi.fn()

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

vi.mock('../utils/audio', () => ({
  playNotificationSound: playNotificationSoundMock,
}))

function makeResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('notification store', () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    setActivePinia(createPinia())
    apiFetchMock.mockReset()
    playNotificationSoundMock.mockReset()
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    consoleErrorSpy.mockRestore()
  })

  it('fetchHistory preserves realtime notifications that arrive during the request', async () => {
    const { useNotificationStore } = await import('./notifications')
    const store = useNotificationStore()
    const pendingFetch = deferred<ReturnType<typeof makeResponse>>()

    apiFetchMock.mockReturnValueOnce(pendingFetch.promise)
    const historyPromise = store.fetchHistory()

    store.addAppNotification({
      id: 99,
      message: 'live update',
      category: 'user',
      client_received_at: Date.now() + 100,
    })

    pendingFetch.resolve(makeResponse([
      { id: 1, message: 'history item', category: 'system', is_read: false },
    ]))

    await historyPromise
    expect(store.appNotifications.map((notification) => notification.id)).toEqual([99, 1])
  })

  it('clearAllNotifications restores prior notifications while keeping concurrent realtime items on failure', async () => {
    const { useNotificationStore } = await import('./notifications')
    const store = useNotificationStore()
    store.addAppNotification({ id: 2, message: 'second', category: 'system' })
    store.addAppNotification({ id: 1, message: 'first', category: 'system' })

    const pendingDelete = deferred<never>()
    apiFetchMock.mockReturnValueOnce(pendingDelete.promise)

    const clearPromise = store.clearAllNotifications()
    expect(store.appNotifications).toEqual([])

    store.addAppNotification({ id: 77, message: 'realtime', category: 'user' })
    pendingDelete.reject(new Error('delete failed'))
    await clearPromise

    expect(store.appNotifications.map((notification) => notification.id)).toEqual([77, 1, 2])
  })

  it('deleteNotification restores the removed notification in order when the API call fails', async () => {
    const { useNotificationStore } = await import('./notifications')
    const store = useNotificationStore()
    store.addAppNotification({ id: 3, message: 'third', category: 'system' })
    store.addAppNotification({ id: 2, message: 'second', category: 'system' })
    store.addAppNotification({ id: 1, message: 'first', category: 'system' })

    apiFetchMock.mockRejectedValueOnce(new Error('delete failed'))
    await store.deleteNotification(2)

    expect(store.appNotifications.map((notification) => notification.id)).toEqual([1, 2, 3])
  })

  it('addToast plays a sound and auto-removes the toast after the lifetime', async () => {
    vi.useFakeTimers()
    const { useNotificationStore } = await import('./notifications')
    const store = useNotificationStore()

    store.addToast({ title: 'hello', body: 'world' })
    expect(store.activeToasts).toHaveLength(1)
    expect(playNotificationSoundMock).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(5000)
    expect(store.activeToasts).toHaveLength(0)
  })

  it('tracks unique unread conversations and clears them for direct chats and rooms', async () => {
    const { useNotificationStore } = await import('./notifications')
    const store = useNotificationStore()

    store.incrementChatUnread(5)
    store.incrementChatUnread(5)
    store.incrementChatUnread(-77)
    expect(store.chatUnreadCount).toBe(2)

    store.markChatAsRead(5)
    expect(store.chatUnreadCount).toBe(1)

    store.markChatAsRead(-77)
    expect(store.chatUnreadCount).toBe(0)

    store.setChatUnreadCount(0)
    expect(store.chatUnreadCount).toBe(0)
  })

  it('syncs and mutates muted conversation ids from poll/list state', async () => {
    const { useNotificationStore } = await import('./notifications')
    const store = useNotificationStore()

    store.syncMutedConversationIds([5, -77, -77, 0, null])
    expect(store.mutedConversationIds).toEqual([5, -77])
    expect(store.isConversationMuted(-77)).toBe(true)

    store.setConversationMuted(9, true)
    expect(store.isConversationMuted(9)).toBe(true)

    store.setConversationMuted(-77, false)
    expect(store.isConversationMuted(-77)).toBe(false)
  })

  it('fetchInitialCounts syncs both unread and muted conversation ids from poll', async () => {
    const { useNotificationStore } = await import('./notifications')
    const store = useNotificationStore()

    localStorage.setItem('auth_token', 'token')
    apiFetchMock.mockResolvedValueOnce(makeResponse({
      unread_chats_count: 2,
      conversations_with_unread: [{ user_id: 5 }, { user_id: -22 }],
      muted_conversation_ids: [-22, 9],
    }))

    await store.fetchInitialCounts()

    expect(store.chatUnreadCount).toBe(2)
    expect(store.unreadChatUserIds).toEqual([5, -22])
    expect(store.mutedConversationIds).toEqual([-22, 9])
  })
})