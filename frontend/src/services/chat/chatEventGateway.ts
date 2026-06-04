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

export class ChatEventGateway {
  constructor(private stores: ChatGatewayStores) {}

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
      this.stores.messages?.appendOrReplaceMessage?.(roomKey, data as Message)
      this.stores.conversations?.patchConversation?.(roomKey, {
        last_message_at: data.created_at,
        last_message_type: data.message_type,
        last_message_content: typeof data.content === 'string' ? data.content : null,
      })
      return { handled: true, eventName, roomKey }
    }

    if (eventName === 'chat:read') {
      this.stores.messages?.patchReadState?.(roomKey as number, Number(data?.reader_id))
      this.stores.conversations?.patchConversation?.(roomKey as number, { unread_count: 0 })
      return { handled: true, eventName, roomKey }
    }

    if (eventName === 'chat:reaction') {
      if (typeof data?.id !== 'number') {
        return { handled: false, eventName, roomKey, reason: 'missing-message-id' }
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

export function createChatEventGateway(stores: ChatGatewayStores) {
  return new ChatEventGateway(stores)
}

