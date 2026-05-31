import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { PendingUpload } from './chatUploadBackground'

type XhrScenario = (xhr: MockXHR) => void

function installIndexedDb(initialRecords: Array<Record<string, any>>) {
  const records = new Map(initialRecords.map((record) => [record.id, { ...record }]))
  const deletedIds: number[] = []

  const buildRequest = <T,>(result: T) => {
    const request: {
      result: T
      error: null
      onsuccess: null | (() => void)
      onerror: null | (() => void)
    } = {
      result,
      error: null,
      onsuccess: null,
      onerror: null,
    }
    Promise.resolve().then(() => {
      request.onsuccess?.()
    })
    return request
  }

  const createTransaction = () => {
    const tx: {
      objectStore: (_store: string) => {
        put: (value: Record<string, any>) => void
        delete: (id: number) => void
        get: (id: number) => ReturnType<typeof buildRequest>
        getAll: () => ReturnType<typeof buildRequest>
      }
      oncomplete: null | (() => void)
      onerror: null | (() => void)
      onabort: null | (() => void)
    } = {
      objectStore: () => ({
        put(value: Record<string, any>) {
          records.set(value.id, { ...value })
          Promise.resolve().then(() => Promise.resolve().then(() => tx.oncomplete?.()))
        },
        delete(id: number) {
          records.delete(id)
          deletedIds.push(id)
          Promise.resolve().then(() => Promise.resolve().then(() => tx.oncomplete?.()))
        },
        get(id: number) {
          return buildRequest(records.get(id))
        },
        getAll() {
          return buildRequest(Array.from(records.values()))
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
      contains: () => true,
    },
    transaction: () => createTransaction(),
    close: vi.fn(),
    onversionchange: null as null | (() => void),
  }

  vi.stubGlobal('indexedDB', {
    open: () => {
      const request: {
        result: typeof db
        error: null
        onupgradeneeded: null | ((event: { target: { result: typeof db } }) => void)
        onsuccess: null | (() => void)
        onerror: null | (() => void)
      } = {
        result: db,
        error: null,
        onupgradeneeded: null,
        onsuccess: null,
        onerror: null,
      }
      Promise.resolve().then(() => {
        request.onsuccess?.()
      })
      return request
    },
  })

  return { records, deletedIds }
}

class MockXHR {
  static instances: MockXHR[] = []
  static scenarios: XhrScenario[] = []
  static reset() {
    MockXHR.instances = []
    MockXHR.scenarios = []
  }

  static enqueueScenario(scenario: XhrScenario) {
    MockXHR.scenarios.push(scenario)
  }

  method = ''
  url = ''
  headers: Record<string, string> = {}
  responseText = ''
  status = 0
  timeout = 0
  formData: FormData | null = null
  upload = {
    onprogress: null as ((event: ProgressEvent<EventTarget> & { lengthComputable: boolean; loaded: number; total: number }) => void) | null,
  }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  ontimeout: (() => void) | null = null

  constructor() {
    MockXHR.instances.push(this)
  }

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }

  send(formData: FormData) {
    this.formData = formData
    const scenario = MockXHR.scenarios.shift()
    if (!scenario) {
      throw new Error('Missing MockXHR scenario')
    }
    scenario(this)
  }

  abort() {
    this.onabort?.()
  }
}

describe('chatUploadBackground', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  async function importFreshModule() {
    vi.resetModules()
    return import('./chatUploadBackground')
  }

  function makeBaseSubmitParams(overrides: Partial<Parameters<any>[0]> = {}) {
    return {
      optimisticId: -101,
      userId: -77,
      roomKind: 'channel' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['image-bytes'], { type: 'image/png' }),
      fileName: 'image.png',
      mimeType: 'image/png',
      thumbnail: 'data:image/png;base64,thumb',
      width: 120,
      height: 80,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      localBlobUrl: 'blob:local-image',
      ...overrides,
    }
  }

  beforeEach(() => {
    vi.useFakeTimers()
    MockXHR.reset()
    vi.spyOn(globalThis, 'setInterval').mockImplementation(() => 1 as any)
    vi.spyOn(globalThis, 'clearInterval').mockImplementation(() => undefined)
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return {
          ok: true,
          status: 204,
          json: async () => ({}),
        } as Response
      }
      if (url.includes('/send')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 9001,
            sender_id: 15,
            receiver_id: 77,
            content: JSON.parse(String(init?.body ?? '{}')).content,
            message_type: 'image',
            is_read: false,
            created_at: '2026-05-14T00:00:00Z',
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('indexeddb unavailable')
      },
    })
    vi.stubGlobal('XMLHttpRequest', MockXHR as any)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:restored-upload'),
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: undefined,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('builds optimistic messages and generates unique optimistic ids', async () => {
    const service = await importFreshModule()
    const optimisticIdA = service.createOptimisticUploadId()
    const optimisticIdB = service.createOptimisticUploadId()

    expect(optimisticIdA).toBeLessThan(0)
    expect(optimisticIdB).toBeLessThan(0)
    expect(optimisticIdA).not.toBe(optimisticIdB)

    const message = service.buildOptimisticMessageFromUpload({
      id: -1,
      userId: -7,
      roomKind: 'channel',
      senderId: 3,
      msgType: 'document',
      file: new Blob(['doc'], { type: 'application/pdf' }),
      fileName: 'report.pdf',
      mimeType: 'application/pdf',
      thumbnail: '',
      width: 0,
      height: 0,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'failed',
      progress: 55,
      uploadedBytes: 11,
      totalBytes: 20,
      createdAt: '2026-05-14T00:00:00Z',
      errorMessage: 'bad request',
    })

    expect(message.is_sending).toBe(false)
    expect(message.is_error).toBe(true)
    expect(JSON.parse(message.content)).toMatchObject({
      placeholder: true,
      file_name: 'report.pdf',
      mime_type: 'application/pdf',
      size: 3,
    })
  })

  it('covers upload routing, preview metadata, retry, and restore helper branches', async () => {
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const upload = {
      id: -900,
      userId: -77,
      roomKind: 'group' as const,
      senderId: 15,
      msgType: 'video' as const,
      file: new Blob(['video'], { type: 'video/mp4' }),
      fileName: 'clip.mp4',
      mimeType: 'video/mp4',
      thumbnail: '',
      width: 0,
      height: 0,
      caption: 'caption',
      albumId: 'album-1',
      albumIndex: 2,
      albumSize: 3,
      phase: 'uploading' as const,
      progress: 0,
      uploadedBytes: 2,
      totalBytes: 5,
      nextOffset: 3,
      createdAt: '2026-05-14T00:00:00Z',
    }

    expect(hooks.normalizeUploadRoomKind('direct', -1)).toBe('direct')
    expect(hooks.normalizeUploadRoomKind('unknown', -1)).toBe('channel')
    expect(hooks.normalizeUploadRoomKind(undefined, 7)).toBe('direct')
    expect(hooks.isSessionBackedUploadRoomKind('group')).toBe(true)
    expect(hooks.isSessionBackedUploadRoomKind('channel')).toBe(false)
    expect(hooks.shouldUseSessionBackedUpload(upload)).toBe(true)
    expect(hooks.isSingleSessionBackedUpload(upload)).toBe(false)
    expect(hooks.resolveUploadTargetId(upload)).toBe(77)
    expect(hooks.buildSingleUploadBatchIdempotencyKey(upload)).toContain('single:group:15:77:-900:video')
    expect(hooks.buildAlbumUploadBatchIdempotencyKey(upload)).toContain('album:group:15:77:album-1')

    hooks.applyPreviewMetadataToUpload(upload, {
      thumbnail: 'server-thumb',
      width: '640',
      height: 480,
      duration_ms: '1200',
    })
    expect(upload).toMatchObject({
      thumbnail: 'server-thumb',
      serverThumbnail: 'server-thumb',
      width: 640,
      height: 480,
      durationMs: 1200,
    })
    hooks.applyPreviewMetadataToUpload(upload, null)

    expect(hooks.getUploadPreviewMetadata(upload)).toEqual({
      thumbnail: 'server-thumb',
      width: 640,
      height: 480,
      duration_ms: 1200,
      album_index: 2,
      caption: 'caption',
    })
    expect(hooks.getUploadResumeProgress(upload)).toBe(60)
    expect(hooks.getUploadResumeProgress({ ...upload, file: new Blob([]), totalBytes: 0, nextOffset: 0 })).toBe(0)

    expect(hooks.isTransientUploadError(new Error('Failed to fetch'))).toBe(true)
    expect(hooks.isTransientUploadError('خطای ارسال (503)')).toBe(true)
    expect(hooks.isTransientUploadError('validation failed')).toBe(false)
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    expect(hooks.computeRetryDelayMs(0)).toBe(1000)
    expect(hooks.computeSendRetryDelayMs(5)).toBe(15000)

    expect(hooks.canUseUploadServiceWorker()).toBe(false)
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: { controller: {}, addEventListener: vi.fn() },
    })
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Chrome/123 Safari/537.36',
    })
    expect(hooks.canUseUploadServiceWorker()).toBe(true)
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Firefox/120',
    })
    expect(hooks.canUseUploadServiceWorker()).toBe(false)

    class ImmediateFileReader {
      result = 'data:text/plain;base64,aGVsbG8='
      error = null
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      readAsDataURL() {
        this.onload?.()
      }
    }
    vi.stubGlobal('FileReader', ImmediateFileReader)
    const dataUrl = await hooks.blobToDataUrl(new Blob(['hello'], { type: 'text/plain' }))
    expect(dataUrl).toMatch(/^data:text\/plain;base64,/)
    const decodedBlob = hooks.dataUrlToBlob('data:text/plain;base64,aGk=')
    expect(decodedBlob).toBeInstanceOf(Blob)
    expect(decodedBlob.type).toBe('text/plain')
    expect(decodedBlob.size).toBe(2)

    const restoredDocumentBlob = hooks.restorePersistedFile({
      ...upload,
      msgType: 'document',
      fileName: 'doc.pdf',
      mimeType: 'application/pdf',
      file: new Blob(['doc'], { type: 'application/pdf' }),
    })
    expect(restoredDocumentBlob).toBeInstanceOf(File)

    const restoredDocumentDataUrl = hooks.restorePersistedFile({
      ...upload,
      msgType: 'document',
      fileName: 'doc.txt',
      mimeType: 'text/plain',
      file: undefined,
      fileDataUrl: 'data:text/plain;base64,ZG9j',
    })
    expect(restoredDocumentDataUrl).toBeInstanceOf(File)

    const restoredBytes = hooks.restorePersistedFile({
      ...upload,
      file: undefined,
      fileBytes: new TextEncoder().encode('bytes').buffer,
      mimeType: 'image/png',
      msgType: 'image',
    })
    expect(restoredBytes).toBeInstanceOf(Blob)
    expect(hooks.restorePersistedFile({ ...upload, file: undefined, fileBytes: undefined })).toBeNull()
    const uploadWithoutRoomKind = { ...upload } as Record<string, unknown>
    delete uploadWithoutRoomKind.roomKind
    expect(hooks.normalizePersistedUpload(uploadWithoutRoomKind as any, new Blob(['x']))).toMatchObject({
      roomKind: 'channel',
      file: expect.any(Blob),
    })

    const statefulUpload = {
      ...upload,
      batchId: 'batch',
      sessionId: 'session',
      resumeToken: 'resume',
      nextOffset: 5,
      sessionExpiresAt: 'soon',
      fileId: 'file',
    }
    hooks.clearResumableUploadState(statefulUpload)
    expect(statefulUpload).toMatchObject({ nextOffset: 0 })
    expect(statefulUpload.batchId).toBeUndefined()
    expect(statefulUpload.sessionId).toBeUndefined()
    expect(statefulUpload.resumeToken).toBeUndefined()
    expect(statefulUpload.fileId).toBeUndefined()
  })

  it('guards pre-init helper access and missing navigator globals', async () => {
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks

    vi.stubGlobal('navigator', undefined as any)
    expect(hooks.canUseUploadServiceWorker()).toBe(false)
    await expect(hooks.uploadApiFetch('/needs-init')).rejects.toThrow('[uploadService] not initialized')
  })

  it('persists, restores, lists, and deletes upload records through IndexedDB helpers', async () => {
    const { records, deletedIds } = installIndexedDb([])
    class ImmediateFileReader {
      result = 'data:application/pdf;base64,ZG9j'
      error = null
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      readAsDataURL() {
        this.onload?.()
      }
    }
    vi.stubGlobal('FileReader', ImmediateFileReader)

    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const documentUpload = {
      id: -940,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'document' as const,
      file: new File(['doc'], 'report.pdf', { type: 'application/pdf' }),
      fileName: 'report.pdf',
      mimeType: 'application/pdf',
      thumbnail: '',
      width: 0,
      height: 0,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'queued' as const,
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 3,
      createdAt: '2026-05-14T00:00:00Z',
      localBlobUrl: 'blob:ui-only',
    }

    await hooks.idbPut(documentUpload)
    expect(records.get(-940)?.localBlobUrl).toBeUndefined()
    expect(records.get(-940)?.fileDataUrl).toMatch(/^data:application\/pdf/)

    const restored = await hooks.idbGet(-940)
    expect(restored).toMatchObject({ id: -940, roomKind: 'direct', fileName: 'report.pdf' })
    expect(restored?.file).toBeInstanceOf(File)

    records.set(-941, {
      ...documentUpload,
      id: -941,
      file: undefined,
      fileDataUrl: undefined,
      fileBytes: undefined,
    })
    const all = await hooks.idbGetAll()
    expect(all.map((upload) => upload.id)).toEqual([-940])

    await hooks.idbDelete(-940)
    expect(deletedIds).toContain(-940)
    expect(await hooks.idbGet(-940)).toBeNull()

    const throwingDb = { transaction: () => { throw new Error('tx failed') } } as unknown as IDBDatabase
    await expect(hooks.putRecord(throwingDb, documentUpload as any)).resolves.toBe(false)
  })

  it('restores queued album uploads on init and drops un-restorable persisted records', async () => {
    installIndexedDb([
      {
        id: -942,
        userId: -77,
        roomKind: 'channel',
        senderId: 15,
        msgType: 'image',
        file: new Blob(['queued'], { type: 'image/png' }),
        fileName: 'queued-album.png',
        mimeType: 'image/png',
        thumbnail: '',
        width: 120,
        height: 80,
        albumId: 'persisted-album',
        albumIndex: 0,
        albumSize: 1,
        phase: 'queued',
        progress: 0,
        uploadedBytes: 0,
        totalBytes: 6,
        createdAt: '2026-05-14T00:00:00Z',
      },
      {
        id: -943,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'document',
        file: undefined,
        fileName: 'missing.bin',
        mimeType: 'application/octet-stream',
        thumbnail: '',
        width: 0,
        height: 0,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'queued',
        progress: 0,
        uploadedBytes: 0,
        totalBytes: 0,
        createdAt: '2026-05-14T00:00:00Z',
      },
    ])

    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks

    MockXHR.enqueueScenario(() => {})

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    expect(hooks.state.albumBatches.get('persisted-album')?.optimisticIds.has(-942)).toBe(true)
    expect(hooks.state.pendingUploads.get(-942)?.localBlobUrl).toBe('blob:restored-upload')
    await expect(hooks.idbGet(-943)).resolves.toBeNull()

    service.cancelUpload(-942)
    await vi.runAllTimersAsync()
  })

  it('parses upload API responses and maps server errors', async () => {
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) } as Response)
    await expect(hooks.uploadApiFetch('/ok')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenLastCalledWith(
      'https://coin.test/api/ok',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer jwt' }) }),
    )

    fetchMock.mockResolvedValueOnce({ ok: true, status: 204, json: async () => ({}) } as Response)
    await expect(hooks.uploadApiFetch('/empty')).resolves.toBeUndefined()

    fetchMock.mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) } as Response)
    await expect(hooks.uploadApiFetch('/expired')).rejects.toThrow('نشست شما منقضی شده است')

    fetchMock.mockResolvedValueOnce({ ok: false, status: 413, json: async () => ({}) } as Response)
    await expect(hooks.uploadApiFetch('/large')).rejects.toThrow('حجم فایل')

    fetchMock.mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ detail: 'conflict' }) } as Response)
    await expect(hooks.uploadApiFetch('/conflict')).rejects.toThrow('conflict')

    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => { throw new Error('bad json') } } as unknown as Response)
    await expect(hooks.uploadApiFetch('/bad-json')).rejects.toThrow('خطای سرور (500)')
  })

  it('covers service-worker and resumable helper branches through test hooks', async () => {
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks

    const baseUpload = {
      id: -930,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'video' as const,
      file: new Blob(['video-payload'], { type: 'video/mp4' }),
      fileName: 'clip.mp4',
      mimeType: 'video/mp4',
      thumbnail: 'thumb-local',
      serverThumbnail: 'thumb-server',
      width: 640,
      height: 360,
      durationMs: 2200,
      caption: 'caption',
      albumId: 'album-hook',
      albumIndex: 3,
      albumSize: 4,
      phase: 'uploaded' as const,
      progress: 100,
      uploadedBytes: 13,
      totalBytes: 13,
      fileId: 'file-hook',
      batchId: 'batch-hook',
      sessionId: 'session-hook',
      resumeToken: 'resume-hook',
      nextOffset: 13,
      sessionExpiresAt: '2026-05-14T01:00:00Z',
      createdAt: '2026-05-14T00:00:00Z',
      localBlobUrl: 'blob:hook',
    }

    expect(JSON.parse(hooks.buildContent({ ...baseUpload, fileId: undefined }, 'preview'))).toMatchObject({
      placeholder: true,
      thumbnail: 'thumb-local',
      width: 640,
      height: 360,
      album_id: 'album-hook',
      album_index: 3,
      durationMs: 2200,
      caption: 'caption',
    })
    expect(JSON.parse(hooks.buildContent(baseUpload, 'final'))).toMatchObject({
      file_id: 'file-hook',
      thumbnail: 'thumb-server',
      caption: 'caption',
    })
    expect(JSON.parse(hooks.buildContent({ ...baseUpload, msgType: 'document', thumbnail: 'ignored' }, 'final'))).toMatchObject({
      file_id: 'file-hook',
      file_name: 'clip.mp4',
      mime_type: 'video/mp4',
      size: baseUpload.file.size,
    })
    expect(JSON.parse(hooks.buildContent({
      ...baseUpload,
      msgType: 'voice',
      thumbnail: 'ignored-voice-thumb',
      width: 500,
      height: 250,
      albumId: 'album-hook',
      caption: 'ignored caption',
      durationMs: 1900,
    }, 'preview'))).toEqual({
      placeholder: true,
      durationMs: 1900,
    })
    expect(hooks.getCommittedAlbumMessageIndex({ content: JSON.stringify({ album_index: 2 }), id: 10 } as any)).toBe(2)
    expect(hooks.getCommittedAlbumMessageIndex({ content: '{bad', id: 10 } as any)).toBe(Number.MAX_SAFE_INTEGER)

    await expect(hooks.postUploadsToServiceWorker([-930])).resolves.toBe(false)
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => '' })
    await expect(hooks.postUploadsToServiceWorker([-930])).resolves.toBe(false)

    const postMessage = vi.fn(() => {
      throw new Error('post failed')
    })
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36',
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: {
        controller: { postMessage },
        addEventListener: vi.fn(),
      },
    })
    await expect(hooks.postUploadsToServiceWorker([-930])).resolves.toBe(false)

    hooks.state.serviceWorkerOwnedUploads.clear()
    await hooks.pauseUploadForServiceWorker({ ...baseUpload, id: -931 })
    expect(hooks.state.serviceWorkerOwnedUploads.has(-931)).toBe(true)

    const abortingXhr = new MockXHR()
    const abortingXhrAbortSpy = vi.spyOn(abortingXhr, 'abort')
    hooks.state.xhrControllers.set(-932, abortingXhr as unknown as XMLHttpRequest)
    const pausePromise = hooks.pauseUploadForServiceWorker({ ...baseUpload, id: -932 })
    expect(abortingXhrAbortSpy).toHaveBeenCalled()
    hooks.state.serviceWorkerHandoffResolvers.get(-932)?.()
    await pausePromise
    expect(hooks.state.serviceWorkerOwnedUploads.has(-932)).toBe(true)

    await hooks.cancelServerUploadState({ ...baseUpload, batchId: undefined, sessionId: undefined })
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ status: 'cancelled' }) } as Response)
    await hooks.cancelServerUploadState({ ...baseUpload, batchId: undefined, sessionId: 'session-cancel' })
    expect(fetchMock).toHaveBeenLastCalledWith(
      'https://coin.test/api/chat/upload-sessions/session-cancel/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('covers legacy upload transport error branches through direct hook runs', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const makeLegacyUpload = (id: number): PendingUpload => ({
      id,
      userId: -77,
      roomKind: 'channel' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['payload'], { type: 'image/png' }),
      fileName: `legacy-${Math.abs(id)}.png`,
      mimeType: 'image/png',
      thumbnail: '',
      width: 120,
      height: 80,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'queued' as const,
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 7,
      createdAt: '2026-05-14T00:00:00Z',
    })

    const expiredUpload = makeLegacyUpload(-3441)
    hooks.state.pendingUploads.set(expiredUpload.id, expiredUpload)
    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 401
      xhr.responseText = '{}'
      xhr.onload?.()
    })
    await hooks.runLegacyUpload(expiredUpload)
    expect(expiredUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -3441,
      errorMessage: 'نشست شما منقضی شده است. لطفاً صفحه را رفرش کنید.',
    }))

    const tooLargeUpload = makeLegacyUpload(-3442)
    hooks.state.pendingUploads.set(tooLargeUpload.id, tooLargeUpload)
    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 413
      xhr.responseText = '{}'
      xhr.onload?.()
    })
    await hooks.runLegacyUpload(tooLargeUpload)
    expect(tooLargeUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -3442,
      errorMessage: 'حجم فایل از حد مجاز ۵۰ مگابایت بیشتر است.',
    }))

    const genericServerUpload = makeLegacyUpload(-3443)
    hooks.state.pendingUploads.set(genericServerUpload.id, genericServerUpload)
    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 422
      xhr.responseText = 'not-json'
      xhr.onload?.()
    })
    await hooks.runLegacyUpload(genericServerUpload)
    expect(genericServerUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -3443,
      errorMessage: 'خطای سرور (422)',
    }))

    const retryableUpload = makeLegacyUpload(-3444)
    hooks.state.pendingUploads.set(retryableUpload.id, retryableUpload)
    MockXHR.enqueueScenario((xhr) => xhr.onerror?.())
    await hooks.runLegacyUpload(retryableUpload)
    expect(retryableUpload.phase).toBe('queued')
    expect(retryableUpload.retryCount).toBe(1)
    hooks.state.pendingUploads.delete(retryableUpload.id)
    await vi.runAllTimersAsync()

    const abortedUpload = makeLegacyUpload(-3445)
    hooks.state.pendingUploads.set(abortedUpload.id, abortedUpload)
    MockXHR.enqueueScenario((xhr) => xhr.onabort?.())
    await hooks.runLegacyUpload(abortedUpload)
    expect(abortedUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -3445,
      errorMessage: 'UploadCancelled',
    }))
  })

  it('covers resumable chunk XHR error branches and service-worker handoff/reclaim paths', async () => {
    const { records } = installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const baseUpload = {
      id: -950,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['payload'], { type: 'image/png' }),
      fileName: 'photo.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 0,
      height: 0,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploading' as const,
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 7,
      batchId: 'batch-950',
      sessionId: 'session-950',
      resumeToken: 'resume-950',
      nextOffset: 0,
      createdAt: '2026-05-14T00:00:00Z',
    }

    await expect(hooks.appendResumableUploadChunk({ ...baseUpload, sessionId: undefined }, new Blob(['x']), 0, false))
      .rejects.toThrow('Missing resumable upload session state')

    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: 7 } as any)
      xhr.status = 200
      xhr.responseText = '{bad'
      xhr.onload?.()
    })
    await expect(hooks.appendResumableUploadChunk({ ...baseUpload, id: -951 }, new Blob(['abc']), 0, false))
      .rejects.toThrow('پاسخ نامعتبر سرور')

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 401
      xhr.responseText = JSON.stringify({ detail: 'expired' })
      xhr.onload?.()
    })
    await expect(hooks.appendResumableUploadChunk({ ...baseUpload, id: -952 }, new Blob(['abc']), 0, false))
      .rejects.toThrow('نشست شما منقضی شده است')

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 500
      xhr.responseText = JSON.stringify({ detail: 'server detail' })
      xhr.onload?.()
    })
    await expect(hooks.appendResumableUploadChunk({ ...baseUpload, id: -953 }, new Blob(['abc']), 0, false))
      .rejects.toThrow('server detail')

    MockXHR.enqueueScenario((xhr) => xhr.onerror?.())
    await expect(hooks.appendResumableUploadChunk({ ...baseUpload, id: -954 }, new Blob(['abc']), 0, false))
      .rejects.toThrow('Network Error')

    MockXHR.enqueueScenario((xhr) => xhr.onabort?.())
    await expect(hooks.appendResumableUploadChunk({ ...baseUpload, id: -955 }, new Blob(['abc']), 0, false))
      .rejects.toThrow('UploadCancelled')

    MockXHR.enqueueScenario((xhr) => xhr.ontimeout?.())
    await expect(hooks.appendResumableUploadChunk({ ...baseUpload, id: -956 }, new Blob(['abc']), 0, false))
      .rejects.toThrow('Network Error (timeout)')

    const serviceWorkerPostMessage = vi.fn()
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36',
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: {
        controller: { postMessage: serviceWorkerPostMessage },
        addEventListener: vi.fn(),
      },
    })

    hooks.state.pendingUploads.set(-957, { ...baseUpload, id: -957, phase: 'uploaded', albumId: null })
    hooks.state.pendingUploads.set(-958, { ...baseUpload, id: -958, phase: 'sent', albumId: null })
    await hooks.handoffEligibleUploadsToServiceWorker()
    expect(serviceWorkerPostMessage).toHaveBeenCalledWith(expect.objectContaining({ type: 'chat-upload:handoff', uploadIds: [-957] }))
    expect(hooks.state.serviceWorkerOwnedUploads.has(-957)).toBe(true)

    serviceWorkerPostMessage.mockImplementationOnce(() => {
      throw new Error('reclaim failed')
    })
    hooks.state.serviceWorkerOwnedUploads.add(-959)
    hooks.state.serviceWorkerOwnedUploads.add(-960)
    records.set(-960, {
      ...baseUpload,
      id: -960,
      phase: 'queued',
      file: new Blob(['restored'], { type: 'image/png' }),
      localBlobUrl: undefined,
    })
    await hooks.reclaimUploadsFromServiceWorker()
    expect(hooks.state.pendingUploads.has(-959)).toBe(false)
    expect(hooks.state.pendingUploads.get(-960)?.localBlobUrl).toBe('blob:restored-upload')
  })

  it('covers direct resumable commit retry, abort, handoff, and validation branches', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const makeUpload = (id: number, batchId = `batch-${Math.abs(id)}`) => ({
      id,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['payload'], { type: 'image/png' }),
      fileName: `photo-${Math.abs(id)}.png`,
      mimeType: 'image/png',
      thumbnail: '',
      width: 120,
      height: 80,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploaded' as const,
      progress: 100,
      uploadedBytes: 7,
      totalBytes: 7,
      batchId,
      fileId: `file-${Math.abs(id)}`,
      createdAt: '2026-05-14T00:00:00Z',
      activitySignalActive: true,
    })

    await expect(hooks.commitSingleUploadBatch({ ...makeUpload(-970), batchId: undefined }))
      .rejects.toThrow('Missing upload batch id')

    let retryAttempts = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/chat/upload-batches/batch-971/commit')) {
        retryAttempts += 1
        if (retryAttempts === 1) {
          return { ok: false, status: 503, json: async () => ({}) } as Response
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [{
              id: 9971,
              sender_id: 15,
              receiver_id: 77,
              content: JSON.stringify({ file_id: 'file-971' }),
              message_type: 'image',
              is_read: false,
              created_at: '2026-05-14T00:30:00Z',
            }],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })
    const retryUpload = makeUpload(-971, 'batch-971')
    hooks.state.pendingUploads.set(-971, retryUpload)
    await hooks.commitSingleUploadBatch(retryUpload)
    expect(retryUpload.phase).toBe('uploaded')
    await vi.runAllTimersAsync()
    expect(retryAttempts).toBe(2)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -971)).toBe(true)

    fetchMock.mockImplementationOnce(async () => {
      hooks.state.abortFlags.add(-972)
      throw new Error('network down')
    })
    const abortUpload = makeUpload(-972, 'batch-972')
    hooks.state.pendingUploads.set(-972, abortUpload)
    await hooks.commitSingleUploadBatch(abortUpload)
    expect(events.some((event) => event.type === 'cancelled' && event.optimisticId === -972)).toBe(true)
    expect(hooks.state.pendingUploads.has(-972)).toBe(false)

    const handoffResolved = vi.fn()
    fetchMock.mockImplementationOnce(async () => {
      throw new Error('handoff abort')
    })
    const handoffUpload = makeUpload(-973, 'batch-973')
    hooks.state.serviceWorkerHandoffAbortIds.add(-973)
    hooks.state.serviceWorkerHandoffResolvers.set(-973, handoffResolved)
    await hooks.commitSingleUploadBatch(handoffUpload)
    expect(handoffUpload.phase).toBe('uploaded')
    expect(handoffResolved).toHaveBeenCalledOnce()
  })

  it('covers album batch commit empty, retry, and final failure branches', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const makeAlbumUpload = (id: number, index: number) => ({
      id,
      userId: 77,
      roomKind: 'group' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob([`payload-${index}`], { type: 'image/png' }),
      fileName: `album-${index}.png`,
      mimeType: 'image/png',
      thumbnail: '',
      width: 120,
      height: 80,
      albumId: 'album-commit',
      albumIndex: index,
      albumSize: 2,
      phase: 'uploaded' as const,
      progress: 100,
      uploadedBytes: 9,
      totalBytes: 9,
      batchId: 'album-batch',
      fileId: `album-file-${index}`,
      createdAt: '2026-05-14T00:00:00Z',
      activitySignalActive: true,
    })

    const noBatch = hooks.ensureAlbumBatch('album-no-batch', 77, 1, 'group')
    await expect(hooks.commitAlbumBatch(noBatch, [makeAlbumUpload(-980, 0)]))
      .rejects.toThrow('Missing album upload batch id')

    const emptyBatch = hooks.ensureAlbumBatch('album-empty', 77, 1, 'group', 'empty-batch')
    const abortedUpload = { ...makeAlbumUpload(-981, 0), albumId: 'album-empty', batchId: 'empty-batch' }
    hooks.state.abortFlags.add(-981)
    await hooks.commitAlbumBatch(emptyBatch, [abortedUpload])
    expect(fetchMock).not.toHaveBeenCalledWith(
      'https://coin.test/api/chat/upload-batches/empty-batch/commit',
      expect.anything(),
    )

    let commitAttempts = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/chat/upload-batches/album-batch/commit')) {
        commitAttempts += 1
        if (commitAttempts === 1) {
          return { ok: false, status: 504, json: async () => ({}) } as Response
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              { id: 9982, sender_id: 15, receiver_id: 77, content: JSON.stringify({ album_index: 1 }), message_type: 'image', is_read: false, created_at: '2026-05-14T00:31:00Z' },
              { id: 9981, sender_id: 15, receiver_id: 77, content: JSON.stringify({ album_index: 0 }), message_type: 'image', is_read: false, created_at: '2026-05-14T00:31:00Z' },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })
    const retryBatch = hooks.ensureAlbumBatch('album-commit', 77, 2, 'group', 'album-batch')
    const firstUpload = makeAlbumUpload(-982, 0)
    const secondUpload = makeAlbumUpload(-983, 1)
    hooks.state.pendingUploads.set(-982, firstUpload)
    hooks.state.pendingUploads.set(-983, secondUpload)
    retryBatch.optimisticIds.add(-982)
    retryBatch.optimisticIds.add(-983)
    await hooks.commitAlbumBatch(retryBatch, [firstUpload, secondUpload])
    expect(commitAttempts).toBe(1)
    expect(firstUpload.phase).toBe('uploaded')
    await vi.runAllTimersAsync()
    expect(commitAttempts).toBe(2)
    expect(events.filter((event) => event.type === 'sent').map((event) => event.optimisticId)).toEqual([-982, -983])

    fetchMock.mockImplementationOnce(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ messages: [] }),
    }) as Response)
    const failingBatch = hooks.ensureAlbumBatch('album-fail', 77, 1, 'group', 'album-fail-batch')
    const failingUpload = { ...makeAlbumUpload(-984, 0), albumId: 'album-fail', batchId: 'album-fail-batch' }
    hooks.state.pendingUploads.set(-984, failingUpload)
    failingBatch.optimisticIds.add(-984)
    await hooks.commitAlbumBatch(failingBatch, [failingUpload])
    expect(failingUpload.phase).toBe('failed')
    expect(events.some((event) => event.type === 'error' && event.optimisticId === -984)).toBe(true)
  })

  it('covers IndexedDB open, upgrade, and failed transaction branches', async () => {
    let service = await importFreshModule()
    let hooks = service.__chatUploadBackgroundTestHooks
    const createObjectStore = vi.fn()
    vi.stubGlobal('indexedDB', {
      open: () => {
        const request: any = {
          result: {
            objectStoreNames: { contains: () => false },
            createObjectStore,
            close: vi.fn(),
            onversionchange: null,
          },
          error: null,
          onupgradeneeded: null,
          onsuccess: null,
          onerror: null,
        }
        Promise.resolve().then(() => {
          request.onupgradeneeded?.({ target: request })
          request.onsuccess?.()
        })
        return request
      },
    })
    const upgradedDb = await hooks.openDB()
    expect(createObjectStore).toHaveBeenCalledWith('pending', { keyPath: 'id' })
      if (!upgradedDb.onversionchange) {
        throw new Error('Expected IndexedDB versionchange handler')
      }
      upgradedDb.onversionchange(new Event('versionchange') as IDBVersionChangeEvent)

    service = await importFreshModule()
    hooks = service.__chatUploadBackgroundTestHooks
    vi.stubGlobal('indexedDB', {
      open: () => {
        const request: any = {
          result: null,
          error: new Error('open failed'),
          onupgradeneeded: null,
          onsuccess: null,
          onerror: null,
        }
        Promise.resolve().then(() => request.onerror?.())
        return request
      },
    })
    await expect(hooks.openDB()).rejects.toThrow('open failed')

    service = await importFreshModule()
    hooks = service.__chatUploadBackgroundTestHooks
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('open threw')
      },
    })
    await expect(hooks.openDB()).rejects.toThrow('open threw')

    service = await importFreshModule()
    hooks = service.__chatUploadBackgroundTestHooks
    const throwingDb = {
      objectStoreNames: { contains: () => true },
      transaction: () => {
        throw new Error('transaction failed')
      },
      close: vi.fn(),
      onversionchange: null,
    }
    vi.stubGlobal('indexedDB', {
      open: () => {
        const request: any = {
          result: throwingDb,
          error: null,
          onupgradeneeded: null,
          onsuccess: null,
          onerror: null,
        }
        Promise.resolve().then(() => request.onsuccess?.())
        return request
      },
    })
    expect(await hooks.putRecord(throwingDb as unknown as IDBDatabase, { id: -990 } as any)).toBe(false)
    await expect(hooks.idbDelete(-990)).resolves.toBeUndefined()
    await expect(hooks.idbGet(-990)).resolves.toBeNull()
    await expect(hooks.idbGetAll()).resolves.toEqual([])
  })

  it('resumes foreground uploads across queued, uploading, uploaded, and sending phases', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const makeUpload = (id: number, phase: 'queued' | 'uploading' | 'uploaded' | 'sending', roomKind: 'channel' | 'direct' = 'channel') => ({
      id,
      userId: roomKind === 'direct' ? 77 : -77,
      roomKind,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['payload'], { type: 'image/png' }),
      fileName: `foreground-${Math.abs(id)}.png`,
      mimeType: 'image/png',
      thumbnail: '',
      width: 120,
      height: 80,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase,
      progress: phase === 'uploaded' || phase === 'sending' ? 100 : 0,
      uploadedBytes: phase === 'uploaded' || phase === 'sending' ? 7 : 0,
      totalBytes: 7,
      batchId: roomKind === 'direct' ? `foreground-batch-${Math.abs(id)}` : undefined,
      fileId: phase === 'uploaded' || phase === 'sending' ? `foreground-file-${Math.abs(id)}` : undefined,
      createdAt: '2026-05-14T00:00:00Z',
    })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.includes('/api/chat/upload-batches/foreground-batch-993/commit') || url.includes('/api/chat/upload-batches/foreground-batch-994/commit')) {
        const optimisticId = url.includes('993') ? -993 : -994
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [{
              id: Math.abs(optimisticId),
              sender_id: 15,
              receiver_id: 77,
              content: JSON.stringify({ file_id: `foreground-file-${Math.abs(optimisticId)}` }),
              message_type: 'image',
              is_read: false,
              created_at: '2026-05-14T00:40:00Z',
            }],
          }),
        } as Response
      }
      if (url.includes('/send')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 991,
            sender_id: 15,
            receiver_id: -77,
            content: JSON.parse(String(init?.body ?? '{}')).content,
            message_type: 'image',
            is_read: false,
            created_at: '2026-05-14T00:41:00Z',
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({ file_id: 'foreground-queued-file', width: 100, height: 80 })
      xhr.onload?.()
    })
    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({ file_id: 'foreground-uploading-file', width: 100, height: 80 })
      xhr.onload?.()
    })

    const aborted = makeUpload(-991, 'uploaded')
    const owned = makeUpload(-992, 'uploaded')
    const queued = makeUpload(-995, 'queued')
    const uploading = { ...makeUpload(-996, 'uploading'), nextOffset: 2, uploadedBytes: 2, progress: 30 }
    const uploadedDirect = makeUpload(-993, 'uploaded', 'direct')
    const sendingDirect = makeUpload(-994, 'sending', 'direct')
    for (const upload of [aborted, owned, queued, uploading, uploadedDirect, sendingDirect]) {
      hooks.state.pendingUploads.set(upload.id, upload)
    }
    hooks.state.abortFlags.add(-991)
    hooks.state.serviceWorkerOwnedUploads.add(-992)

    await hooks.resumePendingUploadsAfterForegroundWake()
    await vi.runAllTimersAsync()

    expect(events.some((event) => event.type === 'uploaded' && event.optimisticId === -995)).toBe(true)
    expect(events.some((event) => event.type === 'uploaded' && event.optimisticId === -996)).toBe(true)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -993)).toBe(true)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -994)).toBe(true)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -996)).toBe(true)
    expect(hooks.state.pendingUploads.has(-996)).toBe(false)
    expect(hooks.state.pendingUploads.has(-991)).toBe(true)
    expect(hooks.state.serviceWorkerOwnedUploads.has(-992)).toBe(true)
  })

  it('rejects submitUpload before the background service is initialized', async () => {
    const service = await importFreshModule()

    await expect(service.submitUpload(makeBaseSubmitParams())).rejects.toThrow('[uploadService] not initialized')
  })

  it('submits a legacy channel upload, emits progress/uploaded/sent events, and clears the pending entry', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 } as any)
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        file_id: 'chat-file-1',
        file_name: 'server-image.png',
        mime_type: 'image/png',
        thumbnail: 'server-thumb',
        width: 640,
        height: 480,
      })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams())
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'progress', 'uploaded', 'sent'])
    expect(events[2]).toMatchObject({ type: 'uploaded', fileId: 'chat-file-1' })
    expect(events[3]).toMatchObject({ type: 'sent', optimisticId: -101 })
    expect(service.getPendingForUser(-77)).toEqual([])
    expect(MockXHR.instances).toHaveLength(1)
    expect(MockXHR.instances[0]?.url).toBe('https://coin.test/api/chat/upload-media')
    expect(fetchMock).toHaveBeenCalledWith(
      'https://coin.test/api/chat/rooms/77/send',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('submits a resumable direct upload through batch/session endpoints and commits it without legacy upload-media', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const directFile = new Blob(['direct-image-bytes'], { type: 'image/png' })
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return {
          ok: true,
          status: 204,
          json: async () => ({}),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'batch-direct-1' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'session-direct-1',
            resume_token: 'resume-direct-1',
            next_offset: 0,
            expires_at: '2026-05-14T01:00:00Z',
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/session-direct-1/finalize') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ final_chat_file_id: 'direct-file-1' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/session-direct-1')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            total_bytes: directFile.size,
            next_offset: directFile.size,
            received_bytes: directFile.size,
            final_chat_file_id: 'direct-file-1',
            preview_metadata: {
              thumbnail: 'server-thumb',
              width: 640,
              height: 480,
            },
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/batch-direct-1/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9901,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'direct-file-1' }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T00:10:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: directFile.size, total: directFile.size } as any)
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        next_offset: directFile.size,
        received_bytes: directFile.size,
      })
      xhr.onload?.()
    })

    await service.submitUpload(
      makeBaseSubmitParams({
        optimisticId: -401,
        userId: 77,
        roomKind: 'direct',
        file: directFile,
        fileName: 'direct-image.png',
        localBlobUrl: 'blob:direct-local',
      }),
    )
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'progress', 'uploaded', 'sent'])
    expect(events[2]).toMatchObject({
      type: 'uploaded',
      optimisticId: -401,
      fileId: 'direct-file-1',
    })
    expect(events[3]).toMatchObject({
      type: 'sent',
      optimisticId: -401,
      localBlobUrl: 'blob:direct-local',
    })
    expect(service.getPendingForUser(77)).toEqual([])
    expect(MockXHR.instances).toHaveLength(1)
    expect(MockXHR.instances[0]?.url).toBe('https://coin.test/api/chat/upload-sessions/session-direct-1/chunk')
    expect(fetchMock).not.toHaveBeenCalledWith(
      'https://coin.test/api/chat/upload-media',
      expect.anything(),
    )
  })

  it('cancels a queued upload before it starts and removes it from pending uploads', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    MockXHR.enqueueScenario(() => {})
    MockXHR.enqueueScenario(() => {})
    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({ file_id: 'never-used' })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -201, userId: -88 }))
    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -202, userId: -88 }))
    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -203, userId: -88 }))

    expect(service.getPendingForUser(-88).map((upload) => upload.id)).toEqual([-201, -202, -203])

    service.cancelUpload(-203)
    expect(events.some((event) => event.type === 'cancelled' && event.optimisticId === -203)).toBe(true)
    expect(service.getPendingForUser(-88).map((upload) => upload.id)).toEqual([-201, -202])

    service.cancelUpload(-201)
    service.cancelUpload(-202)
  })

  it('covers eager cancel cleanup with throwing controllers and retry routing branches', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/chat/upload-batches/queued-batch/cancel') && init?.method === 'POST') {
        return { ok: true, status: 200, json: async () => ({ status: 'cancelled' }) } as Response
      }
      if (url.includes('/send')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 9901,
            sender_id: 15,
            receiver_id: 77,
            content: JSON.parse(String(init?.body ?? '{}')).content,
            message_type: 'image',
            is_read: false,
            created_at: '2026-05-14T00:00:00Z',
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const queuedDirectUpload = {
      id: -3491,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['queued'], { type: 'image/png' }),
      fileName: 'queued-direct.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 120,
      height: 80,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'queued' as const,
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 6,
      batchId: 'queued-batch',
      sessionId: 'queued-session',
      createdAt: '2026-05-14T00:00:00Z',
    }

    hooks.state.pendingUploads.set(queuedDirectUpload.id, queuedDirectUpload)
    hooks.state.xhrControllers.set(queuedDirectUpload.id, {
      abort() {
        throw new Error('broken xhr abort')
      },
    } as any)
    hooks.state.sendControllers.set(queuedDirectUpload.id, {
      abort() {
        throw new Error('broken send abort')
      },
    } as any)

    service.cancelUpload(queuedDirectUpload.id)
    await vi.runAllTimersAsync()

    expect(hooks.state.pendingUploads.has(queuedDirectUpload.id)).toBe(false)
    expect(events).toContainEqual(expect.objectContaining({ type: 'cancelled', optimisticId: -3491 }))
    expect(fetchMock).toHaveBeenCalledWith(
      'https://coin.test/api/chat/upload-batches/queued-batch/cancel',
      expect.objectContaining({ method: 'POST' }),
    )

    const retryableFailedUpload = {
      ...queuedDirectUpload,
      id: -3492,
      userId: -77,
      roomKind: 'channel' as const,
      fileName: 'retry-channel.png',
      phase: 'failed' as const,
      batchId: undefined,
      sessionId: undefined,
      errorMessage: 'temporary failure',
    }
    hooks.state.pendingUploads.set(retryableFailedUpload.id, retryableFailedUpload)
    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        file_id: 'retry-file-3492',
        file_name: 'retry-channel.png',
        mime_type: 'image/png',
        thumbnail: '',
        width: 120,
        height: 80,
      })
      xhr.onload?.()
    })

    service.retryFailedUpload(retryableFailedUpload.id)
    await vi.runAllTimersAsync()

    expect(events).toContainEqual(expect.objectContaining({ type: 'sent', optimisticId: -3492 }))

    const retryableAlbumUpload = {
      ...queuedDirectUpload,
      id: -3493,
      userId: -77,
      roomKind: 'channel' as const,
      phase: 'failed' as const,
      fileId: 'existing-file-3493',
      albumId: 'retry-album',
      albumIndex: 0,
      albumSize: 1,
      batchId: undefined,
      sessionId: undefined,
      errorMessage: 'album failure',
    }
    hooks.state.pendingUploads.set(retryableAlbumUpload.id, retryableAlbumUpload)
    hooks.state.albumBatches.set('retry-album', {
      albumId: 'retry-album',
      userId: -77,
      roomKind: 'channel',
      expectedCount: 1,
      optimisticIds: new Set([retryableAlbumUpload.id]),
      commitRetryCount: 0,
      flushing: false,
    })

    service.retryFailedUpload(retryableAlbumUpload.id)
    await Promise.resolve()

    expect(retryableAlbumUpload.errorMessage).toBeUndefined()
  })

  it('cancels an active resumable upload and notifies the server batch cancel path', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    let activeChunkXhr: MockXHR | null = null
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'cancel-batch-1' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'cancel-session-1',
            resume_token: 'cancel-resume-1',
            next_offset: 0,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T02:30:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/cancel-batch-1/cancel') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: 'cancelled' }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      activeChunkXhr = xhr
    })

    await service.submitUpload(
      makeBaseSubmitParams({
        optimisticId: -250,
        userId: 77,
        roomKind: 'direct',
        file: new Blob(['abcd'], { type: 'image/png' }),
        fileName: 'cancel-me.png',
      }),
    )
    await vi.advanceTimersByTimeAsync(0)

    expect((activeChunkXhr as MockXHR | null)?.url).toBe('https://coin.test/api/chat/upload-sessions/cancel-session-1/chunk')

    service.cancelUpload(-250)
    await vi.runAllTimersAsync()

    expect(events.some((event) => event.type === 'cancelled' && event.optimisticId === -250)).toBe(true)
    expect(events.every((event) => event.type !== 'error')).toBe(true)
    expect(service.getPendingForUser(77)).toEqual([])
    expect(fetchMock).toHaveBeenCalledWith(
      'https://coin.test/api/chat/upload-batches/cancel-batch-1/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('retries a legacy upload after an XHR timeout without marking the upload as failed', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: 10 } as any)
      xhr.ontimeout?.()
    })
    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 10, total: 10 } as any)
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        file_id: 'legacy-timeout-file',
        file_name: 'retry-image.png',
        mime_type: 'image/png',
        thumbnail: 'retry-thumb',
        width: 320,
        height: 240,
      })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -271, userId: -66 }))
    await vi.runAllTimersAsync()

    expect(MockXHR.instances).toHaveLength(2)
    expect(events.some((event) => event.type === 'uploaded' && event.optimisticId === -271)).toBe(true)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -271)).toBe(true)
    expect(events.every((event) => event.type !== 'error')).toBe(true)
    expect(service.getPendingForUser(-66)).toEqual([])
  })

  it('retries a resumable chunk upload after an XHR network error and eventually commits the direct message', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const directFile = new Blob(['retry-direct'], { type: 'image/png' })
    let syncSessionReads = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'retry-batch-1' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'retry-session-1',
            resume_token: 'retry-token-1',
            next_offset: 0,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T03:00:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/retry-session-1') && init?.method !== 'POST') {
        syncSessionReads += 1
        if (syncSessionReads === 1) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              status: 'uploading',
              total_bytes: directFile.size,
              next_offset: 0,
              received_bytes: 0,
            }),
          } as Response
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            total_bytes: directFile.size,
            next_offset: directFile.size,
            received_bytes: directFile.size,
            final_chat_file_id: 'retry-direct-file',
            preview_metadata: {
              thumbnail: 'retry-direct-thumb',
              width: 640,
              height: 480,
            },
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/retry-session-1/finalize') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ final_chat_file_id: 'retry-direct-file' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/retry-batch-1/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9911,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'retry-direct-file' }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T00:18:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      xhr.onerror?.()
    })
    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: directFile.size, total: directFile.size } as any)
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        session_id: 'retry-session-1',
        received_bytes: directFile.size,
        next_offset: directFile.size,
        status: 'uploaded',
      })
      xhr.onload?.()
    })

    await service.submitUpload(
      makeBaseSubmitParams({
        optimisticId: -272,
        userId: 77,
        roomKind: 'direct',
        file: directFile,
        fileName: 'retry-direct.png',
        localBlobUrl: 'blob:retry-direct',
      }),
    )
    await vi.runAllTimersAsync()

    expect(MockXHR.instances).toHaveLength(2)
    expect(events.some((event) => event.type === 'uploaded' && event.optimisticId === -272)).toBe(true)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -272)).toBe(true)
    expect(events.every((event) => event.type !== 'error')).toBe(true)
    expect(service.getPendingForUser(77)).toEqual([])
  })

  it('retries a failed send-only upload without re-uploading the file', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    let sendAttempts = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.includes('/send')) {
        sendAttempts += 1
        if (sendAttempts === 1) {
          return {
            ok: false,
            status: 409,
            json: async () => ({ detail: 'conflict' }),
          } as Response
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 9010,
            sender_id: 15,
            receiver_id: 77,
            content: JSON.parse(String(init?.body ?? '{}')).content,
            message_type: 'image',
            is_read: false,
            created_at: '2026-05-14T00:01:00Z',
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        file_id: 'chat-file-retry',
        file_name: 'retry-image.png',
        mime_type: 'image/png',
        thumbnail: 'server-thumb',
        width: 320,
        height: 240,
      })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -301, userId: -99 }))
    await vi.runAllTimersAsync()

    expect(events.some((event) => event.type === 'error' && event.optimisticId === -301 && event.errorMessage === 'conflict')).toBe(true)
    expect(service.getPendingForUser(-99).map((upload) => upload.id)).toEqual([-301])
    expect(MockXHR.instances).toHaveLength(1)

    service.retryFailedUpload(-301)
    await vi.runAllTimersAsync()

    expect(MockXHR.instances).toHaveLength(1)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -301)).toBe(true)
    expect(service.getPendingForUser(-99)).toEqual([])
  })

  it('automatically retries transient send failures without re-uploading the legacy file', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    let sendAttempts = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.includes('/send')) {
        sendAttempts += 1
        if (sendAttempts === 1) {
          return {
            ok: false,
            status: 503,
            json: async () => ({}),
          } as Response
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 9020,
            sender_id: 15,
            receiver_id: 88,
            content: JSON.parse(String(init?.body ?? '{}')).content,
            message_type: 'image',
            is_read: false,
            created_at: '2026-05-14T00:19:00Z',
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        file_id: 'legacy-retry-send-file',
        file_name: 'retry-send.png',
        mime_type: 'image/png',
        thumbnail: 'send-thumb',
        width: 500,
        height: 300,
      })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -302, userId: -88 }))
    await vi.runAllTimersAsync()

    expect(MockXHR.instances).toHaveLength(1)
    expect(sendAttempts).toBe(2)
    expect(events.some((event) => event.type === 'sent' && event.optimisticId === -302)).toBe(true)
    expect(events.every((event) => !(event.type === 'error' && event.optimisticId === -302))).toBe(true)
    expect(service.getPendingForUser(-88)).toEqual([])
  })

  it('recreates a persisted resumable upload when its stale session no longer exists', async () => {
    installIndexedDb([
      {
        id: -401,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'image',
        fileName: 'stale-session.png',
        mimeType: 'image/png',
        thumbnail: 'data:image/png;base64,thumb',
        width: 120,
        height: 80,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'uploading',
        progress: 50,
        uploadedBytes: 2,
        totalBytes: 4,
        createdAt: '2026-05-14T00:15:00Z',
        batchId: 'stale-batch',
        sessionId: 'stale-session',
        resumeToken: 'stale-token',
        nextOffset: 2,
        sessionExpiresAt: '2026-05-14T01:15:00Z',
        file: new Blob(['abcd'], { type: 'image/png' }),
      },
    ])

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/stale-session')) {
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: 'missing session' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'fresh-batch-1' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'fresh-session-1',
            resume_token: 'fresh-token-1',
            next_offset: 0,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T02:15:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/fresh-session-1/finalize') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ final_chat_file_id: 'fresh-file-1' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/fresh-session-1')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            total_bytes: 4,
            next_offset: 4,
            received_bytes: 4,
            final_chat_file_id: 'fresh-file-1',
            preview_metadata: {
              thumbnail: 'server-thumb',
              width: 640,
              height: 480,
            },
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/fresh-batch-1/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9910,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'fresh-file-1' }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T00:16:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 4, total: 4 } as any)
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        session_id: 'fresh-session-1',
        received_bytes: 4,
        next_offset: 4,
        status: 'uploaded',
      })
      xhr.onload?.()
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.waitForChatUploadBackgroundReady()
    await vi.runAllTimersAsync()

    const fetchUrls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(fetchUrls).toContain('https://coin.test/api/chat/upload-sessions/stale-session')
    expect(fetchUrls).toContain('https://coin.test/api/chat/upload-batches')
    expect(fetchUrls).toContain('https://coin.test/api/chat/upload-sessions')
    expect(fetchUrls).toContain('https://coin.test/api/chat/upload-batches/fresh-batch-1/commit')
    expect(events.map((event) => event.type)).toEqual(['added', 'progress', 'uploaded', 'sent'])
    expect(events[2]).toMatchObject({ type: 'uploaded', optimisticId: -401, fileId: 'fresh-file-1' })
    expect(events[3]).toMatchObject({ type: 'sent', optimisticId: -401 })
    expect(service.getPendingForUser(77)).toEqual([])
    expect(MockXHR.instances).toHaveLength(1)
    expect(MockXHR.instances[0]?.url).toBe('https://coin.test/api/chat/upload-sessions/fresh-session-1/chunk')
  })

  it('resumes persisted uploaded direct uploads on init and drops stale failed records', async () => {
    installIndexedDb([
      {
        id: -501,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'document',
        fileName: 'resume.pdf',
        mimeType: 'application/pdf',
        thumbnail: '',
        width: 0,
        height: 0,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'uploaded',
        progress: 100,
        uploadedBytes: 3,
        totalBytes: 3,
        createdAt: '2026-05-14T00:20:00Z',
        batchId: 'resume-batch',
        sessionId: 'resume-session',
        fileId: 'resume-file',
        fileDataUrl: 'data:application/pdf;base64,ZG9j',
      },
      {
        id: -502,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'image',
        fileName: 'stale.png',
        mimeType: 'image/png',
        thumbnail: '',
        width: 0,
        height: 0,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'failed',
        progress: 25,
        uploadedBytes: 1,
        totalBytes: 4,
        createdAt: '2026-05-14T00:21:00Z',
        file: new Blob(['stale'], { type: 'image/png' }),
      },
    ])

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/resume-batch/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9951,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'resume-file' }),
                message_type: 'document',
                is_read: false,
                created_at: '2026-05-14T00:22:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.waitForChatUploadBackgroundReady()
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'sent'])
    expect(events[0]).toMatchObject({ type: 'added', optimisticId: -501, userId: 77 })
    expect(events[1]).toMatchObject({ type: 'sent', optimisticId: -501 })
    expect(service.getPendingForUser(77)).toEqual([])
  })

  it('returns pending uploads in stable timeline order for remount adoption', async () => {
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks

    hooks.state.pendingUploads.set(-611, {
      id: -611,
      userId: 77,
      roomKind: 'direct',
      senderId: 15,
      msgType: 'image',
      file: new Blob(['later'], { type: 'image/png' }),
      fileName: 'later.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 40,
      height: 40,
      phase: 'queued',
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 5,
      createdAt: '2026-05-14T00:10:03Z',
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
    })
    hooks.state.pendingUploads.set(-612, {
      id: -612,
      userId: 77,
      roomKind: 'direct',
      senderId: 15,
      msgType: 'image',
      file: new Blob(['album-1'], { type: 'image/png' }),
      fileName: 'album-1.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 40,
      height: 40,
      phase: 'queued',
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 5,
      createdAt: '2026-05-14T00:10:01Z',
      albumId: 'album-a',
      albumIndex: 1,
      albumSize: 2,
    })
    hooks.state.pendingUploads.set(-613, {
      id: -613,
      userId: 77,
      roomKind: 'direct',
      senderId: 15,
      msgType: 'image',
      file: new Blob(['album-0'], { type: 'image/png' }),
      fileName: 'album-0.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 40,
      height: 40,
      phase: 'queued',
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 5,
      createdAt: '2026-05-14T00:10:01Z',
      albumId: 'album-a',
      albumIndex: 0,
      albumSize: 2,
    })
    hooks.state.pendingUploads.set(-614, {
      id: -614,
      userId: 77,
      roomKind: 'direct',
      senderId: 15,
      msgType: 'image',
      file: new Blob(['sent'], { type: 'image/png' }),
      fileName: 'sent.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 40,
      height: 40,
      phase: 'sent',
      progress: 100,
      uploadedBytes: 5,
      totalBytes: 5,
      createdAt: '2026-05-14T00:10:00Z',
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
    })
    hooks.state.pendingUploads.set(-615, {
      id: -615,
      userId: 88,
      roomKind: 'direct',
      senderId: 15,
      msgType: 'image',
      file: new Blob(['other-user'], { type: 'image/png' }),
      fileName: 'other-user.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 40,
      height: 40,
      phase: 'queued',
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 5,
      createdAt: '2026-05-14T00:09:59Z',
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
    })

    expect(service.getPendingForUser(77).map((upload) => upload.id)).toEqual([-613, -612, -611])
  })

  it('emits restored added events in stable timeline order during init', async () => {
    installIndexedDb([
      {
        id: -621,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'image',
        fileName: 'later.png',
        mimeType: 'image/png',
        thumbnail: '',
        width: 40,
        height: 40,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'uploaded',
        progress: 100,
        uploadedBytes: 5,
        totalBytes: 5,
        createdAt: '2026-05-14T00:11:03Z',
        batchId: 'later-batch',
        fileId: 'later-file',
        file: new Blob(['later'], { type: 'image/png' }),
      },
      {
        id: -622,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'image',
        fileName: 'album-1.png',
        mimeType: 'image/png',
        thumbnail: '',
        width: 40,
        height: 40,
        albumId: 'album-init',
        albumIndex: 1,
        albumSize: 2,
        phase: 'uploaded',
        progress: 100,
        uploadedBytes: 5,
        totalBytes: 5,
        createdAt: '2026-05-14T00:11:01Z',
        batchId: 'album-init-batch',
        fileId: 'album-file-1',
        file: new Blob(['album-1'], { type: 'image/png' }),
      },
      {
        id: -623,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'image',
        fileName: 'album-0.png',
        mimeType: 'image/png',
        thumbnail: '',
        width: 40,
        height: 40,
        albumId: 'album-init',
        albumIndex: 0,
        albumSize: 2,
        phase: 'uploaded',
        progress: 100,
        uploadedBytes: 5,
        totalBytes: 5,
        createdAt: '2026-05-14T00:11:01Z',
        batchId: 'album-init-batch',
        fileId: 'album-file-0',
        file: new Blob(['album-0'], { type: 'image/png' }),
      },
    ])

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return Promise.resolve({ ok: true, status: 204, json: async () => ({}) } as Response)
      }
      if (
        (url.endsWith('/api/chat/upload-batches/album-init-batch/commit') ||
          url.endsWith('/api/chat/upload-batches/later-batch/commit')) &&
        init?.method === 'POST'
      ) {
        return new Promise<Response>(() => {})
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.waitForChatUploadBackgroundReady()

    expect(events.filter((event) => event.type === 'added').map((event) => event.optimisticId)).toEqual([-623, -622, -621])
  })

  it('commits a resumable group album through one shared batch and preserves album order', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    const firstAlbumFile = new Blob(['abcd'], { type: 'image/png' })
    const secondAlbumFile = new Blob(['wxyz'], { type: 'image/png' })

    const sessionStateById: Record<string, { fileId: string; thumbnail: string; width: number; height: number }> = {
      'session-album-1': { fileId: 'group-file-1', thumbnail: 'thumb-1', width: 640, height: 480 },
      'session-album-2': { fileId: 'group-file-2', thumbnail: 'thumb-2', width: 800, height: 600 },
    }
    let sessionCounter = 0

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'group-album-batch' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        sessionCounter += 1
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: `session-album-${sessionCounter}`,
            resume_token: `resume-album-${sessionCounter}`,
            next_offset: 0,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T02:00:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      if (url.includes('/api/chat/upload-sessions/session-album-') && url.endsWith('/finalize') && init?.method === 'POST') {
        const sessionId = url.split('/').slice(-2, -1)[0]!
        return {
          ok: true,
          status: 200,
          json: async () => ({ final_chat_file_id: sessionStateById[sessionId]!.fileId }),
        } as Response
      }
      if (url.includes('/api/chat/upload-sessions/session-album-') && init?.method !== 'POST') {
        const sessionId = url.split('/').pop()!
        const state = sessionStateById[sessionId]!
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            total_bytes: 4,
            next_offset: 4,
            received_bytes: 4,
            final_chat_file_id: state.fileId,
            preview_metadata: {
              thumbnail: state.thumbnail,
              width: state.width,
              height: state.height,
            },
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/group-album-batch/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9202,
                sender_id: 15,
                receiver_id: 55,
                content: JSON.stringify({ file_id: 'group-file-2', album_index: 1 }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T00:30:02Z',
              },
              {
                id: 9201,
                sender_id: 15,
                receiver_id: 55,
                content: JSON.stringify({ file_id: 'group-file-1', album_index: 0 }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T00:30:01Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: firstAlbumFile.size, total: firstAlbumFile.size } as any)
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        session_id: 'session-album-1',
        received_bytes: firstAlbumFile.size,
        next_offset: firstAlbumFile.size,
        status: 'uploaded',
      })
      xhr.onload?.()
    })
    MockXHR.enqueueScenario((xhr) => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: secondAlbumFile.size, total: secondAlbumFile.size } as any)
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        session_id: 'session-album-2',
        received_bytes: secondAlbumFile.size,
        next_offset: secondAlbumFile.size,
        status: 'uploaded',
      })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -601,
      userId: -55,
      roomKind: 'group',
      albumId: 'group-album-1',
      albumIndex: 0,
      albumSize: 2,
      file: firstAlbumFile,
      fileName: 'first.png',
      localBlobUrl: 'blob:first',
    }))

    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -602,
      userId: -55,
      roomKind: 'group',
      albumId: 'group-album-1',
      albumIndex: 1,
      albumSize: 2,
      file: secondAlbumFile,
      fileName: 'second.png',
      localBlobUrl: 'blob:second',
    }))
    await vi.runAllTimersAsync()

    const fetchUrls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(fetchUrls.filter((url) => url.endsWith('/api/chat/upload-batches'))).toHaveLength(1)
    expect(fetchUrls.filter((url) => url.endsWith('/api/chat/upload-sessions'))).toHaveLength(2)
    expect(fetchUrls.filter((url) => url.endsWith('/api/chat/upload-batches/group-album-batch/commit'))).toHaveLength(1)

    const sentEvents = events.filter((event) => event.type === 'sent')
    expect(events.filter((event) => event.type === 'uploaded').map((event) => event.fileId)).toEqual(['group-file-1', 'group-file-2'])
    expect(sentEvents).toHaveLength(2)
    expect(sentEvents.map((event) => event.optimisticId)).toEqual([-601, -602])
    expect(sentEvents.map((event) => JSON.parse(event.serverMessage.content).file_id)).toEqual(['group-file-1', 'group-file-2'])
    expect(service.getPendingForUser(-55)).toEqual([])
    expect(MockXHR.instances).toHaveLength(2)
    expect(MockXHR.instances[0]?.url).toBe('https://coin.test/api/chat/upload-sessions/session-album-1/chunk')
    expect(MockXHR.instances[1]?.url).toBe('https://coin.test/api/chat/upload-sessions/session-album-2/chunk')
  })

  it('hands off a hidden single direct upload to the service worker and completes it from the bridge message', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    let serviceWorkerMessageHandler: ((event: { data: any }) => void) | null = null
    const postMessage = vi.fn()
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36',
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: {
        controller: { postMessage },
        addEventListener: vi.fn((type: string, handler: (event: { data: any }) => void) => {
          if (type === 'message') {
            serviceWorkerMessageHandler = handler
          }
        }),
      },
    })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'sw-batch-1' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'sw-session-1',
            resume_token: 'sw-resume-1',
            next_offset: 0,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T04:00:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    let activeChunkXhr: MockXHR | null = null
    MockXHR.enqueueScenario((xhr) => {
      activeChunkXhr = xhr
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -701,
      userId: 77,
      roomKind: 'direct',
      file: new Blob(['service-worker-image'], { type: 'image/png' }),
      fileName: 'sw-image.png',
      localBlobUrl: 'blob:sw-image',
    }))
    await vi.advanceTimersByTimeAsync(0)

    expect((activeChunkXhr as MockXHR | null)?.url).toBe('https://coin.test/api/chat/upload-sessions/sw-session-1/chunk')

    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    await Promise.resolve()
    await vi.runAllTimersAsync()

    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'chat-upload:handoff',
      uploadIds: [-701],
    }))

    ;(serviceWorkerMessageHandler as ((event: { data: any }) => void) | null)?.({
      data: {
        type: 'chat-upload:sent',
        uploadId: -701,
        serverMessage: {
          id: 9701,
          sender_id: 15,
          receiver_id: 77,
          content: JSON.stringify({ file_id: 'sw-file-1' }),
          message_type: 'image',
          is_read: false,
          created_at: '2026-05-14T00:40:00Z',
        },
      },
    })
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'sent'])
    expect(events[1]).toMatchObject({
      type: 'sent',
      optimisticId: -701,
      localBlobUrl: 'blob:sw-image',
    })
    expect(service.getPendingForUser(77)).toEqual([])
  })

  it('marks a handed-off single direct upload as failed when the service worker bridge reports an error', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    let serviceWorkerMessageHandler: ((event: { data: any }) => void) | null = null
    let visibilityState: 'hidden' | 'visible' = 'hidden'
    const postMessage = vi.fn()
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36',
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: {
        controller: { postMessage },
        addEventListener: vi.fn((type: string, handler: (event: { data: any }) => void) => {
          if (type === 'message') {
            serviceWorkerMessageHandler = handler
          }
        }),
      },
    })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibilityState,
    })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'sw-batch-error' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'sw-session-error',
            resume_token: 'sw-resume-error',
            next_offset: 0,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T04:10:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    let activeChunkXhr: MockXHR | null = null
    MockXHR.enqueueScenario((xhr) => {
      activeChunkXhr = xhr
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -711,
      userId: 77,
      roomKind: 'direct',
      file: new Blob(['service-worker-error'], { type: 'image/png' }),
      fileName: 'sw-error.png',
      localBlobUrl: 'blob:sw-error',
    }))
    await vi.advanceTimersByTimeAsync(0)

    expect((activeChunkXhr as MockXHR | null)?.url).toBe('https://coin.test/api/chat/upload-sessions/sw-session-error/chunk')

    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    await Promise.resolve()
    await vi.runAllTimersAsync()

    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'chat-upload:handoff',
      uploadIds: [-711],
    }))

    ;(serviceWorkerMessageHandler as ((event: { data: any }) => void) | null)?.({
      data: {
        type: 'chat-upload:error',
        uploadId: -711,
        errorMessage: 'service worker upload failed',
      },
    })
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'error'])
    expect(events[1]).toMatchObject({
      type: 'error',
      optimisticId: -711,
      errorMessage: 'service worker upload failed',
    })
    expect(service.getPendingForUser(77)).toEqual([
      expect.objectContaining({
        id: -711,
        phase: 'failed',
        errorMessage: 'service worker upload failed',
      }),
    ])
  })

  it('reclaims handed-off uploads on foreground wake and resumes them from persisted session state', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    let visibilityState: 'hidden' | 'visible' = 'hidden'
    const postMessage = vi.fn()
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36',
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: {
        controller: { postMessage },
        addEventListener: vi.fn(),
      },
    })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibilityState,
    })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'sw-batch-reclaim' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'sw-session-reclaim',
            resume_token: 'sw-resume-reclaim',
            next_offset: 0,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T04:20:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/sw-session-reclaim')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            total_bytes: 5,
            next_offset: 5,
            received_bytes: 5,
            final_chat_file_id: 'sw-file-reclaim',
            preview_metadata: {
              thumbnail: 'sw-thumb',
              width: 640,
              height: 480,
            },
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/sw-batch-reclaim/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9712,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'sw-file-reclaim' }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T00:47:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    let activeChunkXhr: MockXHR | null = null
    MockXHR.enqueueScenario((xhr) => {
      activeChunkXhr = xhr
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -712,
      userId: 77,
      roomKind: 'direct',
      file: new Blob(['sw-resume'], { type: 'image/png' }),
      fileName: 'sw-resume.png',
      localBlobUrl: 'blob:sw-resume',
    }))
    await vi.advanceTimersByTimeAsync(0)

    expect((activeChunkXhr as MockXHR | null)?.url).toBe('https://coin.test/api/chat/upload-sessions/sw-session-reclaim/chunk')

    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    await Promise.resolve()
    await vi.runAllTimersAsync()

    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'chat-upload:handoff',
      uploadIds: [-712],
    }))

    visibilityState = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    await Promise.resolve()
    await vi.runAllTimersAsync()

    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'chat-upload:reclaim',
      uploadIds: [-712],
    }))
    expect(events.map((event) => event.type)).toEqual(['added', 'uploaded', 'sent'])
    expect(events[1]).toMatchObject({
      type: 'uploaded',
      optimisticId: -712,
      fileId: 'sw-file-reclaim',
    })
    expect(events[2]).toMatchObject({
      type: 'sent',
      optimisticId: -712,
      localBlobUrl: 'blob:sw-resume',
    })
    expect(service.getPendingForUser(77)).toEqual([])
  })

  it('restores uploaded document resumes from persisted file bytes and reuses the reconstructed file on commit', async () => {
    installIndexedDb([
      {
        id: -702,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'document',
        fileName: 'bytes-resume.pdf',
        mimeType: 'application/pdf',
        thumbnail: '',
        width: 0,
        height: 0,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'uploaded',
        progress: 100,
        uploadedBytes: 3,
        totalBytes: 3,
        createdAt: '2026-05-14T00:45:00Z',
        batchId: 'bytes-resume-batch',
        sessionId: 'bytes-session',
        fileId: 'bytes-file',
        fileBytes: new Uint8Array([100, 111, 99]),
      },
    ])

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/bytes-resume-batch/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9702,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'bytes-file' }),
                message_type: 'document',
                is_read: false,
                created_at: '2026-05-14T00:46:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.waitForChatUploadBackgroundReady()
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'sent'])
    expect(events[0]).toMatchObject({ type: 'added', optimisticId: -702 })
    expect(events[1]).toMatchObject({
      type: 'sent',
      optimisticId: -702,
      localBlobUrl: 'blob:restored-upload',
    })
    expect(service.getPendingForUser(77)).toEqual([])
  })

  it('force-flushes an album when preprocessing dropped an expected legacy item', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({
        file_id: 'legacy-album-only-file',
        file_name: 'only.png',
        mime_type: 'image/png',
        thumbnail: 'only-thumb',
        width: 320,
        height: 240,
      })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -801,
      userId: -44,
      roomKind: 'channel',
      albumId: 'legacy-album-missing-one',
      albumIndex: 0,
      albumSize: 2,
      fileName: 'only.png',
      localBlobUrl: 'blob:only',
    }))
    await vi.runAllTimersAsync()

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('legacy-album-missing-one'),
    )
    expect(events.map((event) => event.type)).toEqual(['added', 'uploaded', 'sent'])
    expect(fetchMock).toHaveBeenCalledWith(
      'https://coin.test/api/chat/rooms/44/send',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(service.getPendingForUser(-44)).toEqual([])

    warnSpy.mockRestore()
  })

  it('marks a direct resumable upload failed when session creation returns an auth error', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'auth-error-batch' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: false,
          status: 401,
          json: async () => ({ detail: 'expired token' }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -811,
      userId: 77,
      roomKind: 'direct',
      fileName: 'auth-error.png',
    }))
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'error'])
    expect(events[1]).toMatchObject({
      optimisticId: -811,
      errorMessage: 'نشست شما منقضی شده است. لطفاً صفحه را رفرش کنید.',
    })
    expect(service.getPendingForUser(77)).toEqual([
      expect.objectContaining({ id: -811, phase: 'failed' }),
    ])
  })

  it('marks a legacy upload failed when upload-media returns invalid JSON', async () => {
    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = '{not-json'
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({
      optimisticId: -812,
      userId: -88,
      roomKind: 'channel',
    }))
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'error'])
    expect(events[1]).toMatchObject({
      optimisticId: -812,
      errorMessage: 'پاسخ نامعتبر سرور',
    })
    expect(service.getPendingForUser(-88)).toEqual([
      expect.objectContaining({ id: -812, phase: 'failed' }),
    ])
  })

  it('continues notifying subscribers when one upload subscriber throws', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const service = await importFreshModule()
    const events: any[] = []
    const unsubscribeThrowing = service.subscribeToUploads(() => {
      throw new Error('subscriber exploded')
    })
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({ file_id: 'subscriber-file' })
      xhr.onload?.()
    })

    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -821, userId: -31 }))
    await vi.runAllTimersAsync()

    expect(warnSpy).toHaveBeenCalledWith(
      '[uploadService] subscriber failed:',
      expect.any(Error),
    )
    expect(events.map((event) => event.type)).toEqual(['added', 'uploaded', 'sent'])

    unsubscribeThrowing()
    warnSpy.mockClear()
    MockXHR.enqueueScenario((xhr) => {
      xhr.status = 200
      xhr.responseText = JSON.stringify({ file_id: 'subscriber-file-2' })
      xhr.onload?.()
    })
    await service.submitUpload(makeBaseSubmitParams({ optimisticId: -822, userId: -31 }))
    await vi.runAllTimersAsync()

    expect(warnSpy).not.toHaveBeenCalledWith(
      '[uploadService] subscriber failed:',
      expect.any(Error),
    )
    warnSpy.mockRestore()
  })

  it('ignores persisted records that cannot restore their file blob', async () => {
    installIndexedDb([
      {
        id: -831,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'image',
        fileName: 'missing-file.png',
        mimeType: 'image/png',
        thumbnail: '',
        width: 0,
        height: 0,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'queued',
        progress: 0,
        uploadedBytes: 0,
        totalBytes: 0,
        createdAt: '2026-05-14T01:10:00Z',
      },
    ])

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.waitForChatUploadBackgroundReady()
    await vi.runAllTimersAsync()

    expect(events).toEqual([])
    expect(service.getPendingForUser(77)).toEqual([])
    expect(MockXHR.instances).toHaveLength(0)
  })

  it('resumes an upload whose existing session is already ready and commits without another chunk', async () => {
    installIndexedDb([
      {
        id: -841,
        userId: 77,
        roomKind: 'direct',
        senderId: 15,
        msgType: 'video',
        fileName: 'ready-video.webm',
        mimeType: 'video/webm',
        thumbnail: 'client-video-thumb',
        width: 100,
        height: 50,
        durationMs: 1200,
        albumId: null,
        albumIndex: 0,
        albumSize: 1,
        phase: 'uploading',
        progress: 20,
        uploadedBytes: 2,
        totalBytes: 10,
        createdAt: '2026-05-14T01:20:00Z',
        batchId: 'ready-batch',
        sessionId: 'ready-session',
        resumeToken: 'ready-token',
        nextOffset: 2,
        file: new Blob(['readyvideo'], { type: 'video/webm' }),
      },
    ])

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/ready-session') && init?.method !== 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            total_bytes: 10,
            next_offset: 10,
            received_bytes: 10,
            final_chat_file_id: 'ready-video-file',
            preview_metadata: {
              thumbnail: 'server-video-thumb',
              width: 1920,
              height: 1080,
              duration_ms: 3456,
            },
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/ready-batch/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9841,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'ready-video-file' }),
                message_type: 'video',
                is_read: false,
                created_at: '2026-05-14T01:21:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.waitForChatUploadBackgroundReady()
    await vi.runAllTimersAsync()

    expect(MockXHR.instances).toHaveLength(0)
    expect(events.map((event) => event.type)).toEqual(['added', 'uploaded', 'sent'])
    expect(events[1]).toMatchObject({
      optimisticId: -841,
      fileId: 'ready-video-file',
    })
    expect(JSON.parse(events[1].content)).toMatchObject({
      file_id: 'ready-video-file',
      thumbnail: 'server-video-thumb',
      width: 1920,
      height: 1080,
      durationMs: 3456,
    })
    expect(service.getPendingForUser(77)).toEqual([])
  })

  it('falls back from blob persistence to bytes when IndexedDB rejects document blobs', async () => {
    const storedRecords: Array<Record<string, any>> = []
    let putCount = 0
    const db = {
      objectStoreNames: { contains: () => true },
      transaction: () => {
        const tx: {
          objectStore: (_store: string) => { put: (value: Record<string, any>) => void }
          oncomplete: null | (() => void)
          onerror: null | (() => void)
          onabort: null | (() => void)
        } = {
          objectStore: () => ({
            put(value: Record<string, any>) {
              storedRecords.push({ ...value })
              putCount += 1
              Promise.resolve().then(() => {
                if (putCount === 1) {
                  tx.onerror?.()
                } else {
                  tx.oncomplete?.()
                }
              })
            },
          }),
          oncomplete: null,
          onerror: null,
          onabort: null,
        }
        return tx
      },
      close: vi.fn(),
      onversionchange: null,
    }
    vi.stubGlobal('indexedDB', {
      open: () => {
        const request: {
          result: typeof db
          error: null
          onupgradeneeded: null | ((event: { target: { result: typeof db } }) => void)
          onsuccess: null | (() => void)
          onerror: null | (() => void)
        } = {
          result: db,
          error: null,
          onupgradeneeded: null,
          onsuccess: null,
          onerror: null,
        }
        Promise.resolve().then(() => request.onsuccess?.())
        return request
      },
    })

    class ThrowingFileReader {
      result = null
      error = null
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      readAsDataURL() {
        throw new Error('reader boom')
      }
    }
    vi.stubGlobal('FileReader', ThrowingFileReader as any)

    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    await expect(hooks.blobToDataUrl(new Blob(['doc'], { type: 'text/plain' }))).rejects.toThrow('reader boom')

    const fileBytes = new Uint8Array([100, 111, 99]).buffer
    const fallbackFile = {
      size: 3,
      type: 'application/pdf',
      arrayBuffer: vi.fn().mockResolvedValue(fileBytes),
    }
    const upload = {
      id: -901,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'document' as const,
      file: fallbackFile as any,
      fileName: 'report.pdf',
      mimeType: 'application/pdf',
      thumbnail: '',
      width: 0,
      height: 0,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'queued' as const,
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 3,
      createdAt: '2026-05-14T02:00:00Z',
    }

    await hooks.idbPut(upload)

    expect(storedRecords).toHaveLength(2)
    expect(storedRecords[0]?.fileDataUrl).toBeUndefined()
  expect(storedRecords[0]?.file).toBe(fallbackFile)
    expect(storedRecords[1]?.file).toBeUndefined()
    expect(storedRecords[1]?.fileBytes).toBeInstanceOf(ArrayBuffer)
  expect(fallbackFile.arrayBuffer).toHaveBeenCalledOnce()
  })

  it('swallows document byte fallback failures and IndexedDB open failures during persistence', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const storedRecords: Array<Record<string, any>> = []
    let putCount = 0
    const db = {
      objectStoreNames: { contains: () => true },
      transaction: () => {
        const tx: {
          objectStore: (_store: string) => { put: (value: Record<string, any>) => void }
          oncomplete: null | (() => void)
          onerror: null | (() => void)
          onabort: null | (() => void)
        } = {
          objectStore: () => ({
            put(value: Record<string, any>) {
              storedRecords.push({ ...value })
              putCount += 1
              Promise.resolve().then(() => {
                if (putCount === 1) {
                  tx.onerror?.()
                } else {
                  tx.oncomplete?.()
                }
              })
            },
          }),
          oncomplete: null,
          onerror: null,
          onabort: null,
        }
        return tx
      },
      close: vi.fn(),
      onversionchange: null,
    }
    vi.stubGlobal('indexedDB', {
      open: () => {
        const request: {
          result: typeof db
          error: null
          onupgradeneeded: null | ((event: { target: { result: typeof db } }) => void)
          onsuccess: null | (() => void)
          onerror: null | (() => void)
        } = {
          result: db,
          error: null,
          onupgradeneeded: null,
          onsuccess: null,
          onerror: null,
        }
        Promise.resolve().then(() => request.onsuccess?.())
        return request
      },
    })

    class ImmediateFileReader {
      result = 'data:application/pdf;base64,ZG9j'
      error = null
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      readAsDataURL() {
        this.onload?.()
      }
    }
    vi.stubGlobal('FileReader', ImmediateFileReader as any)

    let service = await importFreshModule()
    let hooks = service.__chatUploadBackgroundTestHooks
    const failingFile = {
      size: 3,
      type: 'application/pdf',
      arrayBuffer: vi.fn().mockRejectedValue(new Error('arrayBuffer failed')),
    }
    await hooks.idbPut({
      id: -902,
      userId: 77,
      roomKind: 'direct',
      senderId: 15,
      msgType: 'document',
      file: failingFile as any,
      fileName: 'broken.pdf',
      mimeType: 'application/pdf',
      thumbnail: '',
      width: 0,
      height: 0,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'queued',
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 3,
      createdAt: '2026-05-14T02:05:00Z',
    } as any)

    expect(storedRecords).toHaveLength(1)
  expect(storedRecords[0]?.file).toBe(failingFile)

    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('open failed')
      },
    })
    service = await importFreshModule()
    hooks = service.__chatUploadBackgroundTestHooks
    await hooks.idbPut({
      id: -903,
      userId: 77,
      roomKind: 'direct',
      senderId: 15,
      msgType: 'image',
      file: new Blob(['img'], { type: 'image/png' }),
      fileName: 'open-fail.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 10,
      height: 10,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'queued',
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 3,
      createdAt: '2026-05-14T02:06:00Z',
    } as any)

    expect(warnSpy).toHaveBeenCalledWith('[uploadService] idbPut open failed:', expect.any(Error))
    warnSpy.mockRestore()
  })

  it('handles service-worker handoff failures, malformed bridge payloads, and reclaim URL creation failures', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { records } = installIndexedDb([])
    let messageHandler: ((event: { data: any }) => void) | null = null
    const controller = { postMessage: vi.fn(() => { throw new Error('post failed') }) }
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36',
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      writable: true,
      value: {
        controller,
        addEventListener: vi.fn((type: string, handler: (event: { data: any }) => void) => {
          if (type === 'message') {
            messageHandler = handler
          }
        }),
      },
    })

    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    await expect(hooks.postUploadsToServiceWorker([-911])).resolves.toBe(false)
    expect(warnSpy).toHaveBeenCalledWith(
      '[uploadService] failed to hand off uploads to service worker:',
      expect.any(Error),
    )

    controller.postMessage = vi.fn()
    const throwingUpload = {
      id: -912,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['payload'], { type: 'image/png' }),
      fileName: 'sw-pause.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 10,
      height: 10,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploading' as const,
      progress: 10,
      uploadedBytes: 1,
      totalBytes: 10,
      createdAt: '2026-05-14T02:10:00Z',
    }
    hooks.state.xhrControllers.set(-912, { abort() { throw new Error('xhr abort failed') } } as any)
    hooks.state.sendControllers.set(-912, { abort() { throw new Error('send abort failed') } } as any)
    const pausePromise = hooks.pauseUploadForServiceWorker(throwingUpload)
    hooks.state.serviceWorkerHandoffResolvers.get(-912)?.()
    await pausePromise
    expect(hooks.state.serviceWorkerOwnedUploads.has(-912)).toBe(true)

      if (!messageHandler) {
        throw new Error('Expected service worker message handler')
      }
      ;(messageHandler as (e: any) => void)({ data: { type: 'chat-upload:sent' } })
    expect(hooks.state.pendingUploads.has(-999)).toBe(false)

    records.set(-913, {
      ...throwingUpload,
      id: -913,
      phase: 'queued',
      localBlobUrl: undefined,
      file: new Blob(['restored'], { type: 'image/png' }),
    })
    hooks.state.serviceWorkerOwnedUploads.add(-913)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {
        throw new Error('url failed')
      }),
    })
    await hooks.reclaimUploadsFromServiceWorker()
    expect(hooks.state.pendingUploads.get(-913)?.localBlobUrl).toBeUndefined()

    warnSpy.mockRestore()
  })

  it('reuses existing resumable album batches, resets terminal sessions, and tolerates cancel cleanup failures', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const albumUpload: PendingUpload = {
      id: -921,
      userId: 77,
      roomKind: 'group' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['album'], { type: 'image/png' }),
      fileName: 'album.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 10,
      height: 10,
      albumId: 'existing-album',
      albumIndex: 0,
      albumSize: 2,
      phase: 'queued' as const,
      progress: 0,
      uploadedBytes: 0,
      totalBytes: 5,
      createdAt: '2026-05-14T02:15:00Z',
    }
    hooks.ensureAlbumBatch('existing-album', 77, 2, 'group', 'existing-batch')
    await hooks.ensureResumableUploadBatch(albumUpload)
    expect(albumUpload.batchId).toBe('existing-batch')

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/chat/upload-sessions/terminal-session') && init?.method !== 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'failed',
            total_bytes: 5,
            next_offset: 0,
            received_bytes: 0,
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ batch_id: 'new-batch' }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            session_id: 'new-session',
            resume_token: 'new-resume',
            next_offset: 2,
            chunk_size: 5 * 1024 * 1024,
            expires_at: '2026-05-14T03:00:00Z',
            status: 'uploading',
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/batch-cancel/cancel') && init?.method === 'POST') {
        throw new Error('cancel failed')
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    const upload = {
      id: -922,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['reset'], { type: 'image/png' }),
      fileName: 'reset.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 12,
      height: 12,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploading' as const,
      progress: 40,
      uploadedBytes: 2,
      totalBytes: 5,
      createdAt: '2026-05-14T02:16:00Z',
      batchId: 'old-batch',
      sessionId: 'terminal-session',
      resumeToken: 'old-resume',
      nextOffset: 2,
    }

    await hooks.ensureResumableUploadSession(upload)
    expect(upload.batchId).toBe('new-batch')
    expect(upload.sessionId).toBe('new-session')
    expect(upload.resumeToken).toBe('new-resume')

    await expect(hooks.finalizeResumableUploadSession({ ...upload, sessionId: undefined })).rejects.toThrow('Missing upload session id')
    await expect(hooks.cancelServerUploadState({ ...upload, batchId: 'batch-cancel', sessionId: undefined })).resolves.toBeUndefined()
  })

  it('marks single resumable commits failed on empty payloads and send timeouts', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const makeUpload = (id: number, batchId: string) => ({
      id,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['payload'], { type: 'image/png' }),
      fileName: `single-${Math.abs(id)}.png`,
      mimeType: 'image/png',
      thumbnail: '',
      width: 10,
      height: 10,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploaded' as const,
      progress: 100,
      uploadedBytes: 7,
      totalBytes: 7,
      batchId,
      fileId: `file-${Math.abs(id)}`,
      createdAt: '2026-05-14T02:20:00Z',
    })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/chat/upload-batches/empty-commit/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ messages: [] }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/timeout-commit/commit') && init?.method === 'POST') {
        return await new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new Error('aborted by timeout signal')))
        })
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    const emptyUpload = makeUpload(-931, 'empty-commit')
    hooks.state.pendingUploads.set(emptyUpload.id, emptyUpload)
    await hooks.commitSingleUploadBatch(emptyUpload)
    expect(emptyUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -931,
      errorMessage: 'پیام نهایی از سرور دریافت نشد',
    }))

    const timeoutUpload = { ...makeUpload(-932, 'timeout-commit'), sendRetryCount: 999 }
    hooks.state.pendingUploads.set(timeoutUpload.id, timeoutUpload)
    const timeoutPromise = hooks.commitSingleUploadBatch(timeoutUpload)
    await vi.runAllTimersAsync()
    await timeoutPromise
    expect(timeoutUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -932,
      errorMessage: 'Network Error (send timeout)',
    }))
  })

  it('covers legacy send parse-failure, cancel, and timeout cleanup branches', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    const makeLegacyUpload = (id: number) => ({
      id,
      userId: -77,
      roomKind: 'channel' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['payload'], { type: 'image/png' }),
      fileName: `legacy-${Math.abs(id)}.png`,
      mimeType: 'image/png',
      thumbnail: '',
      width: 120,
      height: 80,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploaded' as const,
      progress: 100,
      uploadedBytes: 7,
      totalBytes: 7,
      fileId: `legacy-file-${Math.abs(id)}`,
      createdAt: '2026-05-14T02:25:00Z',
    })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/chat/rooms/77/send')) {
        const brokenJsonResponse = new Response(null, { status: 500 })
        Object.defineProperty(brokenJsonResponse, 'json', {
          configurable: true,
          value: async () => {
            throw new Error('broken json')
          },
        })
        return brokenJsonResponse
      }
      if (url.endsWith('/api/chat/rooms/78/send')) {
        hooks.state.abortFlags.add(-942)
        throw new Error('network down')
      }
      if (url.endsWith('/api/chat/rooms/79/send')) {
        return await new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new Error('legacy timeout abort')))
        })
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    const parseFailUpload = makeLegacyUpload(-941)
    ;(parseFailUpload as any).sendRetryCount = 999
    hooks.state.pendingUploads.set(parseFailUpload.id, parseFailUpload)
    await hooks.sendOneLegacy(parseFailUpload)
    expect(parseFailUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -941,
      errorMessage: 'خطای ارسال (500)',
    }))

    const cancelledUpload = {
      ...makeLegacyUpload(-942),
      userId: -78,
      albumId: 'legacy-cancel-album',
    }
    hooks.state.pendingUploads.set(cancelledUpload.id, cancelledUpload)
    hooks.state.albumBatches.set('legacy-cancel-album', {
      albumId: 'legacy-cancel-album',
      userId: -78,
      roomKind: 'channel',
      expectedCount: 1,
      optimisticIds: new Set([cancelledUpload.id]),
      commitRetryCount: 0,
      flushing: false,
    })
    await hooks.sendOneLegacy(cancelledUpload)
    expect(events).toContainEqual(expect.objectContaining({ type: 'cancelled', optimisticId: -942 }))
    expect(hooks.state.pendingUploads.has(-942)).toBe(false)

    const timeoutUpload = { ...makeLegacyUpload(-943), userId: -79, sendRetryCount: 999 }
    hooks.state.pendingUploads.set(timeoutUpload.id, timeoutUpload)
    const timeoutPromise = hooks.sendOneLegacy(timeoutUpload)
    await vi.runAllTimersAsync()
    await timeoutPromise
    expect(timeoutUpload.phase).toBe('failed')
    expect(events).toContainEqual(expect.objectContaining({
      type: 'error',
      optimisticId: -943,
      errorMessage: 'Network Error (send timeout)',
    }))
  })

  it('resumes session-backed foreground uploads with preserved progress and flushes uploaded albums', async () => {
    installIndexedDb([])
    const service = await importFreshModule()
    const hooks = service.__chatUploadBackgroundTestHooks
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-sessions/foreground-session') && init?.method !== 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            total_bytes: 10,
            next_offset: 10,
            received_bytes: 10,
            final_chat_file_id: 'foreground-file',
            preview_metadata: {
              thumbnail: 'foreground-thumb',
              width: 100,
              height: 80,
            },
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/foreground-batch/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9931,
                sender_id: 15,
                receiver_id: 77,
                content: JSON.stringify({ file_id: 'foreground-file' }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T02:31:00Z',
              },
            ],
          }),
        } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/foreground-album-batch/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9932,
                sender_id: 15,
                receiver_id: 55,
                content: JSON.stringify({ file_id: 'album-file', album_index: 0 }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T02:31:30Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    const uploading = {
      id: -951,
      userId: 77,
      roomKind: 'direct' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['1234567890'], { type: 'image/png' }),
      fileName: 'foreground.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 0,
      height: 0,
      albumId: null,
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploading' as const,
      progress: 10,
      uploadedBytes: 1,
      totalBytes: 10,
      batchId: 'foreground-batch',
      sessionId: 'foreground-session',
      resumeToken: 'foreground-token',
      nextOffset: 4,
      createdAt: '2026-05-14T02:30:00Z',
    }
    const uploadedAlbum = {
      id: -952,
      userId: -55,
      roomKind: 'group' as const,
      senderId: 15,
      msgType: 'image' as const,
      file: new Blob(['album'], { type: 'image/png' }),
      fileName: 'foreground-album.png',
      mimeType: 'image/png',
      thumbnail: '',
      width: 20,
      height: 20,
      albumId: 'foreground-album',
      albumIndex: 0,
      albumSize: 1,
      phase: 'uploaded' as const,
      progress: 100,
      uploadedBytes: 5,
      totalBytes: 5,
      batchId: 'foreground-album-batch',
      fileId: 'album-file',
      createdAt: '2026-05-14T02:30:30Z',
    }
    hooks.state.pendingUploads.set(uploading.id, uploading)
    hooks.state.pendingUploads.set(uploadedAlbum.id, uploadedAlbum)
    hooks.ensureAlbumBatch('foreground-album', -55, 1, 'group', 'foreground-album-batch').optimisticIds.add(uploadedAlbum.id)

    await hooks.resumePendingUploadsAfterForegroundWake()
    expect(uploading.totalBytes).toBe(uploading.file.size)
    expect(uploading.uploadedBytes).toBe(4)
    expect(uploading.progress).toBe(40)
    await Promise.resolve()
    await vi.runAllTimersAsync()

    expect(events).toContainEqual(expect.objectContaining({ type: 'sent', optimisticId: -952 }))
  })

  it('reuses the existing init promise on repeat init calls and tolerates resume failures', async () => {
    const service = await importFreshModule()

    const firstInit = service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    const secondInit = service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    expect(secondInit).toBe(firstInit)
    await firstInit
    await secondInit
  })

  it('resumes uploaded albums during init even when preview URL recreation fails', async () => {
    installIndexedDb([
      {
        id: -961,
        userId: -55,
        roomKind: 'group',
        senderId: 15,
        msgType: 'image',
        fileName: 'init-album.png',
        mimeType: 'image/png',
        thumbnail: '',
        width: 50,
        height: 40,
        albumId: 'init-album',
        albumIndex: 0,
        albumSize: 1,
        phase: 'uploaded',
        progress: 100,
        uploadedBytes: 5,
        totalBytes: 5,
        batchId: 'init-album-batch',
        fileId: 'init-album-file',
        createdAt: '2026-05-14T02:40:00Z',
        file: new Blob(['album'], { type: 'image/png' }),
      },
    ])
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {
        throw new Error('createObjectURL failed')
      }),
    })

    const service = await importFreshModule()
    const events: any[] = []
    service.subscribeToUploads((event) => events.push(event))
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/activity')) {
        return { ok: true, status: 204, json: async () => ({}) } as Response
      }
      if (url.endsWith('/api/chat/upload-batches/init-album-batch/commit') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [
              {
                id: 9961,
                sender_id: 15,
                receiver_id: 55,
                content: JSON.stringify({ file_id: 'init-album-file', album_index: 0 }),
                message_type: 'image',
                is_read: false,
                created_at: '2026-05-14T02:41:00Z',
              },
            ],
          }),
        } as Response
      }
      throw new Error(`Unexpected fetch ${url}`)
    })

    await service.initChatUploadBackground({ apiBaseUrl: 'https://coin.test', getAuthToken: () => 'jwt' })
    await service.waitForChatUploadBackgroundReady()
    await vi.runAllTimersAsync()

    expect(events.map((event) => event.type)).toEqual(['added', 'sent'])
    expect(service.getPendingForUser(-55)).toEqual([])
  })
})