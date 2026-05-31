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
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'info').mockImplementation(() => {})
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

  it('prewarms cached files, formats cache size, and clears legacy debug overlays', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const debugBox = document.createElement('div')
    debugBox.id = 'chat-file-debug-box'
    document.body.appendChild(debugBox)
    localStorage.setItem('chatFileDebug', '1')

    cachedEntries.set('prewarm-1', {
      blob: new Blob(['warm'], { type: 'application/pdf' }),
      fileName: 'warm.pdf',
      mimeType: 'application/pdf',
      size: 4,
      cachedAt: Date.now(),
    })

    await fileHandler.prewarmFileCache('prewarm-1')
    expect(fileHandler.isFileCached('prewarm-1')).toBe(true)
    expect(await fileHandler.getCacheSize()).toBe('0.00 MB')

    fileHandler.initChatFileDebugOverlay()
    expect(localStorage.getItem('chatFileDebug')).toBeNull()
    expect(document.getElementById('chat-file-debug-box')).toBeNull()
  })

  it('reuses cached object URLs for seeded files and revokes them when the cache is cleared', async () => {
    const fileHandler = await import('./useChatFileHandler')

    await fileHandler.seedFileCache('media-1', new Blob(['media'], { type: 'image/png' }), 'media.png', 'image/png')

    expect(await fileHandler.getCachedFileObjectUrl('media-1')).toBe('blob:cached-file')
    expect(await fileHandler.getCachedFileObjectUrl('media-1')).toBe('blob:cached-file')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)

    await fileHandler.clearFileCache()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:cached-file')
    expect(await fileHandler.getCachedFileObjectUrl('media-1')).toBeNull()
  })

  it('downloads uncached files once, reuses the cache, and exposes the wrapper composable api', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const fetchMock = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(['downloaded'], { type: 'application/octet-stream' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await fileHandler.downloadFileToDisk('disk-1', '/api/chat/files/disk-1', 'archive.zip')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(anchorClickSpy).toHaveBeenCalledTimes(1)
    expect(fileHandler.isFileCached('disk-1')).toBe(true)

    const api = fileHandler.useChatFileHandler()
    expect(api.canShareFiles()).toBe(true)
    expect(api.downloadingFiles).toBeDefined()

    await fileHandler.downloadFileToDisk('disk-1', '/api/chat/files/disk-1', 'archive.zip')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(anchorClickSpy).toHaveBeenCalledTimes(2)
  })

  it('returns false for unsupported share paths and treats AbortError as a successful multi-share dismissal', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const shareMock = navigator.share as ReturnType<typeof vi.fn>

    await fileHandler.seedFileCache('share-1', new Blob(['a'], { type: 'application/octet-stream' }), 'file.bin', 'application/octet-stream')

    Object.defineProperty(navigator, 'share', {
      configurable: true,
      writable: true,
      value: undefined,
    })
    expect(await fileHandler.shareFile('share-1', 'file.bin', 'application/octet-stream')).toBe(false)
    expect(fileHandler.canShareFiles()).toBe(false)

    Object.defineProperty(navigator, 'share', {
      configurable: true,
      writable: true,
      value: shareMock,
    })
    shareMock.mockRejectedValueOnce(new DOMException('dismissed', 'AbortError'))

    const dismissed = await fileHandler.shareMultipleFiles(['share-1'])
    expect(dismissed).toBe(true)
  })

  it('throws when the network download fails during open and reports zero bytes for invalid cached entries', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const fetchMock = vi.fn(async () => ({ ok: false, status: 503 }))
    vi.stubGlobal('fetch', fetchMock)
    cachedEntries.set('broken-entry', { nope: true })

    await expect(fileHandler.handleFileClick('missing-1', '/api/chat/files/missing-1', 'missing.pdf')).rejects.toThrow('HTTP 503')
    expect(await fileHandler.getCacheBytes()).toBe(0)
    expect(fileHandler.useChatFileHandler().downloadingFiles['missing-1']).toBeUndefined()
  })

  it('prefetches uncached files for sharing, reuses the cache, and returns false on failed prefetches', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    fetchMock.mockResolvedValueOnce({
      ok: true,
      blob: async () => new Blob(['remote'], { type: 'application/pdf' }),
    })
    expect(await fileHandler.shareFile('remote-1', 'remote.pdf', 'application/pdf', '/api/chat/files/remote-1')).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fileHandler.isFileCached('remote-1')).toBe(true)

    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 })
    expect(await fileHandler.shareFile('remote-2', 'remote.pdf', 'application/pdf', '/api/chat/files/remote-2')).toBe(false)
    expect(await fileHandler.shareFile('remote-3', 'remote.pdf', 'application/pdf')).toBe(false)
    expect(await fileHandler.shareFile('', 'remote.pdf', 'application/pdf')).toBe(false)
  })

  it('returns false when file sharing is unsupported or canShare probes fail', async () => {
    const fileHandler = await import('./useChatFileHandler')

    Object.defineProperty(navigator, 'share', {
      configurable: true,
      writable: true,
      value: undefined,
    })
    expect(fileHandler.canShareFiles()).toBe(false)

    Object.defineProperty(navigator, 'share', {
      configurable: true,
      writable: true,
      value: vi.fn(async () => {}),
    })
    Object.defineProperty(navigator, 'canShare', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {
        throw new Error('probe failed')
      }),
    })
    expect(fileHandler.canShareFiles()).toBe(false)
  })

  it('handles multi-share edge cases and generic share rejections', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const shareMock = navigator.share as ReturnType<typeof vi.fn>
    const canShareMock = navigator.canShare as ReturnType<typeof vi.fn>

    expect(await fileHandler.shareMultipleFiles([])).toBe(false)

    Object.defineProperty(navigator, 'share', {
      configurable: true,
      writable: true,
      value: undefined,
    })
    expect(await fileHandler.shareMultipleFiles(['missing'])).toBe(false)

    Object.defineProperty(navigator, 'share', {
      configurable: true,
      writable: true,
      value: shareMock,
    })
    Object.defineProperty(navigator, 'canShare', {
      configurable: true,
      writable: true,
      value: canShareMock,
    })

    await fileHandler.seedFileCache('multi-1', new Blob(['m1'], { type: 'application/pdf' }), 'm1.pdf', 'application/pdf')
    canShareMock.mockReturnValueOnce(false)
    shareMock.mockRejectedValueOnce(new Error('share rejected'))

    expect(await fileHandler.shareMultipleFiles(['multi-1'])).toBe(false)
  })

  it('surfaces cache clear failures and tolerates cache-size scan failures', async () => {
    const fileHandler = await import('./useChatFileHandler')

    localforageInstance.iterate.mockRejectedValueOnce(new Error('scan failed'))
    expect(await fileHandler.getCacheBytes()).toBe(0)

    localforageInstance.clear.mockRejectedValueOnce(new Error('quota exceeded'))
    await expect(fileHandler.clearFileCache()).rejects.toThrow('quota exceeded')
    expect(console.error).toHaveBeenCalled()
  })

  it('falls back from inline open to cached download when opening a blob tab fails and tolerates cache-write failures', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementationOnce(() => {
        throw new Error('popup blocked')
      })
      .mockImplementation(() => {})
    const fetchMock = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(['pdf'], { type: 'application/pdf' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await fileHandler.seedFileCache('inline-1', new Blob(['inline'], { type: 'application/pdf' }), 'inline.pdf', 'application/pdf')
    await fileHandler.handleFileClick('inline-1', '/api/chat/files/inline-1', 'inline.pdf')

    expect(anchorClickSpy).toHaveBeenCalledTimes(2)
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2)
    expect(console.warn).toHaveBeenCalledWith('[chat-file] openBlobInTab anchor click failed', expect.any(Error))

    localforageInstance.setItem.mockRejectedValueOnce(new Error('quota-full'))
    await fileHandler.handleFileClick('inline-2', '/api/chat/files/inline-2', 'remote.pdf')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fileHandler.isFileCached('inline-2')).toBe(true)
    expect(console.warn).toHaveBeenCalledWith('[useChatFileHandler] cache write failed', expect.any(Error))
  })

  it('covers share fast-path fallback branches for canShare, synchronous throws, async rejects, and File construction failure', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const shareMock = navigator.share as ReturnType<typeof vi.fn>
    const canShareMock = navigator.canShare as ReturnType<typeof vi.fn>
    const originalFile = File

    await fileHandler.seedFileCache('share-fast', new Blob(['bin'], { type: 'application/octet-stream' }), 'share.bin', 'application/octet-stream')

    canShareMock.mockReturnValueOnce(false)
    shareMock.mockResolvedValueOnce(undefined)
    expect(await fileHandler.shareFile('share-fast', 'share.bin', 'application/octet-stream')).toBe(true)

    canShareMock.mockImplementationOnce(() => {
      throw new Error('probe exploded')
    })
    shareMock.mockResolvedValueOnce(undefined)
    expect(await fileHandler.shareFile('share-fast', 'share.bin', 'application/octet-stream')).toBe(true)

    shareMock.mockImplementationOnce(() => {
      throw new DOMException('blocked', 'NotAllowedError')
    })
    expect(await fileHandler.shareFile('share-fast', 'share.bin', 'application/octet-stream')).toBe(false)

    shareMock.mockRejectedValueOnce(new Error('share rejected'))
    expect(await fileHandler.shareFile('share-fast', 'share.bin', 'application/octet-stream')).toBe(false)

    Object.defineProperty(globalThis, 'File', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {
        throw new Error('file construction failed')
      }),
    })
    expect(await fileHandler.shareFile('share-fast', 'share.bin', 'application/octet-stream')).toBe(false)

    Object.defineProperty(globalThis, 'File', {
      configurable: true,
      writable: true,
      value: originalFile,
    })
  })

  it('short-circuits prewarm and share/download requests while a fetch is already in flight', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const fetchMock = vi.fn(() => new Promise((resolve) => {
      setTimeout(() => {
        resolve({
          ok: true,
          blob: async () => new Blob(['remote'], { type: 'application/pdf' }),
        })
      }, 0)
    }))
    vi.stubGlobal('fetch', fetchMock)

    cachedEntries.set('prewarm-hit', {
      blob: new Blob(['warm'], { type: 'application/pdf' }),
      fileName: 'warm.pdf',
      mimeType: 'application/pdf',
      size: 4,
      cachedAt: Date.now(),
    })

    await fileHandler.prewarmFileCache('prewarm-hit')
    await fileHandler.prewarmFileCache('prewarm-hit')
    expect(localforageInstance.getItem).toHaveBeenCalledTimes(1)

    const slowShare = fileHandler.shareFile('slow-share', 'slow.pdf', 'application/pdf', '/api/chat/files/slow-share')
    const secondShare = fileHandler.shareFile('slow-share', 'slow.pdf', 'application/pdf', '/api/chat/files/slow-share')
    await expect(Promise.all([slowShare, secondShare])).resolves.toEqual([true, true])

    const slowDownload = fileHandler.downloadFileToDisk('slow-download', '/api/chat/files/slow-download', 'slow.zip')
    const secondDownload = fileHandler.downloadFileToDisk('slow-download', '/api/chat/files/slow-download', 'slow.zip')
    await Promise.all([slowDownload, secondDownload])

    const slowOpen = fileHandler.handleFileClick('slow-open', '/api/chat/files/slow-open', 'slow-open.pdf')
    const secondOpen = fileHandler.handleFileClick('slow-open', '/api/chat/files/slow-open', 'slow-open.pdf')
    await Promise.all([slowOpen, secondOpen])

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(anchorClickSpy).toHaveBeenCalledTimes(4)
  })

  it('hydrates local blob sources through one shared cache promise for media-style callers', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const localBlob = new Blob(['image'], { type: 'image/png' })
    const fetchMock = vi.fn(() => new Promise((resolve) => {
      setTimeout(() => {
        resolve({
          ok: true,
          blob: async () => localBlob,
        })
      }, 0)
    }))
    vi.stubGlobal('fetch', fetchMock)

    const first = fileHandler.ensureFileCached('media-1', 'photo.png', {
      mimeType: 'image/png',
      localUrl: 'blob:photo',
    })
    const second = fileHandler.ensureFileCached('media-1', 'photo.png', {
      mimeType: 'image/png',
      localUrl: 'blob:photo',
    })

    const [firstEntry, secondEntry] = await Promise.all([first, second])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(firstEntry?.fileName).toBe('photo.png')
    expect(firstEntry?.mimeType).toBe('image/png')
    expect(secondEntry?.mimeType).toBe('image/png')
    expect(fileHandler.isFileCached('media-1')).toBe(true)
  })

  it('covers cache-read failures, no-op ids, healthy seed skips, and object-url revocation timers', async () => {
    vi.useFakeTimers()
    const fileHandler = await import('./useChatFileHandler')
    const fetchMock = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(['remote'], { type: 'application/pdf' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await fileHandler.handleFileClick('', '/api/chat/files/empty', 'empty.pdf')
    await fileHandler.downloadFileToDisk('', '/api/chat/files/empty', 'empty.pdf')
    await fileHandler.prewarmFileCache('')
    await fileHandler.seedFileCache('', new Blob(['x']), 'x.bin')
    expect(fetchMock).not.toHaveBeenCalled()

    localforageInstance.getItem.mockRejectedValueOnce(new Error('read failed'))
    await fileHandler.handleFileClick('read-fail', '/api/chat/files/read-fail', 'read-fail.pdf')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(console.warn).toHaveBeenCalledWith('[useChatFileHandler] cache read failed', expect.any(Error))

    await fileHandler.seedFileCache('healthy', new Blob(['healthy'], { type: 'application/pdf' }), 'healthy.pdf', 'application/pdf')
    localforageInstance.setItem.mockClear()
    await fileHandler.seedFileCache('healthy', new Blob(['replacement'], { type: 'application/pdf' }), 'replacement.pdf', 'application/pdf')
    expect(localforageInstance.setItem).not.toHaveBeenCalled()

    await fileHandler.seedFileCache('zero', new Blob([], { type: 'application/pdf' }), 'zero.pdf', 'application/pdf')
    await fileHandler.seedFileCache('zero', new Blob(['filled'], { type: 'application/pdf' }), 'filled.pdf', 'application/pdf')
    expect(fileHandler.isFileCached('zero')).toBe(true)

    await fileHandler.handleFileClick('healthy', '/api/chat/files/healthy', 'healthy.pdf')
    await fileHandler.downloadFileToDisk('healthy', '/api/chat/files/healthy', 'healthy.pdf')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(URL.revokeObjectURL).toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('promotes IndexedDB cache hits into memory, reuses cached opens, patches missing mime types for share, and tolerates seed/multi-share file failures', async () => {
    const fileHandler = await import('./useChatFileHandler')
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const shareMock = navigator.share as ReturnType<typeof vi.fn>
    const originalFile = File

    cachedEntries.set('idb-open', {
      blob: new Blob(['cached'], { type: 'application/pdf' }),
      fileName: 'cached.pdf',
      mimeType: 'application/pdf',
      size: 6,
      cachedAt: Date.now(),
    })

    await fileHandler.handleFileClick('idb-open', '/api/chat/files/idb-open', 'cached.pdf')
    expect(fileHandler.isFileCached('idb-open')).toBe(true)
    expect(anchorClickSpy).toHaveBeenCalledTimes(1)

    shareMock.mockResolvedValueOnce(undefined)
    cachedEntries.set('share-mime', {
      blob: new Blob(['raw'], { type: '' }),
      fileName: 'raw.bin',
      mimeType: '',
      size: 3,
      cachedAt: Date.now(),
    })
    expect(await fileHandler.shareFile('share-mime', 'raw.bin', 'application/octet-stream')).toBe(true)
    const sharePayload = shareMock.mock.calls.at(-1)?.[0] as ShareData
    expect(sharePayload.files?.[0]?.type).toBe('application/octet-stream')

    localforageInstance.setItem.mockRejectedValueOnce(new Error('seed quota'))
    await fileHandler.seedFileCache('seed-fail', new Blob(['seed'], { type: 'application/pdf' }), 'seed.pdf', 'application/pdf')
    expect(console.warn).toHaveBeenCalledWith('[useChatFileHandler] seed cache write failed', expect.any(Error))

    await fileHandler.seedFileCache('multi-fail', new Blob(['m'], { type: 'application/pdf' }), 'm.pdf', 'application/pdf')
    Object.defineProperty(globalThis, 'File', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {
        throw new Error('multi file failed')
      }),
    })
    expect(await fileHandler.shareMultipleFiles(['multi-fail'])).toBe(false)
    expect(console.warn).toHaveBeenCalledWith('[useChatFileHandler] multi-share File() failed', expect.any(Error))
    Object.defineProperty(globalThis, 'File', {
      configurable: true,
      writable: true,
      value: originalFile,
    })
  })
})