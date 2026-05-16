import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const chatViewMocks = vi.hoisted(() => ({
  routeState: {
    path: '/chat',
    query: {} as Record<string, string>,
  },
  routerReplaceMock: vi.fn(),
  routerPushMock: vi.fn(),
  apiFetchMock: vi.fn(async () => ({})),
  loadConversationsMock: vi.fn(),
  loadMessagesMock: vi.fn(),
  conversationsSeed: [] as any[],
  messagesSeed: [] as any[],
  imageCacheState: {} as Record<string, string>,
  downloadMediaMock: vi.fn(),
  openMediaLightboxMock: vi.fn(),
  handleMediaUploadWrapperMock: vi.fn(),
  setLightboxIndexMock: vi.fn(),
  closeLightboxMock: vi.fn(),
  cancelDocumentDownloadMock: vi.fn(),
  cancelMediaDownloadMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  setConversationMutedMock: vi.fn(),
  seedFileCacheMock: vi.fn(),
  shareMultipleFilesMock: vi.fn(),
  shareFileMock: vi.fn(),
}))

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
  clearBackStack: chatViewMocks.clearBackStackMock,
}))

vi.mock('../stores/notifications', () => ({
  useNotificationStore: () => ({
    setConversationMuted: chatViewMocks.setConversationMutedMock,
  }),
}))

vi.mock('../composables/chat/useChatMessages', () => ({
  useChatMessages: (options: any) => ({
    apiFetch: chatViewMocks.apiFetchMock,
    loadConversations: chatViewMocks.loadConversationsMock.mockImplementation(async () => {
      options.conversations.value = [...chatViewMocks.conversationsSeed]
    }),
    loadMessages: chatViewMocks.loadMessagesMock.mockImplementation(async () => {
      options.messages.value = [...chatViewMocks.messagesSeed]
      return [...chatViewMocks.messagesSeed]
    }),
    loadOlderMessages: vi.fn(async () => 0),
    markAsRead: vi.fn(),
    sendMessage: vi.fn(),
    sendMediaMessage: vi.fn(),
    cancelTextMessage: vi.fn(),
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
    hasOlderMessages: { value: false },
    isLoadingOlderMessages: { value: false },
  }),
}))

vi.mock('../composables/chat/useChatMedia', async () => {
  const { ref } = await import('vue')
  return {
    useChatMedia: () => ({
      imageCache: ref(chatViewMocks.imageCacheState),
      scheduleMediaHydration: vi.fn(),
      downloadMedia: chatViewMocks.downloadMediaMock,
      lightboxMedia: ref(null),
      cancelUpload: vi.fn(),
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
      scrollToBottom: vi.fn(),
      forceScrollToBottom: vi.fn(),
      handleScroll: vi.fn(),
      scrollToUnreadOrBottom: vi.fn(),
      scrollToMessage: vi.fn(),
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
    chatViewMocks.routerPushMock.mockReset()
    chatViewMocks.apiFetchMock.mockReset()
    chatViewMocks.apiFetchMock.mockResolvedValue({})
    chatViewMocks.loadConversationsMock.mockReset()
    chatViewMocks.loadMessagesMock.mockReset()
    chatViewMocks.conversationsSeed = []
    chatViewMocks.messagesSeed = []
    chatViewMocks.imageCacheState = {}
    chatViewMocks.downloadMediaMock.mockReset()
    chatViewMocks.openMediaLightboxMock.mockReset()
    chatViewMocks.handleMediaUploadWrapperMock.mockReset()
    chatViewMocks.setLightboxIndexMock.mockReset()
    chatViewMocks.closeLightboxMock.mockReset()
    chatViewMocks.cancelDocumentDownloadMock.mockReset()
    chatViewMocks.cancelMediaDownloadMock.mockReset()
    chatViewMocks.pushBackStateMock.mockReset()
    chatViewMocks.popBackStateMock.mockReset()
    chatViewMocks.clearBackStackMock.mockReset()
    chatViewMocks.setConversationMutedMock.mockReset()
    chatViewMocks.seedFileCacheMock.mockReset()
    chatViewMocks.shareMultipleFilesMock.mockReset()
    chatViewMocks.shareFileMock.mockReset()
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

  it('blocks accountants from starting a brand-new direct chat through startNewChat', async () => {
    const wrapper = await mountChatView({ currentUserIsAccountant: true })
    await flushPromises()

    await getExposedStartNewChat(wrapper)(55, 'Target User')

    expect(chatViewMocks.loadMessagesMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('حسابدار در این فاز اجازه شروع گفتگوی مستقیم جدید را ندارد')
    wrapper.unmount()
  }, 10000)

  it('blocks positive route targets for accountants when no direct conversation exists yet', async () => {
    const wrapper = await mountChatView({
      currentUserIsAccountant: true,
      targetUserId: 55,
      targetUserName: 'Target User',
    })
    await flushPromises()

    expect(chatViewMocks.loadMessagesMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('حسابدار در این فاز اجازه شروع گفتگوی مستقیم جدید را ندارد')

    wrapper.unmount()
  })

  it('blocks accountants from opening the new conversation modal from the list entry point', async () => {
    const wrapper = await mountChatView({ currentUserIsAccountant: true })
    await flushPromises()

    await wrapper.get('.open-new-conversation').trigger('click')

    expect(document.body.textContent).toContain('حسابدار در این فاز اجازه شروع گفتگوی مستقیم جدید را ندارد')

    wrapper.unmount()
  })

  it('blocks accountants from starting group creation from the header action', async () => {
    const wrapper = await mountChatView({ currentUserIsAccountant: true })
    await flushPromises()

    await wrapper.get('.open-group-creation').trigger('click')

    expect(document.body.textContent).toContain('حسابدار در این فاز اجازه ساخت گروه جدید را ندارد')

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

    await wrapper.get('.chat-header-back').trigger('click')
    await flushPromises()

    expect(chatViewMocks.popBackStateMock).toHaveBeenCalled()
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
})