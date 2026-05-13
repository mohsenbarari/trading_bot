import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const chatViewMocks = vi.hoisted(() => ({
  routeState: {
    path: '/chat',
    query: {} as Record<string, string>,
  },
  routerReplaceMock: vi.fn(),
  routerPushMock: vi.fn(),
  loadConversationsMock: vi.fn(),
  loadMessagesMock: vi.fn(),
  conversationsSeed: [] as any[],
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  setConversationMutedMock: vi.fn(),
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
    apiFetch: vi.fn(async () => ({})),
    loadConversations: chatViewMocks.loadConversationsMock.mockImplementation(async () => {
      options.conversations.value = [...chatViewMocks.conversationsSeed]
    }),
    loadMessages: chatViewMocks.loadMessagesMock.mockImplementation(async () => []),
    loadOlderMessages: vi.fn(async () => 0),
    markAsRead: vi.fn(),
    sendMessage: vi.fn(),
    sendMediaMessage: vi.fn(),
    cancelTextMessage: vi.fn(),
    cancelEdit: vi.fn(),
    handleReply: vi.fn(),
    cancelReply: vi.fn(),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
    startStatusPolling: vi.fn(),
    stopStatusPolling: vi.fn(),
    hasOlderMessages: { value: false },
    isLoadingOlderMessages: { value: false },
  }),
}))

vi.mock('../composables/chat/useChatMedia', async () => {
  const { reactive, ref } = await import('vue')
  return {
    useChatMedia: () => ({
      imageCache: reactive({}),
      scheduleMediaHydration: vi.fn(),
      downloadMedia: vi.fn(),
      lightboxMedia: ref(null),
      cancelUpload: vi.fn(),
      cancelDocumentDownload: vi.fn(),
      cancelMediaDownload: vi.fn(),
      handleMediaClick: vi.fn(),
      setLightboxIndex: vi.fn(),
      closeLightbox: vi.fn(),
      handleMediaUploadWrapper: vi.fn(),
    }),
  }
})

vi.mock('../composables/chat/useChatWebSocket', async () => {
  const { reactive } = await import('vue')
  return {
    useChatWebSocket: () => ({
      typingUsers: reactive({}),
      isTyping: false,
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
  seedFileCache: vi.fn(),
  shareMultipleFiles: vi.fn(),
  shareFile: vi.fn(),
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
    chatViewMocks.loadConversationsMock.mockReset()
    chatViewMocks.loadMessagesMock.mockReset()
    chatViewMocks.conversationsSeed = []
    chatViewMocks.pushBackStateMock.mockReset()
    chatViewMocks.popBackStateMock.mockReset()
    chatViewMocks.clearBackStackMock.mockReset()
    chatViewMocks.setConversationMutedMock.mockReset()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  async function mountChatView(overrides: Record<string, unknown> = {}) {
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
          ChatHeader: { template: '<button class="chat-header-stub open-group-creation" @click="$emit(\'create-group\')">group</button>' },
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
        },
      },
    })
  }

  it('blocks accountants from starting a brand-new direct chat through startNewChat', async () => {
    const wrapper = await mountChatView({ currentUserIsAccountant: true })
    await flushPromises()

    await wrapper.vm.$.exposed.startNewChat(55, 'Target User')

    expect(chatViewMocks.loadMessagesMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('حسابدار در این فاز اجازه شروع گفتگوی مستقیم جدید را ندارد')
  })

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
})