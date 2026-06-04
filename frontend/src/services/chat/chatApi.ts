import type { Conversation, Message } from '../../types/chat'
import { apiFetchJson } from '../../utils/auth'
import {
  buildChatMessagesEndpoint,
  buildChatReadEndpoint,
  buildChatSendBody,
  buildChatSendEndpoint,
} from '../../utils/chatRoomRouting'
import type { ErrorPolicyContext } from '../../utils/httpErrorPolicy'

export type ChatApiFetcher = <T = unknown>(
  endpoint: string,
  options?: RequestInit,
  errorContext?: ErrorPolicyContext,
) => Promise<T>

const defaultFetcher: ChatApiFetcher = async <T = unknown>(endpoint: string, options?: RequestInit, errorContext?: ErrorPolicyContext) =>
  await apiFetchJson(`/api${endpoint}`, options, errorContext) as T

export interface SendTextMessagePayload {
  conversationKey: number
  content: string
  messageType: Message['message_type']
  replyToMessageId?: number
}

export async function fetchChatConversations(fetcher: ChatApiFetcher = defaultFetcher) {
  return await fetcher<Conversation[]>('/chat/conversations', {}, {
    surface: 'messenger',
    scope: 'list',
    operation: 'load-list',
    preserveExistingData: true,
    resourceLabel: 'لیست گفتگوها',
    fallbackMessage: 'دریافت گفتگوها ممکن نشد.',
  })
}

export async function fetchChatMessages(
  conversationKey: number,
  query: string,
  fetcher: ChatApiFetcher = defaultFetcher,
) {
  return await fetcher<Message[]>(buildChatMessagesEndpoint(conversationKey, query), {}, {
    surface: 'messenger',
    scope: 'panel',
    operation: 'load-detail',
    preserveExistingData: true,
    resourceLabel: 'گفتگو',
    fallbackMessage: 'دریافت پیام‌های این گفتگو ممکن نشد.',
  })
}

export async function markChatConversationRead(
  conversationKey: number,
  fetcher: ChatApiFetcher = defaultFetcher,
) {
  return await fetcher(buildChatReadEndpoint(conversationKey), {
    method: 'POST',
  })
}

export async function sendChatTextMessage(
  payload: SendTextMessagePayload,
  fetcher: ChatApiFetcher = defaultFetcher,
) {
  return await fetcher<Message>(buildChatSendEndpoint(payload.conversationKey), {
    method: 'POST',
    body: JSON.stringify(buildChatSendBody(payload.conversationKey, {
      content: payload.content,
      message_type: payload.messageType,
      reply_to_message_id: payload.replyToMessageId,
    })),
  })
}
