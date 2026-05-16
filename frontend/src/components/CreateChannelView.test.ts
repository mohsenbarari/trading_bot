import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiFetchMock = vi.fn()
const apiFetchJsonMock = vi.fn()
const pushBackStateMock = vi.fn()
const popBackStateMock = vi.fn()
const discardBackStateMock = vi.fn()
const buildChatFileUrlMock = vi.fn(() => '')
const uploadAvatarImageMock = vi.fn()
const routerPushMock = vi.fn()
const routerResolveMock = vi.fn()
const currentRouteState = { value: { fullPath: '/chat?user_id=-7' } }

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
  apiFetchJson: apiFetchJsonMock,
}))

vi.mock('../composables/useBackButton', () => ({
  pushBackState: pushBackStateMock,
  popBackState: popBackStateMock,
  discardBackState: discardBackStateMock,
}))

vi.mock('../utils/chatFiles', () => ({
  buildChatFileUrl: buildChatFileUrlMock,
  getAvatarInitial: (value: string) => value.slice(0, 1).toUpperCase(),
  uploadAvatarImage: uploadAvatarImageMock,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
    resolve: routerResolveMock,
    currentRoute: currentRouteState,
  }),
}))

function makeResponse(payload: unknown, ok = true, status = ok ? 200 : 400) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

