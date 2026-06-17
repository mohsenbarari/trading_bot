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
  wsHandlers: new Map<string, Set<(data: any) => void>>(),
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
    offer_type: 'sell',
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
    offer_type: 'buy',
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
  beforeEach(async () => {
    vi.useFakeTimers()
    const { clearCurrentUserSummary } = await import('../utils/currentUser')
    clearCurrentUserSummary()
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
      if (path === '/api/offers/my?since_hours=1&limit=3&status_filter=expired') return responseOf(recentOffersFixture)
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
  })

  it('loads market dependencies, wires OffersList props, and refreshes from the child event', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()
    expect(marketViewMocks.startPollingMock).toHaveBeenCalled()
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

    marketViewMocks.fetchOffersMock.mockClear()
    await wrapper.find('.emit-trade-completed').trigger('click')
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
    expect(marketViewMocks.stopPollingMock).toHaveBeenCalled()
    expect(marketViewMocks.clearBackStackMock).toHaveBeenCalled()
  }, 15000)

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
      commodity_id: 2,
      quantity: 50,
      price: 222222,
      is_wholesale: true,
      lot_sizes: null,
      notes: 'از متن بازار',
      republished_from_id: null,
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

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
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

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
    await wrapper.find('.send-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-edit').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('خرید طلای آب‌شده 50 عدد 222222: از متن بازار')

    wrapper.unmount()
  })

  it('loads recent offers from the dropdown and republishes the selected offer after confirmation', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()
    marketViewMocks.apiFetchMock.mockClear()
    marketViewMocks.fetchOffersMock.mockClear()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    expect(marketViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/offers/my?since_hours=1&limit=3&status_filter=expired')
    const recentItems = wrapper.findAll('.recent-offer-item')
    expect(recentItems).toHaveLength(2)
    expect(wrapper.text()).toContain('سکه')
    expect(wrapper.text()).toContain('طلای آب‌شده')
    expect(wrapper.text()).toContain('توضیح: از لیست اخیر')
    expect(wrapper.text()).toContain('خرد · پله‌ها: ۵ + ۳')
    expect(wrapper.text()).not.toContain('۱۴۰۵/۰۳/۰۱ ۱۲:۳۰')
    expect(wrapper.text()).not.toContain('۱۴۰۵/۰۳/۰۱ ۱۲:۱۰')

    await recentItems[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('طلای آب‌شده')
    expect(wrapper.text()).toContain('8 عدد 222,000')

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const republishCall = marketViewMocks.apiFetchMock.mock.calls.find(
      ([path, options]) => path === '/api/offers/' && options?.method === 'POST',
    )
    expect(republishCall).toBeTruthy()
    expect(JSON.parse(String(republishCall![1].body))).toEqual({
      offer_type: 'buy',
      commodity_id: 2,
      quantity: 8,
      price: 222000,
      is_wholesale: false,
      lot_sizes: [5, 3],
      notes: null,
      republished_from_id: 92,
      warning_acknowledged: false,
      idempotency_key: expect.any(String),
    })
    expect(republishCall![1]).toEqual(expect.objectContaining({ retryNetwork: false }))
    expect(marketViewMocks.fetchOffersMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('closes the recent offers menu when toggled again or when clicking outside it', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    expect(wrapper.find('.recent-offers-dropdown').exists()).toBe(true)

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    expect(wrapper.find('.recent-offers-dropdown').exists()).toBe(false)

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    expect(wrapper.find('.recent-offers-dropdown').exists()).toBe(true)

    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()

    expect(wrapper.find('.recent-offers-dropdown').exists()).toBe(false)

    wrapper.unmount()
  })

  it('anchors the recent offers menu above the market input without fixed-position drift', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 })

    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    const dropdown = wrapper.get('.recent-offers-dropdown').element as HTMLElement
    window.dispatchEvent(new Event('resize'))
    await nextTick()

    expect(dropdown.style.position).toBe('absolute')
    expect(dropdown.style.left).toBe('0.25rem')
    expect(dropdown.style.top).toBe('auto')
    expect(dropdown.style.bottom).toBe('calc(100% + 0.75rem)')
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

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    const dropdown = wrapper.get('.recent-offers-dropdown').element as HTMLElement
    window.dispatchEvent(new Event('resize'))
    await nextTick()

    expect(dropdown.style.position).toBe('absolute')
    expect(dropdown.style.width).toBe('220px')
    expect(dropdown.style.maxHeight).toBe('160px')
    expect(dropdown.style.bottom).toBe('calc(100% + 0.75rem)')
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
      if (path === '/api/offers/my?since_hours=1&limit=3&status_filter=expired') {
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

    expect(wrapper.find('.recent-offers-state--error').text()).toContain('بارگذاری لفظ‌های اخیر ممکن نشد.')

    wrapper.get('.recent-offers-dropdown').element.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()

    expect(wrapper.find('.recent-offers-dropdown').exists()).toBe(true)

    recentOffersMode = 'success'
    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()
    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.recent-offer-item')).toHaveLength(1)
    expect(wrapper.text()).toContain('سکه')

    wrapper.unmount()
  })

  it('republishes active retail recent offers from remaining quantity and current lot sizes', async () => {
    const activeRecentOffer = {
      id: 93,
      offer_type: 'sell',
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
      status: 'active',
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
      if (path === '/api/offers/my?since_hours=1&limit=3&status_filter=expired') {
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

    expect(wrapper.text()).toContain('خرد · پله‌ها: ۳ + ۲')
    expect(wrapper.text()).not.toContain('توضیح:')

    await wrapper.find('.recent-offer-item').trigger('click')
    await flushPromises()

    await wrapper.find('.offer-preview-confirm').trigger('click')
    await flushPromises()

    const republishCall = marketViewMocks.apiFetchMock.mock.calls.find(
      ([path, options]) => path === '/api/offers/' && options?.method === 'POST',
    )

    expect(republishCall).toBeTruthy()
    expect(JSON.parse(String(republishCall![1].body))).toEqual({
      offer_type: 'sell',
      commodity_id: 3,
      quantity: 5,
      price: 111000,
      is_wholesale: false,
      lot_sizes: [3, 2],
      notes: '   ',
      republished_from_id: 93,
      warning_acknowledged: false,
      idempotency_key: expect.any(String),
    })
    expect(republishCall![1]).toEqual(expect.objectContaining({ retryNetwork: false }))

    wrapper.unmount()
  })

  it('returns a repeated recent offer back into the market chatbox when the user chooses edit', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.recent-offers-toggle').trigger('click')
    await flushPromises()

    const recentItems = wrapper.findAll('.recent-offer-item')
    await recentItems[0]!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(true)

    await wrapper.find('.offer-preview-edit').trigger('click')
    await flushPromises()

    expect(wrapper.find('.offer-preview-card').exists()).toBe(false)
    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).value).toBe('فروش سکه 12 عدد 345678: از لیست اخیر')

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

    emitWs('market:opened', {
      is_open: true,
      active_web_notice_visible: true,
      offers_since_last_open: 0,
    })
    await nextTick()

    expect(wrapper.find('.market-runtime-notice').text()).toContain('شروع فعالیت بازار')
    expect(wrapper.find('.text-offer-input').attributes('disabled')).toBeUndefined()

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
    expect(JSON.parse(String(postCalls[1]![1].body)).idempotency_key).toBe(JSON.parse(String(postCalls[0]![1].body)).idempotency_key)

    wrapper.unmount()
  })

  it('blocks preview confirmation while the market is closed and keeps the preview open', async () => {
    const wrapper = await mountMarketView()
    await flushPromises()

    await wrapper.find('.text-offer-input').setValue('خرید طلای آب‌شده 50 عدد 222222')
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
    expect(
      marketViewMocks.apiFetchMock.mock.calls.find(
        ([path, options]) => path === '/api/offers/' && options?.method === 'POST',
      ),
    ).toBeUndefined()

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

    expect((wrapper.find('.text-offer-input').element as HTMLTextAreaElement).placeholder).toBe('مثال: خرید سکه 30 عدد 125000')
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
