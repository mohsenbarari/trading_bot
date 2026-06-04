import type { ChatAlbumTimelineItem, ChatTimelineGroup, ChatTimelineItem, Conversation } from '../types/chat'

export const MESSENGER_CONVERSATION_INITIAL_WINDOW = 64
export const MESSENGER_CONVERSATION_WINDOW_BATCH = 48
export const MESSENGER_CONVERSATION_WINDOW_THRESHOLD_PX = 640

export interface MessengerConversationWindowOptions {
  limit: number
  selectedUserId?: number | null
}

export interface MessengerConversationWindow {
  items: Conversation[]
  hasMore: boolean
  hiddenCount: number
}

export interface MessengerTimelineRenderBudget {
  groupCount: number
  itemCount: number
  albumWrapperCount: number
  mediaItemCount: number
  virtualizationCandidate: boolean
}

export function isMandatoryPinnedConversation(conv: Conversation) {
  return conv.room_kind === 'channel' && conv.is_mandatory === true
}

export function isMessengerConversationPinned(conv: Conversation) {
  return isMandatoryPinnedConversation(conv) || conv.is_pinned === true
}

export function compareMessengerConversationActivity(a: Pick<Conversation, 'last_message_at'>, b: Pick<Conversation, 'last_message_at'>) {
  if (!a.last_message_at) return 1
  if (!b.last_message_at) return -1
  if (b.last_message_at > a.last_message_at) return 1
  if (b.last_message_at < a.last_message_at) return -1
  return 0
}

export function sortMessengerConversations(conversations: Conversation[]) {
  return [...conversations].sort((a, b) => {
    const mandatoryPinDelta = Number(isMandatoryPinnedConversation(b)) - Number(isMandatoryPinnedConversation(a))
    if (mandatoryPinDelta !== 0) return mandatoryPinDelta

    const pinDelta = Number(isMessengerConversationPinned(b)) - Number(isMessengerConversationPinned(a))
    if (pinDelta !== 0) return pinDelta

    if (isMessengerConversationPinned(a) && isMessengerConversationPinned(b) && !isMandatoryPinnedConversation(a) && !isMandatoryPinnedConversation(b)) {
      const aPinOrder = Number(a.pin_order ?? 0)
      const bPinOrder = Number(b.pin_order ?? 0)
      if (bPinOrder !== aPinOrder) return bPinOrder - aPinOrder

      const aPinnedAt = a.pinned_at || ''
      const bPinnedAt = b.pinned_at || ''
      if (bPinnedAt > aPinnedAt) return 1
      if (bPinnedAt < aPinnedAt) return -1
    }

    return compareMessengerConversationActivity(a, b)
  })
}

export function getNextPinnedConversationOrder(conversations: Conversation[]) {
  return conversations.reduce((maxOrder, conversation) => {
    if (isMandatoryPinnedConversation(conversation) || !isMessengerConversationPinned(conversation)) {
      return maxOrder
    }

    const currentOrder = Number(conversation.pin_order ?? 0)
    return Number.isFinite(currentOrder) ? Math.max(maxOrder, currentOrder) : maxOrder
  }, 0) + 1
}

export function selectConversationWindow(
  conversations: Conversation[],
  options: MessengerConversationWindowOptions,
): MessengerConversationWindow {
  const limit = Math.max(0, Math.floor(options.limit))
  if (limit === 0) {
    return {
      items: [],
      hasMore: conversations.length > 0,
      hiddenCount: conversations.length,
    }
  }

  if (conversations.length <= limit) {
    return {
      items: conversations,
      hasMore: false,
      hiddenCount: 0,
    }
  }

  const items = conversations.slice(0, limit)
  const selectedUserId = options.selectedUserId
  if (selectedUserId != null && !items.some(conversation => conversation.other_user_id === selectedUserId)) {
    const selectedConversation = conversations.find(conversation => conversation.other_user_id === selectedUserId)
    if (selectedConversation) {
      items.push(selectedConversation)
    }
  }

  return {
    items,
    hasMore: items.length < conversations.length,
    hiddenCount: Math.max(0, conversations.length - items.length),
  }
}

export function shouldExpandConversationWindow(
  metrics: { scrollTop: number; clientHeight: number; scrollHeight: number },
  thresholdPx = MESSENGER_CONVERSATION_WINDOW_THRESHOLD_PX,
) {
  const distanceFromBottom = metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight
  return distanceFromBottom <= thresholdPx
}

function isAlbumTimelineItem(item: ChatTimelineItem): item is ChatAlbumTimelineItem {
  return 'type' in item && item.type === 'album'
}

export function summarizeTimelineRenderBudget(groups: ChatTimelineGroup[]): MessengerTimelineRenderBudget {
  let itemCount = 0
  let albumWrapperCount = 0
  let mediaItemCount = 0

  groups.forEach((group) => {
    group.items.forEach((item) => {
      itemCount += 1
      if (isAlbumTimelineItem(item)) {
        albumWrapperCount += 1
        mediaItemCount += item.messages.length
        return
      }

      if (item.message_type === 'image' || item.message_type === 'video' || item.message_type === 'document' || item.message_type === 'voice') {
        mediaItemCount += 1
      }
    })
  })

  return {
    groupCount: groups.length,
    itemCount,
    albumWrapperCount,
    mediaItemCount,
    virtualizationCandidate: itemCount >= 96 || mediaItemCount >= 48,
  }
}
