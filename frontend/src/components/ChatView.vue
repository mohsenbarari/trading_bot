<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted, nextTick, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MessengerLoadingScreen from './chat/MessengerLoadingScreen.vue'
import ChatHeader from './chat/ChatHeader.vue'
import ChatShell from './chat/containers/ChatShell.vue'
import ConversationListContainer from './chat/containers/ConversationListContainer.vue'
import ChatRoomContainer from './chat/containers/ChatRoomContainer.vue'
import { pushBackState, popBackState, clearBackStack, discardBackState } from '../composables/useBackButton'
import { useWebSocket } from '../composables/useWebSocket'

import type { ChatAlbumTimelineItem, ChatForwardTarget, ChatSelectionPurpose, ChatTimelineGroup, ChatTimelineItem, Conversation, Message, MessageReaction, PinnedMessageState } from '../types/chat'
import { WS_NOTIFICATION_EVENTS } from '../types/notifications'
import { useChatMedia } from '../composables/chat/useChatMedia'
import { useChatWebSocket } from '../composables/chat/useChatWebSocket'
import { formatIranDate, formatIranTime, isTodayInIran, isYesterdayInIran } from '../utils/iranTime'
import { useChatMessages } from '../composables/chat/useChatMessages'
import { useChatScroll } from '../composables/chat/useChatScroll'
import { useNotificationStore } from '../stores/notifications'
import { useChatSessionStore } from '../stores/chat/session'
import { useMessagesStore } from '../stores/chat/messages'
import { useChatUiStore } from '../stores/chat/ui'
import { createChatRoomLifecycleRuntime } from '../services/chat/chatRoomLifecycle'
import {
  ensureFileCached,
  canShareFiles,
  shareMultipleFiles,
  shareFile as cachedShareFileGlobal,
} from '../composables/chat/useChatFileHandler'
import { MESSAGE_REACTION_CATALOG, recordRecentMessageReaction } from '../utils/messageReactions'
import {
  buildChatSendBody,
  buildChatSendEndpoint,
  isNamedRoomKind,
  resolveRoomConversationKey,
} from '../utils/chatRoomRouting'
import { resolveConversationProfileTarget } from '../utils/accountantChatIdentity'
import { isAdminRole } from '../utils/currentUser'
import { isUserOnline } from '../utils/userPresence'
import { markMessengerPerformance } from '../utils/messengerRefactor'
import { recordMessengerDomSnapshot, recordMessengerMetric, scheduleMessengerDiagnosticTask } from '../utils/messengerDiagnosticsMetrics'
import {
  buildMessengerConversationQuery,
  clearMessengerTimelineCache,
  createMessengerTimelineCache,
  getAlbumMessagesForMessage as getAlbumMessagesForMessageFromController,
  getAlbumMeta,
  getContextMenuMessageIds as getContextMenuMessageIdsFromController,
  getRouteQueryValue,
  groupMessengerMessages,
  normalizeMessageIds,
  sortMessageIdsByMessageOrder,
  toggleMessageSelectionBatch,
} from '../utils/chatTimelineController'
import {
  compareMessengerConversationActivity,
  getNextPinnedConversationOrder,
  isMandatoryPinnedConversation as isMandatoryPinnedConversationFromModel,
  isMessengerConversationPinned,
  sortMessengerConversations,
  summarizeTimelineRenderBudget,
} from '../utils/conversationListModel'
import {
  reduceMessengerOverlayState,
  type MessengerOverlayAction,
  type MessengerOverlayState,
} from '../utils/composerOverlayState'
import {
  buildMessengerContextMenuModel,
  getMessengerContextMenuStyle,
  type MessengerContextMenuModel,
} from '../utils/messageContextMenuModel'

const loadChatSearchGlobalList = () => import('./chat/ChatSearchGlobalList.vue')

const ChatNewConversationModal = defineAsyncComponent(() => import('./chat/ChatNewConversationModal.vue'))
const ChatGroupManagerModal = defineAsyncComponent(() => import('./chat/ChatGroupManagerModal.vue'))
const CreateChannelView = defineAsyncComponent(() => import('./CreateChannelView.vue'))
const AdminBroadcastModal = defineAsyncComponent(() => import('./AdminBroadcastModal.vue'))
const keepInactiveMessengerSurfacesMounted = Boolean(import.meta.env.VITEST)
const MESSENGER_INTERACTION_WARM_DEFER_MS = 3200
const MESSENGER_INTERACTION_DIAGNOSTIC_DEFER_MS = 2600
const MESSENGER_INITIAL_POLL_DEFER_MS = 4200
const MESSENGER_STATUS_POLL_DEFER_MS = 1800
const MESSENGER_PINNED_MESSAGE_DEFER_MS = 900

let interactionChunksWarmed = false
function warmMessengerInteractionChunks() {
  if (interactionChunksWarmed) return
  interactionChunksWarmed = true
  scheduleMessengerDiagnosticTask(() => {
    void loadChatSearchGlobalList().catch(() => null)
  }, { deferMs: MESSENGER_INTERACTION_WARM_DEFER_MS, timeoutMs: 1200, fallbackDelayMs: 240 })
}

// Props
const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  currentUserId: number
  currentUserRole?: string | null
  currentUserIsAccountant?: boolean
  currentUserIsCustomer?: boolean
  targetUserId?: number
  targetUserName?: string
}>()

const router = useRouter()
const route = useRoute()
const notificationStore = useNotificationStore()
const chatSessionStore = useChatSessionStore()
const chatMessagesStore = useMessagesStore()
const chatUiStore = useChatUiStore()
const chatRoomLifecycle = createChatRoomLifecycleRuntime()
const registeredRoomLifecycleCleanups = new Set<number>()
const { on: onGlobalWs, off: offGlobalWs } = useWebSocket()

// Emits
const emit = defineEmits<{
  (e: 'navigate', view: string, payload?: any): void
  (e: 'back'): void
}>()

// State
const isLoading = ref(true)
const error = ref('')
const messagePanelError = ref('')

// Conversations & Messages
const conversations = ref<Conversation[]>([])
const selectedUserId = ref<number | null>(null)
const selectedUserName = ref('')
const messages = ref<Message[]>([])
const pinnedMessageState = ref<PinnedMessageState | null>(null)

// Selection State
const selectedMessages = ref<number[]>([])
const forwardMessageIds = ref<number[]>([])
const selectionModePurpose = ref<ChatSelectionPurpose>('default')
const activeAlbumSelectionId = ref<string | null>(null)
const isSelectionMode = computed(() => selectedMessages.value.length > 0)
const selectionMemoKey = computed(() => selectedMessages.value.join('|'))
const isAlbumDownloadSelectionMode = computed(() => {
  return isSelectionMode.value && selectionModePurpose.value === 'album-download' && Boolean(activeAlbumSelectionId.value)
})
const isAlbumForwardSelectionMode = computed(() => {
  return isSelectionMode.value && selectionModePurpose.value === 'album-forward' && Boolean(activeAlbumSelectionId.value)
})
const isAlbumShareSelectionMode = computed(() => {
  return isSelectionMode.value && selectionModePurpose.value === 'album-share' && Boolean(activeAlbumSelectionId.value)
})
const isAlbumActionSelectionMode = computed(() => isAlbumDownloadSelectionMode.value || isAlbumForwardSelectionMode.value || isAlbumShareSelectionMode.value)
const longPressTimer = ref<any>(null)
const selectionBackStateActive = ref(false)
let clearingSelectionFromBack = false

// Search State
const isSearchActive = ref(false)
const searchQuery = ref('')
const searchResults = ref<any[]>([])
const isSearching = ref(false)
const currentSearchIndex = ref(0)
const showInChatSearchList = ref(false)
const searchDebounceTimeout = ref<any>(null)

// UI State
const isLoadingMessages = ref(false)
const messagesContainer = ref<HTMLElement | null>(null)
const virtualTimelineRef = ref<{
  scrollToMessage: (messageId: number) => boolean
  scrollToBottom?: () => boolean
  scrollToUnreadOrBottom?: (currentUserId: number) => boolean
  preservePrependAnchor?: (messageId: number) => boolean
} | null>(null)
const isUserAtBottom = ref(true)
const unreadNewMessagesCount = ref(0)
const showScrollButton = ref(false)
const isMobile = ref(false)
const prefersReducedMotion = ref(false)
const contextMenu = ref<{
  visible: boolean
  x: number
  y: number
  message: Message | null
  messageIds: number[]
  style: Record<string, string> | null
  menuModel?: MessengerContextMenuModel | null
}>({ visible: false, x: 0, y: 0, message: null, messageIds: [], style: null })
const AVAILABLE_MESSAGE_REACTIONS = [...MESSAGE_REACTION_CATALOG] as const
const CONTEXT_MENU_SUPPORTS_FILE_SHARE = canShareFiles()
const pendingReactionMutationVersion = new Map<number, number>()
let reactionMutationVersion = 0
let contextMenuSnapshotVersion = 0

// Input
const messageInput = ref('')
const isSending = ref(false)
const editingMessage = ref<Message | null>(null)
const chatInputBarRef = ref<{
  focusInput: (options?: { cursorToEnd?: boolean }) => void
  adjustTextareaHeight: () => void
} | null>(null)

const showStickerPicker = ref(false)
const isUploading = ref(false)

// Reply & Swipe State
const replyingToMessage = ref<Message | null>(null)
const touchStartX = ref(0)
const touchCurrentX = ref(0)
const swipedMessageId = ref<number | null>(null)
const isViewingReply = ref(false) 

// Forward State
const showForwardModal = ref(false)

// Attachment Bottom Sheet
const showAttachmentMenu = ref(false)
let pendingMediaCaptionReservation: { value: string; consumed: boolean } | null = null

type MessagesContainerMetrics = {
  clientHeight: number
  scrollHeight: number
  scrollTop: number
}

type PendingSelectionAnchor = {
  messageId: number
  offsetTop: number
  userId: number
}

const BOTTOM_LAYOUT_LOCK_THRESHOLD_PX = 96
let messagesContainerResizeObserver: ResizeObserver | null = null
let previousMessagesContainerMetrics: MessagesContainerMetrics | null = null
let pendingSelectionAnchor: PendingSelectionAnchor | null = null
let suppressMissingRoomCleanupDuringRouteSync = false

// Status
const targetUserStatus = ref('آخرین بازدید اخیراً')

function resolveSelectedConversationName(userId: number | null, fallback = '') {
  if (userId == null) {
    return ''
  }

  const conversationName = conversations.value.find((conversation) => conversation.other_user_id === userId)?.other_user_name
  if (conversationName) {
    return conversationName
  }

  const normalizedFallback = fallback.trim()
  if (normalizedFallback) {
    return normalizedFallback
  }

  return ''
}

async function syncSelectedConversationRoute(userId: number | null, userName = '') {
  const currentUserId = getRouteQueryValue(route.query.user_id as string | string[] | undefined)
  const currentUserName = getRouteQueryValue(route.query.user_name as string | string[] | undefined)
  const nextUserId = userId == null ? '' : String(userId)
  const nextUserName = resolveSelectedConversationName(userId, userName)

  if (currentUserId === nextUserId && currentUserName === nextUserName) {
    return
  }

  const nextQuery = buildMessengerConversationQuery(route.query as Record<string, string | string[] | null | undefined>, userId, nextUserName)

  await router.replace({ path: route.path, query: nextQuery })
}

function updateIsMobile() {
  isMobile.value = window.innerWidth < 768
}

function updateReducedMotionPreference() {
  prefersReducedMotion.value = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function bindOverlayBackState(source: () => boolean, onBack: () => void, consumeProgrammaticClose?: () => boolean) {
  let backStateActive = false
  let closingFromBack = false

  watch(source, (isOpen) => {
    if (isOpen) {
      if (!backStateActive) {
        backStateActive = true
        pushBackState(() => {
          backStateActive = false
          closingFromBack = true
          onBack()
          closingFromBack = false
        })
      }
      return
    }

    if (backStateActive) {
      backStateActive = false
      if (!closingFromBack) {
        if (consumeProgrammaticClose?.()) {
          discardBackState()
        } else {
          popBackState()
        }
      }
    }
  })
}

function getComposerOverlayState(): MessengerOverlayState {
  return {
    attachmentOpen: showAttachmentMenu.value,
    stickerOpen: showStickerPicker.value,
    forwardOpen: showForwardModal.value,
    searchActive: isSearchActive.value,
    inChatSearchList: showInChatSearchList.value,
  }
}

function applyComposerOverlayAction(action: MessengerOverlayAction) {
  const nextState = reduceMessengerOverlayState(getComposerOverlayState(), action)
  showAttachmentMenu.value = nextState.attachmentOpen
  showStickerPicker.value = nextState.stickerOpen
  showForwardModal.value = nextState.forwardOpen
  isSearchActive.value = nextState.searchActive
  showInChatSearchList.value = nextState.inChatSearchList
  return nextState
}

function resetComposerDraftState() {
  editingMessage.value = null
  replyingToMessage.value = null
  messageInput.value = ''
  if (isMobile.value) {
    swipedMessageId.value = null
  }
}

function prepareConversationTransition() {
  applyComposerOverlayAction({ type: 'enter_conversation' })
  closeContextMenu()
  resetComposerDraftState()
}

function beginReplyTransition(msg: Message) {
  applyComposerOverlayAction({ type: 'enter_reply' })
  editingMessage.value = null
  if (isMobile.value) {
    swipedMessageId.value = null
  }
  handleReply(msg)
  closeContextMenu()
}

function beginEditTransition(msg: Message) {
  applyComposerOverlayAction({ type: 'enter_editing' })
  replyingToMessage.value = null
  if (isMobile.value) {
    swipedMessageId.value = null
  }
  editingMessage.value = msg
  messageInput.value = msg.content
  closeContextMenu()
  nextTick(() => {
    chatInputBarRef.value?.adjustTextareaHeight?.()
    chatInputBarRef.value?.focusInput?.({ cursorToEnd: true })
  })
}

const {
  isViewingReply: scrollIsViewingReply,
  scrollToBottom,
  forceScrollToBottom,
  handleScroll,
  scrollToUnreadOrBottom,
  scrollToMessage: scrollToMessageInDom
} = useChatScroll({
  messagesContainer,
  messages,
  currentUserId: props.currentUserId,
  unreadNewMessagesCount,
  markAsRead: () => messagesLogic?.markAsRead(),
  isUserAtBottom,
  showScrollButton
})

watch(scrollIsViewingReply, (val) => { isViewingReply.value = val })

function scrollToMessage(msgId: number) {
  if (virtualTimelineRef.value?.scrollToMessage(msgId)) {
    isViewingReply.value = true
    window.setTimeout(() => {
      isViewingReply.value = false
    }, 3000)
    return
  }

  scrollToMessageInDom(msgId)
}

function scrollToBottomForActiveTimeline() {
  if (virtualTimelineRef.value?.scrollToBottom?.()) {
    return
  }

  scrollToBottom()
}

function scrollToUnreadOrBottomForActiveTimeline() {
  if (props.currentUserId && virtualTimelineRef.value?.scrollToUnreadOrBottom?.(props.currentUserId)) {
    return
  }

  if (
    import.meta.env.VITE_MESSENGER_VIRTUAL_TIMELINE === 'true'
    && selectedRoomKind.value === 'direct'
    && timelineRenderBudget.value.virtualizationCandidate
  ) {
    let attempt = 0
    const retryVirtualUnreadScroll = () => {
      attempt += 1
      if (props.currentUserId && virtualTimelineRef.value?.scrollToUnreadOrBottom?.(props.currentUserId)) {
        return
      }
      if (attempt < 10) {
        window.setTimeout(retryVirtualUnreadScroll, 80)
        return
      }
      scrollToUnreadOrBottom()
    }
    window.setTimeout(retryVirtualUnreadScroll, 80)
    return
  }

  scrollToUnreadOrBottom()
}

const unreadMentionMessages = computed(() => {
  if (!props.currentUserId) return []
  return messages.value.filter((m) => {
    if (m.sender_id === props.currentUserId) return false
    if (m.is_read) return false
    const mentions = Array.isArray(m.mentions) ? m.mentions : []
    return mentions.includes(props.currentUserId) || m.mention_all === true
  })
})

function handleScrollButtonClick() {
  if (unreadMentionMessages.value.length > 0) {
    const oldestMention = unreadMentionMessages.value[0]
    if (!oldestMention) {
      scrollToBottomForActiveTimeline()
      return
    }
    scrollToMessage(oldestMention.id)
  } else {
    scrollToBottomForActiveTimeline()
  }
}

watch([selectedUserId, selectedUserName], ([nextUserId, nextUserName]) => {
  void syncSelectedConversationRoute(nextUserId, nextUserName)
})

const messagesLogic = useChatMessages({
  apiBaseUrl: props.apiBaseUrl,
  jwtToken: props.jwtToken,
  currentUserId: props.currentUserId,
  selectedUserId,
  messages,
  conversations,
  error,
  messagePanelError,
  isLoadingMessages,
  isSending,
  unreadNewMessagesCount,
  isUserAtBottom,
  isViewingReply,
  targetUserStatus,
  selectedUserName,
  messageInput,
  editingMessage,
  replyingToMessage,
  swipedMessageId,
  isMobile,
  showStickerPicker,
  scrollToBottom: scrollToBottomForActiveTimeline,
  scrollToUnreadOrBottom: scrollToUnreadOrBottomForActiveTimeline,
  forceScrollToBottom,
  focusMessageInput: (options?: { cursorToEnd?: boolean }) => {
    chatInputBarRef.value?.focusInput(options)
  },
  adjustTextareaHeight: () => {
    chatInputBarRef.value?.adjustTextareaHeight()
  },
  onNamedRoomUnavailable: async (conversationKey) => {
    if (selectedUserId.value !== conversationKey) {
      return
    }
    clearActiveConversationState()
    await syncSelectedConversationRoute(null, '')
    void loadConversations()
  },
})

const {
  apiFetch,
  loadConversations,
  loadMessages,
  loadOlderMessages,
  markAsRead,
  sendMessage,
  sendMediaMessage,
  cancelTextMessage,
  cancelEdit,
  handleReply,
  cancelReply,
  startPolling,
  stopPolling,
  startStatusPolling,
  stopStatusPolling,
  hasOlderMessages,
  isLoadingOlderMessages
} = messagesLogic

const selectedConversation = computed(() => {
  return conversations.value.find(c => c.other_user_id === selectedUserId.value) ?? null
})

const selectedRoomKind = computed<'direct' | 'channel' | 'group'>(() => {
  const roomKind = selectedConversation.value?.room_kind
  return roomKind === 'channel' || roomKind === 'group' ? roomKind : 'direct'
})

const mediaLogic = useChatMedia({
  apiBaseUrl: props.apiBaseUrl,
  jwtToken: props.jwtToken,
  currentUserId: props.currentUserId,
  selectedUserId,
  selectedRoomKind,
  messages,
  error,
  isUploading,
  scrollToBottom,
  sendMediaMessage
})

const wsLogic = useChatWebSocket({
  selectedUserId,
  messageInput,
  messages,
  conversations,
  apiFetch,
  loadConversations,
  loadMessages,
  scrollToBottom,
  markAsRead,
  isUserAtBottom
})

const {
  imageCache,
  scheduleMediaHydration,
  downloadMedia,
  lightboxMedia,
  cancelUpload,
  cancelDocumentDownload,
  cancelMediaDownload,
  handleMediaClick: openMediaLightbox,
  setLightboxIndex,
  closeLightbox,
  handleMediaUploadWrapper
} = mediaLogic

type NormalizedLocation = {
  lat: number
  lng: number
  snapshot_id?: string | number
}

function normalizeLocationPayload(raw: unknown): NormalizedLocation | null {
  if (!raw || typeof raw !== 'object') {
    return null
  }

  const candidate = raw as Record<string, unknown>
  const lat = Number(candidate.lat ?? candidate.latitude)
  const lng = Number(candidate.lng ?? candidate.longitude)

  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return null
  }

  const normalized: NormalizedLocation = { lat, lng }
  const snapshotId = candidate.snapshot_id
  if (typeof snapshotId === 'string' || typeof snapshotId === 'number') {
    normalized.snapshot_id = snapshotId
  }

  return normalized
}

