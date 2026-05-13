import type { Conversation, Message } from '../types/chat'

export type PublicProfileTarget = {
  id: number
  account_name: string
  highlight_accountant_user_id?: number | null
  highlight_accountant_relation_display_name?: string | null
}

function normalizePositiveInt(value: unknown): number | null {
  const normalized = Number(value)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
}

function normalizeAccountName(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function resolveConversationProfileTarget(
  conversation?: Pick<Conversation, 'other_user_id' | 'other_user_name' | 'profile_user_id' | 'profile_account_name' | 'highlight_accountant_user_id' | 'highlight_accountant_relation_display_name'> | null,
): PublicProfileTarget | null {
  if (!conversation) {
    return null
  }

  const targetId = normalizePositiveInt(conversation.profile_user_id) ?? normalizePositiveInt(conversation.other_user_id)
  const accountName = normalizeAccountName(conversation.profile_account_name) ?? normalizeAccountName(conversation.other_user_name)
  if (!targetId || !accountName) {
    return null
  }

  return {
    id: targetId,
    account_name: accountName,
    highlight_accountant_user_id: normalizePositiveInt(conversation.highlight_accountant_user_id),
    highlight_accountant_relation_display_name: normalizeAccountName(conversation.highlight_accountant_relation_display_name),
  }
}

export function resolveForwardedProfileTarget(
  message?: Pick<Message, 'forwarded_from_id' | 'forwarded_from_name' | 'forwarded_from_profile_user_id' | 'forwarded_from_profile_account_name' | 'forwarded_from_highlight_accountant_user_id' | 'forwarded_from_highlight_accountant_relation_display_name'> | null,
): PublicProfileTarget | null {
  if (!message) {
    return null
  }

  const targetId = normalizePositiveInt(message.forwarded_from_profile_user_id) ?? normalizePositiveInt(message.forwarded_from_id)
  const accountName = normalizeAccountName(message.forwarded_from_profile_account_name) ?? normalizeAccountName(message.forwarded_from_name)
  if (!targetId || !accountName) {
    return null
  }

  return {
    id: targetId,
    account_name: accountName,
    highlight_accountant_user_id: normalizePositiveInt(message.forwarded_from_highlight_accountant_user_id),
    highlight_accountant_relation_display_name: normalizeAccountName(message.forwarded_from_highlight_accountant_relation_display_name),
  }
}