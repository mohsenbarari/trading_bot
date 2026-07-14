import { flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const useOffersMocks = vi.hoisted(() => {
  const handlers = new Map<string, Array<(payload: any) => void>>()
  return {
    handlers,
    on: vi.fn((event: string, callback: (payload: any) => void) => {
      const current = handlers.get(event) ?? []
      current.push(callback)
      handlers.set(event, current)
    }),
    connect: vi.fn(),
    apiFetch: vi.fn(),
    intervalCallbacks: [] as Array<() => void>,
  }
})

vi.mock('./useWebSocket.ts', () => ({
  useWebSocket: () => ({
    on: useOffersMocks.on,
    connect: useOffersMocks.connect,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: useOffersMocks.apiFetch,
}))

function emitOfferEvent(event: string, payload: any) {
  for (const handler of useOffersMocks.handlers.get(event) ?? []) {
    handler(payload)
  }
}

async function importFreshUseOffers() {
  vi.resetModules()
  return import('./useOffers')
}

function pageResponse(items: any[], nextCursor: string | null = null, hasMore = false, status = 200) {
  return new Response(JSON.stringify({
    items,
    next_cursor: nextCursor,
    has_more: hasMore,
    page_size: items.length,
  }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('useOffers', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'token-1')
    useOffersMocks.handlers.clear()
    useOffersMocks.on.mockClear()
    useOffersMocks.connect.mockClear()
    useOffersMocks.apiFetch.mockReset()
    useOffersMocks.intervalCallbacks = []

    vi.spyOn(globalThis, 'setInterval').mockImplementation(((callback: TimerHandler) => {
      useOffersMocks.intervalCallbacks.push(callback as () => void)
      return useOffersMocks.intervalCallbacks.length as unknown as number
    }) as typeof setInterval)
    vi.spyOn(globalThis, 'clearInterval').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches offers, filters expired entries, and updates the reactive state', async () => {
    vi.setSystemTime(new Date('2026-05-14T12:00:00Z'))
    useOffersMocks.apiFetch.mockResolvedValue(pageResponse([
      { id: 1, expires_at_ts: Math.floor(Date.now() / 1000) + 30 },
      { id: 2, expires_at_ts: Math.floor(Date.now() / 1000) - 30 },
      { id: 3 },
    ]))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    await offersApi.fetchOffers()

    expect(useOffersMocks.apiFetch).toHaveBeenCalledWith('/api/offers/page?limit=50', {
      cache: 'no-store',
      retryNetwork: false,
    })
    expect(offersApi.offers.value.map((offer: { id: number }) => offer.id)).toEqual([1, 3])
    expect(offersApi.error.value).toBe('')
    expect(offersApi.isLoading.value).toBe(false)
  })

  it('starts polling once, reuses the shared interval, and refreshes on created/updated/expired realtime events', async () => {
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(pageResponse([{ id: 7, price: 100, viewer_effective_price: 90 }]))
      .mockResolvedValueOnce(pageResponse([{ id: 7, price: 100, viewer_effective_price: 90 }, { id: 8, price: 200 }]))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    offersApi.startPolling()
    await nextTick()
    await flushPromises()

    expect(useOffersMocks.connect).toHaveBeenCalledTimes(1)
    expect(globalThis.setInterval).toHaveBeenCalledTimes(1)
    expect(offersApi.offers.value).toEqual([{ id: 7, price: 100, viewer_effective_price: 90 }])

    offersApi.startPolling()
    expect(globalThis.setInterval).toHaveBeenCalledTimes(1)

    emitOfferEvent('offer:created', { id: 8 })
    await flushPromises()
    expect(offersApi.offers.value).toEqual([{ id: 7, price: 100, viewer_effective_price: 90 }, { id: 8, price: 200 }])

    emitOfferEvent('offer:updated', { id: 8, price: 250, remaining_quantity: 5 })
    expect(offersApi.offers.value.find((offer: { id: number }) => offer.id === 8)).toMatchObject({
      id: 8,
      price: 250,
      remaining_quantity: 5,
    })
    expect(offersApi.offers.value.find((offer: { id: number }) => offer.id === 7)).toMatchObject({
      id: 7,
      viewer_effective_price: 90,
    })

    emitOfferEvent('offer:updated', { id: 8, status: 'completed', remaining_quantity: 0 })
    expect(offersApi.offers.value.map((offer: { id: number }) => offer.id)).toEqual([7])

    emitOfferEvent('offer:expired', { id: 7 })
    expect(offersApi.offers.value.map((offer: { id: number }) => offer.id)).toEqual([])

    offersApi.stopPolling()
    expect(globalThis.clearInterval).toHaveBeenCalledTimes(1)
  })

  it('uses public offer identity for cross-server realtime updates while preserving the local id', async () => {
    useOffersMocks.apiFetch.mockResolvedValueOnce(pageResponse([
      { id: 41, offer_public_id: 'ofr-shared', price: 100, remaining_quantity: 10 },
    ]))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()
    await offersApi.fetchOffers()

    emitOfferEvent('offer:updated', {
      id: 9041,
      offer_public_id: 'ofr-shared',
      remaining_quantity: 6,
    })
    expect(offersApi.offers.value).toEqual([
      { id: 41, offer_public_id: 'ofr-shared', price: 100, remaining_quantity: 6 },
    ])

    emitOfferEvent('offer:expired', {
      id: 9041,
      offer_public_id: 'ofr-shared',
      status: 'expired',
    })
    expect(offersApi.offers.value).toEqual([])
  })

  it('keeps the current offers on transient fetch errors and queues silent overlap after the current fetch', async () => {
    let resolveFirstFetch!: (value: Response) => void
    useOffersMocks.apiFetch
      .mockImplementationOnce(() => new Promise<Response>((resolve) => {
        resolveFirstFetch = resolve
      }))
      .mockRejectedValueOnce(new Error('network unstable'))
      .mockRejectedValueOnce(new Error('network unstable'))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()
    offersApi.offers.value = [{ id: 11, price: 300 }]

    const firstFetch = offersApi.fetchOffers(true)
    await offersApi.fetchOffers(true)
    expect(useOffersMocks.apiFetch).toHaveBeenCalledTimes(1)
    expect(useOffersMocks.apiFetch).toHaveBeenCalledWith('/api/offers/page?limit=50', {
      cache: 'no-store',
      retryNetwork: false,
    })

    resolveFirstFetch(pageResponse([{ id: 11, price: 350 }]))
    await firstFetch
    await flushPromises()
    expect(useOffersMocks.apiFetch).toHaveBeenCalledTimes(2)
    expect(offersApi.offers.value).toEqual([{ id: 11, price: 350 }])

    await offersApi.fetchOffers()
    expect(useOffersMocks.apiFetch).toHaveBeenCalledTimes(3)
    expect(offersApi.offers.value).toEqual([{ id: 11, price: 350 }])
    expect(offersApi.error.value).toBe('دریافت لیست لفظ‌ها ممکن نشد.')
  })

  it('loads more than 100 offers across cursor pages without duplicates', async () => {
    const rows = Array.from({ length: 137 }, (_, index) => ({
      id: 137 - index,
      offer_public_id: `ofr-${137 - index}`,
    }))
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(pageResponse(rows.slice(0, 50), 'cursor-50', true))
      .mockResolvedValueOnce(pageResponse(rows.slice(50, 100), 'cursor-100', true))
      .mockResolvedValueOnce(pageResponse(rows.slice(100), null, false))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    await offersApi.fetchOffers()
    await offersApi.loadMoreOffers()
    await offersApi.loadMoreOffers()

    expect(useOffersMocks.apiFetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/offers/page?limit=50',
      '/api/offers/page?limit=50&cursor=cursor-50',
      '/api/offers/page?limit=50&cursor=cursor-100',
    ])
    expect(offersApi.offers.value).toHaveLength(137)
    expect(new Set(offersApi.offers.value.map((offer: any) => offer.offer_public_id)).size).toBe(137)
    expect(offersApi.hasMore.value).toBe(false)
  })

  it('deduplicates page boundaries by public id and preserves loaded pages on silent refresh', async () => {
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(pageResponse([
        { id: 3, offer_public_id: 'ofr-3', price: 300 },
        { id: 2, offer_public_id: 'ofr-2', price: 200 },
      ], 'cursor-2', true))
      .mockResolvedValueOnce(pageResponse([
        { id: 2, offer_public_id: 'ofr-2', price: 200 },
        { id: 1, offer_public_id: 'ofr-1', price: 100 },
      ], null, false))
      .mockResolvedValueOnce(pageResponse([
        { id: 4, offer_public_id: 'ofr-4', price: 400 },
        { id: 3, offer_public_id: 'ofr-3', price: 350 },
      ], 'fresh-cursor', true))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    await offersApi.fetchOffers()
    await offersApi.loadMoreOffers()
    expect(offersApi.offers.value.map((offer: any) => offer.offer_public_id)).toEqual(['ofr-3', 'ofr-2', 'ofr-1'])

    await offersApi.fetchOffers(true)
    expect(offersApi.offers.value.map((offer: any) => offer.offer_public_id)).toEqual(['ofr-4', 'ofr-3', 'ofr-2', 'ofr-1'])
    expect(offersApi.offers.value.find((offer: any) => offer.offer_public_id === 'ofr-3')?.price).toBe(350)
    expect(offersApi.hasMore.value).toBe(false)
  })

  it('resets pagination when filters change and never reuses a cursor from another filter set', async () => {
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(pageResponse([{ id: 3, offer_public_id: 'ofr-3' }], 'all-cursor', true))
      .mockResolvedValueOnce(pageResponse([{ id: 2, offer_public_id: 'ofr-2' }], 'all-cursor-2', true))
      .mockResolvedValueOnce(pageResponse([{ id: 9, offer_public_id: 'buy-cash-coin' }], null, false))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    await offersApi.fetchOffers()
    await offersApi.loadMoreOffers()
    await offersApi.setFilters({
      offerType: 'buy',
      settlementType: 'cash',
      commodityId: 7,
      ownOnly: true,
    })

    expect(useOffersMocks.apiFetch.mock.calls[2]?.[0]).toBe(
      '/api/offers/page?limit=50&offer_type=buy&settlement_type=cash&commodity_id=7&own_only=true',
    )
    expect(String(useOffersMocks.apiFetch.mock.calls[2]?.[0])).not.toContain('cursor=')
    expect(offersApi.offers.value.map((offer: any) => offer.offer_public_id)).toEqual(['buy-cash-coin'])
    expect(offersApi.hasMore.value).toBe(false)
  })

  it('resets shared filters when a newly mounted market view requests the default dataset', async () => {
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(pageResponse([{ id: 7, offer_public_id: 'buy-only' }]))
      .mockResolvedValueOnce(pageResponse([{ id: 8, offer_public_id: 'all-offers' }]))

    const { useOffers } = await importFreshUseOffers()
    const firstView = useOffers()
    await firstView.setFilters({ offerType: 'buy' })

    const remountedView = useOffers()
    await remountedView.setFilters({})

    expect(useOffersMocks.apiFetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/offers/page?limit=50&offer_type=buy',
      '/api/offers/page?limit=50',
    ])
    expect(remountedView.offers.value.map((offer: any) => offer.offer_public_id)).toEqual(['all-offers'])
  })

  it('keeps rows and the cursor after a load-more failure so retry can succeed', async () => {
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(pageResponse([{ id: 2, offer_public_id: 'ofr-2' }], 'retry-cursor', true))
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(pageResponse([{ id: 1, offer_public_id: 'ofr-1' }], null, false))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    await offersApi.fetchOffers()
    await offersApi.loadMoreOffers()
    expect(offersApi.offers.value.map((offer: any) => offer.offer_public_id)).toEqual(['ofr-2'])
    expect(offersApi.paginationError.value).toBe('دریافت ادامه لفظ‌های بازار ممکن نشد.')
    expect(offersApi.hasMore.value).toBe(true)

    await offersApi.loadMoreOffers()
    expect(useOffersMocks.apiFetch.mock.calls[1]?.[0]).toBe('/api/offers/page?limit=50&cursor=retry-cursor')
    expect(useOffersMocks.apiFetch.mock.calls[2]?.[0]).toBe('/api/offers/page?limit=50&cursor=retry-cursor')
    expect(offersApi.offers.value.map((offer: any) => offer.offer_public_id)).toEqual(['ofr-2', 'ofr-1'])
    expect(offersApi.paginationError.value).toBe('')
    expect(offersApi.hasMore.value).toBe(false)
  })

  it('handles an empty final page without exposing a load-more action', async () => {
    useOffersMocks.apiFetch.mockResolvedValueOnce(pageResponse([], null, false))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()
    await offersApi.fetchOffers()

    expect(offersApi.offers.value).toEqual([])
    expect(offersApi.hasMore.value).toBe(false)
    await offersApi.loadMoreOffers()
    expect(useOffersMocks.apiFetch).toHaveBeenCalledTimes(1)
  })

  it('short-circuits when auth token is missing and does not start loading', async () => {
    localStorage.removeItem('auth_token')
    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    await offersApi.fetchOffers()

    expect(useOffersMocks.apiFetch).not.toHaveBeenCalled()
    expect(offersApi.isLoading.value).toBe(false)
  })

  it('surfaces list errors on non-ok responses and keeps previous offers during silent refresh failures', async () => {
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'boom' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'still boom' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()
    offersApi.offers.value = [{ id: 25, price: 111 }]

    await offersApi.fetchOffers(false)
    expect(offersApi.error.value).toBe('دریافت لیست لفظ‌ها ممکن نشد.')
    expect(offersApi.offers.value).toEqual([{ id: 25, price: 111 }])

    offersApi.error.value = ''
    await offersApi.fetchOffers(true)
    expect(offersApi.error.value).toBe('')
    expect(offersApi.offers.value).toEqual([{ id: 25, price: 111 }])
  })
})