const selectedLocation = ref<NormalizedLocation | null>(null)

function handleLocationClick(msg: Message) {
  try {
    const loc = JSON.parse(msg.content)
    const normalized = normalizeLocationPayload(loc)
    if (!normalized) {
      throw new Error('Invalid location payload')
    }
    selectedLocation.value = normalized
  } catch {
    console.error('Failed to parse location data')
  }
}

const handleCancelSend = (msg: any) => {
  if (msg.message_type === 'text') {
    cancelTextMessage(msg.id);
  } else {
    cancelUpload(msg.id);
  }
};

const handleCancelDownload = (msg: Message) => {
  if (msg.message_type === 'document') {
    cancelDocumentDownload(msg.id)
    return
  }

  cancelMediaDownload(msg.id)
}

function closeLocationModal() {
  selectedLocation.value = null
}

const {
  typingUsers,
  activityTextByConversation,
  isTyping,
  handleTypingWrapper,
  sendTypingSignal
} = wsLogic

const LOAD_OLDER_TRIGGER_PX = 96

function captureMessagesContainerMetrics(container = messagesContainer.value): MessagesContainerMetrics | null {
  if (!container) return null

  return {
    clientHeight: container.clientHeight,
    scrollHeight: container.scrollHeight,
    scrollTop: container.scrollTop,
  }
}

function syncMessagesContainerMetrics(container = messagesContainer.value) {
  previousMessagesContainerMetrics = captureMessagesContainerMetrics(container)
}

function handleMessagesContainerResize() {
  const container = messagesContainer.value
  if (!container) return

  const previousMetrics = previousMessagesContainerMetrics
  const nextMetrics = captureMessagesContainerMetrics(container)
  if (!nextMetrics) return

  if (
    previousMetrics
    && previousMetrics.clientHeight > 0
    && nextMetrics.clientHeight > 0
    && previousMetrics.clientHeight !== nextMetrics.clientHeight
  ) {
    const previousDistanceFromBottom = previousMetrics.scrollHeight - previousMetrics.scrollTop - previousMetrics.clientHeight
    if (previousDistanceFromBottom <= BOTTOM_LAYOUT_LOCK_THRESHOLD_PX) {
      const heightDelta = nextMetrics.clientHeight - previousMetrics.clientHeight
      const adjustedScrollTop = previousMetrics.scrollTop - heightDelta
      const maxScrollTop = Math.max(0, nextMetrics.scrollHeight - nextMetrics.clientHeight)
      container.scrollTop = Math.max(0, Math.min(adjustedScrollTop, maxScrollTop))
      nextMetrics.scrollTop = container.scrollTop
    }
  }

  previousMessagesContainerMetrics = nextMetrics
}

function captureSelectionAnchor(messageId: number) {
  if (!isLoadingOlderMessages.value || !selectedUserId.value) {
    return
  }

  const container = messagesContainer.value
  const target = document.getElementById(`msg-${messageId}`)
  if (!container || !target) {
    return
  }

  const containerRect = container.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()

  pendingSelectionAnchor = {
    messageId,
    offsetTop: targetRect.top - containerRect.top,
    userId: selectedUserId.value,
  }
}

function restorePendingSelectionAnchor(container: HTMLElement, userId: number) {
  const anchor = pendingSelectionAnchor
  if (!anchor || anchor.userId !== userId) {
    return false
  }

  pendingSelectionAnchor = null

  const target = document.getElementById(`msg-${anchor.messageId}`)
  if (!target) {
    return false
  }

  const containerRect = container.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  const currentOffsetTop = targetRect.top - containerRect.top
  const offsetDelta = currentOffsetTop - anchor.offsetTop

  if (Math.abs(offsetDelta) < 1) {
    return true
  }

  const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight)
  container.scrollTop = Math.max(0, Math.min(container.scrollTop + offsetDelta, maxScrollTop))
  return true
}

function captureSelectionAnchorForItem(item: any) {
  if (!isLoadingOlderMessages.value) {
    return
  }

  if (item?.type === 'album' && Array.isArray(item.messages) && item.messages.length > 0) {
    captureSelectionAnchor(item.messages[0].id)
    return
  }

  if (typeof item?.id === 'number') {
    captureSelectionAnchor(item.id)
  }
}

function attachMessagesContainerResizeObserver(container: HTMLElement | null) {
  messagesContainerResizeObserver?.disconnect()
  messagesContainerResizeObserver = null

  if (!container || typeof ResizeObserver === 'undefined') {
    syncMessagesContainerMetrics(container)
    return
  }

  syncMessagesContainerMetrics(container)
  messagesContainerResizeObserver = new ResizeObserver(() => {
    handleMessagesContainerResize()
  })
  messagesContainerResizeObserver.observe(container)
}

function getViewportAnchorMessageId(container: HTMLElement) {
  const containerRect = container.getBoundingClientRect()
  const candidates = Array.from(container.querySelectorAll<HTMLElement>('.message-bubble[id^="msg-"]'))
    .map((element) => {
      const rect = element.getBoundingClientRect()
      return {
        element,
        rect,
        distance: Math.abs(rect.top - containerRect.top),
      }
    })
    .filter(({ rect }) => rect.bottom > containerRect.top && rect.top < containerRect.bottom)
    .sort((left, right) => left.distance - right.distance)

  const anchorElement = candidates[0]?.element
  if (!anchorElement) return null
  const id = Number(anchorElement.id.replace(/^msg-/, ''))
  return Number.isFinite(id) && id > 0 ? id : null
}

const handleMessagesScroll = async () => {
  handleScroll()

  const container = messagesContainer.value
  const userId = selectedUserId.value

  if (!container || !userId || isLoadingMessages.value || isLoadingOlderMessages.value || !hasOlderMessages.value) {
    return
  }

  if (container.scrollTop > LOAD_OLDER_TRIGGER_PX) {
    return
  }

  const prependAnchorMessageId = getViewportAnchorMessageId(container)
    ?? messages.value.find(message => message.id > 0)?.id
    ?? null
  const previousHeight = container.scrollHeight
  const previousTop = container.scrollTop
  const loadedCount = await loadOlderMessages(userId)

  if (loadedCount <= 0) {
    return
  }

  await nextTick()
  const restoredSelectionAnchor = restorePendingSelectionAnchor(container, userId)
  if (!restoredSelectionAnchor) {
    const preservedVirtualAnchor = prependAnchorMessageId !== null
      ? virtualTimelineRef.value?.preservePrependAnchor?.(prependAnchorMessageId) === true
      : false

    if (!preservedVirtualAnchor) {
      const newHeight = container.scrollHeight
      container.scrollTop = previousTop + (newHeight - previousHeight)
    }
  }
  syncMessagesContainerMetrics(container)
}

const isSelectedUserDeleted = computed(() => {
  const conv = conversations.value.find(c => c.other_user_id === selectedUserId.value)
  return conv?.room_kind && conv.room_kind !== 'direct' ? false : !!conv?.other_user_is_deleted
})

const pinnedMessage = computed(() => pinnedMessageState.value?.message ?? null)

const selectedRoomMemberCount = computed(() => selectedConversation.value?.member_count ?? null)
const selectedRoomIsMandatory = computed(() => !!selectedConversation.value?.is_mandatory)
const selectedRoomIsSystem = computed(() => !!selectedConversation.value?.is_system)
const isSelectedManagementRoom = computed(() => selectedRoomKind.value === 'group' && selectedRoomIsSystem.value)
const selectedAvatarFileId = computed(() => selectedConversation.value?.avatar_file_id ?? null)
const isCurrentUserCustomer = computed(() => props.currentUserIsCustomer === true)
const canStartNewConversation = computed(() => true)
const canCreateGroup = computed(() => !isCurrentUserCustomer.value)
const canCreateOptionalChannel = computed(() => (props.currentUserRole ?? null) === 'مدیر ارشد')
const canSendAdminBroadcast = computed(() => (props.currentUserRole ?? null) === 'مدیر ارشد')

const canSendToSelectedRoom = computed(() => {
  if (selectedRoomKind.value === 'direct') return true
  if (isSelectedManagementRoom.value) return false
  return selectedConversation.value?.can_send !== false
})

const isSelectedRoomReadOnly = computed(() => {
  return selectedRoomKind.value !== 'direct' && !canSendToSelectedRoom.value
})

const canManagePinnedMessages = computed(() => {
  if (!selectedUserId.value) return false
  if (isSelectedManagementRoom.value) return false
  if (selectedRoomKind.value === 'direct') return true
  return selectedConversation.value?.member_role === 'admin'
})

function isPinnedMessageInContext(msg?: Message | null) {
  return Boolean(pinnedMessage.value && msg && pinnedMessage.value.id === msg.id)
}

function canPinMessageInContext(msg?: Message | null, messageIds: number[] = []) {
  if (!msg) return false
  if (messageIds.length !== 1) return false
  if (msg.is_deleted) return false
  if (!isPersistedMessageId(msg.id)) return false
  return canManagePinnedMessages.value
}

function canEditMessageInContext(msg?: Message | null, messageIds: number[] = []) {
  if (!msg) return false
  if (messageIds.length !== 1) return false
  if (msg.sender_id !== props.currentUserId) return false
  if (msg.message_type !== 'text') return false
  if ((msg as any).forwarded_from_id || (msg as any).forwarded_from_name) return false
  const msgTime = new Date(msg.created_at).getTime()
  return (Date.now() - msgTime) <= 48 * 60 * 60 * 1000
}

function canDeleteMessageIdsInContext(messageIds: number[]) {
  const normalizedMessageIds = normalizeMessageIds(messageIds)
  if (normalizedMessageIds.length === 0) return false

  return normalizedMessageIds.every((messageId) => {
    const msg = messages.value.find(message => message.id === messageId)
    return isDeletableMessage(msg)
  })
}

const isContextMessagePinned = computed(() => {
  return isPinnedMessageInContext(contextMenu.value.message)
})

const canPinContextMessage = computed(() => {
  return canPinMessageInContext(contextMenu.value.message, contextMenu.value.messageIds)
})

const selectedRoomStatusText = computed(() => {
  if (selectedRoomKind.value === 'direct') {
    return targetUserStatus.value
  }
  if (isSelectedManagementRoom.value) return 'پیام مدیریت'
  return selectedRoomKind.value === 'group' ? 'گروه' : 'کانال'
})

const selectedConversationActivityText = computed(() => {
  if (selectedUserId.value == null) return ''
  return activityTextByConversation.value[selectedUserId.value] || ''
})

function isMandatoryPinnedConversation(conv: Conversation) {
  return isMandatoryPinnedConversationFromModel(conv)
}

function isConversationPinned(conv: Conversation) {
  return isMessengerConversationPinned(conv)
}

function getNextLocalPinOrder() {
  return getNextPinnedConversationOrder(conversations.value)
}

function compareConversationActivity(a: Conversation, b: Conversation) {
  return compareMessengerConversationActivity(a, b)
}

function isSameConversation(left: Conversation, right: Conversation) {
  if (left.room_kind === 'direct' || right.room_kind === 'direct') {
    return left.room_kind === right.room_kind && Number(left.other_user_id) === Number(right.other_user_id)
  }
  return left.room_kind === right.room_kind && Number(left.chat_id) === Number(right.chat_id)
}

function patchConversationState(target: Conversation, patch: Partial<Conversation>) {
  conversations.value = conversations.value.map((conversation) => (
    isSameConversation(conversation, target)
      ? { ...conversation, ...patch }
      : conversation
  ))
}

function upsertConversationState(nextConversation: Conversation) {
  const currentIndex = conversations.value.findIndex((conversation) => isSameConversation(conversation, nextConversation))
  if (currentIndex === -1) {
    conversations.value = [nextConversation, ...conversations.value]
    return
  }

  conversations.value = conversations.value.map((conversation, index) => (
    index === currentIndex
      ? { ...conversation, ...nextConversation }
      : conversation
  ))
}

function removeConversationStateByKey(conversationKey: number) {
  conversations.value = conversations.value.filter((conversation) => conversation.other_user_id !== conversationKey)
}

function upsertNamedRoomConversation(
  roomKind: 'group' | 'channel',
  chatId: number,
  patch: Partial<Conversation> & { other_user_name?: string },
) {
  const conversationKey = resolveRoomConversationKey(roomKind, chatId) ?? -Math.abs(chatId)
  const existing = conversations.value.find((conversation) => (
    conversation.other_user_id === conversationKey
    || (conversation.room_kind === roomKind && conversation.chat_id === chatId)
  ))

  upsertConversationState({
    id: existing?.id ?? chatId,
    other_user_name: patch.other_user_name || existing?.other_user_name || (roomKind === 'group' ? 'گروه' : 'کانال'),
    last_message_content: existing?.last_message_content ?? null,
    last_message_type: existing?.last_message_type ?? null,
    last_message_at: existing?.last_message_at ?? null,
    unread_count: existing?.unread_count ?? 0,
    ...existing,
    ...patch,
    chat_id: chatId,
    room_kind: roomKind,
    other_user_id: conversationKey,
  })

  if (selectedUserId.value === conversationKey && patch.other_user_name) {
    selectedUserName.value = patch.other_user_name
  }
}

function getPinnedMessagePreview(msg: Message | null | undefined) {
  if (!msg) return ''
  if (msg.is_deleted) return 'پیام حذف شد'
  if (msg.message_type === 'image') return 'تصویر'
  if (msg.message_type === 'video') return 'ویدئو'
  if (msg.message_type === 'voice') return 'پیام صوتی'
  if (msg.message_type === 'sticker') return 'استیکر'
  if (msg.message_type === 'location') return 'موقعیت'
  if (msg.message_type === 'document') {
    try {
      const parsed = JSON.parse(msg.content)
      if (typeof parsed?.file_name === 'string' && parsed.file_name.trim()) {
        return parsed.file_name
      }
    } catch {
      // noop
    }
    return 'فایل'
  }
  return msg.content || 'پیام سنجاق‌شده'
}

const pinnedMessageMetaText = computed(() => {
  const msg = pinnedMessage.value
  if (!msg) return ''
  if (selectedRoomKind.value === 'direct') return 'برای رفتن به پیام ضربه بزنید'
  return msg.sender_name || 'پیام سنجاق‌شده'
})

let pinnedMessageRequestId = 0
let pinnedMessageLoadTimer: ReturnType<typeof window.setTimeout> | null = null

function cancelScheduledPinnedMessageLoad() {
  if (pinnedMessageLoadTimer == null) return
  window.clearTimeout(pinnedMessageLoadTimer)
  pinnedMessageLoadTimer = null
}

function schedulePinnedMessageStateLoad(conversationKey: number) {
  cancelScheduledPinnedMessageLoad()
  pinnedMessageLoadTimer = window.setTimeout(() => {
    pinnedMessageLoadTimer = null
    if (selectedUserId.value !== conversationKey) return
    void loadPinnedMessageState()
  }, MESSENGER_PINNED_MESSAGE_DEFER_MS)
}

async function loadPinnedMessageState() {
  const conversation = selectedConversation.value
  const conversationKey = selectedUserId.value
  const requestId = ++pinnedMessageRequestId

  if (!conversation || !conversationKey) {
    pinnedMessageState.value = null
    return
  }

  try {
    const nextState = conversation.room_kind === 'direct'
      ? await apiFetch(`/chat/direct/${conversationKey}/pinned-message`)
      : await apiFetch(`/chat/rooms/${conversation.chat_id}/pinned-message`)

    if (requestId !== pinnedMessageRequestId || selectedUserId.value !== conversationKey) {
      return
    }

    pinnedMessageState.value = nextState?.message ? nextState : { ...nextState, message: null }
  } catch {
    if (requestId === pinnedMessageRequestId && selectedUserId.value === conversationKey) {
      pinnedMessageState.value = null
    }
  }
}

async function handlePinnedMessageToggle(targetMessage: Message, pinned: boolean) {
  if (!isPersistedMessageId(targetMessage.id)) return

  try {
    const nextState = await apiFetch(`/chat/messages/${targetMessage.id}/pin`, {
      method: 'POST',
      body: JSON.stringify({ pinned }),
    })
    pinnedMessageState.value = nextState?.message ? nextState : { ...nextState, message: null }
  } catch (err) {
    const message = err instanceof Error ? err.message : 'عملیات سنجاق پیام انجام نشد'
    showInlineToast(message)
  }
}

const sortedConversations = computed(() => {
  return sortMessengerConversations(conversations.value)
})

const totalUnread = computed(() => {
  return conversations.value.reduce((sum, c) => sum + c.unread_count, 0)
})

function isDeletableMessage(msg?: Message | null) {
  if (!msg) return false
  if (msg.sender_id !== props.currentUserId) return false
  const msgTime = new Date(msg.created_at).getTime()
  return (Date.now() - msgTime) <= 48 * 60 * 60 * 1000
}

function isPersistedMessageId(messageId: number) {
  return Number.isInteger(messageId) && messageId > 0
}

function removeLocalOnlyMessage(msg?: Message | null) {
  if (!msg) return

  if (msg.message_type === 'text' || msg.message_type === 'sticker') {
    cancelTextMessage(msg.id)
    return
  }

  cancelUpload(msg.id)

  const index = messages.value.findIndex(message => message.id === msg.id)
  if (index !== -1) {
    messages.value.splice(index, 1)
  }
}

function normalizeMessageReactions(rawReactions: unknown): MessageReaction[] {
  if (!Array.isArray(rawReactions)) {
    return []
  }

  return rawReactions
    .map((reaction) => {
      if (!reaction || typeof reaction !== 'object') {
        return null
      }

      const candidate = reaction as Record<string, unknown>
      const emoji = typeof candidate.emoji === 'string' ? candidate.emoji : ''
      const userId = Number(candidate.user_id)
      if (!emoji || !Number.isFinite(userId)) {
        return null
      }

      return {
        emoji,
        user_id: userId,
      }
    })
    .filter((reaction): reaction is MessageReaction => Boolean(reaction))
}

