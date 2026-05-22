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

    const wrapper = mount(OwnerCustomerManagerModal, {
      global: {
        stubs: {
          Teleport: true,
        },
      },
    })

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

    const wrapper = mount(OwnerCustomerManagerModal, {
      global: {
        stubs: {
          Teleport: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('button.toggle-sessions').exists()).toBe(false)
    expect(wrapper.text()).toContain('کپی لینک ثبت‌نام')

    wrapper.unmount()
  })
})
