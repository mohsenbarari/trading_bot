import { describe, expect, it, vi, beforeEach } from 'vitest'
import {
  buildCustomerDetailUpdatePayload,
  buildCustomerPayload,
  createOwnerCustomerRelation,
  fetchOwnerCustomerRelations,
  fetchOwnerCustomerTradeStats,
  fetchOwnerCustomerTrades,
  normalizeCommissionRate,
  normalizeLatinDigits,
  terminateOwnerCustomerSession,
  updateOwnerCustomerRelation,
  useOwnerCustomers,
  type CustomerRelation,
} from './useOwnerCustomers'
import {
  createOwnerAccountantRelation,
  fetchOwnerAccountantRelations,
  normalizeDutyDescription,
  terminateOwnerAccountantSession,
  updateOwnerAccountantRelation,
  useOwnerAccountants,
} from './useOwnerAccountants'

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

function makeCustomer(overrides: Partial<CustomerRelation> = {}): CustomerRelation {
  return {
    id: 1,
    owner_user_id: 7,
    customer_user_id: 10,
    customer_account_name: 'customer10',
    invitation_account_name: null,
    mobile_number: '09120000000',
    management_name: 'مشتری اول',
    customer_tier: 'tier2',
    commission_rate: 0.5,
    min_trade_quantity: null,
    max_trade_quantity: null,
    max_daily_trades: null,
    max_daily_commodity_volume: null,
    status: 'active',
    invitation_token: 'token',
    registration_link: null,
    expires_at: null,
    activated_at: '2026-01-02T10:00:00Z',
    deleted_at: null,
    created_at: '2026-01-01T10:00:00Z',
    ...overrides,
  }
}