describe('CreateChannelView.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchJsonMock.mockReset()
    pushBackStateMock.mockReset()
    popBackStateMock.mockReset()
    discardBackStateMock.mockReset()
    buildChatFileUrlMock.mockClear()
    uploadAvatarImageMock.mockReset()
    routerPushMock.mockReset()
    routerResolveMock.mockReset()
    routerResolveMock.mockReturnValue({ href: '/users/2?account_name=member2' })
    currentRouteState.value.fullPath = '/chat?user_id=-7'
    routerPushMock.mockImplementation(async () => {
      currentRouteState.value.fullPath = '/users/2?account_name=member2'
    })
  })

  it('emits open-channel from overview and routes member profiles from the channel members page', async () => {
    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/chat/channels') {
        return [
          {
            id: 7,
            type: 'channel',
            title: 'Channel Seven',
            description: 'Example channel',
            avatar_file_id: null,
            created_by_id: 1,
            is_system: false,
            is_mandatory: false,
            member_count: 2,
            created_at: '2026-05-12T10:00:00',
          },
        ]
      }

      if (url === '/api/chat/channels/7/members') {
        return [
          {
            user_id: 1,
            account_name: 'owner1',
            full_name: 'Owner One',
            mobile_number: '09120000001',
            avatar_file_id: null,
            role: 'admin',
            joined_at: '2026-05-12T10:00:00',
            is_channel_creator: true,
          },
          {
            user_id: 2,
            account_name: 'member2',
            full_name: 'Member Two',
            mobile_number: '09120000002',
            avatar_file_id: null,
            role: 'member',
            joined_at: '2026-05-12T10:01:00',
            is_channel_creator: false,
          },
        ]
      }

      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    const CreateChannelView = (await import('./CreateChannelView.vue')).default
    const wrapper = mount(CreateChannelView, {
      props: {
        apiBaseUrl: '',
        jwtToken: 'token',
        currentUserId: 1,
        showCloseButton: true,
        initialChannelId: 7,
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          transition: false,
        },
      },
    })

    await flushPromises()
    await flushPromises()

    const openChannelButton = wrapper.findAll('button').find((button) => button.text().includes('باز کردن در پیام‌رسان'))
    expect(openChannelButton).toBeTruthy()
    await openChannelButton!.trigger('click')
    expect(wrapper.emitted('open-channel')?.[0]).toEqual([{ chatId: 7, title: 'Channel Seven' }])

    const membersEntry = wrapper.findAll('.telegram-row').find((row) => row.text().includes('اعضای کانال'))
    expect(membersEntry).toBeTruthy()
    await membersEntry!.trigger('click')
    await flushPromises()

    const memberRow = wrapper.findAll('.chat-user-row').find((row) => row.text().includes('Member Two'))
    expect(memberRow).toBeTruthy()
    const profileButton = memberRow!.findAll('.chat-user-row__action-btn').find((button) => button.text().includes('پروفایل'))
    expect(profileButton).toBeTruthy()
    await profileButton!.trigger('click')
    await flushPromises()

    expect(routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '2' },
      query: { account_name: 'member2' },
    })
  })

  it('creates a new channel with an uploaded avatar and adds selected members', async () => {
    const createdChannel = {
      id: 12,
      type: 'channel' as const,
      title: 'Fresh Channel',
      description: 'Created here',
      avatar_file_id: 'avatar-12',
      created_by_id: 1,
      is_system: false,
      is_mandatory: false,
      member_count: 1,
      created_at: '2026-05-12T11:00:00',
    }
    const inviteCandidate = {
      user_id: 2,
      account_name: 'member2',
      full_name: 'Member Two',
      mobile_number: '09120000002',
      avatar_file_id: null,
      is_already_member: false,
    }
    let currentMembers: Array<{
      user_id: number
      account_name: string
      full_name: string
      mobile_number: string
      avatar_file_id: null
      role: 'admin' | 'member'
      joined_at: string
      is_channel_creator: boolean
    }> = [
      {
        user_id: 1,
        account_name: 'owner1',
        full_name: 'Owner One',
        mobile_number: '09120000001',
        avatar_file_id: null,
        role: 'admin' as const,
        joined_at: '2026-05-12T11:00:00',
        is_channel_creator: true,
      },
    ]

    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/chat/channels') return []
      if (url === '/api/chat/channels/12/members') return currentMembers
      if (url.startsWith('/api/chat/channels/invite-candidates?')) {
        return {
          items: currentMembers.some((member) => member.user_id === 2) ? [] : [inviteCandidate],
          total: 1,
          active_total: 1,
        }
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/chat/channels') {
        const body = JSON.parse(String(options?.body || '{}'))
        expect(body).toMatchObject({
          title: 'Fresh Channel',
          description: 'Created here',
          avatar_file_id: 'avatar-12',
        })
        return makeResponse({ channel: createdChannel, member_picker_required: true })
      }

      if (url === '/api/chat/channels/12/members/bulk') {
        const body = JSON.parse(String(options?.body || '{}'))
        expect(body).toEqual({ user_ids: [2] })
        currentMembers = [
          ...currentMembers,
          {
            user_id: 2,
            account_name: 'member2',
            full_name: 'Member Two',
            mobile_number: '09120000002',
            avatar_file_id: null,
            role: 'member',
            joined_at: '2026-05-12T11:05:00',
            is_channel_creator: false,
          },
        ]
        createdChannel.member_count = 2
        return makeResponse({
          chat_id: 12,
          processed_user_ids: [2],
          added_count: 1,
          reactivated_count: 0,
          already_member_count: 0,
          member_count: 2,
          select_all_active_users: false,
        })
      }

      throw new Error(`Unhandled apiFetch call: ${url}`)
    })

    uploadAvatarImageMock.mockResolvedValue({ file_id: 'avatar-12' })

    const CreateChannelView = (await import('./CreateChannelView.vue')).default
    const wrapper = mount(CreateChannelView, {
      props: {
        apiBaseUrl: '',
        jwtToken: 'token',
        currentUserId: 1,
        showCloseButton: true,
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          transition: false,
        },
      },
    })

    await flushPromises()

    const openCreateButton = wrapper.findAll('button').find((button) => button.text().includes('کانال جدید'))
    expect(openCreateButton).toBeTruthy()
    await openCreateButton!.trigger('click')

    const avatarInput = wrapper.get('input[type="file"]')
    Object.defineProperty(avatarInput.element, 'files', {
      configurable: true,
      value: [new File(['avatar'], 'channel.png', { type: 'image/png' })],
    })
    await avatarInput.trigger('change')
    await flushPromises()

    await wrapper.get('#channel-title').setValue('Fresh Channel')
    await wrapper.get('#channel-description').setValue('Created here')

    const createButton = wrapper.findAll('button').find((button) => button.text().includes('ساخت کانال'))
    expect(createButton).toBeTruthy()
    await createButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(uploadAvatarImageMock).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('refresh-conversations')).toHaveLength(1)
    expect(wrapper.text()).toContain('افزودن عضو')

    const candidateRow = wrapper.findAll('.chat-user-row').find((row) => row.text().includes('Member Two'))
    expect(candidateRow).toBeTruthy()
    await candidateRow!.trigger('click')

    const addMembersButton = wrapper.findAll('button').find((button) => button.text().trim() === 'افزودن')
    expect(addMembersButton).toBeTruthy()
    await addMembersButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.emitted('refresh-conversations')).toHaveLength(2)
    expect(currentMembers).toHaveLength(2)
    expect(wrapper.text()).toContain('اعضای کانال')
    expect(wrapper.text()).toContain('Member Two')
  })

  it('updates channel settings, promotes and demotes admins, then deletes the current channel', async () => {
    let channelDeleted = false
    let currentChannel = {
      id: 7,
      type: 'channel' as const,
      title: 'Channel Seven',
      description: 'Example channel',
      avatar_file_id: 'old-avatar',
      created_by_id: 1,
      is_system: false,
      is_mandatory: false,
      member_count: 2,
      created_at: '2026-05-12T10:00:00',
    }
    let currentMembers = [
      {
        user_id: 1,
        account_name: 'owner1',
        full_name: 'Owner One',
        mobile_number: '09120000001',
        avatar_file_id: null,
        role: 'admin' as const,
        joined_at: '2026-05-12T10:00:00',
        is_channel_creator: true,
      },
      {
        user_id: 2,
        account_name: 'member2',
        full_name: 'Member Two',
        mobile_number: '09120000002',
        avatar_file_id: null,
        role: 'member' as const,
        joined_at: '2026-05-12T10:01:00',
        is_channel_creator: false,
      },
    ]
    const patchBodies: any[] = []

    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/chat/channels') {
        return channelDeleted ? [] : [currentChannel]
      }
      if (url === '/api/chat/channels/7/members') {
        return currentMembers
      }
      if (url.startsWith('/api/chat/channels/invite-candidates?')) {
        return { items: [], total: 0, active_total: 0 }
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/chat/channels/7') {
        const body = JSON.parse(String(options?.body || '{}'))
        patchBodies.push(body)
        currentChannel = {
          ...currentChannel,
          title: body.title,
          description: body.description,
          avatar_file_id: body.avatar_file_id,
        }
        return makeResponse(currentChannel)
      }

      if (url === '/api/chat/channels/7/members/2') {
        const body = JSON.parse(String(options?.body || '{}'))
        if (body.role === 'admin') {
          currentMembers = currentMembers.map((member) =>
            member.user_id === 2 ? { ...member, role: 'admin' as const } : member,
          )
          return makeResponse({ chat_id: 7, user_id: 2, role: 'admin', removed: false, member_count: 2 })
        }
        currentMembers = currentMembers.map((member) =>
          member.user_id === 2 ? { ...member, role: 'member' as const } : member,
        )
        return makeResponse({ chat_id: 7, user_id: 2, role: 'member', removed: false, member_count: 2 })
      }

      if (url === '/api/chat/channels/7/unfollow') {
        channelDeleted = true
        return makeResponse({ chat_id: 7, user_id: 1, role: null, removed: true, member_count: 0, left: true })
      }

      throw new Error(`Unhandled apiFetch call: ${url}`)
    })

    const CreateChannelView = (await import('./CreateChannelView.vue')).default
    const wrapper = mount(CreateChannelView, {
      props: {
        apiBaseUrl: '',
        jwtToken: 'token',
        currentUserId: 1,
        showCloseButton: false,
        initialChannelId: 7,
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          transition: false,
        },
      },
    })

    await flushPromises()
    await flushPromises()

    const settingsEntry = wrapper.findAll('.telegram-row').find((row) => row.text().includes('تنظیمات کانال'))
    expect(settingsEntry).toBeTruthy()
    await settingsEntry!.trigger('click')
    await flushPromises()

    const removeAvatarButton = wrapper.findAll('button').find((button) => button.text().includes('حذف عکس'))
    expect(removeAvatarButton).toBeTruthy()
    await removeAvatarButton!.trigger('click')
    await wrapper.get('#edit-channel-title').setValue('Renamed Channel')
    await wrapper.get('#edit-channel-description').setValue('Updated channel details')

    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('ذخیره تغییرات'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(patchBodies[0]).toMatchObject({
      title: 'Renamed Channel',
      description: 'Updated channel details',
      avatar_file_id: null,
    })
    expect(wrapper.emitted('refresh-conversations')).toHaveLength(1)
    expect(wrapper.text()).toContain('Renamed Channel')
    expect(currentChannel.description).toBe('Updated channel details')

    const adminsEntry = wrapper.findAll('.telegram-row').find((row) => row.text().includes('مدیریت ادمین‌ها'))
    expect(adminsEntry).toBeTruthy()
    await adminsEntry!.trigger('click')
    await flushPromises()

    const promoteButton = wrapper.findAll('.chat-user-row__action-btn').find((button) => button.text().includes('ارتقا به ادمین'))
    expect(promoteButton).toBeTruthy()
    await promoteButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.emitted('refresh-conversations')).toHaveLength(2)
    expect(currentMembers.find((member) => member.user_id === 2)?.role).toBe('admin')

    const demoteButton = wrapper.findAll('.chat-user-row__action-btn').find((button) => button.text().includes('حذف ادمین'))
    expect(demoteButton).toBeTruthy()
    await demoteButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.emitted('refresh-conversations')).toHaveLength(3)
    expect(currentMembers.find((member) => member.user_id === 2)?.role).toBe('member')

    const headerButtons = wrapper.findAll('.manager-header .header-icon-btn')
    expect(headerButtons[0]).toBeTruthy()
    await headerButtons[0]!.trigger('click')
    await flushPromises()

    const deleteChannelButton = wrapper.findAll('.telegram-row').find((row) => row.text().includes('حذف کانال'))
    expect(deleteChannelButton).toBeTruthy()
    await deleteChannelButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.emitted('left')?.[0]).toEqual([7])
    expect(wrapper.emitted('refresh-conversations')).toHaveLength(4)
    expect(wrapper.text()).toContain('ساخت کانال جدید')
  })

})