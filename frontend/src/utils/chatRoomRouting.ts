import type { Message } from '../types/chat'

export function isChannelConversationKey(conversationKey: number) {
  return conversationKey < 0
}

export function resolveChannelChatId(conversationKey: number) {
  return Math.abs(conversationKey)
}

export function buildChatMessagesEndpoint(conversationKey: number, query: string) {
  if (isChannelConversationKey(conversationKey)) {
    return `/chat/rooms/${resolveChannelChatId(conversationKey)}/messages?${query}`
  }
  return `/chat/messages/${conversationKey}?${query}`
}

export function buildChatReadEndpoint(conversationKey: number) {
  if (isChannelConversationKey(conversationKey)) {
    return `/chat/rooms/${resolveChannelChatId(conversationKey)}/read`
  }
  return `/chat/read/${conversationKey}`
}

export function buildChatSendEndpoint(conversationKey: number) {
  if (isChannelConversationKey(conversationKey)) {
    return `/chat/rooms/${resolveChannelChatId(conversationKey)}/send`
  }
  return '/chat/send'
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

  if (!isChannelConversationKey(conversationKey)) {
    body.receiver_id = conversationKey
  }

  if (typeof payload.reply_to_message_id === 'number') {
    body.reply_to_message_id = payload.reply_to_message_id
  }

  return body
}