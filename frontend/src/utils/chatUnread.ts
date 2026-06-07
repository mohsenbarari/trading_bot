import type { Message } from '../types/chat'

type UnreadCandidateMessage = Pick<Message, 'sender_id' | 'receiver_id' | 'is_read'>

export function isUnreadMessageForViewer(message: UnreadCandidateMessage, currentUserId: number) {
  const viewerId = Number(currentUserId)
  const senderId = Number(message.sender_id)

  if (Number.isFinite(senderId) && senderId === viewerId) {
    return false
  }

  return Number(message.receiver_id) === viewerId && message.is_read !== true
}
