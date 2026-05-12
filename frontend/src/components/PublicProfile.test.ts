import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const buildChatFileUrlMock = vi.fn(() => '')
const uploadAvatarImageMock = vi.fn()

vi.mock('../utils/chatFiles', () => ({
  buildChatFileUrl: buildChatFileUrlMock,
  getAvatarInitial: (value: string) => value.slice(0, 1),
  uploadAvatarImage: uploadAvatarImageMock,
}))

function makeResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

describe('PublicProfile.vue', () => {
  beforeEach(() => {
    buildChatFileUrlMock.mockClear()
    uploadAvatarImageMock.mockReset()
    vi.stubGlobal('fetch', vi.fn())
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
})