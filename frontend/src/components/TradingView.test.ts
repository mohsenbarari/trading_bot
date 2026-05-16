import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const tradingViewMocks = vi.hoisted(() => ({
  apiFetchJsonMock: vi.fn(),
  apiFetchMock: vi.fn(),
  wsConnectMock: vi.fn(),
  wsOnMock: vi.fn(),
  wsOffMock: vi.fn(),
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
    useTradingSort: (offers: { value: any[] }) => ({
      filterType: ref<'all' | 'buy' | 'sell'>('all'),
      sortCommodity: ref(''),
      sortDirection: ref<'none' | 'asc' | 'desc'>('none'),
      showSortPanel: ref(false),
      filteredOffers: computed(() => offers.value),
      toggleSort: vi.fn(),
      clearSort: vi.fn(),
    }),
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
    offer_user_id: 9,
    offer_user_name: 'seller-user',
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
          quantity: 50,
          price: 222222,
          is_wholesale: true,
          lot_sizes: null,
          notes: 'از متن',
        },
      }
    }
    if (path === '/api/offers/' && options?.method === 'POST') {
      return { success: true, id: 999 }
    }
    return null
  })
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
    tradingViewMocks.apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({}),
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

    wrapper.unmount()
  })

  it('walks the wholesale create-offer wizard and submits the previewed offer', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    tradingViewMocks.apiFetchJsonMock.mockClear()

    await wrapper.find('.action-btn.buy').trigger('click')
    await flushPromises()
    await wrapper.findAll('.commodity-btn')[0]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.qty-btn')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('.lot-btn.wholesale').trigger('click')
    await flushPromises()

    await wrapper.find('.price-input').setValue('123456')
    await wrapper.find('.confirm-btn').trigger('click')
    await flushPromises()

    await wrapper.find('.notes-input').setValue('یادداشت تست')
    await wrapper.find('.confirm-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.preview-card').text()).toContain('🟢 خرید')
    expect(wrapper.find('.preview-card').text()).toContain('سکه')
    expect(wrapper.find('.preview-card').text()).toContain('123,456')

    await wrapper.find('.submit-btn').trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'buy',
        commodity_id: 1,
        commodity_name: 'سکه',
        quantity: 10,
        price: 123456,
        is_wholesale: true,
        lot_sizes: null,
        notes: 'یادداشت تست',
        republished_from_id: null,
      }),
    }))

    wrapper.unmount()
  })

  it('validates retail lot input before submitting a retail offer', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    tradingViewMocks.apiFetchJsonMock.mockClear()

    await wrapper.find('.action-btn.sell').trigger('click')
    await flushPromises()
    await wrapper.findAll('.commodity-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.qty-btn')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('.lot-btn.retail').trigger('click')
    await flushPromises()

    await wrapper.find('.lot-input').setValue('4 3')
    await wrapper.find('.confirm-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.wizard-error').text()).toContain('مجموع (7) با تعداد (10) برابر نیست')

    await wrapper.find('.lot-input').setValue('4 6')
    await wrapper.find('.confirm-btn').trigger('click')
    await flushPromises()

    await wrapper.find('.price-input').setValue('333333')
    await wrapper.find('.confirm-btn').trigger('click')
    await flushPromises()
    await wrapper.find('.confirm-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.preview-card').text()).toContain('خُرد')
    expect(wrapper.find('.preview-card').text()).toContain('4')
    expect(wrapper.find('.preview-card').text()).toContain('6')

    await wrapper.find('.submit-btn').trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'sell',
        commodity_id: 2,
        commodity_name: 'طلای آب‌شده',
        quantity: 10,
        price: 333333,
        is_wholesale: false,
        lot_sizes: [4, 6],
        notes: '',
        republished_from_id: null,
      }),
    }))

    wrapper.unmount()
  })

  it('parses and submits a text offer from the bottom composer', async () => {
    const wrapper = await mountTradingView()
    await flushPromises()

    tradingViewMocks.apiFetchJsonMock.mockClear()

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/parse', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ text: 'خرید طلای آب‌شده 50 عدد 222222' }),
    }))
    expect(tradingViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'buy',
        commodity_id: 2,
        quantity: 50,
        price: 222222,
        is_wholesale: true,
        lot_sizes: null,
        notes: 'از متن',
      }),
    }))
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('')

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

  it('expires active offers and reopens expired offers from the my-offers tab', async () => {
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
    await repeatedWrapper.find('.repeat-btn').trigger('click')
    await flushPromises()

    expect(repeatedWrapper.find('.preview-card').text()).toContain('طلای آب‌شده')
    expect(repeatedWrapper.find('.preview-card').text()).toContain('لفظ تکراری')

    confirmSpy.mockRestore()
    repeatedWrapper.unmount()
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
})