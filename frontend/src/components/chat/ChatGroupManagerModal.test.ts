import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { invalidateChatManagerCache } from '../../services/chat/chatManagerCache'

const GROUP_CANDIDATE_BASE_URL = '/api/chat/groups/member-candidates?limit=100'

const apiFetchMock = vi.fn()
const apiFetchJsonMock = vi.fn()
const pushBackStateMock = vi.fn()
const popBackStateMock = vi.fn()
const buildChatFileUrlMock = vi.fn(() => '')
const uploadAvatarImageMock = vi.fn()
const currentUserSummaryMock = ref({
  id: 1,
  role: 'عادی',
  account_name: 'owner1',
  is_accountant: false,
  accountant_owner_account_name: null,
})
const primeCurrentUserSummaryMock = vi.fn(async () => currentUserSummaryMock.value)

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

vi.mock('../../utils/currentUser', () => ({
  currentUserSummary: currentUserSummaryMock,
  primeCurrentUserSummary: primeCurrentUserSummaryMock,
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
    invalidateChatManagerCache()
    apiFetchMock.mockReset()
    apiFetchJsonMock.mockReset()
    pushBackStateMock.mockReset()
    popBackStateMock.mockReset()
    buildChatFileUrlMock.mockClear()
    uploadAvatarImageMock.mockReset()
    currentUserSummaryMock.value = {
      id: 1,
      role: 'عادی',
      account_name: 'owner1',
      is_accountant: false,
      accountant_owner_account_name: null,
    }
    primeCurrentUserSummaryMock.mockClear()
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
      if (url === GROUP_CANDIDATE_BASE_URL || url === `${GROUP_CANDIDATE_BASE_URL}&selected_user_ids=2`) {
        return {
          items: [
            {
              user_id: 1,
              account_name: 'owner1',
              full_name: 'Owner One',
              mobile_number: '09120000001',
              avatar_file_id: null,
            },
            {
              user_id: 2,
              account_name: 'member2',
              full_name: 'Member Two',
              mobile_number: '09120000002',
              avatar_file_id: null,
            },
          ],
        }
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/chat/groups') {
        const body = JSON.parse(String(options?.body || '{}'))
        expect(body).toMatchObject({
          title: 'Fresh Group',
          avatar_file_id: 'group-avatar',
          member_ids: [2],
        })
        expect(body).not.toHaveProperty('description')
        return makeResponse({
          group: {
            id: 19,
            title: 'Fresh Group',
            description: null,
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
    expect(wrapper.find('#group-description').exists()).toBe(false)

    const createButton = wrapper.findAll('button').find((button) => button.text().includes('ساخت گروه'))
    expect(createButton).toBeTruthy()
    await createButton!.trigger('click')
    await flushPromises()

    expect(uploadAvatarImageMock).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('created')?.[0]).toEqual([
      {
        id: 19,
        title: 'Fresh Group',
        description: null,
        avatar_file_id: 'group-avatar',
        member_count: 2,
        max_members: 50,
        current_user_role: 'admin',
      },
    ])
  })

  it('suggests an accounting group title when selected members belong to exactly two accounting groups', async () => {
    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === GROUP_CANDIDATE_BASE_URL || url === `${GROUP_CANDIDATE_BASE_URL}&selected_user_ids=2`) {
        return {
          items: [
            {
              user_id: 2,
              account_name: 'accountant2',
              full_name: 'حسابدار دو',
              mobile_number: '09120000002',
              avatar_file_id: null,
              chat_role_kind: 'accountant',
              chat_role_label: 'حسابدار',
              chat_accountant_owner_name: 'owner2',
            },
          ],
        }
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

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

    const candidateRow = wrapper.findAll('.chat-user-row').find((row) => row.text().includes('حسابدار دو'))
    expect(candidateRow).toBeTruthy()
    await candidateRow!.trigger('click')
    await wrapper.findAll('button').find((button) => button.text().includes('ادامه'))!.trigger('click')
    await flushPromises()

    expect((wrapper.get('#group-title').element as HTMLInputElement).value).toBe('حسابداری owner1-owner2')
    await wrapper.get('#group-title').setValue('نام دستی')
    expect((wrapper.get('#group-title').element as HTMLInputElement).value).toBe('نام دستی')
  })

  it('persists an existing group avatar immediately from the overview', async () => {
    let currentGroup = {
      id: 7,
      title: 'Group Seven',
      description: 'Example group',
      avatar_file_id: null as string | null,
      member_count: 2,
      max_members: 50,
      current_user_role: 'admin' as const,
    }

    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/chat/groups/7') {
        return {
          group: currentGroup,
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
          ],
        }
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/chat/groups/7' && options?.method === 'PATCH') {
        const body = JSON.parse(String(options?.body || '{}'))
        expect(body).toEqual({
          title: 'Group Seven',
          avatar_file_id: 'group-avatar-new',
        })
        expect(body).not.toHaveProperty('description')
        currentGroup = { ...currentGroup, avatar_file_id: body.avatar_file_id }
        return makeResponse(currentGroup)
      }
      throw new Error(`Unhandled apiFetch call: ${url}`)
    })

    uploadAvatarImageMock.mockResolvedValue({ file_id: 'group-avatar-new' })

    const ChatGroupManagerModal = (await import('./ChatGroupManagerModal.vue')).default
    const wrapper = mount(ChatGroupManagerModal, {
      props: {
        show: true,
        groupId: 7,
        currentUserId: 1,
        apiBaseUrl: 'https://coin.test',
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

    const avatarInput = wrapper.get('input[type="file"]')
    const file = new File(['avatar'], 'group.png', { type: 'image/png' })
    Object.defineProperty(avatarInput.element, 'files', {
      configurable: true,
      value: [file],
    })
    await avatarInput.trigger('change')
    await flushPromises()
    await flushPromises()

    expect(uploadAvatarImageMock).toHaveBeenCalledWith(file, 'https://coin.test')
    expect(currentGroup.avatar_file_id).toBe('group-avatar-new')
    expect(wrapper.emitted('updated')?.[0]).toEqual([currentGroup])
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
      if (
        url === `${GROUP_CANDIDATE_BASE_URL}&exclude_chat_id=7`
        || url === `${GROUP_CANDIDATE_BASE_URL}&exclude_chat_id=7&selected_user_ids=3`
      ) {
        return {
          items: [
            {
              user_id: 3,
              account_name: 'member3',
              full_name: 'Member Three',
              mobile_number: '09120000003',
              avatar_file_id: null,
            },
          ],
        }
      }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/chat/groups/7') {
        const body = JSON.parse(String(options?.body || '{}'))
        currentGroup = {
          ...currentGroup,
          title: body.title,
          description: body.description ?? currentGroup.description,
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
    expect(wrapper.find('#group-edit-description').exists()).toBe(false)

    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('ذخیره تغییرات'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(currentGroup.title).toBe('Renamed Group')
    expect(currentGroup.description).toBe('Example group')
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

    const memberTwoRow = wrapper.findAll('.chat-user-row').find((row) => row.text().includes('Member Two'))
    expect(memberTwoRow).toBeTruthy()
    const removeButton = memberTwoRow!.findAll('.chat-user-row__action-btn--danger').find((button) => button.text().includes('حذف'))
    expect(removeButton).toBeTruthy()
    await removeButton!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(currentMembers.some((member) => member.user_id === 2)).toBe(false)
    expect(currentGroup.member_count).toBe(2)
    expect(wrapper.emitted('updated')).toHaveLength(5)
  })

  it('covers filtering, page labels, guard helpers, avatar guards, and manager back-state callbacks', async () => {
    const groupDetail = {
      group: {
        id: 7,
        title: 'Group Seven',
        description: 'Example group',
        avatar_file_id: 'avatar-7',
        member_count: 3,
        max_members: 50,
        current_user_role: 'admin' as const,
      },
      members: [
        {
          user_id: 2,
          account_name: 'owner2',
          full_name: 'Owner Two',
          mobile_number: '09120000002',
          avatar_file_id: null,
          role: 'admin' as const,
          is_group_creator: true,
        },
        {
          user_id: 3,
          account_name: 'admin3',
          full_name: 'Admin Three',
          mobile_number: '09120000003',
          avatar_file_id: null,
          role: 'admin' as const,
          is_group_creator: false,
        },
        {
          user_id: 4,
          account_name: 'member4',
          full_name: 'Member Four',
          mobile_number: '09120000004',
          avatar_file_id: null,
          role: 'member' as const,
          is_group_creator: false,
        },
      ],
    }

    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url.startsWith(GROUP_CANDIDATE_BASE_URL)) return { items: [] }
      if (url === '/api/chat/groups/7') return groupDetail
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    const ChatGroupManagerModal = (await import('./ChatGroupManagerModal.vue')).default

    const createWrapper = mount(ChatGroupManagerModal, {
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

    const createVm = createWrapper.vm as unknown as {
      page: 'select-members' | 'details'
      pageTitle: string
      pageSubtitle: string
      canGoBack: boolean
      selectedUserIds: Set<number>
    }

    expect(createVm.pageTitle).toBe('افزودن اعضا')
    expect(createVm.pageSubtitle).toContain('اعضایی را که می‌خواهید در گروه باشند انتخاب کنید.')
    expect(createVm.canGoBack).toBe(false)

    createVm.selectedUserIds = new Set([4])
    createVm.page = 'details'
    await nextTick()

    expect(createVm.pageTitle).toBe('اطلاعات گروه')
    expect(createVm.pageSubtitle).toContain('۱ عضو انتخاب شده')
    expect(createVm.canGoBack).toBe(true)

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

    const avatarClick = vi.fn()
    const wrapperVm = wrapper.vm as unknown as {
      page: 'overview' | 'edit' | 'members' | 'admins' | 'add-members'
      pageTitle: string
      pageSubtitle: string
      canGoBack: boolean
      members: Array<{
        user_id: number
        account_name: string
        full_name: string
        mobile_number: string
        role: 'admin' | 'member'
        is_group_creator: boolean
      }>
      memberQuery: string
      adminQuery: string
      filteredMembers: Array<{ user_id: number }>
      filteredAdmins: Array<{ user_id: number }>
      promotableMembers: Array<{ user_id: number }>
      getMemberGuardReason: (member: {
        user_id: number
        account_name: string
        full_name: string
        mobile_number: string
        role: 'admin' | 'member'
        is_group_creator: boolean
      }) => string
      canDemote: (member: {
        user_id: number
        account_name: string
        full_name: string
        mobile_number: string
        role: 'admin' | 'member'
        is_group_creator: boolean
      }) => boolean
      canRemove: (member: {
        user_id: number
        account_name: string
        full_name: string
        mobile_number: string
        role: 'admin' | 'member'
        is_group_creator: boolean
      }) => boolean
      avatarBusy: boolean
      avatarInput: { click: () => void } | null
      triggerAvatarPicker: () => void
      avatarFileId: string | null
      clearAvatar: () => void
      pageHistory: string[]
      managerBackStateActive: boolean
      pushManagerBackState: () => void
      handleBack: (fromBack?: boolean) => boolean
      requestClose: (fromBack?: boolean) => void
    }

    expect(wrapperVm.pageTitle).toBe('مدیریت گروه')
    expect(wrapperVm.pageSubtitle).toContain('۳ عضو')
    expect(wrapperVm.canGoBack).toBe(false)
    expect(wrapper.text()).toContain('نقش شما')
    expect(wrapper.text()).toContain('ادمین گروه')
    expect(wrapper.text()).toContain('اعضا و دسترسی‌ها')
    expect(wrapper.text()).toContain('خروج')

    wrapperVm.page = 'edit'
    await nextTick()
    expect(wrapperVm.pageTitle).toBe('ویرایش اطلاعات گروه')
    expect(wrapperVm.pageSubtitle).toContain('نام گروه را از همین صفحه مدیریت کنید.')
    expect(wrapperVm.canGoBack).toBe(true)

    wrapperVm.page = 'members'
    wrapperVm.memberQuery = 'four'
    await nextTick()
    expect(wrapperVm.filteredMembers.map((member) => member.user_id)).toEqual([4])

    wrapperVm.adminQuery = 'admin three'
    await nextTick()
    expect(wrapperVm.filteredAdmins.map((member) => member.user_id)).toEqual([3])

    wrapperVm.adminQuery = 'member four'
    await nextTick()
    expect(wrapperVm.promotableMembers.map((member) => member.user_id)).toEqual([4])

    const creator = wrapperVm.members[0]!
    const otherAdmin = wrapperVm.members[1]!
    const regularMember = wrapperVm.members[2]!
    expect(wrapperVm.getMemberGuardReason(creator)).toContain('سازنده گروه')
    expect(wrapperVm.canDemote(creator)).toBe(false)
    expect(wrapperVm.canRemove(creator)).toBe(false)

    const selfMember = {
      user_id: 1,
      account_name: 'self1',
      full_name: 'Self One',
      mobile_number: '09120000001',
      role: 'member' as const,
      is_group_creator: false,
    }
    expect(wrapperVm.getMemberGuardReason(selfMember)).toContain('برای خروج از گروه')
    expect(wrapperVm.canRemove(selfMember)).toBe(false)

    wrapperVm.members = [otherAdmin]
    await nextTick()
    expect(wrapperVm.getMemberGuardReason(otherAdmin)).toContain('حداقل یک ادمین فعال')
    expect(wrapperVm.canDemote(otherAdmin)).toBe(false)
    expect(wrapperVm.canRemove(otherAdmin)).toBe(false)

    wrapperVm.members = [creator, otherAdmin, regularMember]
    await nextTick()
    expect(wrapperVm.canRemove(regularMember)).toBe(true)

    wrapperVm.avatarInput = { click: avatarClick }
    wrapperVm.triggerAvatarPicker()
    expect(avatarClick).toHaveBeenCalledTimes(1)
    wrapperVm.avatarBusy = true
    wrapperVm.triggerAvatarPicker()
    expect(avatarClick).toHaveBeenCalledTimes(1)

    wrapperVm.avatarFileId = 'avatar-7'
    wrapperVm.clearAvatar()
    expect(wrapperVm.avatarFileId).toBe('avatar-7')
    wrapperVm.avatarBusy = false
    wrapperVm.clearAvatar()
    expect(wrapperVm.avatarFileId).toBeNull()

    wrapperVm.page = 'edit'
    wrapperVm.pageHistory = ['overview']
    const pushCallsBefore = pushBackStateMock.mock.calls.length
    wrapperVm.pushManagerBackState()
    expect(pushBackStateMock.mock.calls.length).toBeGreaterThanOrEqual(pushCallsBefore)

    const backHandler = pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(backHandler).toBeTypeOf('function')
    backHandler?.()
    await nextTick()

    expect(wrapperVm.page).toBe('overview')
    expect(pushBackStateMock.mock.calls.length).toBeGreaterThan(pushCallsBefore)

    wrapperVm.managerBackStateActive = true
    wrapperVm.requestClose()
    expect(popBackStateMock).toHaveBeenCalled()
    expect(wrapper.emitted('close')).toBeTruthy()

    wrapperVm.pageHistory = []
    expect(wrapperVm.handleBack(true)).toBe(false)
  })

  it('surfaces fallback errors for avatar upload, loading, creation, mutations, and leave failures', async () => {
    const groupDetail = {
      group: {
        id: 7,
        title: 'Group Seven',
        description: 'Example group',
        avatar_file_id: null,
        member_count: 2,
        max_members: 50,
        current_user_role: 'admin' as const,
      },
      members: [
        {
          user_id: 2,
          account_name: 'member2',
          full_name: 'Member Two',
          mobile_number: '09120000002',
          avatar_file_id: null,
          role: 'member' as const,
          is_group_creator: false,
        },
      ],
    }

    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url.startsWith(GROUP_CANDIDATE_BASE_URL)) return { items: [] }
      if (url === '/api/chat/groups/7') return groupDetail
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    const ChatGroupManagerModal = (await import('./ChatGroupManagerModal.vue')).default

    const createWrapper = mount(ChatGroupManagerModal, {
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

    const createVm = createWrapper.vm as unknown as {
      errorMessage: string
      title: string
      selectedUserIds: Set<number>
      handleAvatarSelected: (event: Event) => Promise<void>
      loadUsers: (query?: string) => Promise<void>
      createGroup: () => Promise<void>
    }

    uploadAvatarImageMock.mockRejectedValueOnce(new Error('avatar failed'))
    const avatarInput = {
      files: [new File(['avatar'], 'avatar.png', { type: 'image/png' })],
      value: 'picked',
    }
    await createVm.handleAvatarSelected({ target: avatarInput } as unknown as Event)
    expect(createVm.errorMessage).toBe('avatar failed')
    expect(avatarInput.value).toBe('')

    apiFetchJsonMock.mockRejectedValueOnce('users unavailable')
    await createVm.loadUsers('ali')
    expect(createVm.errorMessage).toBe('خطا در دریافت کاربران')

    createVm.title = 'Broken Group'
    createVm.selectedUserIds = new Set([2])
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'duplicate title' }, false))
    await createVm.createGroup()
    expect(createVm.errorMessage).toBe('duplicate title')

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

    const vm = wrapper.vm as unknown as {
      errorMessage: string
      title: string
      description: string
      selectedUserIds: Set<number>
      loadGroupDetail: () => Promise<void>
      updateGroupSettings: () => Promise<void>
      addSelectedMembers: () => Promise<void>
      mutateMember: (member: {
        user_id: number
        account_name: string
        full_name: string
        mobile_number: string
        role: 'admin' | 'member'
        is_group_creator: boolean
      }, endpoint: string, method: string, successText: string) => Promise<void>
      leaveGroup: () => Promise<void>
      mutatingUserId: number | null
    }

    invalidateChatManagerCache()
    apiFetchJsonMock.mockRejectedValueOnce('detail unavailable')
    await vm.loadGroupDetail()
    expect(vm.errorMessage).toBe('خطا در دریافت گروه')

    vm.title = 'Updated Group'
    vm.description = 'Updated details'
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'update blocked' }, false))
    await vm.updateGroupSettings()
    expect(vm.errorMessage).toBe('update blocked')

    vm.selectedUserIds = new Set([2])
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'add blocked' }, false))
    await vm.addSelectedMembers()
    expect(vm.errorMessage).toBe('add blocked')

    const member = {
      user_id: 2,
      account_name: 'member2',
      full_name: 'Member Two',
      mobile_number: '09120000002',
      role: 'member' as const,
      is_group_creator: false,
    }
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'mutation blocked' }, false))
    await vm.mutateMember(member, '/api/chat/groups/7/members/2', 'DELETE', 'ok')
    expect(vm.errorMessage).toBe('mutation blocked')
    expect(vm.mutatingUserId).toBeNull()

    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'leave blocked' }, false))
    await vm.leaveGroup()
    expect(vm.errorMessage).toBe('leave blocked')
  })

  it('reloads search candidates only on searchable pages and resets state when the manager closes', async () => {
    vi.useFakeTimers()

    const groupDetail = {
      group: {
        id: 7,
        title: 'Group Seven',
        description: 'Example group',
        avatar_file_id: null,
        member_count: 2,
        max_members: 50,
        current_user_role: 'admin' as const,
      },
      members: [],
    }

    apiFetchJsonMock.mockImplementation(async (url: string) => {
      if (url === '/api/chat/groups/7') return groupDetail
      if (url.startsWith(GROUP_CANDIDATE_BASE_URL)) return { items: [] }
      throw new Error(`Unhandled apiFetchJson call: ${url}`)
    })

    const ChatGroupManagerModal = (await import('./ChatGroupManagerModal.vue')).default
    const wrapper = mount(ChatGroupManagerModal, {
      props: {
        show: false,
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

    const vm = wrapper.vm as unknown as {
      page: 'overview' | 'add-members'
      directoryQuery: string
      title: string
      description: string
      selectedUserIds: Set<number>
      managerBackStateActive: boolean
    }

    expect(pushBackStateMock).not.toHaveBeenCalled()

    await wrapper.setProps({ show: true })
    await flushPromises()
    await flushPromises()

    expect(pushBackStateMock).toHaveBeenCalled()
    expect(apiFetchJsonMock).toHaveBeenCalledWith('/api/chat/groups/7')

    apiFetchJsonMock.mockClear()
    vm.page = 'overview'
    vm.directoryQuery = 'ignored'
    await nextTick()
    await vi.advanceTimersByTimeAsync(220)
    expect(apiFetchJsonMock).not.toHaveBeenCalled()

    vm.page = 'add-members'
    vm.directoryQuery = 'member search'
    await nextTick()
    await vi.advanceTimersByTimeAsync(220)

    expect(apiFetchJsonMock).toHaveBeenCalledWith(`${GROUP_CANDIDATE_BASE_URL}&exclude_chat_id=7&q=member+search`)

    vm.title = 'Dirty title'
    vm.description = 'Dirty description'
    vm.selectedUserIds = new Set([3])
    vm.managerBackStateActive = true
    await wrapper.setProps({ show: false })
    await flushPromises()

    expect(popBackStateMock).toHaveBeenCalled()
    expect(vm.page).toBe('overview')
    expect(vm.directoryQuery).toBe('')
    expect(vm.title).toBe('')
    expect(vm.description).toBe('')
    expect(vm.selectedUserIds.size).toBe(0)

    vi.useRealTimers()
  })
})
