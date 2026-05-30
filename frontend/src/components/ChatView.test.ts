import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as chatRoomRouting from '../utils/chatRoomRouting'

const chatViewMocks = vi.hoisted(() => ({
  routeState: {
    path: '/chat',
    query: {} as Record<string, string | string[] | null | undefined>,
  },
  routerReplaceMock: vi.fn(),
  routerPushMock: vi.fn(),
  apiFetchMock: vi.fn(async (_url: string, _options?: RequestInit) => ({})),
  messagesLogicOptions: null as any,
  loadConversationsMock: vi.fn(),
  loadMessagesMock: vi.fn(),
  conversationsSeed: [] as any[],
  messagesSeed: [] as any[],
  imageCacheState: {} as Record<string, string>,
  downloadMediaMock: vi.fn(),
  scheduleMediaHydrationMock: vi.fn(),
  openMediaLightboxMock: vi.fn(),
  handleMediaUploadWrapperMock: vi.fn(),
  cancelUploadMock: vi.fn(),
  setLightboxIndexMock: vi.fn(),
  closeLightboxMock: vi.fn(),
  cancelDocumentDownloadMock: vi.fn(),
  cancelMediaDownloadMock: vi.fn(),
  cancelTextMessageMock: vi.fn(),
  loadOlderMessagesMock: vi.fn(async () => 0),
  hasOlderMessagesValue: false,
  isLoadingOlderMessagesValue: false,
  scrollToBottomMock: vi.fn(),
  scrollToMessageMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  discardBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  setConversationMutedMock: vi.fn(),
  seedFileCacheMock: vi.fn(),
  shareMultipleFilesMock: vi.fn(),
  shareFileMock: vi.fn(),
}))

let chatViewResizeObserverCallback: ResizeObserverCallback | null = null

