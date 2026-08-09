import { beforeEach, describe, expect, it, vi } from 'vitest'

const storageMocks = vi.hoisted(() => ({
  clear: vi.fn(),
  iterate: vi.fn(),
}))

vi.mock('localforage', () => ({
  default: {
    createInstance: () => storageMocks,
  },
}))

import {
  clearStorageFileCache,
  getStorageCacheBytes,
  getStorageCacheSize,
  StorageCacheClearUnavailableError,
  StorageCacheSizeUnavailableError,
} from './useStorageCacheMetrics'

describe('useStorageCacheMetrics.ts', () => {
  beforeEach(() => {
    storageMocks.clear.mockReset()
    storageMocks.iterate.mockReset()
  })

  it('reports a real empty cache as zero', async () => {
    storageMocks.iterate.mockResolvedValue(undefined)

    await expect(getStorageCacheBytes()).resolves.toBe(0)
    await expect(getStorageCacheSize()).resolves.toBe('0.00 MB')
  })

  it('sums actual cached Blob bytes and ignores invalid entries', async () => {
    const oneMiB = new Blob([new Uint8Array(1024 * 1024)])
    const entries = [{ blob: oneMiB, size: 1 }, { blob: 'not-a-blob' }, null]
    storageMocks.iterate.mockImplementation(
      async (iterator: (value: unknown) => void | Promise<void>) => {
        for (const entry of entries) await iterator(entry)
      },
    )

    await expect(getStorageCacheBytes()).resolves.toBe(1024 * 1024)
    await expect(getStorageCacheSize()).resolves.toBe('1.00 MB')
  })

  it('rejects a failed size scan instead of returning zero and does not log raw details', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    storageMocks.iterate.mockRejectedValue(new Error('raw-indexeddb-scan-cause'))

    const result = getStorageCacheSize()

    await expect(result).rejects.toBeInstanceOf(StorageCacheSizeUnavailableError)
    await expect(getStorageCacheBytes()).rejects.toThrow('Local file cache size is unavailable.')
    expect(warnSpy).not.toHaveBeenCalled()
    expect(errorSpy).not.toHaveBeenCalled()

    warnSpy.mockRestore()
    errorSpy.mockRestore()
  })

  it('clears the persistent file store and sanitizes a provider failure', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    storageMocks.clear.mockResolvedValueOnce(undefined)
    await expect(clearStorageFileCache()).resolves.toBeUndefined()

    storageMocks.clear.mockRejectedValueOnce(new Error('raw-indexeddb-clear-cause'))
    await expect(clearStorageFileCache()).rejects.toBeInstanceOf(StorageCacheClearUnavailableError)
    expect(errorSpy).not.toHaveBeenCalled()
    errorSpy.mockRestore()
  })
})
