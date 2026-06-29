import { effectScope, nextTick, ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MAX_STICKERS_PER_MESSAGE } from '../../utils/emojiStickerCatalog'

const messageMocks = vi.hoisted(() => ({
  apiFetchJson: vi.fn(),
  syncMutedConversationIds: vi.fn(),
  markChatAsRead: vi.fn(),
  getPendingForUser: vi.fn(() => []),
  buildOptimisticMessageFromUpload: vi.fn((upload: any) => upload.message),
  waitForChatUploadBackgroundReady: vi.fn(async () => {}),
  hydrateFromCache: vi.fn(async () => null),
  replaceFromServer: vi.fn(async (_userId: number, conversations: any[]) => conversations),
  setConversationStoreError: vi.fn(),
  conversationHydrationStatus: 'fresh',
}))

vi.mock('../../utils/auth', () => ({
  apiFetchJson: messageMocks.apiFetchJson,
}))

vi.mock('../../stores/notifications', () => ({
  useNotificationStore: () => ({
    syncMutedConversationIds: messageMocks.syncMutedConversationIds,
    markChatAsRead: messageMocks.markChatAsRead,
  }),
}))

vi.mock('../../stores/chat/conversations', () => ({
  useConversationsStore: () => ({
    hydrateFromCache: messageMocks.hydrateFromCache,
    replaceFromServer: messageMocks.replaceFromServer,
    setError: messageMocks.setConversationStoreError,
    get hydrationStatus() {
      return messageMocks.conversationHydrationStatus
    },
  }),
}))

vi.mock('../../services/chatUploadBackground', () => ({
  getPendingForUser: messageMocks.getPendingForUser,
  buildOptimisticMessageFromUpload: messageMocks.buildOptimisticMessageFromUpload,
  waitForChatUploadBackgroundReady: messageMocks.waitForChatUploadBackgroundReady,
}))

import { useChatMessages } from './useChatMessages'

