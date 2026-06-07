import { describe, expect, it } from 'vitest'
import { isUnreadMessageForViewer } from './chatUnread'

describe('chatUnread', () => {
  it('does not treat the viewer own room messages as unread jump anchors', () => {
    expect(isUnreadMessageForViewer({
      sender_id: 7,
      receiver_id: 7,
      is_read: false,
    } as any, 7)).toBe(false)
  })

  it('keeps unread messages from other users eligible for unread jumps', () => {
    expect(isUnreadMessageForViewer({
      sender_id: 12,
      receiver_id: 7,
      is_read: false,
    } as any, 7)).toBe(true)
  })

  it('ignores messages already marked as read', () => {
    expect(isUnreadMessageForViewer({
      sender_id: 12,
      receiver_id: 7,
      is_read: true,
    } as any, 7)).toBe(false)
  })
})
