import type { Conversation, Message, MessageReaction } from '../../types/chat'
import { resolveMessengerRealtimeConversationKey } from '../../utils/chatRealtimeMediaPolicy'
import { resolveRoomConversationKey } from '../../utils/chatRoomRouting'

export type ChatGatewayEventName =
  | 'chat:message'
  | 'chat:read'
  | 'chat:reaction'
  | 'chat:typing'
  | 'chat:activity'
  | 'conversation:update'

export interface ChatGatewayStores {
  messages?: {
    appendOrReplaceMessage?: (roomKey: number, message: Message) => void
    patchReadState?: (roomKey: number, readerId?: number | null) => void
    patchReaction?: (messageId: number, reactions: MessageReaction[]) => void
  }
  conversations?: {
    patchConversation?: (conversationKey: number, patch: Partial<Conversation>) => void
  }
  session?: {
    setUserTyping?: (roomKey: number, userId: number, active: boolean, name?: string | null) => void
    setUserActivity?: (roomKey: number, userId: number, activity: string, active: boolean, name?: string | null) => void
  }
}

export interface ChatGatewayResult {
  handled: boolean
  eventName: ChatGatewayEventName
  roomKey: number | null
  reason?: string
}

type EventClock = {
  version: number | null
  timestamp: number | null
}

export interface ChatEventGatewayOptions {
  maxMessageClockEntries?: number
  maxConversationClockEntries?: number
  maxReactionClockEntries?: number
}

const DEFAULT_MAX_MESSAGE_CLOCK_ENTRIES = 2000
const DEFAULT_MAX_CONVERSATION_CLOCK_ENTRIES = 500
const DEFAULT_MAX_REACTION_CLOCK_ENTRIES = 2000

function normalizeReactionList(reactions: unknown): MessageReaction[] {
  return Array.isArray(reactions)
    ? reactions
      .map((reaction: any) => ({
        emoji: typeof reaction?.emoji === 'string' ? reaction.emoji : '',
        user_id: Number(reaction?.user_id),
      }))
      .filter((reaction) => reaction.emoji && Number.isFinite(reaction.user_id))
    : []
}

function getRoomKeyFromPayload(payload: any): number | null {
  const directOrRoomKey = resolveMessengerRealtimeConversationKey(payload)
  if (directOrRoomKey !== null) return directOrRoomKey
  return resolveRoomConversationKey(payload?.room_kind, payload?.chat_id)
}

function parseEventClock(payload: any): EventClock {
  const rawVersion = Number(payload?.version ?? payload?.event_version ?? payload?.revision)
  const rawDate = payload?.updated_at ?? payload?.created_at ?? payload?.timestamp
  const timestamp = typeof rawDate === 'string' || typeof rawDate === 'number'
    ? new Date(rawDate).getTime()
    : Number.NaN

  return {
    version: Number.isFinite(rawVersion) ? rawVersion : null,
    timestamp: Number.isFinite(timestamp) ? timestamp : null,
  }
}

function compareEventClock(left: EventClock, right: EventClock) {
  if (left.version !== null && right.version !== null && left.version !== right.version) {
    return left.version - right.version
  }
  if (left.timestamp !== null && right.timestamp !== null && left.timestamp !== right.timestamp) {
    return left.timestamp - right.timestamp
  }
  return 0
}

function hasComparableClock(clock: EventClock) {
  return clock.version !== null || clock.timestamp !== null
}

function rememberClock(
  map: Map<number, EventClock>,
  key: number,
  clock: EventClock,
  maxEntries: number,
) {
  if (maxEntries <= 0) return
  if (map.has(key)) {
    map.delete(key)
  }
  map.set(key, clock)
  while (map.size > maxEntries) {
    const oldestKey = map.keys().next().value
    if (oldestKey === undefined) break
    map.delete(oldestKey)
  }
}

export class ChatEventGateway {
  private latestMessageClockById = new Map<number, EventClock>()
  private latestConversationClockByRoom = new Map<number, EventClock>()
  private latestReactionClockByMessage = new Map<number, EventClock>()
  private maxMessageClockEntries: number
  private maxConversationClockEntries: number
  private maxReactionClockEntries: number

  constructor(
    private stores: ChatGatewayStores,
    options: ChatEventGatewayOptions = {},
  ) {
    this.maxMessageClockEntries = options.maxMessageClockEntries ?? DEFAULT_MAX_MESSAGE_CLOCK_ENTRIES
    this.maxConversationClockEntries = options.maxConversationClockEntries ?? DEFAULT_MAX_CONVERSATION_CLOCK_ENTRIES
    this.maxReactionClockEntries = options.maxReactionClockEntries ?? DEFAULT_MAX_REACTION_CLOCK_ENTRIES
  }