function buildOptimisticMessageReactions(rawReactions: unknown, currentUserId: number, emoji: string): MessageReaction[] {
  const normalized = normalizeMessageReactions(rawReactions)
  const previousOwnReaction = normalized.find((reaction) => Number(reaction.user_id) === currentUserId)
  const withoutOwnReaction = normalized.filter((reaction) => Number(reaction.user_id) !== currentUserId)

  if (previousOwnReaction?.emoji === emoji) {
    return withoutOwnReaction
  }

  return [
    ...withoutOwnReaction,
    {
      emoji,
      user_id: currentUserId,
    },
  ]
}

function applyMessageReactionState(messageId: number, reactions: unknown) {
  const messageIndex = messages.value.findIndex((message) => message.id === messageId)
  const normalizedReactions = normalizeMessageReactions(reactions)

  if (messageIndex !== -1) {
    const existingMessage = messages.value[messageIndex]
    if (!existingMessage) {
      return
    }

    messages.value[messageIndex] = {
      ...existingMessage,
      reactions: normalizedReactions,
    }
  }

  if (contextMenu.value.message?.id === messageId) {
    contextMenu.value = {
      ...contextMenu.value,
      message: contextMenu.value.message
        ? { ...contextMenu.value.message, reactions: normalizedReactions }
        : null,
    }
  }
}

function resetSelectionContext() {
  selectionModePurpose.value = 'default'
  activeAlbumSelectionId.value = null
}

const canEdit = computed(() => {
  return canEditMessageInContext(contextMenu.value.message, contextMenu.value.messageIds)
})

const canDelete = computed(() => {
  return canDeleteMessageIdsInContext(contextMenu.value.messageIds)
})

const canDeleteSelected = computed(() => {
   if (selectedMessages.value.length === 0) return false;
   return selectedMessages.value.every(id => {
      const msg = messages.value.find(m => m.id === id);
      if (!msg) return false;
      if (msg.sender_id !== props.currentUserId) return false;
      const msgTime = new Date(msg.created_at).getTime();
      return (Date.now() - msgTime) <= 48 * 60 * 60 * 1000;
   });
})

const canCopySelected = computed(() => {
   if (selectedMessages.value.length === 0) return false;
   return selectedMessages.value.every(id => {
      const msg = messages.value.find(m => m.id === id);
      return msg && msg.message_type === 'text';
   });
})

function getAlbumMessagesForMessage(msg: Message) {
  return getAlbumMessagesForMessageFromController(msg, messages.value)
}

function getContextMenuMessageIds(msg: Message) {
  return getContextMenuMessageIdsFromController(msg, messages.value)
}

async function toggleMessageReaction(msg: Message, emoji: string) {
  if (!isPersistedMessageId(msg.id)) {
    return
  }

  const currentMessage = messages.value.find((candidate) => candidate.id === msg.id) ?? msg
  const previousReactions = normalizeMessageReactions(currentMessage.reactions)
  const optimisticReactions = buildOptimisticMessageReactions(previousReactions, props.currentUserId, emoji)
  const mutationVersion = ++reactionMutationVersion
  pendingReactionMutationVersion.set(msg.id, mutationVersion)
  applyMessageReactionState(msg.id, optimisticReactions)

  try {
    const updatedMessage = await apiFetch(`/chat/messages/${msg.id}/reaction`, {
      method: 'POST',
      body: JSON.stringify({ emoji }),
    })

    if (pendingReactionMutationVersion.get(msg.id) !== mutationVersion) {
      return
    }

    pendingReactionMutationVersion.delete(msg.id)
    applyMessageReactionState(msg.id, updatedMessage?.reactions)

    const persistedOwnReaction = normalizeMessageReactions(updatedMessage?.reactions).find(
      (reaction) => Number(reaction.user_id) === props.currentUserId,
    )
    if (persistedOwnReaction?.emoji) {
      recordRecentMessageReaction(persistedOwnReaction.emoji)
    }
  } catch (err) {
    if (pendingReactionMutationVersion.get(msg.id) !== mutationVersion) {
      return
    }

    pendingReactionMutationVersion.delete(msg.id)
    applyMessageReactionState(msg.id, previousReactions)
    console.error('Failed to toggle message reaction:', err)
    alert('خطا در ثبت ری‌اکشن')
  }
}

function openForwardModalForIds(messageIds: number[]) {
  const normalized = sortMessageIdsByChatOrder(messageIds)
  if (normalized.length === 0) return

  forwardMessageIds.value = normalized
  applyComposerOverlayAction({ type: 'open_forward' })
}

function sortMessageIdsByChatOrder(messageIds: number[]) {
  return sortMessageIdsByMessageOrder(messageIds, messages.value)
}

function toggleSelectionBatch(messageIds: number[]) {
  const result = toggleMessageSelectionBatch(selectedMessages.value, messageIds, messages.value)
  selectedMessages.value = result.selectedMessageIds
  if (result.cleared) resetSelectionContext()
}

function buildForwardContent(message: Message, forwardedAlbumId: string | null, forwardedAlbumIndex?: number) {
  if (message.message_type !== 'image' && message.message_type !== 'video') {
    return message.content
  }

  try {
    const parsed = JSON.parse(message.content)
    if (!parsed || typeof parsed !== 'object') {
      return message.content
    }

    if (forwardedAlbumId) {
      parsed.album_id = forwardedAlbumId
      parsed.album_index = typeof forwardedAlbumIndex === 'number' ? forwardedAlbumIndex : 0
    } else {
      delete parsed.album_id
      delete parsed.album_index
    }

    return JSON.stringify(parsed)
  } catch {
    return message.content
  }
}

function normalizeForwardedSourceName(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function resolveForwardSource(message: Message) {
  const existingForwardName = normalizeForwardedSourceName((message as any).forwarded_from_name_override)
    || normalizeForwardedSourceName((message as any).forwarded_from_name)
  const existingForwardedFromRaw = (message as any).forwarded_from_id
  const existingForwardedFromId = typeof existingForwardedFromRaw === 'number' && existingForwardedFromRaw > 0
    ? existingForwardedFromRaw
    : null

  if (existingForwardName) {
    return {
      forwardedFromId: existingForwardedFromId,
      forwardedFromNameOverride: existingForwardName,
    }
  }

  const messageRoomKind = (message as any).room_kind
  const messageChatId = (message as any).chat_id
  const isCurrentChannelMessage = selectedRoomKind.value === 'channel'
    && (!messageChatId || messageChatId === selectedConversation.value?.chat_id)
  const isChannelMessage = messageRoomKind === 'channel' || isCurrentChannelMessage

  if (isChannelMessage) {
    const channelName = normalizeForwardedSourceName(selectedConversation.value?.other_user_name)
      || normalizeForwardedSourceName(selectedUserName.value)
    if (channelName) {
      return {
        forwardedFromId: null,
        forwardedFromNameOverride: channelName,
      }
    }
  }

  return {
    forwardedFromId: message.sender_id,
    forwardedFromNameOverride: null,
  }
}

function prepareForwardBatch(messageIds: number[]) {
  const orderedIds = sortMessageIdsByChatOrder(messageIds)
  const selectedSet = new Set(orderedIds)
  const albumAssignments = new Map<number, { albumId: string, albumIndex: number }>()
  const visitedAlbumIds = new Set<string>()

  orderedIds.forEach((messageId) => {
    const message = messages.value.find(candidate => candidate.id === messageId)
    if (!message) return

    const albumMeta = getAlbumMeta(message)
    if (!albumMeta.albumId || visitedAlbumIds.has(albumMeta.albumId)) return

    visitedAlbumIds.add(albumMeta.albumId)
    const albumMessages = getAlbumMessagesForMessage(message)
    if (albumMessages.length <= 1) return

    const isWholeAlbumSelected = albumMessages.every(albumMessage => selectedSet.has(albumMessage.id))
    if (!isWholeAlbumSelected) return

    const forwardedAlbumId = globalThis.crypto?.randomUUID?.()
      ?? `forward_album_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`

    albumMessages.forEach((albumMessage, index) => {
      if (!selectedSet.has(albumMessage.id)) return
      albumAssignments.set(albumMessage.id, { albumId: forwardedAlbumId, albumIndex: index })
    })
  })

  return orderedIds
    .map((messageId) => {
      const message = messages.value.find(candidate => candidate.id === messageId)
      if (!message) return null

      const albumAssignment = albumAssignments.get(message.id)
      const forwardSource = resolveForwardSource(message)

      return {
        message,
        content: buildForwardContent(message, albumAssignment?.albumId ?? null, albumAssignment?.albumIndex),
        forwardedFromId: forwardSource.forwardedFromId,
        forwardedFromNameOverride: forwardSource.forwardedFromNameOverride,
      }
    })
    .filter((item): item is {
      message: Message
      content: string
      forwardedFromId: number | null
      forwardedFromNameOverride: string | null
    } => Boolean(item))
}

async function deleteMessagesByIds(messageIds: number[], confirmMessage: string) {
  const normalized = normalizeMessageIds(messageIds)
  if (normalized.length === 0) return false

  if (!confirm(confirmMessage)) return false

  try {
    for (const msgId of normalized) {
      const msg = messages.value.find(message => message.id === msgId)
      if (!isDeletableMessage(msg)) continue

      if (!isPersistedMessageId(msgId)) {
        removeLocalOnlyMessage(msg)
        continue
      }

      await messagesLogic.apiFetch(`/chat/messages/${msgId}`, { method: 'DELETE' })
      const index = messages.value.findIndex(message => message.id === msgId)
      if (index !== -1) messages.value.splice(index, 1)
    }

    return true
  } catch (err) {
    console.error('Failed to delete messages:', err)
    alert('خطا در حذف پیام')
    return false
  }
}

function hydrateRenderedMedia(item: any) {
  if (item?.type === 'album') {
    const albumMessages = Array.isArray(item.messages) ? item.messages : []
    albumMessages.forEach((message: Message) => {
      scheduleMediaHydration(message.content, message.message_type, {
        // Keep the broader no-auto-download policy, but allow visible album images
        // to hydrate to their real file so received albums stop staying semi-blurry.
        allowNetwork: message.message_type === 'image'
      })
    })
    return
  }

  if (item?.content) {
    scheduleMediaHydration(item.content, item.message_type)
  }
}

const timelineControllerCache = createMessengerTimelineCache()

// Per-ISO-string cache for `formatDateForSeparator`. `formatIranDate(...)`
// is expensive on weak devices, and `groupedMessages` re-runs on every
// reactive tick. Same `created_at` string always resolves to the same
// Persian date label, so memoize by that string.
const dateSeparatorLabelCache = new Map<string, string>()

const groupedMessages = computed(() => groupMessengerMessages(messages.value, formatDateForSeparator, timelineControllerCache))
const timelineRenderBudget = computed(() => summarizeTimelineRenderBudget(groupedMessages.value))

function isAlbumTimelineItem(item: ChatTimelineItem): item is ChatAlbumTimelineItem {
  return 'type' in item && item.type === 'album'
}

function getTimelineItemMessage(item: ChatTimelineItem): Message {
  return isAlbumTimelineItem(item) ? item.messages[0]! : item
}

function getTimelineItemAlbumItems(item: ChatTimelineItem): Message[] {
  return isAlbumTimelineItem(item) ? item.messages : []
}

function scrollToTimelineGroup(group: ChatTimelineGroup) {
  const firstItem = group.items[0]
  if (!firstItem) return
  scrollToMessage(getTimelineItemMessage(firstItem).id)
}

function formatTime(dateStr: string) {
  return formatIranTime(dateStr)
}

function formatDateForSeparator(dateStr: string): string {
    if (!dateStr) return ''

    if (isTodayInIran(dateStr)) return 'امروز';
    if (isYesterdayInIran(dateStr)) return 'دیروز';

    // `formatIranDate(...)` is expensive on weak devices and
    // `groupedMessages` re-runs on every reactive tick. For non-today/
    // non-yesterday messages the resulting Persian date string is stable
    // given the same ISO input, so cache by ISO string.
    const cached = dateSeparatorLabelCache.get(dateStr)
    if (cached !== undefined) return cached

    const label = formatIranDate(dateStr);
    dateSeparatorLabelCache.set(dateStr, label)
    return label
}

const performSearch = () => {
    if (!searchQuery.value.trim()) {
        searchResults.value = []
        currentSearchIndex.value = 0
        return
    }
    if (searchDebounceTimeout.value) clearTimeout(searchDebounceTimeout.value)
    searchDebounceTimeout.value = setTimeout(async () => {
        isSearching.value = true
        try {
            const params = new URLSearchParams()
            params.append('q', searchQuery.value)
            if (selectedUserId.value) {
                params.append('chat_id', selectedUserId.value.toString())
            }
            const response = await messagesLogic.apiFetch(`/chat/search?${params.toString()}`)
            searchResults.value = response
            currentSearchIndex.value = 0 // Reset index on new search
            
            // If we are in-chat and got results, jump to the first one automatically
            if (selectedUserId.value && response.length > 0) {
              setTimeout(() => {
                scrollToMessage(response[0].id)
              }, 300)
            }
        } finally {
            isSearching.value = false
        }
    }, 500)
}

const toggleSearch = () => {
  const willOpenSearch = !isSearchActive.value
  if (willOpenSearch) {
    applyComposerOverlayAction({ type: 'enter_search' })
    searchQuery.value = ''
    searchResults.value = []
    currentSearchIndex.value = 0
    nextTick(() => {
      const input = document.getElementById('search-input')
      if (input) input.focus()
    })
  } else {
    applyComposerOverlayAction({ type: 'close_search' })
    searchQuery.value = ''
    searchResults.value = []
    currentSearchIndex.value = 0
  }
}

const nextSearchResult = async () => {
    if (searchResults.value.length === 0) return
    currentSearchIndex.value = (currentSearchIndex.value + 1) % searchResults.value.length
    const targetMsg = searchResults.value[currentSearchIndex.value]
    
    if (selectedUserId.value) {
        const isLoaded = messages.value.some(m => m.id === targetMsg.id)
        if (!isLoaded) {
            await loadMessages(selectedUserId.value, false, targetMsg.id)
        }
    }
    nextTick(() => {
        scrollToMessage(targetMsg.id)
    })
}

const prevSearchResult = async () => {
    if (searchResults.value.length === 0) return
    currentSearchIndex.value = (currentSearchIndex.value - 1 + searchResults.value.length) % searchResults.value.length
    const targetMsg = searchResults.value[currentSearchIndex.value]
    
    if (selectedUserId.value) {
        const isLoaded = messages.value.some(m => m.id === targetMsg.id)
        if (!isLoaded) {
            await loadMessages(selectedUserId.value, false, targetMsg.id)
        }
    }
    nextTick(() => {
        scrollToMessage(targetMsg.id)
    })
}
const handleToggleInChatList = () => {
    showInChatSearchList.value = !showInChatSearchList.value
    if (!showInChatSearchList.value && searchResults.value.length > 0) {
        const targetMsg = searchResults.value[currentSearchIndex.value]
        setTimeout(() => {
            scrollToMessage(targetMsg.id)
        }, 150)
    }
}
const handleSearchResultClick = async (msg: any) => {
    if (!selectedUserId.value) {
    applyComposerOverlayAction({ type: 'close_search' })
        searchResults.value = []
        currentSearchIndex.value = 0
        const otherId = msg.sender_id === props.currentUserId ? msg.receiver_id : msg.sender_id;
        selectedUserId.value = otherId;
        const conv = sortedConversations.value.find(c => c.other_user_id === otherId)
        selectedUserName.value = conv ? conv.other_user_name : 'User'
        pushBackState(() => {
          selectedUserId.value = null
          selectedUserName.value = ''
          messages.value = []
        })
        await loadMessages(otherId, false, msg.id)
        nextTick(() => scrollToMessage(msg.id))
    } else {
        showInChatSearchList.value = false
        const idx = searchResults.value.findIndex(r => r.id === msg.id)
        if (idx !== -1) currentSearchIndex.value = idx
        
        const isLoaded = messages.value.some(m => m.id === msg.id)
        if (isLoaded) {
            setTimeout(() => scrollToMessage(msg.id), 150)
        } else {
            await loadMessages(selectedUserId.value!, false, msg.id)
            setTimeout(() => scrollToMessage(msg.id), 250)
        }
    }
}

const toggleSelection = (msgId: number) => {
    const index = selectedMessages.value.indexOf(msgId)
    if (index === -1) {
        selectedMessages.value.push(msgId)
    } else {
        selectedMessages.value.splice(index, 1)
        if (selectedMessages.value.length === 0) {
          resetSelectionContext()
        }
    }
}

function startAlbumDownloadSelection(msg: Message, messageIds: number[]) {
  const albumId = getAlbumMeta(msg).albumId
  const normalized = sortMessageIdsByChatOrder(messageIds)
  if (!albumId || normalized.length === 0) return

  activeAlbumSelectionId.value = albumId
  selectionModePurpose.value = 'album-download'
  selectedMessages.value = normalized
}

function startAlbumForwardSelection(msg: Message, messageIds: number[]) {
  const albumId = getAlbumMeta(msg).albumId
  const normalized = sortMessageIdsByChatOrder(messageIds)
  if (!albumId || normalized.length === 0) return

  activeAlbumSelectionId.value = albumId
  selectionModePurpose.value = 'album-forward'
  selectedMessages.value = normalized
}

function startAlbumShareSelection(msg: Message, messageIds: number[]) {
  const albumId = getAlbumMeta(msg).albumId
  const normalized = sortMessageIdsByChatOrder(messageIds)
  if (!albumId || normalized.length === 0) return

  activeAlbumSelectionId.value = albumId
  selectionModePurpose.value = 'album-share'
  selectedMessages.value = normalized
}

function isAlbumInDownloadSelection(item: any) {
  if (!isAlbumActionSelectionMode.value || item?.type !== 'album' || !Array.isArray(item.messages) || item.messages.length === 0) {
    return false
  }

  return getAlbumMeta(item.messages[0]).albumId === activeAlbumSelectionId.value
}

function handleAlbumDownloadItemToggle(msg: Message) {
  if (!isAlbumActionSelectionMode.value) return
  if (getAlbumMeta(msg).albumId !== activeAlbumSelectionId.value) return
  toggleSelection(msg.id)
}

function handleGroupedItemSelection(item: any) {
  if (isAlbumActionSelectionMode.value) {
    return
  }

  captureSelectionAnchorForItem(item)

  if (item?.type === 'album' && Array.isArray(item.messages)) {
    toggleSelectionBatch(item.messages.map((message: Message) => message.id))
    return
  }

  if (typeof item?.id === 'number') {
    toggleSelection(item.id)
  }
}

const clearSelection = () => {
  pendingSelectionAnchor = null
  selectedMessages.value = []
  resetSelectionContext()
}

const selectConversation = (conv: Conversation) => {
  prepareConversationTransition()
  selectedUserId.value = conv.other_user_id
  selectedUserName.value = conv.other_user_name
  messagePanelError.value = ''
  warmMessengerInteractionChunks()
  loadMessages(conv.other_user_id)
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    messagePanelError.value = ''
  })
}

