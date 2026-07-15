import { computed, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const marketViewMocks = vi.hoisted(() => ({
  offersRef: null as any,
  isLoadingRef: null as any,
  isLoadingMoreRef: null as any,
  errorRef: null as any,
  paginationErrorRef: null as any,
  hasMoreRef: null as any,
  fetchOffersMock: vi.fn(),
  loadMoreOffersMock: vi.fn(),
  setFiltersMock: vi.fn(),
  startPollingMock: vi.fn(),
  stopPollingMock: vi.fn(),
  toggleSortMock: vi.fn(),
  clearSortMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  apiFetchMock: vi.fn(),
  apiFetchJsonMock: vi.fn(),
  wsHandlers: new Map<string, Set<(data: any) => void>>(),
}))

vi.mock('../composables/useOffers', () => ({
  useOffers: () => ({
    offers: marketViewMocks.offersRef,
    isLoading: marketViewMocks.isLoadingRef,
    isLoadingMore: marketViewMocks.isLoadingMoreRef,
    error: marketViewMocks.errorRef,
    paginationError: marketViewMocks.paginationErrorRef,
    hasMore: marketViewMocks.hasMoreRef,
    fetchOffers: marketViewMocks.fetchOffersMock,
    loadMoreOffers: marketViewMocks.loadMoreOffersMock,
    setFilters: marketViewMocks.setFiltersMock,
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

vi.mock('../composables/useWebSocket', () => ({
  useWebSocket: () => ({
    on: (event: string, callback: (data: any) => void) => {
      const handlers = marketViewMocks.wsHandlers.get(event) ?? new Set<(data: any) => void>()
      handlers.add(callback)
      marketViewMocks.wsHandlers.set(event, handlers)
    },
    off: (event: string, callback: (data: any) => void) => {
      const handlers = marketViewMocks.wsHandlers.get(event)
      handlers?.delete(callback)
      if (handlers && handlers.size === 0) {
        marketViewMocks.wsHandlers.delete(event)
      }
    },
  }),
}))

const offersFixture = [
  {
    id: 1,
    offer_type: 'sell',
    settlement_type: 'cash',
    commodity_name: 'سکه',
    remaining_quantity: 20,
    quantity: 20,
    price: 123456,
    is_own_offer: false,
  },
]

const recentOffersFixture = [
  {
    id: 91,
    offer_public_id: 'ofr_recent_91',
    offer_type: 'sell',
    settlement_type: 'cash',
    commodity_id: 1,
    commodity_name: 'سکه',
    quantity: 12,
    remaining_quantity: 12,
    raw_price: 345678,
    price: 345678,
    is_wholesale: true,
    lot_sizes: null,
    original_lot_sizes: null,
    notes: 'از لیست اخیر',
    status: 'expired',
    created_at: '۱۴۰۵/۰۳/۰۱ ۱۲:۳۰',
  },
  {
    id: 92,
    offer_public_id: 'ofr_recent_92',
    offer_type: 'buy',
    settlement_type: 'tomorrow',
    commodity_id: 2,
    commodity_name: 'طلای آب‌شده',
    quantity: 8,
    remaining_quantity: 5,
    raw_price: 222000,
    price: 222000,
    is_wholesale: false,
    lot_sizes: [3, 2],
    original_lot_sizes: [5, 3],
    notes: null,
    status: 'expired',
    created_at: '۱۴۰۵/۰۳/۰۱ ۱۲:۱۰',
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

function emitWs(event: string, data: any) {
  marketViewMocks.wsHandlers.get(event)?.forEach((callback) => callback(data))
}

function getRecentOffersDropdown(): HTMLElement | null {
  return document.body.querySelector('.recent-offers-dropdown')
}

function getRecentOfferItems(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll<HTMLElement>('.recent-offer-item'))
}

async function mountMarketView() {
  const MarketView = (await import('./MarketView.vue')).default
  return mount(MarketView, {
    attachTo: document.body,
    global: {
      stubs: {
        transition: true,
        OffersList: {
          props: [
            'offers',
            'loading',
            'expiryMinutes',
            'currentUserId',
            'currentUserReady',
            'expiredLoading',
            'hasMoreExpired',
            'canLoadExpired',
            'activeLoading',
            'hasMoreActive',
            'activeLoadError',
          ],
          template: `
            <div class="offers-list-stub">
              <div class="offers-count">{{ offers.length }}</div>
              <div class="offers-statuses">{{ offers.map((offer) => offer.status || 'active').join(',') }}</div>
              <div class="offers-expiry">{{ expiryMinutes }}</div>
              <div class="offers-user-id">{{ currentUserId }}</div>
              <div class="offers-active-loading">{{ activeLoading }}</div>
              <div class="offers-active-more">{{ hasMoreActive }}</div>
              <div class="offers-active-error">{{ activeLoadError }}</div>
              <button class="emit-load-more-active" @click="$emit('load-more-active')">active-more</button>
              <button class="emit-retry-active" @click="$emit('retry-active')">active-retry</button>
              <button class="emit-load-more-expired" @click="$emit('load-more-expired')">more</button>
              <button class="emit-trade-completed" @click="$emit('trade-completed')">emit</button>
            </div>
          `,
        },
      },
    },
  })
}

describe('MarketView.vue', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    document.body.innerHTML = ''
    const { clearCurrentUserSummary } = await import('../utils/currentUser')
    clearCurrentUserSummary()
    marketViewMocks.offersRef = ref(offersFixture)
    marketViewMocks.isLoadingRef = ref(false)
    marketViewMocks.isLoadingMoreRef = ref(false)
    marketViewMocks.errorRef = ref('')
    marketViewMocks.paginationErrorRef = ref('')
    marketViewMocks.hasMoreRef = ref(false)
    marketViewMocks.fetchOffersMock.mockReset()
    marketViewMocks.fetchOffersMock.mockResolvedValue(undefined)
    marketViewMocks.loadMoreOffersMock.mockReset()
    marketViewMocks.loadMoreOffersMock.mockResolvedValue(undefined)
    marketViewMocks.setFiltersMock.mockReset()
    marketViewMocks.setFiltersMock.mockResolvedValue(undefined)
    marketViewMocks.startPollingMock.mockReset()
    marketViewMocks.stopPollingMock.mockReset()
    marketViewMocks.toggleSortMock.mockReset()
    marketViewMocks.clearSortMock.mockReset()
    marketViewMocks.pushBackStateMock.mockReset()
    marketViewMocks.popBackStateMock.mockReset()
    marketViewMocks.clearBackStackMock.mockReset()
    marketViewMocks.apiFetchMock.mockReset()
    marketViewMocks.apiFetchJsonMock.mockReset()
    marketViewMocks.wsHandlers.clear()

    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/notifications/preferences' && options?.method === 'PATCH') {
        return responseOf(JSON.parse(String(options.body)))
      }
      if (path === '/api/notifications/preferences') {
        return responseOf({ market_offer_push_enabled: true })
      }
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/offers/market-history?skip=0&limit=25') return responseOf([])
      if (path === '/api/offers/my/repeatable?limit=3') return responseOf(recentOffersFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      if (path === '/api/offers/') return responseOf({ success: true, id: 1001 })
      return responseOf(null)
    })

    marketViewMocks.apiFetchJsonMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/offers/parse') {
        return {
          success: true,
          data: {
            trade_type: 'buy',
            settlement_type: 'tomorrow',
            commodity_id: 2,
            commodity_name: 'طلای آب‌شده',
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

  afterEach(async () => {
    const { clearCurrentUserSummary } = await import('../utils/currentUser')
    clearCurrentUserSummary()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('loads market dependencies, wires OffersList props, and refreshes from the child event', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()
    expect(marketViewMocks.startPollingMock).toHaveBeenCalled()
    expect(marketViewMocks.setFiltersMock).toHaveBeenCalledWith({
      offerType: undefined,
      settlementType: undefined,
      commodityId: undefined,
      ownOnly: false,
    })
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/')
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/')
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/market-state')
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/notifications/preferences')
    expect(wrapper.find('.offers-count').text()).toBe('1')
    expect(wrapper.find('.offers-expiry').text()).toBe('45')
    expect(wrapper.find('.offers-user-id').text()).toBe('77')
    expect(wrapper.find('.sort-toggle-btn').exists()).toBe(false)
    expect(wrapper.find('.clear-sort-btn').exists()).toBe(false)
    expect(wrapper.find('.market-summary-shell').exists()).toBe(false)
    expect(wrapper.get('.tabs-container').attributes('role')).toBe('tablist')
    expect(wrapper.findAll('[role="tab"]').every((btn) => btn.attributes('role') === 'tab')).toBe(true)
    expect(wrapper.findAll('[role="tab"]')[0]?.attributes('aria-selected')).toBe('true')
    expect(wrapper.find('.text-offer-input').attributes('aria-label')).toBe('متن لفظ بازار')
    expect(wrapper.find('.send-btn').attributes('aria-label')).toBe('ارسال لفظ برای پیش‌نمایش')
    expect(wrapper.find('.send-btn').classes()).toContain('ui-icon-button--neutral')
    expect(wrapper.find('.market-notification-toggle').attributes('aria-label')).toBe('خاموش کردن اعلان آفرهای بازار')
    expect(wrapper.find('.market-notification-toggle').attributes('aria-pressed')).toBe('true')

    marketViewMocks.loadMoreOffersMock.mockClear()
    await wrapper.find('.emit-load-more-active').trigger('click')
    expect(marketViewMocks.loadMoreOffersMock).toHaveBeenCalledTimes(1)

    marketViewMocks.fetchOffersMock.mockClear()
    await wrapper.find('.emit-trade-completed').trigger('click')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
    expect(marketViewMocks.stopPollingMock).toHaveBeenCalled()
    expect(marketViewMocks.clearBackStackMock).toHaveBeenCalled()
  }, 15000)

  it('refreshes market history only for terminal realtime offer events', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    marketViewMocks.apiFetchMock.mockClear()

    emitWs('offer:updated', { id: 1, status: 'active', remaining_quantity: 12 })
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/api/offers/market-history?skip=0&limit=25')

    emitWs('offer:updated', { id: 1, status: 'completed', remaining_quantity: 0 })
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/market-history?skip=0&limit=25')

    marketViewMocks.apiFetchMock.mockClear()
    emitWs('offer:expired', { id: 1 })
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/market-history?skip=0&limit=25')

    wrapper.unmount()
  }, 15000)

  it('queues terminal market history refreshes that arrive during an in-flight load', async () => {
    let resolveInitialHistory!: () => void
    let marketHistoryCalls = 0
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/notifications/preferences' && options?.method === 'PATCH') {
        return responseOf(JSON.parse(String(options.body)))
      }
      if (path === '/api/notifications/preferences') return responseOf({ market_offer_push_enabled: true })
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      if (path === '/api/offers/market-history?skip=0&limit=25') {
        marketHistoryCalls += 1
        if (marketHistoryCalls === 1) {
          return new Promise((resolve) => {
            resolveInitialHistory = () => resolve(responseOf([]))
          })
        }
        return responseOf([
          {
            id: 203,
            status: 'completed',
            history_state: 'traded',
            is_read_only: true,
            offer_type: 'sell',
            commodity_name: 'سکه',
            quantity: 4,
            remaining_quantity: 0,
            traded_quantity: 4,
            price: 5000,
            viewer_effective_price: 5000,
            is_wholesale: true,
            lot_sizes: null,
            notes: null,
            created_at: 'امروز',
          },
        ])
      }
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect(marketHistoryCalls).toBe(1)

    emitWs('offer:updated', { id: 203, status: 'completed', remaining_quantity: 0 })
    await nextTick()
    expect(marketHistoryCalls).toBe(1)

    resolveInitialHistory()
    await flushPromises()
    await flushPromises()

    expect(marketHistoryCalls).toBe(2)
    expect(wrapper.find('.offers-statuses').text()).toBe('active,completed')

    wrapper.unmount()
  }, 15000)

  it('appends read-only market history offers for non-customer users only', async () => {
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/notifications/preferences' && options?.method === 'PATCH') {
        return responseOf(JSON.parse(String(options.body)))
      }
      if (path === '/api/notifications/preferences') return responseOf({ market_offer_push_enabled: true })
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      if (path === '/api/offers/market-history?skip=0&limit=25') {
        return responseOf([
          {
            id: 201,
            status: 'expired',
            history_state: 'expired',
            is_read_only: true,
            is_own_offer: true,
            expire_reason: 'manual',
            offer_type: 'buy',
            commodity_name: 'سکه',
            quantity: 10,
            remaining_quantity: 10,
            price: 1000,
            viewer_effective_price: 1000,
            is_wholesale: true,
            lot_sizes: null,
            notes: null,
            created_at: 'امروز',
          },
          {
            id: 202,
            status: 'completed',
            history_state: 'traded',
            is_read_only: true,
            is_own_offer: true,
            offer_type: 'sell',
            commodity_name: 'طلای آب‌شده',
            quantity: 8,
            remaining_quantity: 0,
            traded_quantity: 8,
            price: 2000,
            viewer_effective_price: 2000,
            is_wholesale: true,
            lot_sizes: null,
            notes: null,
            created_at: 'امروز',
          },
        ])
      }
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/market-history?skip=0&limit=25')
    expect(wrapper.find('.offers-count').text()).toBe('3')
    expect(wrapper.find('.offers-statuses').text()).toBe('active,expired,completed')

    const buyTab = wrapper.findAll('[role="tab"]').find((btn) => btn.text().includes('خریدار'))
    expect(buyTab?.exists()).toBe(true)
    await buyTab!.trigger('click')
    expect(wrapper.find('.offers-count').text()).toBe('1')
    expect(wrapper.find('.offers-statuses').text()).toBe('expired')

    const sellTab = wrapper.findAll('[role="tab"]').find((btn) => btn.text().includes('فروشنده'))
    expect(sellTab?.exists()).toBe(true)
    await sellTab!.trigger('click')
    expect(wrapper.find('.offers-count').text()).toBe('2')
    expect(wrapper.find('.offers-statuses').text()).toBe('active,completed')

    const myTab = wrapper.findAll('[role="tab"]').find((btn) => btn.text().includes('لفظ‌های شما'))
    expect(myTab?.exists()).toBe(true)
    await myTab!.trigger('click')
    expect(wrapper.find('.offers-count').text()).toBe('2')
    expect(wrapper.find('.offers-statuses').text()).toBe('expired,completed')

    wrapper.unmount()
  })

  it('does not load market history offers for tier customers', async () => {
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/notifications/preferences' && options?.method === 'PATCH') {
        return responseOf(JSON.parse(String(options.body)))
      }
      if (path === '/api/notifications/preferences') return responseOf({ market_offer_push_enabled: true })
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: 'tier1' })
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/api/offers/market-history?skip=0&limit=25')

    wrapper.unmount()
  })

  it('does not load market history offers for accountant users', async () => {
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/notifications/preferences' && options?.method === 'PATCH') {
        return responseOf(JSON.parse(String(options.body)))
      }
      if (path === '/api/notifications/preferences') return responseOf({ market_offer_push_enabled: true })
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') {
        return responseOf({ id: 77, role: 'عادی', customer_tier: null, is_accountant: true })
      }
      if (path === '/api/offers/market-history?skip=0&limit=25') {
        throw new Error('accountants should not request market history')
      }
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/api/offers/market-history?skip=0&limit=25')

    wrapper.unmount()
  })

  it('toggles market offer notification preference from the header icon', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchMock.mockClear()

    const toggle = wrapper.get('.market-notification-toggle')
    expect(toggle.attributes('aria-pressed')).toBe('true')

    await toggle.trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/notifications/preferences', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ market_offer_push_enabled: false }),
    }))
    expect(wrapper.get('.market-notification-toggle').attributes('aria-label')).toBe('روشن کردن اعلان آفرهای بازار')
    expect(wrapper.get('.market-notification-toggle').attributes('aria-pressed')).toBe('false')
    expect(wrapper.get('.market-notification-toggle').classes()).toContain('market-notification-toggle--muted')

    wrapper.unmount()
  })

  it('supports keyboard navigation across market filter tabs', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    expect(wrapper.findAll('[role="tab"]')[0]?.attributes('aria-selected')).toBe('true')

    await wrapper.findAll('[role="tab"]')[0]!.trigger('keydown', { key: 'ArrowLeft' })
    await nextTick()
    expect(wrapper.findAll('[role="tab"]')[1]?.attributes('aria-selected')).toBe('true')

    await wrapper.findAll('[role="tab"]')[1]!.trigger('keydown', { key: 'End' })
    await nextTick()
    expect(wrapper.findAll('[role="tab"]')[3]?.attributes('aria-selected')).toBe('true')

    await wrapper.findAll('[role="tab"]')[3]!.trigger('keydown', { key: 'Home' })
    await nextTick()
    expect(wrapper.findAll('[role="tab"]')[0]?.attributes('aria-selected')).toBe('true')

    wrapper.unmount()
  })

  it('filters market offers independently by settlement type', async () => {
    marketViewMocks.offersRef = ref([
      { ...offersFixture[0], id: 1, settlement_type: 'cash' },
      { ...offersFixture[0], id: 2, settlement_type: 'tomorrow' },
    ])
    const wrapper = await mountMarketView()
    await flushPromises()

    expect(wrapper.find('.offers-count').text()).toBe('2')
    const filterStrip = wrapper.get('.market-filter-strip')
    expect(filterStrip.findAll('[role="tab"]').map((tab) => tab.text())).toEqual([
      'همه',
      'خریدار',
      'فروشنده',
      'لفظ‌های شما',
      'همه تسویه‌ها',
      'نقد حاضر',
      'فردا',
      'همه کالاها',
      'سکه',
      'طلای آب‌شده',
    ])
    const settlementTabs = wrapper.findAll('.market-settlement-filter-chips [role="tab"]')
    expect(settlementTabs.map((tab) => tab.text())).toEqual(['همه تسویه‌ها', 'نقد حاضر', 'فردا'])

    await settlementTabs[1]!.trigger('click')
    await nextTick()
    expect(wrapper.find('.offers-count').text()).toBe('1')
    expect(marketViewMocks.setFiltersMock).toHaveBeenLastCalledWith({
      offerType: undefined,
      settlementType: 'cash',
      commodityId: undefined,
      ownOnly: false,
    })

    await settlementTabs[2]!.trigger('click')
    await nextTick()
    expect(wrapper.find('.offers-count').text()).toBe('1')
    expect(marketViewMocks.setFiltersMock).toHaveBeenLastCalledWith({
      offerType: undefined,
      settlementType: 'tomorrow',
      commodityId: undefined,
      ownOnly: false,
    })

    const commodityTabs = wrapper.findAll('.market-commodity-filter-chips [role="tab"]')
    await commodityTabs[1]!.trigger('click')
    await nextTick()
    expect(marketViewMocks.setFiltersMock).toHaveBeenLastCalledWith({
      offerType: undefined,
      settlementType: 'tomorrow',
      commodityId: 1,
      ownOnly: false,
    })

    wrapper.unmount()
  })

  it('parses and submits a text offer from the action bar', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchJsonMock.mockClear()
    marketViewMocks.fetchOffersMock.mockClear()
    marketViewMocks.apiFetchMock.mockClear()

    await wrapper.find('.text-offer-input').setValue('خرید نقد فردا طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchJsonMock).toHaveBeenCalledWith('/api/offers/parse', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ text: 'خرید نقد فردا طلای آب‌شده 50 عدد 222222' }),
      retryNetwork: false,
    }), expect.objectContaining({
      surface: 'market',
      scope: 'field',
      operation: 'submit',
    }))
    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/', expect.objectContaining({
      method: 'POST',
      retryNetwork: false,
    }))
    expect(JSON.parse(String(marketViewMocks.apiFetchMock.mock.calls[0]![1].body))).toEqual({
      offer_type: 'buy',
      settlement_type: 'tomorrow',
      commodity_id: 2,
      quantity: 50,
      price: 222222,
      is_wholesale: true,
      lot_sizes: null,
      notes: 'از متن بازار',
      republished_from_id: null,
      republished_from_public_id: null,
      warning_acknowledged: false,
      idempotency_key: expect.any(String),
    })
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('locks offer publishing during in-flight confirmation and uses one stable idempotency key', async () => {
    let resolvePost: ((value: any) => void) | null = null
    marketViewMocks.apiFetchMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/commodities/') return Promise.resolve(responseOf(commoditiesFixture))
      if (path === '/api/trading-settings/') return Promise.resolve(responseOf(settingsFixture))
      if (path === '/api/trading-settings/market-state') {
        return Promise.resolve(responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        }))
      }
      if (path === '/api/auth/me') return Promise.resolve(responseOf({ id: 77, customer_tier: null }))
      if (path === '/api/offers/' && options?.method === 'POST') {
        return new Promise((resolve) => {
          resolvePost = resolve
        }) as Promise<any>
      }
      return Promise.resolve(responseOf(null))
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.text-offer-input').setValue('خرید نقد فردا طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    marketViewMocks.apiFetchMock.mockClear()
    await wrapper.find('.offer-preview-confirm').trigger('click')
    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const postCalls = marketViewMocks.apiFetchMock.mock.calls.filter(([path, options]) => path === '/api/offers/' && options?.method === 'POST')
    expect(postCalls).toHaveLength(1)
    expect(postCalls[0]![1]).toEqual(expect.objectContaining({ retryNetwork: false }))
    expect(JSON.parse(String(postCalls[0]![1].body)).idempotency_key).toEqual(expect.any(String))

    if (!resolvePost) {
      throw new Error('Expected pending offer publish resolver')
    }
    ;(resolvePost as (value: any) => void)(responseOf({ success: true, id: 1001 }))
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)
    wrapper.unmount()
  })

  it('returns a parsed preview back into the market chatbox when the user chooses edit', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    expect(wrapper.find('.text-offer-input').element.tagName).toBe('TEXTAREA')

    await wrapper.find('.text-offer-input').setValue('خرید نقد فردا طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-edit').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('خرید نقد فردا طلای آب‌شده 50 عدد 222222: از متن بازار')

    wrapper.unmount()
  })

  it('loads recent offers from the dropdown and republishes the selected offer after confirmation', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchMock.mockClear()
    marketViewMocks.fetchOffersMock.mockClear()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/my/repeatable?limit=3')
    const recentItems = getRecentOfferItems()
    expect(recentItems).toHaveLength(2)
    expect(document.body.textContent).toContain('سکه')
    expect(document.body.textContent).toContain('طلای آب‌شده')
    expect(document.body.textContent).toContain('توضیح: از لیست اخیر')
    expect(document.body.textContent).toContain('خرد · پله‌ها: ۳ + ۲')
    expect(document.body.textContent).not.toContain('۱۴۰۵/۰۳/۰۱ ۱۲:۳۰')
    expect(document.body.textContent).not.toContain('۱۴۰۵/۰۳/۰۱ ۱۲:۱۰')

    recentItems[1]!.click()
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('طلای آب‌شده')
    expect(document.body.textContent).toContain('فردا 📆')
    expect(wrapper.get('.offer-preview-badges .ui-settlement-badge--tomorrow').text()).toContain('فردا 📆')
    expect(wrapper.get('.offer-preview-line').text()).toContain('5 عدد 222,000')

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const republishCall = marketViewMocks.apiFetchMock.mock.calls.find(
      ([path, options]) => path === '/api/offers/' && options?.method === 'POST',
    )
    expect(republishCall).toBeTruthy()
    expect(JSON.parse(String(republishCall![1].body))).toEqual({
      offer_type: 'buy',
      settlement_type: 'tomorrow',
      commodity_id: 2,
      quantity: 5,
      price: 222000,
      is_wholesale: false,
      lot_sizes: [3, 2],
      notes: null,
      republished_from_id: 92,
      republished_from_public_id: 'ofr_recent_92',
      warning_acknowledged: false,
      idempotency_key: expect.any(String),
    })
    expect(republishCall![1]).toEqual(expect.objectContaining({ retryNetwork: false }))
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('shows a compact empty state when there are no recent expired offers', async () => {
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/notifications/preferences' && options?.method === 'PATCH') {
        return responseOf(JSON.parse(String(options.body)))
      }
      if (path === '/api/notifications/preferences') return responseOf({ market_offer_push_enabled: true })
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/offers/my/repeatable?limit=3') return responseOf([])
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    expect(getRecentOffersDropdown()?.textContent).toContain('بدون فعالیت')
    expect(getRecentOffersDropdown()?.textContent).not.toContain('لفظ اخیری وجود ندارد')
    expect(getRecentOffersDropdown()?.textContent).not.toContain('در یک ساعت گذشته لفظی برای بازنشر ثبت نشده است.')

    wrapper.unmount()
  })

  it('closes the recent offers menu when toggled again or when clicking outside it', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    expect(getRecentOffersDropdown()).not.toBeNull()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    expect(getRecentOffersDropdown()).toBeNull()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    expect(getRecentOffersDropdown()).not.toBeNull()

    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()

    expect(getRecentOffersDropdown()).toBeNull()

    wrapper.unmount()
  })

  it('anchors the recent offers menu above the market input without fixed-position drift', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 })

    const wrapper = await mountMarketView()
    await flushPromises()
    Object.defineProperty(wrapper.get('.input-wrapper').element, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 16, top: 760, width: 360, height: 56, right: 376, bottom: 816, x: 16, y: 760, toJSON: () => ({}) }),
    })

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    const dropdown = getRecentOffersDropdown()!
    window.dispatchEvent(new Event('resize'))
    await nextTick()

    expect(dropdown.style.position).toBe('fixed')
    expect(Number.parseFloat(dropdown.style.left)).toBeGreaterThanOrEqual(8)
    expect(Number.parseFloat(dropdown.style.top)).toBeGreaterThanOrEqual(8)
    expect(dropdown.style.bottom).toBe('auto')
    expect(dropdown.style.width).toBe('304px')
    expect(dropdown.style.maxHeight).toBe('320px')
    expect(dropdown.style.transformOrigin).toBe('bottom left')
    expect(dropdown.style.getPropertyValue('--recent-offers-enter-offset')).toBe('0.35rem')
    expect(dropdown.classList.contains('recent-offers-dropdown--above')).toBe(true)

    wrapper.unmount()
  })

  it('keeps the recent offers menu bounded on short mobile viewports', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 240 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 260 })

    const wrapper = await mountMarketView()
    await flushPromises()
    Object.defineProperty(wrapper.get('.input-wrapper').element, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 8, top: 220, width: 232, height: 56, right: 240, bottom: 276, x: 8, y: 220, toJSON: () => ({}) }),
    })

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    const dropdown = getRecentOffersDropdown()!
    window.dispatchEvent(new Event('resize'))
    await nextTick()

    expect(dropdown.style.position).toBe('fixed')
    expect(Number.parseFloat(dropdown.style.width)).toBeGreaterThanOrEqual(220)
    expect(Number.parseFloat(dropdown.style.width)).toBeLessThanOrEqual(224)
    expect(Number.parseFloat(dropdown.style.maxHeight)).toBeGreaterThanOrEqual(132)
    expect(Number.parseFloat(dropdown.style.maxHeight)).toBeLessThanOrEqual(236)
    expect(dropdown.style.bottom).toBe('auto')
    expect(dropdown.style.transformOrigin).toBe('bottom left')
    expect(dropdown.style.getPropertyValue('--recent-offers-enter-offset')).toBe('0.35rem')
    expect(dropdown.classList.contains('recent-offers-dropdown--above')).toBe(true)

    wrapper.unmount()
  })

  it('shows recent offer load errors, keeps the menu open for inside clicks, and retries successfully', async () => {
    let recentOffersMode: 'error' | 'success' = 'error'
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      if (path === '/api/offers/my/repeatable?limit=3') {
        return recentOffersMode === 'error'
          ? errorResponse(503, { detail: 'بارگذاری لفظ‌های اخیر شکست خورد' })
          : responseOf(recentOffersFixture.slice(0, 1))
      }
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    expect(getRecentOffersDropdown()?.textContent).toContain('بارگذاری لفظ‌های اخیر ممکن نشد.')

    getRecentOffersDropdown()!.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()

    expect(getRecentOffersDropdown()).not.toBeNull()

    recentOffersMode = 'success'
    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    expect(getRecentOfferItems()).toHaveLength(1)
    expect(document.body.textContent).toContain('سکه')

    wrapper.unmount()
  })

  it('republishes expired retail offers from remaining quantity and current lot sizes', async () => {
    const activeRecentOffer = {
      id: 93,
      offer_public_id: 'ofr_recent_93',
      offer_type: 'sell',
      settlement_type: 'cash',
      commodity_id: 3,
      commodity_name: 'ربع سکه',
      quantity: 9,
      remaining_quantity: 5,
      raw_price: null,
      price: 111000,
      is_wholesale: false,
      lot_sizes: [3, 2],
      original_lot_sizes: [4, 3, 2],
      notes: '   ',
      status: 'expired',
      created_at: '۱۴۰۵/۰۳/۰۱ ۱۳:۱۰',
    }

    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      if (path === '/api/offers/my/repeatable?limit=3') {
        return responseOf([activeRecentOffer])
      }
      if (path === '/api/offers/' && options?.method === 'POST') {
        return responseOf({ success: true, id: 1009 })
      }
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('خرد · پله‌ها: ۳ + ۲')
    expect(document.body.textContent).not.toContain('توضیح:')

    getRecentOfferItems()[0]!.click()
    await flushPromises()

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const republishCall = marketViewMocks.apiFetchMock.mock.calls.find(
      ([path, options]) => path === '/api/offers/' && options?.method === 'POST',
    )

    expect(republishCall).toBeTruthy()
    expect(JSON.parse(String(republishCall![1].body))).toEqual({
      offer_type: 'sell',
      settlement_type: 'cash',
      commodity_id: 3,
      quantity: 5,
      price: 111000,
      is_wholesale: false,
      lot_sizes: [3, 2],
      notes: '   ',
      republished_from_id: 93,
      republished_from_public_id: 'ofr_recent_93',
      warning_acknowledged: false,
      idempotency_key: expect.any(String),
    })
    expect(republishCall![1]).toEqual(expect.objectContaining({ retryNetwork: false }))

    wrapper.unmount()
  })

  it('discards a repeated-offer preview when the market session changes', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    getRecentOfferItems()[0]!.click()
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    emitWs('market:closed', {
      is_open: false,
      active_web_notice_visible: true,
      offers_since_last_open: 0,
    })
    await nextTick()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)
    expect(wrapper.find('.recent-offers-toggle').exists()).toBe(false)

    wrapper.unmount()
  })

  it('returns a repeated recent offer back into the market chatbox when the user chooses edit', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    const recentItems = getRecentOfferItems()
    recentItems[0]!.click()
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-edit').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('فروش نقد سکه 12 عدد 345678: از لیست اخیر')

    wrapper.unmount()
  })

  it('renders closed-market notice, disables the composer, and reacts to market runtime websocket events', async () => {
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: false,
          active_web_notice_visible: true,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect(wrapper.find('.market-runtime-notice').text()).toContain('پایان فعالیت بازار')
    expect(wrapper.find('.text-offer-input').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.send-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.recent-offers-toggle').exists()).toBe(false)

    emitWs('market:opened', {
      is_open: true,
      active_web_notice_visible: true,
      offers_since_last_open: 0,
    })
    await nextTick()

    expect(wrapper.find('.market-runtime-notice').text()).toContain('شروع فعالیت بازار')
    expect(wrapper.find('.text-offer-input').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.recent-offers-toggle').exists()).toBe(true)

    emitWs('market:notice_hidden', {
      is_open: true,
      active_web_notice_visible: false,
      offers_since_last_open: 2,
    })
    await nextTick()

    expect(wrapper.find('.market-runtime-notice').exists()).toBe(false)

    wrapper.unmount()
  })

  it('renders and expands the active admin market message and updates it from realtime', async () => {
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/trading-settings/market-state') {
        return responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        })
      }
      if (path === '/api/admin-messages/market/current') {
        return responseOf({
          id: 4,
          content: 'خط اول پیام مدیریت\nخط دوم\nخط سوم\nخط چهارم',
          is_active: true,
          published_at: '2026-05-29T08:00:00Z',
        })
      }
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect(wrapper.find('.admin-market-message').text()).toContain('پیام مدیریت')
    expect(wrapper.find('.admin-market-message').classes()).toContain('admin-market-message--collapsed')
    expect(wrapper.find('.admin-market-message-body').text()).toContain('خط چهارم')

    await wrapper.find('.admin-market-message-expand').trigger('click')
    await nextTick()
    expect(wrapper.find('.admin-market-message').classes()).not.toContain('admin-market-message--collapsed')

    emitWs('market:admin_message_published', {
      id: 5,
      content: 'پیام تازه مدیریت',
      is_active: true,
      published_at: '2026-05-29T09:00:00Z',
    })
    await nextTick()

    expect(wrapper.find('.admin-market-message').text()).toContain('پیام تازه مدیریت')
    expect(wrapper.find('.admin-market-message').classes()).toContain('admin-market-message--collapsed')

    wrapper.unmount()
  })

  it('shows a warning and requires a second confirmation for suspicious prices', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    marketViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: null })
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

    await wrapper.find('.text-offer-input').setValue('خرید نقد فردا طلای آب‌شده 50 عدد 222222')
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
    expect(JSON.parse(String(postCalls[1]![1].body)).idempotency_key).toBe(JSON.parse(String(postCalls[0]![1].body)).idempotency_key)

    wrapper.unmount()
  })

  it('blocks preview confirmation while the market is closed and keeps the preview open', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.text-offer-input').setValue('خرید نقد فردا طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    marketViewMocks.apiFetchMock.mockClear()
    emitWs('market:closed', {
      is_open: false,
      active_web_notice_visible: true,
      offers_since_last_open: 0,
    })
    await nextTick()

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)
    expect(wrapper.find('.offer-preview-error').text()).toBe('بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید.')
    expect(wrapper.find('.offer-preview-confirm').attributes('disabled')).toBeUndefined()
    expect(
      marketViewMocks.apiFetchMock.mock.calls.find(
        ([path, options]) => path === '/api/offers/' && options?.method === 'POST',
      ),
    ).toBeUndefined()

    emitWs('market:opened', {
      is_open: true,
      active_web_notice_visible: true,
      offers_since_last_open: 0,
    })
    await nextTick()

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const postCalls = marketViewMocks.apiFetchMock.mock.calls.filter(
      ([path, options]) => path === '/api/offers/' && options?.method === 'POST',
    )
    expect(postCalls).toHaveLength(1)
    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)

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

  it('hides the create-offer bar and your-offers tab for tier2 customers', async () => {
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/commodities/') return responseOf(commoditiesFixture)
      if (path === '/api/trading-settings/') return responseOf(settingsFixture)
      if (path === '/api/auth/me') return responseOf({ id: 77, customer_tier: 'tier2' })
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect(wrapper.find('.text-offer-input').exists()).toBe(false)
    expect(wrapper.find('.send-btn').exists()).toBe(false)
    expect(wrapper.find('.market-action-bar').exists()).toBe(false)
    expect(wrapper.find('.tier2-offer-note').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('ثبت لفظ برای مشتری سطح 2 غیرفعال است')
    expect(wrapper.text()).not.toContain('شما فقط می‌توانید روی لفظ‌های دیگر درخواست بزنید.')
    expect(wrapper.findAll('[role="tab"]').some((btn) => btn.text().includes('لفظ‌های شما'))).toBe(false)

    wrapper.unmount()
  })

  it('keeps the create-offer bar hidden for cached tier2 users before auth/me resolves', async () => {
    const { cacheCurrentUserSummary } = await import('../utils/currentUser')
    cacheCurrentUserSummary({ id: 77, role: 'عادی', account_name: 'tier2-user', customer_tier: 'tier2' })

    let resolveMe: ((value: unknown) => void) | null = null
    marketViewMocks.apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/commodities/') return Promise.resolve(responseOf(commoditiesFixture))
      if (path === '/api/trading-settings/') return Promise.resolve(responseOf(settingsFixture))
      if (path === '/api/trading-settings/market-state') {
        return Promise.resolve(responseOf({
          is_open: true,
          active_web_notice_visible: false,
          offers_since_last_open: 0,
          last_transition_at: null,
          next_transition_at: null,
        }))
      }
      if (path === '/api/auth/me') {
        return new Promise((resolve) => {
          resolveMe = resolve
        }) as Promise<any>
      }
      return Promise.resolve(responseOf(null))
    })

    const wrapper = await mountMarketView()
    await nextTick()

    expect(wrapper.find('.market-action-bar').exists()).toBe(false)
    expect(wrapper.findAll('[role="tab"]').some((btn) => btn.text().includes('لفظ‌های شما'))).toBe(false)

    if (!resolveMe) {
      throw new Error('Expected auth/me resolver')
    }
    ;(resolveMe as (value: unknown) => void)(responseOf({ id: 77, role: 'عادی', account_name: 'tier2-user', customer_tier: 'tier2' }))
    await flushPromises()

    expect(wrapper.find('.market-action-bar').exists()).toBe(false)

    wrapper.unmount()
  })

  it('resets the selected your-offers tab when tier2 access loads', async () => {
    let resolveMe: ((value: unknown) => void) | null = null
    marketViewMocks.apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/commodities/') return Promise.resolve(responseOf(commoditiesFixture))
      if (path === '/api/trading-settings/') return Promise.resolve(responseOf(settingsFixture))
      if (path === '/api/auth/me') {
        return new Promise((resolve) => {
          resolveMe = resolve
        }) as Promise<any>
      }
      return Promise.resolve(responseOf(null))
    })

    const wrapper = await mountMarketView()
    await nextTick()

    const myTab = wrapper.findAll('[role="tab"]').find((btn) => btn.text().includes('لفظ‌های شما'))
    expect(myTab?.exists()).toBe(true)
    await myTab!.trigger('click')
    expect(wrapper.findAll('[role="tab"]').find((btn) => btn.attributes('aria-selected') === 'true')?.text()).toContain('لفظ‌های شما')

    if (!resolveMe) {
      throw new Error('Expected auth/me resolver')
    }
    ;(resolveMe as (value: unknown) => void)(responseOf({ id: 77, customer_tier: 'tier2' }))
    await flushPromises()

    expect(wrapper.findAll('[role="tab"]').some((btn) => btn.text().includes('لفظ‌های شما'))).toBe(false)
    expect(wrapper.findAll('[role="tab"]').find((btn) => btn.attributes('aria-selected') === 'true')?.text()).toContain('همه')
    expect(wrapper.find('.market-action-bar').exists()).toBe(false)

    wrapper.unmount()
  })

  it('shows the default placeholder plus fetch-error logging when market dependencies fail', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    marketViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/commodities/') throw new Error('commodities failed')
      if (path === '/api/trading-settings/') throw new Error('settings failed')
      if (path === '/api/trading-settings/market-state') throw new Error('market state failed')
      if (path === '/api/auth/me') throw new Error('me failed')
      return responseOf(null)
    })

    const wrapper = await mountMarketView()
    await flushPromises()

    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).placeholder).toBe('مثال: خرید نقد سکه 30 عدد 125000')
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load commodities', expect.any(Error))
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load settings', expect.any(Error))
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load market state', expect.any(Error))
    expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to load current user', expect.any(Error))
    expect(wrapper.find('.market-runtime-notice').exists()).toBe(false)
    expect(wrapper.find('.text-offer-input').attributes('disabled')).toBeUndefined()

    consoleErrorSpy.mockRestore()
    wrapper.unmount()
  })

  it('does not render the removed sort controls even while commodities are loading', async () => {
    let resolveCommodities: ((value: unknown) => void) | null = null
    marketViewMocks.apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/commodities/') {
        return new Promise((resolve) => {
          resolveCommodities = resolve
        }) as Promise<any>
      }
      if (path === '/api/trading-settings/') return Promise.resolve(responseOf(settingsFixture))
      if (path === '/api/auth/me') return Promise.resolve(responseOf({ id: 77, customer_tier: null }))
      return Promise.resolve(responseOf(null))
    })

    const wrapper = await mountMarketView()
    await nextTick()

    expect(wrapper.find('.sort-toggle-btn').exists()).toBe(false)
    expect(wrapper.find('.clear-sort-btn').exists()).toBe(false)
    expect(wrapper.find('.panel-loading').exists()).toBe(false)

    if (!resolveCommodities) {
      throw new Error('Expected commodities resolver')
    }
    ;(resolveCommodities as (v: unknown) => void)(responseOf(commoditiesFixture))
    await flushPromises()

    expect(wrapper.find('.sort-toggle-btn').exists()).toBe(false)
    expect(wrapper.find('.clear-sort-btn').exists()).toBe(false)
    expect(marketViewMocks.toggleSortMock).not.toHaveBeenCalled()
    expect(marketViewMocks.clearSortMock).not.toHaveBeenCalled()

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

    await wrapper.find('.text-offer-input').setValue('فروش نقد سکه 2 عدد 120000')
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

  it('uses the نشد shortcut to cancel all offers and surfaces cancel-all failures', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchMock.mockClear()
    marketViewMocks.fetchOffersMock.mockClear()

    await wrapper.find('.text-offer-input').setValue('نشد')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/cancel-all', { method: 'POST', retryNetwork: false })
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalledTimes(1)

    marketViewMocks.apiFetchMock.mockReset()
    marketViewMocks.apiFetchMock.mockResolvedValueOnce(errorResponse(422, { detail: 'لغو لفظ‌ها ممکن نشد' }))

    await wrapper.find('.text-offer-input').setValue('نشد')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.parse-error').text()).toBe('لغو لفظ‌ها ممکن نشد')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

})