class ChatViewResizeObserverMock {
  constructor(callback: ResizeObserverCallback) {
    chatViewResizeObserverCallback = callback
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.mock('vue-router', () => ({
  useRouter: () => ({
    replace: chatViewMocks.routerReplaceMock,
    push: chatViewMocks.routerPushMock,
  }),
  useRoute: () => chatViewMocks.routeState,
}))

vi.mock('@formkit/auto-animate/vue', () => ({
  vAutoAnimate: {},
}))

vi.mock('../composables/useBackButton', () => ({
  pushBackState: chatViewMocks.pushBackStateMock,
  popBackState: chatViewMocks.popBackStateMock,
  discardBackState: chatViewMocks.discardBackStateMock,
  clearBackStack: chatViewMocks.clearBackStackMock,
}))

vi.mock('../stores/notifications', () => ({
  useNotificationStore: () => ({
    setConversationMuted: chatViewMocks.setConversationMutedMock,
  }),
}))

vi.mock('../composables/chat/useChatMessages', () => ({
  useChatMessages: (options: any) => ({
    ...(chatViewMocks.messagesLogicOptions = options, {}),
    apiFetch: chatViewMocks.apiFetchMock,
    loadConversations: chatViewMocks.loadConversationsMock.mockImplementation(async () => {
      options.conversations.value = [...chatViewMocks.conversationsSeed]
    }),
    loadMessages: chatViewMocks.loadMessagesMock.mockImplementation(async () => {
      options.messages.value = [...chatViewMocks.messagesSeed]
      return [...chatViewMocks.messagesSeed]
    }),
    loadOlderMessages: chatViewMocks.loadOlderMessagesMock,
    markAsRead: vi.fn(),
    sendMessage: vi.fn(),
    sendMediaMessage: vi.fn(),
    cancelTextMessage: chatViewMocks.cancelTextMessageMock,
    cancelEdit: vi.fn(),
    handleReply: vi.fn((msg: any) => {
      options.replyingToMessage.value = msg
    }),
    cancelReply: vi.fn(() => {
      options.replyingToMessage.value = null
    }),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
    startStatusPolling: vi.fn(),
    stopStatusPolling: vi.fn(),
    hasOlderMessages: { get value() { return chatViewMocks.hasOlderMessagesValue } },
    isLoadingOlderMessages: { get value() { return chatViewMocks.isLoadingOlderMessagesValue } },
  }),
}))

vi.mock('../composables/chat/useChatMedia', async () => {
  const { ref } = await import('vue')
  return {
    useChatMedia: () => ({
      imageCache: ref(chatViewMocks.imageCacheState),
      scheduleMediaHydration: chatViewMocks.scheduleMediaHydrationMock,
      downloadMedia: chatViewMocks.downloadMediaMock,
      lightboxMedia: ref(null),
      cancelUpload: chatViewMocks.cancelUploadMock,
      cancelDocumentDownload: chatViewMocks.cancelDocumentDownloadMock,
      cancelMediaDownload: chatViewMocks.cancelMediaDownloadMock,
      handleMediaClick: chatViewMocks.openMediaLightboxMock,
      setLightboxIndex: chatViewMocks.setLightboxIndexMock,
      closeLightbox: chatViewMocks.closeLightboxMock,
      handleMediaUploadWrapper: chatViewMocks.handleMediaUploadWrapperMock,
    }),
  }
})

vi.mock('../composables/chat/useChatWebSocket', async () => {
  const { reactive, computed } = await import('vue')
  return {
    useChatWebSocket: () => ({
      typingUsers: reactive({}),
      isTyping: false,
      activityTextByConversation: computed(() => ({})),
      handleTypingWrapper: vi.fn(),
      sendTypingSignal: vi.fn(),
    }),
  }
})

vi.mock('../composables/chat/useChatScroll', async () => {
  const { ref } = await import('vue')
  return {
    useChatScroll: () => ({
      isViewingReply: ref(false),
      scrollToBottom: chatViewMocks.scrollToBottomMock,
      forceScrollToBottom: vi.fn(),
      handleScroll: vi.fn(),
      scrollToUnreadOrBottom: vi.fn(),
      scrollToMessage: chatViewMocks.scrollToMessageMock,
    }),
  }
})

vi.mock('../composables/chat/useChatFileHandler', () => ({
  seedFileCache: chatViewMocks.seedFileCacheMock,
  shareMultipleFiles: chatViewMocks.shareMultipleFilesMock,
  shareFile: chatViewMocks.shareFileMock,
}))

vi.mock('../utils/messageReactions', () => ({
  MESSAGE_REACTION_CATALOG: ['🔥', '👍'],
  recordRecentMessageReaction: vi.fn(),
}))

vi.mock('../utils/chatRoomRouting', () => ({
  buildChatSendBody: vi.fn(),
  buildChatSendEndpoint: vi.fn(),
  isNamedRoomKind: (kind: string) => kind === 'group' || kind === 'channel',
  resolveRoomConversationKey: vi.fn(),
}))

describe('ChatView.vue', () => {
  beforeEach(() => {
    chatViewMocks.routeState.query = {}
    chatViewMocks.routerReplaceMock.mockReset()
    chatViewMocks.routerReplaceMock.mockImplementation(async ({ path, query }: { path: string, query?: Record<string, string> }) => {
      chatViewMocks.routeState.path = path
      chatViewMocks.routeState.query = { ...(query ?? {}) }
    })
    chatViewMocks.routerPushMock.mockReset()
    chatViewMocks.apiFetchMock.mockReset()
    chatViewMocks.apiFetchMock.mockResolvedValue({})
    chatViewMocks.messagesLogicOptions = null
    chatViewMocks.loadConversationsMock.mockReset()
    chatViewMocks.loadMessagesMock.mockReset()
    chatViewMocks.conversationsSeed = []
    chatViewMocks.messagesSeed = []
    chatViewMocks.imageCacheState = {}
    chatViewMocks.downloadMediaMock.mockReset()
    chatViewMocks.scheduleMediaHydrationMock.mockReset()
    chatViewMocks.openMediaLightboxMock.mockReset()
    chatViewMocks.handleMediaUploadWrapperMock.mockReset()
    chatViewMocks.cancelUploadMock.mockReset()
    chatViewMocks.setLightboxIndexMock.mockReset()
    chatViewMocks.closeLightboxMock.mockReset()
    chatViewMocks.cancelDocumentDownloadMock.mockReset()
    chatViewMocks.cancelMediaDownloadMock.mockReset()
    chatViewMocks.cancelTextMessageMock.mockReset()
    chatViewMocks.loadOlderMessagesMock.mockReset()
    chatViewMocks.loadOlderMessagesMock.mockResolvedValue(0)
    chatViewMocks.hasOlderMessagesValue = false
    chatViewMocks.isLoadingOlderMessagesValue = false
    chatViewMocks.scrollToBottomMock.mockReset()
    chatViewMocks.scrollToMessageMock.mockReset()
    chatViewMocks.pushBackStateMock.mockReset()
    chatViewMocks.popBackStateMock.mockReset()
    chatViewMocks.discardBackStateMock.mockReset()
    chatViewMocks.clearBackStackMock.mockReset()
    chatViewMocks.setConversationMutedMock.mockReset()
    chatViewMocks.seedFileCacheMock.mockReset()
    chatViewMocks.shareMultipleFilesMock.mockReset()
    chatViewMocks.shareFileMock.mockReset()
    chatViewResizeObserverCallback = null
    vi.stubGlobal('ResizeObserver', ChatViewResizeObserverMock)
    vi.mocked(chatRoomRouting.resolveRoomConversationKey).mockImplementation((kind, id) => (
      kind === 'group' || kind === 'channel' ? -Number(id) : Number(id)
    ))
    vi.mocked(chatRoomRouting.buildChatSendEndpoint).mockImplementation((conversationId) => (
      conversationId < 0 ? `/chat/rooms/${Math.abs(conversationId)}/send` : '/chat/send'
    ))
    vi.mocked(chatRoomRouting.buildChatSendBody).mockImplementation((conversationId, payload) => (
      conversationId < 0 ? { ...payload } : { receiver_id: conversationId, ...payload }
    ))
    document.body.innerHTML = ''

    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: vi.fn(() => Promise.resolve()),
      },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  async function mountChatView(
    overrides: Record<string, unknown> = {},
    stubOverrides: Record<string, unknown> = {},
  ) {
    const ChatView = (await import('./ChatView.vue')).default
    return mount(ChatView, {
      props: {
        apiBaseUrl: '',
        jwtToken: 'jwt-token',
        currentUserId: 7,
        currentUserRole: 'عادی',
        currentUserIsAccountant: false,
        currentUserIsCustomer: false,
        ...overrides,
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          transition: false,
          teleport: true,
          MessengerLoadingScreen: { template: '<div class="messenger-loading-stub"></div>' },
          ChatAlbumLayout: { template: '<div class="chat-album-layout-stub"></div>' },
          ChatHeader: { template: '<div><button class="chat-header-stub open-group-creation" @click="$emit(\'create-group\')">group</button><button class="chat-header-back" @click="$emit(\'back\')">back</button><button class="chat-header-view-profile" @click="$emit(\'view-profile\')">profile</button></div>' },
          ChatInputBar: { template: '<div class="chat-input-bar-stub"></div>' },
          ChatMessageItem: { template: '<div class="chat-message-item-stub"></div>' },
          ChatContextMenu: { template: '<div class="chat-context-menu-stub"></div>' },
          ChatSearchGlobalList: { template: '<div class="chat-search-global-list-stub"></div>' },
          ChatEmptyState: { template: '<div class="chat-empty-state-stub"></div>' },
          ChatConversationList: { template: '<button class="chat-conversation-list-stub open-new-conversation" @click="$emit(\'new-conversation\')">new</button>' },
          ChatNewConversationModal: { template: '<div class="chat-new-conversation-modal-stub"></div>' },
          ChatGroupManagerModal: { template: '<div class="chat-group-manager-modal-stub"></div>' },
          CreateChannelView: { template: '<div class="create-channel-view-stub"></div>' },
          AttachmentMenu: { template: '<div class="attachment-menu-stub"></div>' },
          ChatForwardModal: { template: '<div class="chat-forward-modal-stub"></div>' },
          ChatLightbox: { template: '<div class="chat-lightbox-stub"></div>' },
          ChatLocationModal: { template: '<div class="chat-location-modal-stub"></div>' },
          ChatSearchBottomBar: { template: '<div class="chat-search-bottom-bar-stub"></div>' },
          ...stubOverrides,
        },
      },
    })
  }

  function getExposedStartNewChat(wrapper: Awaited<ReturnType<typeof mountChatView>>) {
    const exposed = wrapper.vm.$.exposed as { startNewChat?: (userId: number, userName: string) => unknown } | null
    if (!exposed?.startNewChat) {
      throw new Error('ChatView expose is unavailable in test harness')
    }
    return exposed.startNewChat
  }

  function getChatViewTestHooks(wrapper: Awaited<ReturnType<typeof mountChatView>>) {
    const exposed = wrapper.vm.$.exposed as { __testHooks?: any } | null
    if (!exposed?.__testHooks) {
      throw new Error('ChatView test hooks are unavailable in test harness')
    }
    return exposed.__testHooks
  }

  function buildMessage(overrides: Record<string, unknown> = {}) {
    return {
      id: 11,
      sender_id: 55,
      receiver_id: 7,
      content: 'متن تست',
      message_type: 'text',
      created_at: '2026-05-12T10:00:00',
      is_read: false,
      is_deleted: false,
      reactions: [],
      ...overrides,
    }
  }

  function buildImageMessage(overrides: Record<string, unknown> = {}) {
    return buildMessage({
      message_type: 'image',
      content: JSON.stringify({ file_id: 'img-1' }),
      ...overrides,
    })
  }

  function buildCurrentUserMessage(id: number, content = `متن ${id}`) {
    return buildMessage({
      id,
      sender_id: 7,
      receiver_id: 55,
      content,
      created_at: new Date().toISOString(),
    })
  }

  it('syncs selected conversation route while preserving unrelated array query values', async () => {
    chatViewMocks.routeState.query = {
      user_id: ['12'],
      user_name: ['قدیمی'],
      keep: ['yes'],
      empty: [''],
    } as any

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55)
    expect(chatViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      path: '/chat',
      query: {
        keep: 'yes',
        user_id: '55',
        user_name: 'Target User',
      },
    })

    wrapper.unmount()
  }, 10000)

  it('keeps bottom messages anchored when the messages container height changes', async () => {
    chatViewMocks.messagesSeed = [buildMessage({ id: 31 })]
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    const container = wrapper.get('.messages-container').element as HTMLElement
    let clientHeight = 400
    let scrollHeight = 1000
    Object.defineProperty(container, 'clientHeight', { configurable: true, get: () => clientHeight })
    Object.defineProperty(container, 'scrollHeight', { configurable: true, get: () => scrollHeight })
    container.scrollTop = 520

    expect(chatViewResizeObserverCallback).toBeTruthy()
    chatViewResizeObserverCallback?.([], {} as ResizeObserver)
    clientHeight = 320
    scrollHeight = 1000
    chatViewResizeObserverCallback?.([], {} as ResizeObserver)

    expect(container.scrollTop).toBe(600)

    wrapper.unmount()
  })

  it('covers exposed helper branches for payload normalization, local actions, and ordering', async () => {
    const warnSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const freshMessage = buildCurrentUserMessage(501, 'پیام تازه')
    const oldMessage = buildCurrentUserMessage(502, 'پیام قدیمی')
    oldMessage.created_at = '2020-01-01T00:00:00Z'
    const albumA = buildImageMessage({
      id: 601,
      sender_id: 55,
      receiver_id: 7,
      created_at: '2026-05-12T10:00:01Z',
      content: JSON.stringify({ file_id: 'a', album_id: 'album-a', album_index: 1 }),
    })
    const albumB = buildImageMessage({
      id: 602,
      sender_id: 55,
      receiver_id: 7,
      created_at: '2026-05-12T10:00:00Z',
      content: JSON.stringify({ file_id: 'b', album_id: 'album-a', album_index: 0 }),
    })
    chatViewMocks.messagesSeed = [freshMessage, oldMessage, albumA, albumB]
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_at: '2026-05-12T10:00:00Z',
        unread_count: 0,
        room_kind: 'direct',
        is_pinned: true,
        pin_order: 4,
      },
      {
        id: 56,
        other_user_id: -12,
        chat_id: 12,
        other_user_name: 'کانال',
        last_message_at: '2026-05-12T09:00:00Z',
        unread_count: 0,
        room_kind: 'channel',
        is_mandatory: true,
      },
    ]

    const wrapper = await mountChatView({ targetUserId: 55, targetUserName: 'Target User' })
    await flushPromises()
    const hooks = getChatViewTestHooks(wrapper)

    expect(hooks.normalizeLocationPayload(null)).toBeNull()
    expect(hooks.normalizeLocationPayload({ latitude: '35.7', longitude: '51.4', snapshot_id: 9 })).toEqual({
      lat: 35.7,
      lng: 51.4,
      snapshot_id: 9,
    })
    expect(hooks.normalizeLocationPayload({ lat: 'bad', lng: 51 })).toBeNull()
    hooks.handleLocationClick(buildMessage({ message_type: 'location', content: JSON.stringify({ lat: 1, lng: 2 }) }))
    expect(hooks.state.selectedLocation.value).toEqual({ lat: 1, lng: 2 })
    hooks.closeLocationModal()
    expect(hooks.state.selectedLocation.value).toBeNull()
    hooks.handleLocationClick(buildMessage({ message_type: 'location', content: '{bad json' }))
    expect(warnSpy).toHaveBeenCalled()

    hooks.handleCancelSend({ id: -700, message_type: 'text' })
    hooks.handleCancelSend({ id: -701, message_type: 'image' })
    hooks.handleCancelDownload({ id: 1, message_type: 'document' })
    hooks.handleCancelDownload({ id: 2, message_type: 'image' })
    expect(chatViewMocks.cancelTextMessageMock).toHaveBeenCalledWith(-700)
    expect(chatViewMocks.cancelUploadMock).toHaveBeenCalledWith(-701)
    expect(chatViewMocks.cancelDocumentDownloadMock).toHaveBeenCalledWith(1)
    expect(chatViewMocks.cancelMediaDownloadMock).toHaveBeenCalledWith(2)

    expect(hooks.isPersistedMessageId(5)).toBe(true)
    expect(hooks.isPersistedMessageId(-1)).toBe(false)
    expect(hooks.isDeletableMessage(freshMessage)).toBe(true)
    expect(hooks.isDeletableMessage(oldMessage)).toBe(false)
    expect(hooks.normalizeMessageIds([1, 1, Number.NaN, 2])).toEqual([1, 2])
    expect(hooks.normalizeMessageReactions([{ emoji: '🔥', user_id: '55' }, null, { emoji: '', user_id: 7 }])).toEqual([
      { emoji: '🔥', user_id: 55 },
    ])
    expect(hooks.buildOptimisticMessageReactions([{ emoji: '👍', user_id: 7 }], 7, '👍')).toEqual([])
    expect(hooks.buildOptimisticMessageReactions([], 7, '🔥')).toEqual([{ emoji: '🔥', user_id: 7 }])

    expect(hooks.getPinnedMessagePreview(null)).toBe('')
    expect(hooks.getPinnedMessagePreview({ ...freshMessage, is_deleted: true })).toBe('پیام حذف شد')
    expect(hooks.getPinnedMessagePreview({ ...freshMessage, message_type: 'image' })).toBe('تصویر')
    expect(hooks.getPinnedMessagePreview({ ...freshMessage, message_type: 'document', content: JSON.stringify({ file_name: 'doc.pdf' }) })).toBe('doc.pdf')
    expect(hooks.getPinnedMessagePreview({ ...freshMessage, message_type: 'document', content: '{bad' })).toBe('فایل')

    expect(hooks.isMandatoryPinnedConversation(chatViewMocks.conversationsSeed[1])).toBe(true)
    expect(hooks.isConversationPinned(chatViewMocks.conversationsSeed[0])).toBe(true)
    expect(hooks.getNextLocalPinOrder()).toBe(5)
    expect(hooks.compareConversationActivity({ last_message_at: '' }, { last_message_at: '2026-01-01T00:00:00Z' })).toBe(1)
    expect(hooks.compareConversationActivity({ last_message_at: '2026-01-02T00:00:00Z' }, { last_message_at: '2026-01-01T00:00:00Z' })).toBe(-1)
    expect(hooks.isSameConversation(chatViewMocks.conversationsSeed[0], { ...chatViewMocks.conversationsSeed[0] })).toBe(true)
    hooks.patchConversationState(chatViewMocks.conversationsSeed[0], { is_muted: true })
    expect(hooks.state.conversations.value.find((conversation: any) => conversation.other_user_id === 55)?.is_muted).toBe(true)

    expect(hooks.getAlbumMeta(freshMessage)).toEqual({ albumId: null, albumIndex: Number.MAX_SAFE_INTEGER })
    expect(hooks.getAlbumMeta(albumA)).toEqual({ albumId: 'album-a', albumIndex: 1 })
    expect(hooks.getAlbumMessagesForMessage(albumA).map((message: any) => message.id)).toEqual([602, 601])
    expect(hooks.getContextMenuMessageIds(albumA)).toEqual([602, 601])
    expect(hooks.buildForwardContent(freshMessage, 'new-album')).toBe(freshMessage.content)
    expect(JSON.parse(hooks.buildForwardContent(albumA, null))).not.toHaveProperty('album_id')
    expect(JSON.parse(hooks.buildForwardContent(albumA, 'forwarded', 3))).toMatchObject({ album_id: 'forwarded', album_index: 3 })
    expect(hooks.prepareForwardBatch([601, 602])).toHaveLength(2)

    expect(hooks.formatTime('2026-05-12T10:15:00Z')).toContain('۱۳')
    expect(hooks.formatDateForSeparator('')).toBe('')
    expect(hooks.isUserOnline(new Date().toISOString())).toBe(true)
    expect(hooks.isUserOnline(null)).toBe(false)

    hooks.toggleSelection(501)
    expect(hooks.state.selectedMessages.value).toContain(501)
    hooks.toggleSelection(501)
    expect(hooks.state.selectedMessages.value).not.toContain(501)
    hooks.startAlbumDownloadSelection(albumA, [601, 602])
    expect(hooks.state.selectionModePurpose.value).toBe('album-download')
    expect(hooks.isAlbumInDownloadSelection({ type: 'album', messages: [albumA, albumB] })).toBe(true)
    hooks.handleAlbumDownloadItemToggle(albumA)
    expect(hooks.state.selectedMessages.value).toEqual([602])
    hooks.startAlbumForwardSelection(albumA, [601, 602])
    expect(hooks.state.selectionModePurpose.value).toBe('album-forward')
    hooks.startAlbumShareSelection(albumA, [601])
    expect(hooks.state.selectionModePurpose.value).toBe('album-share')
    hooks.handleGroupedItemSelection({ type: 'album', messages: [albumA, albumB] })
    expect(hooks.state.selectedMessages.value.length).toBeGreaterThan(0)

    hooks.hydrateRenderedMedia({ type: 'album', messages: [albumA, { ...albumB, message_type: 'video' }] })
    hooks.hydrateRenderedMedia(freshMessage)
    expect(chatViewMocks.scheduleMediaHydrationMock).toHaveBeenCalled()

    warnSpy.mockRestore()
    wrapper.unmount()
  })

  it('covers scroll metric helper branches and selection-anchor restoration', async () => {
    chatViewMocks.hasOlderMessagesValue = true
    chatViewMocks.isLoadingOlderMessagesValue = true
    chatViewMocks.loadOlderMessagesMock.mockResolvedValueOnce(2)
    chatViewMocks.messagesSeed = [buildMessage({ id: 701 })]
    const wrapper = await mountChatView({ targetUserId: 55, targetUserName: 'Target User' })
    await flushPromises()
    const hooks = getChatViewTestHooks(wrapper)
    const container = wrapper.get('.messages-container').element as HTMLElement
    let clientHeight = 400
    let scrollHeight = 1000
    Object.defineProperty(container, 'clientHeight', { configurable: true, get: () => clientHeight })
    Object.defineProperty(container, 'scrollHeight', { configurable: true, get: () => scrollHeight })
    container.scrollTop = 520

    expect(hooks.captureMessagesContainerMetrics(null)).toBeNull()
    expect(hooks.captureMessagesContainerMetrics(container)).toEqual({ clientHeight: 400, scrollHeight: 1000, scrollTop: 520 })
    hooks.syncMessagesContainerMetrics(container)
    clientHeight = 300
    scrollHeight = 1000
    hooks.handleMessagesContainerResize()
    expect(container.scrollTop).toBe(620)
    container.scrollTop = 100

    const target = document.createElement('div')
    target.id = 'msg-701'
    document.body.appendChild(target)
    container.getBoundingClientRect = vi.fn(() => ({ top: 100, bottom: 500, left: 0, right: 0, width: 0, height: 400 } as DOMRect))
    target.getBoundingClientRect = vi.fn(() => ({ top: 150, bottom: 180, left: 0, right: 0, width: 0, height: 30 } as DOMRect))
    hooks.captureSelectionAnchor(701)
    expect(hooks.state.pendingSelectionAnchor()).toMatchObject({ messageId: 701, offsetTop: 50, userId: 55 })
    target.getBoundingClientRect = vi.fn(() => ({ top: 250, bottom: 280, left: 0, right: 0, width: 0, height: 30 } as DOMRect))
    expect(hooks.restorePendingSelectionAnchor(container, 55)).toBe(true)
    expect(container.scrollTop).toBe(200)
    expect(hooks.restorePendingSelectionAnchor(container, 55)).toBe(false)

    container.scrollTop = 0
    scrollHeight = 1200
    chatViewMocks.isLoadingOlderMessagesValue = false
    await hooks.handleMessagesScroll()
    await flushPromises()
    expect(chatViewMocks.loadOlderMessagesMock).toHaveBeenCalledWith(55)

    wrapper.unmount()
  })

  it('covers exposed send, forward, recovery, and navigation orchestration branches', async () => {
    vi.useFakeTimers()
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    chatViewMocks.messagesSeed = [
      buildMessage({ id: 901, sender_id: 55, receiver_id: 7, content: 'forward me' }),
    ]
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_at: '2026-05-12T10:00:00Z',
        unread_count: 0,
        room_kind: 'direct',
      },
      {
        id: 88,
        other_user_id: -88,
        chat_id: 88,
        other_user_name: 'Group Room',
        last_message_at: '2026-05-12T09:00:00Z',
        unread_count: 0,
        room_kind: 'group',
      },
    ]

    const wrapper = await mountChatView({ targetUserId: 55, targetUserName: 'Target User' })
    await flushPromises()
    const hooks = getChatViewTestHooks(wrapper)

    await hooks.handleSendVoice(new Blob(['voice'], { type: 'audio/webm' }), 1234)
    expect(chatViewMocks.handleMediaUploadWrapperMock).toHaveBeenCalledWith(
      expect.objectContaining({ name: expect.stringMatching(/^voice_\d+\.webm$/), durationMs: 1234 }),
      null,
      0,
      1,
      { roomKindOverride: 'direct' },
    )

    chatViewMocks.apiFetchMock.mockResolvedValueOnce(buildMessage({ id: 902, message_type: 'location', content: JSON.stringify({ lat: 35.7, lng: 51.4 }) }))
    await hooks.handleSendLocation(35.7, 51.4)
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/send', {
      method: 'POST',
      body: JSON.stringify({ receiver_id: 55, content: JSON.stringify({ lat: 35.7, lng: 51.4 }), message_type: 'location' }),
    })
    expect(hooks.state.messages.value.some((message: any) => message.id === 902)).toBe(true)

    hooks.state.forwardMessageIds.value = [901]
    chatViewMocks.apiFetchMock.mockClear()
    await hooks.forwardSelectedMessages({ kind: 'bot' as any, id: 1, title: 'Unsupported' })
    expect(alertSpy).toHaveBeenCalledWith('هدایت پیام به این مقصد هنوز فعال نشده است')
    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalled()

    hooks.state.forwardMessageIds.value = [901]
    chatViewMocks.apiFetchMock.mockResolvedValue({})
    await hooks.forwardSelectedMessages({ kind: 'group', id: 88, title: 'Group Room' })
    await flushPromises()
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/rooms/88/send', {
      method: 'POST',
      body: JSON.stringify({ content: 'forward me', message_type: 'text', forwarded_from_id: 55 }),
    })
    expect(hooks.state.selectedUserId.value).toBe(-88)
    expect(hooks.state.selectedUserName.value).toBe('Group Room')
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-88)

    hooks.state.forwardMessageIds.value = [901]
    chatViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('forward failed'))
    await hooks.forwardSelectedMessages({ kind: 'user', id: 91, title: 'User 91' })
    expect(alertSpy).toHaveBeenCalledWith('خطا در هدایت پیام‌ها')

    hooks.state.selectedUserId.value = 55
    chatViewMocks.apiFetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({}) })
    await hooks.handleRecoveryAction({ action: 'approve', recoveryId: 'rec-1', userId: 55 })
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/sessions/recovery/rec-1/approve', { method: 'POST' })
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, true)

    chatViewMocks.apiFetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({ detail: 'denied' }) })
    await hooks.handleRecoveryAction({ action: 'request_identity', recoveryId: 'rec-2', userId: 55 })
    expect(alertSpy).toHaveBeenCalledWith('denied')

    chatViewMocks.loadMessagesMock.mockClear()
    hooks.handleRecoveryRealtimeUpdate({ user_id: '55' })
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, true)
    hooks.handleRecoveryRealtimeUpdate({ user_id: 'bad' })
    hooks.handleRecoveryRealtimeUpdate({ user_id: '91' })

    hooks.goBack()
    expect(hooks.state.selectedUserId.value).toBeNull()
    hooks.goBack()
    expect(wrapper.emitted('back')).toBeTruthy()

    hooks.openPublicProfile({ id: 91, account_name: 'owner-91' })
    await vi.runAllTimersAsync()
    expect(chatViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '91' },
      query: { account_name: 'owner-91' },
    })
    hooks.handleTypingForCurrentRoom()

    alertSpy.mockRestore()
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('renders pinned document previews and unpins from the banner', async () => {
    const pinnedDoc = buildCurrentUserMessage(81, JSON.stringify({ file_name: 'invoice.pdf' }))
    pinnedDoc.message_type = 'document'
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]
    chatViewMocks.apiFetchMock
      .mockResolvedValueOnce({ message: pinnedDoc })
      .mockResolvedValueOnce({ message: null })

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    expect(wrapper.get('.pinned-message-meta').text()).toBe('برای رفتن به پیام ضربه بزنید')
    expect(wrapper.get('.pinned-message-preview').text()).toBe('invoice.pdf')

    await wrapper.get('.pinned-message-dismiss').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenLastCalledWith('/chat/messages/81/pin', {
      method: 'POST',
      body: JSON.stringify({ pinned: false }),
    })
    expect(wrapper.find('.pinned-message-banner').exists()).toBe(false)

    wrapper.unmount()
  })

  it('registers manager and admin overlays on the back stack and closes them from the back callback', async () => {
    const wrapper = await mountChatView({ currentUserRole: 'مدیر ارشد' })
    await flushPromises()
    const hooks = getChatViewTestHooks(wrapper)

    chatViewMocks.pushBackStateMock.mockClear()
    hooks.state.groupManagerChatId.value = 91
    hooks.state.showGroupManagerModal.value = true
    await flushPromises()

    let overlayBackCallback = chatViewMocks.pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(overlayBackCallback).toBeTypeOf('function')
    overlayBackCallback?.()
    await flushPromises()
    expect(hooks.state.showGroupManagerModal.value).toBe(false)
    expect(hooks.state.groupManagerChatId.value).toBeNull()

    chatViewMocks.pushBackStateMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    hooks.state.channelManagerChatId.value = 23
    hooks.state.showChannelManagerModal.value = true
    await flushPromises()

    overlayBackCallback = chatViewMocks.pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(overlayBackCallback).toBeTypeOf('function')
    overlayBackCallback?.()
    await flushPromises()
    expect(hooks.state.showChannelManagerModal.value).toBe(false)
    expect(hooks.state.channelManagerChatId.value).toBeNull()
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()

    chatViewMocks.pushBackStateMock.mockClear()
    hooks.openAdminBroadcastModal()
    await flushPromises()
    expect(hooks.state.showAdminBroadcastModal.value).toBe(true)

    overlayBackCallback = chatViewMocks.pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(overlayBackCallback).toBeTypeOf('function')
    overlayBackCallback?.()
    await flushPromises()
    expect(hooks.state.showAdminBroadcastModal.value).toBe(false)

    wrapper.unmount()
  })

  it('rolls back optimistic reactions when the reaction request fails', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    chatViewMocks.messagesSeed = [buildMessage({ reactions: [{ emoji: '👍', user_id: 7 }] })]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-reaction-action" @click="$emit(\'toggle-reaction\', { msg, emoji: \'🔥\' })">{{ (msg.reactions || []).map((item) => item.emoji).join(\',\') }}</button>',
      },
    })
    await flushPromises()

    expect(wrapper.get('.message-reaction-action').text()).toBe('👍')
    chatViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('reaction failed'))

    await wrapper.get('.message-reaction-action').trigger('click')
    await flushPromises()

    expect(alertSpy).toHaveBeenCalledWith('خطا در ثبت ری‌اکشن')
    expect(errorSpy).toHaveBeenCalled()
    expect(wrapper.get('.message-reaction-action').text()).toBe('👍')

    alertSpy.mockRestore()
    errorSpy.mockRestore()
    wrapper.unmount()
  })

  it('cancels local-only selected messages without calling the delete API', async () => {
    chatViewMocks.messagesSeed = [
      buildCurrentUserMessage(-101, 'در حال ارسال'),
      buildImageMessage({
        id: -102,
        sender_id: 7,
        receiver_id: 55,
        created_at: new Date().toISOString(),
        content: JSON.stringify({ file_id: 'local-image' }),
      }),
    ]
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        props: ['selectedMessagesCount', 'isSelectionMode'],
        template: '<div><span class="chat-input-bar-state">{{ String(isSelectionMode) }}|{{ selectedMessagesCount }}</span><button class="delete-selected-action" @click="$emit(\'delete-selected\')">delete</button></div>',
      },
    })
    await flushPromises()

    const selectButtons = wrapper.findAll('.select-message-action')
    await selectButtons[0]!.trigger('click')
    await selectButtons[1]!.trigger('click')
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.delete-selected-action').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(chatViewMocks.cancelTextMessageMock).toHaveBeenCalledWith(-101)
    expect(chatViewMocks.cancelUploadMock).toHaveBeenCalledWith(-102)
    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/chat/messages/-101', { method: 'DELETE' })
    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/chat/messages/-102', { method: 'DELETE' })
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('false|0')

    confirmSpy.mockRestore()
    wrapper.unmount()
  })

  it('allows accountants to start a brand-new direct chat through startNewChat', async () => {
    const wrapper = await mountChatView({ currentUserIsAccountant: true })
    await flushPromises()

    await getExposedStartNewChat(wrapper)(55, 'Target User')

    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55)
    expect(document.body.textContent).not.toContain('حسابدار در این فاز اجازه شروع گفتگوی مستقیم جدید را ندارد')
    wrapper.unmount()
  }, 10000)

  it('opens positive route targets for accountants when no direct conversation exists yet', async () => {
    const wrapper = await mountChatView({
      currentUserIsAccountant: true,
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55)
    expect(document.body.textContent).not.toContain('حسابدار در این فاز اجازه شروع گفتگوی مستقیم جدید را ندارد')

    wrapper.unmount()
  })

  it('allows accountants to open the new conversation modal from the list entry point', async () => {
    const wrapper = await mountChatView({ currentUserIsAccountant: true })
    await flushPromises()

    await wrapper.get('.open-new-conversation').trigger('click')

    expect(wrapper.find('.chat-new-conversation-modal-stub').exists()).toBe(true)

    wrapper.unmount()
  })

  it('allows accountants to start group creation from the header action', async () => {
    const wrapper = await mountChatView({ currentUserIsAccountant: true })
    await flushPromises()

    await wrapper.get('.open-group-creation').trigger('click')

    expect(wrapper.find('.chat-group-manager-modal-stub').exists()).toBe(true)

    wrapper.unmount()
  })

  it('blocks customers from starting group creation from the header action', async () => {
    const wrapper = await mountChatView({ currentUserIsCustomer: true })
    await flushPromises()

    await wrapper.get('.open-group-creation').trigger('click')

    expect(document.body.textContent).toContain('مشتری در این فاز اجازه ساخت گروه جدید را ندارد')

    wrapper.unmount()
  })

  it('opens the new conversation modal and routes its create-group action into the group manager', async () => {
    const wrapper = await mountChatView({}, {
      ChatNewConversationModal: {
        props: ['show'],
        template: `<div>
          <span class="new-chat-modal-state">{{ String(show) }}</span>
          <button class="close-new-chat-modal" @click="$emit('close')">close</button>
          <button class="create-group-from-new-chat" @click="$emit('create-group')">group</button>
        </div>`,
      },
      ChatGroupManagerModal: {
        props: ['show'],
        template: '<div class="group-manager-open-state">{{ String(show) }}</div>',
      },
    })
    await flushPromises()

    expect(wrapper.get('.new-chat-modal-state').text()).toBe('false')
    expect(wrapper.get('.group-manager-open-state').text()).toBe('false')

    await wrapper.get('.open-new-conversation').trigger('click')
    await flushPromises()
    expect(wrapper.get('.new-chat-modal-state').text()).toBe('true')

    await wrapper.get('.close-new-chat-modal').trigger('click')
    await flushPromises()
    expect(wrapper.get('.new-chat-modal-state').text()).toBe('false')

    await wrapper.get('.open-new-conversation').trigger('click')
    await flushPromises()
    await wrapper.get('.create-group-from-new-chat').trigger('click')
    await flushPromises()

    expect(wrapper.get('.new-chat-modal-state').text()).toBe('false')
    expect(wrapper.get('.group-manager-open-state').text()).toBe('true')

    wrapper.unmount()
  })

  it('covers named-room unavailable callback and stale missing-room cleanup paths', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        room_kind: 'direct',
        unread_count: 0,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -88,
      targetUserName: 'Missing Room',
    })
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    const onNamedRoomUnavailable = chatViewMocks.messagesLogicOptions?.onNamedRoomUnavailable
    expect(typeof onNamedRoomUnavailable).toBe('function')

    await onNamedRoomUnavailable?.(-55)
    expect(hooks.state.selectedUserId.value).toBe(-88)

    await onNamedRoomUnavailable?.(-88)
    await flushPromises()
    expect(hooks.state.selectedUserId.value).toBeNull()
    expect(chatViewMocks.routerReplaceMock).toHaveBeenCalledWith({ path: '/chat', query: {} })

    hooks.state.selectedUserId.value = -777
    hooks.state.selectedRoomKind.value = 'group'
    hooks.state.conversations.value = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        room_kind: 'direct',
      },
    ]
    hooks.clearMissingNamedRoomSelection()
    await flushPromises()
    expect(hooks.state.selectedUserId.value).toBeNull()

    wrapper.unmount()
  })

  it('allows accountant route targets when the direct conversation already exists', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]

    const wrapper = await mountChatView({
      currentUserIsAccountant: true,
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55)

    wrapper.unmount()
  })

  it('opens the owner public profile from a direct conversation when additive profile metadata exists', async () => {
    vi.useFakeTimers()
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'دفتر حسابدار',
        profile_user_id: 99,
        profile_account_name: 'owner-99',
        highlight_accountant_user_id: 55,
        highlight_accountant_relation_display_name: 'حسابدار فروش',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]

    const wrapper = await mountChatView({
      currentUserIsAccountant: false,
      targetUserId: 55,
      targetUserName: 'دفتر حسابدار',
    })
    await flushPromises()

    await wrapper.get('.chat-header-view-profile').trigger('click')
    await vi.runAllTimersAsync()

    expect(chatViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '99' },
      query: {
        account_name: 'owner-99',
        highlight_accountant_user_id: '55',
        highlight_accountant_relation_display_name: 'حسابدار فروش',
      },
    })

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('runs global search and opens the selected direct conversation around the result', async () => {
    vi.useFakeTimers()
    chatViewMocks.apiFetchMock.mockImplementation(async (url: string, _options?: RequestInit) => {
      if (url.startsWith('/chat/search')) {
        return [buildMessage({ id: 44, sender_id: 55, receiver_id: 7 })]
      }
      return {}
    })

    const wrapper = await mountChatView({}, {
      ChatHeader: {
        props: ['isSearchActive', 'searchResults'],
        template: `<div>
          <button class="toggle-search-action" @click="$emit('toggle-search')">toggle</button>
          <button class="search-action" @click="$emit('search', 'gold')">search</button>
          <span class="search-state">{{ String(isSearchActive) }}|{{ searchResults.length }}</span>
        </div>`,
      },
      ChatSearchGlobalList: {
        props: ['searchResults'],
        template: '<button class="global-search-result" @click="$emit(\'select-result\', searchResults[0])">open</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.toggle-search-action').trigger('click')
    await wrapper.get('.search-action').trigger('click')
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/search?q=gold')
    expect(wrapper.get('.search-state').text()).toBe('true|1')

    await wrapper.get('.global-search-result').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 44)
    expect(chatViewMocks.pushBackStateMock).toHaveBeenCalled()
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(44)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('navigates in-chat search results and toggles the in-chat result list', async () => {
    vi.useFakeTimers()
    chatViewMocks.apiFetchMock.mockImplementation(async (url: string, _options?: RequestInit) => {
      if (url.startsWith('/chat/search')) {
        return [
          buildMessage({ id: 91, sender_id: 55, receiver_id: 7 }),
          buildMessage({ id: 92, sender_id: 55, receiver_id: 7 }),
        ]
      }
      return {}
    })

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatHeader: {
        props: ['isSearchActive', 'searchResults'],
        template: `<div>
          <button class="toggle-search-action" @click="$emit('toggle-search')">toggle</button>
          <button class="search-action" @click="$emit('search', 'gold')">search</button>
          <span class="search-state">{{ String(isSearchActive) }}|{{ searchResults.length }}</span>
        </div>`,
      },
      ChatSearchBottomBar: {
        props: ['showInChatSearchList'],
        template: `<div>
          <span class="bottom-search-state">{{ String(showInChatSearchList) }}</span>
          <button class="next-search-action" @click="$emit('next')">next</button>
          <button class="prev-search-action" @click="$emit('prev')">prev</button>
          <button class="toggle-search-list-action" @click="$emit('toggle-list')">list</button>
        </div>`,
      },
      ChatSearchGlobalList: {
        props: ['searchResults'],
        template: '<button class="in-chat-search-result" @click="$emit(\'select-result\', searchResults[1])">open</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.toggle-search-action').trigger('click')
    await wrapper.get('.search-action').trigger('click')
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(300)

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/search?q=gold&chat_id=55')
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(91)

    chatViewMocks.loadMessagesMock.mockClear()
    await wrapper.get('.next-search-action').trigger('click')
    await flushPromises()
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 92)
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(92)

    await wrapper.get('.toggle-search-list-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.bottom-search-state').text()).toBe('true')

    await wrapper.get('.in-chat-search-result').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(250)
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 92)
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(92)

    await wrapper.get('.toggle-search-list-action').trigger('click')
    await vi.advanceTimersByTimeAsync(150)
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalled()

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('handles direct mute actions from the conversation list and refreshes local mute state', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
        is_muted: false,
      },
    ]

    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        props: ['conversations'],
        template: "<button class='conversation-mute-action' @click=\"$emit('conversation-action', { action: 'mute', conv: conversations[0] })\">mute</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.setConversationMutedMock.mockClear()

    await wrapper.get('.conversation-mute-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/direct/55/mute', {
      method: 'POST',
      body: JSON.stringify({ muted: true }),
    })
    expect(chatViewMocks.setConversationMutedMock).toHaveBeenCalledWith(55, true)
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('pins a direct conversation from the list', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
        is_pinned: false,
      },
    ]

    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        props: ['conversations'],
        template: "<button class='conversation-pin-action' @click=\"$emit('conversation-action', { action: 'pin', conv: conversations[0] })\">pin</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.conversation-pin-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/direct/55/pin', {
      method: 'POST',
      body: JSON.stringify({ pinned: true }),
    })
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('moves a pinned group conversation upward from the list', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 88,
        other_user_id: -88,
        other_user_name: 'گروه فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 88,
      },
    ]

    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        props: ['conversations'],
        template: "<button class='conversation-move-pin-up-action' @click=\"$emit('conversation-action', { action: 'move-pin-up', conv: conversations[0] })\">move</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.conversation-move-pin-up-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/rooms/88/pin-order', {
      method: 'POST',
      body: JSON.stringify({ direction: 'up' }),
    })
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('marks a channel conversation as unread from the list', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        props: ['conversations'],
        template: "<button class='conversation-mark-unread-action' @click=\"$emit('conversation-action', { action: 'mark-unread', conv: conversations[0] })\">unread</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.conversation-mark-unread-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/rooms/23/mark-unread', {
      method: 'POST',
      body: JSON.stringify({ unread: true }),
    })
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('deletes a direct conversation from the list', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]

    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        props: ['conversations'],
        template: "<button class='conversation-delete-action' @click=\"$emit('conversation-action', { action: 'delete', conv: conversations[0] })\">delete</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.conversation-delete-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/direct/55', { method: 'DELETE' })
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.conversation-delete-action').exists()).toBe(true)

    wrapper.unmount()
  })

  it('routes group leave and channel unfollow actions to their dedicated endpoints', async () => {
    const groupWrapper = await mountChatView({}, {
      ChatConversationList: {
        template: "<button class='conversation-leave-action' @click=\"$emit('conversation-action', { action: 'leave', conv: { other_user_id: -88, other_user_name: 'گروه فعال', room_kind: 'group', chat_id: 88 } })\">leave</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    await groupWrapper.get('.conversation-leave-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/groups/88/leave', { method: 'POST' })
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)

    groupWrapper.unmount()

    const channelWrapper = await mountChatView({}, {
      ChatConversationList: {
        template: "<button class='conversation-unfollow-action' @click=\"$emit('conversation-action', { action: 'unfollow', conv: { other_user_id: -23, other_user_name: 'کانال فعال', room_kind: 'channel', chat_id: 23 } })\">unfollow</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()
    await channelWrapper.get('.conversation-unfollow-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/channels/23/unfollow', { method: 'POST' })
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)

    channelWrapper.unmount()
  })

  it('opens the context menu for a text message and copies its content', async () => {
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 790, clientY: 590 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        props: ['menuState'],
        template: '<div><span class="menu-visible">{{ String(menuState.visible) }}</span><button class="menu-copy-action" @click="$emit(\'copy\')">copy</button></div>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()

    expect(wrapper.get('.menu-visible').text()).toBe('true')

    await wrapper.get('.menu-copy-action').trigger('click')
    await flushPromises()

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('متن تست')
    expect(wrapper.get('.menu-visible').text()).toBe('false')
    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('enters edit mode from the message context menu', async () => {
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 320, clientY: 280 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-edit-action" @click="$emit(\'edit\')">edit</button>',
      },
      ChatInputBar: {
        props: ['modelValue', 'editingMessage'],
        template: '<div class="chat-input-bar-state">{{ modelValue }}|{{ editingMessage?.id ?? \"none\" }}</div>',
        methods: {
          adjustTextareaHeight() {},
          focusInput() {},
        },
      },
    })
    await flushPromises()

    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-edit-action').trigger('click')
    await flushPromises()

    expect(wrapper.get('.chat-input-bar-state').text()).toContain('متن تست')
    expect(wrapper.get('.chat-input-bar-state').text()).toContain('11')

    wrapper.unmount()
  })

  it('pins the context-menu message through the pin endpoint', async () => {
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 250, clientY: 260 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-pin-action" @click="$emit(\'pin-message\')">pin</button>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.apiFetchMock.mockResolvedValue({ message: buildMessage() })
    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-pin-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/messages/11/pin', {
      method: 'POST',
      body: JSON.stringify({ pinned: true }),
    })

    wrapper.unmount()
  })

  it('reports pin-message failures through the inline toast', async () => {
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 250, clientY: 260 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-pin-action" @click="$emit(\'pin-message\')">pin</button>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('pin failed'))
    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-pin-action').trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('pin failed')

    wrapper.unmount()
  })

  it('toggles a message reaction directly from the bubble event', async () => {
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-reaction-action" @click="$emit(\'toggle-reaction\', { msg, emoji: \'🔥\' })">react</button>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.apiFetchMock.mockResolvedValue({ reactions: [{ emoji: '🔥', user_id: 7 }] })
    await wrapper.get('.message-reaction-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/messages/11/reaction', {
      method: 'POST',
      body: JSON.stringify({ emoji: '🔥' }),
    })

    wrapper.unmount()
  })

  it('reacts to the context-menu message and closes the menu', async () => {
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 410, clientY: 300 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        props: ['menuState'],
        template: '<div><span class="menu-visible">{{ String(menuState.visible) }}</span><button class="menu-react-action" @click="$emit(\'react\', \'🔥\')">react</button></div>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.apiFetchMock.mockResolvedValue({ reactions: [{ emoji: '🔥', user_id: 7 }] })
    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.menu-visible').text()).toBe('true')

    await wrapper.get('.menu-react-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/messages/11/reaction', {
      method: 'POST',
      body: JSON.stringify({ emoji: '🔥' }),
    })
    expect(wrapper.get('.menu-visible').text()).toBe('false')

    wrapper.unmount()
  })

  it('opens media clicks through the lightbox handler in normal mode', async () => {
    const imageMessage = buildImageMessage()
    chatViewMocks.messagesSeed = [imageMessage]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="media-click-action" @click="$emit(\'media-click\', msg)">media</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.media-click-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.openMediaLightboxMock).toHaveBeenCalledWith(imageMessage)

    wrapper.unmount()
  })

  it('downloads uncached media first when saving from the context menu', async () => {
    const imageMessage = buildImageMessage()
    chatViewMocks.messagesSeed = [imageMessage]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 430, clientY: 310 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-save-media-action" @click="$emit(\'save-media\')">save</button>',
      },
    })
    await flushPromises()

    chatViewMocks.downloadMediaMock.mockClear()
    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-save-media-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.downloadMediaMock).toHaveBeenCalledWith(imageMessage)

    wrapper.unmount()
  })

  it('saves cached media through a temporary anchor without re-downloading', async () => {
    const imageMessage = buildImageMessage()
    chatViewMocks.messagesSeed = [imageMessage]
    chatViewMocks.imageCacheState = { 'img-1': 'blob:cached-image' }

    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 440, clientY: 320 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-save-media-action" @click="$emit(\'save-media\')">save</button>',
      },
    })
    await flushPromises()

    chatViewMocks.downloadMediaMock.mockClear()
    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-save-media-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.downloadMediaMock).not.toHaveBeenCalled()
    expect(anchorClickSpy).toHaveBeenCalledTimes(1)

    anchorClickSpy.mockRestore()
    wrapper.unmount()
  })

  it('toggles message selection instead of reopening the context menu while selection mode is active', async () => {
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<div><button class="select-message-action" @click="$emit(\'select\', msg)">select</button><button class="message-click-action" @click="emitClick">message</button></div>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 300, clientY: 260 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        props: ['menuState'],
        template: '<div class="menu-visible">{{ String(menuState.visible) }}</div>',
      },
      ChatInputBar: {
        props: ['selectedMessagesCount', 'isSelectionMode'],
        template: '<div class="chat-input-bar-state">{{ String(isSelectionMode) }}|{{ selectedMessagesCount }}</div>',
      },
    })
    await flushPromises()

    await wrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('true|1')

    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()

    expect(wrapper.get('.chat-input-bar-state').text()).toBe('false|0')
    expect(wrapper.get('.menu-visible').text()).toBe('false')

    wrapper.unmount()
  })

  it('keeps media clicks inside selection mode instead of opening the lightbox', async () => {
    const imageMessage = buildImageMessage()
    chatViewMocks.messagesSeed = [imageMessage]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<div><button class="select-message-action" @click="$emit(\'select\', msg)">select</button><button class="media-click-action" @click="$emit(\'media-click\', msg)">media</button></div>',
      },
      ChatInputBar: {
        props: ['selectedMessagesCount', 'isSelectionMode'],
        template: '<div class="chat-input-bar-state">{{ String(isSelectionMode) }}|{{ selectedMessagesCount }}</div>',
      },
    })
    await flushPromises()

    await wrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('true|1')

    chatViewMocks.openMediaLightboxMock.mockClear()
    await wrapper.get('.media-click-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.openMediaLightboxMock).not.toHaveBeenCalled()
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('false|0')

    wrapper.unmount()
  })

  it('deletes the context-menu message and removes it from the visible list', async () => {
    chatViewMocks.messagesSeed = [buildMessage({
      sender_id: 7,
      receiver_id: 55,
      created_at: new Date().toISOString(),
    })]
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 450, clientY: 330 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-delete-action" @click="$emit(\'delete\')">delete</button>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-delete-action').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/messages/11', { method: 'DELETE' })
    expect(wrapper.find('.message-click-action').exists()).toBe(false)

    confirmSpy.mockRestore()
    wrapper.unmount()
  })

  it('closes the context menu without copying when the message is not text', async () => {
    const imageMessage = buildImageMessage()
    chatViewMocks.messagesSeed = [imageMessage]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 460, clientY: 340 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        props: ['menuState'],
        template: '<div><span class="menu-visible">{{ String(menuState.visible) }}</span><button class="menu-copy-action" @click="$emit(\'copy\')">copy</button></div>',
      },
    })
    await flushPromises()

    const clipboardSpy = vi.mocked(navigator.clipboard.writeText)
    clipboardSpy.mockClear()

    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.menu-visible').text()).toBe('true')

    await wrapper.get('.menu-copy-action').trigger('click')
    await flushPromises()

    expect(clipboardSpy).not.toHaveBeenCalled()
    expect(wrapper.get('.menu-visible').text()).toBe('false')

    wrapper.unmount()
  })

  it('copies selected text messages from selection mode and clears the selection', async () => {
    chatViewMocks.messagesSeed = [
      buildCurrentUserMessage(11, 'پیام اول'),
      buildCurrentUserMessage(12, 'پیام دوم'),
    ]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        props: ['selectedMessagesCount', 'isSelectionMode'],
        template: '<div><span class="chat-input-bar-state">{{ String(isSelectionMode) }}|{{ selectedMessagesCount }}</span><button class="copy-selected-action" @click="$emit(\'copy-selected\')">copy</button></div>',
      },
    })
    await flushPromises()

    const selectButtons = wrapper.findAll('.select-message-action')
    await selectButtons[0]!.trigger('click')
    await selectButtons[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('true|2')

    const clipboardSpy = vi.mocked(navigator.clipboard.writeText)
    clipboardSpy.mockClear()
    await wrapper.get('.copy-selected-action').trigger('click')
    await flushPromises()

    expect(clipboardSpy).toHaveBeenCalledWith('پیام اول\n\nپیام دوم')
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('false|0')

    wrapper.unmount()
  })

  it('replies to a single selected message and exits selection mode', async () => {
    chatViewMocks.messagesSeed = [buildCurrentUserMessage(11, 'پیام برای ریپلای')]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        props: ['selectedMessagesCount', 'isSelectionMode', 'replyingToMessage'],
        template: '<div><span class="chat-input-bar-state">{{ String(isSelectionMode) }}|{{ selectedMessagesCount }}|{{ replyingToMessage?.id ?? \"none\" }}</span><button class="reply-selected-action" @click="$emit(\'reply-selected\')">reply</button></div>',
      },
    })
    await flushPromises()

    await wrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('true|1|none')

    await wrapper.get('.reply-selected-action').trigger('click')
    await flushPromises()

    expect(wrapper.get('.chat-input-bar-state').text()).toBe('false|0|11')

    wrapper.unmount()
  })

  it('opens the forward modal from selected messages', async () => {
    chatViewMocks.messagesSeed = [buildCurrentUserMessage(11, 'پیام برای هدایت')]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        template: '<button class="forward-selected-action" @click="$emit(\'forward-selected\')">forward</button>',
      },
      ChatForwardModal: {
        props: ['showForwardModal'],
        template: '<div class="forward-modal-state">{{ String(showForwardModal) }}</div>',
      },
    })
    await flushPromises()

    await wrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    await wrapper.get('.forward-selected-action').trigger('click')
    await flushPromises()

    expect(wrapper.get('.forward-modal-state').text()).toBe('true')

    wrapper.unmount()
  })

  it('forwards a full image album to both direct and group targets with fresh album metadata', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'forwarded-album-1' })

    chatViewMocks.messagesSeed = [
      buildImageMessage({
        id: 21,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-1', album_id: 'source-album', album_index: 0 }),
      }),
      buildImageMessage({
        id: 22,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-2', album_id: 'source-album', album_index: 1 }),
      }),
    ]
    chatViewMocks.conversationsSeed = [
      {
        id: 66,
        other_user_id: 66,
        other_user_name: 'Direct Target',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
      {
        id: 88,
        other_user_id: -88,
        other_user_name: 'Group Target',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 88,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Source User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: `<div>
          <template v-if="msg.type === 'album'">
            <button class="select-message-action" @click="$emit('select', msg.messages[0])">{{ msg.messages[0].id }}</button>
            <button class="select-message-action" @click="$emit('select', msg.messages[1])">{{ msg.messages[1].id }}</button>
          </template>
          <button v-else class="select-message-action" @click="$emit('select', msg)">{{ msg.id }}</button>
        </div>`,
      },
      ChatInputBar: {
        template: '<button class="forward-selected-action" @click="$emit(\'forward-selected\')">forward</button>',
      },
      ChatForwardModal: {
        props: ['showForwardModal'],
        template: `<div>
          <span class="forward-modal-state">{{ String(showForwardModal) }}</span>
          <button class="emit-forward-targets" @click="$emit('forward-to', [
            { kind: 'user', id: 66, title: 'Direct Target' },
            { kind: 'group', id: 88, title: 'Group Target' }
          ])">send</button>
        </div>`,
      },
    })
    await flushPromises()

    const selectButtons = wrapper.findAll('.select-message-action')
    await selectButtons[0]!.trigger('click')
    await flushPromises()
    await wrapper.get('.forward-selected-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.forward-modal-state').text()).toBe('true')

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.emit-forward-targets').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledTimes(4)
    const calls = chatViewMocks.apiFetchMock.mock.calls as unknown as Array<[string, { body: string }]>
    expect(calls.map(call => call[0])).toEqual([
      '/chat/send',
      '/chat/send',
      '/chat/rooms/88/send',
      '/chat/rooms/88/send',
    ])
    const directBodies = calls.slice(0, 2).map(call => JSON.parse(call[1].body))
    const groupBodies = calls.slice(2).map(call => JSON.parse(call[1].body))
    expect(directBodies.map(body => body.receiver_id)).toEqual([66, 66])
    expect([...directBodies, ...groupBodies].map(body => body.forwarded_from_id)).toEqual([55, 55, 55, 55])
    expect([...directBodies, ...groupBodies].map(body => JSON.parse(body.content).album_id)).toEqual([
      'forwarded-album-1',
      'forwarded-album-1',
      'forwarded-album-1',
      'forwarded-album-1',
    ])
    expect([...directBodies, ...groupBodies].map(body => JSON.parse(body.content).album_index)).toEqual([0, 1, 0, 1])
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('strips album metadata when forwarding only one media item from an album', async () => {
    chatViewMocks.messagesSeed = [
      buildImageMessage({
        id: 21,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-1', album_id: 'source-album', album_index: 0 }),
      }),
    ]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Source User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        template: '<button class="forward-selected-action" @click="$emit(\'forward-selected\')">forward</button>',
      },
      ChatForwardModal: {
        template: '<button class="emit-forward-target" @click="$emit(\'forward-to\', { kind: \'user\', id: 66, title: \'Direct Target\' })">send</button>',
      },
    })
    await flushPromises()

    await wrapper.findAll('.select-message-action')[0]!.trigger('click')
    await flushPromises()
    await wrapper.get('.forward-selected-action').trigger('click')
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.emit-forward-target').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledTimes(1)
    const body = JSON.parse((chatViewMocks.apiFetchMock.mock.calls as unknown as Array<[string, { body: string }]>)[0]![1].body)
    const content = JSON.parse(body.content)
    expect(content.file_id).toBe('img-1')
    expect(content.album_id).toBeUndefined()
    expect(content.album_index).toBeUndefined()

    wrapper.unmount()
  })

  it('alerts and skips forwarding when every selected target is unsupported', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    chatViewMocks.messagesSeed = [buildCurrentUserMessage(11, 'پیام برای هدایت')]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Source User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        template: '<button class="forward-selected-action" @click="$emit(\'forward-selected\')">forward</button>',
      },
      ChatForwardModal: {
        template: '<button class="emit-unsupported-forward" @click="$emit(\'forward-to\', { kind: \'bot\', id: 3, title: \'Unsupported\' })">send</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    await wrapper.get('.forward-selected-action').trigger('click')
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.emit-unsupported-forward').trigger('click')
    await flushPromises()

    expect(alertSpy).toHaveBeenCalledWith('هدایت پیام به این مقصد هنوز فعال نشده است')
    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalled()

    alertSpy.mockRestore()
    wrapper.unmount()
  })

  it('reports mixed unsupported forward targets after sending to supported targets', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    chatViewMocks.messagesSeed = [buildCurrentUserMessage(11, 'پیام برای هدایت')]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Source User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        template: '<button class="forward-selected-action" @click="$emit(\'forward-selected\')">forward</button>',
      },
      ChatForwardModal: {
        template: `<button class="emit-mixed-forward" @click="$emit('forward-to', [
          { kind: 'user', id: 66, title: 'Direct Target' },
          { kind: 'bot', id: 3, title: 'Unsupported' }
        ])">send</button>`,
      },
    })
    await flushPromises()

    await wrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    await wrapper.get('.forward-selected-action').trigger('click')
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.emit-mixed-forward').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledTimes(1)
    expect(alertSpy).toHaveBeenCalledWith('برخی مقصدها هنوز برای هدایت پشتیبانی نمی‌شوند')

    alertSpy.mockRestore()
    wrapper.unmount()
  })

  it('reports total and partial forward failures without blocking successful targets', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    chatViewMocks.messagesSeed = [buildCurrentUserMessage(11, 'پیام برای هدایت')]

    const failedWrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Source User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        template: '<button class="forward-selected-action" @click="$emit(\'forward-selected\')">forward</button>',
      },
      ChatForwardModal: {
        template: '<button class="emit-failed-forward" @click="$emit(\'forward-to\', { kind: \'user\', id: 66, title: \'Direct Target\' })">send</button>',
      },
    })
    await flushPromises()

    await failedWrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    await failedWrapper.get('.forward-selected-action').trigger('click')
    await flushPromises()
    chatViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('send failed'))

    await failedWrapper.get('.emit-failed-forward').trigger('click')
    await flushPromises()

    expect(alertSpy).toHaveBeenCalledWith('خطا در هدایت پیام‌ها')
    failedWrapper.unmount()

    alertSpy.mockClear()
    chatViewMocks.messagesSeed = [buildCurrentUserMessage(12, 'پیام دوم')]
    const partialWrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Source User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        template: '<button class="forward-selected-action" @click="$emit(\'forward-selected\')">forward</button>',
      },
      ChatForwardModal: {
        template: `<button class="emit-partial-forward" @click="$emit('forward-to', [
          { kind: 'user', id: 66, title: 'Direct Target' },
          { kind: 'group', id: 88, title: 'Group Target' }
        ])">send</button>`,
      },
    })
    await flushPromises()

    await partialWrapper.get('.select-message-action').trigger('click')
    await flushPromises()
    await partialWrapper.get('.forward-selected-action').trigger('click')
    await flushPromises()
    chatViewMocks.apiFetchMock
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error('group failed'))

    await partialWrapper.get('.emit-partial-forward').trigger('click')
    await flushPromises()

    expect(alertSpy).toHaveBeenCalledWith('بخشی از پیام‌ها برای این مقاصد هدایت نشدند: Group Target')
    expect(errorSpy).toHaveBeenCalled()

    partialWrapper.unmount()
    alertSpy.mockRestore()
    errorSpy.mockRestore()
  })

  it('routes recovery actions through the session recovery API and reports failures inline', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    chatViewMocks.messagesSeed = [buildMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Recovery User',
    }, {
      ChatMessageItem: {
        template: `<div>
          <button class="recovery-approve" @click="$emit('recovery-action', { action: 'approve', recoveryId: 'rec-1' })">approve</button>
          <button class="recovery-identity" @click="$emit('recovery-action', { action: 'request_identity', recoveryId: 'rec-2' })">identity</button>
        </div>`,
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.apiFetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({}) })
    await wrapper.get('.recovery-approve').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/sessions/recovery/rec-1/approve', { method: 'POST' })
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, true)

    chatViewMocks.apiFetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({ detail: 'نیاز به مدرک' }) })
    await wrapper.get('.recovery-identity').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/sessions/recovery/rec-2/request-identity', { method: 'POST' })
    expect(alertSpy).toHaveBeenCalledWith('نیاز به مدرک')

    alertSpy.mockRestore()
    wrapper.unmount()
  })

  it('deletes selected messages from selection mode and clears the selection', async () => {
    chatViewMocks.messagesSeed = [
      buildCurrentUserMessage(11, 'پیام اول'),
      buildCurrentUserMessage(12, 'پیام دوم'),
    ]
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="select-message-action" @click="$emit(\'select\', msg)">{{ msg.id }}</button>',
      },
      ChatInputBar: {
        props: ['selectedMessagesCount', 'isSelectionMode'],
        template: '<div><span class="chat-input-bar-state">{{ String(isSelectionMode) }}|{{ selectedMessagesCount }}</span><button class="delete-selected-action" @click="$emit(\'delete-selected\')">delete</button></div>',
      },
    })
    await flushPromises()

    const selectButtons = wrapper.findAll('.select-message-action')
    await selectButtons[0]!.trigger('click')
    await selectButtons[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('true|2')

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.delete-selected-action').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(chatViewMocks.apiFetchMock).toHaveBeenNthCalledWith(1, '/chat/messages/11', { method: 'DELETE' })
    expect(chatViewMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/chat/messages/12', { method: 'DELETE' })
    expect(wrapper.get('.chat-input-bar-state').text()).toBe('false|0')

    confirmSpy.mockRestore()
    wrapper.unmount()
  })

  it('selects a conversation from the list and clears it through the header back action', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Target User',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]

    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        props: ['conversations'],
        template: '<button class="select-conversation-action" @click="$emit(\'select-conversation\', conversations[0])">select</button>',
      },
    })
    await flushPromises()

    chatViewMocks.loadMessagesMock.mockClear()
    await wrapper.get('.select-conversation-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55)
    expect(chatViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      path: '/chat',
      query: {
        user_id: '55',
        user_name: 'Target User',
      },
    })

    chatViewMocks.routerReplaceMock.mockClear()
    await wrapper.get('.chat-header-back').trigger('click')
    await flushPromises()

    expect(chatViewMocks.discardBackStateMock).toHaveBeenCalled()
    expect(chatViewMocks.popBackStateMock).not.toHaveBeenCalled()
    expect(chatViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      path: '/chat',
      query: {},
    })
    expect(wrapper.find('.select-conversation-action').exists()).toBe(true)

    wrapper.unmount()
  })

  it('opens a newly created group conversation and pushes a back-state cleanup callback', async () => {
    const wrapper = await mountChatView({}, {
      ChatGroupManagerModal: {
        template: '<button class="group-created-action" @click="$emit(\'created\', { id: 88, title: \'گروه تازه\' })">created</button>',
      },
    })
    await flushPromises()

    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.pushBackStateMock.mockClear()

    await wrapper.get('.group-created-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-88)
    expect(chatViewMocks.pushBackStateMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('updates the selected group title when the group manager emits an update', async () => {
    const wrapper = await mountChatView({
      targetUserId: -88,
      targetUserName: 'عنوان قدیمی',
    }, {
      ChatHeader: {
        props: ['selectedUserName'],
        template: '<div class="chat-header-name">{{ selectedUserName }}</div>',
      },
      ChatGroupManagerModal: {
        template: '<button class="group-updated-action" @click="$emit(\'updated\', { id: 88, title: \'عنوان جدید\' })">updated</button>',
      },
    })
    await flushPromises()

    expect(wrapper.get('.chat-header-name').text()).toContain('عنوان قدیمی')

    chatViewMocks.conversationsSeed = [
      {
        id: 88,
        other_user_id: -88,
        other_user_name: 'عنوان جدید',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 88,
      },
    ]
    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.group-updated-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.chat-header-name').text()).toContain('عنوان جدید')

    wrapper.unmount()
  })

  it('clears the active group conversation when the group manager emits left', async () => {
    const wrapper = await mountChatView({
      targetUserId: -88,
      targetUserName: 'گروه فعال',
    }, {
      ChatGroupManagerModal: {
        template: '<button class="group-left-action" @click="$emit(\'left\', 88)">left</button>',
      },
    })
    await flushPromises()

    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.group-left-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.open-new-conversation').exists()).toBe(true)

    wrapper.unmount()
  })

  it('opens an existing channel conversation from the channel manager', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال موجود',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -23,
      targetUserName: 'کانال موجود',
    }, {
      ChatHeader: {
        template: '<div><button class="open-room-manager" @click="$emit(\'manage-room\')">manage</button></div>',
      },
      CreateChannelView: {
        template: '<button class="open-channel-action" @click="$emit(\'open-channel\', { chatId: 23, title: \'کانال موجود\' })">open</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.open-room-manager').trigger('click')
    await flushPromises()

    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    await wrapper.get('.open-channel-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-23)

    wrapper.unmount()
  })

  it('opens a fallback channel conversation when the manager target is not in the conversation list yet', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -23,
      targetUserName: 'کانال فعال',
    }, {
      ChatHeader: {
        template: '<div><button class="open-room-manager" @click="$emit(\'manage-room\')">manage</button></div>',
      },
      CreateChannelView: {
        template: '<button class="open-channel-fallback-action" @click="$emit(\'open-channel\', { chatId: 44, title: \'کانال تازه\' })">open</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.open-room-manager').trigger('click')
    await flushPromises()

    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    await wrapper.get('.open-channel-fallback-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-44)

    wrapper.unmount()
  })

  it('refreshes the selected channel title when the channel manager requests a conversation refresh', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -23,
      targetUserName: 'کانال فعال',
    }, {
      ChatHeader: {
        props: ['selectedUserName'],
        template: '<div><span class="chat-header-name">{{ selectedUserName }}</span><button class="open-room-manager" @click="$emit(\'manage-room\')">manage</button></div>',
      },
      CreateChannelView: {
        template: '<button class="refresh-channel-action" @click="$emit(\'refresh-conversations\')">refresh</button>',
      },
    })
    await flushPromises()

    expect(wrapper.get('.chat-header-name').text()).toContain('کانال فعال')

    await wrapper.get('.open-room-manager').trigger('click')
    await flushPromises()

    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال به‌روزشده',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.refresh-channel-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.chat-header-name').text()).toContain('کانال به‌روزشده')

    wrapper.unmount()
  })

  it('opens the selected group room manager from the header manage-room action', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 88,
        other_user_id: -88,
        other_user_name: 'گروه فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 88,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -88,
      targetUserName: 'گروه فعال',
    }, {
      ChatHeader: {
        template: '<div><button class="open-room-manager" @click="$emit(\'manage-room\')">manage</button></div>',
      },
      ChatGroupManagerModal: {
        props: ['show'],
        template: '<div class="group-manager-open">{{ String(show) }}</div>',
      },
    })
    await flushPromises()

    expect(wrapper.get('.group-manager-open').text()).toBe('false')

    await wrapper.get('.open-room-manager').trigger('click')
    await flushPromises()

    expect(wrapper.get('.group-manager-open').text()).toBe('true')

    wrapper.unmount()
  })

  it('opens channel creation only for super admins and refreshes the selected title when the channel manager closes', async () => {
    const deniedWrapper = await mountChatView({ currentUserRole: 'عادی' }, {
      ChatHeader: {
        template: '<div><button class="open-channel-creation" @click="$emit(\'create-channel\')">channel</button></div>',
      },
    })
    await flushPromises()

    await deniedWrapper.get('.open-channel-creation').trigger('click')
    await flushPromises()

    expect(deniedWrapper.find('.channel-manager-overlay').exists()).toBe(false)
    deniedWrapper.unmount()

    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({
      currentUserRole: 'مدیر ارشد',
      targetUserId: -23,
      targetUserName: 'کانال فعال',
    }, {
      ChatHeader: {
        props: ['selectedUserName'],
        template: `<div>
          <span class="chat-header-name">{{ selectedUserName }}</span>
          <button class="open-room-manager" @click="$emit('manage-room')">manage</button>
          <button class="open-channel-creation" @click="$emit('create-channel')">channel</button>
        </div>`,
      },
      CreateChannelView: {
        props: ['initialChannelId'],
        template: `<div>
          <span class="channel-manager-id">{{ initialChannelId ?? 'none' }}</span>
          <button class="close-channel-manager" @click="$emit('close')">close</button>
        </div>`,
      },
    })
    await flushPromises()

    await wrapper.get('.open-channel-creation').trigger('click')
    await flushPromises()
    expect(wrapper.find('.channel-manager-overlay').exists()).toBe(true)
    expect(wrapper.get('.channel-manager-id').text()).toBe('none')

    await wrapper.get('.close-channel-manager').trigger('click')
    await flushPromises()
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()
    expect(wrapper.find('.channel-manager-overlay').exists()).toBe(false)

    await wrapper.get('.open-room-manager').trigger('click')
    await flushPromises()
    expect(wrapper.get('.channel-manager-id').text()).toBe('23')

    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال به‌روزشده',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.close-channel-manager').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.channel-manager-overlay').exists()).toBe(false)
    expect(wrapper.get('.chat-header-name').text()).toContain('کانال به‌روزشده')

    wrapper.unmount()
  })

  it('clears attachment and sticker overlays when selecting a channel conversation from the list', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        props: ['conversations'],
        template: '<button class="select-channel-conversation" @click="$emit(\'select-conversation\', conversations[0])">select</button>',
      },
    })
    await flushPromises()
    const hooks = getChatViewTestHooks(wrapper)

    hooks.state.showAttachmentMenu.value = true
    hooks.state.showStickerPicker.value = true
    chatViewMocks.loadMessagesMock.mockClear()

    await wrapper.get('.select-channel-conversation').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-23)
    expect(hooks.state.selectedUserId.value).toBe(-23)
    expect(hooks.state.showAttachmentMenu.value).toBe(false)
    expect(hooks.state.showStickerPicker.value).toBe(false)

    wrapper.unmount()
  })

  it('clears the active channel conversation when the channel manager emits left', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -23,
      targetUserName: 'کانال فعال',
    }, {
      ChatHeader: {
        template: '<div><button class="open-room-manager" @click="$emit(\'manage-room\')">manage</button></div>',
      },
      CreateChannelView: {
        template: '<button class="channel-left-action" @click="$emit(\'left\', 23)">left</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.open-room-manager').trigger('click')
    await flushPromises()

    chatViewMocks.loadConversationsMock.mockClear()
    await wrapper.get('.channel-left-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.open-new-conversation').exists()).toBe(true)

    wrapper.unmount()
  })

  it('shows an inline toast when a named-room action is missing chat metadata', async () => {
    const wrapper = await mountChatView({}, {
      ChatConversationList: {
        template: "<button class='invalid-room-action' @click=\"$emit('conversation-action', { action: 'leave', conv: { other_user_id: -44, other_user_name: 'Broken Group', room_kind: 'group' } })\">broken</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.invalid-room-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('اطلاعات این گفتگو کامل نیست. لطفا دوباره تلاش کنید.')

    wrapper.unmount()
  })

  it('toggles attachments, clears sticker mode, and reserves the composer caption for the first selected media item', async () => {
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatInputBar: {
        props: ['modelValue', 'stickerPickerOpen'],
        template: "<div><span class='composer-state'>{{ modelValue }}|{{ String(stickerPickerOpen) }}</span><button class='set-caption' @click=\"$emit('update:modelValue', 'کپشن آماده')\">caption</button><button class='open-sticker' @click=\"$emit('update:stickerPickerOpen', true)\">sticker</button><button class='toggle-attachment' @click=\"$emit('toggle-attachment')\">attach</button></div>",
      },
      AttachmentMenu: {
        props: ['modelValue'],
        template: "<div><span class='attachment-open'>{{ String(modelValue) }}</span><button class='select-media-action' @click='emitMedia'>media</button></div>",
        methods: {
          emitMedia(this: { $emit: (event: string, ...args: unknown[]) => void }) {
            this.$emit('select-media', new File(['img'], 'photo.png', { type: 'image/png' }), 'album-1', 0, 2)
          },
        },
      },
    })
    await flushPromises()

    await wrapper.get('.set-caption').trigger('click')
    await wrapper.get('.open-sticker').trigger('click')
    await flushPromises()
    expect(wrapper.get('.composer-state').text()).toBe('کپشن آماده|true')

    await wrapper.get('.toggle-attachment').trigger('click')
    await flushPromises()

    expect(wrapper.get('.attachment-open').text()).toBe('true')
    expect(wrapper.get('.composer-state').text()).toBe('کپشن آماده|false')

    await wrapper.get('.select-media-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.handleMediaUploadWrapperMock).toHaveBeenCalledTimes(1)
    const mediaCall = chatViewMocks.handleMediaUploadWrapperMock.mock.calls[0]
    expect(mediaCall?.[0]).toBeInstanceOf(File)
    expect(mediaCall?.[1]).toBe('album-1')
    expect(mediaCall?.[2]).toBe(0)
    expect(mediaCall?.[3]).toBe(2)
    expect(mediaCall?.[4]).toMatchObject({
      caption: 'کپشن آماده',
      roomKindOverride: 'direct',
    })

    const onCaptionApplied = mediaCall?.[4]?.onCaptionApplied as (() => void) | undefined
    expect(onCaptionApplied).toBeTypeOf('function')
    onCaptionApplied?.()
    await flushPromises()

    expect(wrapper.get('.composer-state').text()).toBe('|false')

    wrapper.unmount()
  })

  it('keeps the reserved caption after the attachment sheet closes before delayed media selection', async () => {
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatInputBar: {
        template: "<button class='toggle-attachment' @click=\"$emit('toggle-attachment', 'کپشن آماده')\">attach</button>",
      },
      AttachmentMenu: {
        props: ['modelValue'],
        template: "<div><span class='attachment-open'>{{ String(modelValue) }}</span><button class='close-attachment' @click=\"$emit('update:modelValue', false)\">close</button><button class='select-media-after-close' @click='emitMedia'>media</button></div>",
        methods: {
          emitMedia(this: { $emit: (event: string, ...args: unknown[]) => void }) {
            this.$emit('select-media', new File(['img'], 'photo.png', { type: 'image/png' }), null, 0, 1)
          },
        },
      },
    })
    await flushPromises()

    await wrapper.get('.toggle-attachment').trigger('click')
    await flushPromises()
    expect(wrapper.get('.attachment-open').text()).toBe('true')

    await wrapper.get('.close-attachment').trigger('click')
    await flushPromises()
    expect(wrapper.get('.attachment-open').text()).toBe('false')

    await wrapper.get('.select-media-after-close').trigger('click')
    await flushPromises()

    expect(chatViewMocks.handleMediaUploadWrapperMock).toHaveBeenCalledTimes(1)
    const mediaCall = chatViewMocks.handleMediaUploadWrapperMock.mock.calls[0]
    expect(mediaCall?.[4]).toMatchObject({
      caption: 'کپشن آماده',
      roomKindOverride: 'direct',
    })

    wrapper.unmount()
  })

  it('sends selected files through the document-upload path', async () => {
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      AttachmentMenu: {
        template: "<button class='select-file-action' @click='emitFile'>file</button>",
        methods: {
          emitFile(this: { $emit: (event: string, ...args: unknown[]) => void }) {
            this.$emit('select-file', new File(['doc'], 'contract.pdf', { type: 'application/pdf' }))
          },
        },
      },
    })
    await flushPromises()

    await wrapper.get('.select-file-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.handleMediaUploadWrapperMock).toHaveBeenCalledWith(
      expect.any(File),
      null,
      0,
      1,
      expect.objectContaining({ sendAsDocument: true, roomKindOverride: 'direct' }),
    )

    wrapper.unmount()
  })

  it('blocks attachment toggles for read-only channels', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فقط‌خواندنی',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
        can_send: false,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -23,
      targetUserName: 'کانال فقط‌خواندنی',
    }, {
      ChatInputBar: {
        template: "<button class='toggle-attachment' @click=\"$emit('toggle-attachment')\">attach</button>",
      },
      AttachmentMenu: {
        props: ['modelValue'],
        template: "<div class='attachment-open'>{{ String(modelValue) }}</div>",
      },
    })
    await flushPromises()

    await wrapper.get('.toggle-attachment').trigger('click')
    await flushPromises()

    expect(wrapper.get('.attachment-open').text()).toBe('false')

    wrapper.unmount()
  })

  it('routes direct voice recordings through the upload wrapper with duration metadata', async () => {
    const voiceBlob = new Blob(['voice'], { type: 'audio/webm' })
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatInputBar: {
        props: ['allowVoiceRecording'],
        template: "<div><span class='voice-allowed'>{{ String(allowVoiceRecording) }}</span><button class='send-voice-action' @click='emitVoice'>voice</button></div>",
        methods: {
          emitVoice(this: { $emit: (event: string, ...args: unknown[]) => void }) {
            this.$emit('send-voice', voiceBlob, 1450)
          },
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('.voice-allowed').text()).toBe('true')

    await wrapper.get('.send-voice-action').trigger('click')
    await flushPromises()

    const voiceCall = chatViewMocks.handleMediaUploadWrapperMock.mock.calls[0]
    expect(voiceCall?.[0]).toBeInstanceOf(File)
    expect((voiceCall?.[0] as File).type).toBe('audio/webm')
    expect((voiceCall?.[0] as File & { durationMs?: number }).durationMs).toBe(1450)
    expect(voiceCall?.slice(1)).toEqual([
      null,
      0,
      1,
      { roomKindOverride: 'direct' },
    ])

    wrapper.unmount()
  })

  it('sends direct locations through the chat API', async () => {
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      AttachmentMenu: {
        template: "<button class='select-location-action' @click=\"$emit('select-location', 35.7, 51.4)\">location</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.apiFetchMock.mockResolvedValue(buildMessage({
      id: 91,
      sender_id: 7,
      receiver_id: 55,
      message_type: 'location',
      content: JSON.stringify({ lat: 35.7, lng: 51.4 }),
    }))

    await wrapper.get('.select-location-action').trigger('click')
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/send', {
      method: 'POST',
      body: JSON.stringify({
        receiver_id: 55,
        content: JSON.stringify({ lat: 35.7, lng: 51.4 }),
        message_type: 'location',
      }),
    })

    wrapper.unmount()
  })

  it('blocks group location sends with the phase-gate alert', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    chatViewMocks.conversationsSeed = [
      {
        id: 88,
        other_user_id: -88,
        other_user_name: 'گروه فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 88,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -88,
      targetUserName: 'گروه فعال',
    }, {
      AttachmentMenu: {
        template: "<button class='select-location-action' @click=\"$emit('select-location', 35.7, 51.4)\">location</button>",
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.select-location-action').trigger('click')
    await flushPromises()

    expect(alertSpy).toHaveBeenCalledWith('ارسال موقعیت در گروه در این فاز هنوز فعال نشده است.')
    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalled()

    alertSpy.mockRestore()
    wrapper.unmount()
  })

  it('bridges focus helpers, closes overlays from back-state callbacks, and handles swipe-to-reply touch hooks', async () => {
    const focusInputMock = vi.fn()
    const adjustTextareaHeightMock = vi.fn()
    const message = buildMessage({ id: 501, sender_id: 55, receiver_id: 7 })
    chatViewMocks.messagesSeed = [message]

    const wrapper = await mountChatView({}, {
      ChatHeader: {
        props: ['isSearchActive'],
        template: "<div><span class='search-open-state'>{{ String(isSearchActive) }}</span><button class='toggle-search-action' @click=\"$emit('toggle-search')\">search</button></div>",
      },
      ChatInputBar: {
        props: ['replyingToMessage'],
        methods: {
          focusInput: focusInputMock,
          adjustTextareaHeight: adjustTextareaHeightMock,
        },
        template: '<div class="chat-input-bridge-stub">{{ replyingToMessage?.id ?? "none" }}</div>',
      },
      ChatNewConversationModal: {
        props: ['show'],
        template: '<div class="new-chat-modal-state">{{ String(show) }}</div>',
      },
    })
    await flushPromises()

    await wrapper.get('.open-new-conversation').trigger('click')
    await flushPromises()
    expect(wrapper.get('.new-chat-modal-state').text()).toBe('true')

    const newChatBackCallback = chatViewMocks.pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(newChatBackCallback).toBeTypeOf('function')
    newChatBackCallback?.()
    await flushPromises()
    expect(wrapper.get('.new-chat-modal-state').text()).toBe('false')

    await wrapper.get('.toggle-search-action').trigger('click')
    await flushPromises()
    expect(wrapper.get('.search-open-state').text()).toBe('true')

    const searchBackCallback = chatViewMocks.pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(searchBackCallback).toBeTypeOf('function')
    searchBackCallback?.()
    await flushPromises()
    expect(wrapper.get('.search-open-state').text()).toBe('false')

    await getExposedStartNewChat(wrapper)(55, 'Target User')
    await flushPromises()

    chatViewMocks.messagesLogicOptions.focusMessageInput({ cursorToEnd: true })
    chatViewMocks.messagesLogicOptions.adjustTextareaHeight()
    expect(focusInputMock).toHaveBeenCalledWith({ cursorToEnd: true })
    expect(adjustTextareaHeightMock).toHaveBeenCalledTimes(1)

    const hooks = getChatViewTestHooks(wrapper)
    hooks.handleTouchStart({ touches: [{ clientX: 120 }] } as any, message)
    hooks.state.longPressTimer.value = window.setTimeout(() => {}, 0)
    hooks.handleTouchMove({ touches: [{ clientX: 260 }] } as any, message)
    hooks.handleTouchEnd({} as any, message)
    await flushPromises()

    expect(hooks.state.longPressTimer.value).toBeNull()
    expect(wrapper.get('.chat-input-bridge-stub').text()).toContain('501')

    wrapper.unmount()
  })

  it('renders read-only room composer props and the album-forward selection bar', async () => {
    const albumMessages = [
      buildImageMessage({
        id: 71,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-a', album_id: 'album-forward', album_index: 0 }),
      }),
      buildImageMessage({
        id: 72,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-b', album_id: 'album-forward', album_index: 1 }),
      }),
    ]
    chatViewMocks.messagesSeed = albumMessages
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فقط‌خواندنی',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
        can_send: false,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -23,
      targetUserName: 'کانال فقط‌خواندنی',
    }, {
      ChatInputBar: {
        props: ['isReadOnly', 'readOnlyBannerText', 'disableRichComposer', 'allowVoiceRecording'],
        template: '<div class="room-composer-state">{{ String(isReadOnly) }}|{{ readOnlyBannerText }}|{{ String(disableRichComposer) }}|{{ String(allowVoiceRecording) }}</div>',
      },
      AttachmentMenu: {
        props: ['allowLocation'],
        template: '<div class="attachment-location-flag">{{ String(allowLocation) }}</div>',
      },
      ChatForwardModal: {
        props: ['showForwardModal'],
        template: '<div class="forward-modal-state">{{ String(showForwardModal) }}</div>',
      },
    })
    await flushPromises()

    expect(wrapper.get('.room-composer-state').text()).toBe('true|فقط مدیران کانال امکان ارسال پیام دارند.|true|false')
    expect(wrapper.get('.attachment-location-flag').text()).toBe('false')

    const hooks = getChatViewTestHooks(wrapper)
    hooks.state.selectedMessages.value = [71, 72]
    hooks.state.selectionModePurpose.value = 'album-forward'
    hooks.state.activeAlbumSelectionId.value = 'album-forward'
    await flushPromises()

    expect(wrapper.text()).toContain('2 مدیا برای هدایت انتخاب شده')
    await wrapper.find('.album-download-selection-bar .selection-action-btn.primary').trigger('click')
    await flushPromises()
    expect(wrapper.get('.forward-modal-state').text()).toBe('true')

    wrapper.unmount()
  })

  it('falls back to the current user profile from non-direct rooms and forwards manager profile opens', async () => {
    vi.useFakeTimers()
    chatViewMocks.conversationsSeed = [
      {
        id: 88,
        other_user_id: -88,
        other_user_name: 'گروه فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 88,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -88,
      targetUserName: 'گروه فعال',
    }, {
      ChatHeader: {
        template: '<div><button class="chat-header-view-profile" @click="$emit(\'view-profile\')">profile</button><button class="open-room-manager" @click="$emit(\'manage-room\')">manage</button></div>',
      },
      ChatGroupManagerModal: {
        template: '<button class="manager-open-profile" @click="$emit(\'open-public-profile\', { id: 77, account_name: \'managed-user\' })">open profile</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.chat-header-view-profile').trigger('click')
    await vi.runAllTimersAsync()
    expect(chatViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '7' },
      query: undefined,
    })

    chatViewMocks.routerPushMock.mockClear()
    await wrapper.get('.open-room-manager').trigger('click')
    await flushPromises()
    await wrapper.get('.manager-open-profile').trigger('click')
    await vi.runAllTimersAsync()

    expect(chatViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '77' },
      query: { account_name: 'managed-user' },
    })

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('falls back to the selected direct conversation profile when additive metadata is missing', async () => {
    vi.useFakeTimers()
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'مخاطب مستقیم',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'مخاطب مستقیم',
    })
    await flushPromises()

    await wrapper.get('.chat-header-view-profile').trigger('click')
    await vi.runAllTimersAsync()

    expect(chatViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '55' },
      query: { account_name: 'مخاطب مستقیم' },
    })

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('opens and closes the location modal for valid payloads and ignores invalid ones', async () => {
    chatViewMocks.messagesSeed = [buildMessage({
      message_type: 'location',
      content: JSON.stringify({ latitude: 35.7, longitude: 51.4, snapshot_id: 'snap-1' }),
    })]
    const validWrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="location-click-action" @click="$emit(\'location-click\', msg)">loc</button>',
      },
      ChatLocationModal: {
        props: ['location'],
        template: '<div><span class="location-state">{{ location ? `${location.lat},${location.lng}` : "none" }}</span><button class="close-location" @click="$emit(\'close\')">close</button></div>',
      },
    })
    await flushPromises()

    await validWrapper.get('.location-click-action').trigger('click')
    await flushPromises()
    expect(validWrapper.get('.location-state').text()).toBe('35.7,51.4')

    await validWrapper.get('.close-location').trigger('click')
    await flushPromises()
    expect(validWrapper.get('.location-state').text()).toBe('none')
    validWrapper.unmount()

    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    chatViewMocks.messagesSeed = [buildMessage({
      id: 77,
      message_type: 'location',
      content: 'not-json',
    })]
    const invalidWrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="location-click-action" @click="$emit(\'location-click\', msg)">loc</button>',
      },
      ChatLocationModal: {
        props: ['location'],
        template: '<div class="location-state">{{ location ? "set" : "none" }}</div>',
      },
    })
    await flushPromises()

    await invalidWrapper.get('.location-click-action').trigger('click')
    await flushPromises()
    expect(errorSpy).toHaveBeenCalledWith('Failed to parse location data')
    expect(invalidWrapper.get('.location-state').text()).toBe('none')

    errorSpy.mockRestore()
    invalidWrapper.unmount()
  })

  it('routes lightbox navigation, reply, and forward actions back into chat state', async () => {
    chatViewMocks.messagesSeed = [buildImageMessage()]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatInputBar: {
        props: ['replyingToMessage'],
        template: '<div class="reply-state">{{ replyingToMessage?.id ?? "none" }}</div>',
      },
      ChatForwardModal: {
        props: ['showForwardModal'],
        template: '<div class="forward-modal-state">{{ String(showForwardModal) }}</div>',
      },
      ChatLightbox: {
        template: '<div><button class="lightbox-navigate" @click="$emit(\'navigate\', 3)">nav</button><button class="lightbox-reply" @click="$emit(\'reply\', 11)">reply</button><button class="lightbox-forward" @click="$emit(\'forward\', 11)">forward</button><button class="lightbox-close" @click="$emit(\'close\')">close</button></div>',
      },
    })
    await flushPromises()

    await wrapper.get('.lightbox-navigate').trigger('click')
    expect(chatViewMocks.setLightboxIndexMock).toHaveBeenCalledWith(3)

    await wrapper.get('.lightbox-reply').trigger('click')
    await flushPromises()
    expect(wrapper.get('.reply-state').text()).toBe('11')
    expect(chatViewMocks.closeLightboxMock).toHaveBeenCalledTimes(1)

    await wrapper.get('.lightbox-forward').trigger('click')
    await flushPromises()
    expect(wrapper.get('.forward-modal-state').text()).toBe('true')
    expect(chatViewMocks.closeLightboxMock).toHaveBeenCalledTimes(2)

    await wrapper.get('.lightbox-close').trigger('click')
    expect(chatViewMocks.closeLightboxMock).toHaveBeenCalledTimes(3)

    wrapper.unmount()
  })

  it('shares cached media from the lightbox and deletes owned media messages', async () => {
    const imageMessage = buildImageMessage({
      sender_id: 7,
      receiver_id: 55,
      created_at: new Date().toISOString(),
    })
    chatViewMocks.messagesSeed = [imageMessage]
    chatViewMocks.imageCacheState = { 'img-1': 'blob:cached-image' }
    chatViewMocks.shareFileMock.mockResolvedValue(true)

    const fetchMock = vi.fn(async () => new Response(new Blob(['cached-image'], { type: 'image/jpeg' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatLightbox: {
        template: '<div><button class="lightbox-share" @click="$emit(\'share\', 11)">share</button><button class="lightbox-delete" @click="$emit(\'delete\', 11)">delete</button></div>',
      },
    })
    await flushPromises()

    await wrapper.get('.lightbox-share').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('blob:cached-image')
    const seedCall = chatViewMocks.seedFileCacheMock.mock.calls[0]
    expect(seedCall?.[0]).toBe('img-1')
    expect(seedCall?.[1]).toBeTruthy()
    expect(seedCall?.[2]).toBe('01_img-1.jpg')
    expect(typeof seedCall?.[3]).toBe('string')
    expect(chatViewMocks.shareFileMock).toHaveBeenCalledWith(
      'img-1',
      '01_img-1.jpg',
      'image/jpeg',
      '/api/chat/files/img-1?token=jwt-token',
    )

    chatViewMocks.apiFetchMock.mockClear()
    await wrapper.get('.lightbox-delete').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/messages/11', { method: 'DELETE' })
    expect(chatViewMocks.closeLightboxMock).toHaveBeenCalled()

    confirmSpy.mockRestore()
    wrapper.unmount()
  })

  it('covers selection helper guards and lightbox/profile fallback branches through exposed hooks', async () => {
    vi.useFakeTimers()
    const textMessage = buildCurrentUserMessage(11, 'پیام متنی')
    const imageMessage = buildImageMessage({
      id: 12,
      sender_id: 7,
      receiver_id: 55,
      created_at: new Date().toISOString(),
      content: JSON.stringify({ file_id: 'img-12' }),
    })
    chatViewMocks.messagesSeed = [textMessage, imageMessage]
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'مخاطب مستقیم',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]
    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.closeLightboxMock.mockClear()
    chatViewMocks.seedFileCacheMock.mockClear()
    chatViewMocks.shareFileMock.mockClear()
    const clipboardWriteText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: clipboardWriteText },
    })

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'مخاطب مستقیم',
    }, {
      ChatInputBar: {
        props: ['replyingToMessage'],
        template: '<div class="reply-state">{{ replyingToMessage?.id ?? "none" }}</div>',
      },
    })
    await flushPromises()
    const hooks = getChatViewTestHooks(wrapper)
    chatViewMocks.apiFetchMock.mockClear()

    await hooks.handleDeleteSelected()
    await flushPromises()
    expect(chatViewMocks.apiFetchMock).not.toHaveBeenCalled()

    hooks.state.selectedMessages.value = [11]
    await hooks.handleDownloadSelectedAlbumMessages()
    await flushPromises()
    expect(hooks.state.selectedMessages.value).toEqual([])

    hooks.state.selectedMessages.value = [12, 11]
    hooks.handleCopySelected()
    await flushPromises()
    expect(clipboardWriteText).toHaveBeenCalledWith('پیام متنی')
    expect(hooks.state.selectedMessages.value).toEqual([])

    hooks.handleReplySelected()
    expect(wrapper.get('.reply-state').text()).toBe('none')
    hooks.state.selectedMessages.value = [11]
    hooks.handleReplySelected()
    await flushPromises()
    expect(wrapper.get('.reply-state').text()).toBe('11')
    expect(hooks.state.selectedMessages.value).toEqual([])

    hooks.openForwardModal()
    expect(hooks.state.showForwardModal.value).toBe(false)
    hooks.state.selectedMessages.value = [12, 11]
    hooks.openForwardModal()
    expect(hooks.state.showForwardModal.value).toBe(true)
    expect(hooks.state.forwardMessageIds.value).toEqual([11, 12])
    hooks.closeForwardModal()

    chatViewMocks.routerPushMock.mockClear()
    hooks.openPublicProfile({ id: 0, account_name: 'invalid' })
    hooks.openPublicProfile(undefined)
    await vi.runAllTimersAsync()
    expect(chatViewMocks.routerPushMock).not.toHaveBeenCalled()

    hooks.handleLightboxReply(999)
    expect(chatViewMocks.closeLightboxMock).not.toHaveBeenCalled()
    await hooks.handleLightboxShare(999)
    await flushPromises()
    expect(chatViewMocks.shareFileMock).not.toHaveBeenCalled()
    await hooks.handleLightboxShare(11)
    await flushPromises()
    expect(chatViewMocks.seedFileCacheMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('این پیام قابل اشتراک‌گذاری نیست')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('covers context-menu share branches for non-shareable, unseeded, and unsupported media', async () => {
    const mountShareHarness = async (message: ReturnType<typeof buildMessage>) => {
      chatViewMocks.messagesSeed = [message]
      const wrapper = await mountChatView({
        targetUserId: 55,
        targetUserName: 'Target User',
      }, {
        ChatMessageItem: {
          props: ['msg'],
          template: '<button class="message-click-action" @click="emitClick">message</button>',
          methods: {
            emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
              this.$emit('click-message', new MouseEvent('click', { clientX: 470, clientY: 340 }), this.msg)
            },
          },
        },
        ChatContextMenu: {
          template: '<button class="menu-share-action" @click="$emit(\'share\')">share</button>',
        },
      })
      await flushPromises()
      await wrapper.get('.message-click-action').trigger('click')
      await flushPromises()
      return wrapper
    }

    const textWrapper = await mountShareHarness(buildMessage({ content: 'plain text', message_type: 'text' }))
    await textWrapper.get('.menu-share-action').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('این پیام قابل اشتراک‌گذاری نیست')
    textWrapper.unmount()

    chatViewMocks.seedFileCacheMock.mockClear()
    chatViewMocks.shareFileMock.mockClear()
    vi.stubGlobal('fetch', vi.fn(async () => new Response('missing', { status: 404 })))
    const unseededWrapper = await mountShareHarness(buildImageMessage({ content: JSON.stringify({ file_id: 'missing-img' }) }))
    await unseededWrapper.get('.menu-share-action').trigger('click')
    await flushPromises()
    expect(chatViewMocks.shareFileMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
    unseededWrapper.unmount()

    chatViewMocks.imageCacheState = { 'img-unsupported': 'blob:unsupported-image' }
    chatViewMocks.shareFileMock.mockResolvedValueOnce(false)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['image'], { type: 'image/jpeg' }), { status: 200 })))
    const unsupportedWrapper = await mountShareHarness(buildImageMessage({ content: JSON.stringify({ file_id: 'img-unsupported' }) }))
    await unsupportedWrapper.get('.menu-share-action').trigger('click')
    await flushPromises()
    expect(chatViewMocks.seedFileCacheMock).toHaveBeenCalledWith(
      'img-unsupported',
      expect.anything(),
      '01_img-unsupported.jpg',
      expect.any(String),
    )
    expect(chatViewMocks.shareFileMock).toHaveBeenCalledWith(
      'img-unsupported',
      '01_img-unsupported.jpg',
      'image/jpeg',
      '/api/chat/files/img-unsupported?token=jwt-token',
    )
    expect(document.body.textContent).toContain('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
    unsupportedWrapper.unmount()
  })

  it('covers direct album action helpers and empty album selection exits', async () => {
    const mediaMessage = buildImageMessage({
      id: 77,
      sender_id: 7,
      receiver_id: 55,
      created_at: new Date().toISOString(),
      content: JSON.stringify({ file_id: 'img-77' }),
    })
    chatViewMocks.messagesSeed = [mediaMessage]
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatInputBar: {
        props: ['replyingToMessage', 'isSelectionMode', 'selectedMessagesCount'],
        template: '<div class="input-state">{{ replyingToMessage?.id ?? "none" }}|{{ String(isSelectionMode) }}|{{ selectedMessagesCount }}</div>',
      },
      ChatForwardModal: {
        props: ['showForwardModal'],
        template: '<div class="forward-modal-state">{{ String(showForwardModal) }}</div>',
      },
    })
    await flushPromises()
    const hooks = getChatViewTestHooks(wrapper)

    hooks.handleAlbumReplyItem(mediaMessage)
    await flushPromises()
    expect(wrapper.get('.input-state').text()).toContain('77')

    hooks.handleAlbumForwardItem(mediaMessage)
    await flushPromises()
    expect(wrapper.get('.forward-modal-state').text()).toBe('true')
    hooks.closeForwardModal()

    chatViewMocks.apiFetchMock.mockClear()
    await hooks.handleAlbumDeleteItem(mediaMessage)
    await flushPromises()
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/messages/77', { method: 'DELETE' })

    hooks.state.selectedMessages.value = []
    hooks.state.selectionModePurpose.value = 'album-forward'
    hooks.handleForwardSelectedAlbumMessages()
    await flushPromises()
    expect(wrapper.get('.input-state').text()).toContain('false|0')

    hooks.state.selectedMessages.value = []
    hooks.state.selectionModePurpose.value = 'album-share'
    await hooks.handleShareSelectedAlbumMessages()
    await flushPromises()
    expect(wrapper.get('.input-state').text()).toContain('false|0')

    hooks.state.contextMenu.value = {
      visible: true,
      message: mediaMessage,
      messageIds: [77],
      x: 0,
      y: 0,
    }
    hooks.handleShareAlbum()
    expect(hooks.state.contextMenu.value.visible).toBe(false)

    hooks.state.contextMenu.value = {
      visible: true,
      message: mediaMessage,
      messageIds: [77],
      x: 0,
      y: 0,
    }
    hooks.goBack()
    expect(hooks.state.contextMenu.value.visible).toBe(false)

    confirmSpy.mockRestore()
    wrapper.unmount()
  })

  it('shares an album from the context menu through the selection bar', async () => {
    const albumMessages = [
      buildImageMessage({
        id: 21,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-1', album_id: 'album-1', album_index: 0 }),
      }),
      buildImageMessage({
        id: 22,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-2', album_id: 'album-1', album_index: 1 }),
      }),
    ]
    chatViewMocks.messagesSeed = albumMessages
    chatViewMocks.imageCacheState = {
      'img-1': 'blob:cached-image-1',
      'img-2': 'blob:cached-image-2',
    }
    chatViewMocks.shareMultipleFilesMock.mockResolvedValue(true)
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input)
      return new Response(new Blob([url], { type: 'image/jpeg' }), { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 490, clientY: 350 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-share-album-action" @click="$emit(\'share-album\')">share album</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-share-album-action').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('2 مدیا برای اشتراک‌گذاری انتخاب شده')
    await wrapper.find('.album-download-selection-bar .selection-action-btn.primary').trigger('click')
    await flushPromises()

    expect(chatViewMocks.shareMultipleFilesMock).toHaveBeenCalledWith(['img-1', 'img-2'])

    wrapper.unmount()
  })

  it('covers album-save selection and media download helper fallbacks', async () => {
    const mediaMessage = buildImageMessage({
      id: 81,
      sender_id: 55,
      receiver_id: 7,
      content: JSON.stringify({ file_id: 'img-save', album_id: 'album-save', album_index: 0 }),
      local_blob_url: 'blob:cached-image',
    })

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    hooks.state.contextMenu.value = {
      visible: true,
      message: mediaMessage,
      messageIds: [81, 82, 81],
      x: 0,
      y: 0,
    }

    hooks.handleSaveAlbum()
    expect(hooks.state.selectionModePurpose.value).toBe('album-download')
    expect(hooks.state.selectedMessages.value).toEqual([81, 82])
    expect(hooks.state.contextMenu.value.visible).toBe(false)

    hooks.state.contextMenu.value = {
      visible: true,
      message: null,
      messageIds: [81],
      x: 0,
      y: 0,
    }
    hooks.handleSaveAlbum()
    expect(hooks.state.contextMenu.value.visible).toBe(false)

    const createdAnchors: HTMLAnchorElement[] = []
    const originalCreateElement = document.createElement.bind(document)
    const createElementSpy = vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      const element = originalCreateElement(tagName as keyof HTMLElementTagNameMap, options as ElementCreationOptions | undefined)
      if (tagName === 'a') {
        createdAnchors.push(element as HTMLAnchorElement)
        vi.spyOn(element as HTMLAnchorElement, 'click').mockImplementation(() => {})
      }
      return element
    }) as typeof document.createElement)

    await hooks.saveMessageMediaToDevice(mediaMessage, 1, 2)
    expect(createdAnchors).toHaveLength(1)
    expect(createdAnchors[0]?.download).toBe('02_img-save.jpg')
    expect(createdAnchors[0]?.href).toBe('blob:cached-image')

    createElementSpy.mockRestore()
    wrapper.unmount()
  })

  it('falls back to direct media downloads when album media fetches fail', async () => {
    const mediaMessage = buildImageMessage({
      id: 91,
      sender_id: 55,
      receiver_id: 7,
      content: JSON.stringify({ file_id: 'img-fallback' }),
      local_blob_url: undefined,
    })

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    const fetchMock = vi.fn(async () => new Response('missing', { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const createdAnchors: HTMLAnchorElement[] = []
    const originalCreateElement = document.createElement.bind(document)
    const createElementSpy = vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      const element = originalCreateElement(tagName as keyof HTMLElementTagNameMap, options as ElementCreationOptions | undefined)
      if (tagName === 'a') {
        createdAnchors.push(element as HTMLAnchorElement)
        vi.spyOn(element as HTMLAnchorElement, 'click').mockImplementation(() => {})
      }
      return element
    }) as typeof document.createElement)

    await hooks.saveMessageMediaToDevice(mediaMessage)

    expect(fetchMock).toHaveBeenCalledWith('/api/chat/files/img-fallback?token=jwt-token')
    expect(errorSpy).toHaveBeenCalledWith('Album media download failed, falling back to direct URL:', expect.any(Error))
    expect(createdAnchors).toHaveLength(1)
    expect(createdAnchors[0]?.download).toBe('img-fallback.jpg')
    expect(createdAnchors[0]?.href).toContain('/api/chat/files/img-fallback?token=jwt-token')

    createElementSpy.mockRestore()
    errorSpy.mockRestore()
    wrapper.unmount()
  })

  it('covers media cache seeding helpers and mime/file-name inference branches', async () => {
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    const imageMessage = buildImageMessage({
      id: 101,
      sender_id: 55,
      receiver_id: 7,
      content: JSON.stringify({ file_id: 'img-seed' }),
      local_blob_url: 'blob:local-image',
    })
    const documentMessage = buildMessage({
      id: 102,
      sender_id: 55,
      receiver_id: 7,
      message_type: 'document',
      content: JSON.stringify({
        file_id: 'doc-seed',
        file_name: 'report.pdf',
        mime_type: 'application/pdf',
      }),
      local_blob_url: 'blob:local-doc',
    })

    expect(hooks.inferMediaMime(buildMessage({ message_type: 'text', content: 'plain text' }))).toBe('application/octet-stream')
    expect(hooks.inferMediaMime(documentMessage)).toBe('application/pdf')
    expect(hooks.inferMediaMime(buildMessage({ message_type: 'document', content: '{invalid' }))).toBe('application/octet-stream')

    expect(hooks.inferMediaFileName(imageMessage, 'img-seed', 1)).toBe('02_img-seed.jpg')
    expect(hooks.inferMediaFileName(buildMessage({ message_type: 'voice', content: JSON.stringify({ file_id: 'voice-1' }) }), 'voice-1', 0)).toBe('01_voice-1.webm')
    expect(hooks.inferMediaFileName(documentMessage, 'doc-seed', 0)).toBe('report.pdf')

    chatViewMocks.seedFileCacheMock.mockClear()
    let fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === 'blob:local-image') {
        return new Response(new Uint8Array([1, 2, 3]), {
          status: 200,
          headers: { 'Content-Type': 'image/png' },
        })
      }
      return new Response('missing', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(hooks.ensureMessageBlobInFileCache(imageMessage)).resolves.toBe('img-seed')
    expect(chatViewMocks.seedFileCacheMock).toHaveBeenCalledWith(
      'img-seed',
      expect.anything(),
      '01_img-seed.jpg',
      'image/png',
    )

    chatViewMocks.seedFileCacheMock.mockClear()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === 'blob:local-doc') {
        throw new Error('local blob failed')
      }
      if (url === '/api/chat/files/doc-seed?token=jwt-token') {
        return new Response(new Uint8Array([4, 5, 6]), {
          status: 200,
          headers: { 'Content-Type': 'application/pdf' },
        })
      }
      return new Response('missing', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(hooks.ensureMessageBlobInFileCache(documentMessage)).resolves.toBe('doc-seed')
    expect(fetchMock).toHaveBeenNthCalledWith(1, 'blob:local-doc')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/chat/files/doc-seed?token=jwt-token')
    expect(chatViewMocks.seedFileCacheMock).toHaveBeenCalledWith(
      'doc-seed',
      expect.anything(),
      'report.pdf',
      'application/pdf',
    )
    expect(warnSpy).toHaveBeenCalledWith('[chat-share] seed from local blob failed', expect.any(Error))

    fetchMock = vi.fn(async () => new Response('missing', { status: 404 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(hooks.ensureMessageBlobInFileCache(buildImageMessage({ content: JSON.stringify({ file_id: 'img-missing' }) }))).resolves.toBeNull()
    await expect(hooks.ensureMessageBlobInFileCache(buildImageMessage({ content: '{}' }))).resolves.toBeNull()

    warnSpy.mockRestore()
    wrapper.unmount()
  })

  it('covers room-manager routing helpers and missing named-room cleanup', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 88,
        other_user_id: -88,
        other_user_name: 'گروه فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 88,
      },
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال فعال',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({
      targetUserId: -88,
      targetUserName: 'گروه فعال',
    })
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)

    hooks.openSelectedRoomManager()
    expect(hooks.state.showGroupManagerModal.value).toBe(true)

    hooks.state.selectedUserId.value = -23
    hooks.state.selectedUserName.value = 'کانال فعال'
    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    await hooks.handleChannelManagerOpenChannel({ chatId: 23, title: 'کانال فعال' })
    await flushPromises()
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-23)
    expect(hooks.state.selectedUserId.value).toBe(-23)

    hooks.openSelectedRoomManager()
    expect(hooks.state.showChannelManagerModal.value).toBe(true)

    chatViewMocks.conversationsSeed = []
    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    hooks.state.showAttachmentMenu.value = true
    hooks.state.showStickerPicker.value = true
    await hooks.handleChannelManagerOpenChannel({ chatId: 77, title: 'کانال تازه' })
    await flushPromises()
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalledTimes(1)
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-77)
    expect(hooks.state.selectedUserId.value).toBe(-77)
    expect(hooks.state.selectedUserName.value).toBe('کانال تازه')
    expect(hooks.state.showAttachmentMenu.value).toBe(false)
    expect(hooks.state.showStickerPicker.value).toBe(false)

    hooks.state.selectedUserId.value = -99
    hooks.state.selectedUserName.value = 'گفتگوی حذف‌شده'
    hooks.state.showAttachmentMenu.value = true
    hooks.state.showStickerPicker.value = true
    hooks.clearMissingNamedRoomSelection()
    expect(hooks.state.selectedUserId.value).toBeNull()
    expect(hooks.state.selectedUserName.value).toBe('')
    expect(hooks.state.showAttachmentMenu.value).toBe(false)
    expect(hooks.state.showStickerPicker.value).toBe(false)

    wrapper.unmount()
  })

  it('covers direct and named-room conversation actions across success and error branches', async () => {
    const directConversation = {
      id: 55,
      other_user_id: 55,
      other_user_name: 'گفتگوی مستقیم',
      last_message_content: null,
      last_message_type: null,
      last_message_at: null,
      unread_count: 0,
      room_kind: 'direct',
      is_pinned: false,
      is_muted: false,
      pinned_at: null,
      pin_order: null,
    }
    const groupConversation = {
      id: 88,
      other_user_id: -88,
      other_user_name: 'گروه فعال',
      last_message_content: null,
      last_message_type: null,
      last_message_at: null,
      unread_count: 0,
      room_kind: 'group',
      chat_id: 88,
    }
    const channelConversation = {
      id: 23,
      other_user_id: -23,
      other_user_name: 'کانال فعال',
      last_message_content: null,
      last_message_type: null,
      last_message_at: null,
      unread_count: 0,
      room_kind: 'channel',
      chat_id: 23,
      is_muted: false,
    }

    chatViewMocks.conversationsSeed = [directConversation, groupConversation, channelConversation]
    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'گفتگوی مستقیم',
    })
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadConversationsMock.mockClear()

    await hooks.handleConversationAction({ action: 'pin', conv: directConversation })
    await flushPromises()
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/direct/55/pin', expect.objectContaining({ method: 'POST' }))

    chatViewMocks.apiFetchMock.mockClear()
    await hooks.handleConversationAction({ action: 'move-pin-up', conv: directConversation })
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/direct/55/pin-order', expect.objectContaining({ method: 'POST' }))

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.setConversationMutedMock.mockClear()
    await hooks.handleConversationAction({ action: 'mute', conv: channelConversation })
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/rooms/23/mute', expect.objectContaining({ method: 'POST' }))
    expect(chatViewMocks.setConversationMutedMock).toHaveBeenCalledWith(-23, true)

    chatViewMocks.apiFetchMock.mockClear()
    await hooks.handleConversationAction({ action: 'mark-unread', conv: groupConversation })
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/rooms/88/mark-unread', expect.objectContaining({ method: 'POST' }))

    hooks.state.selectedUserId.value = 55
    hooks.state.selectedUserName.value = 'گفتگوی مستقیم'
    chatViewMocks.apiFetchMock.mockClear()
    await hooks.handleConversationAction({ action: 'delete', conv: directConversation })
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/direct/55', { method: 'DELETE' })
    expect(hooks.state.selectedUserId.value).toBeNull()

    hooks.state.selectedUserId.value = -88
    hooks.state.selectedUserName.value = 'گروه فعال'
    chatViewMocks.apiFetchMock.mockClear()
    await hooks.handleConversationAction({ action: 'leave', conv: groupConversation })
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/groups/88/leave', { method: 'POST' })
    expect(hooks.state.selectedUserId.value).toBeNull()

    hooks.state.selectedUserId.value = -23
    hooks.state.selectedUserName.value = 'کانال فعال'
    chatViewMocks.apiFetchMock.mockClear()
    await hooks.handleConversationAction({ action: 'unfollow', conv: channelConversation })
    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/channels/23/unfollow', { method: 'POST' })
    expect(hooks.state.selectedUserId.value).toBeNull()

    chatViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('action failed'))
    chatViewMocks.loadConversationsMock.mockClear()
    await hooks.handleConversationAction({ action: 'pin', conv: directConversation })
    await flushPromises()
    expect(chatViewMocks.loadConversationsMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('action failed')

    wrapper.unmount()
  })

  it('covers search-result navigation helpers across global-search and in-chat flows', async () => {
    vi.useFakeTimers()
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'هدف جستجو',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'direct',
      },
    ]

    const wrapper = await mountChatView()
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    const globalResult = { id: 501, sender_id: 55, receiver_id: 7 }
    const loadedResult = { id: 502, sender_id: 55, receiver_id: 7 }
    const unloadedResult = { id: 503, sender_id: 55, receiver_id: 7 }

    hooks.state.searchResults.value = [loadedResult]
    hooks.state.currentSearchIndex.value = 0
    hooks.state.showInChatSearchList.value = true
    chatViewMocks.scrollToMessageMock.mockClear()
    hooks.handleToggleInChatList()
    expect(hooks.state.showInChatSearchList.value).toBe(false)
    await vi.advanceTimersByTimeAsync(150)
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(502)

    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.scrollToMessageMock.mockClear()
    hooks.state.isSearchActive.value = true
    hooks.state.showInChatSearchList.value = true
    hooks.state.searchResults.value = [globalResult]
    hooks.state.currentSearchIndex.value = 0
    hooks.state.selectedUserId.value = null
    hooks.state.selectedUserName.value = ''
    await hooks.handleSearchResultClick(globalResult)
    await flushPromises()
    expect(hooks.state.isSearchActive.value).toBe(false)
    expect(hooks.state.showInChatSearchList.value).toBe(false)
    expect(hooks.state.searchResults.value).toEqual([])
    expect(hooks.state.currentSearchIndex.value).toBe(0)
    expect(hooks.state.selectedUserId.value).toBe(55)
    expect(hooks.state.selectedUserName.value).toBe('هدف جستجو')
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 501)

    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.scrollToMessageMock.mockClear()
    hooks.state.selectedUserId.value = 55
    hooks.state.selectedUserName.value = 'هدف جستجو'
    hooks.state.messages.value = [buildCurrentUserMessage(502, 'بارگذاری شده')]
    hooks.state.searchResults.value = [globalResult, loadedResult]
    hooks.state.currentSearchIndex.value = 0
    hooks.state.showInChatSearchList.value = true
    await hooks.handleSearchResultClick(loadedResult)
    expect(hooks.state.currentSearchIndex.value).toBe(1)
    expect(hooks.state.showInChatSearchList.value).toBe(false)
    expect(chatViewMocks.loadMessagesMock).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(150)
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(502)

    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.scrollToMessageMock.mockClear()
    hooks.state.messages.value = []
    hooks.state.searchResults.value = [unloadedResult]
    hooks.state.currentSearchIndex.value = 0
    hooks.state.showInChatSearchList.value = true
    await hooks.handleSearchResultClick(unloadedResult)
    await flushPromises()
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 503)
    await vi.advanceTimersByTimeAsync(250)
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(503)

    vi.useRealTimers()
    wrapper.unmount()
  })

  it('routes the scroll button to the oldest unread mention and falls back to bottom without a viewer id', async () => {
    const mentionByTag = buildMessage({ id: 201, sender_id: 55, receiver_id: 7, mentions: [7] })
    const mentionAll = buildMessage({ id: 202, sender_id: 56, receiver_id: 7, mention_all: true })
    const alreadyRead = buildMessage({ id: 203, sender_id: 57, receiver_id: 7, is_read: true, mentions: [7] })
    const selfMessage = buildMessage({ id: 204, sender_id: 7, receiver_id: 55, mentions: [7] })

    let wrapper = await mountChatView({ currentUserId: 7 })
    await flushPromises()

    let hooks = getChatViewTestHooks(wrapper)
    hooks.state.selectedUserId.value = 55
    hooks.state.selectedUserName.value = 'گفتگو'
    hooks.state.showScrollButton.value = true
    hooks.state.messages.value = [selfMessage, alreadyRead, mentionByTag, mentionAll]
    await flushPromises()

    const scrollButton = wrapper.get('.scroll-bottom-btn')
    expect(scrollButton.classes()).toContain('has-mention')
    expect(wrapper.find('.scroll-mention-badge').exists()).toBe(true)

    await scrollButton.trigger('click')
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(201)
    expect(chatViewMocks.scrollToBottomMock).not.toHaveBeenCalled()

    wrapper.unmount()

    wrapper = await mountChatView({ currentUserId: 0 as any })
    await flushPromises()

    hooks = getChatViewTestHooks(wrapper)
  hooks.state.selectedUserId.value = 55
  hooks.state.selectedUserName.value = 'گفتگو'
    hooks.state.showScrollButton.value = true
    hooks.state.messages.value = [mentionByTag, mentionAll]
    await flushPromises()

    expect(wrapper.find('.scroll-mention-badge').exists()).toBe(false)
    await wrapper.get('.scroll-bottom-btn').trigger('click')
    expect(chatViewMocks.scrollToBottomMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('covers search toggle and empty-query reset paths', async () => {
    const wrapper = await mountChatView()
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    const focusMock = vi.fn()
    const getElementByIdSpy = vi.spyOn(document, 'getElementById').mockReturnValue({ focus: focusMock } as any)

    hooks.state.searchResults.value = [{ id: 301 }]
    hooks.state.currentSearchIndex.value = 4
    hooks.state.showInChatSearchList.value = true
    hooks.toggleSearch()
    await flushPromises()

    expect(hooks.state.isSearchActive.value).toBe(true)
    expect(hooks.state.searchQuery.value).toBe('')
    expect(hooks.state.searchResults.value).toEqual([])
    expect(hooks.state.currentSearchIndex.value).toBe(0)
    expect(hooks.state.showInChatSearchList.value).toBe(false)
    expect(focusMock).toHaveBeenCalled()

    hooks.state.searchResults.value = [{ id: 302 }]
    hooks.state.currentSearchIndex.value = 2
    hooks.state.searchQuery.value = '   '
    hooks.performSearch()
    expect(hooks.state.searchResults.value).toEqual([])
    expect(hooks.state.currentSearchIndex.value).toBe(0)
    expect(hooks.state.isSearching.value).toBe(false)

    hooks.state.searchResults.value = [{ id: 303 }]
    hooks.state.currentSearchIndex.value = 1
    hooks.state.showInChatSearchList.value = true
    hooks.toggleSearch()
    await flushPromises()

    expect(hooks.state.isSearchActive.value).toBe(false)
    expect(hooks.state.searchResults.value).toEqual([])
    expect(hooks.state.currentSearchIndex.value).toBe(0)
    expect(hooks.state.showInChatSearchList.value).toBe(false)

    getElementByIdSpy.mockRestore()
    wrapper.unmount()
  })

  it('covers next and previous search result helpers for loaded and unloaded targets', async () => {
    const wrapper = await mountChatView()
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)
    const loadedResult = { id: 401, sender_id: 55, receiver_id: 7 }
    const unloadedResult = { id: 402, sender_id: 55, receiver_id: 7 }

    hooks.state.selectedUserId.value = 55
    hooks.state.messages.value = [buildMessage({ id: 401, sender_id: 55, receiver_id: 7 })]
    hooks.state.searchResults.value = [loadedResult, unloadedResult]
    hooks.state.currentSearchIndex.value = 1

    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.scrollToMessageMock.mockClear()
    await hooks.nextSearchResult()
    await flushPromises()
    expect(hooks.state.currentSearchIndex.value).toBe(0)
    expect(chatViewMocks.loadMessagesMock).not.toHaveBeenCalled()
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(401)

    hooks.state.messages.value = [buildMessage({ id: 401, sender_id: 55, receiver_id: 7 })]
    hooks.state.currentSearchIndex.value = 0
    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.scrollToMessageMock.mockClear()
    await hooks.prevSearchResult()
    await flushPromises()
    expect(hooks.state.currentSearchIndex.value).toBe(1)
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 402)
    expect(chatViewMocks.scrollToMessageMock).toHaveBeenCalledWith(402)

    hooks.state.searchResults.value = []
    hooks.state.currentSearchIndex.value = 0
    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.scrollToMessageMock.mockClear()
    await hooks.nextSearchResult()
    await hooks.prevSearchResult()
    expect(chatViewMocks.loadMessagesMock).not.toHaveBeenCalled()
    expect(chatViewMocks.scrollToMessageMock).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('covers route/new-chat helpers and group-channel lifecycle handlers', async () => {
    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال رفرش‌شده',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
    ]

    const wrapper = await mountChatView({ currentUserRole: 'مدیر ارشد' })
    await flushPromises()

    const hooks = getChatViewTestHooks(wrapper)

    chatViewMocks.loadMessagesMock.mockClear()
    hooks.openConversationFromRoute(0, 'ignored')
    expect(chatViewMocks.loadMessagesMock).not.toHaveBeenCalled()

    hooks.openConversationFromRoute(-88, 'اتاق منفی')
    expect(hooks.state.selectedUserId.value).toBe(-88)
    expect(hooks.state.selectedUserName.value).toBe('اتاق منفی')
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-88)

    chatViewMocks.loadMessagesMock.mockClear()
    hooks.openNewConversation()
    await flushPromises()
    expect(chatViewMocks.pushBackStateMock).toHaveBeenCalled()
    hooks.handleNewChatSearch(66, 'گفتگوی تازه')
    let discardCalls = chatViewMocks.discardBackStateMock.mock.calls.length
    expect(discardCalls).toBeGreaterThanOrEqual(1)
    expect(chatViewMocks.popBackStateMock).not.toHaveBeenCalled()
    expect(hooks.state.showNewChatModal.value).toBe(false)
    expect(hooks.state.selectedUserId.value).toBe(66)
    expect(hooks.state.selectedUserName.value).toBe('گفتگوی تازه')
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(66)

    hooks.openNewConversation()
    await flushPromises()
    expect(hooks.state.showNewChatModal.value).toBe(true)

    hooks.openGroupCreation()
    expect(chatViewMocks.discardBackStateMock.mock.calls.length).toBeGreaterThanOrEqual(discardCalls)
    discardCalls = chatViewMocks.discardBackStateMock.mock.calls.length
    expect(chatViewMocks.popBackStateMock).not.toHaveBeenCalled()
    expect(hooks.state.showNewChatModal.value).toBe(false)
    expect(hooks.state.showGroupManagerModal.value).toBe(true)
    expect(hooks.state.groupManagerChatId.value).toBeNull()

    hooks.openNewConversation()
    await flushPromises()
    hooks.openChannelCreation()
    expect(chatViewMocks.discardBackStateMock.mock.calls.length).toBeGreaterThanOrEqual(discardCalls)
    expect(chatViewMocks.popBackStateMock).not.toHaveBeenCalled()
    expect(hooks.state.showNewChatModal.value).toBe(false)
    expect(hooks.state.showChannelManagerModal.value).toBe(true)
    expect(hooks.state.channelManagerChatId.value).toBeNull()

    chatViewMocks.loadConversationsMock.mockClear()
    hooks.state.selectedUserId.value = -23
    hooks.state.selectedUserName.value = 'قدیمی'
    hooks.handleChannelManagerConversationRefresh()
    await flushPromises()
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()
    expect(hooks.state.selectedUserName.value).toBe('کانال رفرش‌شده')

    chatViewMocks.loadConversationsMock.mockClear()
    hooks.closeChannelManager()
    await flushPromises()
    expect(hooks.state.showChannelManagerModal.value).toBe(false)
    expect(hooks.state.channelManagerChatId.value).toBeNull()
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()

    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    hooks.state.showGroupManagerModal.value = true
    hooks.state.groupManagerChatId.value = 99
    await hooks.handleGroupCreated({ id: 99, title: 'گروه جدید' })
    await flushPromises()
    expect(hooks.state.showGroupManagerModal.value).toBe(false)
    expect(hooks.state.groupManagerChatId.value).toBeNull()
    expect(hooks.state.selectedUserId.value).toBe(-99)
    expect(hooks.state.selectedUserName.value).toBe('گروه جدید')
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(-99)

    chatViewMocks.conversationsSeed = [
      {
        id: 23,
        other_user_id: -23,
        other_user_name: 'کانال رفرش‌شده',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'channel',
        chat_id: 23,
      },
      {
        id: 99,
        other_user_id: -99,
        other_user_name: 'گروه جدید',
        last_message_content: null,
        last_message_type: null,
        last_message_at: null,
        unread_count: 0,
        room_kind: 'group',
        chat_id: 99,
      },
    ]

    chatViewMocks.loadConversationsMock.mockClear()
    hooks.state.selectedUserId.value = -99
    hooks.state.selectedUserName.value = 'گروه قدیمی'
    await hooks.handleGroupUpdated({ id: 99, title: 'گروه به‌روز' })
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()
    expect(hooks.state.selectedUserName.value).toBe('گروه به‌روز')

    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.routerReplaceMock.mockClear()
    hooks.state.selectedUserId.value = -99
    hooks.state.selectedUserName.value = 'گروه به‌روز'
    hooks.state.showAttachmentMenu.value = true
    hooks.state.showStickerPicker.value = true
    chatViewMocks.routeState.query = {
      user_id: '-99',
      user_name: 'گروه به‌روز',
    }
    await hooks.handleGroupLeft(99)
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()
    expect(hooks.state.selectedUserId.value).toBeNull()
    expect(hooks.state.selectedUserName.value).toBe('')
    expect(hooks.state.showAttachmentMenu.value).toBe(false)
    expect(hooks.state.showStickerPicker.value).toBe(false)
    expect(chatViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      path: '/chat',
      query: {},
    })

    chatViewMocks.loadConversationsMock.mockClear()
    chatViewMocks.routerReplaceMock.mockClear()
    hooks.state.selectedUserId.value = -23
    hooks.state.selectedUserName.value = 'کانال رفرش‌شده'
    hooks.state.showAttachmentMenu.value = true
    hooks.state.showStickerPicker.value = true
    chatViewMocks.routeState.query = {
      user_id: '-23',
      user_name: 'کانال رفرش‌شده',
    }
    await hooks.handleChannelLeft(23)
    expect(chatViewMocks.loadConversationsMock).toHaveBeenCalled()
    expect(hooks.state.selectedUserId.value).toBeNull()
    expect(hooks.state.selectedUserName.value).toBe('')
    expect(hooks.state.showAttachmentMenu.value).toBe(false)
    expect(hooks.state.showStickerPicker.value).toBe(false)
    expect(chatViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      path: '/chat',
      query: {},
    })

    wrapper.unmount()
  })

  it('downloads an album from the context menu through the selection bar', async () => {
    vi.useFakeTimers()
    const albumMessages = [
      buildImageMessage({
        id: 31,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-1', album_id: 'album-2', album_index: 0 }),
      }),
      buildImageMessage({
        id: 32,
        sender_id: 55,
        receiver_id: 7,
        content: JSON.stringify({ file_id: 'img-2', album_id: 'album-2', album_index: 1 }),
      }),
    ]
    chatViewMocks.messagesSeed = albumMessages
    chatViewMocks.imageCacheState = {
      'img-1': 'blob:cached-image-1',
      'img-2': 'blob:cached-image-2',
    }
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<button class="message-click-action" @click="emitClick">message</button>',
        methods: {
          emitClick(this: { $emit: (event: string, ...args: unknown[]) => void; msg: unknown }) {
            this.$emit('click-message', new MouseEvent('click', { clientX: 520, clientY: 360 }), this.msg)
          },
        },
      },
      ChatContextMenu: {
        template: '<button class="menu-save-album-action" @click="$emit(\'save-album\')">save album</button>',
      },
    })
    await flushPromises()

    await wrapper.get('.message-click-action').trigger('click')
    await flushPromises()
    await wrapper.get('.menu-save-album-action').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('2 مدیا برای دانلود انتخاب شده')
    const downloadClick = wrapper.find('.album-download-selection-bar .selection-action-btn.primary')
    const downloadPromise = downloadClick.trigger('click')
    await vi.runAllTimersAsync()
    await downloadPromise
    await flushPromises()

    expect(anchorClickSpy).toHaveBeenCalledTimes(2)

    anchorClickSpy.mockRestore()
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('runs global search and opens the matching direct conversation result', async () => {
    vi.useFakeTimers()
    chatViewMocks.conversationsSeed = [
      {
        id: 55,
        other_user_id: 55,
        other_user_name: 'Search Target',
        last_message_content: 'needle',
        last_message_type: 'text',
        last_message_at: '2026-05-12T10:00:00',
        unread_count: 0,
      },
    ]
    const resultMessage = buildMessage({ id: 71, sender_id: 55, receiver_id: 7, content: 'needle result' })

    const wrapper = await mountChatView({}, {
      ChatHeader: {
        props: ['isSearchActive', 'searchResults'],
        template: '<div><span class="search-state">{{ String(isSearchActive) }}|{{ searchResults.length }}</span><button class="toggle-search" @click="$emit(\'toggle-search\')">search</button><button class="run-search" @click="$emit(\'search\', \'needle\')">run</button></div>',
      },
      ChatSearchGlobalList: {
        props: ['searchResults'],
        template: '<button class="global-search-result" @click="$emit(\'select-result\', searchResults[0])">open</button>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.apiFetchMock.mockResolvedValueOnce([resultMessage])

    await wrapper.get('.toggle-search').trigger('click')
    await wrapper.get('.run-search').trigger('click')
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/search?q=needle')
    expect(wrapper.get('.search-state').text()).toBe('true|1')

    await wrapper.get('.global-search-result').trigger('click')
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).toHaveBeenLastCalledWith(55, false, 71)
    expect(chatViewMocks.pushBackStateMock).toHaveBeenCalled()

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('navigates in-chat search results, loads missing targets, and toggles the list overlay', async () => {
    vi.useFakeTimers()
    chatViewMocks.messagesSeed = [buildMessage({ id: 11, sender_id: 55, receiver_id: 7, content: 'loaded result' })]
    const searchResults = [
      buildMessage({ id: 11, sender_id: 55, receiver_id: 7, content: 'loaded result' }),
      buildMessage({ id: 99, sender_id: 55, receiver_id: 7, content: 'older missing result' }),
    ]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Search Target',
    }, {
      ChatHeader: {
        props: ['isSearchActive', 'searchResults', 'currentSearchIndex'],
        template: '<div><span class="search-state">{{ String(isSearchActive) }}|{{ searchResults.length }}|{{ currentSearchIndex }}</span><button class="toggle-search" @click="$emit(\'toggle-search\')">search</button><button class="run-search" @click="$emit(\'search\', \'needle\')">run</button></div>',
      },
      ChatSearchBottomBar: {
        props: ['currentSearchIndex', 'totalResults', 'showInChatSearchList'],
        template: '<div><span class="bottom-search-state">{{ currentSearchIndex }}|{{ totalResults }}|{{ String(showInChatSearchList) }}</span><button class="next-result" @click="$emit(\'next\')">next</button><button class="prev-result" @click="$emit(\'prev\')">prev</button><button class="toggle-result-list" @click="$emit(\'toggle-list\')">list</button></div>',
      },
      ChatSearchGlobalList: {
        props: ['searchResults'],
        template: '<button class="in-chat-result" @click="$emit(\'select-result\', searchResults[1])">select</button>',
      },
    })
    await flushPromises()

    chatViewMocks.apiFetchMock.mockClear()
    chatViewMocks.loadMessagesMock.mockClear()
    chatViewMocks.apiFetchMock.mockResolvedValueOnce(searchResults)

    await wrapper.get('.toggle-search').trigger('click')
    await wrapper.get('.run-search').trigger('click')
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()

    expect(chatViewMocks.apiFetchMock).toHaveBeenCalledWith('/chat/search?q=needle&chat_id=55')
    expect(wrapper.get('.bottom-search-state').text()).toBe('0|2|false')

    await wrapper.get('.next-result').trigger('click')
    await flushPromises()
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 99)
    expect(wrapper.get('.bottom-search-state').text()).toBe('1|2|false')

    await wrapper.get('.toggle-result-list').trigger('click')
    await flushPromises()
    expect(wrapper.find('.in-chat-result').exists()).toBe(true)

    chatViewMocks.loadMessagesMock.mockClear()
    await wrapper.get('.in-chat-result').trigger('click')
    await flushPromises()
    expect(chatViewMocks.loadMessagesMock).toHaveBeenCalledWith(55, false, 99)
    expect(wrapper.get('.bottom-search-state').text()).toBe('1|2|false')

    await wrapper.get('.prev-result').trigger('click')
    await flushPromises()
    expect(wrapper.get('.bottom-search-state').text()).toBe('0|2|false')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('routes cancel-send and cancel-download events by message type', async () => {
    const textMessage = buildMessage({ id: 31, message_type: 'text' })
    const imageMessage = buildImageMessage({ id: 32, content: JSON.stringify({ file_id: 'img-32' }) })
    const documentMessage = buildMessage({
      id: 33,
      message_type: 'document',
      content: JSON.stringify({ file_id: 'doc-33', file_name: 'doc.pdf' }),
    })
    chatViewMocks.messagesSeed = [textMessage, imageMessage, documentMessage]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: `<div>
          <button class="cancel-send-action" @click="$emit('cancel-send', msg)">cancel send</button>
          <button class="cancel-download-action" @click="$emit('cancel-download', msg)">cancel download</button>
        </div>`,
      },
    })
    await flushPromises()

    const cancelSendButtons = wrapper.findAll('.cancel-send-action')
    await cancelSendButtons[0]!.trigger('click')
    await cancelSendButtons[1]!.trigger('click')

    expect(chatViewMocks.cancelTextMessageMock).toHaveBeenCalledWith(31)
    expect(chatViewMocks.cancelUploadMock).toHaveBeenCalledWith(32)

    const cancelDownloadButtons = wrapper.findAll('.cancel-download-action')
    await cancelDownloadButtons[2]!.trigger('click')
    await cancelDownloadButtons[1]!.trigger('click')

    expect(chatViewMocks.cancelDocumentDownloadMock).toHaveBeenCalledWith(33)
    expect(chatViewMocks.cancelMediaDownloadMock).toHaveBeenCalledWith(32)

    wrapper.unmount()
  })

  it('groups album media and hydrates rendered media through the message on-load callback', async () => {
    const albumImage = buildImageMessage({
      id: 41,
      content: JSON.stringify({ file_id: 'img-41', album_id: 'album-hydrate', album_index: 1 }),
      created_at: '2026-05-10T10:00:00Z',
    })
    const albumVideo = buildImageMessage({
      id: 42,
      message_type: 'video',
      content: JSON.stringify({ file_id: 'vid-42', album_id: 'album-hydrate', album_index: 0 }),
      created_at: '2026-05-10T09:59:00Z',
    })
    const documentMessage = buildMessage({
      id: 43,
      message_type: 'document',
      content: JSON.stringify({ file_id: 'doc-43', file_name: 'doc.pdf' }),
      created_at: '2026-05-11T10:00:00Z',
    })
    chatViewMocks.messagesSeed = [albumImage, albumVideo, documentMessage]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg', 'isAlbum', 'albumItems', 'onLoad'],
        template: `<button class="hydrate-action" @click="onLoad?.()">
          {{ isAlbum ? 'album:' + albumItems.map((item) => item.id).join(',') : 'single:' + msg.id }}
        </button>`,
      },
    })
    await flushPromises()

    const hydrateButtons = wrapper.findAll('.hydrate-action')
    expect(hydrateButtons.map((button) => button.text())).toEqual([
      'album:42,41',
      'single:43',
    ])

    await hydrateButtons[0]!.trigger('click')
    await hydrateButtons[1]!.trigger('click')

    expect(chatViewMocks.scheduleMediaHydrationMock).toHaveBeenCalledWith(
      albumVideo.content,
      'video',
      { allowNetwork: false },
    )
    expect(chatViewMocks.scheduleMediaHydrationMock).toHaveBeenCalledWith(
      albumImage.content,
      'image',
      { allowNetwork: true },
    )
    expect(chatViewMocks.scheduleMediaHydrationMock).toHaveBeenCalledWith(
      documentMessage.content,
      'document',
    )

    wrapper.unmount()
  })

  it('loads older messages near the top and keeps the scroll anchor stable after prepend', async () => {
    chatViewMocks.hasOlderMessagesValue = true
    chatViewMocks.loadOlderMessagesMock.mockResolvedValue(2)
    chatViewMocks.messagesSeed = [buildMessage({ id: 51 })]

    const wrapper = await mountChatView({
      targetUserId: 55,
      targetUserName: 'Target User',
    }, {
      ChatMessageItem: {
        props: ['msg'],
        template: '<div :id="`msg-${msg.id}`" class="scroll-message">{{ msg.id }}</div>',
      },
    })
    await flushPromises()

    const container = wrapper.get('.messages-container').element as HTMLElement
    let scrollHeight = 1000
    Object.defineProperty(container, 'clientHeight', { configurable: true, get: () => 400 })
    Object.defineProperty(container, 'scrollHeight', { configurable: true, get: () => scrollHeight })
    container.scrollTop = 24

    const scrollPromise = wrapper.get('.messages-container').trigger('scroll')
    scrollHeight = 1220
    await scrollPromise
    await flushPromises()

    expect(chatViewMocks.loadOlderMessagesMock).toHaveBeenCalledWith(55)
    expect(container.scrollTop).toBe(244)

    wrapper.unmount()
  })
})