describe('useChatMessages', () => {
  let scope: ReturnType<typeof effectScope> | null
  let selectedUserId: ReturnType<typeof ref<number | null>>
  let messages: ReturnType<typeof ref<any[]>>
  let conversations: ReturnType<typeof ref<any[]>>
  let error: ReturnType<typeof ref<string>>
  let messagePanelError: ReturnType<typeof ref<string>>
  let isLoadingMessages: ReturnType<typeof ref<boolean>>
  let isSending: ReturnType<typeof ref<boolean>>
  let unreadNewMessagesCount: ReturnType<typeof ref<number>>
  let isUserAtBottom: ReturnType<typeof ref<boolean>>
  let isViewingReply: ReturnType<typeof ref<boolean>>
  let isInitialChatOpenSettling: ReturnType<typeof ref<boolean>>
  let targetUserStatus: ReturnType<typeof ref<string>>
  let selectedUserName: ReturnType<typeof ref<string>>
  let messageInput: ReturnType<typeof ref<string>>
  let editingMessage: ReturnType<typeof ref<any | null>>
  let replyingToMessage: ReturnType<typeof ref<any | null>>
  let swipedMessageId: ReturnType<typeof ref<number | null>>
  let isMobile: ReturnType<typeof ref<boolean>>
  let showStickerPicker: ReturnType<typeof ref<boolean>>
  let scrollToBottomMock: ReturnType<typeof vi.fn>
  let scrollToUnreadOrBottomMock: ReturnType<typeof vi.fn>
  let scrollToMessageMock: ReturnType<typeof vi.fn>
  let forceScrollToBottomMock: ReturnType<typeof vi.fn>
  let focusMessageInputMock: ReturnType<typeof vi.fn>
  let adjustTextareaHeightMock: ReturnType<typeof vi.fn>
  let subject: ReturnType<typeof useChatMessages>
  let alertSpy: ReturnType<typeof vi.spyOn>
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  function createSubject(options: { withMessagePanelError?: boolean } = {}) {
    scope = effectScope()
    scope.run(() => {
      subject = useChatMessages({
        apiBaseUrl: '',
        jwtToken: 'jwt',
        currentUserId: 7,
        selectedUserId: selectedUserId as any,
        messages: messages as any,
        conversations: conversations as any,
        error: error as any,
        ...(options.withMessagePanelError ? { messagePanelError: messagePanelError as any } : {}),
        isLoadingMessages: isLoadingMessages as any,
        isSending: isSending as any,
        unreadNewMessagesCount: unreadNewMessagesCount as any,
        isUserAtBottom: isUserAtBottom as any,
        isViewingReply: isViewingReply as any,
        isInitialChatOpenSettling: isInitialChatOpenSettling as any,
        targetUserStatus: targetUserStatus as any,
        selectedUserName: selectedUserName as any,
        messageInput: messageInput as any,
        editingMessage,
        replyingToMessage,
        swipedMessageId: swipedMessageId as any,
        isMobile: isMobile as any,
        showStickerPicker: showStickerPicker as any,
        scrollToBottom: scrollToBottomMock,
        scrollToUnreadOrBottom: scrollToUnreadOrBottomMock,
        scrollToMessage: scrollToMessageMock,
        forceScrollToBottom: forceScrollToBottomMock,
        focusMessageInput: focusMessageInputMock,
        adjustTextareaHeight: adjustTextareaHeightMock,
      })
    })
  }

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-14T14:00:00Z'))
    messageMocks.apiFetchJson.mockReset()
    messageMocks.syncMutedConversationIds.mockReset()
    messageMocks.markChatAsRead.mockReset()
    messageMocks.getPendingForUser.mockReset()
    messageMocks.getPendingForUser.mockReturnValue([])
    messageMocks.buildOptimisticMessageFromUpload.mockClear()
    messageMocks.waitForChatUploadBackgroundReady.mockClear()
    messageMocks.hydrateFromCache.mockReset()
    messageMocks.hydrateFromCache.mockResolvedValue(null)
    messageMocks.replaceFromServer.mockReset()
    messageMocks.replaceFromServer.mockImplementation(async (_userId: number, nextConversations: any[]) => nextConversations)
    messageMocks.setConversationStoreError.mockReset()
    messageMocks.conversationHydrationStatus = 'fresh'
    window.sessionStorage.clear()

    selectedUserId = ref<number | null>(12)
    messages = ref<any[]>([])
    conversations = ref<any[]>([])
    error = ref('')
    messagePanelError = ref('')
    isLoadingMessages = ref(false)
    isSending = ref(false)
    unreadNewMessagesCount = ref(0)
    isUserAtBottom = ref(true)
    isViewingReply = ref(false)
    isInitialChatOpenSettling = ref(false)
    targetUserStatus = ref('')
    selectedUserName = ref('مخاطب')
    messageInput = ref('')
    editingMessage = ref<any | null>(null)
    replyingToMessage = ref<any | null>(null)
    swipedMessageId = ref<number | null>(null)
    isMobile = ref(false)
    showStickerPicker = ref(false)
    scrollToBottomMock = vi.fn()
    scrollToUnreadOrBottomMock = vi.fn()
    scrollToMessageMock = vi.fn()
    forceScrollToBottomMock = vi.fn()
    focusMessageInputMock = vi.fn()
    adjustTextareaHeightMock = vi.fn()
    subject = null as never
    scope = null
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined)
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  })

  afterEach(() => {
    scope?.stop()
    alertSpy.mockRestore()
    consoleErrorSpy.mockRestore()
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    vi.unstubAllEnvs()
  })

  it('loads conversations, syncs muted ids, and stores fetch errors', async () => {
    createSubject()
    messageMocks.apiFetchJson.mockResolvedValueOnce([
      { other_user_id: 12, is_muted: true },
      { other_user_id: -5, is_muted: false },
    ])

    await subject.loadConversations()

    expect(conversations.value).toHaveLength(2)
    expect(messageMocks.syncMutedConversationIds).not.toHaveBeenCalled()
    vi.runOnlyPendingTimers()
    expect(messageMocks.syncMutedConversationIds).toHaveBeenCalledWith([12])

    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('load failed'))
    await subject.loadConversations()
    expect(error.value).toBe('دریافت گفتگوها ممکن نشد.')
  })

  it('loads chat messages, merges pending optimistic uploads, and marks the chat as read', async () => {
    createSubject()
    messageMocks.getPendingForUser.mockReturnValue(
      [
        {
          message: {
            id: -99,
            sender_id: 7,
            receiver_id: 12,
            content: 'pending',
            message_type: 'image',
          },
        },
      ] as any
    )
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return [
          {
            id: 1,
            sender_id: 12,
            receiver_id: 7,
            content: 'سلام',
            message_type: 'text',
            is_read: true,
            created_at: '2026-05-14T13:59:00Z',
          },
        ]
      }
      if (url === '/api/chat/read/12') {
        return {}
      }
      throw new Error(`unexpected url ${url}`)
    })

    await subject.loadMessages(12)
    await flushPromises()

    expect(messageMocks.waitForChatUploadBackgroundReady).toHaveBeenCalledTimes(1)
    expect(messages.value!.map((message) => message.id)).toEqual([1, -99])
    expect(scrollToUnreadOrBottomMock).not.toHaveBeenCalled()
    vi.advanceTimersByTime(0)
    await flushPromises()
    expect(scrollToUnreadOrBottomMock).not.toHaveBeenCalled()
    expect(isInitialChatOpenSettling.value).toBe(true)
    expect(scrollToBottomMock).toHaveBeenCalledTimes(1)
    expect(messageMocks.markChatAsRead).toHaveBeenCalledWith(12)
    vi.advanceTimersByTime(1400)
    expect(isInitialChatOpenSettling.value).toBe(false)
    expect(isLoadingMessages.value).toBe(false)
  })

  it('does not re-add a pending document upload already represented by a server message', async () => {
    createSubject()
    const documentPayload = {
      file_name: 'resume.txt',
      mime_type: 'text/plain',
      size: 34,
    }
    messageMocks.getPendingForUser.mockReturnValue(
      [
        {
          message: {
            id: -101,
            sender_id: 7,
            receiver_id: 12,
            content: JSON.stringify({ placeholder: true, ...documentPayload }),
            message_type: 'document',
          },
        },
      ] as any
    )
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return [
          {
            id: 51,
            sender_id: 7,
            receiver_id: 12,
            content: JSON.stringify({ file_id: 'file-51', ...documentPayload }),
            message_type: 'document',
            is_read: true,
            created_at: '2026-05-14T13:59:00Z',
          },
        ]
      }
      if (url === '/api/chat/read/12') {
        return {}
      }
      throw new Error(`unexpected url ${url}`)
    })

    await subject.loadMessages(12)
    await flushPromises()
    vi.advanceTimersByTime(0)
    await flushPromises()

    expect(messages.value!.map((message) => message.id)).toEqual([51])
    expect(messages.value![0]?.is_sending).not.toBe(true)
  })

  it('opens at the first valid unread message from the peer before marking the direct chat read', async () => {
    createSubject()
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return [
          {
            id: 20,
            sender_id: 7,
            receiver_id: 12,
            content: 'mine',
            message_type: 'text',
            is_read: false,
            created_at: '2026-05-14T13:58:00Z',
          },
          {
            id: 21,
            sender_id: 12,
            receiver_id: 7,
            content: 'unread 1',
            message_type: 'text',
            is_read: false,
            created_at: '2026-05-14T13:59:00Z',
          },
          {
            id: 22,
            sender_id: 12,
            receiver_id: 7,
            content: 'unread 2',
            message_type: 'text',
            is_read: false,
            created_at: '2026-05-14T14:00:00Z',
          },
        ]
      }
      if (url === '/api/chat/read/12') {
        return {}
      }
      throw new Error(`unexpected url ${url}`)
    })

    await subject.loadMessages(12)
    vi.advanceTimersByTime(0)
    await flushPromises()

    expect(scrollToMessageMock).toHaveBeenCalledWith(21)
    expect(scrollToUnreadOrBottomMock).not.toHaveBeenCalled()
    expect(scrollToBottomMock).not.toHaveBeenCalled()
    expect(isInitialChatOpenSettling.value).toBe(false)
    expect(messageMocks.markChatAsRead).toHaveBeenCalledWith(12)
  })

  it('preserves the initial unread anchor across immediate read-state reloads', async () => {
    createSubject()
    let loadCount = 0
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        loadCount += 1
        const isRead = loadCount > 1
        return [
          {
            id: 21,
            sender_id: 12,
            receiver_id: 7,
            content: 'first unread',
            message_type: 'text',
            is_read: isRead,
            created_at: '2026-05-14T14:00:00Z',
          },
          {
            id: 22,
            sender_id: 12,
            receiver_id: 7,
            content: 'second unread',
            message_type: 'text',
            is_read: isRead,
            created_at: '2026-05-14T14:00:01Z',
          },
        ]
      }
      if (url === '/api/chat/read/12') {
        return {}
      }
      throw new Error(`unexpected url ${url}`)
    })

    await subject.loadMessages(12)
    vi.advanceTimersByTime(0)
    await flushPromises()

    expect(scrollToMessageMock).toHaveBeenCalledWith(21)
    expect(scrollToUnreadOrBottomMock).not.toHaveBeenCalled()

    scrollToBottomMock.mockClear()
    await subject.loadMessages(12)
    vi.advanceTimersByTime(0)
    await flushPromises()

    expect(scrollToMessageMock).toHaveBeenCalledWith(21)
    expect(scrollToBottomMock).not.toHaveBeenCalled()
  })

  it('preserves the initial unread anchor across an immediate remount', async () => {
    createSubject()
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return [
          {
            id: 31,
            sender_id: 12,
            receiver_id: 7,
            content: 'first unread',
            message_type: 'text',
            is_read: false,
            created_at: '2026-05-14T14:00:00Z',
          },
        ]
      }
      if (url === '/api/chat/read/12') return {}
      throw new Error(`unexpected url ${url}`)
    })

    await subject.loadMessages(12)
    vi.advanceTimersByTime(0)
    await flushPromises()

    scope?.stop()
    messages.value = []
    scrollToMessageMock.mockClear()
    scrollToBottomMock.mockClear()
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return [
          {
            id: 31,
            sender_id: 12,
            receiver_id: 7,
            content: 'first unread',
            message_type: 'text',
            is_read: true,
            created_at: '2026-05-14T14:00:00Z',
          },
        ]
      }
      if (url === '/api/chat/read/12') return {}
      throw new Error(`unexpected url ${url}`)
    })

    createSubject()
    await subject.loadMessages(12)
    vi.advanceTimersByTime(0)
    await flushPromises()

    expect(scrollToMessageMock).toHaveBeenCalledWith(31)
    expect(scrollToBottomMock).not.toHaveBeenCalled()
  })

  it('expands the initial load limit to include all known unread messages from a conversation', async () => {
    createSubject()
    conversations.value = [{ other_user_id: 12, unread_count: 30 }]
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return [
          {
            id: 1,
            sender_id: 12,
            receiver_id: 7,
            content: 'first unread',
            message_type: 'text',
            is_read: false,
          },
        ]
      }
      if (url === '/api/chat/read/12') {
        return {}
      }
      throw new Error(`unexpected url ${url}`)
    })

    await subject.loadMessages(12)
    vi.advanceTimersByTime(0)
    await flushPromises()

    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/chat\/messages\/12\?limit=38&/),
      {},
      expect.any(Object),
    )
    expect(scrollToMessageMock).toHaveBeenCalledWith(1)
    expect(scrollToBottomMock).not.toHaveBeenCalled()
  })

  it('requests the virtual timeline open limit only when the stage E flag is enabled', async () => {
    createSubject()
    conversations.value = [{ other_user_id: 12, unread_count: 0 }]
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/chat/read/12') return {}
      return []
    })

    await subject.loadMessages(12)
    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/chat\/messages\/12\?limit=16&/),
      {},
      expect.any(Object),
    )

    scope?.stop()
    messages.value = []
    vi.stubEnv('VITE_MESSENGER_VIRTUAL_TIMELINE', 'true')
    createSubject()
    conversations.value = [{ other_user_id: 12, unread_count: 0 }]
    messageMocks.apiFetchJson.mockClear()
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/chat/read/12') return {}
      return []
    })

    await subject.loadMessages(12)
    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/chat\/messages\/12\?limit=128&/),
      {},
      expect.any(Object),
    )
  })

  it('uses the full initial load limit for route-open chats before conversation state is loaded', async () => {
    createSubject()
    conversations.value = []
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/chat/read/12') return {}
      return []
    })

    await subject.loadMessages(12)

    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/chat\/messages\/12\?limit=48&/),
      {},
      expect.any(Object),
    )
  })

  it('uses the full initial load limit when conversation state is still cache-hydrated', async () => {
    createSubject()
    messageMocks.conversationHydrationStatus = 'cached'
    conversations.value = [{ other_user_id: 12, unread_count: 0 }]
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/chat/read/12') return {}
      return []
    })

    await subject.loadMessages(12)

    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/chat\/messages\/12\?limit=48&/),
      {},
      expect.any(Object),
    )
  })

  it('reuses the snapshot warm path before the fresh server load resolves', async () => {
    createSubject()
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return [
          {
            id: 10,
            sender_id: 12,
            receiver_id: 7,
            content: 'cached',
            message_type: 'text',
            created_at: '2026-05-14T13:00:00Z',
            reactions: [{ emoji: '🔥', user_id: 12 }],
            reply_to_message: { id: 5, sender_id: 7, content: 'reply', message_type: 'text' },
          },
        ]
      }
      if (url === '/api/chat/read/12') {
        return {}
      }
      throw new Error(`unexpected url ${url}`)
    })

    await subject.loadMessages(12)
    await flushPromises()

    selectedUserId.value = 99
    await nextTick()

    let resolveMessages: ((value: any[]) => void) | null = null
    messageMocks.apiFetchJson.mockImplementation((url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return new Promise((resolve) => {
          resolveMessages = resolve
        })
      }
      if (url === '/api/chat/read/12') {
        return Promise.resolve({})
      }
      throw new Error(`unexpected url ${url}`)
    })

    selectedUserId.value = 12
    const pendingLoad = subject.loadMessages(12)
    await nextTick()

    expect(messages.value?.map((message) => message.id)).toEqual([10])
    const cachedMessage = messages.value?.[0]
    expect(cachedMessage).toBeDefined()
    expect(cachedMessage).not.toBe(cachedMessage?.reply_to_message)
    expect(isLoadingMessages.value).toBe(false)

      const cb = resolveMessages as ((msgs: any) => void) | null
      cb?.([
      {
        id: 10,
        sender_id: 12,
        receiver_id: 7,
        content: 'cached',
        message_type: 'text',
        created_at: '2026-05-14T13:00:00Z',
        reactions: [{ emoji: '🔥', user_id: 12 }],
        reply_to_message: { id: 5, sender_id: 7, content: 'reply', message_type: 'text' },
      },
      {
        id: 11,
        sender_id: 12,
        receiver_id: 7,
        content: 'fresh',
        message_type: 'text',
        created_at: '2026-05-14T14:00:00Z',
      },
    ])

    await pendingLoad
    await flushPromises()
      expect(messages.value?.map((message) => message.id)).toEqual([10, 11])
  })

  it('evicts the oldest cached snapshot after enough chat switches and ignores empty snapshots', async () => {
    createSubject()

    for (let offset = 0; offset < 13; offset += 1) {
      const userId = 100 + offset
      messages.value = offset === 0
        ? [{ id: -offset - 1, sender_id: 7, receiver_id: userId, content: 'pending only', message_type: 'text' }]
        : [{ id: offset + 1, sender_id: userId, receiver_id: 7, content: `msg-${offset}`, message_type: 'text' }]
      selectedUserId.value = userId
      await nextTick()
    }

    selectedUserId.value = 999
    await nextTick()

    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/100?limit=')) return []
      if (url.startsWith('/api/chat/messages/101?limit=')) {
        return [{ id: 500, sender_id: 101, receiver_id: 7, content: 'fresh snapshot miss', message_type: 'text' }]
      }
      if (url === '/api/chat/read/100' || url === '/api/chat/read/101') return {}
      throw new Error(`unexpected url ${url}`)
    })

    selectedUserId.value = 100
    await subject.loadMessages(100)
    expect(messages.value).toEqual([])

    selectedUserId.value = 101
    await subject.loadMessages(101)
    expect(messages.value?.map((message) => message.id)).toEqual([500])
  })

  it('loads older messages with dedupe and stops pagination when the server returns no more rows', async () => {
    messages.value = [
      { id: 10, sender_id: 12, receiver_id: 7, content: 'current', message_type: 'text' },
      { id: 11, sender_id: 7, receiver_id: 12, content: 'tail', message_type: 'text' },
    ]
    createSubject()

    messageMocks.apiFetchJson.mockResolvedValueOnce([
      { id: 8, sender_id: 12, receiver_id: 7, content: 'older 1', message_type: 'text' },
      { id: 9, sender_id: 12, receiver_id: 7, content: 'older 2', message_type: 'text' },
      { id: 10, sender_id: 12, receiver_id: 7, content: 'current', message_type: 'text' },
    ])

    const appended = await subject.loadOlderMessages(12)
    expect(appended).toBe(2)
    expect(messages.value.map((message) => message.id)).toEqual([8, 9, 10, 11])
    expect(subject.hasOlderMessages.value).toBe(false)

    messageMocks.apiFetchJson.mockResolvedValueOnce([])
    const secondAppend = await subject.loadOlderMessages(12)
    expect(secondAppend).toBe(0)
    expect(subject.hasOlderMessages.value).toBe(false)
  })

  it('treats non-array older-message payloads as exhausted pagination', async () => {
    createSubject()
    messages.value = [{ id: 10, sender_id: 12, receiver_id: 7, content: 'current', message_type: 'text' }]

    messageMocks.apiFetchJson.mockResolvedValueOnce({ rows: [] })
    await expect(subject.loadOlderMessages(12)).resolves.toBe(0)
    expect(subject.hasOlderMessages.value).toBe(false)
  })

  it('sends media, replaces optimistic placeholders, updates previews, and surfaces send errors', async () => {
    createSubject()
    showStickerPicker.value = true
    messages.value = [{ id: -5, content: 'temp', message_type: 'image' }]
    conversations.value = [{ other_user_id: 12, unread_count: 5 }]

    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/chat/send') {
        return {
          id: 25,
          sender_id: 7,
          receiver_id: 12,
          content: 'file-1',
          message_type: 'image',
          created_at: '2026-05-14T14:00:00Z',
        }
      }
      if (url === '/api/chat/conversations') {
        return conversations.value
      }
      throw new Error(`unexpected url ${url}`)
    })

    const sent = await subject.sendMediaMessage('image', 'file-1', 'blob://preview', -5)

    expect(sent?.id).toBe(25)
    expect(messages.value[0]).toMatchObject({ id: 25, local_blob_url: 'blob://preview' })
    expect(conversations.value[0]).toMatchObject({
      unread_count: 0,
      last_message_type: 'image',
      last_message_content: 'تصویر',
    })
    expect(showStickerPicker.value).toBe(false)
    expect(scrollToBottomMock).toHaveBeenCalled()

    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('media failed'))
    const failed = await subject.sendMediaMessage('video', 'file-2')
    expect(failed).toBeNull()
    expect(alertSpy).toHaveBeenCalledWith('media failed')
  })

  it('adds new conversations for direct media sends and appends when the optimistic placeholder is missing', async () => {
    createSubject()
    conversations.value = []
    selectedUserName.value = ''
    messages.value = []

    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/chat/send') {
        return {
          id: 77,
          sender_id: 7,
          receiver_id: 15,
          content: 'doc-file',
          message_type: 'voice',
          created_at: '2026-05-14T14:05:00Z',
        }
      }
      if (url === '/api/chat/conversations') {
        return conversations.value
      }
      throw new Error(`unexpected url ${url}`)
    })

    selectedUserId.value = 15
    const sent = await subject.sendMediaMessage('voice', 'doc-file', undefined, -404)

    expect(sent?.id).toBe(77)
    expect(messages.value.map((message) => message.id)).toEqual([77])
    expect(conversations.value[0]).toMatchObject({
      other_user_id: 15,
      other_user_name: 'گفتگوی جدید',
      last_message_type: 'voice',
      last_message_content: 'پیام صوتی',
    })
    expect(scrollToBottomMock).toHaveBeenCalled()
  })

  it('edits existing messages and sends optimistic text messages with reply metadata', async () => {
    createSubject()
    messages.value = [{ id: 7, content: 'old', message_type: 'text' }]
    editingMessage.value = { id: 7, content: 'old', message_type: 'text' }
    messageInput.value = 'edited text'

    messageMocks.apiFetchJson.mockResolvedValueOnce({ id: 7, content: 'edited text', message_type: 'text' })
    await subject.sendMessage()

    expect(messages.value[0].content).toBe('edited text')
    expect(editingMessage.value).toBeNull()
    expect(messageInput.value).toBe('')
    expect(adjustTextareaHeightMock).toHaveBeenCalled()

    replyingToMessage.value = { id: 90, sender_id: 12, content: 'reply target', message_type: 'text' }
    messageInput.value = 'fresh text'
    isMobile.value = true
    swipedMessageId.value = 90
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/chat/send') {
        return {
          id: 101,
          sender_id: 7,
          receiver_id: 12,
          content: 'fresh text',
          message_type: 'text',
          created_at: '2026-05-14T14:00:00Z',
        }
      }
      if (url === '/api/chat/conversations') {
        return conversations.value
      }
      throw new Error(`unexpected url ${url}`)
    })

    await subject.sendMessage()
    await flushPromises()

    expect(messages.value[messages.value.length - 1].id).toBe(101)
    expect(replyingToMessage.value).toBeNull()
    expect(swipedMessageId.value).toBeNull()
    expect(forceScrollToBottomMock).toHaveBeenCalled()
    expect(focusMessageInputMock).toHaveBeenCalled()
  })

  it('rejects oversized sticker messages, flags failed sends, and supports polling/status updates', async () => {
    createSubject()
    messageInput.value = '😀'.repeat(MAX_STICKERS_PER_MESSAGE + 1)

    await subject.sendMessage()
    expect(alertSpy).toHaveBeenCalledWith(`حداکثر ${MAX_STICKERS_PER_MESSAGE} استیکر در هر پیام مجاز است.`)

    alertSpy.mockClear()
    messageInput.value = 'fails later'
    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('send failed'))
    await subject.sendMessage()
    await flushPromises()

    expect(messages.value).toHaveLength(1)
    expect(messages.value![0]).toMatchObject({ is_sending: false, is_error: true })

    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/users-public/12') {
        return { last_seen_at: '2026-05-14T13:58:30' }
      }
      if (url === '/api/chat/conversations') {
        return []
      }
      if (url.startsWith('/api/chat/messages/12?limit=')) {
        return []
      }
      if (url === '/api/chat/read/12') {
        return {}
      }
      throw new Error(`unexpected url ${url}`)
    })

    subject.startStatusPolling(12)
    await flushPromises()
    expect(targetUserStatus.value).toBe('آنلاین')

    subject.startPolling()
    vi.advanceTimersByTime(30000)
    await flushPromises()
    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith('/api/chat/conversations', {}, expect.objectContaining({
      surface: 'messenger',
      scope: 'list',
      operation: 'load-list',
    }))

    subject.stopPolling()
    subject.stopStatusPolling()
  })

  it('loads around-message slices, ignores stale loads, and handles message-load errors', async () => {
    createSubject()
    messageMocks.apiFetchJson.mockResolvedValueOnce([
      { id: 88, sender_id: 12, receiver_id: 7, content: 'around', message_type: 'text' },
    ])

    await subject.loadMessages(12, false, 88)
    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith(expect.stringContaining('around_id=88'), {}, expect.objectContaining({
      surface: 'messenger',
      scope: 'panel',
      operation: 'load-detail',
    }))
    expect((messages.value ?? []).map((message) => message.id)).toEqual([88])
    expect(subject.hasOlderMessages.value).toBe(true)
    expect(isLoadingMessages.value).toBe(false)

    let resolveLoad!: (value: any[]) => void
    messageMocks.apiFetchJson.mockImplementationOnce(() => new Promise((resolve) => {
      resolveLoad = resolve
    }))
    const staleLoad = subject.loadMessages(12)
    selectedUserId.value = 99
    resolveLoad([{ id: 99, sender_id: 12, receiver_id: 7, content: 'stale', message_type: 'text' }])
    await staleLoad
    expect((messages.value ?? []).map((message) => message.id)).toEqual([88])

    scope?.stop()
    messages.value = []
    selectedUserId.value = 12
    createSubject()
    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('history failed'))
    await subject.loadMessages(12)
    expect(error.value).toBe('دریافت پیام‌های این گفتگو ممکن نشد.')
    expect(isLoadingMessages.value).toBe(false)
  })

  it('routes active message-load failures to the pane-scoped error when provided', async () => {
    createSubject({ withMessagePanelError: true })
    messages.value = [{ id: 1, sender_id: 12, receiver_id: 7, content: 'old', message_type: 'text' }]
    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('history failed'))

    await subject.loadMessages(12)

    expect(messagePanelError.value).toBe('دریافت پیام‌های این گفتگو ممکن نشد.')
    expect(error.value).toBe('')
    expect(messages.value.map((message) => message.id)).toEqual([1])
  })

  it('updates unread state for silent refreshes and keeps bottom anchoring for outgoing/current-user messages', async () => {
    createSubject()
    messages.value = [
      { id: 1, sender_id: 12, receiver_id: 7, content: 'old', message_type: 'text' },
    ]
    isUserAtBottom.value = false
    messageMocks.apiFetchJson.mockResolvedValueOnce([
      { id: 1, sender_id: 12, receiver_id: 7, content: 'old', message_type: 'text' },
      { id: 2, sender_id: 12, receiver_id: 7, content: 'remote', message_type: 'text' },
    ])

    await subject.loadMessages(12, true)
    expect(unreadNewMessagesCount.value).toBe(1)

    isUserAtBottom.value = true
    isViewingReply.value = false
    messageMocks.apiFetchJson.mockResolvedValueOnce([
      { id: 1, sender_id: 12, receiver_id: 7, content: 'old', message_type: 'text' },
      { id: 2, sender_id: 12, receiver_id: 7, content: 'remote', message_type: 'text' },
      { id: 3, sender_id: 7, receiver_id: 12, content: 'mine', message_type: 'text' },
    ])

    await subject.loadMessages(12, true)
    await flushPromises()
    expect(scrollToBottomMock).toHaveBeenCalled()
  })

  it('keeps bottom anchored when silent hydration prepends older messages without changing the latest message', async () => {
    createSubject()
    messages.value = [
      { id: 120, sender_id: 7, receiver_id: 12, content: 'tail 1', message_type: 'text' },
      { id: 121, sender_id: 7, receiver_id: 12, content: 'tail 2', message_type: 'text' },
    ]
    isUserAtBottom.value = true
    isViewingReply.value = false
    messageMocks.apiFetchJson.mockResolvedValueOnce([
      { id: 113, sender_id: 7, receiver_id: 12, content: 'older', message_type: 'text' },
      { id: 120, sender_id: 7, receiver_id: 12, content: 'tail 1', message_type: 'text' },
      { id: 121, sender_id: 7, receiver_id: 12, content: 'tail 2', message_type: 'text' },
    ])

    await subject.loadMessages(12, true)
    await flushPromises()

    expect(messages.value.map((message) => message.id)).toEqual([113, 120, 121])
    expect(forceScrollToBottomMock).toHaveBeenCalled()
    expect(scrollToBottomMock).not.toHaveBeenCalled()
    expect(unreadNewMessagesCount.value).toBe(0)
  })

  it('covers older-message guards and all-duplicate pagination responses', async () => {
    createSubject()

    isLoadingMessages.value = true
    await expect(subject.loadOlderMessages(12)).resolves.toBe(0)
    isLoadingMessages.value = false

    messages.value = [{ id: -1, sender_id: 7, receiver_id: 12, content: 'pending', message_type: 'text' }]
    await expect(subject.loadOlderMessages(12)).resolves.toBe(0)
    expect(subject.hasOlderMessages.value).toBe(false)

    subject.hasOlderMessages.value = true
    messages.value = [{ id: 10, sender_id: 12, receiver_id: 7, content: 'current', message_type: 'text' }]
    selectedUserId.value = 99
    messageMocks.apiFetchJson.mockResolvedValueOnce([{ id: 9, sender_id: 12, receiver_id: 7, content: 'older', message_type: 'text' }])
    await expect(subject.loadOlderMessages(12)).resolves.toBe(0)

    selectedUserId.value = 12
    messageMocks.apiFetchJson.mockResolvedValueOnce([{ id: 10, sender_id: 12, receiver_id: 7, content: 'dupe', message_type: 'text' }])
    await expect(subject.loadOlderMessages(12)).resolves.toBe(0)
    expect(messages.value.map((message) => message.id)).toEqual([10])
  })

  it('covers read failures, edit failures, no-target sends, abort cancellation, and sticker forwarding', async () => {
    createSubject()

    selectedUserId.value = null
    await subject.markAsRead()
    await expect(subject.sendMediaMessage('image', 'file')).resolves.toBeNull()
    messageInput.value = 'no target'
    await subject.sendMessage()
    expect(messageMocks.apiFetchJson).not.toHaveBeenCalled()

    selectedUserId.value = 12
    conversations.value = [{ other_user_id: 12, unread_count: 4 }]
    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('read failed'))
    await subject.markAsRead()
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to mark as read', expect.any(Error))

    messageMocks.apiFetchJson.mockResolvedValueOnce({})
    await subject.markAsRead()
    expect(conversations.value[0].unread_count).toBe(0)
    expect(messageMocks.markChatAsRead).toHaveBeenCalledWith(12)

    messages.value = [{ id: 7, content: 'old', message_type: 'text' }]
    editingMessage.value = { id: 7, content: 'old', message_type: 'text' }
    messageInput.value = 'bad edit'
    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('edit failed'))
    await subject.sendMessage()
    expect(alertSpy).toHaveBeenCalledWith('خطا در ویرایش پیام')
    editingMessage.value = null

    messageInput.value = 'abort me'
    messageMocks.apiFetchJson.mockImplementationOnce((_url: string, options?: RequestInit) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))
    }))
    const sendPromise = subject.sendMessage()
    await nextTick()
    const tempId = messages.value.find((message) => message.id < 0)?.id
    expect(typeof tempId).toBe('number')
    subject.cancelTextMessage(tempId!)
    await sendPromise
    expect(messages.value.some((message) => message.id === tempId)).toBe(false)

    messageMocks.apiFetchJson.mockResolvedValueOnce({ id: 50, sender_id: 7, receiver_id: 12, content: '🙂', message_type: 'sticker' })
    subject.sendSticker('🙂')
    await flushPromises()
    expect(messages.value.some((message) => message.id === 50)).toBe(true)
  })

  it('handles reply focus helpers and mobile reply cancellation', async () => {
    createSubject()
    isMobile.value = true
    swipedMessageId.value = 321
    const replyMessage = { id: 222, sender_id: 12, content: 'reply me', message_type: 'text' } as any

    subject.handleReply(replyMessage)
    await nextTick()

    expect(replyingToMessage.value).toEqual(replyMessage)
    expect(focusMessageInputMock).toHaveBeenCalled()

    subject.cancelReply()
    expect(replyingToMessage.value).toBeNull()
    expect(swipedMessageId.value).toBeNull()
  })

  it('formats target-user status for minutes, today, yesterday, old dates, missing data, and failures', async () => {
    createSubject()
    const statuses = [
      { last_seen_at: '2026-05-14T13:55:00' },
      { last_seen_at: '2026-05-14T09:15:00Z' },
      { last_seen_at: '2026-05-13T18:30:00Z' },
      { last_seen_at: '2026-05-10T08:00:00Z' },
      {},
    ]
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url === '/api/users-public/12') return statuses.shift()
      throw new Error('unexpected')
    })

    for (const expected of ['5 دقیقه پیش', 'امروز ۱۲:۴۵', 'دیروز ۲۲:۰۰', '۱۴۰۵', 'خیلی وقت پیش']) {
      subject.startStatusPolling(12)
      await flushPromises()
      subject.stopStatusPolling()
      expect(targetUserStatus.value).toContain(expected)
    }

    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('status failed'))
    subject.startStatusPolling(12)
    await flushPromises()
    subject.stopStatusPolling()
    expect(consoleErrorSpy).toHaveBeenCalledWith('Error fetching status', expect.any(Error))
  })
})
