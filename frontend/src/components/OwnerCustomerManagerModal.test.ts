import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import OwnerCustomerManagerModal from './OwnerCustomerManagerModal.vue'

const { apiFetchMock } = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

function makeResponse(payload: unknown, ok = true, status = ok ? 200 : 400) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

const activeRelation = {
  id: 11,
  owner_user_id: 3,
  customer_user_id: 18,
  customer_account_name: 'customer18',
  invitation_account_name: null,
  mobile_number: '09120000000',
  management_name: 'مشتری ویژه',
  customer_tier: 'tier2',
  commission_rate: 0.5,
  min_trade_quantity: 1,
  max_trade_quantity: 10,
  max_daily_trades: 5,
  max_daily_commodity_volume: 100,
  status: 'active',
  invitation_token: 'token-1',
  registration_link: null,
  expires_at: null,
  activated_at: '2026-05-21T12:00:00Z',
  deleted_at: null,
  created_at: '2026-05-20T12:00:00Z',
}

const pendingRelation = {
  ...activeRelation,
  id: 12,
  customer_user_id: null,
  customer_account_name: null,
  invitation_account_name: 'pending_customer',
  status: 'pending',
  registration_link: 'https://example.com/register/token-2',
  expires_at: '2026-05-28T12:00:00Z',
  activated_at: null,
}

const expiredRelation = {
  ...pendingRelation,
  id: 13,
  management_name: 'مشتری منقضی',
  registration_link: null,
  status: 'expired',
  expires_at: '2026-05-20T12:00:00Z',
  created_at: '2026-05-19T12:00:00Z',
}

const deletedRelation = {
  ...activeRelation,
  id: 14,
  management_name: 'مشتری حذف‌شده',
  status: 'deleted',
  customer_user_id: null,
  customer_account_name: null,
  invitation_account_name: 'deleted_customer',
  deleted_at: '2026-05-22T12:00:00Z',
  created_at: '2026-05-18T12:00:00Z',
}

