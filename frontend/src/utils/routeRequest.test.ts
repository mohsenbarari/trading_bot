import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppHttpError } from './httpErrorPolicy'

const routeRequestMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('./auth', () => ({
  apiFetch: routeRequestMocks.apiFetch,
}))

function responseOf(body: unknown, init: ResponseInit = {}) {
  return new Response(body === undefined ? undefined : JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
    ...init,
  })
}

describe('routeRequest', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    routeRequestMocks.apiFetch.mockReset()
  })

  it('uses one non-retrying apiFetch call for authenticated requests and parses JSON', async () => {
    routeRequestMocks.apiFetch.mockResolvedValue(responseOf({ id: 7 }))
    const { routeRequestJson } = await import('./routeRequest')

    await expect(routeRequestJson<{ id: number }>('/api/example', {
      headers: { 'x-route': 'home' },
      timeoutMs: null,
    })).resolves.toEqual({ id: 7 })

    expect(routeRequestMocks.apiFetch).toHaveBeenCalledTimes(1)
    expect(routeRequestMocks.apiFetch).toHaveBeenCalledWith('/api/example', expect.objectContaining({
      headers: { 'x-route': 'home' },
      retryNetwork: false,
      signal: expect.any(AbortSignal),
      trackConnectionState: false,
    }))
  })

  it('uses native fetch in public mode and does not call apiFetch', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(responseOf({ public: true }))
    const { routeRequestJson } = await import('./routeRequest')

    await expect(routeRequestJson<{ public: boolean }>('/api/public', {
      mode: 'public',
      timeoutMs: null,
    })).resolves.toEqual({ public: true })

    expect(fetchSpy).toHaveBeenCalledWith('/api/public', expect.objectContaining({
      signal: expect.any(AbortSignal),
    }))
    expect(routeRequestMocks.apiFetch).not.toHaveBeenCalled()
  })

  it('normalizes non-ok responses with the shared HTTP error policy', async () => {
    routeRequestMocks.apiFetch.mockResolvedValue(responseOf({ detail: 'دسترسی مجاز نیست' }, { status: 403 }))
    const { routeRequest } = await import('./routeRequest')

    const request = routeRequest('/api/private', {
      timeoutMs: null,
      errorContext: { scope: 'page', resourceLabel: 'حساب' },
    })

    await expect(request).rejects.toMatchObject({
      name: 'AppHttpError',
      status: 403,
      detail: 'دسترسی مجاز نیست',
      context: { scope: 'page', resourceLabel: 'حساب' },
    })
  })

  it('aborts at the timeout boundary and returns a structured timeout error', async () => {
    vi.useFakeTimers()
    routeRequestMocks.apiFetch.mockImplementation((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(options.signal?.reason), { once: true })
    }))
    const { routeRequest } = await import('./routeRequest')

    const request = routeRequest('/api/slow', { timeoutMs: 25 })
    const rejection = expect(request).rejects.toMatchObject({
      name: 'AppHttpError',
      status: null,
      errorCode: 'REQUEST_TIMEOUT',
    })
    await vi.advanceTimersByTimeAsync(25)
    await rejection
  })

  it('forwards an external abort without converting it to a route failure', async () => {
    const controller = new AbortController()
    routeRequestMocks.apiFetch.mockImplementation((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(options.signal?.reason), { once: true })
    }))
    const { routeRequest } = await import('./routeRequest')

    const request = routeRequest('/api/cancelled', { signal: controller.signal, timeoutMs: null })
    controller.abort(new DOMException('Caller cancelled', 'AbortError'))

    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('normalizes a network failure and lets a manual retry start a new call', async () => {
    routeRequestMocks.apiFetch
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(responseOf(undefined, { status: 204 }))
    const { routeRequest } = await import('./routeRequest')

    await expect(routeRequest('/api/retry', { timeoutMs: null })).rejects.toBeInstanceOf(AppHttpError)
    await expect(routeRequest('/api/retry', { timeoutMs: null })).resolves.toMatchObject({ status: 204 })
    expect(routeRequestMocks.apiFetch).toHaveBeenCalledTimes(2)
  })
})
