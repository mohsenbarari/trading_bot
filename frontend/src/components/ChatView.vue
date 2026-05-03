<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted, nextTick } from 'vue'
import MessengerLoadingScreen from './chat/MessengerLoadingScreen.vue'
import ChatAlbumLayout from './chat/ChatAlbumLayout.vue'
import ChatHeader from './chat/ChatHeader.vue'
import ChatInputBar from './chat/ChatInputBar.vue'
import ChatMessageItem from './chat/ChatMessageItem.vue'
import ChatContextMenu from './chat/ChatContextMenu.vue'
import ChatSearchGlobalList from './chat/ChatSearchGlobalList.vue'
import ChatEmptyState from './chat/ChatEmptyState.vue'
import ChatConversationList from './chat/ChatConversationList.vue'
import ChatNewConversationModal from './chat/ChatNewConversationModal.vue'
import AttachmentMenu from './chat/AttachmentMenu.vue'
import { vAutoAnimate } from '@formkit/auto-animate/vue'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'

import type { ChatForwardTarget, Conversation, Message, MessageReaction } from '../types/chat'
import { useChatMedia } from '../composables/chat/useChatMedia'
import { useChatWebSocket } from '../composables/chat/useChatWebSocket'
import { useChatMessages } from '../composables/chat/useChatMessages'
import { useChatScroll } from '../composables/chat/useChatScroll'
import {
  seedFileCache,
  shareMultipleFiles,
  shareFile as cachedShareFileGlobal,
} from '../composables/chat/useChatFileHandler'
import { MESSAGE_REACTION_CATALOG, recordRecentMessageReaction } from '../utils/messageReactions'

// Props
const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  currentUserId: number
  targetUserId?: number
  targetUserName?: string
}>()

// Emits
const emit = defineEmits<{
  (e: 'navigate', view: string, payload?: any): void
  (e: 'back'): void
}>()

// State
const isLoading = ref(true)
const error = ref('')

// Conversations & Messages
const conversations = ref<Conversation[]>([])
const selectedUserId = ref<number | null>(null)
const selectedUserName = ref('')
const messages = ref<Message[]>([])

// Selection State
const selectedMessages = ref<number[]>([])
const selectionModePurpose = ref<'default' | 'album-download' | 'album-forward' | 'album-share'>('default')
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
const contextMenuBackStateActive = ref(false)
let closingContextMenuFromBack = false

// Search State
const isSearchActive = ref(false)
const isHeaderMenuOpen = ref(false)
const searchQuery = ref('')
const searchResults = ref<any[]>([])
const isSearching = ref(false)
const currentSearchIndex = ref(0)
const showInChatSearchList = ref(false)
const searchDebounceTimeout = ref<any>(null)

// UI State
const isLoadingMessages = ref(false)
const messagesContainer = ref<HTMLElement | null>(null)
const isUserAtBottom = ref(true)
const unreadNewMessagesCount = ref(0)
const showScrollButton = ref(false)
const isMobile = ref(false)
const contextMenu = ref<{ visible: boolean; x: number; y: number; message: Message | null; messageIds: number[] }>({ visible: false, x: 0, y: 0, message: null, messageIds: [] })
const AVAILABLE_MESSAGE_REACTIONS = [...MESSAGE_REACTION_CATALOG] as const
const pendingReactionMutationVersion = new Map<number, number>()
let reactionMutationVersion = 0

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

// Status
const targetUserStatus = ref('آخرین بازدید اخیراً')

function updateIsMobile() {
  isMobile.value = window.innerWidth < 768
}

