import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const tradingViewMocks = vi.hoisted(() => ({
  apiFetchJsonMock: vi.fn(),
  apiFetchMock: vi.fn(),
  wsConnectMock: vi.fn(),
  wsOnMock: vi.fn((_event: string, _handler: (payload: unknown) => void) => {}),
  wsOffMock: vi.fn((_event: string, _handler: (payload: unknown) => void) => {}),
}))

vi.mock('../composables/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: tradingViewMocks.wsConnectMock,
    on: tradingViewMocks.wsOnMock,
    off: tradingViewMocks.wsOffMock,
  }),
}))

vi.mock('../composables/useTradingSort', async () => {
  const { computed, ref } = await import('vue')
  return {
    useTradingSort: (offers: { value: any[] }) => {
      const sortCommodity = ref('')
      const sortDirection = ref<'none' | 'asc' | 'desc'>('none')
      return {
        filterType: ref<'all' | 'buy' | 'sell'>('all'),
        sortCommodity,
        sortDirection,
        showSortPanel: ref(false),
        filteredOffers: computed(() => offers.value),
        toggleSort: vi.fn((commodityName: string) => {
          if (sortCommodity.value !== commodityName) {
            sortCommodity.value = commodityName
            sortDirection.value = 'asc'
          } else if (sortDirection.value === 'asc') {
            sortDirection.value = 'desc'
          } else {
            sortCommodity.value = ''
            sortDirection.value = 'none'
          }
        }),
        clearSort: vi.fn(() => {
          sortCommodity.value = ''
          sortDirection.value = 'none'
        }),
      }
    },
  }
})

vi.mock('../utils/auth', () => ({
  apiFetchJson: tradingViewMocks.apiFetchJsonMock,
  apiFetch: tradingViewMocks.apiFetchMock,
}))

const offersFixture = [
  {
    id: 101,
    user_id: 9,
    user_account_name: 'seller-user',
    is_own_offer: false,
    offer_type: 'sell',
    commodity_id: 1,
    commodity_name: 'سکه',
    quantity: 20,
    remaining_quantity: 20,
    price: 123456,
    is_wholesale: true,
    lot_sizes: null,
    notes: 'فروش فوری',
    status: 'active',
    channel_message_id: null,
    created_at: 'امروز',
  },
]

const myOffersFixture = [
  {
    id: 202,
    user_id: 7,
    user_account_name: 'my-user',
    is_own_offer: true,
    offer_type: 'buy',
    commodity_id: 2,
    commodity_name: 'طلا',
    quantity: 15,
    remaining_quantity: 10,
    price: 654321,
    is_wholesale: false,
    lot_sizes: [10, 5],
    notes: 'لفظ من',
    status: 'active',
    channel_message_id: null,
    created_at: 'دیروز',
  },
]

const myTradesFixture = [
  {
    id: 301,
    trade_number: 10001,
    trade_type: 'buy',
    commodity_name: 'سکه',
    quantity: 5,
    price: 120000,
    trade_path_kind: 'owner_customer_tier2',
    trade_path_summary: 'مالک ↔ مشتری سطح ۲',
    offer_user_id: 9,
    offer_user_name: 'حسابدار فروش',
    offer_user_profile_user_id: 77,
    offer_user_profile_account_name: 'owner-77',
    offer_user_resolved_from_accountant_id: 9,
    offer_user_highlight_accountant_user_id: 9,
    offer_user_highlight_accountant_relation_display_name: 'حسابدار فروش',
    counterparty_user_id: 77,
    counterparty_name: 'حسابدار فروش',
    counterparty_profile_user_id: 77,
    counterparty_profile_account_name: 'owner-77',
    counterparty_highlight_accountant_user_id: 9,
    counterparty_highlight_accountant_relation_display_name: 'حسابدار فروش',
    customer_context_visible: true,
    customer_context_user_id: 55,
    customer_context_management_name: 'مشتری واسط',
    customer_context_tier: 'tier2',
    responder_user_id: 7,
    responder_user_name: 'my-user',
    created_at: 'امروز',
  },
]

const commoditiesFixture = [
  { id: 1, name: 'سکه' },
  { id: 2, name: 'طلای آب‌شده' },
]

