import { resolveRoomConversationKey, type ChatActivityKind } from './chatRoomRouting'

export interface MessengerRealtimeActivityPayload {
  conversationKey: number
  senderId: number
  senderName: string
  activity: ChatActivityKind
  active: boolean
}

export interface MessengerMediaDownloadPatch {
  is_downloading: boolean
  download_progress: number
}

export type MessengerMediaDownloadStage = 'active' | 'completed' | 'reset'

const DEFAULT_ACTIVITY_SENDER_NAME = 'کاربر'

export function resolveMessengerRealtimeConversationKey(payload: unknown): number | null {
  const data = payload as Record<string, unknown> | null | undefined
  const roomConversationKey = resolveRoomConversationKey(data?.room_kind, data?.chat_id)
  if (roomConversationKey !== null) {
    return roomConversationKey
  }

  const senderId = Number(data?.sender_id)
  return Number.isFinite(senderId) ? senderId : null
}

export function normalizeMessengerRealtimeActivityPayload(payload: unknown): MessengerRealtimeActivityPayload | null {
  const data = payload as Record<string, unknown> | null | undefined
  const conversationKey = resolveMessengerRealtimeConversationKey(data)
  const senderId = Number(data?.sender_id)
  const activity = resolveMessengerActivityKind(data?.activity)

  if (conversationKey === null || !Number.isFinite(senderId) || !activity) {
    return null
  }

  return {
    conversationKey,
    senderId,
    senderName: normalizeMessengerActivitySenderName(data?.sender_name),
    activity,
    active: data?.active !== false,
  }
}

export function buildMessengerActivityLabel(
  conversationKey: number,
  activity: ChatActivityKind,
  senderNames: string[],
) {
  const suffix = activity === 'typing' ? 'در حال نوشتن...' : 'در حال ارسال فایل...'
  if (conversationKey > 0) {
    return suffix
  }

  const uniqueNames = Array.from(new Set(senderNames.filter((name) => name && name.trim())))
  if (uniqueNames.length === 1) {
    return `${uniqueNames[0]} ${suffix}`
  }
  if (uniqueNames.length > 1) {
    return `${uniqueNames.length.toLocaleString('fa-IR')} نفر ${suffix}`
  }
  return `یک نفر ${suffix}`
}

export function isMessengerRuntimeEventForConversation(
  eventConversationKey: number | null | undefined,
  selectedConversationKey: number | null | undefined,
) {
  return typeof eventConversationKey === 'number'
    && typeof selectedConversationKey === 'number'
    && eventConversationKey === selectedConversationKey
}

export function getMessengerMediaDownloadPatch(
  stage: MessengerMediaDownloadStage,
  progress = 0,
): MessengerMediaDownloadPatch {
  if (stage === 'active') {
    return {
      is_downloading: true,
      download_progress: clampMessengerProgress(progress),
    }
  }

  if (stage === 'completed') {
    return {
      is_downloading: false,
      download_progress: 100,
    }
  }

  return {
    is_downloading: false,
    download_progress: 0,
  }
}

function resolveMessengerActivityKind(value: unknown): ChatActivityKind | null {
  return value === 'typing' || value === 'uploading_file' ? value : null
}

function normalizeMessengerActivitySenderName(value: unknown) {
  return typeof value === 'string' && value.trim()
    ? value.trim()
    : DEFAULT_ACTIVITY_SENDER_NAME
}

function clampMessengerProgress(value: unknown) {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) return 0
  return Math.max(0, Math.min(100, Math.round(numericValue)))
}