import type { Message } from '../types/chat'

export type ChatActivityKind = 'typing' | 'uploading_file'

export function isRoomConversationKey(conversationKey: number) {
  return conversationKey < 0
}

export function isChannelConversationKey(conversationKey: number) {
  return isRoomConversationKey(conversationKey)
}

export function resolveRoomChatId(conversationKey: number) {
  return Math.abs(conversationKey)
}

export function resolveChannelChatId(conversationKey: number) {
  return resolveRoomChatId(conversationKey)
}

export function isNamedRoomKind(roomKind: unknown): roomKind is 'channel' | 'group' {
  return roomKind === 'channel' || roomKind === 'group'
}

export function resolveRoomConversationKey(roomKind: unknown, chatId: unknown): number | null {
  if (!isNamedRoomKind(roomKind)) {
    return null
  }

  const numericChatId = Number(chatId)
  return Number.isFinite(numericChatId) && numericChatId > 0
    ? -numericChatId
    : null
}

export function buildChatMessagesEndpoint(conversationKey: number, query: string) {
  if (isRoomConversationKey(conversationKey)) {
    return `/chat/rooms/${resolveRoomChatId(conversationKey)}/messages?${query}`
  }
  return `/chat/messages/${conversationKey}?${query}`
}

export function buildChatReadEndpoint(conversationKey: number) {
  if (isRoomConversationKey(conversationKey)) {
    return `/chat/rooms/${resolveRoomChatId(conversationKey)}/read`
  }
  return `/chat/read/${conversationKey}`
}

export function buildChatSendEndpoint(conversationKey: number) {
  if (isRoomConversationKey(conversationKey)) {
    return `/chat/rooms/${resolveRoomChatId(conversationKey)}/send`
  }
  return '/chat/send'
}

export function buildChatActivityEndpoint(conversationKey: number) {
  if (isRoomConversationKey(conversationKey)) {
    return `/chat/rooms/${resolveRoomChatId(conversationKey)}/activity`
  }
  return '/chat/activity'
}

export function buildChatSendBody(
  conversationKey: number,
  payload: {
    content: string
    message_type: Message['message_type']
    reply_to_message_id?: number
  }
) {
  const body: Record<string, unknown> = {
    content: payload.content,
    message_type: payload.message_type,
  }

  if (!isRoomConversationKey(conversationKey)) {
    body.receiver_id = conversationKey
  }

  if (typeof payload.reply_to_message_id === 'number') {
    body.reply_to_message_id = payload.reply_to_message_id
  }

  return body
}

export function buildChatActivityBody(
  conversationKey: number,
  payload: {
    activity: ChatActivityKind
    active?: boolean
  }
) {
  const body: Record<string, unknown> = {
    activity: payload.activity,
    active: payload.active ?? true,
  }

  if (!isRoomConversationKey(conversationKey)) {
    body.receiver_id = conversationKey
  }

  return body
}