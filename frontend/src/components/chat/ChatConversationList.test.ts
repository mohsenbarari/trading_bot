import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

  afterEach(() => {
    vi.useRealTimers()
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

  it('shows the customer badge for direct conversations when the role label is missing', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const customerConversation = makeConversation({
      other_user_name: 'مشتری بازار تهران',
      chat_role_kind: 'customer',
      chat_role_label: null,
    })
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [customerConversation],
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

    expect(wrapper.get('.conv-name').text()).toBe('مشتری بازار تهران')
    expect(wrapper.get('.conv-role-badge').text()).toBe('مشتری')
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

  it('renders system management conversations distinctly without leave actions', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const managementRoom = makeConversation({
      id: 3,
      chat_id: 30,
      other_user_id: -30,
      other_user_name: 'پیام مدیریت',
      room_kind: 'group',
      is_system: true,
      can_send: false,
      last_message_content: 'اطلاعیه مهم بازار',
    })

    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [managementRoom],
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

    const card = wrapper.find('.conversation-card')
    expect(card.classes()).toContain('conversation-card--management')
    expect(wrapper.text()).toContain('پیام مدیریت · اطلاعیه مهم بازار')

    await wrapper.find('.conversation-item').trigger('contextmenu', { clientX: 48, clientY: 52 })
    await flushPromises()

    const menuText = wrapper.find('.conversation-menu-panel').text()
    expect(menuText).not.toContain('ترک گروه')
    expect(menuText).not.toContain('حذف گفتگو')
  })

  it('emits the optional-channel unfollow action and renders a warning divider before it', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const optionalChannel = makeConversation({
      id: 12,
      chat_id: 101,
      other_user_id: -101,
      other_user_name: 'Optional Channel',
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
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    await wrapper.get('.conversation-item').trigger('contextmenu', { clientX: 48, clientY: 52 })
    await flushPromises()

    expect(wrapper.findAll('.menu-action-divider')).toHaveLength(1)
    const unfollowAction = wrapper.findAll('.menu-action').find((button) => button.text().includes('لغو دنبال‌کردن'))
    expect(unfollowAction).toBeTruthy()

    await unfollowAction!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('conversation-action')?.[0]?.[0]).toMatchObject({
      action: 'unfollow',
      conv: expect.objectContaining({ id: optionalChannel.id }),
    })
    expect(discardBackStateMock).toHaveBeenCalledTimes(1)
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

  it('shows media captions in the conversation preview text', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [makeConversation({
          last_message_type: 'image',
          last_message_content: JSON.stringify({
            file_id: 'image-11',
            caption: 'کپشن آخرین تصویر',
          }),
        })],
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

    expect(wrapper.text()).toContain('تصویر · کپشن آخرین تصویر')
  })

  it('shows generalized activity text for room conversations', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const groupConversation = makeConversation({
      id: 2,
      chat_id: 17,
      other_user_id: -17,
      other_user_name: 'Group Alpha',
      room_kind: 'group',
      last_message_content: 'old preview',
    })

    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [groupConversation],
        selectedUserId: null,
        typingUsers: {},
        activityTextByConversation: {
          [-17]: 'علی در حال ارسال فایل...',
        },
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

    expect(wrapper.text()).toContain('علی در حال ارسال فایل')
    expect(wrapper.text()).not.toContain('old preview')
  })

  it('renders avatar images, online state, unread badges, and emits the new-conversation action', async () => {
    buildChatFileUrlMock.mockReturnValue('https://cdn.example.com/avatar.png')
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [makeConversation({
          avatar_file_id: 'avatar-1',
          unread_count: 12,
          other_user_last_seen_at: new Date(Date.now() - 60_000).toISOString(),
        })],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '/api',
        canStartNewConversation: true,
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    expect(wrapper.get('.conv-avatar-image').attributes('src')).toBe('https://cdn.example.com/avatar.png')
    expect(wrapper.find('.online-indicator-dot').exists()).toBe(true)
    expect(wrapper.get('.unread-badge').text()).toBe('۱۲')

    await wrapper.get('.fab-new-chat').trigger('click')
    expect(wrapper.emitted('new-conversation')).toHaveLength(1)
  })

  it('renders long conversation lists through a bounded progressive window', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const conversations = Array.from({ length: 95 }, (_, index) => makeConversation({
      id: index + 1,
      other_user_id: index + 1,
      other_user_name: `User ${index + 1}`,
      last_message_at: `2026-05-12T10:${String(index % 60).padStart(2, '0')}:00`,
    }))

    const wrapper = mount(ChatConversationList, {
      props: {
        conversations,
        selectedUserId: 95,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    expect(wrapper.findAll('.conversation-item')).toHaveLength(65)
    expect(wrapper.text()).toContain('User 95')
    expect(wrapper.get('.conversation-window-more').text()).toContain('۳۰')

    await wrapper.get('.conversation-window-more').trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(95)
    expect(wrapper.find('.conversation-window-more').exists()).toBe(false)
  })

  it('suppresses the immediate post-menu click and then restores normal conversation selection after closing', async () => {
    vi.useFakeTimers()
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const conversation = makeConversation()
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [conversation],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    const item = wrapper.get('.conversation-item')
    await item.trigger('contextmenu', { clientX: 30, clientY: 40 })
    await flushPromises()
    await item.trigger('click')
    expect(wrapper.emitted('select-conversation')).toBeUndefined()

    await wrapper.get('.conversation-menu-overlay').trigger('click')
    vi.runAllTimers()
    await flushPromises()

    expect(popBackStateMock).toHaveBeenCalledTimes(1)

    await item.trigger('click')
    expect(wrapper.emitted('select-conversation')?.[0]?.[0]).toMatchObject({ id: conversation.id })
  })

  it('cancels long-press on pointer move but opens the menu after a stable hold', async () => {
    vi.useFakeTimers()
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [makeConversation()],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    const item = wrapper.get('.conversation-item')
    item.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 20, clientY: 20 }))
    item.element.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 40, clientY: 42 }))
    vi.advanceTimersByTime(450)
    await flushPromises()
    expect(wrapper.find('.conversation-menu-panel').exists()).toBe(false)

    item.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 24, clientY: 24 }))
    vi.advanceTimersByTime(420)
    await flushPromises()
    expect(wrapper.find('.conversation-menu-panel').exists()).toBe(true)
  })

  it('shows pin reordering, mark-unread, and mute actions for pinned direct conversations', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [
          makeConversation({ id: 1, is_pinned: true, pin_order: 2, pinned_at: '2026-05-12T10:10:00' }),
          makeConversation({ id: 2, other_user_id: 22, other_user_name: 'Pinned B', is_pinned: true, pin_order: 1, pinned_at: '2026-05-12T10:09:00' }),
        ],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    await wrapper.findAll('.conversation-item')[1]!.trigger('contextmenu', { clientX: 60, clientY: 80 })
    await flushPromises()

    const menuText = wrapper.get('.conversation-menu-panel').text()
    expect(menuText).toContain('برداشتن سنجاق')
    expect(menuText).toContain('جابجایی به بالا')
    expect(menuText).toContain('علامت‌گذاری به‌عنوان خوانده‌نشده')
    expect(menuText).toContain('بی‌صدا کردن گفتگو')
  })

  it('shows the empty action state for mandatory channels when no actions are available', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [makeConversation({
          id: 5,
          chat_id: 33,
          other_user_id: -33,
          other_user_name: 'Mandatory Channel',
          room_kind: 'channel',
          is_mandatory: true,
          unread_count: 2,
        })],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    await wrapper.get('.conversation-item').trigger('contextmenu', { clientX: 80, clientY: 90 })
    await flushPromises()

    expect(wrapper.get('.conversation-menu-empty').text()).toContain('برای این گفتگو عملیاتی در دسترس نیست')
  })

  it('sorts pinned and recent conversations, shows typing text, and offers group leave plus pin-down actions', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [
          makeConversation({ id: 8, other_user_id: 18, other_user_name: 'Older Direct', last_message_at: '2026-05-12T08:00:00' }),
          makeConversation({ id: 7, other_user_id: 17, other_user_name: 'Pinned Older', is_pinned: true, pinned_at: '2026-05-12T10:00:00', pin_order: null }),
          makeConversation({ id: 9, other_user_id: 19, other_user_name: 'Pinned Newer', is_pinned: true, pinned_at: '2026-05-12T10:05:00', pin_order: null }),
          makeConversation({ id: 6, chat_id: 26, other_user_id: -26, other_user_name: 'Group Room', room_kind: 'group', is_pinned: false }),
          makeConversation({ id: 10, other_user_id: 20, other_user_name: 'Typing Direct', last_message_at: '2026-05-12T09:00:00' }),
        ],
        selectedUserId: null,
        typingUsers: { 20: true },
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    expect(wrapper.text()).toContain('در حال نوشتن...')

    const pinnedRow = wrapper.findAll('.conversation-item').find((row) => row.text().includes('Pinned Newer'))
    expect(pinnedRow).toBeTruthy()
    await pinnedRow!.trigger('contextmenu', { clientX: 44, clientY: 66 })
    await flushPromises()
    expect(wrapper.get('.conversation-menu-panel').text()).toContain('جابجایی به پایین')

    const groupRow = wrapper.findAll('.conversation-item').find((row) => row.text().includes('Group Room'))
    expect(groupRow).toBeTruthy()
    await groupRow!.trigger('contextmenu', { clientX: 55, clientY: 77 })
    await flushPromises()
    expect(wrapper.get('.conversation-menu-panel').text()).toContain('ترک گروه')
  })

  it('emits the group leave action and closes the menu through the action path', async () => {
    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const groupConversation = makeConversation({
      id: 13,
      chat_id: 131,
      other_user_id: -131,
      other_user_name: 'Group Action Room',
      room_kind: 'group',
    })

    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [groupConversation],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    await wrapper.get('.conversation-item').trigger('contextmenu', { clientX: 58, clientY: 84 })
    await flushPromises()

    const leaveAction = wrapper.findAll('.menu-action').find((button) => button.text().includes('ترک گروه'))
    expect(leaveAction).toBeTruthy()

    await leaveAction!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('conversation-action')?.[0]?.[0]).toMatchObject({
      action: 'leave',
      conv: expect.objectContaining({ id: groupConversation.id }),
    })
    expect(discardBackStateMock).toHaveBeenCalledTimes(1)
  })

  it('runs the registered back-state closer and clears pending long-press timers on unmount', async () => {
    vi.useFakeTimers()
    let registeredBackHandler: null | (() => void) = null
    pushBackStateMock.mockImplementationOnce((handler: () => void) => {
      registeredBackHandler = handler
    })
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')

    const ChatConversationList = (await import('./ChatConversationList.vue')).default
    const wrapper = mount(ChatConversationList, {
      props: {
        conversations: [makeConversation()],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    await wrapper.get('.conversation-item').trigger('contextmenu', { clientX: 20, clientY: 30 })
    await flushPromises()
    expect(typeof registeredBackHandler).toBe('function')

    if (!registeredBackHandler) {
      throw new Error('Expected registered back handler')
    }
    (registeredBackHandler as () => void)()
    await flushPromises()
    const freshWrapper = mount(ChatConversationList, {
      props: {
        conversations: [makeConversation()],
        selectedUserId: null,
        typingUsers: {},
        apiBaseUrl: '',
      },
      global: {
        directives: { ripple: {} },
        stubs: { teleport: true, transition: false },
      },
    })

    const item = freshWrapper.get('.conversation-item')
    item.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 12, clientY: 12 }))
    freshWrapper.unmount()
    expect(clearTimeoutSpy).toHaveBeenCalled()
  })
})
