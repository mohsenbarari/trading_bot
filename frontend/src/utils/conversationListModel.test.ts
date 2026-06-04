import { describe, expect, it } from 'vitest'
import type { ChatTimelineGroup, Conversation, Message } from '../types/chat'
import {
  compareMessengerConversationActivity,
  getNextPinnedConversationOrder,
  isMandatoryPinnedConversation,
  isMessengerConversationPinned,
  selectConversationWindow,
  shouldExpandConversationWindow,
  sortMessengerConversations,
  summarizeTimelineRenderBudget,
} from './conversationListModel'

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 1,
    other_user_id: 11,
    other_user_name: 'direct-user',
    avatar_file_id: null,
    other_user_is_deleted: false,
    last_message_content: 'hello',
    last_message_type: 'text',
    last_message_at: '2026-05-30T10:00:00',
    unread_count: 0,
    other_user_last_seen_at: null,
    room_kind: 'direct',
    can_send: true,
    member_role: null,
    member_count: null,
    max_members: null,
    is_system: false,
    is_mandatory: false,
    is_muted: false,
    is_pinned: false,
    pinned_at: null,
    pin_order: null,
    ...overrides,
  }
}

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    sender_id: 11,
    receiver_id: 22,
    content: 'hello',
    message_type: 'text',
    is_read: false,
    created_at: '2026-05-30T10:00:00',
    ...overrides,
  }
}

describe('conversationListModel', () => {
  it('sorts mandatory, pinned, and recent conversations with stable pure helpers', () => {
    const recent = conversation({ id: 1, other_user_id: 1, other_user_name: 'recent', last_message_at: '2026-05-30T10:00:00' })
    const stale = conversation({ id: 2, other_user_id: 2, other_user_name: 'stale', last_message_at: '2026-05-29T10:00:00' })
    const pinnedLow = conversation({ id: 3, other_user_id: 3, other_user_name: 'pinned-low', is_pinned: true, pin_order: 1, pinned_at: '2026-05-30T08:00:00' })
    const pinnedHigh = conversation({ id: 4, other_user_id: 4, other_user_name: 'pinned-high', is_pinned: true, pin_order: 3, pinned_at: '2026-05-30T09:00:00' })
    const mandatory = conversation({ id: 5, other_user_id: -5, chat_id: 5, other_user_name: 'mandatory', room_kind: 'channel', is_mandatory: true })

    expect(isMandatoryPinnedConversation(mandatory)).toBe(true)
    expect(isMessengerConversationPinned(mandatory)).toBe(true)
    expect(getNextPinnedConversationOrder([recent, pinnedLow, pinnedHigh, mandatory])).toBe(4)
    expect(compareMessengerConversationActivity(recent, stale)).toBe(-1)
    expect(sortMessengerConversations([stale, recent, pinnedLow, mandatory, pinnedHigh]).map(item => item.other_user_name)).toEqual([
      'mandatory',
      'pinned-high',
      'pinned-low',
      'recent',
      'stale',
    ])
  })

  it('selects a bounded conversation window while keeping the active row visible', () => {
    const conversations = Array.from({ length: 12 }, (_, index) => conversation({
      id: index + 1,
      other_user_id: index + 1,
      other_user_name: `user-${index + 1}`,
    }))

    expect(selectConversationWindow(conversations, { limit: 0 })).toEqual({
      items: [],
      hasMore: true,
      hiddenCount: 12,
    })

    const window = selectConversationWindow(conversations, { limit: 5, selectedUserId: 12 })
    expect(window.items.map(item => item.other_user_id)).toEqual([1, 2, 3, 4, 5, 12])
    expect(window.hasMore).toBe(true)
    expect(window.hiddenCount).toBe(6)
  })

  it('detects near-bottom expansion and summarizes timeline render budget', () => {
    expect(shouldExpandConversationWindow({ scrollTop: 300, clientHeight: 600, scrollHeight: 1500 })).toBe(true)
    expect(shouldExpandConversationWindow({ scrollTop: 100, clientHeight: 600, scrollHeight: 1500 }, 200)).toBe(false)

    const album = {
      type: 'album' as const,
      id: 'album-a',
      sender_id: 11,
      created_at: '2026-05-30T10:00:00',
      messages: [message({ id: 1, message_type: 'image' }), message({ id: 2, message_type: 'video' })],
    }
    const groups: ChatTimelineGroup[] = [{
      label: 'امروز',
      items: [album, message({ id: 3, message_type: 'document' })],
    }]

    expect(summarizeTimelineRenderBudget(groups)).toEqual({
      groupCount: 1,
      itemCount: 2,
      albumWrapperCount: 1,
      mediaItemCount: 3,
      virtualizationCandidate: false,
    })
  })
})