const tradingSettingsFixture = {
  offer_min_quantity: 1,
  offer_max_quantity: 1000,
  lot_min_size: 5,
  lot_max_count: 5,
  offer_expiry_minutes: 60,
}

function buildJsonResponse() {
  tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersFixture
    if (path === '/api/offers/my?since_hours=2') return myOffersFixture
    if (path === '/api/trades/my') return myTradesFixture
    if (path === '/api/commodities/') return commoditiesFixture
    if (path === '/api/trading-settings/') return tradingSettingsFixture
    if (path === '/api/offers/parse') {
      return {
        success: true,
        data: {
          trade_type: 'buy',
          commodity_id: 2,
          commodity_name: 'طلای آب‌شده',
          quantity: 50,
          price: 222222,
          is_wholesale: true,
          lot_sizes: null,
          notes: 'از متن',
        },
      }
    }
    return null
  })
}

function responseOf(data: unknown) {
  return {
    ok: true,
    json: async () => data,
  }
}

function errorResponse(status: number, data: unknown) {
  return {
    ok: false,
    status,
    json: async () => data,
  }
}

async function mountTradingView(overrides: Record<string, unknown> = {}) {
  const TradingView = (await import('./TradingView.vue')).default
  return mount(TradingView, {
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
      user: { id: 7, account_name: 'my-user' },
      ...overrides,
    },
    global: {
      stubs: {
        LoadingSkeleton: { template: '<div class="loading-skeleton-stub"></div>' },
        TradeLotSuggestionAlert: {
          props: ['show', 'lotSummary', 'availableLots'],
          template: `
            <div v-if="show" class="trade-lot-suggestion-alert-stub">
              <div class="trade-lot-summary">{{ lotSummary }}</div>
              <button
                v-if="Array.isArray(availableLots) && availableLots.length"
                class="accept-suggested-lot"
                @click="$emit('select-lot', availableLots[0])"
              >accept</button>
              <button class="close-suggestion" @click="$emit('close')">close</button>
            </div>
          `,
        },
      },
    },
  })
}