function openConversationFromRoute(targetId: number, fallbackName = '') {
  if (!Number.isInteger(targetId) || targetId === 0) {
    return
  }

  const resolvedName = resolveSelectedConversationName(targetId, fallbackName)

  if (targetId > 0) {
    startNewChat(targetId, resolvedName)
    return
  }

  prepareConversationTransition()
  selectedUserId.value = targetId
  selectedUserName.value = resolvedName
  messagePanelError.value = ''
  warmMessengerInteractionChunks()
  void loadMessages(targetId)
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    messagePanelError.value = ''
  })
}

const startNewChat = (userId: number, userName: string) => {
  prepareConversationTransition()
  const existingConversation = conversations.value.find((conversation) => conversation.other_user_id === userId)
  if (!existingConversation) {
    conversations.value.unshift({
      id: userId,
      other_user_id: userId,
      other_user_name: userName,
      last_message_content: null,
      last_message_type: null,
      last_message_at: null,
      unread_count: 0,
      room_kind: 'direct',
    })
  }
  selectedUserId.value = userId
  selectedUserName.value = userName
  messagePanelError.value = ''
  warmMessengerInteractionChunks()
  loadMessages(userId)
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    messagePanelError.value = ''
  })
}

function ensureRouteConversationPlaceholder(targetId: number, fallbackName = '') {
  if (conversations.value.some((conversation) => conversation.other_user_id === targetId)) {
    return
  }

  if (targetId <= 0 && !fallbackName) {
    return
  }

  const isNamedRoom = targetId < 0
  conversations.value.unshift({
    id: isNamedRoom ? Math.abs(targetId) : targetId,
    other_user_id: targetId,
    other_user_name: fallbackName || selectedUserName.value || 'گفتگوی جدید',
    last_message_content: null,
    last_message_type: null,
    last_message_at: null,
    unread_count: 0,
    room_kind: isNamedRoom ? 'group' : 'direct',
    chat_id: isNamedRoom ? Math.abs(targetId) : undefined,
  })
}

async function loadConversationsAfterRouteOpen(targetId: number, fallbackName = '') {
  suppressMissingRoomCleanupDuringRouteSync = true
  try {
    await loadConversations()
    if (selectedUserId.value !== targetId) {
      return
    }

    ensureRouteConversationPlaceholder(targetId, fallbackName)
    selectedUserName.value = resolveSelectedConversationName(targetId, selectedUserName.value || fallbackName)
  } finally {
    suppressMissingRoomCleanupDuringRouteSync = false
  }
}

const showNewChatModal = ref(false)
const showGroupManagerModal = ref(false)
const showChannelManagerModal = ref(false)
const showAdminBroadcastModal = ref(false)
const groupManagerChatId = ref<number | null>(null)
const channelManagerChatId = ref<number | null>(null)
let discardNextGroupManagerHistoryClose = false
let discardNextChannelManagerHistoryClose = false
const newChatModalBackStateActive = ref(false)
let closingNewChatModalFromBack = false

function closeNewChatModalForAction() {
  if (newChatModalBackStateActive.value) {
    newChatModalBackStateActive.value = false
    discardBackState()
  }
  showNewChatModal.value = false
}

const handleNewChatSearch = (userId: number, userName: string) => {
    closeNewChatModalForAction()
    startNewChat(userId, userName)
}

function openNewConversation() {
  showNewChatModal.value = true
}

function openGroupCreation() {
  if (!canCreateGroup.value) {
    showInlineToast(
      isCurrentUserCustomer.value
        ? 'مشتری در این فاز اجازه ساخت گروه جدید را ندارد'
        : 'اجازه ساخت گروه جدید وجود ندارد'
    )
    return
  }
  closeNewChatModalForAction()
  groupManagerChatId.value = null
  showGroupManagerModal.value = true
}

function openChannelCreation() {
  if (!canCreateOptionalChannel.value) return
  closeNewChatModalForAction()
  channelManagerChatId.value = null
  showChannelManagerModal.value = true
}

function openAdminBroadcastModal() {
  if (!canSendAdminBroadcast.value) return
  closeNewChatModalForAction()
  showAdminBroadcastModal.value = true
}

function closeGroupManager() {
  showGroupManagerModal.value = false
  groupManagerChatId.value = null
}

function closeAdminBroadcastModal() {
  showAdminBroadcastModal.value = false
}

function closeChannelManager() {
  channelManagerChatId.value = null
  showChannelManagerModal.value = false
  if (selectedUserId.value != null) {
    selectedUserName.value = resolveSelectedConversationName(selectedUserId.value)
  }
}

function clearActiveConversationState() {
  resetComposerDraftState()
  selectedUserId.value = null
  selectedUserName.value = ''
  messages.value = []
  error.value = ''
  messagePanelError.value = ''
  unreadNewMessagesCount.value = 0
  applyComposerOverlayAction({ type: 'close_composer_overlays' })
  closeContextMenu()
}

function clearMissingNamedRoomSelection() {
  if (suppressMissingRoomCleanupDuringRouteSync) {
    return
  }

  const activeConversationId = selectedUserId.value
  if (!activeConversationId || activeConversationId >= 0) {
    return
  }

  const stillExists = conversations.value.some((conversation) => conversation.other_user_id === activeConversationId)
  if (stillExists) {
    return
  }

  clearActiveConversationState()
}

async function handleChannelManagerOpenChannel(payload: { chatId: number; title: string }) {
  channelManagerChatId.value = null
  const conversationKey = resolveRoomConversationKey('channel', payload.chatId) ?? -Math.abs(payload.chatId)
  discardNextChannelManagerHistoryClose = true
  showChannelManagerModal.value = false
  upsertNamedRoomConversation('channel', payload.chatId, {
    other_user_name: payload.title,
  })

  const existingConversation = conversations.value.find((conversation) => (
    conversation.room_kind === 'channel' && conversation.chat_id === payload.chatId
  ))

  if (existingConversation) {
    selectConversation(existingConversation)
    await syncSelectedConversationRoute(existingConversation.other_user_id, existingConversation.other_user_name)
    return
  }

  prepareConversationTransition()
  selectedUserId.value = conversationKey
  selectedUserName.value = payload.title
  messagePanelError.value = ''
  void loadMessages(conversationKey)
  await syncSelectedConversationRoute(conversationKey, payload.title)
}

async function handleChannelManagerConversationRefresh(channel?: {
  id: number
  title: string
  avatar_file_id?: string | null
  member_count?: number
  is_system?: boolean
  is_mandatory?: boolean
} | null) {
  if (!channel) {
    void (async () => {
      await loadConversations()
      if (selectedUserId.value != null) {
        selectedUserName.value = resolveSelectedConversationName(selectedUserId.value, selectedUserName.value)
      }
    })()
    return
  }

  upsertNamedRoomConversation('channel', channel.id, {
    other_user_name: channel.title,
    avatar_file_id: channel.avatar_file_id ?? null,
    member_count: channel.member_count ?? null,
    is_system: channel.is_system,
    is_mandatory: channel.is_mandatory,
  })

  const conversationKey = resolveRoomConversationKey('channel', channel.id) ?? -Math.abs(channel.id)
  if (selectedUserId.value === conversationKey) {
    selectedUserName.value = channel.title
    await syncSelectedConversationRoute(conversationKey, channel.title)
  }
}

function openSelectedRoomManager() {
  if (!selectedConversation.value?.chat_id) return

  if (selectedRoomKind.value === 'group') {
    groupManagerChatId.value = selectedConversation.value.chat_id
    showGroupManagerModal.value = true
    return
  }

  if (selectedRoomKind.value === 'channel') {
    channelManagerChatId.value = selectedConversation.value.chat_id
    showChannelManagerModal.value = true
  }
}

async function handleGroupCreated(group: { id: number; title: string }) {
  discardNextGroupManagerHistoryClose = true
  showGroupManagerModal.value = false
  groupManagerChatId.value = null
  const conversationKey = resolveRoomConversationKey('group', group.id) ?? -Math.abs(group.id)
  upsertNamedRoomConversation('group', group.id, {
    other_user_name: group.title,
  })
  prepareConversationTransition()
  selectedUserId.value = conversationKey
  selectedUserName.value = group.title
  messagePanelError.value = ''
  void loadMessages(conversationKey)
  await syncSelectedConversationRoute(conversationKey, group.title)
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    messagePanelError.value = ''
  })
}

async function handleGroupUpdated(group: { id: number; title: string }) {
  const conversationKey = resolveRoomConversationKey('group', group.id) ?? -Math.abs(group.id)
  const shouldRefreshSelectedTitle = selectedUserId.value === conversationKey
  upsertNamedRoomConversation('group', group.id, {
    other_user_name: group.title,
  })
  if (shouldRefreshSelectedTitle) {
    selectedUserName.value = group.title
    await syncSelectedConversationRoute(conversationKey, group.title)
  }
}

async function handleGroupLeft(chatId: number) {
  const managedChatId = groupManagerChatId.value
  showGroupManagerModal.value = false
  groupManagerChatId.value = null
  const explicitConversationKey = resolveRoomConversationKey('group', chatId)
  const managedConversationKey = resolveRoomConversationKey('group', managedChatId)
  const routeConversationKey = Number(getRouteQueryValue(route.query.user_id as string | string[] | undefined) || 0)
  const shouldClearCurrentGroup = [explicitConversationKey, managedConversationKey]
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    .some((conversationKey) => selectedUserId.value === conversationKey || routeConversationKey === conversationKey)
  if (shouldClearCurrentGroup) {
    clearActiveConversationState()
    await syncSelectedConversationRoute(null, '')
  }
  const conversationKeyToRemove = explicitConversationKey ?? managedConversationKey
  if (typeof conversationKeyToRemove === 'number') {
    removeConversationStateByKey(conversationKeyToRemove)
  }
}

async function handleChannelLeft(chatId: number) {
  const managedChatId = channelManagerChatId.value
  showChannelManagerModal.value = false
  channelManagerChatId.value = null
  const explicitConversationKey = resolveRoomConversationKey('channel', chatId)
  const managedConversationKey = resolveRoomConversationKey('channel', managedChatId)
  const routeConversationKey = Number(getRouteQueryValue(route.query.user_id as string | string[] | undefined) || 0)
  const shouldClearCurrentChannel = [explicitConversationKey, managedConversationKey]
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    .some((conversationKey) => selectedUserId.value === conversationKey || routeConversationKey === conversationKey)
  if (shouldClearCurrentChannel) {
    clearActiveConversationState()
    await syncSelectedConversationRoute(null, '')
  }
  const conversationKeyToRemove = explicitConversationKey ?? managedConversationKey
  if (typeof conversationKeyToRemove === 'number') {
    removeConversationStateByKey(conversationKeyToRemove)
  }
}

type ConversationListAction = 'pin' | 'unpin' | 'move-pin-up' | 'move-pin-down' | 'mute' | 'unmute' | 'mark-unread' | 'delete' | 'leave' | 'unfollow'

function clearSelectedConversationIfMatches(conv: Conversation) {
  if (selectedUserId.value !== conv.other_user_id) return
  clearActiveConversationState()
}

async function handleConversationAction(payload: { action: ConversationListAction; conv: Conversation }) {
  const { action, conv } = payload
  let shouldReloadConversations = false

  try {
    if (conv.room_kind !== 'direct' && !conv.chat_id) {
      throw new Error('اطلاعات این گفتگو کامل نیست. لطفا دوباره تلاش کنید.')
    }

    if (action === 'pin' || action === 'unpin') {
      const endpoint = conv.room_kind === 'direct'
        ? `/chat/direct/${conv.other_user_id}/pin`
        : `/chat/rooms/${conv.chat_id}/pin`
      await apiFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify({ pinned: action === 'pin' }),
      })
      patchConversationState(conv, {
        is_pinned: action === 'pin',
        pinned_at: action === 'pin' ? new Date().toISOString() : null,
        pin_order: action === 'pin' ? getNextLocalPinOrder() : null,
      })
    } else if (action === 'move-pin-up' || action === 'move-pin-down') {
      const endpoint = conv.room_kind === 'direct'
        ? `/chat/direct/${conv.other_user_id}/pin-order`
        : `/chat/rooms/${conv.chat_id}/pin-order`
      await apiFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify({ direction: action === 'move-pin-up' ? 'up' : 'down' }),
      })
      shouldReloadConversations = true
    } else if (action === 'mute' || action === 'unmute') {
      const endpoint = conv.room_kind === 'direct'
        ? `/chat/direct/${conv.other_user_id}/mute`
        : `/chat/rooms/${conv.chat_id}/mute`
      await apiFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify({ muted: action === 'mute' }),
      })
      patchConversationState(conv, { is_muted: action === 'mute' })
      notificationStore.setConversationMuted(conv.other_user_id, action === 'mute')
    } else if (action === 'mark-unread') {
      const endpoint = conv.room_kind === 'direct'
        ? `/chat/direct/${conv.other_user_id}/mark-unread`
        : `/chat/rooms/${conv.chat_id}/mark-unread`
      await apiFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify({ unread: true }),
      })
      patchConversationState(conv, { unread_count: Math.max(1, Number(conv.unread_count || 0)) })
      notificationStore.incrementChatUnread(conv.other_user_id)
    } else if (action === 'delete') {
      await apiFetch(`/chat/direct/${conv.other_user_id}`, { method: 'DELETE' })
      clearSelectedConversationIfMatches(conv)
      removeConversationStateByKey(conv.other_user_id)
    } else if (action === 'leave') {
      await apiFetch(`/chat/groups/${conv.chat_id}/leave`, { method: 'POST' })
      clearSelectedConversationIfMatches(conv)
      removeConversationStateByKey(conv.other_user_id)
    } else if (action === 'unfollow') {
      await apiFetch(`/chat/channels/${conv.chat_id}/unfollow`, { method: 'POST' })
      clearSelectedConversationIfMatches(conv)
      removeConversationStateByKey(conv.other_user_id)
    }

    if (shouldReloadConversations) {
      await loadConversations()
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : 'عملیات گفتگو انجام نشد'
    showInlineToast(message)
  }
}

const showContextMenu = (event: Event, msg: Message) => {
  if (isAlbumActionSelectionMode.value) return

  const messageIds = getContextMenuMessageIds(msg)

  let clientX = 0
  let clientY = 0
  if (event instanceof MouseEvent) {
    event.preventDefault()
    clientX = event.clientX
    clientY = event.clientY
  } else if (event instanceof TouchEvent && event.touches.length > 0) {
    const touch = event.touches[0]
    if (touch) {
      clientX = touch.clientX
      clientY = touch.clientY
    }
  }

  const menuModel = buildMessengerContextMenuModel({
    messageType: msg.message_type,
    isAlbumSelection: messageIds.length > 1,
    supportsFileShare: CONTEXT_MENU_SUPPORTS_FILE_SHARE,
    canEdit: canEditMessageInContext(msg, messageIds),
    canDelete: canDeleteMessageIdsInContext(messageIds),
    canPin: canPinMessageInContext(msg, messageIds),
    isPinnedMessage: isPinnedMessageInContext(msg),
    showReactionRow: !msg.is_deleted && AVAILABLE_MESSAGE_REACTIONS.length > 0,
    hasOverflowReactions: AVAILABLE_MESSAGE_REACTIONS.length > 6,
    isReactionPickerExpanded: false,
  })
  const style = getMessengerContextMenuStyle({
    x: clientX,
    y: clientY,
    menuWidth: menuModel.menuWidth,
    menuHeight: menuModel.menuHeight,
    viewportWidth: typeof window !== 'undefined' ? window.innerWidth : 400,
    viewportHeight: typeof window !== 'undefined' ? window.innerHeight : 800,
  })

  const expectedMessageId = msg.id
  const snapshotVersion = ++contextMenuSnapshotVersion

  contextMenu.value = {
    visible: true,
    x: Number.parseFloat(style.left) || clientX,
    y: Number.parseFloat(style.top) || clientY,
    message: msg,
    messageIds,
    style,
    menuModel,
  }
  markMessengerPerformance('message-context-menu-open')

  nextTick(() => {
    const runSnapshot = () => {
      if (
        snapshotVersion !== contextMenuSnapshotVersion
        || !contextMenu.value.visible
        || contextMenu.value.message?.id !== expectedMessageId
      ) {
        return
      }

      const root = typeof document !== 'undefined'
        ? document.querySelector('.chat-view') || document.body
        : null
      if (root) {
        recordMessengerDomSnapshot('message-context-menu-open', root, {
          selectedUserId: selectedUserId.value,
          messageCount: messages.value.length,
        })
      }
    }

    scheduleMessengerDiagnosticTask(runSnapshot, {
      deferMs: MESSENGER_INTERACTION_DIAGNOSTIC_DEFER_MS,
      timeoutMs: 1200,
      fallbackDelayMs: 240,
    })
  })
}

function closeContextMenu() {
  contextMenuSnapshotVersion += 1
  contextMenu.value = { visible: false, x: 0, y: 0, message: null, messageIds: [], style: null, menuModel: null }
}

function closeCurrentOverlayThen(closeCurrent: () => void, openNext: () => void) {
  closeCurrent()
  void nextTick(() => {
    openNext()
  })
}

function closeTransientActionSurfacesForNavigation() {
  if (contextMenu.value.visible) {
    closeContextMenu()
  }
  if (showForwardModal.value) {
    closeForwardModal()
  }
  if (lightboxMedia.value) {
    closeLightbox()
  }
}

const handleMessageClick = (event: Event, msg: Message) => {
    if (isAlbumActionSelectionMode.value) {
      event.preventDefault()
      return
    }

    if (isSelectionMode.value) {
      event.preventDefault()
      captureSelectionAnchor(msg.id)
      toggleSelection(msg.id)
      return
    }

    showContextMenu(event, msg)
}

const handleMessageReactionToggle = ({ msg, emoji }: { msg: Message, emoji: string }) => {
  void toggleMessageReaction(msg, emoji)
}

const handleContextMenuReaction = (emoji: string) => {
  const msg = contextMenu.value.message
  closeContextMenu()
  if (!msg) {
    return
  }
  void toggleMessageReaction(msg, emoji)
}

const handleMediaInteraction = (msg: Message) => {
  if (isAlbumActionSelectionMode.value) {
    handleAlbumDownloadItemToggle(msg)
    return
  }

  if (isSelectionMode.value) {
    captureSelectionAnchor(msg.id)
    const messageIds = getContextMenuMessageIds(msg)
    if (messageIds.length > 1) {
      toggleSelectionBatch(messageIds)
    } else {
      toggleSelection(msg.id)
    }
    return
  }

  openMediaLightbox(msg)
}

const handleEditMessage = () => {
  const msg = contextMenu.value.message;
  if (!msg) return;
  beginEditTransition(msg)
};

const handleDeleteMessage = async () => {
  const messageIds = normalizeMessageIds(contextMenu.value.messageIds)
  if (messageIds.length === 0) return

  const deleted = await deleteMessagesByIds(
    messageIds,
    messageIds.length > 1 ? 'آیا از حذف این آلبوم اطمینان دارید؟' : 'آیا از حذف این پیام اطمینان دارید؟'
  )

  if (deleted) {
    if (pinnedMessage.value && messageIds.includes(pinnedMessage.value.id)) {
      pinnedMessageState.value = null
    }
    closeContextMenu()
  }
}

