import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.fn()

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

async function mountOffersList(overrides: Record<string, unknown> = {}) {
  const OffersList = (await import('./OffersList.vue')).default
  return mount(OffersList, {
    props: {
      offers: [],
      loading: false,
      expiryMinutes: 60,
      currentUserId: 77,
      ...overrides,
    },
    global: {
      stubs: {
        teleport: true,
        transition: false,
      },
    },
  })
}

describe('OffersList.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders owner-only customer context badges when present', async () => {
    const wrapper = await mountOffersList({
      offers: [
        {
          id: 1,
          offer_type: 'sell',
          commodity_name: 'سکه',
          quantity: 20,
          remaining_quantity: 20,
          price: 52000,
          viewer_effective_price: 52000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: true,
          customer_management_name: 'سینا',
          customer_tier: 'tier1',
        },
      ],
    })

    expect(wrapper.text()).toContain('مشتری')
    expect(wrapper.text()).toContain('سینا')
    expect(wrapper.text()).toContain('سطح 1')
  })

  it('prefers viewer_effective_price over the global published price in the market card', async () => {
    const wrapper = await mountOffersList({
      offers: [
        {
          id: 2,
          offer_type: 'buy',
          commodity_name: 'طلا',
          quantity: 10,
          remaining_quantity: 10,
          price: 50000,
          viewer_effective_price: 49700,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
        },
      ],
    })

    expect(wrapper.find('.price').text()).toContain('49,700')
    expect(wrapper.find('.price').text()).not.toContain('50,000')
  })

  it('deduplicates retail lot buttons and falls back for invalid display price and unknown customer tier labels', async () => {
    const wrapper = await mountOffersList({
      offers: [
        {
          id: 3,
          user_id: 12,
          offer_type: 'sell',
          commodity_name: 'نیم‌سکه',
          quantity: 20,
          remaining_quantity: 20,
          price: null,
          viewer_effective_price: 'invalid',
          is_wholesale: false,
          lot_sizes: [10, 30, 10, 5, 0],
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: true,
          customer_management_name: null,
          customer_tier: 'tier-x',
        },
      ],
    })

    expect(wrapper.findAll('.trade-btn').map((button) => button.text())).toEqual(['5 عدد', '10 عدد', '20 عدد'])
    expect(wrapper.find('.price').text()).toBe('---')
    expect(wrapper.text()).toContain('خُرد: 20 + 10 + 5')
    expect(wrapper.text()).toContain('سطح نامشخص')
  })

  it('uses the two-tap confirm flow for retail lots, clears stale pending state, and executes the confirmed trade', async () => {
    vi.useFakeTimers()
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 9,
          user_id: 15,
          offer_type: 'sell',
          commodity_name: 'سکه',
          quantity: 25,
          remaining_quantity: 25,
          price: 52000,
          viewer_effective_price: 52000,
          is_wholesale: false,
          lot_sizes: [15, 10, 25],
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
        },
      ],
    })

    const tradeButtons = wrapper.findAll('.trade-btn')
    expect(tradeButtons).toHaveLength(3)
    expect(tradeButtons.map((button) => button.text())).toEqual(['10 عدد', '15 عدد', '25 عدد'])

    await tradeButtons[0]!.trigger('click')
    expect(wrapper.text()).toContain('تایید 10 عدد؟')
    expect(apiFetchMock).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(wrapper.text()).not.toContain('تایید 10 عدد؟')

    const refreshedButtons = wrapper.findAll('.trade-btn')
    await refreshedButtons[2]!.trigger('click')
    await refreshedButtons[2]!.trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/trades/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ offer_id: 9, quantity: 25 }),
    }))
    expect(wrapper.emitted('trade-completed')).toHaveLength(1)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('treats explicit is_own_offer as authoritative and cancels the offer successfully', async () => {
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({}),
    })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 12,
          user_id: 999,
          offer_type: 'buy',
          commodity_name: 'طلا',
          quantity: 4,
          remaining_quantity: 4,
          price: 45000,
          viewer_effective_price: 45000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
          is_own_offer: true,
        },
      ],
    })

    await wrapper.find('.cancel-own-offer-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/offers/12', expect.objectContaining({ method: 'DELETE' }))
    expect(wrapper.emitted('trade-completed')).toHaveLength(1)
  })

  it('uses currentUserId ownership fallback and clears cancel errors after the timeout', async () => {
    vi.useFakeTimers()
    apiFetchMock.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: 'لغو لفظ ممکن نشد' }),
    })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 13,
          user_id: 77,
          offer_type: 'buy',
          commodity_name: 'سکه',
          quantity: 9,
          remaining_quantity: 9,
          price: 51000,
          viewer_effective_price: 51000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
        },
      ],
    })

    await wrapper.find('.cancel-own-offer-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('لغو لفظ ممکن نشد')

    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()

    expect(wrapper.text()).not.toContain('لغو لفظ ممکن نشد')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('shows generic trade failures and network failures without opening the suggestion alert', async () => {
    vi.useFakeTimers()
    apiFetchMock
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: 'خطا در انجام معامله' }),
      })
      .mockRejectedValueOnce(new Error('سرور در دسترس نیست'))

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 14,
          user_id: 21,
          offer_type: 'sell',
          commodity_name: 'طلای آب‌شده',
          quantity: 7,
          remaining_quantity: 7,
          price: 47000,
          viewer_effective_price: 47000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
        },
      ],
    })

    const tradeButton = () => wrapper.find('.trade-btn')

    await tradeButton().trigger('click')
    await tradeButton().trigger('click')
    await flushPromises()

    expect(wrapper.find('.trade-suggestion-card').exists()).toBe(false)
    expect(wrapper.text()).toContain('خطا در انجام معامله')

    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()

    await tradeButton().trigger('click')
    await tradeButton().trigger('click')
    await flushPromises()

    expect(wrapper.find('.trade-suggestion-card').exists()).toBe(false)
    expect(wrapper.text()).toContain('سرور در دسترس نیست')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('opens the lot suggestion alert for unavailable lots and closes it when the source offer disappears', async () => {
    apiFetchMock.mockResolvedValue({
      ok: false,
      json: async () => ({
        error_code: 'TRADE_LOT_UNAVAILABLE',
        detail: 'این لات‌ها مانده‌اند.',
        offer_id: 11,
        offer_type: 'buy',
        offer_type_label: 'خرید',
        commodity_name: 'طلا',
        price: 49700,
        remaining_quantity: 13,
        lot_summary: '8 + 5',
        available_lots: [8, 5],
      }),
    })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 11,
          user_id: 22,
          offer_type: 'buy',
          commodity_name: 'طلا',
          quantity: 13,
          remaining_quantity: 13,
          price: 50000,
          viewer_effective_price: 49700,
          is_wholesale: false,
          lot_sizes: [8, 5],
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
        },
      ],
    })

    const tradeButtons = wrapper.findAll('.trade-btn')
    await tradeButtons[2]!.trigger('click')
    await tradeButtons[2]!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.trade-suggestion-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('این لات‌ها مانده‌اند.')
    expect(wrapper.emitted('trade-completed')).toBeUndefined()

    await wrapper.setProps({ offers: [] })
    await flushPromises()

    expect(wrapper.find('.trade-suggestion-card').exists()).toBe(false)
  })

  it('syncs the suggestion overlay with live offer updates and closes it when the offer becomes inactive', async () => {
    apiFetchMock.mockResolvedValue({
      ok: false,
      json: async () => ({
        error_code: 'TRADE_LOT_UNAVAILABLE',
        detail: 'لطفا یکی از لات‌های جدید را انتخاب کنید.',
        offer_id: 15,
        available_lots: [8, 5],
      }),
    })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 15,
          user_id: 42,
          offer_type: 'buy',
          commodity_name: 'طلا',
          quantity: 13,
          remaining_quantity: 13,
          price: 49700,
          viewer_effective_price: 49700,
          is_wholesale: false,
          lot_sizes: [8, 5],
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
        },
      ],
    })

    const originalButtons = wrapper.findAll('.trade-btn')
    await originalButtons[2]!.trigger('click')
    await originalButtons[2]!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.trade-suggestion-card').exists()).toBe(true)
    expect(wrapper.findAll('.trade-suggestion-lot-btn')).toHaveLength(2)

    await wrapper.setProps({
      offers: [
        {
          id: 15,
          user_id: 42,
          offer_type: 'buy',
          commodity_name: 'طلا',
          quantity: 13,
          remaining_quantity: 8,
          price: 50000,
          viewer_effective_price: 49800,
          is_wholesale: false,
          lot_sizes: [8],
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
        },
      ],
    })
    await flushPromises()

    expect(wrapper.findAll('.trade-suggestion-lot-btn')).toHaveLength(1)
    expect(wrapper.text()).toContain('49,800')
    expect(wrapper.text()).toContain('8 عدد')

    await wrapper.setProps({
      offers: [
        {
          id: 15,
          user_id: 42,
          offer_type: 'buy',
          commodity_name: 'طلا',
          quantity: 13,
          remaining_quantity: 0,
          price: 50000,
          viewer_effective_price: 49800,
          is_wholesale: false,
          lot_sizes: [],
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'inactive',
        },
      ],
    })
    await flushPromises()

    expect(wrapper.find('.trade-suggestion-card').exists()).toBe(false)
  })
})