describe('owner relation composables', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
  })

  it('normalizes customer payloads and local customer state for route-native workspaces', () => {
    expect(normalizeLatinDigits('۰۹١٢')).toBe('0912')
    expect(normalizeCommissionRate('۱.۲۳')).toBe(1.23)
    expect(normalizeCommissionRate('200')).toBe(100)

    expect(buildCustomerPayload({
      customer_tier: 'tier2',
      commission_rate: '0.75',
      min_trade_quantity: '1',
      max_trade_quantity: '',
      max_daily_trades: '3',
      max_daily_commodity_volume: '100',
    })).toEqual({
      customer_tier: 'tier2',
      commission_rate: 0.75,
      min_trade_quantity: 1,
      max_trade_quantity: null,
      max_daily_trades: 3,
      max_daily_commodity_volume: 100,
    })

    const detailPayload = buildCustomerDetailUpdatePayload(makeCustomer(), {
      customer_tier: 'tier1',
      commission_rate: '',
      min_trade_quantity: '2',
      max_trade_quantity: '',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })
    expect(detailPayload).toEqual({
      customer_tier: 'tier1',
      commission_rate: null,
      min_trade_quantity: 2,
    })

    const state = useOwnerCustomers()
    state.relations.value = [
      makeCustomer({ id: 3, status: 'deleted', created_at: '2026-01-03T10:00:00Z' }),
      makeCustomer({ id: 2, status: 'pending', customer_user_id: null, created_at: '2026-01-02T10:00:00Z' }),
      makeCustomer({ id: 1, status: 'active', created_at: '2026-01-01T10:00:00Z' }),
    ]
    state.selectedRelationId.value = 1
    expect(state.pendingInvitationRelations.value.map(relation => relation.id)).toEqual([2])
    expect(state.manageableRelations.value.map(relation => relation.id)).toEqual([1, 3])
    expect(state.selectedRelation.value?.id).toBe(1)
  })

  it('routes customer API calls through the extracted data layer', async () => {
    const customer = makeCustomer()
    apiFetchMock
      .mockResolvedValueOnce(makeResponse([customer]))
      .mockResolvedValueOnce(makeResponse({ ...customer, id: 2 }))
      .mockResolvedValueOnce(makeResponse({ ...customer, commission_rate: 0.8 }))
      .mockResolvedValueOnce(makeResponse({ detail: 'done' }))
      .mockResolvedValueOnce(makeResponse([{ id: 9, trade_number: 1001 }]))
      .mockResolvedValueOnce(makeResponse({ relation_id: 1, period_days: 7 }))

    expect(await fetchOwnerCustomerRelations({ retryNetwork: false })).toEqual([customer])
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/customers/owner-relations', { retryNetwork: false })

    await createOwnerCustomerRelation({ management_name: 'مشتری دوم' })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/customers/owner-relations', {
      method: 'POST',
      body: JSON.stringify({ management_name: 'مشتری دوم' }),
    })

    await updateOwnerCustomerRelation(1, { commission_rate: 0.8 }, { retryNetwork: false })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/customers/owner-relations/1', {
      method: 'PATCH',
      body: JSON.stringify({ commission_rate: 0.8 }),
      retryNetwork: false,
    })

    await terminateOwnerCustomerSession(1, 'session-1')
    expect(apiFetchMock).toHaveBeenNthCalledWith(4, '/api/customers/owner-relations/1/sessions/session-1', {
      method: 'DELETE',
    })

    await fetchOwnerCustomerTrades(10, { limit: 20 })
    expect(apiFetchMock).toHaveBeenNthCalledWith(5, '/api/trades/with/10?limit=20')

    await fetchOwnerCustomerTradeStats(1, 7)
    expect(apiFetchMock).toHaveBeenNthCalledWith(6, '/api/customers/owner-relations/1/trade-stats?days=7')
  })

  it('routes accountant API calls and state through the extracted data layer', async () => {
    const accountant = {
      id: 8,
      owner_user_id: 7,
      accountant_user_id: 18,
      accountant_account_name: 'acc-active',
      global_account_name: 'acc-active',
      relation_display_name: 'حسابدار فعال',
      duty_description: 'پیگیری',
      mobile_number: '09120000000',
      status: 'active',
      invitation_token: 'token',
      registration_link: null,
      expires_at: '2026-01-03T10:00:00Z',
      activated_at: '2026-01-02T10:00:00Z',
      deleted_at: null,
      created_at: '2026-01-01T10:00:00Z',
    }

    expect(normalizeDutyDescription('  ')).toBeNull()
    expect(normalizeDutyDescription(' گزارش روزانه ')).toBe('گزارش روزانه')

    const state = useOwnerAccountants()
    state.relations.value = [
      { ...accountant, id: 2, status: 'pending', created_at: '2026-01-02T10:00:00Z' },
      accountant,
    ]
    state.selectedRelationId.value = 8
    expect(state.pendingInvitationRelations.value.map(relation => relation.id)).toEqual([2])
    expect(state.selectedRelation.value?.relation_display_name).toBe('حسابدار فعال')

    apiFetchMock
      .mockResolvedValueOnce(makeResponse([accountant]))
      .mockResolvedValueOnce(makeResponse({ ...accountant, id: 9 }))
      .mockResolvedValueOnce(makeResponse({ ...accountant, duty_description: 'ثبت معاملات' }))
      .mockResolvedValueOnce(makeResponse({ detail: 'done' }))

    expect(await fetchOwnerAccountantRelations()).toEqual([accountant])
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/accountants/owner-relations')

    await createOwnerAccountantRelation({ relation_display_name: 'حسابدار جدید' })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/accountants/owner-relations', {
      method: 'POST',
      body: JSON.stringify({ relation_display_name: 'حسابدار جدید' }),
    })

    await updateOwnerAccountantRelation(8, { duty_description: 'ثبت معاملات' })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/accountants/owner-relations/8', {
      method: 'PATCH',
      body: JSON.stringify({ duty_description: 'ثبت معاملات' }),
    })

    await terminateOwnerAccountantSession(8, 'session-8')
    expect(apiFetchMock).toHaveBeenNthCalledWith(4, '/api/accountants/owner-relations/8/sessions/session-8', {
      method: 'DELETE',
    })
  })
})
