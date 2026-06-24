import type { ChatUserListRowBadge } from '../components/chat/ChatUserListRow.vue'
import type { ChatRoleKind } from '../types/chat'

type ChatRoleSource = {
  chat_role_kind?: ChatRoleKind | string | null
  chat_role_label?: string | null
  chat_accountant_owner_label?: string | null
}

export function getChatRoleBadge(source: ChatRoleSource): ChatUserListRowBadge | null {
  if (source.chat_role_kind === 'accountant') {
    const label = (source.chat_role_label || '').trim()
    if (!label) return null
    return { label, tone: 'target' }
  }
  if (source.chat_role_kind === 'customer') {
    const label = (source.chat_role_label || '').trim() || 'مشتری'
    return { label, tone: 'creator' }
  }
  const label = (source.chat_role_label || '').trim()
  if (!label) return null
  return { label, tone: 'member' }
}

export function getAccountantOwnerBadge(source: ChatRoleSource): ChatUserListRowBadge | null {
  if (source.chat_role_kind !== 'accountant') return null
  const label = (source.chat_accountant_owner_label || '').trim()
  return label ? { label, tone: 'info' } : null
}

export function getChatRoleBadgeClass(source: ChatRoleSource): string {
  if (source.chat_role_kind === 'accountant') return 'role-accountant'
  if (source.chat_role_kind === 'customer') return 'role-customer'
  return 'role-colleague'
}