const {
  isViewingReply: scrollIsViewingReply,
  scrollToBottom,
  forceScrollToBottom,
  handleScroll,
  scrollToUnreadOrBottom,
  scrollToMessage
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

const messagesLogic = useChatMessages({
  apiBaseUrl: props.apiBaseUrl,
  jwtToken: props.jwtToken,
  currentUserId: props.currentUserId,
  selectedUserId,
  messages,
  conversations,
  error,
  isLoadingMessages,
  isSending,
  unreadNewMessagesCount,
  isUserAtBottom,
  isViewingReply,
  targetUserStatus,
  messageInput,
  editingMessage,
  replyingToMessage,
  swipedMessageId,
  isMobile,
  showStickerPicker,
  scrollToBottom,
  scrollToUnreadOrBottom,
  forceScrollToBottom,
  focusMessageInput: (options?: { cursorToEnd?: boolean }) => {
    chatInputBarRef.value?.focusInput(options)
  },
  adjustTextareaHeight: () => {
    chatInputBarRef.value?.adjustTextareaHeight()
  }
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

const mediaLogic = useChatMedia({
  apiBaseUrl: props.apiBaseUrl,
  jwtToken: props.jwtToken,
  currentUserId: props.currentUserId,
  selectedUserId,
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

  const previousHeight = container.scrollHeight
  const previousTop = container.scrollTop
  const loadedCount = await loadOlderMessages(userId)

  if (loadedCount <= 0) {
    return
  }

  await nextTick()
  const restoredSelectionAnchor = restorePendingSelectionAnchor(container, userId)
  if (!restoredSelectionAnchor) {
    const newHeight = container.scrollHeight
    container.scrollTop = previousTop + (newHeight - previousHeight)
  }
  syncMessagesContainerMetrics(container)
}

const isSelectedUserDeleted = computed(() => {
  const conv = conversations.value.find(c => c.other_user_id === selectedUserId.value)
  return conv ? !!conv.other_user_is_deleted : false
})

const sortedConversations = computed(() => {
  return [...conversations.value].sort((a, b) => {
    if (!a.last_message_at) return 1
    if (!b.last_message_at) return -1
    // Lexicographic compare on ISO-8601 strings matches chronological
    // order for same-timezone suffix strings (the backend emits consistent
    // `YYYY-MM-DDTHH:MM:SS[.ffffff]` values). This avoids constructing
    // two `Date` objects in the comparator on every WS-triggered re-sort,
    // which is O(n log n) object churn on busy chats.
    if (b.last_message_at > a.last_message_at) return 1
    if (b.last_message_at < a.last_message_at) return -1
    return 0
  })
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

function normalizeMessageIds(messageIds: number[]) {
  const seen = new Set<number>()
  const normalized: number[] = []

  messageIds.forEach((messageId) => {
    if (!Number.isFinite(messageId) || seen.has(messageId)) return
    seen.add(messageId)
    normalized.push(messageId)
  })

  return normalized
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
  const msg = contextMenu.value.message
  if (!msg) return false
  if (contextMenu.value.messageIds.length !== 1) return false
  if (msg.sender_id !== props.currentUserId) return false
  if (msg.message_type !== 'text') return false
  if ((msg as any).forwarded_from_id || (msg as any).forwarded_from_name) return false
  const msgTime = new Date(msg.created_at).getTime()
  return (Date.now() - msgTime) <= 48 * 60 * 60 * 1000
})

const canDelete = computed(() => {
  const messageIds = normalizeMessageIds(contextMenu.value.messageIds)
  if (messageIds.length === 0) return false

  return messageIds.every((messageId) => {
    const msg = messages.value.find(message => message.id === messageId)
    return isDeletableMessage(msg)
  })
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

function getAlbumMeta(msg: Message): { albumId: string | null, albumIndex: number } {
  if (msg.message_type !== 'image' && msg.message_type !== 'video') {
    return { albumId: null, albumIndex: Number.MAX_SAFE_INTEGER }
  }

  try {
    const content = JSON.parse(msg.content)
    const albumId = typeof content.album_id === 'string' && content.album_id.trim()
      ? content.album_id.trim()
      : null
    const albumIndex = typeof content.album_index === 'number' && Number.isFinite(content.album_index)
      ? content.album_index
      : Number.MAX_SAFE_INTEGER

    return { albumId, albumIndex }
  } catch {
    return { albumId: null, albumIndex: Number.MAX_SAFE_INTEGER }
  }
}

function getAlbumMessagesForMessage(msg: Message) {
  const albumMeta = getAlbumMeta(msg)
  if (!albumMeta.albumId) {
    return [msg]
  }

  const albumMessages = messages.value
    .filter(candidate => {
      if (candidate.message_type !== 'image' && candidate.message_type !== 'video') return false
      if (candidate.reply_to_message || candidate.is_error) return false
      if (candidate.sender_id !== msg.sender_id) return false
      return getAlbumMeta(candidate).albumId === albumMeta.albumId
    })
    .sort((left, right) => {
      const leftMeta = getAlbumMeta(left)
      const rightMeta = getAlbumMeta(right)
      const byIndex = leftMeta.albumIndex - rightMeta.albumIndex
      if (byIndex !== 0) return byIndex

      const byCreatedAt = new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
      if (byCreatedAt !== 0) return byCreatedAt

      return left.id - right.id
    })

  return albumMessages.length > 0 ? albumMessages : [msg]
}

function getContextMenuMessageIds(msg: Message) {
  const albumMessages = getAlbumMessagesForMessage(msg)
  return normalizeMessageIds(albumMessages.length > 1 ? albumMessages.map(message => message.id) : [msg.id])
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

  selectedMessages.value = normalized
  showForwardModal.value = true
}

function sortMessageIdsByChatOrder(messageIds: number[]) {
  const normalized = normalizeMessageIds(messageIds)
  const positionById = new Map<number, number>()

  messages.value.forEach((message, index) => {
    positionById.set(message.id, index)
  })

  return [...normalized].sort((left, right) => {
    return (positionById.get(left) ?? Number.MAX_SAFE_INTEGER) - (positionById.get(right) ?? Number.MAX_SAFE_INTEGER)
  })
}

function toggleSelectionBatch(messageIds: number[]) {
  const normalized = sortMessageIdsByChatOrder(messageIds)
  if (normalized.length === 0) return

  const allSelected = normalized.every(messageId => selectedMessages.value.includes(messageId))
  if (allSelected) {
    selectedMessages.value = selectedMessages.value.filter(messageId => !normalized.includes(messageId))
    if (selectedMessages.value.length === 0) {
      resetSelectionContext()
    }
    return
  }

  selectedMessages.value = sortMessageIdsByChatOrder([
    ...selectedMessages.value,
    ...normalized,
  ])
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
      const forwardedFromRaw = (message as any).forwarded_from_id
      const forwardedFromId = typeof forwardedFromRaw === 'number' ? forwardedFromRaw : message.sender_id

      return {
        message,
        content: buildForwardContent(message, albumAssignment?.albumId ?? null, albumAssignment?.albumIndex),
        forwardedFromId,
      }
    })
    .filter((item): item is { message: Message, content: string, forwardedFromId: number } => Boolean(item))
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

// Stable album wrapper cache: returns the same { type: 'album', ... } object
// across re-runs of `groupedMessages` when the album's composition did not
// actually change. This avoids re-rendering every album `<ChatMessageItem>`
// on each new incoming WebSocket message.
const albumWrapperCache = new Map<string, { signature: string, wrapper: any }>()

// Per-ISO-string cache for `formatDateForSeparator`. `toLocaleDateString('fa-IR', ...)`
// is expensive on weak devices, and `groupedMessages` re-runs on every
// reactive tick. Same `created_at` string always resolves to the same
// Persian date label, so memoize by that string.
const dateSeparatorLabelCache = new Map<string, string>()

// Stable group wrapper cache: preserve the exact group object reference
// when the group's date label and items list signature haven't changed.
// This lets a `v-memo` on `.message-group` skip patching untouched
// older day-groups when only the latest group grows with a new message.
const groupWrapperCache = new Map<string, { signature: string, group: { label: string, items: any[] } }>()

const groupedMessages = computed(() => {
  const groups: { label: string, items: any[] }[] = []
  if (messages.value.length === 0) return groups;
  
  const firstMsg = messages.value[0]
  if (!firstMsg) return groups
  
  let currentLabel = formatDateForSeparator(firstMsg.created_at)
  let currentGroup: any[] = [firstMsg]
  
  for (let i = 1; i < messages.value.length; i++) {
      const msg = messages.value[i]
      if (!msg) continue;
      const label = formatDateForSeparator(msg.created_at)
      if (label !== currentLabel) {
          groups.push({ label: currentLabel, items: currentGroup })
          currentLabel = label
          currentGroup = [msg]
      } else {
          currentGroup.push(msg)
      }
  }
  groups.push({ label: currentLabel, items: currentGroup })  // Group only messages that were explicitly sent in the same album batch.
  groups.forEach(group => {
    // Single-pass bucketing: compute album meta for every message and
    // bucket eligible media messages by (senderId + albumId) so we avoid
    // a nested filter for each album-seed message (previously O(n*k)).
    const albumMetaByMessageId = new Map<number, { albumId: string | null, albumIndex: number }>()
    const albumBuckets = new Map<string, any[]>()

    group.items.forEach(msg => {
      const meta = getAlbumMeta(msg)
      albumMetaByMessageId.set(msg.id, meta)

      if (!meta.albumId) return
      const isMedia = msg.message_type === 'image' || msg.message_type === 'video'
      if (!isMedia || msg.reply_to_message || msg.is_error) return

      const bucketKey = `${msg.sender_id}::${meta.albumId}`
      const bucket = albumBuckets.get(bucketKey)
      if (bucket) {
        bucket.push(msg)
      } else {
        albumBuckets.set(bucketKey, [msg])
      }
    })

    // Sort each bucket by album_index / created_at / id.
    albumBuckets.forEach((bucket) => {
      bucket.sort((left, right) => {
        const leftMeta = albumMetaByMessageId.get(left.id)
        const rightMeta = albumMetaByMessageId.get(right.id)
        const byIndex = (leftMeta?.albumIndex ?? Number.MAX_SAFE_INTEGER) - (rightMeta?.albumIndex ?? Number.MAX_SAFE_INTEGER)
        if (byIndex !== 0) return byIndex
        const byCreatedAt = new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
        if (byCreatedAt !== 0) return byCreatedAt
        return left.id - right.id
      })
    })

    const collapsedItems: any[] = []
    const consumedAlbumKeys = new Set<string>()

    group.items.forEach(msg => {
      const isMedia = msg.message_type === 'image' || msg.message_type === 'video'
      if (!isMedia || msg.reply_to_message || msg.is_error) {
        collapsedItems.push(msg)
        return
      }

      const meta = albumMetaByMessageId.get(msg.id)
      if (!meta?.albumId) {
        collapsedItems.push(msg)
        return
      }

      const bucketKey = `${msg.sender_id}::${meta.albumId}`
      if (consumedAlbumKeys.has(bucketKey)) return
      consumedAlbumKeys.add(bucketKey)

      const bucket = albumBuckets.get(bucketKey) ?? [msg]
      if (bucket.length > 1) {
        const signature = bucket.map(m => `${m.id}:${(m as any).is_deleted ? 1 : 0}:${(m as any).content?.length ?? 0}:${JSON.stringify((m as any).reactions ?? [])}`).join('|')
        const cacheKey = `${msg.sender_id}::${meta.albumId}`
        const cached = albumWrapperCache.get(cacheKey)
        if (cached && cached.signature === signature) {
          collapsedItems.push(cached.wrapper)
        } else {
          const wrapper = { type: 'album', id: `album_${meta.albumId}`, sender_id: msg.sender_id, messages: bucket }
          albumWrapperCache.set(cacheKey, { signature, wrapper })
          collapsedItems.push(wrapper)
        }
      } else {
        collapsedItems.push(msg)
      }
    })

    group.items = collapsedItems
  })

  // Stable group wrappers: preserve the exact group wrapper reference when
  // the ordered list of item *references* hasn't changed. This lets a
  // `v-memo` at the `.message-group` level skip patching older day-groups
  // entirely when only the latest group grows with a new message.
  //
  // NOTE: we compare by reference identity (not by id) because upstream
  // paths like `messages.value[idx] = serverMsg` replace the message
  // object while keeping the id stable — if the cached group kept the
  // stale reference, the child `<ChatMessageItem v-memo="[item, ...]">`
  // would never invalidate on edits/send-complete.
  const stableGroups: { label: string, items: any[] }[] = []
  const seenLabels = new Set<string>()
  for (const group of groups) {
    const cacheKey = group.label
    const cached = groupWrapperCache.get(cacheKey)
    let reuse = false
    if (cached && cached.group.items.length === group.items.length) {
      reuse = true
      const prevItems = cached.group.items
      const nextItems = group.items
      for (let i = 0; i < nextItems.length; i++) {
        if (prevItems[i] !== nextItems[i]) { reuse = false; break }
      }
    }
    if (reuse && cached) {
      stableGroups.push(cached.group)
    } else {
      const stable = { label: group.label, items: group.items }
      groupWrapperCache.set(cacheKey, { signature: '', group: stable })
      stableGroups.push(stable)
    }
    seenLabels.add(cacheKey)
  }
  if (groupWrapperCache.size > seenLabels.size) {
    for (const key of groupWrapperCache.keys()) {
      if (!seenLabels.has(key)) groupWrapperCache.delete(key)
    }
  }

  return stableGroups
})

function formatTime(dateStr: string) {
  const date = new Date(dateStr)
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
}

function formatDateForSeparator(dateStr: string): string {
    if (!dateStr) return ''

    const date = new Date(dateStr);
    const now = new Date();
    if (date.toDateString() === now.toDateString()) return 'امروز';
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) return 'دیروز';

    // `toLocaleDateString('fa-IR', ...)` is expensive on weak devices and
    // `groupedMessages` re-runs on every reactive tick. For non-today/
    // non-yesterday messages the resulting Persian date string is stable
    // given the same ISO input, so cache by ISO string.
    const cached = dateSeparatorLabelCache.get(dateStr)
    if (cached !== undefined) return cached

    const label = date.toLocaleDateString('fa-IR', { year: 'numeric', month: 'long', day: 'numeric' });
    dateSeparatorLabelCache.set(dateStr, label)
    return label
}

function isUserOnline(lastSeen: string | null | undefined): boolean {
  if (!lastSeen) return false
  const date = new Date(lastSeen)
  return (new Date().getTime() - date.getTime()) < 180000
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
    isSearchActive.value = !isSearchActive.value
    if (isSearchActive.value) {
        searchQuery.value = ''
        searchResults.value = []
        currentSearchIndex.value = 0
        showInChatSearchList.value = false
        nextTick(() => {
            const input = document.getElementById('search-input')
            if (input) input.focus()
        })
    } else {
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
        isSearchActive.value = false
        showInChatSearchList.value = false
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
  selectedUserId.value = conv.other_user_id
  selectedUserName.value = conv.other_user_name
  loadMessages(conv.other_user_id)
  contextMenu.value.visible = false;
  editingMessage.value = null;
  messageInput.value = '';
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
  })
}

const startNewChat = (userId: number, userName: string) => {
  selectedUserId.value = userId
  selectedUserName.value = userName
  loadMessages(userId)
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
  })
}

const showNewChatModal = ref(false)

const handleNewChatSearch = (userId: number, userName: string) => {
    showNewChatModal.value = false
    startNewChat(userId, userName)
}

const showContextMenu = (event: Event, msg: Message) => {
  if (isAlbumActionSelectionMode.value) return

  const messageIds = getContextMenuMessageIds(msg)

  let clientX = 0, clientY = 0;
  if (event instanceof MouseEvent) {
    event.preventDefault();
    clientX = event.clientX;
    clientY = event.clientY;
  } else if (event instanceof TouchEvent && event.touches.length > 0) {
    const touch = event.touches[0];
    if (touch) {
      clientX = touch.clientX;
      clientY = touch.clientY;
    }
  }

  const menuWidth = 220;
  const menuHeight = messageIds.length > 1 ? 320 : 300;
  const padding = 10;

  if (clientX + menuWidth > window.innerWidth) clientX = window.innerWidth - menuWidth - padding;
  if (clientX < padding) clientX = padding;
  if (clientY + menuHeight > window.innerHeight - padding) {
    clientY = clientY - menuHeight - padding;
    if (clientY < padding) clientY = padding;
  }

  contextMenu.value = {
    visible: true,
    x: clientX,
    y: clientY,
    message: msg,
    messageIds,
  }
};

const closeContextMenu = () => {
  contextMenu.value = { visible: false, x: 0, y: 0, message: null, messageIds: [] }
};

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
  replyingToMessage.value = null;
  if (isMobile.value) {
    swipedMessageId.value = null;
  }
  editingMessage.value = msg;
  messageInput.value = msg.content;
  closeContextMenu();
  nextTick(() => {
    chatInputBarRef.value?.adjustTextareaHeight();
    chatInputBarRef.value?.focusInput({ cursorToEnd: true });
  });
};

const handleDeleteMessage = async () => {
  const messageIds = normalizeMessageIds(contextMenu.value.messageIds)
  if (messageIds.length === 0) return

  const deleted = await deleteMessagesByIds(
    messageIds,
    messageIds.length > 1 ? 'آیا از حذف این آلبوم اطمینان دارید؟' : 'آیا از حذف این پیام اطمینان دارید؟'
  )

  if (deleted) {
    closeContextMenu()
  }
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
  // Try local blob URL (imageCache) → fetch as blob without network roundtrip.
  const localUrl = imageCache.value[fileId] || msg.local_blob_url
  if (localUrl) {
    try {
      const resp = await fetch(localUrl)
      const blob = await resp.blob()
      await seedFileCache(fileId, blob, inferMediaFileName(msg, fileId), blob.type || inferMediaMime(msg))
      return fileId
    } catch (err) {
      console.warn('[chat-share] seed from local blob failed', err)
    }
  }
  // Fall back to backend fetch.
  const downloadUrl = buildMediaDownloadUrl(fileId)
  try {
    const resp = await fetch(downloadUrl)
    if (!resp.ok) return null
    const blob = await resp.blob()
    await seedFileCache(fileId, blob, inferMediaFileName(msg, fileId), blob.type || inferMediaMime(msg))
    return fileId
  } catch (err) {
    console.warn('[chat-share] api fetch failed', err)
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
  if (selectedMessages.value.length > 0) showForwardModal.value = true
}

async function handleSendVoice(blob: Blob, durationMs: number) {
  if (!selectedUserId.value || !blob) return
  const file = new File([blob], `voice_${Date.now()}.webm`, { type: blob.type || 'audio/webm' })
  // Pack durationMs into the file object so handleMediaUploadWrapper can extract it
  ;(file as any).durationMs = durationMs
  await handleMediaUploadWrapper(file)
}

async function handleSendLocation(lat: number, lng: number) {
  if (!selectedUserId.value) return
  const normalized = normalizeLocationPayload({ lat, lng })
  if (!normalized) return

  const content = JSON.stringify(normalized)
  try {
    const newMsg = await messagesLogic.apiFetch('/chat/send', {
      method: 'POST',
      body: JSON.stringify({
        receiver_id: selectedUserId.value,
        content,
        message_type: 'location'
      })
    })
    messages.value.push(newMsg)
    scrollToBottom()
  } catch (e: any) {
    error.value = e.message
  }
}

function closeForwardModal() {
  showForwardModal.value = false
}

async function forwardSelectedMessages(targets: ChatForwardTarget | ChatForwardTarget[]) {
  const targetList = Array.isArray(targets) ? targets : [targets]
  const userTargets = targetList.filter(t => t.kind === 'user')
  const hasNonUser = targetList.length !== userTargets.length

  if (userTargets.length === 0) {
    if (hasNonUser) alert('هدایت پیام به گروه به زودی اضافه می‌شود')
    return
  }

  const preparedBatch = prepareForwardBatch(selectedMessages.value)
  if (preparedBatch.length === 0) return

  // Close modal and clear selection immediately so the UI unblocks.
  // Sending happens in parallel in the background.
  selectedMessages.value = []
  showForwardModal.value = false

  // Build flat (target, item) send tasks so we can parallelize across
  // both targets and items, not sequentially per-target then per-item.
  type ForwardTask = {
    target: ChatForwardTarget
    item: (typeof preparedBatch)[number]
  }
  const tasks: ForwardTask[] = []
  for (const target of userTargets) {
    for (const item of preparedBatch) {
      tasks.push({ target, item })
    }
  }

  const failuresByTarget = new Map<number, number>()
  const titleByTarget = new Map<number, string>()
  const totalByTarget = new Map<number, number>()
  userTargets.forEach(t => {
    titleByTarget.set(t.id, t.title)
    totalByTarget.set(t.id, preparedBatch.length)
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
        await messagesLogic.apiFetch('/chat/send', {
          method: 'POST',
          body: JSON.stringify({
            receiver_id: task.target.id,
            content: task.item.content,
            message_type: task.item.message.message_type,
            forwarded_from_id: task.item.forwardedFromId,
          })
        })
      } catch (forwardError) {
        console.error('Failed to forward message:', task.item.message.id, 'to', task.target.id, forwardError)
        failuresByTarget.set(task.target.id, (failuresByTarget.get(task.target.id) ?? 0) + 1)
      }
    }
  }

  try {
    const workers = Array.from({ length: concurrency }, () => runWorker())
    await Promise.all(workers)

    const fullyFailedTargets: string[] = []
    let anySuccess = false
    userTargets.forEach(t => {
      const failed = failuresByTarget.get(t.id) ?? 0
      const total = totalByTarget.get(t.id) ?? 0
      if (failed >= total) {
        fullyFailedTargets.push(titleByTarget.get(t.id) ?? String(t.id))
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
    } else if (hasNonUser) {
      alert('هدایت پیام به گروه به زودی اضافه می‌شود')
    }

    // Fire conversation refresh in background; don't block UI.
    void loadConversations()

    // If only one target, open that chat (previous UX). For multi-target, stay on current chat.
    if (userTargets.length === 1) {
      const only = userTargets[0]!
      const targetUserId = only.id
      const targetConversation = conversations.value.find(c => c.other_user_id === targetUserId)
      const targetName = targetConversation?.other_user_name || only.title

      if (selectedUserId.value !== targetUserId) {
        selectedUserId.value = targetUserId
        selectedUserName.value = targetName
        void loadMessages(targetUserId)
      } else {
        selectedUserName.value = targetName
        void loadMessages(targetUserId, true)
      }
    }
  } finally {
    isSending.value = false
  }
}

const handleReplyMessage = () => {
  const msg = contextMenu.value.message
  if (!msg) return
  handleReply(msg)
  closeContextMenu()
}

const handleForwardMessage = () => {
  const msg = contextMenu.value.message
  const messageIds = normalizeMessageIds(contextMenu.value.messageIds)

  if (msg && messageIds.length > 1) {
    startAlbumForwardSelection(msg, messageIds)
    closeContextMenu()
    return
  }

  openForwardModalForIds(contextMenu.value.messageIds)
  closeContextMenu()
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
  selectedMessages.value = orderedIds
  showForwardModal.value = true
}

function handleAlbumReplyItem(msg: Message) {
  handleReply(msg)
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

  handleReply(msg)
  closeLightbox()
}

function handleLightboxForward(msgId: number) {
  openForwardModalForIds([msgId])
  closeLightbox()
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

function goBack() {
  if (contextMenu.value.visible) {
    closeContextMenu()
    return
  }

  if (selectedUserId.value) {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    popBackState()
  } else {
    emit('back')
  }
}

function viewProfile() {
  if (selectedUserId.value) {
    emit('navigate', 'public_profile', { 
        id: selectedUserId.value, 
        account_name: selectedUserName.value 
    })
  }
}

const handleCall = () => alert('قابلیت تماس به زودی اضافه می‌شود')

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
  pendingSelectionAnchor = null
  // Clear the album wrapper cache when switching chats so cached wrappers
  // from other conversations do not leak into memory over time.
  albumWrapperCache.clear()
  groupWrapperCache.clear()
  dateSeparatorLabelCache.clear()
  if (newVal) {
    startStatusPolling(newVal)
    nextTick(() => {
      syncMessagesContainerMetrics()
    })
  } else {
    stopStatusPolling()
    previousMessagesContainerMetrics = null
  }
})

watch(messagesContainer, (container) => {
  attachMessagesContainerResizeObserver(container)
})

onMounted(async () => {
  isLoading.value = true
  await loadConversations()
  isLoading.value = false
  
  if (props.targetUserId && props.targetUserName) {
    selectedUserId.value = props.targetUserId
    selectedUserName.value = props.targetUserName
    loadMessages(props.targetUserId)
    pushBackState(() => {
      selectedUserId.value = null
      selectedUserName.value = ''
      messages.value = []
    })
  }
  
  startPolling()
  updateIsMobile()
  window.addEventListener('resize', updateIsMobile)
  nextTick(() => {
    attachMessagesContainerResizeObserver(messagesContainer.value)
  })
})

watch(() => props.targetUserId, (newId) => {
  if (newId && props.targetUserName) {
    selectedUserId.value = newId
    selectedUserName.value = props.targetUserName
    loadMessages(newId)
    pushBackState(() => {
      selectedUserId.value = null
      selectedUserName.value = ''
      messages.value = []
    })
  }
})

watch(isSelectionMode, (isEnabled) => {
  if (isEnabled) {
    showStickerPicker.value = false
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

  if (selectionBackStateActive.value) {
    selectionBackStateActive.value = false
    if (!clearingSelectionFromBack) {
      popBackState()
    }
  }
})

watch(() => contextMenu.value.visible, (isVisible) => {
  if (isVisible) {
    if (!contextMenuBackStateActive.value) {
      contextMenuBackStateActive.value = true
      pushBackState(() => {
        closingContextMenuFromBack = true
        closeContextMenu()
        closingContextMenuFromBack = false
      })
    }
    return
  }

  if (contextMenuBackStateActive.value) {
    contextMenuBackStateActive.value = false
    if (!closingContextMenuFromBack) {
      popBackState()
    }
  }
})

watch(showAttachmentMenu, (isOpen) => {
  if (isOpen) {
    showStickerPicker.value = false
  }
})

function handleToggleAttachment() {
  showStickerPicker.value = false
  showAttachmentMenu.value = !showAttachmentMenu.value
}

onUnmounted(() => {
  messagesContainerResizeObserver?.disconnect()
  messagesContainerResizeObserver = null
  window.removeEventListener('resize', updateIsMobile)
  stopPolling()
  stopStatusPolling()
  clearBackStack()
})

// Types/Typescript requires this to be exposed properly
defineExpose({ startNewChat })


import ChatForwardModal from './chat/ChatForwardModal.vue'
import ChatLightbox from './chat/ChatLightbox.vue'
import ChatLocationModal from './chat/ChatLocationModal.vue'
import ChatSearchBottomBar from './chat/ChatSearchBottomBar.vue'
</script>


<template>
  <div class="chat-view">
    <!-- Header - Telegram Style -->
    <ChatHeader
      :isSelectionMode="isSelectionMode"
      :selectedUserId="selectedUserId"
      :selectedUserName="selectedUserName"
      :targetUserStatus="targetUserStatus"
      :isTyping="isTyping"
      :totalUnread="totalUnread"
      :isSearchActive="isSearchActive"
      :searchQuery="searchQuery"
      :searchResults="searchResults"
      :currentSearchIndex="currentSearchIndex"
      :selectedMessagesCount="selectedMessages.length"
      @back="goBack"
      @view-profile="viewProfile"
      @toggle-search="toggleSearch"
      @search="(val: string) => { searchQuery = val; performSearch(); }"
      @result-click="handleSearchResultClick"
      @call="handleCall"
      @clear-selection="clearSelection"
      :isDeleted="isSelectedUserDeleted"
    />

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

    <!-- Global Search Results -->
    <ChatSearchGlobalList
      v-else-if="isSearchActive && !selectedUserId"
      :searchResults="searchResults"
      :searchQuery="searchQuery"
      :conversations="sortedConversations"
      :currentUserId="currentUserId"
      @select-result="handleSearchResultClick"
    />

    <!-- Conversation List -->
    <ChatConversationList
      v-else-if="!selectedUserId && !isSearchActive"
      :conversations="sortedConversations"
      :selectedUserId="selectedUserId"
      :typingUsers="typingUsers"
      @select-conversation="selectConversation"
      @new-conversation="showNewChatModal = true"
    />

    <!-- Messages View -->
    <template v-else>
      <ChatEmptyState v-if="!selectedUserId" />
      
      <!-- In-Chat Search List -->
      <ChatSearchGlobalList
        v-if="isSearchActive && selectedUserId && showInChatSearchList"
        :searchResults="searchResults"
        :searchQuery="searchQuery"
        :conversations="sortedConversations"
        :currentUserId="currentUserId"
        @select-result="handleSearchResultClick"
      />
      
      <div v-else class="chat-content">
        <div v-if="isLoadingMessages" class="loading-state">
          <MessengerLoadingScreen
            mode="chat"
            title="در حال باز کردن گفتگو"
            subtitle="آخرین پیام‌ها با یک بارگذاری سبک و سریع آماده می‌شوند."
          />
        </div>
        
        <div v-else class="messages-container" ref="messagesContainer" @scroll.passive="handleMessagesScroll">
          <div v-if="isLoadingOlderMessages" class="history-loading-indicator">
            <span class="history-loading-dot"></span>
            <span>در حال بارگذاری پیام‌های قبلی...</span>
          </div>

          <div v-if="messages.length === 0" class="empty-state">
            <span>💬</span>
            <p>شروع گفتگو...</p>
          </div>
          
          <div v-for="group in groupedMessages" :key="group.label" class="message-group" v-auto-animate v-memo="[group, searchQuery, isSelectionMode, activeAlbumSelectionId, selectionMemoKey]">
            <div class="date-separator sticky-date">
              <span @click="scrollToMessage(group.items[0].id)">{{ group.label }}</span>
            </div>

            <template v-for="(item, index) in group.items" :key="item.id">
              <ChatMessageItem
                v-memo="[item, searchQuery, isSelectionMode, isAlbumInDownloadSelection(item), selectionMemoKey]"
                :msg="item.type === 'album' ? item.messages[0] : item"
                :isAlbum="item.type === 'album'"
                :albumItems="item.type === 'album' ? item.messages : []"
                :isAlbumDownloadMode="isAlbumInDownloadSelection(item)"
                :selectedAlbumDownloadMessageIds="selectedMessages"
                :currentUserId="props.currentUserId"
                :selectedUserName="selectedUserName"
                :selectedMessages="selectedMessages"
                :imageCache="imageCache"
                :isSelectionMode="isSelectionMode"
                :searchQuery="searchQuery"
                @swipe-reply="handleReply"
                @select="handleGroupedItemSelection(item)"
                @click-message="handleMessageClick"
                @context-menu="showContextMenu"
                @scroll-to="scrollToMessage"
                @media-click="handleMediaInteraction"
                @location-click="handleLocationClick"
                @download="downloadMedia"
                @cancel-send="handleCancelSend"
                @cancel-download="handleCancelDownload"
                @reply-album-item="handleAlbumReplyItem"
                @forward-album-item="handleAlbumForwardItem"
                @delete-album-item="handleAlbumDeleteItem"
                @toggle-album-download-item="handleAlbumDownloadItemToggle"
                @toggle-reaction="handleMessageReactionToggle"
                :on-load="() => hydrateRenderedMedia(item)"
              />
            </template>
          </div> <!-- End v-for="groupedMessages" message-group -->
        
        <!-- Scroll to Bottom Button -->
        <button 
          v-if="showScrollButton" 
          class="scroll-bottom-btn" 
          @click="scrollToBottom"
        >
          <span v-if="unreadNewMessagesCount > 0" class="scroll-badge">{{ unreadNewMessagesCount }}</span>
          <svg viewBox="0 0 24 24" fill="currentColor" width="24" height="24">
            <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/>
          </svg>
        </button>
      </div> <!-- End .messages-container -->
      </div> <!-- End .chat-content -->

      <!-- In-Chat Search Navigation Bottom Bar -->
      <ChatSearchBottomBar
        v-if="selectedUserId && isSearchActive && searchResults.length > 0"
        :currentSearchIndex="currentSearchIndex"
        :totalResults="searchResults.length"
        :showInChatSearchList="showInChatSearchList"
        @next="nextSearchResult"
        @prev="prevSearchResult"
        @toggle-list="handleToggleInChatList"
      />

      <div v-else-if="selectedUserId && isAlbumDownloadSelectionMode" class="album-download-selection-bar">
        <button class="selection-action-btn" @click="clearSelection">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
          <span>انصراف</span>
        </button>
        <div class="album-download-selection-summary">
          {{ selectedMessages.length }} مدیا برای دانلود انتخاب شده
        </div>
        <button class="selection-action-btn primary" @click="handleDownloadSelectedAlbumMessages">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          <span>دانلود</span>
        </button>
      </div>

      <div v-else-if="selectedUserId && isAlbumForwardSelectionMode" class="album-download-selection-bar">
        <button class="selection-action-btn" @click="clearSelection">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
          <span>انصراف</span>
        </button>
        <div class="album-download-selection-summary">
          {{ selectedMessages.length }} مدیا برای هدایت انتخاب شده
        </div>
        <button class="selection-action-btn primary" :disabled="selectedMessages.length === 0" @click="handleForwardSelectedAlbumMessages">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 14 20 9 15 4"></polyline>
            <path d="M4 20v-7a4 4 0 0 1 4-4h12"></path>
          </svg>
          <span>هدایت</span>
        </button>
      </div>

      <div v-else-if="selectedUserId && isAlbumShareSelectionMode" class="album-download-selection-bar">
        <button class="selection-action-btn" @click="clearSelection">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
          <span>انصراف</span>
        </button>
        <div class="album-download-selection-summary">
          {{ selectedMessages.length }} مدیا برای اشتراک‌گذاری انتخاب شده
        </div>
        <button class="selection-action-btn primary" :disabled="selectedMessages.length === 0" @click="handleShareSelectedAlbumMessages">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="18" cy="5" r="3"></circle>
            <circle cx="6" cy="12" r="3"></circle>
            <circle cx="18" cy="19" r="3"></circle>
            <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
            <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
          </svg>
          <span>اشتراک‌گذاری</span>
        </button>
      </div>

      <!-- Input Area -->
      <ChatInputBar
        ref="chatInputBarRef"
        v-else-if="selectedUserId"
        v-model="messageInput"
        v-model:stickerPickerOpen="showStickerPicker"
        :editingMessage="editingMessage"
        :isSelectionMode="isSelectionMode"
        :replyingToMessage="replyingToMessage"
        :selectedUserName="selectedUserName"
        :currentUserId="props.currentUserId"
        :selectedMessagesCount="selectedMessages.length"
        :canDeleteSelected="canDeleteSelected"
        :canCopySelected="canCopySelected"
        :isSending="isSending"
        :isDeleted="isSelectedUserDeleted"
        :selectedMessages="selectedMessages"
        :isUploading="isUploading"
        @cancel-edit="cancelEdit"
        @cancel-reply="cancelReply"
        @delete-selected="handleDeleteSelected"
        @reply-selected="handleReplySelected"
        @copy-selected="handleCopySelected"
        @forward-selected="openForwardModal"
        @toggle-attachment="handleToggleAttachment"
        @send-text="(text: string) => { messageInput = text; sendMessage(); }"
        @send-voice="handleSendVoice"
        @typing="handleTypingWrapper"
      />

      <!-- Attachment Bottom Sheet -->
      <AttachmentMenu
        v-model="showAttachmentMenu"
        @select-media="handleMediaUploadWrapper"
        @select-file="(file) => handleMediaUploadWrapper(file, null, 0, 1, { sendAsDocument: true })"
        @select-location="handleSendLocation"
      />

      <!-- Forward Target Modal -->
      <ChatForwardModal
        :showForwardModal="showForwardModal"
        :sortedConversations="sortedConversations"
        @close="closeForwardModal"
        @forward-to="forwardSelectedMessages"
      />

    <!-- Context Menu -->
    <ChatContextMenu
      :menuState="contextMenu"
      :isAlbumSelection="contextMenu.messageIds.length > 1"
      :currentUserId="props.currentUserId"
      :canEdit="canEdit"
      :canDelete="canDelete"
      :availableReactions="[...AVAILABLE_MESSAGE_REACTIONS]"
      @react="handleContextMenuReaction"
      @reply="handleReplyMessage"
      @forward="handleForwardMessage"
      @copy="handleCopyMessage"
      @edit="handleEditMessage"
      @delete="handleDeleteMessage"
      @close="closeContextMenu"
      @save-media="handleSaveMedia"
      @save-album="handleSaveAlbum"
      @share="handleShareMessage"
      @share-album="handleShareAlbum"
    />

    <!-- Lightbox Overlay -->
    <ChatLightbox 
      :lightboxMedia="lightboxMedia" 
      :currentUserId="props.currentUserId"
      @close="closeLightbox" 
      @navigate="handleLightboxNavigate"
      @reply="handleLightboxReply"
      @forward="handleLightboxForward"
      @share="handleLightboxShare"
      @delete="handleLightboxDelete"
    />

    <!-- Location Modal Overlay -->
    <ChatLocationModal
      :location="selectedLocation"
      @close="closeLocationModal"
    />

    </template>

    <!-- New Conversation Search Modal (outside v-if/v-else chain so it's always available) -->
    <ChatNewConversationModal
      :show="showNewChatModal"
      @close="showNewChatModal = false"
      @start-chat="handleNewChatSearch"
    />
    </div>
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
  animation: typing-dot 1.4s infinite ease-in-out both;
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
  animation: history-loading-pulse 1.2s ease-in-out infinite;
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
  transition: opacity 0.2s;
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
  animation: slideIn 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  /* Smooth transition for swipe/returning */
  transition: transform 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  
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
  transition: opacity 0.2s;
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
  transition: background 0.2s;
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
  transition: transform 0.3s cubic-bezier(0.2, 0, 0, 1), opacity 0.3s;
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
  transition: transform 0.2s;
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
  transition: all 0.2s;
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
  transition: opacity 0.15s cubic-bezier(0.2, 0, 0, 1), transform 0.15s cubic-bezier(0.2, 0, 0, 1);
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
  transition: background 0.1s; /* Faster hover transition */
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
  animation: ripple 0.6s linear;
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
  animation: slideUp 0.15s ease-out;
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
  transition: background 0.2s;
}

.close-reply:hover {
  background: rgba(0, 0, 0, 0.05);
  color: #000;
}

/* Highlight Animation */
.highlight-message {
  animation: highlight 3s ease-in-out;
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
  transition: opacity 0.2s, background 0.2s;
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
  transition: stroke-dasharray 0.3s ease;
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
  transition: background 0.2s;
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
