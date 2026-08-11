import { describe, expect, it, vi, beforeEach } from 'vitest'
import {
  buildCustomerDetailUpdatePayload,
  buildCustomerPayload,
  createOwnerCustomerRelation,
  deleteOwnerCustomerRelation,
  fetchOwnerCustomerRelation,
  fetchOwnerCustomerRelations,
  fetchOwnerCustomerTradeStats,
  fetchOwnerCustomerTrades,
  normalizeCommissionRate,
  normalizeLatinDigits,
  normalizeOptionalNumber,
  terminateOwnerCustomerSession,
  updateOwnerCustomerRelation,
  useOwnerCustomers,
  type CustomerRelation,
} from './useOwnerCustomers'
import {
  createOwnerAccountantRelation,
  deleteOwnerAccountantRelation,
  fetchOwnerAccountantRelation,
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
    expect(normalizeOptionalNumber('۱۲,۵')).toBe(12.5)
    expect(normalizeOptionalNumber('١٢,٥')).toBe(12.5)
    expect(normalizeOptionalNumber('۱٬۲۳۴٫۵')).toBe(1234.5)
    expect(normalizeOptionalNumber('١٬٢٣٤٫٥')).toBe(1234.5)

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

  it('emits only changed customer detail values and sends null for an explicitly cleared limit', () => {
    const detailPayload = buildCustomerDetailUpdatePayload(makeCustomer({
      commission_rate: 0.5,
      min_trade_quantity: 1,
      max_trade_quantity: 20,
      max_daily_trades: 3,
      max_daily_commodity_volume: 100,
    }), {
      customer_tier: 'tier2',
      commission_rate: '۰,۵',
      min_trade_quantity: '۱',
      max_trade_quantity: '',
      max_daily_trades: '٤',
      max_daily_commodity_volume: '۱۰۰,۰',
    })

    expect(detailPayload).toEqual({
      max_trade_quantity: null,
      max_daily_trades: 4,
    })
  })

  it('rejects invalid non-empty customer detail numbers instead of silently omitting them', () => {
    expect(() => buildCustomerDetailUpdatePayload(makeCustomer(), {
      customer_tier: 'tier2',
      commission_rate: 'عدد نامعتبر',
      min_trade_quantity: '',
      max_trade_quantity: '',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })).toThrow('مقدار «درصد کمیسیون مشتری» باید یک عدد معتبر باشد.')

    expect(() => buildCustomerDetailUpdatePayload(makeCustomer(), {
      customer_tier: 'tier2',
      commission_rate: '۰٫۵',
      min_trade_quantity: '',
      max_trade_quantity: '۱۲٫۵ نامعتبر',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })).toThrow('مقدار «حداکثر مقدار معامله» باید یک عدد معتبر باشد.')
  })

  it('rejects invalid non-empty customer create numbers instead of clearing them', () => {
    expect(() => buildCustomerPayload({
      customer_tier: 'tier2',
      commission_rate: 'درصد نامعتبر',
      min_trade_quantity: '',
      max_trade_quantity: '',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })).toThrow('مقدار «درصد کمیسیون مشتری» باید یک عدد معتبر باشد.')

    expect(() => buildCustomerPayload({
      customer_tier: 'tier1',
      commission_rate: 'این فیلد در پایه نادیده است',
      min_trade_quantity: '',
      max_trade_quantity: '۱۲٫۵ نامعتبر',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })).toThrow('مقدار «حداکثر مقدار معامله» باید یک عدد معتبر باشد.')
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
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/customers/owner-relations', expect.objectContaining({
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await createOwnerCustomerRelation({ management_name: 'مشتری دوم' })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/customers/owner-relations', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ management_name: 'مشتری دوم' }),
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await updateOwnerCustomerRelation(1, { commission_rate: 0.8 }, { retryNetwork: false })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/customers/owner-relations/1', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ commission_rate: 0.8 }),
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await terminateOwnerCustomerSession(1, 'session-1')
    expect(apiFetchMock).toHaveBeenNthCalledWith(4, '/api/customers/owner-relations/1/sessions/session-1', expect.objectContaining({
      method: 'DELETE',
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await fetchOwnerCustomerTrades(10, { limit: 20 })
    expect(apiFetchMock).toHaveBeenNthCalledWith(5, '/api/trades/with/10?limit=20', expect.objectContaining({
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await fetchOwnerCustomerTradeStats(1, 7)
    expect(apiFetchMock).toHaveBeenNthCalledWith(6, '/api/customers/owner-relations/1/trade-stats?days=7', expect.objectContaining({
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))
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
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/accountants/owner-relations', expect.objectContaining({
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await createOwnerAccountantRelation({ relation_display_name: 'حسابدار جدید' })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/accountants/owner-relations', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ relation_display_name: 'حسابدار جدید' }),
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await updateOwnerAccountantRelation(8, { duty_description: 'ثبت معاملات' })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/accountants/owner-relations/8', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ duty_description: 'ثبت معاملات' }),
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await terminateOwnerAccountantSession(8, 'session-8')
    expect(apiFetchMock).toHaveBeenNthCalledWith(4, '/api/accountants/owner-relations/8/sessions/session-8', expect.objectContaining({
      method: 'DELETE',
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))
  })

  it('requires explicit delete semantics and exposes terminal-capable owner detail requests', async () => {
    const customer = makeCustomer({ status: 'deleted', deleted_at: '2026-01-03T10:00:00Z' })
    const accountant = {
      id: 8,
      owner_user_id: 7,
      accountant_user_id: 18,
      accountant_account_name: 'acc-terminal',
      global_account_name: 'acc-terminal',
      relation_display_name: 'حسابدار پایان‌یافته',
      duty_description: null,
      mobile_number: '09120000000',
      status: 'deleted',
      expires_at: '2026-01-03T10:00:00Z',
      activated_at: '2026-01-02T10:00:00Z',
      deleted_at: '2026-01-04T10:00:00Z',
      created_at: '2026-01-01T10:00:00Z',
    }
    apiFetchMock
      .mockResolvedValueOnce(makeResponse(customer))
      .mockResolvedValueOnce(makeResponse({ ...customer, status: 'revoked' }))
      .mockResolvedValueOnce(makeResponse(accountant))
      .mockResolvedValueOnce(makeResponse(accountant))

    expect(await fetchOwnerCustomerRelation(1)).toEqual(customer)
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/customers/owner-relations/1', expect.objectContaining({
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await deleteOwnerCustomerRelation(1, 'cancel-pending', 'لغو مشتری ناموفق بود.')
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/customers/owner-relations/1?expected_action=cancel-pending',
      expect.objectContaining({ method: 'DELETE' }),
    )

    expect(await fetchOwnerAccountantRelation(8)).toEqual(accountant)
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/accountants/owner-relations/8', expect.objectContaining({
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))

    await deleteOwnerAccountantRelation(8, 'delete-account', 'حذف حسابدار ناموفق بود.')
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      4,
      '/api/accountants/owner-relations/8?expected_action=delete-account',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('rejects invalid relation-list payloads instead of converting failures to true empty lists', async () => {
    apiFetchMock
      .mockResolvedValueOnce(makeResponse({ detail: 'not-an-array' }))
      .mockResolvedValueOnce(makeResponse(null))

    await expect(fetchOwnerCustomerRelations()).rejects.toThrow('پاسخ لیست مشتریان معتبر نبود.')
    await expect(fetchOwnerAccountantRelations()).rejects.toThrow('پاسخ لیست حسابداران معتبر نبود.')
  })

  it('rejects invalid owner relation detail payloads', async () => {
    apiFetchMock
      .mockResolvedValueOnce(makeResponse([]))
      .mockResolvedValueOnce(makeResponse(null))

    await expect(fetchOwnerCustomerRelation(1)).rejects.toThrow('پاسخ پرونده مشتری معتبر نبود.')
    await expect(fetchOwnerAccountantRelation(8)).rejects.toThrow('پاسخ پرونده حسابدار معتبر نبود.')
  })
})
