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
    useOffersMocks.apiFetch.mockResolvedValue(new Response(JSON.stringify([
      { id: 1, expires_at_ts: Math.floor(Date.now() / 1000) + 30 },
      { id: 2, expires_at_ts: Math.floor(Date.now() / 1000) - 30 },
      { id: 3 },
    ]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()

    await offersApi.fetchOffers()

    expect(useOffersMocks.apiFetch).toHaveBeenCalledWith('/api/offers/')
    expect(offersApi.offers.value.map((offer: { id: number }) => offer.id)).toEqual([1, 3])
    expect(offersApi.error.value).toBe('')
    expect(offersApi.isLoading.value).toBe(false)
  })

  it('starts polling once, reuses the shared interval, and refreshes on created/updated/expired realtime events', async () => {
    useOffersMocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: 7, price: 100, viewer_effective_price: 90 }]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: 7, price: 100, viewer_effective_price: 90 }, { id: 8, price: 200 }]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

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

  it('keeps the current offers on transient fetch errors and avoids silent overlap when already fetching', async () => {
    let resolveFirstFetch!: (value: Response) => void
    useOffersMocks.apiFetch
      .mockImplementationOnce(() => new Promise<Response>((resolve) => {
        resolveFirstFetch = resolve
      }))
      .mockRejectedValueOnce(new Error('network unstable'))

    const { useOffers } = await importFreshUseOffers()
    const offersApi = useOffers()
    offersApi.offers.value = [{ id: 11, price: 300 }]

    const firstFetch = offersApi.fetchOffers(true)
    await offersApi.fetchOffers(true)
    expect(useOffersMocks.apiFetch).toHaveBeenCalledTimes(1)

    resolveFirstFetch(new Response(JSON.stringify([{ id: 11, price: 350 }]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await firstFetch
    expect(offersApi.offers.value).toEqual([{ id: 11, price: 350 }])

    await offersApi.fetchOffers()
    expect(offersApi.offers.value).toEqual([{ id: 11, price: 350 }])
    expect(offersApi.error.value).toBe('دریافت لیست لفظ‌ها ممکن نشد.')
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
