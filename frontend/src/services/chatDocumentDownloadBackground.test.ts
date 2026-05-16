import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type IndexedDbRecord = Record<string, any>

class AbortAwareResponse {
  ok: boolean
  status: number
  headers: { get: (key: string) => string | null }
  body: ReadableStream<Uint8Array> | null
  private blobFactory: () => Promise<Blob>

  constructor(options: {
    ok?: boolean
    status?: number
    headers?: Record<string, string>
    body?: ReadableStream<Uint8Array> | null
    blob?: Blob
  }) {
    this.ok = options.ok ?? true
    this.status = options.status ?? 200
    this.headers = {
      get: (key: string) => options.headers?.[key.toLowerCase()] ?? options.headers?.[key] ?? null,
    }
    this.body = options.body ?? null
    this.blobFactory = async () => options.blob ?? new Blob(['doc'], { type: 'application/pdf' })
  }

  async blob() {
    return this.blobFactory()
  }
}

function installIndexedDb(recordsInput: IndexedDbRecord[], options?: {
  hasStore?: boolean
  triggerUpgrade?: boolean
  failOpen?: boolean
  failGetAll?: boolean
}) {
  const records = new Map(recordsInput.map((record) => [record.messageId, { ...record }]))
  let hasStore = options?.hasStore ?? true

  const createTransaction = () => {
    const tx: {
      objectStore: (_store: string) => {
        put: (record: IndexedDbRecord) => void
        delete: (messageId: number) => void
        getAll: () => {
          result: IndexedDbRecord[]
          onsuccess: null | (() => void)
          onerror: null | (() => void)
        }
      }
      oncomplete: null | (() => void)
      onerror: null | (() => void)
      onabort: null | (() => void)
    } = {
      objectStore: () => ({
        put(record: IndexedDbRecord) {
          queueMicrotask(() => {
            records.set(record.messageId, { ...record })
            tx.oncomplete?.()
          })
        },
        delete(messageId: number) {
          queueMicrotask(() => {
            records.delete(messageId)
            tx.oncomplete?.()
          })
        },
        getAll() {
          const request = {
            result: [] as IndexedDbRecord[],
            onsuccess: null as null | (() => void),
            onerror: null as null | (() => void),
          }

          queueMicrotask(() => {
            if (options?.failGetAll) {
              request.onerror?.()
              return
            }

            request.result = [...records.values()].map((record) => ({ ...record }))
            request.onsuccess?.()
          })

          return request
        },
      }),
      oncomplete: null,
      onerror: null,
      onabort: null,
    }

    return tx
  }

  const db = {
    objectStoreNames: {
      contains: vi.fn(() => hasStore),
    },
    createObjectStore: vi.fn(() => {
      hasStore = true
    }),
    transaction: vi.fn(() => createTransaction()),
    close: vi.fn(),
    onversionchange: null as null | (() => void),
  }

  const openMock = vi.fn(() => {
    const request = {
      result: db,
      error: options?.failOpen ? new Error('open failed') : null,
      onupgradeneeded: null as null | ((event: Event) => void),
      onsuccess: null as null | (() => void),
      onerror: null as null | (() => void),
    }

    queueMicrotask(() => {
      if (options?.failOpen) {
        request.onerror?.()
        return
      }

      if (options?.triggerUpgrade) {
        request.onupgradeneeded?.({ target: { result: db } } as unknown as Event)
      }

      request.onsuccess?.()
    })

    return request
  })

  vi.stubGlobal('indexedDB', {
    open: openMock,
  })

  return {
    records,
    db,
    openMock,
    triggerVersionChange() {
      db.onversionchange?.()
    },
  }
}