describe('OwnerCustomerManagerModal.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiFetchMock.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  function mountModal() {
    return mount(OwnerCustomerManagerModal, {
      global: {
        stubs: {
          Teleport: true,
        },
      },
    })
  }

  async function openCreatePanel(wrapper: any) {
    await flushPromises()
  }

  async function openRelationsPanel(wrapper: any) {
    await flushPromises()
  }

  async function backToCategories(wrapper: any) {
    const backButton = wrapper.findAll('button').find((button: any) => button.text().includes('بازگشت به لیست'))
    if (backButton) {
      await backButton.trigger('click')
    }
    await flushPromises()
  }

  async function openCustomerDetail(wrapper: any, text = 'مشتری ویژه') {
    const card = wrapper.findAll('.customer-card').find((node: any) => node.text().includes(text))
    expect(card).toBeTruthy()
    await card!.get('.manage-customer').trigger('click')
    await flushPromises()
  }

  async function openDetailAccordion(wrapper: any, label: string) {
    const accordion = wrapper.findAll('.detail-accordion').find((node: any) => node.text().includes(label))
    expect(accordion).toBeTruthy()
    await accordion!.get('.ds-accordion-header').trigger('click')
    await flushPromises()
  }

  it('loads and terminates customer sessions for an active customer', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true)
    let sessionListCallCount = 0

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations') {
        return makeResponse([activeRelation])
      }
      if (url === '/api/customers/owner-relations/11/sessions' && options?.method === 'GET') {
        sessionListCallCount += 1
        return makeResponse(
          sessionListCallCount === 1
            ? [
                {
                  id: '11111111-1111-1111-1111-111111111111',
                  device_name: 'Chrome on Android',
                  device_ip: '10.0.0.15',
                  platform: 'web',
                  home_server: 'foreign',
                  is_primary: true,
                  is_active: true,
                  created_at: '2026-05-21T10:00:00Z',
                  last_active_at: '2026-05-21T12:30:00Z',
                },
              ]
            : [],
        )
      }
      if (url === '/api/customers/owner-relations/11/sessions/11111111-1111-1111-1111-111111111111' && options?.method === 'DELETE') {
        return makeResponse({ detail: 'نشست مشتری با موفقیت پایان یافت' })
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()

    await flushPromises()
    await openRelationsPanel(wrapper)
    await openCustomerDetail(wrapper)
    await openDetailAccordion(wrapper, 'نشست مشتری')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/customers/owner-relations/11/sessions', { method: 'GET' })
    expect(wrapper.text()).toContain('Chrome on Android')
    expect(wrapper.text()).toContain('primary')

    await wrapper.get('button.terminate-session').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalled()
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/customers/owner-relations/11/sessions/11111111-1111-1111-1111-111111111111',
      { method: 'DELETE' },
    )
    expect(wrapper.text()).toContain('نشست مشتری با موفقیت پایان یافت')
    expect(wrapper.text()).toContain('در حال حاضر نشست فعالی برای این مشتری ثبت نشده است.')

    wrapper.unmount()
  })

  it('shows session controls only for active customers', async () => {
    apiFetchMock.mockResolvedValue(makeResponse([pendingRelation]))

    const wrapper = mountModal()

    await flushPromises()
    await openRelationsPanel(wrapper)

    expect(wrapper.find('button.terminate-session').exists()).toBe(false)
    expect(wrapper.text()).toContain('کپی لینک ثبت‌نام')

    wrapper.unmount()
  })

  it('orders pending and active relations first, renders status-specific copy, and clears stale open sessions after refresh', async () => {
    const refreshedPendingForActiveId = {
      ...pendingRelation,
      id: 11,
      management_name: 'مشتری ویژه',
      invitation_account_name: 'customer18_pending',
      registration_link: 'https://example.com/register/token-11',
      created_at: '2026-05-23T12:00:00Z',
    }

    let relationsCallCount = 0
    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        relationsCallCount += 1
        if (relationsCallCount === 1) {
          return makeResponse([expiredRelation, activeRelation, pendingRelation, deletedRelation])
        }
        return makeResponse([refreshedPendingForActiveId, expiredRelation, deletedRelation])
      }
      if (url === '/api/customers/owner-relations/11/sessions' && options?.method === 'GET') {
        return makeResponse([])
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    const titles = wrapper.findAll('.customer-card h5').map((node) => node.text())
    expect(titles).toEqual(['مشتری ویژه', 'مشتری ویژه', 'مشتری منقضی', 'مشتری حذف‌شده'])
    expect(wrapper.text()).toContain('مهلت این دعوت تمام شده و در انتظار همگام سازی وضعیت است.')
    expect(wrapper.text()).toContain('این مشتری با @customer18 در سطح 2 فعال است.')
    expect(wrapper.text()).toContain('مهلت این دعوت به پایان رسیده است.')
    expect(wrapper.text()).toContain('این رابطه حذف شده است.')

    await openCustomerDetail(wrapper, '@customer18')
    await openDetailAccordion(wrapper, 'نشست مشتری')
    await flushPromises()
    expect(wrapper.text()).toContain('در حال حاضر نشست فعالی برای این مشتری ثبت نشده است.')
    await backToCategories(wrapper)

    await wrapper.get('.refresh-relations').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('نشست‌های فعال مشتری')
    expect(wrapper.findAll('.customer-card h5')[0]!.text()).toBe('مشتری ویژه')
    expect(wrapper.text()).toContain('در انتظار ثبت‌نام')

    wrapper.unmount()
  })

  it('creates a tier2 customer invitation, normalizes numeric fields, and resets the form', async () => {
    const createdRelation = {
      ...pendingRelation,
      id: 33,
      management_name: 'مشتری تازه',
      customer_tier: 'tier2',
      commission_rate: 1.25,
      min_trade_quantity: 2,
      max_trade_quantity: 22,
      max_daily_trades: 6,
      max_daily_commodity_volume: 140,
    }

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([])
      }
      if (url === '/api/customers/owner-relations' && options?.method === 'POST') {
        return makeResponse(createdRelation)
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openCreatePanel(wrapper)

    await wrapper.get('.create-management-name').setValue('مشتری تازه')
    await wrapper.get('.create-mobile-number').setValue('09125550000')
    await wrapper.get('.create-tier-select').setValue('tier2')
    await wrapper.get('.create-commission-rate').setValue('1.25')
    await wrapper.get('.create-min-trade').setValue('2')
    await wrapper.get('.create-max-trade').setValue('22')
    await wrapper.get('.create-max-daily-trades').setValue('6')
    await wrapper.get('.create-max-daily-volume').setValue('140')

    await wrapper.get('.submit-create').trigger('click')
    await flushPromises()

    const postCall = apiFetchMock.mock.calls.find(([url, options]) => url === '/api/customers/owner-relations' && options?.method === 'POST')
    expect(postCall).toBeTruthy()
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      account_name: 'customer_09125550000',
      management_name: 'مشتری تازه',
      mobile_number: '09125550000',
      customer_tier: 'tier2',
      commission_rate: 1.25,
      min_trade_quantity: 2,
      max_trade_quantity: 22,
      max_daily_trades: 6,
      max_daily_commodity_volume: 140,
    })
    expect(wrapper.text()).toContain('دعوت مشتری ثبت شد.')
    expect(wrapper.text()).toContain('مشتری تازه')
    expect((wrapper.get('.create-management-name').element as HTMLInputElement).value).toBe('')
    expect(wrapper.find('.create-commission-rate').exists()).toBe(false)

    wrapper.unmount()
  })

  it('edits an active customer and clears tier2 commission when switching back to tier1', async () => {
    const updatedRelation = {
      ...activeRelation,
      customer_tier: 'tier1',
      commission_rate: null,
      min_trade_quantity: 3,
      max_trade_quantity: 13,
      max_daily_trades: 7,
      max_daily_commodity_volume: 150,
    }

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([activeRelation])
      }
      if (url === '/api/customers/owner-relations/11' && options?.method === 'PATCH') {
        return makeResponse(updatedRelation)
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    await openCustomerDetail(wrapper)
    expect((wrapper.get('.edit-commission-rate').element as HTMLInputElement).placeholder).toBe('0.5')

    await wrapper.get('.edit-tier-select').setValue('tier1')
    await flushPromises()
    expect(wrapper.find('.edit-commission-rate').exists()).toBe(false)

    await wrapper.get('.edit-min-trade').setValue('3')
    await wrapper.get('.edit-max-trade').setValue('13')
    await wrapper.get('.edit-max-daily-trades').setValue('7')
    await wrapper.get('.edit-max-daily-volume').setValue('150')
    await wrapper.get('.save-edit').trigger('click')
    await flushPromises()

    const patchCall = apiFetchMock.mock.calls.find(([url, options]) => url === '/api/customers/owner-relations/11' && options?.method === 'PATCH')
    expect(patchCall).toBeTruthy()
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      customer_tier: 'tier1',
      commission_rate: null,
      min_trade_quantity: 3,
      max_trade_quantity: 13,
      max_daily_trades: 7,
      max_daily_commodity_volume: 150,
    })
    expect(wrapper.text()).toContain('اطلاعات مشتری به‌روزرسانی شد.')
    expect(wrapper.text()).toContain('سطح 1')

    wrapper.unmount()
  })

  it('copies the registration link for pending customers and lets the copied state expire', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    apiFetchMock.mockResolvedValue(makeResponse([pendingRelation]))

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    await wrapper.get('.copy-link').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('https://example.com/register/token-2')
    expect(wrapper.get('.copy-link').text()).toBe('کپی شد')

    await vi.advanceTimersByTimeAsync(1800)
    await flushPromises()

    expect(wrapper.get('.copy-link').text()).toBe('کپی لینک ثبت‌نام')

    wrapper.unmount()
  })

  it('cancels a pending invitation and removes it from the visible list', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true)

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([pendingRelation])
      }
      if (url === '/api/customers/owner-relations/12' && options?.method === 'DELETE') {
        return makeResponse({ detail: 'ok' })
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    await openCustomerDetail(wrapper)
    await openDetailAccordion(wrapper, 'قطع رابطه')
    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledWith('دعوت مشتری ویژه لغو شود؟')
    expect(apiFetchMock).toHaveBeenCalledWith('/api/customers/owner-relations/12', { method: 'DELETE' })
    expect(wrapper.text()).toContain('دعوت مشتری لغو شد.')
    expect(wrapper.text()).toContain('هنوز مشتری فعالی یا دعوت در انتظار ثبت نشده است.')

    wrapper.unmount()
  })

  it('unlinks an active customer after clearing the open-session state', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true)

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([activeRelation])
      }
      if (url === '/api/customers/owner-relations/11/sessions' && options?.method === 'GET') {
        return makeResponse([])
      }
      if (url === '/api/customers/owner-relations/11' && options?.method === 'DELETE') {
        return makeResponse({ detail: 'ok' })
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    await openCustomerDetail(wrapper)
    await openDetailAccordion(wrapper, 'نشست مشتری')
    await flushPromises()
    expect(wrapper.text()).toContain('در حال حاضر نشست فعالی برای این مشتری ثبت نشده است.')

    await openDetailAccordion(wrapper, 'قطع رابطه')
    await wrapper.get('.unlink-active').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledWith('ارتباط مشتری مشتری ویژه قطع شود؟ این عملیات دسترسی مشتری را کامل غیرفعال می‌کند.')
    expect(apiFetchMock).toHaveBeenCalledWith('/api/customers/owner-relations/11', { method: 'DELETE' })
    expect(wrapper.text()).toContain('ارتباط مشتری قطع شد و دسترسی او غیرفعال گردید.')

    wrapper.unmount()
  })

  it('surfaces API detail errors for create and copy-link failures', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('clipboard failed'))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([pendingRelation])
      }
      if (url === '/api/customers/owner-relations' && options?.method === 'POST') {
        return makeResponse({ detail: 'این نام کاربری قبلاً ثبت شده است.' }, false, 409)
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openCreatePanel(wrapper)

    await wrapper.get('.create-management-name').setValue('مشتری تکراری')
    await wrapper.get('.create-mobile-number').setValue('09121110000')
    await wrapper.get('.submit-create').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('این نام کاربری قبلاً ثبت شده است.')

    await openRelationsPanel(wrapper)
    await wrapper.get('.copy-link').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalled()
    expect(wrapper.text()).toContain('کپی لینک ثبت‌نام ممکن نشد.')

    wrapper.unmount()
  })

  it('surfaces session loading errors inside the banner while keeping the active panel open', async () => {
    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([activeRelation])
      }
      if (url === '/api/customers/owner-relations/11/sessions' && options?.method === 'GET') {
        return makeResponse({ detail: 'دریافت نشست‌های مشتری شکست خورد.' }, false, 500)
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    await openCustomerDetail(wrapper)
    await openDetailAccordion(wrapper, 'نشست مشتری')
    await flushPromises()

    expect(wrapper.find('.customer-banner.error').text()).toBe('دریافت نشست‌های مشتری شکست خورد.')
    expect(wrapper.find('.session-empty').text()).toContain('در حال حاضر نشست فعالی برای این مشتری ثبت نشده است.')

    wrapper.unmount()
  })

  it('renders multi-day pending countdowns, revoked copy, and unknown session badges without terminating on cancelled confirm', async () => {
    vi.setSystemTime(new Date('2026-05-21T12:00:00Z'))
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const longPendingRelation = {
      ...pendingRelation,
      id: 21,
      management_name: 'مشتری چندروزه',
      expires_at: '2026-05-23T13:02:03Z',
      created_at: '2026-05-20T13:00:00Z',
    }
    const revokedRelation = {
      ...pendingRelation,
      id: 22,
      management_name: 'دعوت لغوشده',
      status: 'revoked',
      registration_link: null,
      expires_at: null,
    }
    const activeUnknownSessionRelation = {
      ...activeRelation,
      id: 23,
      management_name: 'مشتری نشست‌دار',
    }

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([longPendingRelation, revokedRelation, activeUnknownSessionRelation])
      }
      if (url === '/api/customers/owner-relations/23/sessions' && options?.method === 'GET') {
        return makeResponse([
          {
            id: '33333333-3333-3333-3333-333333333333',
            device_name: '',
            device_ip: null,
            platform: '',
            home_server: '',
            is_primary: false,
            is_active: true,
            created_at: null,
            last_active_at: null,
          },
        ])
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    expect(wrapper.text()).toContain('2 روز و 01:02:03')
    expect(wrapper.text()).toContain('این دعوت توسط مالک لغو شده است.')

    await openCustomerDetail(wrapper, 'مشتری نشست‌دار')
    await openDetailAccordion(wrapper, 'نشست مشتری')
    await flushPromises()

    expect(wrapper.text()).toContain('دستگاه ناشناس')
    expect(wrapper.findAll('.session-badge.neutral').map((node) => node.text())).toEqual(['نامشخص', 'نامشخص'])
    expect(wrapper.text()).toContain('آخرین فعالیت: ---')
    expect(wrapper.text()).toContain('شروع نشست: ---')

    await wrapper.get('.terminate-session').trigger('click')
    expect(confirmMock).toHaveBeenCalledWith('نشست «دستگاه مشتری» پایان یابد؟')
    expect(apiFetchMock).toHaveBeenCalledTimes(2)

    wrapper.unmount()
  })

  it('shows the fallback relation-load error when the API returns no usable detail', async () => {
    apiFetchMock.mockResolvedValue(makeResponse({}, false, 500))

    const wrapper = mountModal()
    await flushPromises()

    expect(wrapper.text()).toContain('دریافت لیست مشتریان ناموفق بود.')
    expect(wrapper.find('.customer-banner.error').exists()).toBe(true)

    wrapper.unmount()
  })

  it('resets the create form, clears tier2 commission on tier changes, and reports clipboard failures', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('clipboard denied'))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    apiFetchMock.mockResolvedValue(makeResponse([pendingRelation]))

    const wrapper = mountModal()
    await flushPromises()
    await openCreatePanel(wrapper)

    await wrapper.get('.create-management-name').setValue('مشتری سطح دو')
    await wrapper.get('.create-mobile-number').setValue('09127770000')
    await wrapper.get('.create-tier-select').setValue('tier2')
    await wrapper.get('.create-commission-rate').setValue('1.5')
    await wrapper.get('.create-tier-select').setValue('tier1')
    await flushPromises()

    expect(wrapper.find('.create-commission-rate').exists()).toBe(false)

    await wrapper.findAll('.panel-actions .secondary-btn')[0]!.trigger('click')

    expect((wrapper.get('.create-management-name').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-mobile-number').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-tier-select').element as HTMLSelectElement).value).toBe('tier1')

    await openRelationsPanel(wrapper)
    await wrapper.get('.copy-link').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('https://example.com/register/token-2')
    expect(wrapper.text()).toContain('کپی لینک ثبت‌نام ممکن نشد.')

    wrapper.unmount()
  })

  it('surfaces session, create, edit, and unlink failures while keeping the relevant UI state intact', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true)

    apiFetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/customers/owner-relations' && !options?.method) {
        return makeResponse([activeRelation, pendingRelation])
      }
      if (url === '/api/customers/owner-relations/11/sessions' && options?.method === 'GET') {
        return makeResponse({ detail: 'نشست‌های مشتری در دسترس نیست.' }, false, 500)
      }
      if (url === '/api/customers/owner-relations/11' && options?.method === 'PATCH') {
        return makeResponse({ detail: 'ویرایش مشتری ناموفق بود.' }, false, 400)
      }
      if (url === '/api/customers/owner-relations' && options?.method === 'POST') {
        return makeResponse({ detail: 'ایجاد مشتری ناموفق بود.' }, false, 400)
      }
      if (url === '/api/customers/owner-relations/12' && options?.method === 'DELETE') {
        return makeResponse({}, false, 500)
      }
      throw new Error(`Unexpected apiFetch call: ${url}`)
    })

    const wrapper = mountModal()
    await flushPromises()
    await openRelationsPanel(wrapper)

    await openCustomerDetail(wrapper, '@customer18')
    await openDetailAccordion(wrapper, 'نشست مشتری')
    await flushPromises()
    expect(wrapper.text()).toContain('نشست‌های مشتری در دسترس نیست.')

    await openDetailAccordion(wrapper, 'مشخصات و محدودیت‌ها')
    await wrapper.get('.edit-min-trade').setValue('3')
    await wrapper.get('.save-edit').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ویرایش مشتری ناموفق بود.')

    await openCreatePanel(wrapper)
    await wrapper.get('.create-management-name').setValue('مشتری خطادار')
    await wrapper.get('.create-mobile-number').setValue('09129990000')
    await wrapper.get('.submit-create').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ایجاد مشتری ناموفق بود.')

    await backToCategories(wrapper)
    await openRelationsPanel(wrapper)
    await openCustomerDetail(wrapper, '@pending_customer')
    await openDetailAccordion(wrapper, 'قطع رابطه')
    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledWith('دعوت مشتری ویژه لغو شود؟')
    expect(wrapper.text()).toContain('لغو دعوت مشتری ناموفق بود.')
    await backToCategories(wrapper)
    expect(wrapper.findAll('.customer-card h5').map((node) => node.text())).toContain('مشتری ویژه')

    wrapper.unmount()
  })
})
