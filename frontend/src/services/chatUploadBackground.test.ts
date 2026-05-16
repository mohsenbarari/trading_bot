import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
})