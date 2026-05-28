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
    await wrapper.get('button.toggle-sessions').trigger('click')
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

    expect(wrapper.find('button.toggle-sessions').exists()).toBe(false)
    expect(wrapper.text()).toContain('کپی لینک ثبت‌نام')

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

    await wrapper.get('.create-account-name').setValue('fresh_customer')
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
      account_name: 'fresh_customer',
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
    expect((wrapper.get('.create-account-name').element as HTMLInputElement).value).toBe('')
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

    await wrapper.get('.start-edit').trigger('click')
    expect((wrapper.get('.edit-commission-rate').element as HTMLInputElement).value).toBe('0.5')

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
    expect(wrapper.find('.edit-panel').exists()).toBe(false)
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

    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledWith('دعوت مشتری ویژه لغو شود؟')
    expect(apiFetchMock).toHaveBeenCalledWith('/api/customers/owner-relations/12', { method: 'DELETE' })
    expect(wrapper.text()).toContain('دعوت مشتری لغو شد.')
    expect(wrapper.text()).toContain('هنوز مشتری فعالی یا دعوت pending ثبت نشده است.')

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

    await wrapper.get('.toggle-sessions').trigger('click')
    await flushPromises()
    expect(wrapper.find('.session-panel').exists()).toBe(true)

    await wrapper.get('.unlink-active').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledWith('ارتباط مشتری مشتری ویژه قطع شود؟ این عملیات دسترسی مشتری را کامل غیرفعال می‌کند.')
    expect(apiFetchMock).toHaveBeenCalledWith('/api/customers/owner-relations/11', { method: 'DELETE' })
    expect(wrapper.find('.session-panel').exists()).toBe(false)
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

    await wrapper.get('.create-account-name').setValue('duplicate_customer')
    await wrapper.get('.create-management-name').setValue('مشتری تکراری')
    await wrapper.get('.create-mobile-number').setValue('09121110000')
    await wrapper.get('.submit-create').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('این نام کاربری قبلاً ثبت شده است.')

    await wrapper.get('.copy-link').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalled()
    expect(wrapper.text()).toContain('کپی لینک ثبت‌نام ممکن نشد.')

    wrapper.unmount()
  })
})
