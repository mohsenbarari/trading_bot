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

})