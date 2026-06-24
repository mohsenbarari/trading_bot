import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatSearchGlobalList from './ChatSearchGlobalList.vue'
import type { Conversation } from '../../types/chat'

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 10,
    other_user_id: 91,
    other_user_name: 'مشتری بازار تهران',
    last_message_content: 'سلام',
    last_message_type: 'text',
    last_message_at: '2026-06-24T09:00:00Z',
    unread_count: 0,
    room_kind: 'direct',
    chat_role_kind: 'customer',
    chat_role_label: null,
    ...overrides,
  }
}

describe('ChatSearchGlobalList.vue', () => {
  it('tags customer names in global search results resolved from conversations', () => {
    const wrapper = mount(ChatSearchGlobalList, {
      props: {
        searchResults: [
          {
            id: 501,
            sender_id: 91,
            receiver_id: 7,
            content: 'needle',
            created_at: '2026-06-24T09:00:00Z',
          },
        ],
        searchQuery: 'needle',
        conversations: [makeConversation()],
        currentUserId: 7,
      },
      global: {
        directives: {
          ripple: {},
        },
      },
    })

    expect(wrapper.get('.customer-name-with-badge__name').text()).toBe('مشتری بازار تهران')
    expect(wrapper.get('.customer-name-with-badge__badge').text()).toBe('مشتری')
  })
})
