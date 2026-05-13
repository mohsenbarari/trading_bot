import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { Conversation } from '../../types/chat'

const pushBackStateMock = vi.fn()
const popBackStateMock = vi.fn()
const discardBackStateMock = vi.fn()
const buildChatFileUrlMock = vi.fn(() => '')

vi.mock('../../composables/useBackButton', () => ({
  pushBackState: pushBackStateMock,
  popBackState: popBackStateMock,
  discardBackState: discardBackStateMock,
}))

vi.mock('../../utils/chatFiles', () => ({
  buildChatFileUrl: buildChatFileUrlMock,
  getAvatarInitial: (value: string) => value.slice(0, 1).toUpperCase(),
}))

vi.mock('@formkit/auto-animate/vue', () => ({
  vAutoAnimate: {},
}))

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 1,
    other_user_id: 11,
    other_user_name: 'direct-user',
    avatar_file_id: null,
    other_user_is_deleted: false,
    last_message_content: 'hello',
    last_message_type: 'text',
    last_message_at: '2026-05-12T10:00:00',
    unread_count: 0,
    other_user_last_seen_at: '2026-05-12T09:59:00',
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

describe('ChatConversationList.vue', () => {
  beforeEach(() => {
    pushBackStateMock.mockReset()
    popBackStateMock.mockReset()
    discardBackStateMock.mockReset()
    buildChatFileUrlMock.mockClear()
  })

  it('opens the direct-conversation menu and emits delete actions', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const directConversation = makeConversation()
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [directConversation],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    await wrapper.find('.conversation-item').trigger('contextmenu', { clientX: 32, clientY: 44 })
    await flushPromises()

    expect(pushBackStateMock).toHaveBeenCalledTimes(1)
    const deleteAction = wrapper.findAll('.menu-action').find((button) => button.text().includes('حذف گفتگو'))
    expect(deleteAction).toBeTruthy()

    await deleteAction!.trigger('click')

    expect(wrapper.emitted('conversation-action')?.[0]?.[0]).toMatchObject({
      action: 'delete',
      conv: expect.objectContaining({ id: directConversation.id }),
    })
    expect(discardBackStateMock).toHaveBeenCalledTimes(1)
  })

  it('shows optional-channel specific menu actions without direct delete actions', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const optionalChannel = makeConversation({
      id: 2,
      chat_id: 17,
      other_user_id: -17,
      other_user_name: 'Channel Alpha',
      room_kind: 'channel',
      is_mandatory: false,
      is_muted: true,
    })

    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [optionalChannel],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    await wrapper.find('.conversation-item').trigger('contextmenu', { clientX: 48, clientY: 52 })
    await flushPromises()

    const menuText = wrapper.find('.conversation-menu-panel').text()
    expect(menuText).toContain('لغو دنبال‌کردن')
    expect(menuText).toContain('خروج از حالت بی‌صدا')
    expect(menuText).not.toContain('حذف گفتگو')
  })

  it('hides the new conversation fab when starting new chats is disabled', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [makeConversation()],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
        canStartNewConversation: false,
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    expect(wrapper.find('.fab-new-chat').exists()).toBe(false)
  })
})