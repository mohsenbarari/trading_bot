import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
  })
}

describe('OffersList.vue', () => {
  beforeEach(() => {
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
})