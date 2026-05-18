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
    localStorage.clear()
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
      accountant_relations: [
        {
          accountant_user_id: 44,
          accountant_account_name: 'acct44',
          relation_display_name: 'حسابدار فروش',
          duty_description: 'پیگیری معاملات',
        },
      ],
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
      accountant_relations: [],
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

  it('shows an invalid-user error when required props are missing and emits navigate on retry', async () => {
    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: null,
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: null,
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('اطلاعات کاربر نامعتبر است.')
    await wrapper.get('.retry-btn').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['home'])
  })

  it('shows the visitor action and navigates to chat for direct public profiles', async () => {
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
      accountant_relations: [],
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

    const messageButton = wrapper.findAll('button').find((button) => button.text().includes('ارسال پیام'))
    expect(messageButton).toBeTruthy()

    const infoHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('اطلاعات شخصی و آمار'))
    expect(infoHeader).toBeTruthy()
    await infoHeader!.trigger('click')
    await infoHeader!.trigger('click')

    await messageButton!.trigger('click')

    expect(wrapper.emitted('navigate')?.[0]).toEqual(['chat', { userId: 30, userName: 'plain30' }])
  })

  it('shows the admin settings action for admin viewers on other profiles', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 61,
      account_name: 'managed61',
      avatar_file_id: null,
      mobile_number: '09121110000',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۲/۰۱',
      trades_count: 1,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 61, account_name: 'managed61' },
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

    const adminSettingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربر'))
    expect(adminSettingsButton).toBeTruthy()
    await adminSettingsButton!.trigger('click')

    expect(wrapper.emitted('navigate')?.[0]).toEqual(['settings', { userId: 61, userName: 'managed61' }])
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
      accountant_relations: [],
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

  it('opens the avatar picker and uploads a new owner avatar', async () => {
    const fetchMock = vi.mocked(fetch)
    const inputClickSpy = vi.spyOn(HTMLInputElement.prototype, 'click')
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 45,
      account_name: 'owner45',
      avatar_file_id: null,
      mobile_number: '09128888888',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۶',
      trades_count: 7,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    uploadAvatarImageMock.mockResolvedValue({ file_id: 'avatar-99' })
    fetchMock.mockResolvedValueOnce(makeResponse({ avatar_file_id: 'avatar-99' }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 45, account_name: 'owner45' },
        viewerUserId: 45,
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

    await wrapper.get('.profile-avatar-btn.primary').trigger('click')
    expect(inputClickSpy).toHaveBeenCalled()

    const input = wrapper.get('.hidden-avatar-input')
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })

    await input.trigger('change')
    await flushPromises()

    expect(uploadAvatarImageMock).toHaveBeenCalledWith(file, '')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/me/avatar', expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({ avatar_file_id: 'avatar-99' }),
    }))

    inputClickSpy.mockRestore()
  })

  it('surfaces avatar update errors from both upload and clear flows', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 46,
      account_name: 'owner46',
      avatar_file_id: 'avatar-46',
      mobile_number: '09129999999',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۱/۰۷',
      trades_count: 9,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ detail: 'حذف عکس ممکن نشد' }, false))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 46, account_name: 'owner46' },
        viewerUserId: 46,
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
    await clearAvatarButton!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('حذف عکس ممکن نشد')

    uploadAvatarImageMock.mockRejectedValueOnce(new Error('آپلود ناموفق بود'))
    const input = wrapper.get('.hidden-avatar-input')
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })

    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('آپلود ناموفق بود')
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
      accountant_relations: [],
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
            template: '<button class="owner-accountant-modal-stub" @click="$emit(\'close\')">accountant modal</button>',
          },
        },
      },
    })

    await flushPromises()

    const settingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربری'))
    expect(settingsButton).toBeTruthy()
    await settingsButton!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['settings'])

    const customerButton = wrapper.findAll('button').find((button) => button.text().includes('مشتریان'))
    expect(customerButton).toBeTruthy()
    await customerButton!.trigger('click')
    expect(vi.mocked(alert)).toHaveBeenCalledWith('قابلیت افزودن مشتری به زودی اضافه خواهد شد.')

    const accountantButton = wrapper.findAll('button').find((button) => button.text().includes('حسابداران'))
    expect(accountantButton).toBeTruthy()
    await accountantButton!.trigger('click')

    expect(wrapper.find('.owner-accountant-modal-stub').exists()).toBe(true)
    await wrapper.get('.owner-accountant-modal-stub').trigger('click')
    expect(wrapper.find('.owner-accountant-modal-stub').exists()).toBe(false)
  })

  it('loads mutual trade history for direct profiles and reuses loaded results on reopen', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 50,
      account_name: 'owner50',
      avatar_file_id: null,
      mobile_number: '09121112222',
      address: 'قم',
      created_at_jalali: '۱۴۰۵/۰۱/۱۰',
      trades_count: 3,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 1,
        trade_number: 10001,
        created_at: 'امروز',
        commodity_name: 'سکه',
        quantity: 2,
        price: 123000,
        trade_type: 'BUY',
        offer_user_name: 'مالک',
        responder_user_name: 'بیننده',
        responder_user_id: 99,
      },
      {
        id: 2,
        trade_number: 10002,
        created_at: 'دیروز',
        commodity_name: 'طلا',
        quantity: 1,
        price: 456000,
        trade_type: 'BUY',
        offer_user_name: 'فروشنده',
        responder_user_name: 'مالک',
        responder_user_id: 50,
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
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

    const historyHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('تاریخچه معاملات مشترک'))
    expect(historyHeader).toBeTruthy()
    await historyHeader!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/trades/with/50', expect.objectContaining({
      headers: {
        Authorization: 'Bearer token',
      },
    }))
    expect(wrapper.text()).toContain('🟢 خرید')
    expect(wrapper.text()).toContain('🔴 فروش')
    expect(wrapper.text()).toContain('بیننده')
    expect(wrapper.text()).toContain('فروشنده')

    await historyHeader!.trigger('click')
    await historyHeader!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('loads own trade history from the self endpoint and shows the empty state', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 51,
      account_name: 'owner51',
      avatar_file_id: null,
      mobile_number: '09123334444',
      address: 'کرج',
      created_at_jalali: '۱۴۰۵/۰۱/۱۱',
      trades_count: 0,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 51, account_name: 'owner51' },
        viewerUserId: 51,
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

    const historyHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('تاریخچه معاملات من'))
    expect(historyHeader).toBeTruthy()
    await historyHeader!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/trades/my', expect.objectContaining({
      headers: {
        Authorization: 'Bearer token',
      },
    }))
    expect(wrapper.text()).toContain('هنوز هیچ معامله‌ای انجام نداده‌اید.')
  })

  it('shows network fetch errors and still allows returning home from the back button', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockRejectedValueOnce(new Error('دریافت پروفایل ناموفق بود'))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 52, account_name: 'owner52' },
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

    expect(wrapper.text()).toContain('دریافت پروفایل ناموفق بود')
    await wrapper.get('.back-button').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['home'])
  })

  it('opens and highlights the accountant section when the owner profile is reached through route query state', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      created_at_jalali: '۱۴۰۵/۰۱/۰۲',
      trades_count: 12,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [
        {
          accountant_user_id: 44,
          accountant_account_name: 'acct44',
          relation_display_name: 'حسابدار فروش',
          duty_description: 'پیگیری معاملات',
        },
        {
          accountant_user_id: 45,
          accountant_account_name: 'acct45',
          relation_display_name: 'حسابدار دوم',
          duty_description: null,
        },
      ],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 20, account_name: 'owner20' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
        highlightAccountantUserId: 44,
        highlightAccountantRelationDisplayName: 'حسابدار فروش',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('نمایش پروفایل مالک اصلی')
    expect(wrapper.text()).toContain('حسابداران این مالک')
    expect(wrapper.find('.public-accountant-card.highlighted').text()).toContain('acct44')
    expect(wrapper.find('.public-accountant-card.highlighted').text()).toContain('مسیر فعلی')

    const accountantHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('حسابداران این مالک'))
    expect(accountantHeader).toBeTruthy()
    const accountantAccordion = wrapper.findAll('.ds-accordion').find((node) => node.text().includes('حسابداران این مالک'))
    expect(accountantAccordion).toBeTruthy()
    await accountantHeader!.trigger('click')
    expect(accountantAccordion!.classes()).not.toContain('open')
    await accountantHeader!.trigger('click')
    expect(accountantAccordion!.classes()).toContain('open')
  })
})