async function handlePinMessage() {
  const msg = contextMenu.value.message
  if (!msg) {
    closeContextMenu()
    return
  }
  closeContextMenu()
  await handlePinnedMessageToggle(msg, !isContextMessagePinned.value)
}

async function handlePinnedBannerUnpin() {
  const msg = pinnedMessage.value
  if (!msg) return
  await handlePinnedMessageToggle(msg, false)
}

function handlePinnedBannerClick() {
  if (!pinnedMessage.value) return
  void scrollToMessage(pinnedMessage.value.id)
}

const handleCopyMessage = () => {
  const msg = contextMenu.value.message;
  if (!msg || msg.message_type !== 'text') { closeContextMenu(); return; }
  navigator.clipboard.writeText(msg.content).then(() => closeContextMenu());
};

const handleSaveMedia = () => {
  const msg = contextMenu.value.message;
  if (!msg) { closeContextMenu(); return; }
  try {
    const parsed = JSON.parse(msg.content);
    const fileId = parsed?.file_id;
    if (!fileId) return;
    const cachedUrl = imageCache.value[fileId];
    if (!cachedUrl) {
      // Download first, then save
      downloadMedia(msg);
      closeContextMenu();
      return;
    }
    // Trigger browser download via hidden <a> tag
    const ext = msg.message_type === 'video' ? 'mp4' : 'jpg';
    const a = document.createElement('a');
    a.href = cachedUrl;
    a.download = `${fileId}.${ext}`;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } catch (e) {
    console.error('Save media error:', e);
  }
  closeContextMenu();
};

const getMediaFileId = (msg: Message) => {
  try {
    const parsed = JSON.parse(msg.content)
    return typeof parsed?.file_id === 'string' && parsed.file_id ? parsed.file_id : null
  } catch {
    return null
  }
}

const buildMediaDownloadUrl = (fileId: string) => {
  const token = props.jwtToken ? encodeURIComponent(props.jwtToken) : ''
  return `${props.apiBaseUrl}/api/chat/files/${encodeURIComponent(fileId)}?token=${token}`
}

const triggerBrowserDownload = (url: string, fileName: string) => {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
}

const buildMediaDownloadFileName = (msg: Message, index = 0, totalCount = 1) => {
  const fileId = getMediaFileId(msg) || `media_${msg.id}`
  const extension = msg.message_type === 'video' ? 'mp4' : 'jpg'
  return totalCount > 1
    ? `${String(index + 1).padStart(2, '0')}_${fileId}.${extension}`
    : `${fileId}.${extension}`
}

async function saveMessageMediaToDevice(msg: Message, index = 0, totalCount = 1) {
  if (msg.message_type !== 'image' && msg.message_type !== 'video') return

  const fileId = getMediaFileId(msg)
  if (!fileId) return

  const fileName = buildMediaDownloadFileName(msg, index, totalCount)
  const cachedUrl = imageCache.value[fileId] || msg.local_blob_url
  if (cachedUrl) {
    triggerBrowserDownload(cachedUrl, fileName)
    return
  }

  const downloadUrl = buildMediaDownloadUrl(fileId)

  try {
    const response = await fetch(downloadUrl)
    if (!response.ok) throw new Error(`Download failed with status ${response.status}`)

    const blob = await response.blob()
    const objectUrl = URL.createObjectURL(blob)
    triggerBrowserDownload(objectUrl, fileName)
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  } catch (error) {
    console.error('Album media download failed, falling back to direct URL:', error)
    triggerBrowserDownload(downloadUrl, fileName)
  }
}

function handleSaveAlbum() {
  const msg = contextMenu.value.message
  const messageIds = normalizeMessageIds(contextMenu.value.messageIds)
  if (!msg || messageIds.length <= 1) {
    closeContextMenu()
    return
  }

  startAlbumDownloadSelection(msg, messageIds)
  closeContextMenu()
}

function inferMediaMime(msg: Message): string {
  if (msg.message_type === 'image') return 'image/jpeg'
  if (msg.message_type === 'video') return 'video/mp4'
  if (msg.message_type === 'voice') return 'audio/webm'
  if (msg.message_type === 'document') {
    try {
      const parsed = JSON.parse(msg.content)
      if (typeof parsed?.mime_type === 'string' && parsed.mime_type) return parsed.mime_type
    } catch { /* noop */ }
    return 'application/octet-stream'
  }
  return 'application/octet-stream'
}

function inferMediaFileName(msg: Message, fileId: string, index = 0): string {
  try {
    const parsed = JSON.parse(msg.content)
    if (typeof parsed?.file_name === 'string' && parsed.file_name.trim()) return parsed.file_name
  } catch { /* noop */ }
  const prefix = String(index + 1).padStart(2, '0')
  if (msg.message_type === 'image') return `${prefix}_${fileId}.jpg`
  if (msg.message_type === 'video') return `${prefix}_${fileId}.mp4`
  if (msg.message_type === 'voice') return `${prefix}_${fileId}.webm`
  return `${prefix}_${fileId}`
}

async function ensureMessageBlobInFileCache(msg: Message): Promise<string | null> {
  const fileId = getMediaFileId(msg)
  if (!fileId) return null
  try {
    const entry = await ensureFileCached(fileId, inferMediaFileName(msg, fileId), {
      mimeType: inferMediaMime(msg),
      localUrl: imageCache.value[fileId] || msg.local_blob_url || undefined,
      fileUrl: buildMediaDownloadUrl(fileId),
    })
    return entry ? fileId : null
  } catch {
    return null
  }
}

function showInlineToast(text: string) {
  const id = 'chat-file-share-toast'
  const existing = document.getElementById(id)
  if (existing) { try { existing.remove() } catch { /* noop */ } }
  const toast = document.createElement('div')
  toast.id = id
  toast.textContent = text
  toast.style.cssText = [
    'position:fixed', 'left:50%', 'bottom:90px', 'transform:translateX(-50%)',
    'background:rgba(40,40,40,0.94)', 'color:#fff', 'padding:10px 16px',
    'border-radius:10px', 'font-size:13px', 'z-index:2147483600',
    'max-width:88vw', 'text-align:center', 'box-shadow:0 4px 18px rgba(0,0,0,0.25)',
    'direction:rtl', 'font-family:inherit', 'pointer-events:none',
    'transition:opacity .25s ease', 'opacity:1',
  ].join(';')
  document.body.appendChild(toast)
  setTimeout(() => { toast.style.opacity = '0' }, 2200)
  setTimeout(() => { try { toast.remove() } catch { /* noop */ } }, 2600)
}

async function handleShareMessage() {
  const msg = contextMenu.value.message
  closeContextMenu()
  if (!msg) return
  const fileId = getMediaFileId(msg)
  if (!fileId) {
    showInlineToast('این پیام قابل اشتراک‌گذاری نیست')
    return
  }
  const seeded = await ensureMessageBlobInFileCache(msg)
  if (!seeded) {
    showInlineToast('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
    return
  }
  const shared = await cachedShareFileGlobal(
    fileId,
    inferMediaFileName(msg, fileId),
    inferMediaMime(msg),
    buildMediaDownloadUrl(fileId),
  )
  if (!shared) showInlineToast('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
}

function handleShareAlbum() {
  const msg = contextMenu.value.message
  const messageIds = normalizeMessageIds(contextMenu.value.messageIds)
  if (!msg || messageIds.length <= 1) {
    closeContextMenu()
    return
  }
  startAlbumShareSelection(msg, messageIds)
  closeContextMenu()
}

async function handleShareSelectedAlbumMessages() {
  const orderedMessages = sortMessageIdsByChatOrder(selectedMessages.value)
    .map((id) => messages.value.find((m) => m.id === id))
    .filter((m): m is Message => Boolean(m))

  if (orderedMessages.length === 0) {
    clearSelection()
    return
  }

  const fileIds: string[] = []
  for (const m of orderedMessages) {
    const fid = await ensureMessageBlobInFileCache(m)
    if (fid) fileIds.push(fid)
  }
  if (fileIds.length === 0) {
    showInlineToast('اشتراک‌گذاری این فایل‌ها در این مرورگر پشتیبانی نمی‌شود')
    clearSelection()
    return
  }
  const ok = await shareMultipleFiles(fileIds)
  if (!ok) showInlineToast('اشتراک‌گذاری این فایل‌ها در این مرورگر پشتیبانی نمی‌شود')
  clearSelection()
}

async function handleDownloadSelectedAlbumMessages() {
  const orderedMessages = sortMessageIdsByChatOrder(selectedMessages.value)
    .map((messageId) => messages.value.find(message => message.id === messageId))
    .filter((message): message is Message => {
      return Boolean(message) && (message!.message_type === 'image' || message!.message_type === 'video')
    })

  if (orderedMessages.length === 0) {
    clearSelection()
    return
  }

  for (const [index, message] of orderedMessages.entries()) {
    await saveMessageMediaToDevice(message, index, orderedMessages.length)

    if (index < orderedMessages.length - 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 140))
    }
  }

  clearSelection()
}

const handleDeleteSelected = async () => {
  if (selectedMessages.value.length === 0) return
  const deleted = await deleteMessagesByIds(selectedMessages.value, 'آیا از حذف پیام‌های انتخاب شده اطمینان دارید؟')
  if (deleted) clearSelection()
}

const handleCopySelected = () => {
    const textToCopy = selectedMessages.value.map(id => {
        const msg = messages.value.find(m => m.id === id);
        return msg?.message_type === 'text' ? msg.content : '';
    }).filter(Boolean).join('\n\n');
    
    if (textToCopy) navigator.clipboard.writeText(textToCopy).then(() => clearSelection());
}

const handleReplySelected = () => {
    if (selectedMessages.value.length === 1) {
        const msg = messages.value.find(m => m.id === selectedMessages.value[0]);
        if (msg) {
            handleReply(msg);
            clearSelection();
        }
    }
}

function openForwardModal() {
  const normalized = sortMessageIdsByChatOrder(selectedMessages.value)
  if (normalized.length === 0) return
  forwardMessageIds.value = normalized
  applyComposerOverlayAction({ type: 'open_forward' })
}

async function handleSendVoice(blob: Blob, durationMs: number) {
  if (!selectedUserId.value || !blob) return
  if (selectedRoomKind.value !== 'direct') return
  const file = new File([blob], `voice_${Date.now()}.webm`, { type: blob.type || 'audio/webm' })
  // Pack durationMs into the file object so handleMediaUploadWrapper can extract it
  ;(file as any).durationMs = durationMs
  await handleMediaUploadWrapper(file, null, 0, 1, { roomKindOverride: selectedRoomKind.value })
}

async function handleSendLocation(lat: number, lng: number) {
  if (!selectedUserId.value) return
  if (isSelectedRoomReadOnly.value) return
  const normalized = normalizeLocationPayload({ lat, lng })
  if (!normalized) return

  const content = JSON.stringify(normalized)
  try {
    const endpoint = buildChatSendEndpoint(selectedUserId.value)
    const payload = buildChatSendBody(selectedUserId.value, {
      content,
      message_type: 'location',
    })
    const newMsg = await messagesLogic.apiFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify(payload)
    })
    messages.value.push(newMsg)
    scrollToBottom()
  } catch (e: any) {
    error.value = e.message
  }
}

function closeForwardModal() {
  forwardMessageIds.value = []
  applyComposerOverlayAction({ type: 'close_forward' })
}

async function forwardSelectedMessages(targets: ChatForwardTarget | ChatForwardTarget[]) {
  const targetList = Array.isArray(targets) ? targets : [targets]
  const supportedTargets = targetList.filter(
    (target): target is ChatForwardTarget => target.kind === 'user' || isNamedRoomKind(target.kind)
  )
  const hasUnsupportedTargets = targetList.length !== supportedTargets.length

  if (supportedTargets.length === 0) {
    if (hasUnsupportedTargets) alert('هدایت پیام به این مقصد هنوز فعال نشده است')
    return
  }

  const forwardIds = sortMessageIdsByChatOrder(
    forwardMessageIds.value.length > 0 ? forwardMessageIds.value : selectedMessages.value,
  )
  if (forwardIds.length === 0) {
    closeForwardModal()
    return
  }

  const preparedBatch = prepareForwardBatch(forwardIds)
  if (preparedBatch.length === 0) {
    closeForwardModal()
    return
  }

  const getTargetKey = (target: ChatForwardTarget) => `${target.kind}:${target.id}`
  const getConversationIdForTarget = (target: ChatForwardTarget) => {
    if (isNamedRoomKind(target.kind)) {
      return resolveRoomConversationKey(target.kind, target.id) ?? target.id
    }
    return target.id
  }
  const findConversationForTarget = (target: ChatForwardTarget) => {
    if (isNamedRoomKind(target.kind)) {
      const conversationId = getConversationIdForTarget(target)
      return conversations.value.find(conversation => (
        conversation.room_kind === target.kind
        && (conversation.chat_id === target.id || conversation.other_user_id === conversationId)
      ))
    }

    return conversations.value.find(conversation => (
      (conversation.room_kind === 'direct' || !conversation.room_kind) && conversation.other_user_id === target.id
    ))
  }
  const previewSource = preparedBatch.at(-1)?.message
  const getForwardPreviewText = (message: Message | undefined) => {
    if (!message) return ''
    if (message.message_type === 'image') return 'تصویر'
    if (message.message_type === 'video') return 'ویدئو'
    if (message.message_type === 'voice') return 'پیام صوتی'
    if (message.message_type === 'location') return 'موقعیت مکانی'
    return message.content || ''
  }
  const forwardedPreviewContent = preparedBatch.length > 1
    ? `${preparedBatch.length} پیام هدایت شد`
    : getForwardPreviewText(previewSource) || 'پیام هدایت شد'
  const patchForwardedTargetConversation = (target: ChatForwardTarget) => {
    const targetConversationId = getConversationIdForTarget(target)
    const targetConversation = findConversationForTarget(target)
    if (target.kind === 'user') {
      if (targetConversation) {
        patchConversationState(targetConversation, {
          last_message_at: new Date().toISOString(),
          last_message_type: previewSource?.message_type || targetConversation.last_message_type,
          last_message_content: forwardedPreviewContent,
        })
      }
      return
    }

    upsertNamedRoomConversation(target.kind, target.id, {
      other_user_name: targetConversation?.other_user_name || target.title,
      last_message_at: new Date().toISOString(),
      last_message_type: previewSource?.message_type || targetConversation?.last_message_type || 'text',
      last_message_content: forwardedPreviewContent,
    })
    if (selectedUserId.value === targetConversationId) {
      selectedUserName.value = targetConversation?.other_user_name || target.title
    }
  }

  // Close modal and clear selection immediately so the UI unblocks.
  // Sending happens in parallel in the background.
  selectedMessages.value = []
  forwardMessageIds.value = []
  applyComposerOverlayAction({ type: 'close_forward' })

  // Build flat (target, item) send tasks so we can parallelize across
  // both targets and items, not sequentially per-target then per-item.
  type ForwardTask = {
    target: ChatForwardTarget
    item: (typeof preparedBatch)[number]
  }
  const tasks: ForwardTask[] = []
  for (const target of supportedTargets) {
    for (const item of preparedBatch) {
      tasks.push({ target, item })
    }
  }

  const failuresByTarget = new Map<string, number>()
  const titleByTarget = new Map<string, string>()
  const totalByTarget = new Map<string, number>()
  supportedTargets.forEach((target) => {
    const targetKey = getTargetKey(target)
    titleByTarget.set(targetKey, target.title)
    totalByTarget.set(targetKey, preparedBatch.length)
  })

  // Adaptive concurrency: keep weak devices safe, boost on capable devices.
  // Each task is a single lightweight POST (no file upload), so we can push
  // higher than upload concurrency. Cap relative to task count.
  const navAny = navigator as any
  const hwCores = typeof navAny?.hardwareConcurrency === 'number' ? navAny.hardwareConcurrency : 4
  const memGb = typeof navAny?.deviceMemory === 'number' ? navAny.deviceMemory : 4
  const baseConcurrency = hwCores >= 8 && memGb >= 4 ? 8
    : hwCores >= 4 ? 6
    : 3
  const concurrency = Math.max(2, Math.min(baseConcurrency, tasks.length))

  isSending.value = true
  const runWorker = async () => {
    while (true) {
      const task = tasks.shift()
      if (!task) return
      try {
        const targetConversationId = getConversationIdForTarget(task.target)
        const endpoint = buildChatSendEndpoint(targetConversationId)
        const payload = buildChatSendBody(targetConversationId, {
          content: task.item.content,
          message_type: task.item.message.message_type,
        })
        if (task.item.forwardedFromId !== null) {
          payload.forwarded_from_id = task.item.forwardedFromId
        }
        if (task.item.forwardedFromNameOverride) {
          payload.forwarded_from_name_override = task.item.forwardedFromNameOverride
        }

        await messagesLogic.apiFetch(endpoint, {
          method: 'POST',
          body: JSON.stringify(payload)
        })
      } catch (forwardError) {
        console.error('Failed to forward message:', task.item.message.id, 'to', task.target.id, forwardError)
        const targetKey = getTargetKey(task.target)
        failuresByTarget.set(targetKey, (failuresByTarget.get(targetKey) ?? 0) + 1)
      }
    }
  }

  try {
    const workers = Array.from({ length: concurrency }, () => runWorker())
    await Promise.all(workers)

    const fullyFailedTargets: string[] = []
    let anySuccess = false
    supportedTargets.forEach((target) => {
      const targetKey = getTargetKey(target)
      const failed = failuresByTarget.get(targetKey) ?? 0
      const total = totalByTarget.get(targetKey) ?? 0
      if (failed >= total) {
        fullyFailedTargets.push(titleByTarget.get(targetKey) ?? target.title)
      } else {
        anySuccess = true
      }
    })

    if (!anySuccess) {
      alert('خطا در هدایت پیام‌ها')
      return
    }

    if (fullyFailedTargets.length > 0) {
      alert(`بخشی از پیام‌ها برای این مقاصد هدایت نشدند: ${fullyFailedTargets.join('، ')}`)
    } else if (hasUnsupportedTargets) {
      alert('برخی مقصدها هنوز برای هدایت پشتیبانی نمی‌شوند')
    }

    supportedTargets.forEach((target) => {
      const targetKey = getTargetKey(target)
      const failed = failuresByTarget.get(targetKey) ?? 0
      const total = totalByTarget.get(targetKey) ?? 0
      if (failed < total) {
        patchForwardedTargetConversation(target)
      }
    })

    // If only one target, open that chat (previous UX). For multi-target, stay on current chat.
    if (supportedTargets.length === 1) {
      const only = supportedTargets[0]!
      const targetConversationId = getConversationIdForTarget(only)
      const targetConversation = findConversationForTarget(only)
      const targetName = targetConversation?.other_user_name || only.title

      if (isNamedRoomKind(only.kind)) {
        showAttachmentMenu.value = false
        showStickerPicker.value = false
      }

      if (selectedUserId.value !== targetConversationId) {
        selectedUserId.value = targetConversationId
        selectedUserName.value = targetName
        void loadMessages(targetConversationId)
      } else {
        selectedUserName.value = targetName
        void loadMessages(targetConversationId, true)
      }
    }
  } finally {
    isSending.value = false
  }
}