  dispatch(eventName: ChatGatewayEventName, payload: unknown): ChatGatewayResult {
    const data = payload as any
    const roomKey = getRoomKeyFromPayload(data)

    if (eventName !== 'conversation:update' && roomKey === null) {
      return { handled: false, eventName, roomKey, reason: 'missing-room-key' }
    }

    if (eventName === 'chat:message') {
      if (!data || typeof data.id !== 'number' || roomKey === null) {
        return { handled: false, eventName, roomKey, reason: 'invalid-message' }
      }
      const messageClock = parseEventClock(data)
      const latestMessageClock = this.latestMessageClockById.get(data.id)
      if (
        latestMessageClock
        && hasComparableClock(messageClock)
        && compareEventClock(messageClock, latestMessageClock) < 0
      ) {
        return { handled: false, eventName, roomKey, reason: 'stale-message' }
      }
      if (hasComparableClock(messageClock)) {
        rememberClock(this.latestMessageClockById, data.id, messageClock, this.maxMessageClockEntries)
      }

      this.stores.messages?.appendOrReplaceMessage?.(roomKey, data as Message)
      const latestConversationClock = this.latestConversationClockByRoom.get(roomKey)
      const canPatchConversation = !latestConversationClock
        || !hasComparableClock(messageClock)
        || compareEventClock(messageClock, latestConversationClock) >= 0
      if (canPatchConversation) {
        if (hasComparableClock(messageClock)) {
          rememberClock(this.latestConversationClockByRoom, roomKey, messageClock, this.maxConversationClockEntries)
        }
        this.stores.conversations?.patchConversation?.(roomKey, {
          last_message_at: data.created_at,
          last_message_type: data.message_type,
          last_message_content: typeof data.content === 'string' ? data.content : null,
        })
      }
      return { handled: true, eventName, roomKey }
    }

    if (eventName === 'chat:read') {
      if (resolveRoomConversationKey(data?.room_kind, data?.chat_id) !== null) {
        return { handled: true, eventName, roomKey }
      }
      this.stores.messages?.patchReadState?.(roomKey as number, Number(data?.reader_id))
      this.stores.conversations?.patchConversation?.(roomKey as number, { unread_count: 0 })
      return { handled: true, eventName, roomKey }
    }

    if (eventName === 'chat:reaction') {
      if (typeof data?.id !== 'number') {
        return { handled: false, eventName, roomKey, reason: 'missing-message-id' }
      }
      const reactionClock = parseEventClock(data)
      const latestReactionClock = this.latestReactionClockByMessage.get(data.id)
      if (
        latestReactionClock
        && hasComparableClock(reactionClock)
        && compareEventClock(reactionClock, latestReactionClock) < 0
      ) {
        return { handled: false, eventName, roomKey, reason: 'stale-reaction' }
      }
      if (hasComparableClock(reactionClock)) {
        rememberClock(this.latestReactionClockByMessage, data.id, reactionClock, this.maxReactionClockEntries)
      }
      this.stores.messages?.patchReaction?.(data.id, normalizeReactionList(data.reactions))
      return { handled: true, eventName, roomKey }
    }

    if (eventName === 'chat:typing' || eventName === 'chat:activity') {
      const senderId = Number(data?.sender_id ?? data?.user_id)
      if (!Number.isFinite(senderId) || roomKey === null) {
        return { handled: false, eventName, roomKey, reason: 'missing-sender' }
      }
      const active = data?.active !== false
      const senderName = typeof data?.sender_name === 'string' ? data.sender_name : null
      if (eventName === 'chat:typing' || data?.activity === 'typing') {
        this.stores.session?.setUserTyping?.(roomKey, senderId, active, senderName)
      } else {
        this.stores.session?.setUserActivity?.(roomKey, senderId, String(data?.activity || 'activity'), active, senderName)
      }
      return { handled: true, eventName, roomKey }
    }

    const conversationKey = Number(data?.other_user_id ?? data?.conversation_key)
    if (!Number.isFinite(conversationKey)) {
      return { handled: false, eventName, roomKey: null, reason: 'missing-conversation-key' }
    }
    this.stores.conversations?.patchConversation?.(conversationKey, data as Partial<Conversation>)
    return { handled: true, eventName, roomKey: conversationKey }
  }
}

export function createChatEventGateway(stores: ChatGatewayStores, options?: ChatEventGatewayOptions) {
  return new ChatEventGateway(stores, options)
}
