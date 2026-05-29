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

function defaultFetchResponse(input: string): Promise<Response> {
  if (input.endsWith('/api/commodities/')) {
    return Promise.resolve(makeResponse([]))
  }

  if (input.endsWith('/api/blocks/status')) {
    return Promise.resolve(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
  }

  if (/\/api\/blocks\/check\/\d+$/.test(input)) {
    return Promise.resolve(makeResponse({ is_blocked_by_me: false }))
  }

  return Promise.reject(new Error(`Unhandled fetch call in PublicProfile.test.ts: ${input}`))
}

describe('PublicProfile.vue', () => {
  beforeEach(() => {
    buildChatFileUrlMock.mockClear()
    uploadAvatarImageMock.mockReset()
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
      return defaultFetchResponse(url)
    }))
    vi.stubGlobal('alert', vi.fn())
    vi.stubGlobal('confirm', vi.fn(() => true))
    localStorage.clear()
    localStorage.setItem('auth_token', 'token')
  })

  it('shows owner-resolution context when the public profile resolves from an accountant', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      last_seen_at: new Date(Date.now() - 60_000).toISOString(),
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

    expect(fetchMock).toHaveBeenCalledWith('/api/users-public/44', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.text()).toContain('نمایش پروفایل مالک اصلی')
    expect(wrapper.text()).toContain('حسابدار فروش')
    expect(wrapper.text()).toContain('owner20')
    expect(wrapper.text()).toContain('آنلاین')
  })
  
  it('applies preset history ranges and renders partial filter summaries for one-sided dates', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-28T12:00:00Z'))

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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))

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

    const presetButton = wrapper.findAll('.history-chip').find((node) => node.text().includes('۳ ماه'))
    expect(presetButton).toBeTruthy()
    await presetButton!.trigger('click')
    await flushPromises()

    const presetFetchCalls = fetchMock.mock.calls.filter(([url]) => typeof url === 'string' && url.startsWith('/api/trades/with/50?'))
    expect(presetFetchCalls.length).toBeGreaterThanOrEqual(1)
    expect(presetFetchCalls.at(-1)?.[0]).toContain('from_date=2026-02-28')
    expect(presetFetchCalls.at(-1)?.[0]).toContain('to_date=2026-05-28')

    const dateInputs = wrapper.findAll('input[type="date"]')
    await dateInputs[0]!.setValue('')
    await dateInputs[1]!.setValue('2026-05-20')
    await wrapper.get('input[list="public-profile-history-commodities"]').setValue('سکه')
    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButton).toBeTruthy()
    await applyButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('تا')
    expect(wrapper.text()).toContain('کالا: سکه')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('falls back to generic api error text for malformed block and export responses', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({ can_block: true, can_block_now: true, max_blocked: 3, current_blocked: 0, remaining: 3, reason_code: null, reason_message: null }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(new Response('bad gateway', { status: 400, headers: { 'Content-Type': 'text/plain' } }))

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
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    expect(blockButton).toBeTruthy()
    await blockButton!.trigger('click')
    await flushPromises()
    expect(vi.mocked(window.alert)).toHaveBeenCalledWith('بلاک کاربر ناموفق بود.')

    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(new Response('server exploded', { status: 400, headers: { 'Content-Type': 'text/plain' } }))

    const historyHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('تاریخچه معاملات مشترک'))
    expect(historyHeader).toBeTruthy()
    await historyHeader!.trigger('click')
    await flushPromises()

    const pdfButton = wrapper.findAll('button').find((node) => node.text().includes('خروجی PDF'))
    expect(pdfButton).toBeTruthy()
    await pdfButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در دریافت خروجی تاریخچه معاملات')
  })

  it('falls back to plain counterparty labels when history profile targets are missing', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 11,
        trade_number: 10011,
        created_at: 'امروز',
        commodity_name: 'سکه',
        quantity: 1,
        price: 111000,
        trade_type: 'SELL',
        offer_user_id: null,
        offer_user_name: null,
        responder_user_id: null,
        responder_user_name: null,
        counterparty_name: 'خریدار بیرونی',
        counterparty_profile_user_id: 'bad-id',
        counterparty_profile_account_name: '',
        customer_context_visible: false,
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

    expect(wrapper.text()).toContain('خریدار بیرونی')
    expect(wrapper.find('.mini-trade-card .trade-counterparty .profile-link-btn').exists()).toBe(false)
  })
  it('shows formatted last-seen text in the profile hero when a timestamp exists', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-22T12:00:00Z'))
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      last_seen_at: '2026-05-22T11:55:00Z',
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

    expect(wrapper.text()).toContain('آخرین بازدید 5 دقیقه پیش')
  })

  it('keeps the profile hero silent when no last-seen timestamp exists', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      last_seen_at: null,
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

    expect(wrapper.find('.profile-presence-status').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('آخرین بازدید')
    expect(wrapper.text()).not.toContain('آنلاین')
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

  it('shows a block toggle next to the message action for non-customer profiles', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 3,
      current_blocked: 1,
      remaining: 2,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse({ success: true, message: 'کاربر با موفقیت بلاک شد.' }))

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
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    expect(blockButton).toBeTruthy()

    await blockButton!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/blocks/status', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/blocks/check/30', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/blocks/30', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(vi.mocked(window.confirm)).toHaveBeenCalledWith('آیا از بلاک کاربر plain30 اطمینان دارید؟')
    expect(vi.mocked(window.alert)).toHaveBeenCalledWith('کاربر با موفقیت بلاک شد.')
    expect(wrapper.findAll('button').some((button) => button.text().includes('رفع بلاک'))).toBe(true)
  })

  it('disables the block action with a capability-aware reason when new blocks are not allowed', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: false,
      max_blocked: 1,
      current_blocked: 1,
      remaining: 0,
      reason_code: 'limit_reached',
      reason_message: 'ظرفیت بلاک شما تکمیل است. حداکثر 1 کاربر را می‌توانید بلاک کنید.',
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))

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
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    expect(blockButton).toBeTruthy()
    expect(blockButton!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('ظرفیت بلاک شما تکمیل است')
    await wrapper.get('[data-test="public-profile-action-help-block_toggle"]').trigger('click')
    expect(wrapper.text()).toContain('ظرفیت بلاک شما تکمیل است')
    await blockButton!.trigger('click')
    expect(vi.mocked(window.confirm)).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('keeps unblock available even when new blocks are globally disabled for the viewer', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: false,
      can_block_now: false,
      max_blocked: 3,
      current_blocked: 1,
      remaining: 0,
      reason_code: 'capability_disabled',
      reason_message: 'قابلیت بلاک برای شما غیرفعال است.',
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: true }))
    fetchMock.mockResolvedValueOnce(makeResponse({ success: true, message: 'رفع بلاک انجام شد.' }))

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
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    const unblockButton = wrapper.findAll('button').find((button) => button.text().includes('رفع بلاک'))
    expect(unblockButton).toBeTruthy()
    expect(unblockButton!.attributes('disabled')).toBeUndefined()

    await unblockButton!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/blocks/30', expect.objectContaining({
      method: 'DELETE',
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(vi.mocked(window.confirm)).toHaveBeenCalledWith('آیا از رفع بلاک کاربر plain30 اطمینان دارید؟')
    expect(vi.mocked(window.alert)).toHaveBeenCalledWith('رفع بلاک انجام شد.')
  })

  it('loads the project users directory for self profiles and navigates through result rows', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 44,
        account_name: 'owner44',
        mobile_number: '09127777777',
      },
      {
        id: 61,
        account_name: 'manager61',
        mobile_number: '09121110000',
      },
    ]))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 61,
        account_name: 'manager61',
        mobile_number: '09121110000',
      },
    ]))

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
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const directoryHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('کاربران پروژه'))
    expect(directoryHeader).toBeTruthy()
    await directoryHeader!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/users-public/44/project-users?limit=25', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.text()).toContain('manager61')
    expect(wrapper.text()).toContain('09121110000')

    await wrapper.get('.project-user-link-btn').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual([
      'public_profile',
      {
        id: 44,
        account_name: 'owner44',
      },
    ])

    await wrapper.get('.project-users-search-input').setValue('manager')
    await wrapper.get('.project-users-search-submit').trigger('submit')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/users-public/44/project-users?limit=25&q=manager', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.text()).toContain('manager61')
  })

  it('shows the project users directory on accountant self profiles resolved to the owner', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 20,
        account_name: 'owner20',
        mobile_number: '09124444444',
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'acct44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const directoryHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('کاربران پروژه'))
    expect(directoryHeader).toBeTruthy()
    await directoryHeader!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/users-public/20/project-users?limit=25', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.text()).toContain('owner20')
  })

  it('shows project-users fetch errors and lets the owner retry with a new search', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({ detail: 'دریافت کاربران پروژه ممکن نشد' }, false))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 61,
        account_name: 'manager61',
        mobile_number: '09121110000',
      },
    ]))

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
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const directoryHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('کاربران پروژه'))
    expect(directoryHeader).toBeTruthy()
    await directoryHeader!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('دریافت کاربران پروژه ممکن نشد')
    expect(wrapper.text()).not.toContain('manager61')

    await wrapper.get('.project-users-search-input').setValue('manager')
    await wrapper.get('.project-users-search-submit').trigger('submit')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/users-public/44/project-users?limit=25&q=manager', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.text()).not.toContain('دریافت کاربران پروژه ممکن نشد')
    expect(wrapper.text()).toContain('manager61')
  })

  it('hides the project users directory on customer self profiles', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 91,
      account_name: 'customer91',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۲/۰۲',
      trades_count: 5,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      customer_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 91, account_name: 'customer91' },
        viewerUserId: 91,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('کاربران پروژه')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('hides the block toggle on customer public profiles', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 91,
      account_name: 'customer91',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۲/۰۲',
      trades_count: 5,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      customer_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 91, account_name: 'customer91' },
        viewerUserId: 20,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.findAll('button').some((button) => button.text().includes('بلاک'))).toBe(false)
  })

  it('opens a local admin user manager for admin viewers on other profiles', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 61,
      account_name: 'managed61',
      mobile_number: '09121110000',
      role: 'عادی',
      account_status: 'active',
      is_customer: true,
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      has_bot_access: false,
      trading_restricted_until: null,
      max_sessions: 1,
      max_accountants: 3,
      max_customers: 5,
      can_block_users: true,
      max_blocked_users: 10,
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
          Teleport: true,
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          UserProfile: {
            props: ['user'],
            emits: ['navigate'],
            template: '<div class="user-profile-stub"><span>{{ user.account_name }}</span><span class="stub-customer-name">{{ user.customer_management_name }}</span><button @click="$emit(\'navigate\', \'manage_users\')">close user profile</button></div>',
          },
        },
      },
    })

    await flushPromises()

    const adminSettingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربر'))
    expect(adminSettingsButton).toBeTruthy()
    await adminSettingsButton!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/users/61', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.find('.user-profile-stub').exists()).toBe(true)
    expect(wrapper.text()).toContain('managed61')
    expect(wrapper.text()).toContain('مشتری ویژه')
    expect(wrapper.emitted('navigate')).toBeUndefined()

    await wrapper.get('.user-profile-stub button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
  })

  it('shows an inline admin error when loading user settings fails', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse({ detail: 'بارگذاری تنظیمات کاربر ممکن نشد' }, false))

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
          Teleport: true,
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          UserProfile: true,
        },
      },
    })

    await flushPromises()

    const adminSettingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربر'))
    expect(adminSettingsButton).toBeTruthy()
    await adminSettingsButton!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/users/61', expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
    expect(wrapper.text()).toContain('بارگذاری تنظیمات کاربر ممکن نشد')
  })

  it('renders the own-profile avatar trigger at the top-right without duplicate hero copy', async () => {
    const fetchMock = vi.mocked(fetch)
    buildChatFileUrlMock.mockImplementation((fileId?: string | null) => fileId ? `/files/${fileId}` : '')
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 31,
      account_name: 'owner31',
      avatar_file_id: 'avatar-1',
      mobile_number: '09126666666',
      address: 'تهران',
      last_seen_at: '2026-05-29T12:00:00Z',
      created_at_jalali: '۱۴۰۵/۰۱/۰۴',
      trades_count: 2,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
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

    expect(wrapper.find('[data-test="profile-avatar-trigger"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="profile-avatar-trigger"]').attributes('aria-label')).toBe('تغییر آواتار')
    expect(wrapper.text()).not.toContain('افزودن عکس')
    expect(wrapper.text()).not.toContain('تغییر عکس')
    expect(wrapper.find('.profile-hero-copy').exists()).toBe(false)
    expect(wrapper.find('.profile-presence-status--own').exists()).toBe(true)
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

    expect(wrapper.text()).not.toContain('افزودن عکس')
    await wrapper.get('[data-test="profile-avatar-trigger"]').trigger('click')
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

  it('surfaces avatar upload errors from the owner avatar trigger flow', async () => {
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

  it('exposes owner actions for settings navigation and owner customer/accountant manager modals', async () => {
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
          OwnerCustomerManagerModal: {
            template: '<button class="owner-customer-modal-stub" @click="$emit(\'close\')">customer modal</button>',
          },
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

    const accountantButton = wrapper.findAll('button').find((button) => button.text().includes('حسابداران'))
    expect(accountantButton).toBeTruthy()

    const customerButton = wrapper.findAll('button').find((button) => button.text().includes('مشتریان'))
    expect(customerButton).toBeTruthy()
    await customerButton!.trigger('click')
    expect(wrapper.find('.owner-customer-modal-stub').exists()).toBe(true)
    await wrapper.get('.owner-customer-modal-stub').trigger('click')
    expect(wrapper.find('.owner-customer-modal-stub').exists()).toBe(false)
    expect(vi.mocked(alert)).not.toHaveBeenCalled()

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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 1,
        trade_number: 10001,
        created_at: 'امروز',
        commodity_name: 'سکه',
        quantity: 2,
        price: 123000,
        trade_type: 'BUY',
        trade_path_kind: 'owner_customer_tier2',
        trade_path_summary: 'مالک ↔ مشتری سطح ۲',
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
        trade_path_kind: 'owner_customer_tier1',
        trade_path_summary: 'مالک ↔ مشتری سطح ۱',
        counterparty_user_id: 70,
        counterparty_name: 'حسابدار فروش',
        counterparty_profile_user_id: 70,
        counterparty_profile_account_name: 'owner-70',
        counterparty_highlight_accountant_user_id: 61,
        counterparty_highlight_accountant_relation_display_name: 'حسابدار فروش',
        customer_context_visible: true,
        customer_context_user_id: 61,
        customer_context_management_name: 'مشتری واسط',
        customer_context_tier: 'tier1',
        offer_user_id: 61,
        offer_user_name: 'حسابدار فروش',
        offer_user_profile_user_id: 70,
        offer_user_profile_account_name: 'owner-70',
        offer_user_highlight_accountant_user_id: 61,
        offer_user_highlight_accountant_relation_display_name: 'حسابدار فروش',
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

    const historyCalls = fetchMock.mock.calls.filter(([url]) => url === '/api/trades/with/50')
    expect(historyCalls).toHaveLength(1)
    expect(wrapper.text()).toContain('🟢 خرید')
    expect(wrapper.text()).toContain('🔴 فروش')
    expect(wrapper.text()).toContain('بیننده')
    expect(wrapper.text()).toContain('مالک ↔ مشتری سطح ۲')
    expect(wrapper.text()).toContain('مالک ↔ مشتری سطح ۱')
    expect(wrapper.text()).toContain('حسابدار فروش')
    expect(wrapper.text()).toContain('مشتری واسط')
    expect(wrapper.text()).toContain('سطح 1')

    const accountantLink = wrapper.findAll('.profile-link-btn').find((node) => node.text().includes('حسابدار فروش'))
    expect(accountantLink).toBeTruthy()
    await accountantLink!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual([
      'public_profile',
      {
        id: 70,
        account_name: 'owner-70',
        highlight_accountant_user_id: 61,
        highlight_accountant_relation_display_name: 'حسابدار فروش',
      },
    ])

    await historyHeader!.trigger('click')
    await historyHeader!.trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/trades/with/50')).toHaveLength(1)
  })

  it('renders target-user history from the viewed profile perspective for super-admin viewers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 60,
      account_name: 'customer60',
      avatar_file_id: null,
      mobile_number: '09125556666',
      address: 'تبریز',
      created_at_jalali: '۱۴۰۵/۰۱/۱۲',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 15,
      customer_owner_account_name: 'owner15',
      customer_management_name: 'مشتری راهبردی',
      customer_tier: 'tier2',
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 3,
        trade_number: 10003,
        created_at: 'امروز',
        commodity_name: 'سکه',
        quantity: 3,
        price: 789000,
        trade_type: 'BUY',
        offer_user_id: 88,
        offer_user_name: 'فروشنده بیرونی',
        responder_user_id: 60,
        responder_user_name: 'مشتری راهبردی',
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 60, account_name: 'customer60' },
        viewerUserId: 900,
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

    const historyHeader = wrapper.findAll('.ds-accordion-header').find((node) => node.text().includes('تاریخچه معاملات این کاربر'))
    expect(historyHeader).toBeTruthy()
    await historyHeader!.trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/trades/with/60')).toHaveLength(1)
    expect(wrapper.text()).toContain('🟢 خرید')
    expect(wrapper.text()).toContain('مالک owner15')
    expect(wrapper.text()).toContain('سطح 2')
    expect(wrapper.text()).not.toContain('هیچ معامله مشترکی یافت نشد.')
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

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/trades/my')).toHaveLength(1)
    expect(wrapper.text()).toContain('هنوز هیچ معامله‌ای انجام نداده‌اید.')
  })

  it('applies history filters and exports with the same query state', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([{ id: 1, name: 'سکه', aliases: [{ alias: 'امامی' }] }]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(new Response('export', {
      status: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': 'attachment; filename="trade_history_owner50.pdf"',
      },
    }))

    const createObjectURL = vi.fn(() => 'blob:history')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

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

    const dateInputs = wrapper.findAll('input[type="date"]')
    await dateInputs[0]!.setValue('2026-05-01')
    await dateInputs[1]!.setValue('2026-05-31')
    await wrapper.get('input[list="public-profile-history-commodities"]').setValue('امامی')

    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButton).toBeTruthy()
    await applyButton!.trigger('click')
    await flushPromises()

    const filteredCall = fetchMock.mock.calls.find(([url]) => typeof url === 'string' && url.includes('/api/trades/with/50?'))
    expect(filteredCall?.[0]).toContain('from_date=2026-05-01')
    expect(filteredCall?.[0]).toContain('to_date=2026-05-31')
    expect(filteredCall?.[0]).toContain('commodity_query=%D8%A7%D9%85%D8%A7%D9%85%DB%8C')

    const pdfButton = wrapper.findAll('button').find((node) => node.text().includes('خروجی PDF'))
    expect(pdfButton).toBeTruthy()
    await pdfButton!.trigger('click')
    await flushPromises()

    const exportCall = fetchMock.mock.calls.find(([url]) => typeof url === 'string' && url.includes('/api/trades/with/50/export?'))
    expect(exportCall?.[0]).toContain('format=pdf')
    expect(exportCall?.[0]).toContain('from_date=2026-05-01')
    expect(exportCall?.[0]).toContain('to_date=2026-05-31')
    expect(exportCall?.[0]).toContain('commodity_query=%D8%A7%D9%85%D8%A7%D9%85%DB%8C')
    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(anchorClick).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledOnce()

    anchorClick.mockRestore()
  })

  it('blocks invalid history date ranges before refetch or export', async () => {
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
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([{ id: 1, name: 'سکه', aliases: [{ alias: 'امامی' }] }]))

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

    const dateInputs = wrapper.findAll('input[type="date"]')
    await dateInputs[0]!.setValue('2026-06-01')
    await dateInputs[1]!.setValue('2026-05-01')

    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButton).toBeTruthy()
    await applyButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('بازه زمانی انتخاب‌شده معتبر نیست.')
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/trades/with/50')).toHaveLength(1)

    const pdfButton = wrapper.findAll('button').find((node) => node.text().includes('خروجی PDF'))
    expect(pdfButton).toBeTruthy()
    await pdfButton!.trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => typeof url === 'string' && url.includes('/api/trades/with/50/export?'))).toBe(false)
    expect(wrapper.text()).toContain('بازه زمانی انتخاب‌شده معتبر نیست.')
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

  it('shows customer context when the public profile belongs to a customer', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 91,
      account_name: 'customer91',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۲/۰۲',
      trades_count: 5,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      customer_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 91, account_name: 'customer91' },
        viewerUserId: 20,
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

    expect(wrapper.text()).toContain('نمای مشتری')
    expect(wrapper.text()).toContain('owner20')
    expect(wrapper.text()).toContain('مشتری ویژه')
    expect(wrapper.text()).toContain('سطح 2')
  })

  it('shows owner customer list for super-admin viewers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))

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
      accountant_relations: [],
      customer_owner_user_id: null,
      customer_owner_account_name: null,
      customer_management_name: null,
      customer_tier: null,
      customer_relations: [
        {
          customer_user_id: 91,
          customer_account_name: 'customer91',
          management_name: 'مشتری ویژه',
          customer_tier: 'tier1',
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
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('مشتریان این مالک')
    expect(wrapper.text()).toContain('مشتری ویژه')
    expect(wrapper.text()).toContain('customer91')
    expect(wrapper.text()).toContain('سطح 1')

    await wrapper.get('.customer-profile-link-btn').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual([
      'public_profile',
      {
        id: 91,
        account_name: 'customer91',
      },
    ])
  })

  it('does not show owner customer list for middle-manager viewers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))

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
      accountant_relations: [],
      customer_owner_user_id: null,
      customer_owner_account_name: null,
      customer_management_name: null,
      customer_tier: null,
      customer_relations: [
        {
          customer_user_id: 91,
          customer_account_name: 'customer91',
          management_name: 'مشتری ویژه',
          customer_tier: 'tier1',
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
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('مشتریان این مالک')
    expect(wrapper.text()).not.toContain('مشتری ویژه')
  })
})