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

  it('creates a new group with an uploaded avatar and selected initial members', async () => {
    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/users-public/search?limit=100') {
        return [
          {
            id: 1,
            account_name: 'owner1',
            full_name: 'Owner One',
            mobile_number: '09120000001',
            avatar_file_id: null,
          },
          {
            id: 2,
            account_name: 'member2',
            full_name: 'Member Two',
            mobile_number: '09120000002',
            avatar_file_id: null,
          },
        ]
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/chat/groups') {
        const body = JSON.parse(String(options?.body || '{}'))
        expect(body).toMatchObject({
          title: 'Fresh Group',
          description: 'Created in test',
          avatar_file_id: 'group-avatar',
          member_ids: [2],
        })
        return makeResponse({
          group: {
            id: 19,
            title: 'Fresh Group',
            description: 'Created in test',
            avatar_file_id: 'group-avatar',
            member_count: 2,
            max_members: 50,
            current_user_role: 'admin',
          },
        })
      }
      throw new Error(`Unhandled apiFetch call: ${url}`)
    })

    uploadAvatarImageMock.mockResolvedValue({ file_id: 'group-avatar' })

    const ChatGroupManagerModal = (await import('./ChatGroupManagerModal.vue')).default
    const wrapper = mount(ChatGroupManagerModal, {
      props: {
        show: true,
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

    const candidateRow = wrapper.findAll('.chat-user-row').find((row) => row.text().includes('Member Two'))
    expect(candidateRow).toBeTruthy()
    await candidateRow!.trigger('click')

    const continueButton = wrapper.findAll('button').find((button) => button.text().includes('ادامه'))
    expect(continueButton).toBeTruthy()
    await continueButton!.trigger('click')
    await flushPromises()

    const avatarInput = wrapper.get('input[type="file"]')
    Object.defineProperty(avatarInput.element, 'files', {
      configurable: true,
      value: [new File(['avatar'], 'group.png', { type: 'image/png' })],
    })
    await avatarInput.trigger('change')
    await flushPromises()

    await wrapper.get('#group-title').setValue('Fresh Group')
    await wrapper.get('#group-description').setValue('Created in test')

    const createButton = wrapper.findAll('button').find((button) => button.text().includes('ساخت گروه'))
    expect(createButton).toBeTruthy()
    await createButton!.trigger('click')
    await flushPromises()

    expect(uploadAvatarImageMock).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('created')?.[0]).toEqual([
      {
        id: 19,
        title: 'Fresh Group',
        description: 'Created in test',
        avatar_file_id: 'group-avatar',
        member_count: 2,
        max_members: 50,
        current_user_role: 'admin',
      },
    ])
  })

  it('updates group settings, manages admins, adds members, and removes members for an existing group', async () => {
    let currentGroup = {
      id: 7,
      title: 'Group Seven',
      description: 'Example group',
      avatar_file_id: 'old-group-avatar',
      member_count: 2,
      max_members: 50,
      current_user_role: 'admin' as const,
    }
    let currentMembers = [
      {
        user_id: 1,
        account_name: 'owner1',
        full_name: 'Owner One',
        mobile_number: '09120000001',
        avatar_file_id: null,
        role: 'admin' as const,
        is_group_creator: true,
      },
      {
        user_id: 2,
        account_name: 'member2',
        full_name: 'Member Two',
        mobile_number: '09120000002',
        avatar_file_id: null,
        role: 'member' as const,
        is_group_creator: false,
      },
    ]

    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/chat/groups/7') {
        return {
          group: currentGroup,
          members: currentMembers,
        }
      }
      if (url === '/api/users-public/search?limit=100') {
        return [
          {
            id: 3,
            account_name: 'member3',
            full_name: 'Member Three',
            mobile_number: '09120000003',
            avatar_file_id: null,
          },
        ]
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/chat/groups/7') {
        const body = JSON.parse(String(options?.body || '{}'))
        currentGroup = {
          ...currentGroup,
          title: body.title,
          description: body.description,
          avatar_file_id: body.avatar_file_id,
        }
        return makeResponse(currentGroup)
      }

      if (url === '/api/chat/groups/7/admins/2' && options?.method === 'POST') {
        currentMembers = currentMembers.map((member) =>
          member.user_id === 2 ? { ...member, role: 'admin' as const } : member,
        )
        return makeResponse({})
      }

      if (url === '/api/chat/groups/7/admins/2' && options?.method === 'DELETE') {
        currentMembers = currentMembers.map((member) =>
          member.user_id === 2 ? { ...member, role: 'member' as const } : member,
        )
        return makeResponse({})
      }

      if (url === '/api/chat/groups/7/members' && options?.method === 'POST') {
        const body = JSON.parse(String(options?.body || '{}'))
        expect(body).toEqual({ user_id: 3 })
        currentMembers = [
          ...currentMembers,
          {
            user_id: 3,
            account_name: 'member3',
            full_name: 'Member Three',
            mobile_number: '09120000003',
            avatar_file_id: null,
            role: 'member',
            is_group_creator: false,
          },
        ]
        currentGroup = { ...currentGroup, member_count: 3 }
        return makeResponse({})
      }

      if (url === '/api/chat/groups/7/members/2' && options?.method === 'DELETE') {
        currentMembers = currentMembers.filter((member) => member.user_id !== 2)
        currentGroup = { ...currentGroup, member_count: 2 }
        return makeResponse({})
      }

      throw new Error(`Unhandled apiFetch call: ${url} (${options?.method || 'GET'})`)
    })

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

    const editEntry = wrapper.findAll('.telegram-row').find((row) => row.text().includes('تنظیمات گروه'))
    expect(editEntry).toBeTruthy()
    await editEntry!.trigger('click')
    await flushPromises()

    const clearAvatarButton = wrapper.findAll('button').find((button) => button.text().includes('حذف عکس'))
    expect(clearAvatarButton).toBeTruthy()
    await clearAvatarButton!.trigger('click')
    await wrapper.get('#group-edit-title').setValue('Renamed Group')
    await wrapper.get('#group-edit-description').setValue('Updated group details')

    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('ذخیره تغییرات'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(currentGroup.title).toBe('Renamed Group')
    expect(currentGroup.description).toBe('Updated group details')
    expect(currentGroup.avatar_file_id).toBeNull()
    expect(wrapper.emitted('updated')).toHaveLength(1)

    const adminsEntry = wrapper.findAll('.telegram-row').find((row) => row.text().includes('مدیریت ادمین‌ها'))
    expect(adminsEntry).toBeTruthy()
    await adminsEntry!.trigger('click')
    await flushPromises()

    const promoteButton = wrapper.findAll('.chat-user-row__action-btn').find((button) => button.text().includes('ارتقا به ادمین'))
    expect(promoteButton).toBeTruthy()
    await promoteButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(currentMembers.find((member) => member.user_id === 2)?.role).toBe('admin')
    expect(wrapper.emitted('updated')).toHaveLength(2)

    const demoteButton = wrapper.findAll('.chat-user-row__action-btn').find((button) => button.text().includes('حذف ادمین'))
    expect(demoteButton).toBeTruthy()
    await demoteButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(currentMembers.find((member) => member.user_id === 2)?.role).toBe('member')
    expect(wrapper.emitted('updated')).toHaveLength(3)

    const headerButtons = wrapper.findAll('.manager-header .header-icon-btn')
    expect(headerButtons[0]).toBeTruthy()
    await headerButtons[0]!.trigger('click')
    await flushPromises()

    const addMembersEntry = wrapper.findAll('.telegram-row').find((row) => row.text().includes('افزودن عضو'))
    expect(addMembersEntry).toBeTruthy()
    await addMembersEntry!.trigger('click')
    await flushPromises()
    await flushPromises()

    const newCandidateRow = wrapper.findAll('.chat-user-row').find((row) => row.text().includes('Member Three'))
    expect(newCandidateRow).toBeTruthy()
    await newCandidateRow!.trigger('click')

    const addButton = wrapper.findAll('button').find((button) => button.text().trim() === 'افزودن')
    expect(addButton).toBeTruthy()
    await addButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(currentGroup.member_count).toBe(3)
    expect(wrapper.emitted('updated')).toHaveLength(4)
    expect(wrapper.text()).toContain('Member Three')

    const removeButton = wrapper.findAll('.chat-user-row__action-btn--danger').find((button) => button.text().includes('حذف'))
    expect(removeButton).toBeTruthy()
    await removeButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(currentMembers.some((member) => member.user_id === 2)).toBe(false)
    expect(currentGroup.member_count).toBe(2)
    expect(wrapper.emitted('updated')).toHaveLength(5)
  })
})