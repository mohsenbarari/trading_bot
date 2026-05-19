import { computed, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const marketViewMocks = vi.hoisted(() => ({
  offersRef: null as any,
  isLoadingRef: null as any,
  fetchOffersMock: vi.fn(),
  startPollingMock: vi.fn(),
  stopPollingMock: vi.fn(),
  toggleSortMock: vi.fn(),
  clearSortMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  apiFetchMock: vi.fn(),
  apiFetchJsonMock: vi.fn(),
}))

vi.mock('../composables/useOffers', () => ({
  useOffers: () => ({
    offers: marketViewMocks.offersRef,
    isLoading: marketViewMocks.isLoadingRef,
    fetchOffers: marketViewMocks.fetchOffersMock,
    startPolling: marketViewMocks.startPollingMock,
    stopPolling: marketViewMocks.stopPollingMock,
  }),
}))

vi.mock('../composables/useTradingSort', () => ({
  useTradingSort: (offers: { value: any[] }) => ({
    ...(function () {
      const filterType = ref<'all' | 'buy' | 'sell'>('all')
      const sortCommodity = ref('')
      const sortDirection = ref<'none' | 'asc' | 'desc'>('none')
      const showSortPanel = ref(false)

      return {
        filterType,
        sortCommodity,
        sortDirection,
        showSortPanel,
        filteredOffers: computed(() => offers.value),
        toggleSort: (commodityName: string) => {
          marketViewMocks.toggleSortMock(commodityName)
          if (sortCommodity.value !== commodityName) {
            sortCommodity.value = commodityName
            sortDirection.value = 'asc'
            return
          }
          if (sortDirection.value === 'asc') {
            sortDirection.value = 'desc'
            return
          }
          sortCommodity.value = ''
          sortDirection.value = 'none'
        },
        clearSort: () => {
          marketViewMocks.clearSortMock()
          sortCommodity.value = ''
          sortDirection.value = 'none'
        },
      }
    })(),
  }),
}))

vi.mock('../composables/useBackButton', () => ({
  pushBackState: marketViewMocks.pushBackStateMock,
  popBackState: marketViewMocks.popBackStateMock,
  clearBackStack: marketViewMocks.clearBackStackMock,
}))

vi.mock('../utils/auth', () => ({
  apiFetch: marketViewMocks.apiFetchMock,
  apiFetchJson: marketViewMocks.apiFetchJsonMock,
}))

const offersFixture = [
  {
    id: 1,
    offer_type: 'sell',
    commodity_name: 'سکه',
    remaining_quantity: 20,
    quantity: 20,
    price: 123456,
    is_own_offer: false,
  },
]

const commoditiesFixture = [
  { id: 1, name: 'سکه' },
  { id: 2, name: 'طلای آب‌شده' },
]

const settingsFixture = {
  offer_min_quantity: 1,
  offer_max_quantity: 1000,
  lot_min_size: 5,
  lot_max_count: 5,
  offer_expiry_minutes: 45,
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

async function mountMarketView() {
  const MarketView = (await import('./MarketView.vue')).default
  return mount(MarketView, {
    global: {
      stubs: {
        OffersList: {
          props: ['offers', 'loading', 'expiryMinutes', 'currentUserId'],
          template: `
            <div class="offers-list-stub">
              <div class="offers-count">{{ offers.length }}</div>
              <div class="offers-expiry">{{ expiryMinutes }}</div>
              <div class="offers-user-id">{{ currentUserId }}</div>
              <button class="emit-trade-completed" @click="$emit('trade-completed')">emit</button>
            </div>
          `,
        },
      },
    },
  })
}

describe('MarketView.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    marketViewMocks.offersRef = ref(offersFixture)
    marketViewMocks.isLoadingRef = ref(false)
    marketViewMocks.fetchOffersMock.mockReset()
    marketViewMocks.startPollingMock.mockReset()
    marketViewMocks.stopPollingMock.mockReset()
    marketViewMocks.toggleSortMock.mockReset()
    marketViewMocks.clearSortMock.mockReset()
    marketViewMocks.pushBackStateMock.mockReset()
    marketViewMocks.popBackStateMock.mockReset()
    marketViewMocks.clearBackStackMock.mockReset()
    marketViewMocks.apiFetchMock.mockReset()
    marketViewMocks.apiFetchJsonMock.mockReset()

    marketViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/auth/me') return responseOf({ id: 77 })
      if (path === '/api/offers/') return responseOf({ success: true, id: 1001 })
      return responseOf(null)
    })

    marketViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
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
            notes: 'از متن بازار',
          },
        }
      }
      return null
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads market dependencies, wires OffersList props, and refreshes from the child event', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()
    expect(marketViewMocks.startPollingMock).toHaveBeenCalled()
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/')
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/')
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(wrapper.find('.offers-count').text()).toBe('1')
    expect(wrapper.find('.offers-expiry').text()).toBe('45')
    expect(wrapper.find('.offers-user-id').text()).toBe('77')

    await wrapper.find('.sort-toggle-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.commodity-btn')[0]!.trigger('click')
    expect(marketViewMocks.toggleSortMock).toHaveBeenCalledWith('سکه')

    marketViewMocks.fetchOffersMock.mockClear()
    await wrapper.find('.emit-trade-completed').trigger('click')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
    expect(marketViewMocks.stopPollingMock).toHaveBeenCalled()
    expect(marketViewMocks.clearBackStackMock).toHaveBeenCalled()
  })

  it('parses and submits a text offer from the action bar', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchJsonMock.mockClear()
    marketViewMocks.fetchOffersMock.mockClear()
    marketViewMocks.apiFetchMock.mockClear()

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/parse', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ text: 'خرید طلای آب‌شده 50 عدد 222222' }),
    }))
    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'buy',
        commodity_id: 2,
        quantity: 50,
        price: 222222,
        is_wholesale: true,
        lot_sizes: null,
        notes: 'از متن بازار',
        warning_acknowledged: false,
      }),
    }))
    expect((wrapper.find('.text-offer-input').element as HTMLInputElement).value).toBe('')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('shows a warning and requires a second confirmation for suspicious prices', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/auth/me') return responseOf({ id: 77 })
      if (path === '/api/offers/' && options?.method === 'POST') {
        const body = JSON.parse(String(options.body))
        if (!body.warning_acknowledged) {
          return errorResponse(409, {
            error_code: 'OFFER_PRICE_WARNING',
            detail: 'warning detail',
            warning: {
              error_code: 'OFFER_PRICE_WARNING',
              title: 'هشدار قیمت فروش',
              detail: 'قیمت فروش شما از پایین‌ترین فروش فعال مشابه پایین‌تر است.',
              message: 'warning message',
              warning_type: 'sell_below_lowest_active',
              reference_label: 'پایین‌ترین قیمت فروش فعال',
              reference_price: 100000,
              proposed_price: 99900,
              difference_percent: 0.1,
            },
          })
        }
        return responseOf({ success: true, id: 1002 })
      }
      return responseOf(null)
    })

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()
    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('در نرخ منصفانه لحاظ نخواهد شد')

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const postCalls = marketViewMocks.apiFetchMock.mock.calls.filter(([path, options]) => path === '/api/offers/' && options?.method === 'POST')
    expect(postCalls).toHaveLength(2)
    expect(JSON.parse(String(postCalls[1]![1].body)).warning_acknowledged).toBe(true)

    wrapper.unmount()
  })

  it('keeps market offer creation text-only and hides wizard actions', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    expect(wrapper.find('.create-btn.buy').exists()).toBe(false)
    expect(wrapper.find('.create-btn.sell').exists()).toBe(false)
    expect(wrapper.find('.wizard-overlay').exists()).toBe(false)

    wrapper.unmount()
  })

  it('shows the default placeholder plus fetch-error logging when market dependencies fail', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/commodities/') throw new Error('commodities failed')
      if (path === '/api/trading-settings/') throw new Error('settings failed')
      if (path === '/api/auth/me') throw new Error('me failed')
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect((wrapper.find('.text-offer-input').element as HTMLInputElement).placeholder).toBe('مثال: خرید سکه 30 عدد 125000')
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load commodities', expect.any(Error))
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load settings', expect.any(Error))
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load current user', expect.any(Error))

    consoleErrorSpy.mockRestore()
    wrapper.unmount()
  })

  it('renders sort loading and toggles the sort icon/clear button states', async () => {
    let resolveCommodities: ((value: unknown) => void) | null = null
    marketViewMocks.apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/commodities/') {
        return new Promise((resolve) => {
          resolveCommodities = resolve
        }) as Promise<any>
      }
      if (path === '/api/trading-settings/') return Promise.resolve(responseOf(settingsFixture))
      if (path === '/api/auth/me') return Promise.resolve(responseOf({ id: 77 }))
      return Promise.resolve(responseOf(null))
    })

    const wrapper = await mountMarketView()
    await nextTick()

    await wrapper.find('.sort-toggle-btn').trigger('click')
    expect(wrapper.find('.panel-loading').exists()).toBe(true)

    if (!resolveCommodities) {
      throw new Error('Expected commodities resolver')
    }
    resolveCommodities(responseOf(commoditiesFixture))
    await flushPromises()

    await wrapper.findAll('.commodity-btn')[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.html()).toContain('lucide-arrow-up')
    expect(wrapper.find('.clear-sort-btn').exists()).toBe(true)

    await wrapper.findAll('.commodity-btn')[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.html()).toContain('lucide-arrow-down')

    wrapper.unmount()
  })

  it('surfaces parse/publish failures and clears preview state on cancel', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    marketViewMocks.apiFetchJsonMock.mockResolvedValueOnce({ success: false, error: 'متن نامعتبر است' })
    await wrapper.find('.text-offer-input').setValue('متن نامعتبر')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.parse-error').text()).toBe('متن نامعتبر است')

    marketViewMocks.apiFetchJsonMock.mockRejectedValueOnce(new Error('parser exploded'))
    await wrapper.find('.text-offer-input').setValue('explode')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.parse-error').text()).toBe('parser exploded')

    marketViewMocks.apiFetchJsonMock.mockResolvedValueOnce({
      success: true,
      data: {
        trade_type: 'sell',
        commodity_id: 1,
        commodity_name: 'سکه',
        quantity: 2,
        price: 120000,
        is_wholesale: false,
        lot_sizes: [1, 1],
        notes: null,
      },
    })
    marketViewMocks.apiFetchMock.mockResolvedValueOnce(errorResponse(422, { detail: 'قیمت نامعتبر است' }))

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

})