describe('TradingView.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    tradingViewMocks.apiFetchJsonMock.mockReset()
    tradingViewMocks.apiFetchMock.mockReset()
    tradingViewMocks.wsConnectMock.mockReset()
    tradingViewMocks.wsOnMock.mockReset()
    tradingViewMocks.wsOffMock.mockReset()
    buildJsonResponse()
    tradingViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && options?.method === 'POST') return responseOf({ success: true, id: 999 })
      return responseOf({})
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads initial offers data and switches across all three tabs', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/', {})
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/commodities/', {})
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/trading-settings/', {})
    expect(wrapper.text()).toContain('سکه')
    expect(wrapper.text()).toContain('فروش فوری')

    const tabs = wrapper.findAll('.tabs button')
    await tabs[1]!.trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/my?since_hours=2', {})
    expect(wrapper.text()).toContain('لفظ من')

    await tabs[2]!.trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/trades/my', {})
    expect(wrapper.text()).toContain('10001')
    expect(wrapper.text()).toContain('120,000')
    expect(wrapper.text()).toContain('مالک ↔ مشتری سطح ۲')
    expect(wrapper.text()).toContain('مشتری واسط')

    wrapper.unmount()
  })

  it('navigates my-trades counterpart links through relation-aware profile metadata', async () => {
    const wrapper = await mountTradingView({ initialTab: 'my_trades' })
    await flushPromises()

    await wrapper.get('.trade-card .profile-link').trigger('click')

    expect(wrapper.emitted('navigate')?.[0]).toEqual([
      'public_profile',
      {
        id: 77,
        account_name: 'owner-77',
        highlight_accountant_user_id: 9,
        highlight_accountant_relation_display_name: 'حسابدار فروش',
      },
    ])

    wrapper.unmount()
  })

  it('hides my-trades counterparty and customer relationship details for customer viewers', async () => {
    const wrapper = await mountTradingView({
      initialTab: 'my_trades',
      user: { id: 7, account_name: 'my-user', is_customer: true, customer_tier: 'tier2' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('10001')
    expect(wrapper.text()).toContain('120,000')
    expect(wrapper.text()).not.toContain('طرف معامله')
    expect(wrapper.text()).not.toContain('مالک ↔ مشتری سطح ۲')
    expect(wrapper.text()).not.toContain('مشتری واسط')
    expect(wrapper.find('.trade-card .profile-link').exists()).toBe(false)

    wrapper.unmount()
  })

  it('upserts rich trade:created payloads without reloading and preserves relation-aware profile targets', async () => {
    const wrapper = await mountTradingView({ initialTab: 'my_trades' })
    await flushPromises()

    const handlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][])
    tradingViewMocks.apiFetchJsonMock.mockClear()

    handlers.get('trade:created')?.({
      id: 999,
      trade_number: 19999,
      offer_id: 101,
      commodity_id: 1,
      trade_type: 'buy',
      commodity_name: 'سکه',
      quantity: 8,
      price: 222000,
      status: 'completed',
      trade_path_kind: 'owner_customer_tier1',
      trade_path_summary: 'مالک ↔ مشتری سطح ۱',
      offer_user_id: 90,
      offer_user_name: 'حسابدار فروش جدید',
      offer_user_profile_user_id: 77,
      offer_user_profile_account_name: 'owner-77',
      offer_user_highlight_accountant_user_id: 90,
      offer_user_highlight_accountant_relation_display_name: 'حسابدار فروش جدید',
      responder_user_id: 7,
      responder_user_name: 'my-user',
      created_at: 'همین الان',
    })
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).not.toHaveBeenCalledWith('/api/trades/my', {})
    expect(wrapper.text()).toContain('19999')
    expect(wrapper.text()).toContain('حسابدار فروش جدید')
    expect(wrapper.text()).toContain('مالک ↔ مشتری سطح ۱')

    await wrapper.get('.trade-card .profile-link').trigger('click')
    expect(wrapper.emitted('navigate')?.at(-1)).toEqual([
      'public_profile',
      {
        id: 77,
        account_name: 'owner-77',
        highlight_accountant_user_id: 90,
        highlight_accountant_relation_display_name: 'حسابدار فروش جدید',
      },
    ])

    wrapper.unmount()
  })

  it('upserts recipient-specific trade:created payloads for accountant audiences without polling', async () => {
    const wrapper = await mountTradingView({ initialTab: 'my_trades', user: { id: 701, account_name: 'accountant-user' } })
    await flushPromises()

    const handlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][])
    tradingViewMocks.apiFetchJsonMock.mockClear()

    handlers.get('trade:created')?.({
      id: 1001,
      trade_number: 21001,
      offer_id: 111,
      commodity_id: 1,
      trade_type: 'sell',
      commodity_name: 'سکه',
      quantity: 2,
      price: 333000,
      status: 'completed',
      recipient_specific: true,
      audience_user_ids: [701],
      trade_path_kind: 'owner_customer_tier1',
      trade_path_summary: 'مالک ↔ مشتری سطح ۱',
      offer_user_id: 7,
      offer_user_name: 'مالک اصلی',
      responder_user_id: 55,
      responder_user_name: 'خریدار بیرونی',
      counterparty_user_id: 55,
      counterparty_name: 'خریدار بیرونی',
      customer_context_visible: true,
      customer_context_management_name: 'مشتری حسابدار',
      customer_context_tier: 'tier1',
      created_at: 'همین الان',
    })
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).not.toHaveBeenCalledWith('/api/trades/my', {})
    expect(wrapper.text()).toContain('21001')
    expect(wrapper.text()).toContain('خریدار بیرونی')
    expect(wrapper.text()).toContain('مشتری حسابدار')

    wrapper.unmount()
  })

  it('ignores unrelated trade:created payloads without forcing a my-trades reload', async () => {
    const wrapper = await mountTradingView({ initialTab: 'my_trades' })
    await flushPromises()

    const handlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][])
    tradingViewMocks.apiFetchJsonMock.mockClear()

    handlers.get('trade:created')?.({
      id: 1002,
      trade_number: 22002,
      offer_id: 112,
      commodity_id: 2,
      trade_type: 'buy',
      commodity_name: 'طلا',
      quantity: 4,
      price: 444000,
      status: 'completed',
      audience_user_ids: [88, 99],
      offer_user_id: 88,
      offer_user_name: 'کاربر دیگر',
      responder_user_id: 99,
      responder_user_name: 'کاربر دیگر ۲',
      created_at: 'همین الان',
    })
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).not.toHaveBeenCalledWith('/api/trades/my', {})
    expect(wrapper.text()).not.toContain('22002')

    wrapper.unmount()
  })

  it('ignores malformed realtime payloads and skips updates when viewer id is invalid', async () => {
    const wrapper = await mountTradingView({ initialTab: 'my_trades' })
    await flushPromises()

    const initialTradeCards = wrapper.findAll('.trade-card').length
    const handlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][])

    handlers.get('trade:created')?.(null)
    handlers.get('trade:created')?.({
      id: 'bad-id',
      trade_number: 21000,
      trade_type: 'buy',
      commodity_name: 'سکه',
      quantity: 1,
      price: 100000,
      offer_user_id: 7,
      responder_user_id: 8,
      created_at: 'همین الان',
    })
    await flushPromises()

    expect(wrapper.findAll('.trade-card')).toHaveLength(initialTradeCards)

    const invalidViewerWrapper = await mountTradingView({
      initialTab: 'my_trades',
      user: { id: Number.NaN, account_name: 'invalid-user' } as any,
    })
    await flushPromises()

    const invalidViewerHandlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][])
    invalidViewerHandlers.get('trade:created')?.({
      id: 22000,
      trade_number: 22000,
      trade_type: 'buy',
      commodity_name: 'سکه',
      quantity: 1,
      price: 100000,
      offer_user_id: 7,
      responder_user_id: 8,
      created_at: 'همین الان',
    })
    await flushPromises()

    expect(invalidViewerWrapper.findAll('.trade-card')).toHaveLength(initialTradeCards)

    wrapper.unmount()
    invalidViewerWrapper.unmount()
  })

  it('merges sparse trade:created updates while preserving existing relation metadata', async () => {
    const wrapper = await mountTradingView({ initialTab: 'my_trades' })
    await flushPromises()

    const handlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][])
    tradingViewMocks.apiFetchJsonMock.mockClear()

    handlers.get('trade:created')?.({
      id: 301,
      trade_number: 10001,
      trade_type: 'buy',
      commodity_name: 'سکه',
      quantity: 7,
      price: 130000,
      offer_user_id: 9,
      responder_user_id: 7,
      offer_user_name: null,
      counterparty_name: null,
      customer_context_visible: false,
      customer_context_management_name: null,
      customer_context_tier: null,
      trade_path_summary: null,
      created_at: 'همین الان',
    })
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).not.toHaveBeenCalledWith('/api/trades/my', {})
    expect(wrapper.text()).toContain('130,000')
    expect(wrapper.text()).toContain('حسابدار فروش')
    expect(wrapper.text()).toContain('مشتری واسط')

    wrapper.unmount()
  })

  it('shows unknown tier fallback text for non-standard customer tiers', async () => {
    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersFixture
      if (path === '/api/offers/my?since_hours=2') return myOffersFixture
      if (path === '/api/trades/my') {
        return [
          {
            ...myTradesFixture[0],
            customer_context_visible: true,
            customer_context_management_name: 'مشتری ناشناخته',
            customer_context_tier: 'mystery',
          },
        ]
      }
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      return null
    })

    const wrapper = await mountTradingView({ initialTab: 'my_trades' })
    await flushPromises()

    expect(wrapper.text()).toContain('سطح نامشخص')

    wrapper.unmount()
  })

  it('renders customer context and viewer-specific display pricing on active offers', async () => {
    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) {
        return [
          {
            ...offersFixture[0],
            price: 50000,
            viewer_effective_price: 49700,
            customer_badge_visible: true,
            customer_management_name: 'سینا',
            customer_tier: 'tier1',
          },
        ]
      }
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      if (path === '/api/offers/my?since_hours=2') return myOffersFixture
      if (path === '/api/trades/my') return myTradesFixture
      return null
    })

    const wrapper = await mountTradingView()
    await flushPromises()

    expect(wrapper.text()).toContain('49,700')
    expect(wrapper.text()).not.toContain('50,000')
    expect(wrapper.text()).toContain('مشتری')
    expect(wrapper.text()).toContain('سینا')
    expect(wrapper.text()).toContain('سطح 1')
  })

  it('surfaces loader errors for each tab and logs commodity/settings fetch failures', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) throw new Error('offers failed')
      if (path === '/api/offers/my?since_hours=2') throw new Error('my offers failed')
      if (path === '/api/trades/my') throw new Error('my trades failed')
      if (path === '/api/commodities/') throw new Error('commodities failed')
      if (path === '/api/trading-settings/') throw new Error('settings failed')
      return null
    })

    const wrapper = await mountTradingView()
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در دریافت لیست لفظ‌ها')
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load commodities', expect.any(Error))
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load settings', expect.any(Error))

    const tabs = wrapper.findAll('.tabs button')
    await tabs[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('خطا در دریافت لفظ‌های من')

    await tabs[2]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('خطا در دریافت صورت معاملات')

    consoleErrorSpy.mockRestore()
    wrapper.unmount()
  })

  it('keeps offer creation text-only and does not render wizard actions', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    expect(wrapper.find('.action-btn.buy').exists()).toBe(false)
    expect(wrapper.find('.action-btn.sell').exists()).toBe(false)
    expect(wrapper.find('.wizard-overlay').exists()).toBe(false)
    expect(wrapper.find('.text-offer-input').exists()).toBe(true)
    expect(wrapper.find('.send-btn').exists()).toBe(true)

    wrapper.unmount()
  })

  it('parses and submits a text offer from the bottom composer', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    tradingViewMocks.apiFetchJsonMock.mockClear()
    tradingViewMocks.apiFetchMock.mockClear()

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/parse', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ text: 'خرید طلای آب‌شده 50 عدد 222222' }),
    }))
    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'buy',
        commodity_id: 2,
        quantity: 50,
        price: 222222,
        is_wholesale: true,
        lot_sizes: null,
        notes: 'از متن',
        warning_acknowledged: false,
      }),
    }))
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('')

    wrapper.unmount()
  })

  it('returns a parsed preview back into the trading composer when the user chooses edit', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-edit').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('خرید طلای آب‌شده 50 عدد 222222: از متن')

    wrapper.unmount()
  })

  it('requires a second confirmation when the server returns a price warning', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    tradingViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && options?.method === 'POST') {
        const body = JSON.parse(String(options.body))
        if (!body.warning_acknowledged) {
          return errorResponse(409, {
            error_code: 'OFFER_PRICE_WARNING',
            detail: 'warning detail',
            warning: {
              error_code: 'OFFER_PRICE_WARNING',
              title: 'هشدار قیمت خرید',
              detail: 'قیمت خرید شما از بالاترین خرید فعال مشابه بالاتر است.',
              message: 'warning message',
              warning_type: 'buy_above_highest_active',
              reference_label: 'بالاترین قیمت خرید فعال',
              reference_price: 100300,
              proposed_price: 100500,
              difference_percent: 0.2,
            },
          })
        }
        return responseOf({ success: true, id: 1000 })
      }
      return responseOf({})
    })

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()
    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('در نرخ منصفانه لحاظ نخواهد شد')

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const postCalls = tradingViewMocks.apiFetchMock.mock.calls.filter(([path, options]) => path === '/api/offers/' && options?.method === 'POST')
    expect(postCalls).toHaveLength(2)
    expect(JSON.parse(String(postCalls[1]![1].body)).warning_acknowledged).toBe(true)

    wrapper.unmount()
  })

  it('opens the trade modal and executes a successful trade', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    await wrapper.find('.trade-btn.full-width').trigger('click')
    await flushPromises()

    expect(wrapper.find('.modal').exists()).toBe(true)
    expect(wrapper.text()).toContain('تعداد: 20')

    tradingViewMocks.apiFetchMock.mockClear()
    await wrapper.find('.confirm-trade-btn').trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/trades/', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        offer_id: 101,
        quantity: 20,
      }),
    }))
    expect(wrapper.find('.modal').exists()).toBe(false)

    wrapper.unmount()
  })

  it('expires active offers and shows a text-only retry note for expired offers', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const repeatedOffer = {
      ...myOffersFixture[0],
      id: 203,
      commodity_name: 'طلای آب‌شده',
      status: 'expired',
      created_at: 'سه روز پیش',
      notes: 'لفظ تکراری',
    }

    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersFixture
      if (path === '/api/offers/my?since_hours=2') return [myOffersFixture[0]]
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      if (path === '/api/offers/202' && options?.method === 'DELETE') return null
      return null
    })

    const wrapper = await mountTradingView({ initialTab: 'my_offers' })
    await flushPromises()

    tradingViewMocks.apiFetchJsonMock.mockClear()
    await wrapper.find('.expire-btn').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/202', { method: 'DELETE' })
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/my?since_hours=2', {})

    wrapper.unmount()

    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/my?since_hours=2') return [repeatedOffer]
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersFixture
      return null
    })

    const repeatedWrapper = await mountTradingView({ initialTab: 'my_offers' })
    await flushPromises()
  expect(repeatedWrapper.text()).toContain('ثبت مجدد فقط با متن')
  expect(repeatedWrapper.find('.repeat-btn').exists()).toBe(false)

    confirmSpy.mockRestore()
    repeatedWrapper.unmount()
  })

  it('shows empty states for initial my-offers and my-trades tabs', async () => {
    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersFixture
      if (path === '/api/offers/my?since_hours=2') return []
      if (path === '/api/trades/my') return []
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      return null
    })

    const myOffersWrapper = await mountTradingView({ initialTab: 'my_offers' })
    await flushPromises()
    expect(myOffersWrapper.text()).toContain('شما هیچ لفظی در ۲ ساعت اخیر نداشته‌اید.')
    myOffersWrapper.unmount()

    const myTradesWrapper = await mountTradingView({ initialTab: 'my_trades' })
    await flushPromises()
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/trades/my', {})
    expect(myTradesWrapper.text()).toContain('هنوز هیچ معامله‌ای انجام نداده‌اید.')
    myTradesWrapper.unmount()
  })

  it('shows a lot suggestion and can retry with the suggested amount', async () => {
    tradingViewMocks.apiFetchMock
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({
          error_code: 'TRADE_LOT_UNAVAILABLE',
          title: 'پیشنهاد معامله',
          intro_text: 'لات انتخابی شما دیگر در دسترس نیست.',
          offer_id: 101,
          offer_type: 'sell',
          offer_type_label: 'فروش',
          commodity_name: 'سکه',
          price: 123456,
          remaining_quantity: 20,
          lot_summary: '5 + 15',
          available_lots: [5, 15],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      })

    const wrapper = await mountTradingView()
    await flushPromises()

    await wrapper.find('.trade-btn.full-width').trigger('click')
    await flushPromises()
    await wrapper.find('.confirm-trade-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.trade-lot-suggestion-alert-stub').exists()).toBe(true)
    expect(wrapper.find('.trade-lot-summary').text()).toBe('5 + 15')

    await wrapper.find('.accept-suggested-lot').trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/api/trades/', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        offer_id: 101,
        quantity: 5,
      }),
    }))
    expect(wrapper.find('.trade-lot-suggestion-alert-stub').exists()).toBe(false)

    wrapper.unmount()
  })

  it('falls back when trade error payloads cannot be parsed', async () => {
    tradingViewMocks.apiFetchMock.mockResolvedValueOnce({
      ok: false,
      json: async () => {
        throw new Error('bad json')
      },
    })

    const wrapper = await mountTradingView()
    await flushPromises()

    await wrapper.find('.trade-btn.full-width').trigger('click')
    await flushPromises()
    await wrapper.find('.confirm-trade-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در انجام معامله')
    wrapper.unmount()
  })

  it('renders timer styles, sort state, and websocket/poll refresh branches', async () => {
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'))
    const nowSeconds = Math.floor(Date.now() / 1000)
    let offersState = [
      { ...offersFixture[0], id: 401, expires_at_ts: nowSeconds + 5400, remaining_quantity: 9, quantity: 9, price: 101000 },
      { ...offersFixture[0], id: 402, expires_at_ts: nowSeconds + 3600, remaining_quantity: 8, quantity: 8, price: 102000 },
      { ...offersFixture[0], id: 403, expires_at_ts: nowSeconds + 1800, remaining_quantity: 7, quantity: 7, price: 103000 },
      { ...offersFixture[0], id: 404, expires_at_ts: nowSeconds + 600, remaining_quantity: 6, quantity: 6, price: 104000 },
      { ...offersFixture[0], id: 405, is_own_offer: true, expires_at_ts: nowSeconds + 600, remaining_quantity: 5, quantity: 5, price: 105000 },
    ]
    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersState
      if (path === '/api/offers/my?since_hours=2') return myOffersFixture
      if (path === '/api/trades/my') return myTradesFixture
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return { ...tradingSettingsFixture, offer_expiry_minutes: 90 }
      return null
    })

    const wrapper = await mountTradingView()
    await flushPromises()

    expect(wrapper.findAll('.timer-bar-track')).toHaveLength(5)
    expect(wrapper.findAll('.offer-card.timer-critical')).toHaveLength(2)
    expect((wrapper.find('.offer-card.has-timer').element as HTMLElement).style.getPropertyValue('--timer-color')).toContain('hsl(')

    await wrapper.find('.sort-toggle-btn').trigger('click')
    expect(wrapper.find('.sort-panel').exists()).toBe(true)
    await wrapper.findAll('.sort-chip')[0]!.trigger('click')
    expect(wrapper.find('.sort-hint').text()).toContain('ارزان‌ترین اول')
    await wrapper.findAll('.sort-chip')[0]!.trigger('click')
    expect(wrapper.find('.sort-hint').text()).toContain('گران‌ترین اول')
    await wrapper.find('.sort-clear-btn').trigger('click')
    expect(wrapper.find('.sort-hint').exists()).toBe(false)

    const handlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][]) 
    handlers.get('offer:expired')?.({ id: 401 })
    await flushPromises()
    expect(wrapper.text()).not.toContain('101,000')

    tradingViewMocks.apiFetchJsonMock.mockClear()
    handlers.get('offer:created')?.({ id: 999 })
    handlers.get('offer:updated')?.({ id: 402 })
    handlers.get('trade:created')?.({ id: 1 })
    await flushPromises()
    expect(tradingViewMocks.apiFetchJsonMock.mock.calls.filter(([path]) => path === '/api/offers/').length).toBeGreaterThanOrEqual(3)

    await wrapper.findAll('.tabs button')[1]!.trigger('click')
    await flushPromises()
    tradingViewMocks.apiFetchJsonMock.mockClear()
    handlers.get('offer:updated')?.({ id: 202 })
    await flushPromises()
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/my?since_hours=2', {})

    await wrapper.findAll('.tabs button')[2]!.trigger('click')
    await flushPromises()
    tradingViewMocks.apiFetchJsonMock.mockClear()
    handlers.get('trade:created')?.({ id: 301 })
    vi.advanceTimersByTime(3000)
    await flushPromises()
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/trades/my', {})

    offersState = []
    await wrapper.findAll('.tabs button')[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('هیچ لفظ فعالی وجود ندارد')

    wrapper.unmount()
    expect(tradingViewMocks.wsOffMock).toHaveBeenCalledWith('offer:expired', expect.any(Function))
  })

  it('surfaces parse failures, non-warning publish failures, and preview cancel state', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/parse' && options?.method === 'POST') {
        return { success: false, error: 'متن قابل پردازش نیست' }
      }
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersFixture
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      return null
    })

    await wrapper.find('.text-offer-input').setValue('متن ناقص')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.parse-error').text()).toBe('متن قابل پردازش نیست')

    tradingViewMocks.apiFetchJsonMock.mockImplementationOnce(async () => {
      throw new Error('parse exploded')
    })
    await wrapper.find('.text-offer-input').setValue('متن خراب')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.parse-error').text()).toBe('parse exploded')

    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/parse' && options?.method === 'POST') {
        return {
          success: true,
          data: {
            trade_type: 'sell',
            commodity_name: 'سکه',
            commodity_id: 1,
            quantity: 2,
            price: 120000,
            is_wholesale: false,
            lot_sizes: [1, 1],
            notes: null,
          },
        }
      }
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersFixture
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      return null
    })
    tradingViewMocks.apiFetchMock.mockResolvedValueOnce(errorResponse(422, { detail: 'قیمت نامعتبر است' }))

    await wrapper.find('.text-offer-input').setValue('فروش سکه 2 عدد 120000')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()
    expect(wrapper.find('.offer-preview-error').text()).toBe('قیمت نامعتبر است')

    await wrapper.find('.offer-preview-cancel').trigger('click')
    await flushPromises()
    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)

    wrapper.unmount()
  })

  it('keeps lot suggestions synchronized with changing offers and closes stale suggestions', async () => {
    let offersState = [{
      ...offersFixture[0],
      is_wholesale: false,
      lot_sizes: [5, 15],
      remaining_quantity: 20,
      quantity: 20,
      expires_at_ts: Math.floor(Date.now() / 1000) + 2,
    }]
    tradingViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/' && (!options?.method || options.method === 'GET')) return offersState
      if (path === '/api/commodities/') return commoditiesFixture
      if (path === '/api/trading-settings/') return tradingSettingsFixture
      return null
    })
    tradingViewMocks.apiFetchMock.mockResolvedValue({
      ok: false,
      json: async () => ({
        error_code: 'TRADE_LOT_UNAVAILABLE',
        offer_id: 101,
        offer_type: 'sell',
        commodity_name: 'سکه',
        price: 123456,
        remaining_quantity: 20,
        available_lots: [5, 15],
      }),
    })

    const wrapper = await mountTradingView()
  const handlers = new Map<string, (payload: unknown) => void>(tradingViewMocks.wsOnMock.mock.calls as [string, (payload: unknown) => void][]) 
    await flushPromises()
    await wrapper.findAll('.trade-btn')[0]!.trigger('click')
    await wrapper.find('.confirm-trade-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.trade-lot-summary').text()).toBe('15 + 5')

    handlers.get('offer:updated')?.({ id: 101 })
    await flushPromises()
    expect(wrapper.find('.trade-lot-summary').text()).toBe('15 + 5')

    offersState = [{
      ...offersState[0]!,
      is_wholesale: false,
      remaining_quantity: 10,
      quantity: 20,
      lot_sizes: [4, 6],
    }]
    handlers.get('offer:updated')?.({ id: 101 })
    await flushPromises()
    expect(wrapper.find('.trade-lot-summary').text()).toBe('10 + 6 + 4')

    offersState = [{
      ...offersState[0]!,
      remaining_quantity: 0,
      quantity: 20,
      lot_sizes: [5, 15],
    }]
    handlers.get('offer:updated')?.({ id: 101 })
    await flushPromises()
    expect(wrapper.find('.trade-lot-suggestion-alert-stub').exists()).toBe(false)

    tradingViewMocks.apiFetchMock.mockResolvedValueOnce({
      ok: false,
      json: async () => ({
        error_code: 'TRADE_LOT_UNAVAILABLE',
        offer_id: 101,
        offer_type: 'sell',
        commodity_name: 'سکه',
        price: 123456,
        remaining_quantity: 20,
        available_lots: [5, 15],
      }),
    })
    offersState = [{
      ...offersFixture[0],
      is_wholesale: false,
      lot_sizes: [5, 15],
      remaining_quantity: 20,
      quantity: 20,
      expires_at_ts: Math.floor(Date.now() / 1000) + 1,
    }]
    handlers.get('offer:updated')?.({ id: 101 })
    await flushPromises()
    await wrapper.findAll('.trade-btn')[0]!.trigger('click')
    await wrapper.find('.confirm-trade-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.trade-lot-suggestion-alert-stub').exists()).toBe(true)

    vi.advanceTimersByTime(2000)
    await flushPromises()
    expect(wrapper.find('.trade-lot-suggestion-alert-stub').exists()).toBe(false)
    expect(wrapper.findAll('.offer-card')).toHaveLength(0)

    offersState = []
    handlers.get('offer:updated')?.({ id: 101 })
    await flushPromises()
    expect(wrapper.find('.trade-lot-suggestion-alert-stub').exists()).toBe(false)

    wrapper.unmount()
  })
})
