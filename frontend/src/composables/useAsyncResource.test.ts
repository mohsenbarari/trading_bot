import { describe, expect, it, vi } from 'vitest'
import { AppHttpError } from '../utils/httpErrorPolicy'
import { useAsyncResource } from './useAsyncResource'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, resolve, reject }
}

describe('useAsyncResource', () => {
  it('moves through loading, ready, and a truthful successful empty state', async () => {
    const loader = vi.fn()
      .mockResolvedValueOnce([{ id: 1 }])
      .mockResolvedValueOnce([])
    const resource = useAsyncResource(loader)

    expect(resource.status.value).toBe('idle')
    const initial = resource.execute()
    expect(resource.status.value).toBe('loading')
    expect(resource.isBusy.value).toBe(true)
    await initial
    expect(resource.status.value).toBe('ready')
    expect(resource.data.value).toEqual([{ id: 1 }])

    await resource.refresh()
    expect(resource.status.value).toBe('empty')
    expect(resource.hasResolvedData.value).toBe(true)
    expect(resource.hasData.value).toBe(false)
  })

  it('never turns a failed initial request into a false empty state', async () => {
    const resource = useAsyncResource(async () => {
      throw new Error('load failed')
    })

    await resource.execute()

    expect(resource.status.value).toBe('error')
    expect(resource.hasResolvedData.value).toBe(false)
    expect(resource.data.value).toBeNull()
    expect(resource.error.value).toMatchObject({ message: 'load failed' })
  })

  it('retains prior data and marks it stale after a refresh failure', async () => {
    const loader = vi.fn()
      .mockResolvedValueOnce({ balance: 12 })
      .mockRejectedValueOnce(new Error('refresh failed'))
    const resource = useAsyncResource(loader)

    await resource.execute()
    const succeededAt = resource.lastSucceededAt.value
    expect(succeededAt).toEqual(expect.any(Number))
    const refresh = resource.refresh()
    expect(resource.status.value).toBe('stale')
    expect(resource.data.value).toEqual({ balance: 12 })
    await refresh

    expect(resource.status.value).toBe('stale')
    expect(resource.data.value).toEqual({ balance: 12 })
    expect(resource.error.value).toMatchObject({ message: 'refresh failed' })
    expect(resource.lastSucceededAt.value).toBe(succeededAt)
  })

  it('distinguishes offline and reconnecting while retaining data', async () => {
    const loader = vi.fn()
      .mockResolvedValueOnce(['cached'])
      .mockRejectedValueOnce(new AppHttpError({ errorCode: 'NETWORK_ERROR', detail: 'offline' }))
      .mockResolvedValueOnce(['fresh'])
    const resource = useAsyncResource(loader)

    await resource.execute()
    await resource.refresh()
    expect(resource.status.value).toBe('offline')
    expect(resource.data.value).toEqual(['cached'])

    const retry = resource.retry()
    expect(resource.status.value).toBe('reconnecting')
    await retry
    expect(resource.status.value).toBe('ready')
    expect(resource.data.value).toEqual(['fresh'])
    expect(loader.mock.calls.map(([context]) => context.reason)).toEqual(['initial', 'refresh', 'reconnect'])
  })

  it('is latest-wins even when an older loader ignores its abort signal', async () => {
    const oldRequest = deferred<string[]>()
    const newRequest = deferred<string[]>()
    const loader = vi.fn()
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise)
    const resource = useAsyncResource(loader)

    const oldRun = resource.execute()
    const newRun = resource.refresh()
    newRequest.resolve(['new'])
    await newRun
    oldRequest.resolve(['old'])
    await oldRun

    expect(resource.status.value).toBe('ready')
    expect(resource.data.value).toEqual(['new'])
    expect(resource.lastSucceededAt.value).toEqual(expect.any(Number))
    expect(loader.mock.calls[0]![0].signal.aborted).toBe(true)
  })

  it('can explicitly mark settled data stale and cancel an active refresh', async () => {
    const pending = deferred<{ id: number }>()
    const resource = useAsyncResource(
      vi.fn().mockResolvedValueOnce({ id: 1 }).mockReturnValueOnce(pending.promise),
    )

    await resource.execute()
    resource.markStale()
    expect(resource.status.value).toBe('stale')

    void resource.refresh()
    expect(resource.isBusy.value).toBe(true)
    resource.cancel()
    expect(resource.isBusy.value).toBe(false)
    expect(resource.status.value).toBe('stale')
    expect(resource.data.value).toEqual({ id: 1 })
  })
})