describe('chatDocumentDownloadBackground', () => {
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  let fetchMock: ReturnType<typeof vi.fn>

  async function importFreshModule() {
    vi.resetModules()
    return import('./chatDocumentDownloadBackground')
  }

  beforeEach(() => {
    vi.useFakeTimers()
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('indexeddb unavailable')
      },
    })
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:downloaded-doc'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    document.body.innerHTML = ''
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    if (originalCreateObjectURL) {
      Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: originalCreateObjectURL })
    }
    if (originalRevokeObjectURL) {
      Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: originalRevokeObjectURL })
    }
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('starts a download, emits added/completed events, and caches the completed object url', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    const unsubscribe = service.subscribeToDocumentDownloads((event) => events.push(event))
    await service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockResolvedValueOnce(new AbortAwareResponse({
      headers: { 'content-type': 'application/pdf' },
      body: null,
      blob: new Blob(['pdf'], { type: 'application/pdf' }),
    }) as any)

    await service.startDocumentDownload({
      messageId: 10,
      userId: 7,
      fileId: 'file-10',
      fileName: 'doc.pdf',
      mimeType: 'application/pdf',
    })
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'completed'])
    expect(service.getCompletedDocumentDownloadUrl('file-10')).toBe('blob:downloaded-doc')
    expect(service.getPendingDocumentDownloadsForUser(7)).toEqual([])
    expect(fetchMock).toHaveBeenCalledWith('https://coin.test/api/chat/files/file-10?token=jwt', expect.any(Object))

    unsubscribe()
  })

  it('warns on subscriber failures and omits the auth token query when no token is available', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    service.subscribeToDocumentDownloads(() => {
      throw new Error('subscriber exploded')
    })
    service.subscribeToDocumentDownloads((event) => events.push(event))
    await service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => null })

    fetchMock.mockResolvedValueOnce(new AbortAwareResponse({
      headers: { 'content-type': 'application/pdf' },
      body: null,
      blob: new Blob(['pdf'], { type: 'application/pdf' }),
    }) as any)

    await service.startDocumentDownload({
      messageId: 18,
      userId: 7,
      fileId: 'file-18',
      fileName: 'tokenless.pdf',
    })
    await vi.runAllTimersAsync()

    expect(fetchMock).toHaveBeenCalledWith('https://coin.test/api/chat/files/file-18', expect.any(Object))
    expect(events.map((event) => event.type)).toEqual(['added', 'completed'])
    expect(warnSpy).toHaveBeenCalledWith('[documentDownloadService] subscriber failed:', expect.any(Error))
  })

  it('reuses an already completed download without hitting the network again', async () => {
    const service = await importFreshModule()
    await service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockResolvedValueOnce(new AbortAwareResponse({ body: null }) as any)
    await service.startDocumentDownload({ messageId: 11, userId: 7, fileId: 'file-11', fileName: 'done.pdf' })
    await vi.runAllTimersAsync()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await service.startDocumentDownload({ messageId: 12, userId: 7, fileId: 'file-11', fileName: 'done.pdf' })
    await vi.runAllTimersAsync()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('throws before initialization, ignores duplicate in-flight starts, and cancels active downloads', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToDocumentDownloads((event) => events.push(event))

    await expect(service.startDocumentDownload({
      messageId: 49,
      userId: 2,
      fileId: 'missing-init',
      fileName: 'missing.pdf',
    })).rejects.toThrow('[documentDownloadService] not initialized')

    await service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockImplementation((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      const signal = options.signal as AbortSignal
      signal.addEventListener('abort', () => reject(new Error('aborted')))
    }))

    await service.startDocumentDownload({ messageId: 50, userId: 3, fileId: 'file-50', fileName: 'hold.pdf' })
    await service.startDocumentDownload({ messageId: 50, userId: 3, fileId: 'file-50', fileName: 'hold.pdf' })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    service.cancelDocumentDownload(50)
    await vi.runAllTimersAsync()

    expect(events.some((event) => event.type === 'cancelled' && event.messageId === 50)).toBe(true)
    expect(service.getPendingDocumentDownloadsForUser(3)).toEqual([])
  })

  it('cancels queued downloads and emits the cancelled event', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToDocumentDownloads((event) => events.push(event))
    await service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockImplementation((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      const signal = options.signal as AbortSignal
      signal.addEventListener('abort', () => reject(new Error('aborted')))
    }))

    await service.startDocumentDownload({ messageId: 101, userId: 8, fileId: 'file-101', fileName: 'hold-a.pdf' })
    await service.startDocumentDownload({ messageId: 102, userId: 8, fileId: 'file-102', fileName: 'hold-b.pdf' })
    await service.startDocumentDownload({ messageId: 13, userId: 8, fileId: 'file-13', fileName: 'cancel.pdf' })
    expect(service.getPendingDocumentDownloadsForUser(8)).toHaveLength(3)

    service.cancelDocumentDownload(13)
    await vi.runAllTimersAsync()

    expect(events.some((event) => event.type === 'cancelled' && event.messageId === 13)).toBe(true)
    expect(service.getPendingDocumentDownloadsForUser(8).map((download) => download.messageId)).toEqual([101, 102])
  })

  it('marks non-transient failures as errors and clears pending state', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToDocumentDownloads((event) => events.push(event))
    await service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockRejectedValueOnce(new Error('permission denied'))

    await service.startDocumentDownload({
      messageId: 15,
      userId: 9,
      fileId: 'file-15',
      fileName: 'broken.pdf',
    })
    await vi.runAllTimersAsync()

    expect(events.some((event) => event.type === 'error' && event.messageId === 15 && /permission denied/i.test(event.errorMessage))).toBe(true)
    expect(service.getPendingDocumentDownloadsForUser(9)).toEqual([])
    expect(service.getCompletedDocumentDownloadUrl('file-15')).toBe('')
  })

  it('retries transient failures and emits progress before completing streamed downloads', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToDocumentDownloads((event) => events.push(event))
    await service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([1, 2]))
        controller.enqueue(new Uint8Array([3, 4]))
        controller.close()
      },
    })

    fetchMock
      .mockRejectedValueOnce(new Error('Failed to fetch'))
      .mockResolvedValueOnce(new AbortAwareResponse({
        headers: { 'content-type': 'application/pdf', 'content-length': '4' },
        body: stream,
      }) as any)

    await service.startDocumentDownload({ messageId: 14, userId: 9, fileId: 'file-14', fileName: 'retry.pdf' })
    await vi.runOnlyPendingTimersAsync()
    await vi.runAllTimersAsync()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(events.some((event) => event.type === 'progress' && event.progress === 50)).toBe(true)
    expect(events.some((event) => event.type === 'progress' && event.progress === 100)).toBe(true)
    expect(events.some((event) => event.type === 'completed' && event.messageId === 14)).toBe(true)
  })

  it('resumes queued downloads from indexeddb, cleans terminal records, and reuses the initialized resume promise', async () => {
    const indexedDb = installIndexedDb([
      {
        messageId: 61,
        userId: 12,
        fileId: 'file-61',
        fileName: 'queued.pdf',
        mimeType: 'application/pdf',
        phase: 'queued',
        progress: 25,
        downloadedBytes: 1,
        totalBytes: 4,
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      {
        messageId: 62,
        userId: 12,
        fileId: 'file-62',
        fileName: 'done.pdf',
        mimeType: 'application/pdf',
        phase: 'completed',
        progress: 100,
        downloadedBytes: 4,
        totalBytes: 4,
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      {
        messageId: 63,
        userId: 12,
        fileId: 'file-63',
        fileName: 'failed.pdf',
        mimeType: 'application/pdf',
        phase: 'failed',
        progress: 0,
        downloadedBytes: 0,
        totalBytes: 0,
        createdAt: '2026-01-01T00:00:00.000Z',
      },
    ], {
      hasStore: false,
      triggerUpgrade: true,
    })

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToDocumentDownloads((event) => events.push(event))

    fetchMock.mockResolvedValueOnce(new AbortAwareResponse({
      headers: { 'content-type': 'application/pdf' },
      body: null,
      blob: new Blob(['resume'], { type: 'application/pdf' }),
    }) as any)

    const firstInit = service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    const secondInit = service.initChatDocumentDownloadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'other-jwt' })

    expect(secondInit).toBe(firstInit)

    await firstInit
    await vi.runAllTimersAsync()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(events.filter((event) => event.type === 'added').map((event) => event.messageId)).toContain(61)
    expect(events.some((event) => event.type === 'completed' && event.messageId === 61)).toBe(true)
    expect(indexedDb.records.size).toBe(0)
    expect(indexedDb.db.createObjectStore).toHaveBeenCalledWith('pending', { keyPath: 'messageId' })

    indexedDb.triggerVersionChange()
    expect(indexedDb.db.close).toHaveBeenCalledTimes(1)
  })
})