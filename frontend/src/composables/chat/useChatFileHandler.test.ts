import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const cachedEntries = new Map<string, unknown>()
const localforageInstance = {
  getItem: vi.fn(async (key: string) => cachedEntries.get(key) ?? null),
  setItem: vi.fn(async (key: string, value: unknown) => {
    cachedEntries.set(key, value)
    return value
  }),
  clear: vi.fn(async () => {
    cachedEntries.clear()
  }),
  iterate: vi.fn(async (iterator: (value: unknown) => void | Promise<void>) => {
    for (const value of cachedEntries.values()) {
      await iterator(value)
    }
  }),
}

vi.mock('localforage', () => ({
  default: {
    createInstance: () => localforageInstance,
  },
}))

describe('useChatFileHandler.ts', () => {
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  const originalNavigatorShare = navigator.share
  const originalNavigatorCanShare = navigator.canShare

  beforeEach(() => {
    cachedEntries.clear()
    localforageInstance.getItem.mockClear()
    localforageInstance.setItem.mockClear()
    localforageInstance.clear.mockClear()
    localforageInstance.iterate.mockClear()
    vi.resetModules()

    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:cached-file'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {}),
    })
    Object.defineProperty(navigator, 'share', {
      configurable: true,
      writable: true,
      value: vi.fn(async () => {}),
    })
    Object.defineProperty(navigator, 'canShare', {
      configurable: true,
      writable: true,
      value: vi.fn(() => true),
    })
  })

  afterEach(() => {
    if (originalCreateObjectURL) {
      Object.defineProperty(URL, 'createObjectURL', {
        configurable: true,
        writable: true,
        value: originalCreateObjectURL,
      })
    } else {
      delete (URL as Partial<typeof URL>).createObjectURL
    }
    if (originalRevokeObjectURL) {
      Object.defineProperty(URL, 'revokeObjectURL', {
        configurable: true,
        writable: true,
        value: originalRevokeObjectURL,
      })
    } else {
      delete (URL as Partial<typeof URL>).revokeObjectURL
    }
    if (originalNavigatorShare) {
      Object.defineProperty(navigator, 'share', {
        configurable: true,
        writable: true,
        value: originalNavigatorShare,
      })
    } else {
      delete (navigator as Partial<Navigator>).share
    }
    if (originalNavigatorCanShare) {
      Object.defineProperty(navigator, 'canShare', {
        configurable: true,
        writable: true,
        value: originalNavigatorCanShare,
      })
    } else {
      delete (navigator as Partial<Navigator>).canShare
    }
  })

  it('tracks seeded files in the cache registry and clears them cleanly', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const blob = new Blob(['hello'], { type: 'text/plain' })

    await fileHandler.seedFileCache('doc-1', blob, 'note.txt', 'text/plain')

    expect(fileHandler.isFileCached('doc-1')).toBe(true)
    expect(fileHandler.useFileCacheRegistry()['doc-1']).toBe(true)
    expect(await fileHandler.getCacheBytes()).toBe(5)

    await fileHandler.clearFileCache()

    expect(fileHandler.isFileCached('doc-1')).toBe(false)
    expect(await fileHandler.getCacheBytes()).toBe(0)
  })

  it('opens a seeded cached file without hitting the network again', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await fileHandler.seedFileCache('doc-2', new Blob(['cached'], { type: 'text/plain' }), 'cached.txt', 'text/plain')
    await fileHandler.handleFileClick('doc-2', '/api/chat/files/doc-2', 'cached.txt')

    expect(fetchMock).not.toHaveBeenCalled()
    expect(anchorClickSpy).toHaveBeenCalledTimes(1)
  })

  it('shares multiple cached files through the native share sheet payload', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const shareMock = navigator.share as ReturnType<typeof vi.fn>

    await fileHandler.seedFileCache('file-a', new Blob(['a'], { type: 'application/pdf' }), 'alpha.pdf', 'application/pdf')
    await fileHandler.seedFileCache('file-b', new Blob(['b'], { type: 'image/png' }), 'beta.png', 'image/png')

    const shared = await fileHandler.shareMultipleFiles(['file-a', 'file-b'])

    expect(shared).toBe(true)
    expect(shareMock).toHaveBeenCalledTimes(1)

    const sharePayload = shareMock.mock.calls[0]?.[0] as ShareData
    expect(sharePayload.files).toHaveLength(2)
    expect(sharePayload.files?.map((file) => file.name)).toEqual(['alpha.pdf', 'beta.png'])
  })
})