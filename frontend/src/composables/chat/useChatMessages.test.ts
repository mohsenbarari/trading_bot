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
  let isLoadingMessages: ReturnType<typeof ref<boolean>>
  let isSending: ReturnType<typeof ref<boolean>>
  let unreadNewMessagesCount: ReturnType<typeof ref<number>>
  let isUserAtBottom: ReturnType<typeof ref<boolean>>
  let isViewingReply: ReturnType<typeof ref<boolean>>
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
  let forceScrollToBottomMock: ReturnType<typeof vi.fn>
  let focusMessageInputMock: ReturnType<typeof vi.fn>
  let adjustTextareaHeightMock: ReturnType<typeof vi.fn>
  let subject: ReturnType<typeof useChatMessages>
  let alertSpy: ReturnType<typeof vi.spyOn>
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  function createSubject() {
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
        isLoadingMessages: isLoadingMessages as any,
        isSending: isSending as any,
        unreadNewMessagesCount: unreadNewMessagesCount as any,
        isUserAtBottom: isUserAtBottom as any,
        isViewingReply: isViewingReply as any,
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

    selectedUserId = ref<number | null>(12)
    messages = ref<any[]>([])
    conversations = ref<any[]>([])
    error = ref('')
    isLoadingMessages = ref(false)
    isSending = ref(false)
    unreadNewMessagesCount = ref(0)
    isUserAtBottom = ref(true)
    isViewingReply = ref(false)
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
  })

  it('loads conversations, syncs muted ids, and stores fetch errors', async () => {
    createSubject()
    messageMocks.apiFetchJson.mockResolvedValueOnce([
      { other_user_id: 12, is_muted: true },
      { other_user_id: -5, is_muted: false },
    ])

    await subject.loadConversations()

    expect(conversations.value).toHaveLength(2)
    expect(messageMocks.syncMutedConversationIds).toHaveBeenCalledWith([12])

    messageMocks.apiFetchJson.mockRejectedValueOnce(new Error('load failed'))
    await subject.loadConversations()
    expect(error.value).toBe('load failed')
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
      if (url.startsWith('/api/chat/messages/12?limit=48')) {
        return [
          {
            id: 1,
            sender_id: 12,
            receiver_id: 7,
            content: 'سلام',
            message_type: 'text',
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
    expect(scrollToUnreadOrBottomMock).toHaveBeenCalledTimes(1)
    expect(messageMocks.markChatAsRead).toHaveBeenCalledWith(12)
    expect(isLoadingMessages.value).toBe(false)
  })

  it('reuses the snapshot warm path before the fresh server load resolves', async () => {
    createSubject()
    messageMocks.apiFetchJson.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/chat/messages/12?limit=48')) {
        return [
          {
            id: 10,
            sender_id: 12,
            receiver_id: 7,
            content: 'cached',
            message_type: 'text',
            created_at: '2026-05-14T13:00:00Z',
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
      if (url.startsWith('/api/chat/messages/12?limit=48')) {
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
      if (url.startsWith('/api/chat/messages/12?limit=48')) {
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
    expect(messageMocks.apiFetchJson).toHaveBeenCalledWith('/api/chat/conversations', {})

    subject.stopPolling()
    subject.stopStatusPolling()
  })
})