import type { ChatUserListRowBadge } from '../components/chat/ChatUserListRow.vue'
import type { ChatRoleKind } from '../types/chat'

type ChatRoleSource = {
  chat_role_kind?: ChatRoleKind | string | null
  chat_role_label?: string | null
}

export function getChatRoleBadge(source: ChatRoleSource): ChatUserListRowBadge | null {
  const label = (source.chat_role_label || '').trim()
  if (!label) return null

  if (source.chat_role_kind === 'accountant') {
    return { label, tone: 'target' }
  }
  if (source.chat_role_kind === 'customer') {
    return { label, tone: 'creator' }
  }
  return { label, tone: 'member' }
}

export function getChatRoleBadgeClass(source: ChatRoleSource): string {
  if (source.chat_role_kind === 'accountant') return 'role-accountant'
  if (source.chat_role_kind === 'customer') return 'role-customer'
  return 'role-colleague'
}