async function handleRecoveryAction(payload: {
  action: 'approve' | 'reject' | 'request_identity'
  recoveryId: string
  userId?: number | null
}) {
  const actionPath = payload.action === 'approve'
    ? 'approve'
    : payload.action === 'reject'
      ? 'reject'
      : 'request-identity'

  try {
    const response = await apiFetch(`/sessions/recovery/${payload.recoveryId}/${actionPath}`, {
      method: 'POST',
    })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(data?.detail || 'انجام این عملیات ممکن نشد')
    }

    if (selectedUserId.value && selectedRoomKind.value === 'direct') {
      void loadMessages(selectedUserId.value, true)
    }
  } catch (err: any) {
    window.alert(err?.message || 'انجام این عملیات ممکن نشد')
  }
}

function handleRecoveryRealtimeUpdate(payload: { user_id?: number | string }) {
  const recoveryUserId = Number(payload?.user_id)
  if (!Number.isFinite(recoveryUserId)) {
    return
  }
  if (selectedRoomKind.value !== 'direct' || selectedUserId.value !== recoveryUserId) {
    return
  }

  void loadMessages(recoveryUserId, true)
}

const handleReplyMessage = () => {
  const msg = contextMenu.value.message
  if (!msg) return
  beginReplyTransition(msg)
}

const handleForwardMessage = () => {
  const msg = contextMenu.value.message
  const messageIds = normalizeMessageIds(contextMenu.value.messageIds)

  if (msg && messageIds.length > 1) {
    closeCurrentOverlayThen(closeContextMenu, () => {
      startAlbumForwardSelection(msg, messageIds)
    })
    return
  }

  closeCurrentOverlayThen(closeContextMenu, () => {
    openForwardModalForIds(messageIds)
  })
}

function handleForwardSelectedAlbumMessages() {
  const orderedIds = sortMessageIdsByChatOrder(selectedMessages.value)
  if (orderedIds.length === 0) {
    clearSelection()
    return
  }

  // Reset purpose before opening modal so the forward modal isn't considered a selection mode.
  selectionModePurpose.value = 'default'
  activeAlbumSelectionId.value = null
  forwardMessageIds.value = orderedIds
  applyComposerOverlayAction({ type: 'open_forward' })
}

function handleAlbumReplyItem(msg: Message) {
  beginReplyTransition(msg)
}

function handleAlbumForwardItem(msg: Message) {
  openForwardModalForIds([msg.id])
}

async function handleAlbumDeleteItem(msg: Message) {
  await deleteMessagesByIds([msg.id], 'آیا از حذف این مدیا اطمینان دارید؟')
}

function handleLightboxNavigate(index: number) {
  setLightboxIndex(index)
}

function handleLightboxReply(msgId: number) {
  const msg = messages.value.find(message => message.id === msgId)
  if (!msg) return

  closeCurrentOverlayThen(closeLightbox, () => {
    beginReplyTransition(msg)
  })
}

function handleLightboxForward(msgId: number) {
  closeCurrentOverlayThen(closeLightbox, () => {
    openForwardModalForIds([msgId])
  })
}

