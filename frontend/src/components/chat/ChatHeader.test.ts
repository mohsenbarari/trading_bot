import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

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

describe('ChatHeader.vue', () => {
  beforeEach(() => {
    pushBackStateMock.mockReset()
    popBackStateMock.mockReset()
    discardBackStateMock.mockReset()
    buildChatFileUrlMock.mockClear()
  })

  it('emits direct-profile and search actions from the direct-room header', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 12,
        selectedUserName: 'ali-user',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        apiBaseUrl: '',
        targetUserStatus: 'آنلاین',
        isTyping: true,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
        canSendAdminBroadcast: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect(wrapper.text()).toContain('در حال نوشتن')
    expect(wrapper.get('.chat-header').element.lastElementChild?.className).toContain('header-menu-container')

    await wrapper.find('.header-avatar').trigger('click')
    expect(wrapper.emitted('view-profile')).toHaveLength(1)

    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()

    expect(pushBackStateMock).toHaveBeenCalledTimes(1)
    const searchItem = wrapper.findAll('.header-menu-item').find((item) => item.text().includes('جستجو'))
    expect(searchItem).toBeTruthy()
    expect(wrapper.text()).toContain('اقدام اصلی')
    expect(wrapper.text()).toContain('ارتباط')

    await searchItem!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('toggle-search')).toHaveLength(1)
    expect(discardBackStateMock).toHaveBeenCalledTimes(1)
  })

  it('routes room-title actions to manage-room for groups and channels', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: -21,
        selectedUserName: 'Group Alpha',
        selectedAvatarFileId: null,
        selectedRoomKind: 'group',
        apiBaseUrl: '',
        targetUserStatus: '۱۲ عضو',
        activityStatusText: 'علی در حال ارسال فایل...',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: 12,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
        canSendAdminBroadcast: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect(wrapper.text()).toContain('علی در حال ارسال فایل')

    await wrapper.find('.header-user-info').trigger('click')
    expect(wrapper.emitted('manage-room')).toHaveLength(1)

    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()

    const manageItem = wrapper.findAll('.header-menu-item').find((item) => item.text().includes('مدیریت گروه'))
    expect(manageItem).toBeTruthy()

    await manageItem!.trigger('click')
    expect(wrapper.emitted('manage-room')).toHaveLength(2)

    await wrapper.setProps({
      selectedUserId: -22,
      selectedUserName: 'Channel Alpha',
      selectedRoomKind: 'channel',
      targetUserStatus: '۱۲ عضو',
      activityStatusText: '',
      roomMemberCount: 12,
    })
    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('مدیریت کانال')
    expect(wrapper.text()).not.toContain('تنظیمات کانال')
  })

  it('handles conversation-list actions and channel creation gating', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const hiddenWrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: null,
        selectedUserName: '',
        selectedAvatarFileId: null,
        selectedRoomKind: null,
        apiBaseUrl: '',
        targetUserStatus: '',
        isTyping: false,
        totalUnread: 7,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: false,
        canCreateChannel: false,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect(hiddenWrapper.text()).toContain('7')

    await hiddenWrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()
    expect(hiddenWrapper.text()).not.toContain('ساخت کانال')
    expect(hiddenWrapper.text()).not.toContain('ساخت گروه جدید')

    const visibleWrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: null,
        selectedUserName: '',
        selectedAvatarFileId: null,
        selectedRoomKind: null,
        apiBaseUrl: '',
        targetUserStatus: '',
        isTyping: false,
        totalUnread: 7,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
        canSendAdminBroadcast: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    await visibleWrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()

    const createChannelItem = visibleWrapper.findAll('.header-menu-item').find((item) => item.text().includes('ساخت کانال'))
    const createGroupItem = visibleWrapper.findAll('.header-menu-item').find((item) => item.text().includes('ساخت گروه جدید'))
    const adminBroadcastItem = visibleWrapper.findAll('.header-menu-item').find((item) => item.text().includes('ارسال پیام مدیریت'))
    expect(visibleWrapper.text()).toContain('مدیریت پیام‌رسان')
    expect(visibleWrapper.text()).toContain('مدیریت سیستم')
    expect(createGroupItem).toBeTruthy()
    expect(createChannelItem).toBeTruthy()
    expect(adminBroadcastItem).toBeTruthy()

    await createGroupItem!.trigger('click')
    await createChannelItem!.trigger('click')
    await adminBroadcastItem!.trigger('click')
    expect(visibleWrapper.emitted('create-group')).toHaveLength(1)
    expect(visibleWrapper.emitted('create-channel')).toHaveLength(1)
    expect(visibleWrapper.emitted('admin-broadcast')).toHaveLength(1)
  })

  it('keeps system management rooms non-manageable from title and menu', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: -44,
        selectedUserName: 'پیام مدیریت',
        selectedAvatarFileId: null,
        selectedRoomKind: 'group',
        apiBaseUrl: '',
        targetUserStatus: 'پیام مدیریت',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: 1,
        isRoomMandatory: false,
        isRoomSystem: true,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    await wrapper.find('.header-user-info').trigger('click')
    expect(wrapper.emitted('manage-room')).toBeUndefined()

    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('جستجو')
    expect(wrapper.text()).not.toContain('مدیریت گروه')
  })

  it('emits search updates and closes the search overlay from the mobile back button', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 12,
        selectedUserName: 'ali-user',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        apiBaseUrl: '',
        targetUserStatus: 'آنلاین',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: true,
        searchQuery: 'old',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    await wrapper.find('#search-input').setValue('needle')
    expect(wrapper.emitted('search')).toEqual([['needle']])

    await wrapper.find('.mobile-back-btn').trigger('click')
    expect(wrapper.emitted('toggle-search')).toHaveLength(1)
  })

  it('renders deleted direct users with avatar images and the inactive status override', async () => {
    buildChatFileUrlMock.mockReturnValue('/avatars/user-17.png')

    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 17,
        selectedUserName: 'deleted-user',
        selectedAvatarFileId: 'avatar-17',
        selectedRoomKind: 'direct',
        apiBaseUrl: '/api',
        targetUserStatus: 'آنلاین',
        activityStatusText: 'در حال ارسال فایل...',
        isTyping: true,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: true,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect(buildChatFileUrlMock).toHaveBeenCalledWith('avatar-17', '/api')
    expect(wrapper.get('.header-avatar-image').attributes('src')).toBe('/avatars/user-17.png')
    expect(wrapper.text()).toContain('غیرفعال')
    expect(wrapper.text()).toContain('حساب کاربری غیرفعال است')
    expect(wrapper.text()).not.toContain('در حال ارسال فایل')
    expect(wrapper.text()).not.toContain('در حال نوشتن')
  })

  it('renders direct header metadata so the visual RTL order becomes avatar then name then role then owner', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 33,
        selectedUserName: 'نوید',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        selectedChatRoleKind: 'accountant',
        selectedChatRoleLabel: 'حسابدار',
        selectedAccountantOwnerLabel: 'سرگروه: زهرا',
        apiBaseUrl: '',
        targetUserStatus: 'آخرین بازدید اخیراً',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    const identity = wrapper.get('.header-identity')
    const identityChildren = identity.element.children
    expect(identityChildren[0]?.className).toContain('header-avatar')
    expect(identityChildren[1]?.className).toContain('header-user-info')

    const titleRow = wrapper.get('.header-title-row')
    const directChildren = titleRow.element.children

    expect(directChildren[0]?.className).toContain('header-name-meta')
    expect(directChildren[1]?.className).toContain('direct-role')
    expect((directChildren[1] as HTMLElement | undefined)?.textContent).toContain('حسابدار')
    expect(directChildren[2]?.className).toContain('header-name')
    expect((directChildren[2] as HTMLElement | undefined)?.textContent).toContain('نوید')

    const metaChildren = directChildren[0]?.children ?? []
    expect(metaChildren).toHaveLength(1)
    expect(metaChildren[0]?.className).toContain('accountant-owner')
    expect((metaChildren[0] as HTMLElement | undefined)?.textContent).toContain('سرگروه: زهرا')
  })

  it('renders the selection header and emits clear-selection', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: true,
        selectedUserId: 12,
        selectedUserName: 'ali-user',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        apiBaseUrl: '',
        targetUserStatus: 'آنلاین',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 3,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect(wrapper.text()).toContain('3')
    expect(wrapper.find('#search-input').exists()).toBe(false)

    await wrapper.find('.header-btn').trigger('click')
    expect(wrapper.emitted('clear-selection')).toHaveLength(1)
  })

  it('prefers explicit activity status text, syncs searchQuery props, and formats room member counts', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default

    const activityWrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 12,
        selectedUserName: 'ali-user',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        apiBaseUrl: '',
        targetUserStatus: 'آنلاین',
        activityStatusText: 'در حال ارسال تصویر...',
        isTyping: true,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: 'old',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect(activityWrapper.text()).toContain('در حال ارسال تصویر')

    const searchWrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 12,
        selectedUserName: 'ali-user',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        apiBaseUrl: '',
        targetUserStatus: 'آنلاین',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: true,
        searchQuery: 'old',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })
    await searchWrapper.setProps({ searchQuery: 'fresh-query' })
    expect((searchWrapper.get('#search-input').element as HTMLInputElement).value).toBe('fresh-query')

    const roomCountWrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: -21,
        selectedUserName: 'Group Alpha',
        selectedAvatarFileId: null,
        selectedRoomKind: 'group',
        apiBaseUrl: '',
        targetUserStatus: '',
        activityStatusText: '',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: 1200,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect((roomCountWrapper.vm as any).roomMemberCountText).toBe('۱٬۲۰۰ عضو')
    expect((roomCountWrapper.vm as any).formatDateForSeparator('2026-05-12T10:00:00.000Z')).not.toBe('')
    expect((roomCountWrapper.vm as any).formatDateForSeparator('')).toBe('')
  })

  it('closes the menu through direct close calls, back-state callbacks, and prop watchers', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default
    const wrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 12,
        selectedUserName: 'ali-user',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        apiBaseUrl: '',
        targetUserStatus: 'آنلاین',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: true,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.header-menu-item').length).toBeGreaterThan(0)

    ;(wrapper.vm as any).closeMenu()
    await flushPromises()
    expect(wrapper.findAll('.header-menu-item')).toHaveLength(0)

    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()
    const backCallback = pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(backCallback).toBeTypeOf('function')
    backCallback?.()
    await flushPromises()
    expect(wrapper.findAll('.header-menu-item')).toHaveLength(0)

    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()
    await wrapper.setProps({ isSelectionMode: true })
    await flushPromises()
    expect(wrapper.findAll('.header-menu-item')).toHaveLength(0)
    expect(popBackStateMock).toHaveBeenCalled()
  })

  it('covers menu handler guards for view-profile and create-group actions', async () => {
    const ChatHeader = (await import('./ChatHeader.vue')).default

    const directWrapper = mount(ChatHeader, {
      props: {
        isSelectionMode: false,
        selectedUserId: 12,
        selectedUserName: 'ali-user',
        selectedAvatarFileId: null,
        selectedRoomKind: 'direct',
        apiBaseUrl: '',
        targetUserStatus: 'آنلاین',
        isTyping: false,
        totalUnread: 0,
        isSearchActive: false,
        searchQuery: '',
        searchResults: [],
        currentSearchIndex: 0,
        selectedMessagesCount: 0,
        isDeleted: false,
        roomMemberCount: null,
        isRoomMandatory: false,
        isRoomSystem: false,
        canCreateGroup: false,
        canCreateChannel: true,
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    await directWrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()
    ;(directWrapper.vm as any).handleMenuViewProfile()
    await flushPromises()
    expect(directWrapper.emitted('view-profile')).toHaveLength(1)

    await directWrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()
    ;(directWrapper.vm as any).handleMenuCreateGroup()
    await flushPromises()
    expect(directWrapper.emitted('create-group')).toBeUndefined()
  })
})
