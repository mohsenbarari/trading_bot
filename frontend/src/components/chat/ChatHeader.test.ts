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
      },
      global: {
        directives: {
          ripple: {},
          'click-outside': {},
        },
      },
    })

    expect(wrapper.text()).toContain('در حال نوشتن')

    await wrapper.find('.header-avatar').trigger('click')
    expect(wrapper.emitted('view-profile')).toHaveLength(1)

    await wrapper.find('.header-menu-container .header-btn').trigger('click')
    await flushPromises()

    expect(pushBackStateMock).toHaveBeenCalledTimes(1)
    const searchItem = wrapper.findAll('.header-menu-item').find((item) => item.text().includes('جستجو'))
    expect(searchItem).toBeTruthy()

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
    expect(createGroupItem).toBeTruthy()
    expect(createChannelItem).toBeTruthy()

    await createGroupItem!.trigger('click')
    await createChannelItem!.trigger('click')
    expect(visibleWrapper.emitted('create-group')).toHaveLength(1)
    expect(visibleWrapper.emitted('create-channel')).toHaveLength(1)
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
})