async function handleLightboxShare(msgId: number) {
  const msg = messages.value.find(message => message.id === msgId)
  if (!msg) return
  const fileId = getMediaFileId(msg)
  if (!fileId) {
    showInlineToast('این پیام قابل اشتراک‌گذاری نیست')
    return
  }
  const seeded = await ensureMessageBlobInFileCache(msg)
  if (!seeded) {
    showInlineToast('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
    return
  }
  const shared = await cachedShareFileGlobal(
    fileId,
    inferMediaFileName(msg, fileId),
    inferMediaMime(msg),
    buildMediaDownloadUrl(fileId),
  )
  if (!shared) showInlineToast('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
}

async function handleLightboxDelete(msgId: number) {
  const deleted = await deleteMessagesByIds([msgId], 'آیا از حذف این مدیا اطمینان دارید؟')
  if (deleted) {
    closeLightbox()
  }
}

async function goBack() {
  if (contextMenu.value.visible) {
    closeContextMenu()
    return
  }

  if (selectedUserId.value) {
    clearActiveConversationState()
    discardBackState()
    await syncSelectedConversationRoute(null, '')
  } else {
    emit('back')
  }
}

function navigateToPublicProfile(target?: {
  id?: number | null
  account_name?: string
  highlight_accountant_user_id?: number | null
  highlight_accountant_relation_display_name?: string | null
} | null) {
  const normalizedId = Number(target?.id)
  if (!Number.isInteger(normalizedId) || normalizedId <= 0) {
    return
  }

  const normalizedAccountName = typeof target?.account_name === 'string' && target.account_name.trim()
    ? target.account_name.trim()
    : ''
  const normalizedHighlightAccountantUserId = Number(target?.highlight_accountant_user_id)
  const hasHighlightAccountantUserId = Number.isInteger(normalizedHighlightAccountantUserId) && normalizedHighlightAccountantUserId > 0
  const normalizedHighlightRelationDisplayName = typeof target?.highlight_accountant_relation_display_name === 'string' && target.highlight_accountant_relation_display_name.trim()
    ? target.highlight_accountant_relation_display_name.trim()
    : ''

  const query: Record<string, string> = {}
  if (normalizedAccountName) {
    query.account_name = normalizedAccountName
  }
  if (hasHighlightAccountantUserId) {
    query.highlight_accountant_user_id = String(normalizedHighlightAccountantUserId)
  }
  if (normalizedHighlightRelationDisplayName) {
    query.highlight_accountant_relation_display_name = normalizedHighlightRelationDisplayName
  }

  closeTransientActionSurfacesForNavigation()

  window.setTimeout(() => {
    void router.push({
      name: 'public-profile',
      params: { id: String(normalizedId) },
      query: Object.keys(query).length > 0 ? query : undefined,
    })
  }, 0)
}

function viewProfile() {
  if (selectedUserId.value && selectedRoomKind.value === 'direct') {
    const conversationProfileTarget = resolveConversationProfileTarget(selectedConversation.value)
    navigateToPublicProfile(conversationProfileTarget ?? {
      id: selectedUserId.value,
      account_name: selectedUserName.value,
    })
    return
  }

  navigateToPublicProfile({ id: props.currentUserId })
}

function openPublicProfile(payload?: {
  id?: number | null
  account_name?: string
  highlight_accountant_user_id?: number | null
  highlight_accountant_relation_display_name?: string | null
}) {
  navigateToPublicProfile(payload)
}

const handleCall = () => alert('قابلیت تماس به زودی اضافه می‌شود')

function handleTypingForCurrentRoom() {
  if (!selectedUserId.value) {
    return
  }
  handleTypingWrapper()
}

const SWIPE_THRESHOLD = 100 
const handleTouchStart = (e: TouchEvent, msg: Message) => {
  if (e.touches.length > 0) {
    const touch = e.touches[0]
    if (touch) {
      touchStartX.value = touch.clientX
      touchCurrentX.value = touch.clientX
      swipedMessageId.value = msg.id
    }
  }
}
const handleTouchMove = (e: TouchEvent, msg: Message) => {
  if (swipedMessageId.value !== msg.id) return
  if (e.touches.length > 0) {
    const touch = e.touches[0]
    if (touch) {
      touchCurrentX.value = touch.clientX
      if (Math.abs(touchCurrentX.value - touchStartX.value) > 10) {
        if (longPressTimer.value) {
          clearTimeout(longPressTimer.value)
          longPressTimer.value = null
        }
      }
    }
  }
}

const handleTouchEnd = (e: TouchEvent, msg: Message) => {
  if (swipedMessageId.value !== msg.id) return
  const diff = touchStartX.value - touchCurrentX.value
  const isSent = msg.sender_id === props.currentUserId
  const isValidSwipe = isSent ? (diff > SWIPE_THRESHOLD) : (diff < -SWIPE_THRESHOLD)

  if (isValidSwipe) handleReply(msg)
  swipedMessageId.value = null
  touchStartX.value = 0
  touchCurrentX.value = 0
}

watch(selectedUserId, (newVal) => {
  chatRoomLifecycle.enterRoom(newVal)
  pendingSelectionAnchor = null
  clearMessengerTimelineCache(timelineControllerCache)
  dateSeparatorLabelCache.clear()
  if (newVal) {
    if (!registeredRoomLifecycleCleanups.has(newVal)) {
      registeredRoomLifecycleCleanups.add(newVal)
      chatRoomLifecycle.registerRoomCleanup(newVal, () => {
        closeContextMenu()
        closeLightbox()
        closeLocationModal()
        showInChatSearchList.value = false
        chatUiStore.clearRoomOverlays()
        chatSessionStore.clearRoomRuntime(newVal)
      })
    }
    pinnedMessageState.value = null
    schedulePinnedMessageStateLoad(newVal)
    if (newVal > 0 && selectedRoomKind.value === 'direct') {
      startStatusPolling(newVal, { initialDelayMs: MESSENGER_STATUS_POLL_DEFER_MS })
    } else {
      stopStatusPolling()
    }
    nextTick(() => {
      syncMessagesContainerMetrics()
    })
  } else {
    cancelScheduledPinnedMessageLoad()
    stopStatusPolling()
    previousMessagesContainerMetrics = null
    pinnedMessageState.value = null
    chatSessionStore.setActiveRoom(null)
  }
})

watch(selectedConversation, (conversation) => {
  const conversationKey = selectedUserId.value
  if (!conversation || !conversationKey) return
  chatSessionStore.setActiveRoom(conversationKey, {
    kind: selectedRoomKind.value,
    title: conversation.other_user_name,
    avatarFileId: conversation.avatar_file_id ?? null,
    statusText: selectedConversationActivityText.value,
  })
  pinnedMessageState.value = null
  schedulePinnedMessageStateLoad(conversationKey)
})

watch(messages, (nextMessages) => {
  const conversationKey = selectedUserId.value
  if (!conversationKey) return
  chatMessagesStore.setMessages(conversationKey, nextMessages)
}, { deep: false })

watch([isSearchActive, searchQuery], () => {
  chatUiStore.setSearch(isSearchActive.value, searchQuery.value)
})

watch([selectedMessages, selectionModePurpose], () => {
  chatUiStore.setSelection(selectedMessages.value, selectionModePurpose.value)
}, { deep: false })

watch(messagesContainer, (container) => {
  attachMessagesContainerResizeObserver(container)
})

watch(() => timelineRenderBudget.value.itemCount, (itemCount) => {
  recordMessengerMetric('timeline-render-item-count', itemCount, 'count', {
    groupCount: timelineRenderBudget.value.groupCount,
    mediaItemCount: timelineRenderBudget.value.mediaItemCount,
    virtualizationCandidate: timelineRenderBudget.value.virtualizationCandidate ? 'true' : 'false',
  })
}, { flush: 'post' })

onMounted(async () => {
  isLoading.value = true
  updateReducedMotionPreference()

  if (props.targetUserId) {
    isLoading.value = false
    openConversationFromRoute(props.targetUserId, props.targetUserName || '')
    void loadConversationsAfterRouteOpen(props.targetUserId, props.targetUserName || '')
  } else {
    await loadConversations()
    isLoading.value = false
  }

  onGlobalWs(WS_NOTIFICATION_EVENTS.sessionRecoveryUpdate, handleRecoveryRealtimeUpdate)
  startPolling({ initialDelayMs: MESSENGER_INITIAL_POLL_DEFER_MS })
  updateIsMobile()
  window.addEventListener('resize', updateIsMobile)
  nextTick(() => {
    attachMessagesContainerResizeObserver(messagesContainer.value)
  })
})

watch(() => props.targetUserId, (newId) => {
  if (newId && newId !== selectedUserId.value) {
    openConversationFromRoute(newId, props.targetUserName || '')
  }
})

watch(conversations, () => {
  clearMissingNamedRoomSelection()
})

watch(isSelectionMode, (isEnabled) => {
  if (isEnabled) {
    markMessengerPerformance('selection-mode-enter')
    recordMessengerMetric('selection-selected-count', selectedMessages.value.length, 'count', {
      purpose: selectionModePurpose.value,
    })
    applyComposerOverlayAction({ type: 'enter_selection' })
    if (!selectionBackStateActive.value) {
      selectionBackStateActive.value = true
      pushBackState(() => {
        clearingSelectionFromBack = true
        clearSelection()
        clearingSelectionFromBack = false
      })
    }
    return
  }

  markMessengerPerformance('selection-mode-exit')
  recordMessengerMetric('selection-selected-count', 0, 'count', {
    purpose: selectionModePurpose.value,
  })

  if (selectionBackStateActive.value) {
    selectionBackStateActive.value = false
    if (!clearingSelectionFromBack) {
      popBackState()
    }
  }
})

watch(showAttachmentMenu, (isOpen) => {
  if (isOpen) {
    applyComposerOverlayAction({ type: 'close_sticker' })
  }
})

watch(showNewChatModal, (isOpen) => {
  if (isOpen) {
    if (!newChatModalBackStateActive.value) {
      newChatModalBackStateActive.value = true
      pushBackState(() => {
        newChatModalBackStateActive.value = false
        closingNewChatModalFromBack = true
        showNewChatModal.value = false
        closingNewChatModalFromBack = false
      })
    }
    return
  }

  if (newChatModalBackStateActive.value) {
    newChatModalBackStateActive.value = false
    if (!closingNewChatModalFromBack) {
      popBackState()
    }
  }
})

bindOverlayBackState(() => showForwardModal.value, () => {
  closeForwardModal()
})

bindOverlayBackState(() => contextMenu.value.visible, () => {
  closeContextMenu()
})

bindOverlayBackState(() => showStickerPicker.value, () => {
  applyComposerOverlayAction({ type: 'close_sticker' })
})

bindOverlayBackState(() => isSearchActive.value, () => {
  applyComposerOverlayAction({ type: 'close_search' })
  searchQuery.value = ''
  searchResults.value = []
  currentSearchIndex.value = 0
})

bindOverlayBackState(() => showInChatSearchList.value, () => {
  showInChatSearchList.value = false
})

bindOverlayBackState(() => Boolean(selectedLocation.value), () => {
  closeLocationModal()
})

bindOverlayBackState(() => Boolean(lightboxMedia.value), () => {
  closeLightbox()
})

bindOverlayBackState(() => showGroupManagerModal.value, () => {
  closeGroupManager()
}, () => {
  const shouldDiscard = discardNextGroupManagerHistoryClose
  discardNextGroupManagerHistoryClose = false
  return shouldDiscard
})

bindOverlayBackState(() => showChannelManagerModal.value, () => {
  closeChannelManager()
}, () => {
  const shouldDiscard = discardNextChannelManagerHistoryClose
  discardNextChannelManagerHistoryClose = false
  return shouldDiscard
})

bindOverlayBackState(() => showAdminBroadcastModal.value, () => {
  closeAdminBroadcastModal()
})

function handleToggleAttachment(composerValue?: string) {
  const canOpenAttachment = !(selectedRoomKind.value === 'channel' && !canSendToSelectedRoom.value)
  if (!canOpenAttachment) {
    return
  }
  if (!showAttachmentMenu.value) {
    const nextComposerValue = typeof composerValue === 'string' ? composerValue : messageInput.value
    if (nextComposerValue !== messageInput.value) {
      messageInput.value = nextComposerValue
    }
    const trimmedComposerValue = nextComposerValue.trim()
    pendingMediaCaptionReservation = trimmedComposerValue
      ? {
          value: trimmedComposerValue,
          consumed: false,
        }
      : null
  }
  applyComposerOverlayAction({ type: 'toggle_attachment', canOpen: canOpenAttachment })
}

function claimComposerCaptionForMedia(albumId?: string | null, albumIndex?: number) {
  if (!pendingMediaCaptionReservation) {
    const trimmedComposerValue = messageInput.value.trim()
    if (!trimmedComposerValue) {
      return { caption: '', onCaptionApplied: undefined as (() => void) | undefined }
    }

    pendingMediaCaptionReservation = {
      value: trimmedComposerValue,
      consumed: false,
    }
  }

  if (!pendingMediaCaptionReservation || pendingMediaCaptionReservation.consumed) {
    return { caption: '', onCaptionApplied: undefined as (() => void) | undefined }
  }

  const normalizedAlbumIndex = typeof albumIndex === 'number' ? albumIndex : 0
  const shouldUseCaption = !albumId || normalizedAlbumIndex === 0
  if (!shouldUseCaption) {
    return { caption: '', onCaptionApplied: undefined as (() => void) | undefined }
  }

  const reservedCaption = pendingMediaCaptionReservation.value
  pendingMediaCaptionReservation.consumed = true

  return {
    caption: reservedCaption,
    onCaptionApplied: () => {
      if (messageInput.value.trim() === reservedCaption) {
        messageInput.value = ''
      }

      if (pendingMediaCaptionReservation?.value === reservedCaption) {
        pendingMediaCaptionReservation = null
      }
    },
  }
}

async function handleAttachmentMediaSelection(
  file: File,
  albumId?: string | null,
  albumIndex?: number,
  albumSize?: number,
) {
  const { caption, onCaptionApplied } = claimComposerCaptionForMedia(albumId, albumIndex)
  await handleMediaUploadWrapper(file, albumId, albumIndex, albumSize, {
    caption,
    onCaptionApplied,
    roomKindOverride: selectedRoomKind.value,
  })
}

async function handleAttachmentFileSelection(file: File) {
  await handleMediaUploadWrapper(file, null, 0, 1, {
    sendAsDocument: true,
    roomKindOverride: selectedRoomKind.value,
  })
}

const chatRoomContainerState = computed(() => ({
  selectedUserId: selectedUserId.value,
  isSearchActive: isSearchActive.value,
  showInChatSearchList: showInChatSearchList.value,
  searchResults: searchResults.value,
  searchQuery: searchQuery.value,
  sortedConversations: sortedConversations.value,
  currentUserId: props.currentUserId,
  isLoadingMessages: isLoadingMessages.value,
  prefersReducedMotion: prefersReducedMotion.value,
  pinnedMessage: pinnedMessage.value,
  isLoadingOlderMessages: isLoadingOlderMessages.value,
  messagePanelError: messagePanelError.value,
  messages: messages.value,
  groupedMessages: groupedMessages.value,
  timelineRenderBudget: timelineRenderBudget.value,
  isSelectionMode: isSelectionMode.value,
  activeAlbumSelectionId: activeAlbumSelectionId.value,
  selectionMemoKey: selectionMemoKey.value,
  selectedMessages: selectedMessages.value,
  selectedUserName: selectedUserName.value,
  imageCache: imageCache.value,
  isSelectedManagementRoom: isSelectedManagementRoom.value,
  showScrollButton: showScrollButton.value,
  unreadMentionMessages: unreadMentionMessages.value,
  unreadNewMessagesCount: unreadNewMessagesCount.value,
  currentSearchIndex: currentSearchIndex.value,
  isAlbumDownloadSelectionMode: isAlbumDownloadSelectionMode.value,
  isAlbumForwardSelectionMode: isAlbumForwardSelectionMode.value,
  isAlbumShareSelectionMode: isAlbumShareSelectionMode.value,
  messageInput: messageInput.value,
  showStickerPicker: showStickerPicker.value,
  editingMessage: editingMessage.value,
  replyingToMessage: replyingToMessage.value,
  canDeleteSelected: canDeleteSelected.value,
  canCopySelected: canCopySelected.value,
  isSending: isSending.value,
  isSelectedUserDeleted: isSelectedUserDeleted.value,
  isSelectedRoomReadOnly: isSelectedRoomReadOnly.value,
  readOnlyBannerText: isSelectedManagementRoom.value
    ? 'این گفتگوی مدیریتی فقط برای اطلاع‌رسانی است.'
    : (selectedRoomKind.value === 'channel' ? 'فقط مدیران کانال امکان ارسال پیام دارند.' : undefined),
  selectedRoomKind: selectedRoomKind.value,
  isUploading: isUploading.value,
  showAttachmentMenu: showAttachmentMenu.value,
  showForwardModal: showForwardModal.value,
  keepInactiveMessengerSurfacesMounted,
  contextMenu: contextMenu.value,
  canEdit: canEdit.value,
  canDelete: canDelete.value,
  canPinContextMessage: canPinContextMessage.value,
  isContextMessagePinned: isContextMessagePinned.value,
  availableMessageReactions: [...AVAILABLE_MESSAGE_REACTIONS],
  lightboxMedia: lightboxMedia.value,
  selectedLocation: selectedLocation.value,
}))

const chatRoomContainerHandlers = {
  setMessagesContainer: (element: Element | null) => {
    messagesContainer.value = element instanceof HTMLElement ? element : null
  },
  setVirtualTimelineRef: (component: any) => {
    virtualTimelineRef.value = component && typeof component.scrollToMessage === 'function'
      ? component
      : null
  },
  setChatInputBarRef: (component: any) => {
    chatInputBarRef.value = component
  },
  updateMessageInput: (value: string) => {
    messageInput.value = value
  },
  updateStickerPickerOpen: (value: boolean) => {
    showStickerPicker.value = value
  },
  updateAttachmentMenu: (value: boolean) => {
    showAttachmentMenu.value = value
  },
  handleSendText: (text: string) => {
    messageInput.value = text
    sendMessage()
  },
  retryLoadMessages: () => {
    messagePanelError.value = ''
    if (selectedUserId.value) {
      void loadMessages(selectedUserId.value)
    }
  },
  handleMessagesScroll,
  scrollToTimelineGroup,
  getTimelineItemMessage,
  isAlbumTimelineItem,
  getTimelineItemAlbumItems,
  isAlbumInDownloadSelection,
  handleReply,
  handleGroupedItemSelection,
  handleMessageClick,
  showContextMenu,
  scrollToMessage,
  handleMediaInteraction,
  handleLocationClick,
  downloadMedia,
  handleCancelSend,
  handleCancelDownload,
  handleAlbumReplyItem,
  handleAlbumForwardItem,
  handleAlbumDeleteItem,
  handleAlbumDownloadItemToggle,
  handleMessageReactionToggle,
  handleRecoveryAction,
  openPublicProfile,
  hydrateRenderedMedia,
  handleScrollButtonClick,
  nextSearchResult,
  prevSearchResult,
  handleToggleInChatList,
  clearSelection,
  handleDownloadSelectedAlbumMessages,
  handleForwardSelectedAlbumMessages,
  handleShareSelectedAlbumMessages,
  cancelEdit,
  cancelReply,
  handleDeleteSelected,
  handleReplySelected,
  handleCopySelected,
  openForwardModal,
  handleToggleAttachment,
  handleSendVoice,
  handleTypingForCurrentRoom,
  handleAttachmentMediaSelection,
  handleAttachmentFileSelection,
  handleSendLocation,
  closeForwardModal,
  forwardSelectedMessages,
  handleContextMenuReaction,
  handleReplyMessage,
  handleForwardMessage,
  handleCopyMessage,
  handleEditMessage,
  handleDeleteMessage,
  handlePinMessage,
  closeContextMenu,
  handleSaveMedia,
  handleSaveAlbum,
  handleShareMessage,
  handleShareAlbum,
  closeLightbox,
  handleLightboxNavigate,
  handleLightboxReply,
  handleLightboxForward,
  handleLightboxShare,
  handleLightboxDelete,
  closeLocationModal,
  handleSearchResultClick,
}

onUnmounted(() => {
  chatRoomLifecycle.leaveRoom(selectedUserId.value)
  cancelScheduledPinnedMessageLoad()
  messagesContainerResizeObserver?.disconnect()
  messagesContainerResizeObserver = null
  window.removeEventListener('resize', updateIsMobile)
  offGlobalWs(WS_NOTIFICATION_EVENTS.sessionRecoveryUpdate, handleRecoveryRealtimeUpdate)
  stopPolling()
  stopStatusPolling()
  clearBackStack()
})

// Types/Typescript requires this to be exposed properly
defineExpose({
  startNewChat,
  __testHooks: {
    state: {
      messages,
      conversations,
      selectedUserId,
      selectedLocation,
      showScrollButton,
      unreadNewMessagesCount,
      contextMenu,
      lightboxMedia,
      selectedMessages,
      selectionModePurpose,
      activeAlbumSelectionId,
      forwardMessageIds,
      showForwardModal,
      showNewChatModal,
      showAttachmentMenu,
      showStickerPicker,
      showGroupManagerModal,
      showChannelManagerModal,
      showAdminBroadcastModal,
      groupManagerChatId,
      channelManagerChatId,
      selectedUserName,
      selectedRoomKind,
      isSearchActive,
      isSearching,
      searchQuery,
      searchResults,
      currentSearchIndex,
      showInChatSearchList,
      longPressTimer,
      messageInput,
      editingMessage,
      replyingToMessage,
      pendingSelectionAnchor: () => pendingSelectionAnchor,
      timelineRenderBudget,
    },
    normalizeLocationPayload,
    handleLocationClick,
    handleCancelSend,
    handleCancelDownload,
    closeLocationModal,
    captureMessagesContainerMetrics,
    syncMessagesContainerMetrics,
    handleMessagesContainerResize,
    captureSelectionAnchor,
    restorePendingSelectionAnchor,
    handleMessagesScroll,
    isMandatoryPinnedConversation,
    isConversationPinned,
    getNextLocalPinOrder,
    getComposerOverlayState,
    applyComposerOverlayAction,
    compareConversationActivity,
    isSameConversation,
    patchConversationState,
    getPinnedMessagePreview,
    isDeletableMessage,
    isPersistedMessageId,
    removeLocalOnlyMessage,
    normalizeMessageIds,
    normalizeMessageReactions,
    buildOptimisticMessageReactions,
    applyMessageReactionState,
    getAlbumMeta,
    getAlbumMessagesForMessage,
    getContextMenuMessageIds,
    buildForwardContent,
    prepareForwardBatch,
    formatTime,
    formatDateForSeparator,
    isUserOnline,
    handleScrollButtonClick,
    performSearch,
    toggleSearch,
    nextSearchResult,
    prevSearchResult,
    toggleSelection,
    handleToggleInChatList,
    handleSearchResultClick,
    openConversationFromRoute,
    handleNewChatSearch,
    openNewConversation,
    openGroupCreation,
    openChannelCreation,
    openAdminBroadcastModal,
    closeGroupManager,
    closeChannelManager,
    closeAdminBroadcastModal,
    startAlbumDownloadSelection,
    startAlbumForwardSelection,
    startAlbumShareSelection,
    isAlbumInDownloadSelection,
    handleAlbumDownloadItemToggle,
    handleGroupedItemSelection,
    clearMissingNamedRoomSelection,
    handleChannelManagerOpenChannel,
    handleChannelManagerConversationRefresh,
    openSelectedRoomManager,
    handleGroupCreated,
    handleGroupUpdated,
    handleGroupLeft,
    handleChannelLeft,
    handleConversationAction,
    hydrateRenderedMedia,
    openForwardModal,
    closeForwardModal,
    forwardSelectedMessages,
    handleSendVoice,
    handleSendLocation,
    handleRecoveryAction,
    handleRecoveryRealtimeUpdate,
    handleShareAlbum,
    handleSaveAlbum,
    saveMessageMediaToDevice,
    inferMediaMime,
    inferMediaFileName,
    ensureMessageBlobInFileCache,
    handleShareSelectedAlbumMessages,
    handleDownloadSelectedAlbumMessages,
    handleDeleteSelected,
    handleCopySelected,
    handleReplySelected,
    handleReplyMessage,
    handleEditMessage,
    handleForwardMessage,
    handleForwardSelectedAlbumMessages,
    handleAlbumReplyItem,
    handleAlbumForwardItem,
    handleAlbumDeleteItem,
    handleLightboxNavigate,
    handleLightboxReply,
    handleLightboxForward,
    handleLightboxShare,
    handleLightboxDelete,
    goBack,
    viewProfile,
    openPublicProfile,
    handleTypingForCurrentRoom,
    handleTouchStart,
    handleTouchMove,
    handleTouchEnd,
  },
})
</script>


<template>
  <ChatShell>
    <!-- Header - Telegram Style -->
    <ChatHeader
      :isSelectionMode="isSelectionMode"
      :selectedUserId="selectedUserId"
      :selectedUserName="selectedUserName"
      :selectedAvatarFileId="selectedAvatarFileId"
      :selectedRoomKind="selectedRoomKind"
      :apiBaseUrl="apiBaseUrl"
      :targetUserStatus="selectedRoomStatusText"
      :activityStatusText="selectedConversationActivityText"
      :isTyping="isTyping"
      :totalUnread="totalUnread"
      :isSearchActive="isSearchActive"
      :searchQuery="searchQuery"
      :searchResults="searchResults"
      :currentSearchIndex="currentSearchIndex"
      :selectedMessagesCount="selectedMessages.length"
      :roomMemberCount="selectedRoomMemberCount"
      :isRoomMandatory="selectedRoomIsMandatory"
      :isRoomSystem="selectedRoomIsSystem"
      :canCreateGroup="canCreateGroup"
      :canCreateChannel="canCreateOptionalChannel"
      :canSendAdminBroadcast="canSendAdminBroadcast"
      @back="goBack"
      @view-profile="viewProfile"
      @toggle-search="toggleSearch"
      @search="(val: string) => { searchQuery = val; performSearch(); }"
      @result-click="handleSearchResultClick"
      @call="handleCall"
      @clear-selection="clearSelection"
      @manage-room="openSelectedRoomManager"
      @create-group="openGroupCreation"
      @create-channel="openChannelCreation"
      @admin-broadcast="openAdminBroadcastModal"
      :isDeleted="isSelectedUserDeleted"
    />

    <div
      v-if="selectedUserId && pinnedMessage"
      class="pinned-message-banner"
      role="button"
      tabindex="0"
      @click="handlePinnedBannerClick"
      @keydown.enter.prevent="handlePinnedBannerClick"
      @keydown.space.prevent="handlePinnedBannerClick"
    >
      <span class="pinned-message-accent" aria-hidden="true"></span>
      <div class="pinned-message-copy">
        <span class="pinned-message-label">پیام سنجاق‌شده</span>
        <span class="pinned-message-meta">{{ pinnedMessageMetaText }}</span>
        <span class="pinned-message-preview">{{ getPinnedMessagePreview(pinnedMessage) }}</span>
      </div>
      <button
        v-if="canManagePinnedMessages"
        class="pinned-message-dismiss"
        type="button"
        @click.stop="handlePinnedBannerUnpin"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    </div>

    <!-- Loading -->
    <div v-if="isLoading" class="loading-state">
      <MessengerLoadingScreen
        mode="list"
        title="در حال آماده‌سازی پیام‌رسان"
        subtitle="گفتگوها و وضعیت‌ها در حال همگام‌سازی هستند."
      />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="error-state">
      <p>{{ error }}</p>
      <button @click="error = ''; loadConversations()">تلاش مجدد</button>
    </div>

    <ConversationListContainer
      v-else-if="!selectedUserId"
      :isSearchActive="isSearchActive"
      :selectedUserId="selectedUserId"
      :searchResults="searchResults"
      :searchQuery="searchQuery"
      :conversations="sortedConversations"
      :currentUserId="currentUserId"
      :typingUsers="typingUsers"
      :activityTextByConversation="activityTextByConversation"
      :apiBaseUrl="apiBaseUrl"
      :canStartNewConversation="canStartNewConversation"
      @select-result="handleSearchResultClick"
      @select-conversation="selectConversation"
      @conversation-action="handleConversationAction"
      @new-conversation="openNewConversation"
    />

    <ChatRoomContainer
      v-else
      :state="chatRoomContainerState"
      :handlers="chatRoomContainerHandlers"
    />

    <!-- New Conversation Search Modal (outside v-if/v-else chain so it's always available) -->
    <ChatNewConversationModal
      v-if="showNewChatModal || keepInactiveMessengerSurfacesMounted"
      :show="showNewChatModal"
      :canStartDirectChat="canStartNewConversation"
      :canCreateGroup="canCreateGroup"
      @close="showNewChatModal = false"
      @start-chat="handleNewChatSearch"
      @create-group="openGroupCreation"
    />

    <ChatGroupManagerModal
      v-if="showGroupManagerModal || keepInactiveMessengerSurfacesMounted"
      :show="showGroupManagerModal"
      :groupId="groupManagerChatId"
      :currentUserId="props.currentUserId"
      @close="closeGroupManager"
      @created="handleGroupCreated"
      @updated="handleGroupUpdated"
      @left="handleGroupLeft"
      @open-public-profile="openPublicProfile"
    />

    <div v-if="showChannelManagerModal" class="channel-manager-overlay">
      <div class="channel-manager-sheet">
        <CreateChannelView
          :apiBaseUrl="props.apiBaseUrl"
          :jwtToken="props.jwtToken"
          :currentUserId="props.currentUserId"
          :showCloseButton="true"
          :initialChannelId="channelManagerChatId"
          @close="closeChannelManager"
          @refresh-conversations="handleChannelManagerConversationRefresh"
          @open-channel="handleChannelManagerOpenChannel"
          @left="handleChannelLeft"
          @open-public-profile="openPublicProfile"
        />
      </div>
    </div>
    <AdminBroadcastModal
      v-if="showAdminBroadcastModal"
      @close="closeAdminBroadcastModal"
      @sent="void loadConversations()"
    />
    </ChatShell>
</template>

<style scoped>
.chat-view {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  /* Telegram classic light background color */
  background-color: #e4eaef;
  z-index: 100;
}

.pinned-message-banner {
  position: absolute;
  top: 60px;
  left: 12px;
  right: 12px;
  z-index: 980;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.1);
  backdrop-filter: blur(18px);
  text-align: right;
}

.pinned-message-accent {
  width: 4px;
  align-self: stretch;
  border-radius: 999px;
  background: linear-gradient(180deg, #f59e0b, #f97316);
}

.pinned-message-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.pinned-message-label {
  color: #b45309;
  font-size: 0.72rem;
  font-weight: 900;
}

.pinned-message-meta {
  color: #0f172a;
  font-size: 0.78rem;
  font-weight: 800;
}

.pinned-message-preview {
  color: #475569;
  font-size: 0.82rem;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pinned-message-dismiss {
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  background: rgba(241, 245, 249, 0.92);
}

/* Header - Telegram Style Glass */
.chat-header {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 56px;
  z-index: 1000;
  display: flex;
  align-items: center;
  padding: 0 8px;
  background: #ffffff; /* Solid white Telegram header */
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  border-bottom: none;
  gap: 8px;
  direction: ltr; /* Force LTR layout as requested */
}

/* Header Buttons */
.header-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0; /* Minimal padding */
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: background 0.2s;
  color: #000000;
  width: 40px; /* Exact touch target size */
  height: 40px;
  flex-shrink: 0;
}

.header-btn svg {
  width: 24px;
  height: 24px;
}

.header-btn:hover {
  background: rgba(0, 0, 0, 0.05);
}

.header-btn:active {
  background: rgba(0, 0, 0, 0.1);
}

/* Header Avatar */
.header-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 600;
  flex-shrink: 0;
  margin: 0; /* Remove margins, rely on gap */
  cursor: pointer;
}

/* Header User Info */
.header-user-info {
  display: flex;
  flex-direction: column;
  justify-content: center;
  margin: 0;
  min-width: 0;
  flex: 1;
  align-items: flex-start; /* Align Left */
  padding-left: 4px; /* Padding on left for LTR */
  cursor: pointer;
}

.header-name {
  font-size: 16px;
  font-weight: 600;
  color: #000000;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
  text-align: left; /* Align Left */
}

.header-status {
  font-size: 13px;
  color: #8E8E93;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
  text-align: left; /* Align Left */
}

.header-status.online {
  color: #f59e0b; /* Telegram blue for online status */
}

/* Header Spacer */
.header-spacer {
  display: none; /* We use flex-grow on user-info instead, or keep it if needed for spacing logic */
}

/* Header Title (for conversation list) */
.header-title {
  font-size: 17px;
  font-weight: 600;
  color: #000000;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  flex: 1;
}

.badge {
  background: #ff3b30;
  color: white;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
}

.channel-manager-overlay {
  position: fixed;
  inset: 0;
  z-index: 2200;
  background: var(--messenger-overlay-medium, rgba(0, 0, 0, 0.34));
  backdrop-filter: blur(10px);
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: 0;
}

.channel-manager-sheet {
  width: min(100vw, 560px);
  height: 100vh;
  overflow: hidden;
  background: var(--messenger-manager-shell-bg, linear-gradient(180deg, #f7fafc 0%, #edf3f8 100%));
}

@media (min-width: 700px) {
  .channel-manager-overlay {
    padding: 24px;
    align-items: center;
  }

  .channel-manager-sheet {
    height: min(94vh, 920px);
    border-radius: var(--messenger-radius-sheet, 28px);
    box-shadow: var(--messenger-shadow-panel, 0 18px 50px rgba(15, 23, 42, 0.12));
  }
}

/* Loading & Empty States */
.loading-state, .error-state, 

.error-state button {
  margin-top: 12px;
  padding: 8px 16px;
  background: var(--primary-color);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

/* Conversations List */


.conversation-item:hover {
  background: #f4f4f5; /* Very light modern gray hover */
}

.conversation-item:active {
  background: #e4e4e7;
}

.conversation-item.has-unread {
  background: rgba(245, 158, 11, 0.05);
}










.typing-text {
  color: #2ea043;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 2px;
}

.typing-dots span {
  animation: typing-dot calc(var(--messenger-motion-standard, 180ms) * 8) infinite ease-in-out both;
  display: inline-block;
}

.typing-dots span:nth-child(1) { animation-delay: -0.32s; }
.typing-dots span:nth-child(2) { animation-delay: -0.16s; }

@keyframes typing-dot {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}


.date-separator {
  display: flex;
  justify-content: center;
  margin: 16px 0;
  z-index: 5;
}

.sticky-date {
   position: sticky;
   top: 10px;
}

.date-separator span {
  background-color: rgba(0, 0, 0, 0.15);
  color: #fff;
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  text-shadow: 0 1px 2px rgba(0,0,0,0.1);
  backdrop-filter: blur(4px);
  cursor: pointer;
  user-select: none;
}
@media (prefers-color-scheme: light) {
    .date-separator span {
        background-color: rgba(0, 0, 0, 0.2); 
        color: #fff;
        text-shadow: none;
        font-weight: 500;
        border: none;
    }
}


/* Chat Content - Main scrollable area */
.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  position: relative;
  /* No padding-top - messages will scroll UNDER the glass header */
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  overflow-anchor: none;
  /* Extra padding at top/bottom so messages start visible, but scroll under header/input */
  padding: 70px 16px 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.messages-container.has-pinned-message {
  padding-top: 126px;
}

.history-loading-indicator {
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  margin-bottom: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  color: #5f6d79;
  font-size: 12px;
  box-shadow: 0 8px 18px rgba(26, 41, 53, 0.08);
  position: sticky;
  top: 8px;
  z-index: 8;
}

.history-loading-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f59e0b, #3390ec);
  animation: history-loading-pulse calc(var(--messenger-motion-standard, 180ms) * 7) ease-in-out infinite;
}

@keyframes history-loading-pulse {
  0%,
  100% {
    transform: scale(0.8);
    opacity: 0.7;
  }
  50% {
    transform: scale(1);
    opacity: 1;
  }
}

.message-group {
  display: flex;
  flex-direction: column;
  width: 100%;
  gap: 6px;
}

.message-wrapper {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
}

.swipe-reply-icon {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: rgba(0,0,0,0.05); /* Gentle circle background like Telegram */
  color: #8e8e93; /* Telegram neutral gray */
  z-index: 1; /* Below the sliding message bubble */
  opacity: 0;
  transition: opacity var(--messenger-motion-standard, 180ms);
}

.swipe-reply-icon.visible {
  opacity: 1;
}

.reply-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

/* Sent message slides left, so icon is on the right */
.swipe-reply-icon.sent-side {
  right: 12px;
}

/* Received message slides right, so icon is on the left */
.swipe-reply-icon.received-side {
  left: 16px;
}

.message-bubble {
  max-width: 92%;
  padding: 8px 12px;
  border-radius: 12px;
  position: relative;
  font-size: 15px; /* Telegram font size */
  line-height: 1.5;
  white-space: pre-wrap; /* Preserve line breaks */
  word-wrap: break-word;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  /* Snappier Telegram animation curve */
  animation: slideIn var(--messenger-motion-overlay, 220ms) cubic-bezier(0.175, 0.885, 0.32, 1.275);
  /* Smooth transition for swipe/returning */
  transition: transform var(--messenger-motion-overlay, 220ms) cubic-bezier(0.25, 0.8, 0.25, 1);
  
  /* Prevent native text selection on long press */
  -webkit-touch-callout: none;
  -webkit-user-select: none;
  -khtml-user-select: none;
  -moz-user-select: none;
  -ms-user-select: none;
  user-select: none;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.95);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.message-bubble p {
  margin: 0;
}


.message-bubble.sent {
  align-self: flex-start;
  background: #eeffde; /* Telegram Sent Green */
  color: #000000;
  border-radius: 12px 12px 4px 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

.message-bubble.received {
  align-self: flex-end;
  background: #FFFFFF; /* White bubble */
  color: #000000;
  border-radius: 12px 12px 12px 4px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}



.msg-time {
  font-size: 11px;
  color: rgba(0, 0, 0, 0.4); /* Telegram time color */
}

/* Override time color for received messages */
.message-bubble.received .msg-time {
  color: #8E8E93;
}

.msg-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 4px;
}

.msg-status {
  display: flex;
  align-items: center;
}

.icon-read {
  fill: #43A047; /* Telegram green read checkmarks */
}

.icon-unread {
  fill: rgba(0, 0, 0, 0.3); /* Translucent unread */
}

/* Forward Styles */
.forwarded-banner {
  font-size: 13px;
  color: #43A047; /* Green highlight for forward header in sent */
  margin-bottom: 2px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.message-bubble.received .forwarded-banner {
  color: #8E8E93;
}

.msg-image-link {
  display: block;
  text-decoration: none;
}

.msg-image {
  max-width: 200px;
  max-height: 200px;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity var(--messenger-motion-standard, 180ms);
}

.msg-image:hover {
  opacity: 0.9;
}

.msg-image-placeholder {
  min-width: 120px;
  min-height: 90px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.08);
  border-radius: 8px;
  color: #888;
}

.msg-sticker {
  font-size: 48px;
}

/* Input Area - Solid Telegram Style */
.input-area {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  padding: 8px 12px 12px 12px;
  background: #ffffff;
  gap: 0;
  border-top: none;
  box-shadow: 0 -1px 2px rgba(0, 0, 0, 0.05);
  position: relative;
  z-index: 60; /* Above sticker picker */
}

.input-container {
  width: 100%;
  gap: 8px;
  flex: 1;
  display: flex;
  align-items: flex-end;
  background: #ffffff; /* Telegram input sits flat on white */
  border: none;
  box-shadow: none;
  border-radius: 20px;
  padding: 8px 4px;
  min-height: 44px;
  transition: background var(--messenger-motion-standard, 180ms);
}

.input-container:focus-within {
  background: #ffffff;
}

.input-container textarea {
  flex: 1;
  padding: 4px 8px;
  border: none;
  background: transparent;
  outline: none;
  font-size: 16px;
  color: #000000;
  resize: none;
  overflow-y: auto;
  min-height: 24px;
  line-height: 24px;
  max-height: 200px;
  font-family: inherit;
  direction: rtl;
  text-align: right;
}

.input-container textarea::placeholder {
  color: #8E8E93;
}

.emoji-btn, .attach-btn, .voice-btn {
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
}

.emoji-btn svg, .attach-btn svg, .voice-btn svg {
  width: 28px;
  height: 28px;
}

.emoji-btn {
  margin-left: 4px;
}

.attach-btn, .voice-btn {
  margin-right: 4px;
}

.send-btn-inline {
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  margin-right: 4px;
}

.send-btn-inline svg {
  width: 28px;
  height: 28px;
}

.send-btn-inline:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}



/* Sticker Picker */
.slide-up-enter-active,
.slide-up-leave-active {
  transition: transform var(--messenger-motion-overlay, 220ms) cubic-bezier(0.2, 0, 0, 1), opacity var(--messenger-motion-overlay, 220ms);
}

.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(100%);
  opacity: 0;
}

.sticker-picker {
  background: #f4f4f5; /* Telegram Light Ash */
  border-top: 1px solid rgba(0,0,0,0.05);
  padding: 16px 12px;
  max-height: 250px;
  overflow-y: auto;
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 50;
  transform: translateY(0);
}

.sticker-pack {
  margin-bottom: 12px;
}

.pack-name {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.stickers-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
}

.sticker-item {
  background: var(--bg-color);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 8px;
  font-size: 20px;
  cursor: pointer;
  transition: transform var(--messenger-motion-standard, 180ms);
}

.sticker-item:hover {
  transform: scale(1.1);
}

/* Scroll Bottom Button */
.scroll-bottom-btn {
  position: absolute;
  bottom: 80px;
  right: 20px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #FFFFFF;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #8E8E93;
  z-index: 999;
  transition: transform var(--messenger-motion-standard, 180ms), box-shadow var(--messenger-motion-standard, 180ms), background var(--messenger-motion-standard, 180ms);
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}

.scroll-bottom-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}

.scroll-badge {
  position: absolute;
  top: -5px;
  left: -5px;
  background: #ff3b30;
  color: white;
  border-radius: 10px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: bold;
  min-width: 18px;
  text-align: center;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

.scroll-mention-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  background: #7c3aed;
  color: white;
  border-radius: 50%;
  width: 18px;
  height: 18px;
  font-size: 11px;
  font-weight: bold;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
  animation: pulse-mention calc(var(--messenger-motion-standard, 180ms) * 11) infinite;
}

.scroll-bottom-btn.has-mention {
  border: 1.5px solid #7c3aed;
  color: #7c3aed;
}

@keyframes pulse-mention {
  0% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.05);
  }
  100% {
    transform: scale(1);
  }
}

