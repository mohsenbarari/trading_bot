import { defineComponent, h, ref, type Ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const wsMocks = vi.hoisted(() => {
  const handlers = new Map<string, (payload: any) => void>()
  return {
    handlers,
    on: vi.fn((event: string, callback: (payload: any) => void) => {
      handlers.set(event, callback)
    }),
    off: vi.fn((event: string) => {
      handlers.delete(event)
    }),
  }
})

vi.mock('../useWebSocket', () => ({
  useWebSocket: () => ({
    on: wsMocks.on,
    off: wsMocks.off,
  }),
}))

import { useChatWebSocket, type UseChatWebSocketOptions } from './useChatWebSocket'

describe('useChatWebSocket', () => {
  let selectedUserId: Ref<number | null>
  let messageInput: Ref<string>
  let messages: Ref<any[]>
  let conversations: Ref<any[]>
  let isUserAtBottom: Ref<boolean>
  let apiFetchMock: ReturnType<typeof vi.fn>
  let loadConversationsMock: ReturnType<typeof vi.fn>
  let loadMessagesMock: ReturnType<typeof vi.fn>
  let scrollToBottomMock: ReturnType<typeof vi.fn>
  let markAsReadMock: ReturnType<typeof vi.fn>
  let wrapper: ReturnType<typeof mount> | null
  let exposed: ReturnType<typeof useChatWebSocket>

  function mountHarness(overrides: Partial<UseChatWebSocketOptions> = {}) {
    const Harness = defineComponent({
      setup() {
        exposed = useChatWebSocket({
          selectedUserId,
          messageInput,
          messages,
          conversations,
          apiFetch: apiFetchMock,
          loadConversations: loadConversationsMock,
          loadMessages: loadMessagesMock,
          scrollToBottom: scrollToBottomMock,
          markAsRead: markAsReadMock,
          isUserAtBottom,
          ...overrides,
        })
        return () => h('div')
      },
    })

    wrapper = mount(Harness)
  }

  function emit(event: string, payload: any) {
    const handler = wsMocks.handlers.get(event)
    if (!handler) {
      throw new Error(`Missing websocket handler for ${event}`)
    }
    handler(payload)
  }

  beforeEach(() => {
    vi.useFakeTimers()
    wsMocks.handlers.clear()
    wsMocks.on.mockClear()
    wsMocks.off.mockClear()

    selectedUserId = ref<number | null>(12)
    messageInput = ref('hello')
    messages = ref<any[]>([])
    conversations = ref<any[]>([])
    isUserAtBottom = ref(true)
    apiFetchMock = vi.fn(async () => ({}))
    loadConversationsMock = vi.fn(async () => {})
    loadMessagesMock = vi.fn(async () => {})
    scrollToBottomMock = vi.fn()
    markAsReadMock = vi.fn(async () => {})
    wrapper = null
    exposed = null as never
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
      wrapper = null
    }
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    wsMocks.handlers.clear()
  })

  it('registers websocket listeners on mount and removes them on unmount', () => {
    mountHarness()

    expect(wsMocks.on).toHaveBeenCalledTimes(5)
    expect(Array.from(wsMocks.handlers.keys()).sort()).toEqual([
      'chat:activity',
      'chat:message',
      'chat:reaction',
      'chat:read',
      'chat:typing',
    ])

    wrapper?.unmount()
    wrapper = null

    expect(wsMocks.off).toHaveBeenCalledTimes(5)
    expect(wsMocks.handlers.size).toBe(0)
  })

  it('sends throttled typing activity for direct and room conversations', async () => {
    mountHarness()

    await exposed.sendTypingSignal()
    await exposed.sendTypingSignal()

    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    expect(apiFetchMock).toHaveBeenLastCalledWith('/chat/activity', {
      method: 'POST',
      body: JSON.stringify({ activity: 'typing', active: true, receiver_id: 12 }),
    })

    selectedUserId.value = -44
    messageInput.value = 'room message'
    vi.advanceTimersByTime(2001)
    await exposed.sendTypingSignal()

    expect(apiFetchMock).toHaveBeenCalledTimes(2)
    expect(apiFetchMock).toHaveBeenLastCalledWith('/chat/rooms/44/activity', {
      method: 'POST',
      body: JSON.stringify({ activity: 'typing', active: true }),
    })
  })

  it('tracks typing and upload activity labels and clears timed-out direct typing state', () => {
    mountHarness()

    emit('chat:typing', { sender_id: 12, sender_name: 'Ali' })
    expect(exposed.isTyping.value).toBe(true)
    expect(exposed.activityTextByConversation.value[12]).toBe('در حال نوشتن...')

    emit('chat:activity', {
      room_kind: 'group',
      chat_id: 5,
      sender_id: 31,
      sender_name: 'Ali',
      activity: 'uploading_file',
      active: true,
    })
    emit('chat:activity', {
      room_kind: 'group',
      chat_id: 5,
      sender_id: 32,
      sender_name: 'Sara',
      activity: 'uploading_file',
      active: true,
    })

    expect(exposed.activityTextByConversation.value[-5]).toContain('نفر در حال ارسال فایل...')

    emit('chat:activity', {
      room_kind: 'group',
      chat_id: 5,
      sender_id: 31,
      activity: 'uploading_file',
      active: false,
    })
    expect(exposed.activityTextByConversation.value[-5]).toContain('Sara')

    vi.advanceTimersByTime(5000)
    expect(exposed.isTyping.value).toBe(false)
    expect(exposed.activityTextByConversation.value[12]).toBeUndefined()
  })

  it('covers fallback activity labels, repeated typing updates, and invalid activity payloads', () => {
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')
    mountHarness()

    emit('chat:activity', {
      room_kind: 'group',
      chat_id: 8,
      sender_id: 44,
      sender_name: '   ',
      activity: 'uploading_file',
      active: true,
    })
    expect(exposed.activityTextByConversation.value[-8]).toBe('کاربر در حال ارسال فایل...')

    emit('chat:typing', { sender_id: 12, sender_name: 'Ali' })
    emit('chat:typing', { sender_id: 12, sender_name: 'Ali' })
    expect(clearTimeoutSpy).toHaveBeenCalled()

    emit('chat:activity', { sender_id: 'bad-id', activity: 'typing' })
    expect(exposed.isTyping.value).toBe(true)

    clearTimeoutSpy.mockRestore()
  })

  it('appends incoming open-chat messages, updates previews, and debounces missing conversation reloads', async () => {
    conversations.value = [{ other_user_id: 12, unread_count: 0, last_message_at: null, last_message_type: null, last_message_content: null }]
    mountHarness()

    emit('chat:typing', { sender_id: 12, sender_name: 'Ali' })
    emit('chat:message', {
      id: 701,
      sender_id: 12,
      created_at: '2026-05-14T14:00:00Z',
      message_type: 'text',
      content: 'سلام',
    })
    await Promise.resolve()
    await Promise.resolve()

    expect(messages.value).toHaveLength(1)
    expect(messages.value?.map((message) => message.id)).toEqual([701])
    expect(scrollToBottomMock).toHaveBeenCalledTimes(1)
    expect(markAsReadMock).toHaveBeenCalledTimes(1)
    expect(conversations.value[0].last_message_content).toBe('سلام')
    expect(conversations.value[0].unread_count).toBe(0)
    expect(exposed.activityTextByConversation.value[12]).toBeUndefined()

    emit('chat:message', { sender_id: 99, message_type: 'text', content: 'new chat' })
    expect(loadConversationsMock).not.toHaveBeenCalled()

    await Promise.resolve()
    vi.advanceTimersByTime(400)
    await Promise.resolve()
    expect(loadConversationsMock).toHaveBeenCalledTimes(1)
  })

  it('increments unread counts for inactive conversations and handles deleted previews', async () => {
    conversations.value = [{ other_user_id: 45, unread_count: 1, last_message_at: null, last_message_type: null, last_message_content: null }]
    mountHarness()

    emit('chat:message', {
      id: 702,
      sender_id: 45,
      created_at: '2026-05-14T14:02:00Z',
      message_type: 'text',
      content: 'gone',
      is_deleted: true,
    })
    await Promise.resolve()

    expect(conversations.value[0].unread_count).toBe(2)
    expect(conversations.value[0].last_message_content).toBe('پیام حذف شد')
  })

  it('coalesces inactive conversation bursts into one conversation patch with accumulated unread count', async () => {
    conversations.value = [{ other_user_id: 45, unread_count: 1, last_message_at: null, last_message_type: null, last_message_content: null }]
    mountHarness()

    emit('chat:message', {
      id: 703,
      sender_id: 45,
      created_at: '2026-05-14T14:03:00Z',
      message_type: 'text',
      content: 'اول',
    })
    emit('chat:message', {
      id: 704,
      sender_id: 45,
      created_at: '2026-05-14T14:03:01Z',
      message_type: 'image',
      content: '',
    })
    await Promise.resolve()

    expect(conversations.value[0]).toMatchObject({
      unread_count: 3,
      last_message_at: '2026-05-14T14:03:01Z',
      last_message_type: 'image',
      last_message_content: 'تصویر',
    })
  })

  it('falls back to loadMessages for incomplete payloads and patches direct read receipts in place', async () => {
    messages.value = [
      { id: 1, receiver_id: 12, is_read: false },
      { id: 2, receiver_id: 50, is_read: false },
    ]
    mountHarness()

    emit('chat:message', {
      sender_id: 12,
      message_type: 'text',
      content: 'fallback payload',
    })
    await Promise.resolve()
    await Promise.resolve()

    expect(loadMessagesMock).toHaveBeenCalledWith(12, true)
    expect(markAsReadMock).toHaveBeenCalledTimes(1)

    emit('chat:read', { reader_id: 12 })
    await Promise.resolve()
    expect(messages.value[0].is_read).toBe(true)
    expect(messages.value[1].is_read).toBe(false)
  })

  it('clears room unread counts and normalizes reaction payloads', async () => {
    selectedUserId.value = -9
    messages.value = [{ id: 88, reactions: [] }]
    conversations.value = [{ other_user_id: -9, unread_count: 4 }]
    mountHarness()

    emit('chat:read', { room_kind: 'channel', chat_id: 9 })
    await Promise.resolve()
    expect(conversations.value[0].unread_count).toBe(0)

    emit('chat:reaction', {
      id: 88,
      reactions: [
        { emoji: '🔥', user_id: 5 },
        { emoji: '', user_id: 7 },
        { emoji: '👍', user_id: 'NaN' },
      ],
    })
    await Promise.resolve()

    expect(messages.value[0].reactions).toEqual([
      { emoji: '🔥', user_id: 5 },
    ])
  })

  it('covers typing failures and ignores invalid or unknown reaction events', async () => {
    const typingErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    apiFetchMock.mockRejectedValueOnce(new Error('typing failed'))
    mountHarness()

    await exposed.sendTypingSignal()
    expect(typingErrorSpy).toHaveBeenCalledWith('Typing signal failed', expect.any(Error))

    messages.value = [{ id: 91, reactions: [{ emoji: '🙂', user_id: 2 }] }]
    emit('chat:reaction', { foo: 'bar' })
    emit('chat:reaction', { id: 404, reactions: [{ emoji: '🔥', user_id: 1 }] })
    await Promise.resolve()
    expect(messages.value[0].reactions).toEqual([{ emoji: '🙂', user_id: 2 }])

    typingErrorSpy.mockRestore()
  })

  it('coalesces open-chat message bursts into one read+scroll follow-up', async () => {
    conversations.value = [{ other_user_id: 12, unread_count: 0, last_message_at: null, last_message_type: null, last_message_content: null }]
    mountHarness()

    emit('chat:message', {
      id: 801,
      sender_id: 12,
      created_at: '2026-05-14T14:00:00Z',
      message_type: 'text',
      content: 'اول',
    })
    emit('chat:message', {
      id: 802,
      sender_id: 12,
      created_at: '2026-05-14T14:00:01Z',
      message_type: 'text',
      content: 'دوم',
    })

    await Promise.resolve()
    await Promise.resolve()

    expect(messages.value.map((message) => message.id)).toEqual([801, 802])
    expect(markAsReadMock).toHaveBeenCalledTimes(1)
    expect(scrollToBottomMock).toHaveBeenCalledTimes(1)
  })

  it('clears active typing timeouts during unmount teardown', () => {
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')
    mountHarness()

    emit('chat:typing', { sender_id: 12, sender_name: 'Ali' })

    wrapper?.unmount()
    wrapper = null

    expect(clearTimeoutSpy).toHaveBeenCalled()
    clearTimeoutSpy.mockRestore()
  })
})
