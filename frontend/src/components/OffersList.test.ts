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

function buildTradeOffer(overrides: Record<string, unknown> = {}) {
  return {
    id: 29,
    user_id: 15,
    offer_type: 'sell',
    commodity_name: 'سکه',
    quantity: 10,
    remaining_quantity: 10,
    price: 52000,
    viewer_effective_price: 52000,
    is_wholesale: true,
    lot_sizes: null,
    notes: null,
    created_at: 'امروز',
    customer_badge_visible: false,
    customer_management_name: null,
    customer_tier: null,
    status: 'active',
    ...overrides,
  }
}

async function confirmTrade(wrapper: Awaited<ReturnType<typeof mountOffersList>>, buttonIndex = 0) {
  const button = () => wrapper.findAll('.trade-btn')[buttonIndex]!
  await button().trigger('click')
  await button().trigger('click')
  await flushPromises()
}

function storedTradeIntents(userId = 77): any[] {
  return JSON.parse(sessionStorage.getItem(`market_trade_intents_v1:user:${userId}`) || '[]')
}

describe('OffersList.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    sessionStorage.clear()
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  afterEach(() => {
    sessionStorage.clear()
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
  }, 10_000)

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

  it('labels cash and tomorrow offers explicitly in market cards', async () => {
    const wrapper = await mountOffersList({
      offers: [
        {
          id: 21,
          offer_type: 'buy',
          settlement_type: 'cash',
          commodity_name: 'امام',
          quantity: 20,
          remaining_quantity: 20,
          price: 176000,
          viewer_effective_price: 176000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
        },
        {
          id: 22,
          offer_type: 'sell',
          settlement_type: 'tomorrow',
          commodity_name: 'ربع بهار',
          quantity: 40,
          remaining_quantity: 40,
          price: 178000,
          viewer_effective_price: 178000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
        },
      ],
    })

    expect(wrapper.findAll('.offer-settlement').map((label) => label.text())).toEqual([
      'تسویه:نقد حاضر ☀️',
      'تسویه:فردا 📆',
    ])
    expect(wrapper.findAll('.offer-header .offer-settlement')).toHaveLength(2)
    expect(wrapper.findAll('.offer-main .offer-settlement')).toHaveLength(0)
    expect(wrapper.findAll('.ui-settlement-badge--cash')).toHaveLength(1)
    expect(wrapper.findAll('.ui-settlement-badge--tomorrow')).toHaveLength(1)
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
    expect(wrapper.text()).not.toContain('خُرد')
    expect(wrapper.text()).not.toContain('یکجا')
    expect(wrapper.text()).toContain('سطح نامشخص')
  })

  it('does not restore the initial quantity when remaining quantity is zero or malformed', async () => {
    const wrapper = await mountOffersList({
      offers: [
        buildTradeOffer({ id: 29, quantity: 10, remaining_quantity: 0 }),
        buildTradeOffer({ id: 30, quantity: 20, remaining_quantity: 'invalid' }),
      ],
    })

    expect(wrapper.text()).toContain('0 عدد')
    expect(wrapper.find('.trade-btn').exists()).toBe(false)
  })

  it('renders manually expired offers as read-only history cards', async () => {
    const wrapper = await mountOffersList({
      offers: [
        {
          id: 4,
          user_id: 12,
          status: 'expired',
          expire_reason: 'manual',
          offer_type: 'sell',
          commodity_name: 'ربع سکه',
          quantity: 20,
          remaining_quantity: 20,
          price: 51000,
          viewer_effective_price: 51000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
        },
      ],
      hasMoreExpired: true,
      canLoadExpired: true,
    })

    expect(wrapper.find('.expired-ribbon').text()).toBe('منقضی')
    expect(wrapper.find('.offer-card-wrap').classes()).toContain('is-expired')
    expect(wrapper.find('.trade-btn').exists()).toBe(false)
    expect(wrapper.find('.cancel-own-offer-btn').exists()).toBe(false)

    await wrapper.find('.expired-load-more-btn').trigger('click')
    expect(wrapper.emitted('load-more-expired')).toHaveLength(1)
  })

  it('renders traded history stamps and keeps terminal offers read-only', async () => {
    const wrapper = await mountOffersList({
      offers: [
        {
          id: 5,
          user_id: 77,
          status: 'completed',
          history_state: 'traded',
          is_read_only: true,
          offer_type: 'buy',
          commodity_name: 'طلای آب‌شده',
          quantity: 12,
          remaining_quantity: 0,
          traded_quantity: 12,
          price: 72000,
          viewer_effective_price: 72000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
        },
        {
          id: 6,
          user_id: 88,
          status: 'expired',
          history_state: 'traded',
          is_read_only: true,
          is_partially_traded: true,
          offer_type: 'sell',
          commodity_name: 'ربع سکه',
          quantity: 40,
          remaining_quantity: 20,
          traded_quantity: 20,
          price: 54000,
          viewer_effective_price: 54000,
          is_wholesale: false,
          lot_sizes: [7],
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
        },
      ],
    })

    expect(wrapper.find('.trade-btn').exists()).toBe(false)
    expect(wrapper.find('.cancel-own-offer-btn').exists()).toBe(false)
    expect(wrapper.findAll('[data-test="history-stamp"]').map((stamp) => stamp.text())).toEqual([
      'معامله‌شده',
      'معامله‌شده 20 عدد',
    ])
    expect(wrapper.findAll('.traded-ribbon')).toHaveLength(2)
    expect(wrapper.find('.expired-ribbon').exists()).toBe(false)
    expect(wrapper.findAll('.offer-card-wrap')[0]!.classes()).toContain('is-traded')
    expect(wrapper.findAll('.offer-card-wrap')[1]!.classes()).toContain('is-traded')
    expect(wrapper.findAll('.offer-card-wrap')[1]!.classes()).not.toContain('is-expired')
    expect(wrapper.findAll('.quantity-badge').map((badge) => badge.text())).toEqual(['12 عدد', '40 عدد'])
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
      retryNetwork: false,
    }))
    expect(JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))).toEqual({
      offer_id: 9,
      quantity: 25,
      idempotency_key: expect.any(String),
    })
    expect(wrapper.emitted('trade-completed')).toHaveLength(1)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('locks trade execution during an in-flight request and does not duplicate rapid taps', async () => {
    vi.useFakeTimers()
    let resolveTrade: ((value: any) => void) | null = null
    apiFetchMock.mockImplementation(() => new Promise((resolve) => {
      resolveTrade = resolve
    }))

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 19,
          user_id: 15,
          offer_type: 'sell',
          commodity_name: 'سکه',
          quantity: 10,
          remaining_quantity: 10,
          price: 52000,
          viewer_effective_price: 52000,
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

    const tradeButton = wrapper.get('.trade-btn')
    await tradeButton.trigger('click')
    await tradeButton.trigger('click')
    await tradeButton.trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    expect(apiFetchMock).toHaveBeenCalledWith('/api/trades/', expect.objectContaining({
      method: 'POST',
      retryNetwork: false,
    }))

    if (!resolveTrade) {
      throw new Error('Expected pending trade resolver')
    }
    ;(resolveTrade as (value: any) => void)({ ok: true, json: async () => ({}) })
    await flushPromises()

    expect(wrapper.emitted('trade-completed')).toHaveLength(1)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('keeps the same trade idempotency key after an ambiguous network failure retry', async () => {
    vi.useFakeTimers()
    apiFetchMock
      .mockRejectedValueOnce(new Error('NetworkError'))
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 29,
          user_id: 15,
          offer_type: 'sell',
          commodity_name: 'سکه',
          quantity: 10,
          remaining_quantity: 10,
          price: 52000,
          viewer_effective_price: 52000,
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

    const tradeButton = () => wrapper.get('.trade-btn')
    await tradeButton().trigger('click')
    await tradeButton().trigger('click')
    await flushPromises()

    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    expect(wrapper.text()).toContain('تکرار همین درخواست معامله دوم نمی‌سازد')

    await tradeButton().trigger('click')
    await tradeButton().trigger('click')
    await flushPromises()

    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))
    expect(secondBody.idempotency_key).toBe(firstBody.idempotency_key)
    expect(wrapper.emitted('trade-completed')).toHaveLength(1)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it.each([408, 425, 429, 500, 502, 503, 504])('keeps the exact trade intent after an ambiguous HTTP %s response', async (status) => {
    apiFetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'temporary failure' }), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 90 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const wrapper = await mountOffersList({ offers: [buildTradeOffer({ id: 90 })] })

    await confirmTrade(wrapper)
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    expect(storedTradeIntents()).toEqual([
      expect.objectContaining({
        offerId: 90,
        quantity: 10,
        idempotencyKey: firstBody.idempotency_key,
        status: 'uncertain',
      }),
    ])

    await confirmTrade(wrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))
    expect(secondBody).toEqual(firstBody)
    expect(wrapper.emitted('trade-completed')).toHaveLength(1)
    expect(storedTradeIntents()).toEqual([])
    wrapper.unmount()
  })

  it('keeps the exact trade intent after the coded temporary contention response', async () => {
    apiFetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: {
          error_code: 'TRADE_CONTENTION_BUSY',
          message: 'این لفظ در حال معامله است.',
        },
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 901 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const wrapper = await mountOffersList({ offers: [buildTradeOffer({ id: 901 })] })

    await confirmTrade(wrapper)
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    expect(storedTradeIntents()[0]?.idempotencyKey).toBe(firstBody.idempotency_key)
    expect(storedTradeIntents()[0]?.status).toBe('uncertain')

    await confirmTrade(wrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))
    expect(secondBody).toEqual(firstBody)
    expect(storedTradeIntents()).toEqual([])
    wrapper.unmount()
  })

  it('keeps uncoded business conflicts definitive', async () => {
    apiFetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'مقدار درخواست معتبر نیست.' }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 902 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const wrapper = await mountOffersList({ offers: [buildTradeOffer({ id: 902 })] })

    await confirmTrade(wrapper)
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    expect(storedTradeIntents()).toEqual([])

    await confirmTrade(wrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))
    expect(secondBody.idempotency_key).not.toBe(firstBody.idempotency_key)
    wrapper.unmount()
  })

  it('does not create a trade intent before the authenticated user identity is ready', async () => {
    apiFetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: 903 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const offer = buildTradeOffer({ id: 903 })
    const wrapper = await mountOffersList({
      offers: [offer],
      currentUserId: undefined,
      currentUserReady: false,
    })

    expect(wrapper.get('.trade-btn').attributes('disabled')).toBeDefined()
    await confirmTrade(wrapper)
    expect(apiFetchMock).not.toHaveBeenCalled()
    expect(sessionStorage.length).toBe(0)

    await wrapper.setProps({ currentUserId: 77, currentUserReady: true })
    await confirmTrade(wrapper)
    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    expect(storedTradeIntents()).toEqual([])
    wrapper.unmount()
  })

  it('preserves the trade intent after an aborted request', async () => {
    apiFetchMock
      .mockRejectedValueOnce(new DOMException('request timed out', 'AbortError'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 91 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const wrapper = await mountOffersList({ offers: [buildTradeOffer({ id: 91 })] })

    await confirmTrade(wrapper)
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    expect(wrapper.text()).toContain('تکرار همین درخواست معامله دوم نمی‌سازد')

    await confirmTrade(wrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))
    expect(secondBody).toEqual(firstBody)
    wrapper.unmount()
  })

  it('restores an uncertain trade intent after the component is reopened', async () => {
    apiFetchMock
      .mockRejectedValueOnce(new Error('NetworkError'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 92 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const offer = buildTradeOffer({ id: 92 })
    const firstWrapper = await mountOffersList({ offers: [offer] })

    await confirmTrade(firstWrapper)
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    firstWrapper.unmount()

    const reopenedWrapper = await mountOffersList({ offers: [offer] })
    await confirmTrade(reopenedWrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))

    expect(secondBody).toEqual(firstBody)
    expect(reopenedWrapper.emitted('trade-completed')).toHaveLength(1)
    expect(storedTradeIntents()).toEqual([])
    reopenedWrapper.unmount()
  })

  it('uses one intent when an old in-flight response completes after a reopened retry', async () => {
    let resolveFirst: ((value: Response) => void) | null = null
    let resolveSecond: ((value: Response) => void) | null = null
    apiFetchMock
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveSecond = resolve }))
    const offer = buildTradeOffer({ id: 93 })
    const firstWrapper = await mountOffersList({ offers: [offer] })

    const firstButton = firstWrapper.get('.trade-btn')
    await firstButton.trigger('click')
    await firstButton.trigger('click')
    await flushPromises()
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    expect(storedTradeIntents()[0]?.status).toBe('in_flight')
    firstWrapper.unmount()

    const reopenedWrapper = await mountOffersList({ offers: [offer] })
    await confirmTrade(reopenedWrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))
    expect(secondBody).toEqual(firstBody)

    if (!resolveSecond || !resolveFirst) throw new Error('Expected both trade resolvers')
    ;(resolveFirst as (value: Response) => void)(new Response(JSON.stringify({ id: 93 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await flushPromises()
    expect(firstWrapper.emitted('trade-completed')).toBeUndefined()
    expect(storedTradeIntents()[0]?.idempotencyKey).toBe(firstBody.idempotency_key)

    ;(resolveSecond as (value: Response) => void)(new Response(JSON.stringify({ id: 93 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await flushPromises()

    expect(reopenedWrapper.emitted('trade-completed')).toHaveLength(1)
    expect(storedTradeIntents()).toEqual([])
    reopenedWrapper.unmount()
  })

  it('does not discard an uncertain intent when the offer temporarily disappears', async () => {
    apiFetchMock
      .mockRejectedValueOnce(new Error('NetworkError'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 94 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const offer = buildTradeOffer({ id: 94 })
    const wrapper = await mountOffersList({ offers: [offer] })

    await confirmTrade(wrapper)
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    await wrapper.setProps({ offers: [] })
    await flushPromises()
    expect(storedTradeIntents()[0]?.idempotencyKey).toBe(firstBody.idempotency_key)

    await wrapper.setProps({ offers: [offer] })
    await flushPromises()
    await confirmTrade(wrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))
    expect(secondBody).toEqual(firstBody)
    wrapper.unmount()
  })

  it('closes definitive failures and successes so later operations receive fresh keys', async () => {
    apiFetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'invalid quantity' }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 95 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 96 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const wrapper = await mountOffersList({ offers: [buildTradeOffer({ id: 95 })] })

    await confirmTrade(wrapper)
    await confirmTrade(wrapper)
    await confirmTrade(wrapper)

    const bodies = apiFetchMock.mock.calls.map((call) => JSON.parse(String(call[1].body)))
    expect(bodies[1].idempotency_key).not.toBe(bodies[0].idempotency_key)
    expect(bodies[2].idempotency_key).not.toBe(bodies[1].idempotency_key)
    expect(wrapper.emitted('trade-completed')).toHaveLength(2)
    expect(storedTradeIntents()).toEqual([])
    wrapper.unmount()
  })

  it('blocks a changed quantity while another intent for the offer is uncertain', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('NetworkError'))
    const offer = buildTradeOffer({
      id: 96,
      quantity: 15,
      remaining_quantity: 15,
      is_wholesale: false,
      lot_sizes: [5, 10],
    })
    const wrapper = await mountOffersList({ offers: [offer] })

    await confirmTrade(wrapper, 0)
    expect(JSON.parse(String(apiFetchMock.mock.calls[0]![1].body)).quantity).toBe(5)

    await confirmTrade(wrapper, 1)
    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('نتیجه درخواست قبلی این لفظ هنوز مشخص نیست')
    wrapper.unmount()
  })

  it('keeps in-memory retry protection when sessionStorage writes fail', async () => {
    const setItemSpy = vi.spyOn(window.sessionStorage.__proto__, 'setItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })
    apiFetchMock
      .mockRejectedValueOnce(new Error('NetworkError'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 97 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const wrapper = await mountOffersList({ offers: [buildTradeOffer({ id: 97 })] })

    await confirmTrade(wrapper)
    const firstBody = JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))
    await confirmTrade(wrapper)
    const secondBody = JSON.parse(String(apiFetchMock.mock.calls[1]![1].body))

    expect(secondBody).toEqual(firstBody)
    expect(wrapper.emitted('trade-completed')).toHaveLength(1)
    wrapper.unmount()
    setItemSpy.mockRestore()
  })

  it('discards malformed persisted data without blocking a new trade intent', async () => {
    sessionStorage.setItem('market_trade_intents_v1:user:77', '{malformed-json')
    apiFetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: 98 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const wrapper = await mountOffersList({ offers: [buildTradeOffer({ id: 98 })] })

    await confirmTrade(wrapper)

    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(String(apiFetchMock.mock.calls[0]![1].body))).toEqual({
      offer_id: 98,
      quantity: 10,
      idempotency_key: expect.stringMatching(/^trade:/),
    })
    expect(storedTradeIntents()).toEqual([])
    wrapper.unmount()
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

    expect(apiFetchMock).toHaveBeenCalledWith('/api/offers/12', expect.objectContaining({ method: 'DELETE', retryNetwork: false }))
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

  it('filters expired offers and renders critical timer styling for near-expiry cards', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-28T09:00:00Z'))

    const nowSec = Date.now() / 1000
    const wrapper = await mountOffersList({
      expiryMinutes: 10,
      offers: [
        {
          id: 30,
          user_id: 50,
          offer_type: 'sell',
          commodity_name: 'سکه فعال',
          quantity: 2,
          remaining_quantity: 2,
          price: 52000,
          viewer_effective_price: 52000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
          expires_at_ts: nowSec + 30,
        },
        {
          id: 31,
          user_id: 51,
          offer_type: 'buy',
          commodity_name: 'سکه منقضی',
          quantity: 1,
          remaining_quantity: 1,
          price: 50000,
          viewer_effective_price: 50000,
          is_wholesale: true,
          lot_sizes: null,
          notes: null,
          created_at: 'امروز',
          customer_badge_visible: false,
          customer_management_name: null,
          customer_tier: null,
          status: 'active',
          expires_at_ts: nowSec - 5,
        },
        {
          id: 32,
          user_id: 52,
          offer_type: 'buy',
          commodity_name: 'طلای بدون تایمر',
          quantity: 3,
          remaining_quantity: 3,
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
          expires_at_ts: null,
        },
      ],
    })

    const cards = wrapper.findAll('.offer-card-wrap')
    expect(cards).toHaveLength(2)
    expect(wrapper.text()).toContain('سکه فعال')
    expect(wrapper.text()).toContain('طلای بدون تایمر')
    expect(wrapper.text()).not.toContain('سکه منقضی')

    expect(cards[0]!.classes()).toContain('has-timer')
    expect(cards[0]!.classes()).toContain('timer-critical')
    expect(cards[0]!.attributes('style')).toContain('--t-pct')
    expect(cards[1]!.classes()).not.toContain('has-timer')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('falls back to the generic trade error when the failure response has no JSON body', async () => {
    vi.useFakeTimers()
    apiFetchMock.mockResolvedValue({
      ok: false,
      json: async () => {
        throw new Error('broken payload')
      },
    })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 33,
          user_id: 53,
          offer_type: 'sell',
          commodity_name: 'خطای معامله',
          quantity: 4,
          remaining_quantity: 4,
          price: 48000,
          viewer_effective_price: 48000,
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

    const tradeButton = wrapper.get('.trade-btn')
    await tradeButton.trigger('click')
    await tradeButton.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در انجام معامله')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('falls back to the generic cancel error when the own-offer failure response has no JSON body', async () => {
    vi.useFakeTimers()
    apiFetchMock.mockResolvedValue({
      ok: false,
      json: async () => {
        throw new Error('broken payload')
      },
    })

    const wrapper = await mountOffersList({
      offers: [
        {
          id: 34,
          user_id: 77,
          offer_type: 'buy',
          commodity_name: 'خطای لغو',
          quantity: 6,
          remaining_quantity: 6,
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
          is_own_offer: true,
        },
      ],
    })

    await wrapper.get('.cancel-own-offer-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در منقضی کردن لفظ')

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
    expect(wrapper.text()).toContain('تکرار همین درخواست معامله دوم نمی‌سازد')

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

  it('emits active pagination requests and disables the action while loading', async () => {
    const wrapper = await mountOffersList({
      offers: [buildTradeOffer()],
      hasMoreActive: true,
      activeLoading: false,
    })

    const button = wrapper.get('.active-load-more-btn')
    await button.trigger('click')
    expect(wrapper.emitted('load-more-active')).toHaveLength(1)

    await wrapper.setProps({ activeLoading: true })
    expect(wrapper.get('.active-load-more-btn').attributes()).toHaveProperty('disabled')
    expect(wrapper.get('.active-load-more-btn').text()).toContain('در حال دریافت')
  })

  it('offers retry after initial and paginated active-list failures', async () => {
    const initialWrapper = await mountOffersList({
      offers: [],
      activeLoadError: 'دریافت بازار ممکن نشد.',
    })
    expect(initialWrapper.text()).toContain('دریافت بازار ممکن نشد.')
    await initialWrapper.get('.market-page-retry-btn').trigger('click')
    expect(initialWrapper.emitted('retry-active')).toHaveLength(1)

    const paginatedWrapper = await mountOffersList({
      offers: [buildTradeOffer()],
      hasMoreActive: true,
      activeLoadError: 'دریافت ادامه بازار ممکن نشد.',
    })
    expect(paginatedWrapper.get('.active-load-error').text()).toContain('دریافت ادامه بازار ممکن نشد.')
    await paginatedWrapper.get('.market-page-retry-btn').trigger('click')
    expect(paginatedWrapper.emitted('retry-active')).toHaveLength(1)
    expect(paginatedWrapper.find('.active-load-more-btn').exists()).toBe(false)
  })
})