/* Fix layout for absolute header */
.conversation-list-wrapper, .loading-state, .error-state {
  flex: 1;
  padding-top: 60px; /* Space for absolute header */
  width: 100%;
}

.loading-state, .error-state {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}

.compact-chat-loading {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  color: var(--messenger-text-secondary);
  font-size: 0.92rem;
}

.compact-spinner {
  width: 18px;
  height: 18px;
}

.chat-panel-error {
  margin: 18px auto;
  width: min(92%, 420px);
  padding: 14px 16px;
  border: 1px solid rgba(220, 38, 38, 0.18);
  border-radius: 14px;
  background: rgba(255, 247, 237, 0.96);
  color: #7f1d1d;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.chat-panel-error strong {
  display: block;
  font-size: 14px;
  margin-bottom: 4px;
}

.chat-panel-error p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}

.chat-panel-error button {
  flex: 0 0 auto;
  border: none;
  border-radius: 10px;
  background: #b91c1c;
  color: #fff;
  padding: 8px 12px;
  font-size: 13px;
  cursor: pointer;
}
/* Context Menu */
.context-menu {
  position: fixed;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  min-width: 150px;
  z-index: 2000;
  overflow: hidden;
  padding: 4px;
  border: 1px solid var(--border-color);
}

.telegram-menu-shadow {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1), 0 1px 4px rgba(0, 0, 0, 0.05); /* Softer nested shadow */
  transform-origin: top left; /* Menu scales from point of click */
}

/* Telegram Zoom-fade animation classes */
.zoom-fade-enter-active,
.zoom-fade-leave-active {
  transition: opacity var(--messenger-motion-fast, 120ms) cubic-bezier(0.2, 0, 0, 1), transform var(--messenger-motion-fast, 120ms) cubic-bezier(0.2, 0, 0, 1);
}

.zoom-fade-enter-from,
.zoom-fade-leave-to {
  opacity: 0;
  transform: scale(0.95);
}

.menu-item {
  padding: 10px 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  color: var(--text-color);
  transition: background var(--messenger-motion-fast, 120ms);
}

/* Base Ripple Setup */
.ripple-container {
  position: relative;
  overflow: hidden;
}

.ripple-effect {
  position: absolute;
  border-radius: 50%;
  background-color: rgba(0, 0, 0, 0.1); /* Default Telegram light-theme ripple */
  transform: scale(0);
  animation: ripple calc(var(--messenger-motion-overlay, 220ms) * 3) linear;
  pointer-events: none; /* Let clicks pass through */
}

@keyframes ripple {
  to {
    transform: scale(4);
    opacity: 0;
  }
}



.menu-item:hover {
  background: rgba(0,0,0,0.05);
}

.menu-item.delete {
  color: #ef4444;
}

.menu-item.delete:hover {
  background: rgba(239, 68, 68, 0.1);
}

.context-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1999; /* Below context menu */
}

.edited-label {
  font-size: 10px;
  font-style: italic;
  opacity: 0.7;
  margin-right: 4px;
}

/* Reply Styles */
.reply-context {
  border-right: 2px solid #3390ec;
  background: rgba(51, 144, 236, 0.08); /* light blue */
  border-radius: 4px;
  padding: 4px 8px;
  margin-bottom: 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  max-width: 100%;
}

.message-bubble.sent .reply-context {
  border-right: 2px solid #43A047;
  background: rgba(67, 160, 71, 0.1);
}

.reply-author {
  font-size: 13px;
  font-weight: 500;
  color: #3390ec;
}

.message-bubble.sent .reply-author {
  color: #2ea043; /* Telegram Sent Reply Author Green */
}

.reply-text {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.8;
  display: block;
  max-width: 100%;
}

/* Reply Banner (Input Area) */
.reply-banner {
  position: relative;
  display: flex;
  align-items: center;
  background: #FFFFFF;
  padding: 8px 16px 8px 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  animation: slideUp var(--messenger-motion-fast, 120ms) ease-out;
  min-height: 46px;
  gap: 12px;
}

@keyframes slideUp {
  from { transform: translateY(100%); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

.reply-banner-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #3390ec;
}

.reply-banner-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  border-right: 2px solid #3390ec;
  padding-right: 8px;
  justify-content: center;
  overflow: hidden;
}

.reply-banner-author {
  font-size: 14px;
  font-weight: 500;
  color: #3390ec;
  line-height: 1.2;
  margin-bottom: 2px;
}

.reply-banner-text {
  font-size: 13px;
  color: #8e8e93;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.close-reply {
  background: none;
  border: none;
  color: #8E8E93;
  cursor: pointer;
  padding: 4px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background var(--messenger-motion-standard, 180ms);
}

.close-reply:hover {
  background: rgba(0, 0, 0, 0.05);
  color: #000;
}

/* Highlight Animation */
.highlight-message {
  animation: highlight calc(var(--messenger-motion-overlay, 220ms) * 14) ease-in-out;
}

@keyframes highlight {
  0% { 
    box-shadow: none;
  }
  15% { 
    box-shadow: 0 0 0 4px rgba(255, 200, 0, 0.5), 0 0 20px 10px rgba(255, 200, 0, 0.3);
  }
  100% { 
    box-shadow: none;
  }
}
/* Search Styles */
.search-bar-container {
  flex: 1;
  display: flex;
  align-items: center;
  position: relative;
  height: 100%;
  margin-right: 8px;
}

.header-search-input {
  flex: 1;
  background: transparent;
  border: none;
  font-size: 16px;
  color: inherit;
  outline: none;
  padding: 0 8px;
  height: 100%;
}

.search-results-dropdown {
  position: absolute;
  top: 100%; /* Below header */
  left: 0;
  right: 0;
  
  /* Glassmorphism */
  background: rgba(255, 255, 255, 0.85); /* Translucent White */
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-top: none;
  max-height: 400px;
  overflow-y: auto;
  z-index: 1000;
  
  /* 3D Shadow */
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15), 0 2px 8px rgba(0,0,0,0.05);
  
  border-radius: 0 0 12px 12px;
}

.search-result-item {
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: background 0.2s;
}

.search-result-item:hover {
  background: #f5f5f5;
}

.search-result-item:last-child {
  border-bottom: none;
}

.search-res-text {
  font-size: 14px;
  color: #000;
}

.search-res-date {
  font-size: 11px;
  color: #8e8e93;
  align-self: flex-end;
}

@media (prefers-color-scheme: dark) {
  .search-results-dropdown {
    background: rgba(30, 30, 32, 0.85); /* Translucent Dark */
    border-color: rgba(255, 255, 255, 0.1);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }
  
  .search-result-item {
    border-bottom-color: rgba(255, 255, 255, 0.05);
  }
  
  .search-result-item:hover {
    background: #2c2c2e;
  }
  
  .search-res-text {
    color: #fff;
  }
}

/* Header Menu */
.header-menu-container {
  display: flex;
  align-items: center;
}

.header-dropdown-menu {
  position: absolute;
  top: 100%;
  right: 0; 
  left: auto;
  margin-top: 8px;
  
  /* Glassmorphism */
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  
  border-radius: 12px; /* Nicer rounded corners */
  
  /* 3D Shadow */
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15), 0 2px 8px rgba(0,0,0,0.05);
  
  min-width: 160px;
  z-index: 2000;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.3);
}

.header-menu-item {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font-size: 14px;
  color: inherit;
}

.header-menu-item:hover {
  background: #f5f5f5;
}

.menu-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 1500;
  background: transparent;
}

@media (prefers-color-scheme: dark) {
  .header-dropdown-menu {
    background: rgba(30, 30, 32, 0.85);
    border-color: rgba(255, 255, 255, 0.1);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }
  .header-menu-item:hover {
    background: #3a3a3c;
  }
}

.selected-message {
  position: relative;
  z-index: 10;
}

.selected-message::before {
  content: '';
  position: absolute;
  top: -4px; right: -16px; bottom: -4px; left: -16px;
  background-color: rgba(51, 144, 236, 0.15); /* Universal Telegram Select Overlay */
  pointer-events: none;
  z-index: -1; /* Behind the bubble but over the background */
  border-radius: 6px;
}

/* Forward Styles */
.forwarded-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  cursor: pointer;
}
.forward-icon {
  color: #3390ec;
}
.message-bubble.sent .forward-icon {
  color: #43A047;
}
.forward-content {
  display: flex;
  flex-direction: column;
}
.forward-title {
  font-size: 13px;
  font-weight: 500;
  color: #3390ec;
  line-height: 1.2;
}
.message-bubble.sent .forward-title {
  color: #43A047;
}
.forward-text {
  font-size: 13px;
  color: inherit;
  opacity: 0.8;
  line-height: 1.2;
}

.selection-bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-around;
  width: 100%;
  padding: 8px 0;
  background: white;
  min-height: 56px;
}

.selection-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: #8e8e93;
  font-size: 11px;
  font-weight: 500;
  gap: 4px;
  padding: 6px 16px;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity var(--messenger-motion-standard, 180ms), background var(--messenger-motion-standard, 180ms);
}

.selection-action-btn:hover {
  background: rgba(0,0,0,0.05);
  color: #000;
}

.selection-action-btn.delete {
  color: #ef4444;
}

.selection-action-btn.delete:hover {
  background: rgba(239, 68, 68, 0.1);
}

.selection-action-btn svg {
  margin-bottom: 2px;
}

.selection-action-btn.primary {
  color: #3390ec;
}

.selection-action-btn.primary:hover {
  background: rgba(51, 144, 236, 0.1);
  color: #1d6fc2;
}

.album-download-selection-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 10px 14px;
  background: rgba(255, 255, 255, 0.98);
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  min-height: 60px;
}

.album-download-selection-summary {
  flex: 1;
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}

/* Telegram-Style Media UI */
.msg-media-link {
  border-radius: 8px;
  overflow: hidden;
  display: block;
  min-width: 150px;
  min-height: 150px;
}
.msg-media-content {
  width: 100%;
  height: auto;
  max-height: 300px;
  object-fit: cover;
  display: block;
}
.msg-video-wrapper {
  position: relative;
}
.video-play-indicator {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: rgba(0,0,0,0.5);
  border-radius: 50%;
  padding: 12px;
  pointer-events: none;
}
.msg-media-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.4);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
}
.progress-container {
  position: relative;
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.progress-ring {
  position: absolute;
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}
.ring-bg {
  fill: none;
  stroke: rgba(255,255,255,0.2);
  stroke-width: 3;
}
.ring-fg {
  fill: none;
  stroke: white;
  stroke-width: 3;
  stroke-linecap: round;
  transition: stroke-dasharray var(--messenger-motion-overlay, 220ms) ease;
}
.progress-text {
  color: white;
  font-size: 11px;
  font-weight: bold;
}
.download-btn {
  background: rgba(0,0,0,0.5);
  border: none;
  border-radius: 50%;
  color: white;
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background var(--messenger-motion-standard, 180ms);
}
.download-btn:hover {
  background: rgba(0,0,0,0.7);
}
.media-type-badge {
  position: absolute;
  bottom: 8px;
  left: 8px;
  background: rgba(0,0,0,0.6);
  color: white;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  gap: 4px;
}


</style>
