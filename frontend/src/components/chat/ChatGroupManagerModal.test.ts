import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiFetchMock = vi.fn()
const apiFetchJsonMock = vi.fn()
const pushBackStateMock = vi.fn()
const popBackStateMock = vi.fn()
const buildChatFileUrlMock = vi.fn(() => '')
const uploadAvatarImageMock = vi.fn()

vi.mock('../../utils/auth', () => ({
  apiFetch: apiFetchMock,
  apiFetchJson: apiFetchJsonMock,
}))

vi.mock('../../composables/useBackButton', () => ({
  pushBackState: pushBackStateMock,
  popBackState: popBackStateMock,
}))

vi.mock('../../utils/chatFiles', () => ({
  buildChatFileUrl: buildChatFileUrlMock,
  getAvatarInitial: (value: string) => value.slice(0, 1).toUpperCase(),
  uploadAvatarImage: uploadAvatarImageMock,
}))

function makeResponse(payload: unknown, ok = true, status = ok ? 200 : 400) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

describe('ChatGroupManagerModal.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchJsonMock.mockReset()
    pushBackStateMock.mockReset()
    popBackStateMock.mockReset()
    buildChatFileUrlMock.mockClear()
    uploadAvatarImageMock.mockReset()
  })

  it('emits member-profile and leave events from the loaded group manager', async () => {
    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/chat/groups/7') {
        return {
          group: {
            id: 7,
            title: 'Group Seven',
            description: 'Example group',
            avatar_file_id: null,
            member_count: 2,
            max_members: 50,
            current_user_role: 'admin',
          },
          members: [
            {
              user_id: 1,
              account_name: 'owner1',
              full_name: 'Owner One',
              mobile_number: '09120000001',
              avatar_file_id: null,
              role: 'admin',
              is_group_creator: true,
            },
            {
              user_id: 2,
              account_name: 'member2',
              full_name: 'Member Two',
              mobile_number: '09120000002',
              avatar_file_id: null,
              role: 'member',
              is_group_creator: false,
            },
          ],
        }
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockResolvedValue(makeResponse({}))

    const ChatGroupManagerModal = (await import('./ChatGroupManagerModal.vue')).default
    const wrapper = mount(ChatGroupManagerModal, {
      props: {
        show: true,
        groupId: 7,
        currentUserId: 1,
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

    await flushPromises()
    await flushPromises()

    const membersEntry = wrapper.findAll('.telegram-row').find((row) => row.text().includes('اعضای گروه'))
    expect(membersEntry).toBeTruthy()
    await membersEntry!.trigger('click')
    await flushPromises()

    const memberRow = wrapper.findAll('.chat-user-row').find((row) => row.text().includes('Member Two'))
    expect(memberRow).toBeTruthy()
    const profileButton = memberRow!.findAll('.chat-user-row__action-btn').find((button) => button.text().includes('پروفایل'))
    expect(profileButton).toBeTruthy()
    await profileButton!.trigger('click')
    expect(wrapper.emitted('open-public-profile')?.[0]).toEqual([{ id: 2, account_name: 'member2' }])

    await wrapper.find('.manager-header .header-icon-btn').trigger('click')
    await flushPromises()

    const leaveButton = wrapper.findAll('.telegram-row').find((row) => row.text().includes('خروج از گروه'))
    expect(leaveButton).toBeTruthy()
    await leaveButton!.trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/chat/groups/7/leave', { method: 'POST' })
    expect(wrapper.emitted('left')?.[0]).toEqual([7])
  })
})