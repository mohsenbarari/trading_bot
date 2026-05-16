import { computed, ref } from 'vue'
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
    filterType: ref<'all' | 'buy' | 'sell'>('all'),
    sortCommodity: ref(''),
    sortDirection: ref<'none' | 'asc' | 'desc'>('none'),
    showSortPanel: ref(false),
    filteredOffers: computed(() => offers.value),
    toggleSort: marketViewMocks.toggleSortMock,
    clearSort: marketViewMocks.clearSortMock,
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
      if (path === '/api/offers/' && options?.method === 'POST') {
        return { success: true, id: 1001 }
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

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/parse', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ text: 'خرید طلای آب‌شده 50 عدد 222222' }),
    }))
    expect(marketViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'buy',
        commodity_id: 2,
        quantity: 50,
        price: 222222,
        is_wholesale: true,
        lot_sizes: null,
        notes: 'از متن بازار',
      }),
    }))
    expect((wrapper.find('.text-offer-input').element as HTMLInputElement).value).toBe('')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('runs the wholesale create-offer wizard and submits the final offer', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchJsonMock.mockClear()

    await wrapper.find('.create-btn.buy').trigger('click')
    await flushPromises()
    await wrapper.findAll('.wizard-btn-outline')[0]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.wizard-btn-quick')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('.lot-type-btn.wholesale').trigger('click')
    await flushPromises()
    await wrapper.find('.wizard-input.big').setValue('123456')
    await wrapper.find('.wizard-submit-btn').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'buy',
        commodity_id: 1,
        commodity_name: 'سکه',
        quantity: 10,
        price: 123456,
        is_wholesale: true,
        lot_sizes: null,
        notes: null,
        republished_from_id: null,
      }),
    }))

    wrapper.unmount()
  })

  it('validates retail lot input before submitting a retail market offer', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchJsonMock.mockClear()

    await wrapper.find('.create-btn.sell').trigger('click')
    await flushPromises()
    await wrapper.findAll('.wizard-btn-outline')[1]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.wizard-btn-quick')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('.lot-type-btn.retail').trigger('click')
    await flushPromises()

    await wrapper.find('.wizard-input').setValue('3 3')
    await wrapper.find('.wizard-primary-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.parse-error').text()).toContain('مجموع (6) با تعداد (10) برابر نیست')

    await wrapper.find('.wizard-input').setValue('4 6')
    await wrapper.find('.wizard-primary-btn').trigger('click')
    await flushPromises()
    await wrapper.find('.wizard-input.big').setValue('333333')
    await wrapper.find('.wizard-submit-btn').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        offer_type: 'sell',
        commodity_id: 2,
        commodity_name: 'طلای آب‌شده',
        quantity: 10,
        price: 333333,
        is_wholesale: false,
        lot_sizes: [4, 6],
        notes: null,
        republished_from_id: null,
      }),
    }))

    wrapper.unmount()
  })
})