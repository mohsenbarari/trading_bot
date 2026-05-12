import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const buildChatFileUrlMock = vi.fn(() => '')
const uploadAvatarImageMock = vi.fn()

vi.mock('../utils/chatFiles', () => ({
  buildChatFileUrl: buildChatFileUrlMock,
  getAvatarInitial: (value: string) => value.slice(0, 1),
  uploadAvatarImage: uploadAvatarImageMock,
}))

function makeResponse(payload: unknown, ok = true, status = ok ? 200 : 400): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

describe('PublicProfile.vue', () => {
  beforeEach(() => {
    buildChatFileUrlMock.mockClear()
    uploadAvatarImageMock.mockReset()
    vi.stubGlobal('fetch', vi.fn())
    vi.stubGlobal('alert', vi.fn())
  })

  it('shows owner-resolution context when the public profile resolves from an accountant', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      created_at_jalali: '۱۴۰۵/۰۱/۰۲',
      trades_count: 12,
      resolved_from_accountant_id: 44,
      highlight_accountant_user_id: 44,
      highlight_accountant_relation_display_name: 'حسابدار فروش',
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'acct44' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/users-public/44', {
      headers: {
        Authorization: 'Bearer token',
      },
    })
    expect(wrapper.text()).toContain('نمایش پروفایل مالک اصلی')
    expect(wrapper.text()).toContain('حسابدار فروش')
    expect(wrapper.text()).toContain('owner20')
  })

  it('does not show the owner-resolution banner for direct public profiles', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('نمایش پروفایل مالک اصلی')
  })

  it('clears the owner avatar through the authenticated avatar endpoint', async () => {
    const fetchMock = vi.mocked(fetch)
    buildChatFileUrlMock.mockImplementation((fileId?: string | null) => fileId ? `/files/${fileId}` : '')
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 31,
      account_name: 'owner31',
      avatar_file_id: 'avatar-1',
      mobile_number: '09126666666',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۴',
      trades_count: 2,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ avatar_file_id: null }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 31, account_name: 'owner31' },
        viewerUserId: 31,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const clearAvatarButton = wrapper.findAll('button').find((button) => button.text().includes('حذف عکس'))
    expect(clearAvatarButton).toBeTruthy()
    await clearAvatarButton!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/me/avatar', expect.objectContaining({
      method: 'PUT',
      headers: {
        Authorization: 'Bearer token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ avatar_file_id: null }),
    }))
    expect(wrapper.findAll('button').some((button) => button.text().includes('حذف عکس'))).toBe(false)
  })

  it('exposes owner actions for settings navigation and accountant-manager modal opening', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 44,
      account_name: 'owner44',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'اصفهان',
      created_at_jalali: '۱۴۰۵/۰۱/۰۵',
      trades_count: 18,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'owner44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: {
            template: '<div class="owner-accountant-modal-stub">accountant modal</div>',
          },
        },
      },
    })

    await flushPromises()

    const settingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربری'))
    expect(settingsButton).toBeTruthy()
    await settingsButton!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['settings'])

    const accountantButton = wrapper.findAll('button').find((button) => button.text().includes('حسابداران'))
    expect(accountantButton).toBeTruthy()
    await accountantButton!.trigger('click')

    expect(wrapper.find('.owner-accountant-modal-stub').exists()).toBe(true)
  })
})