import { defineComponent, h, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Message } from '../../types/chat'
import { useChatMedia } from './useChatMedia'

const mediaMocks = vi.hoisted(() => ({
  submitUpload: vi.fn(async () => {}),
  cancelUpload: vi.fn(),
  getPendingForUser: vi.fn<(userId: number) => any[]>(() => []),
  buildOptimisticMessageFromUpload: vi.fn((upload: any) => ({
    id: upload.id,
    sender_id: upload.senderId,
    receiver_id: upload.userId,
    content: JSON.stringify({ placeholder: true, file_id: upload.fileId || null }),
    message_type: upload.msgType,
    is_read: true,
    is_sending: true,
    created_at: upload.createdAt,
  })),
  waitForReady: vi.fn(async () => {}),
  uploadHandler: null as null | ((event: any) => void),
  uploadUnsubscribe: vi.fn(),

  cancelDocumentDownload: vi.fn(),
  getCompletedDocumentDownloadUrl: vi.fn<(fileId: string) => string>(() => ''),
  getPendingDocumentDownloadsForUser: vi.fn(() => []),
  startDocumentDownload: vi.fn(async () => {}),
  documentHandler: null as null | ((event: any) => void),
  documentUnsubscribe: vi.fn(),
}))

const preprocessMocks = vi.hoisted(() => ({
  canUseImagePreprocessWorker: vi.fn(() => false),
  getRecommendedImagePreprocessParallelism: vi.fn(() => 1),
  processImageInWorker: vi.fn(),
  primeMediaPreprocessTelemetry: vi.fn(),
  recordMediaPreprocessTelemetry: vi.fn(),
}))

const heicMocks = vi.hoisted(() => ({
  convert: vi.fn<(options?: unknown) => Promise<Blob | Blob[]>>(async () => new Blob(['converted-heic'], { type: 'image/jpeg' })),
}))

const fileCacheMocks = vi.hoisted(() => ({
  ensureFileCached: vi.fn<(fileId: string, fileName: string, options?: Record<string, unknown>) => Promise<any | null>>(async () => null),
  getCachedFileObjectUrl: vi.fn<(fileId: string) => Promise<string | null>>(async () => null),
}))

vi.mock('../../utils/imagePreprocessClient', () => ({
  canUseImagePreprocessWorker: preprocessMocks.canUseImagePreprocessWorker,
  getRecommendedImagePreprocessParallelism: preprocessMocks.getRecommendedImagePreprocessParallelism,
  processImageInWorker: preprocessMocks.processImageInWorker,
}))

vi.mock('../../utils/chatMediaTelemetry', () => ({
  primeMediaPreprocessTelemetry: preprocessMocks.primeMediaPreprocessTelemetry,
  recordMediaPreprocessTelemetry: preprocessMocks.recordMediaPreprocessTelemetry,
}))

vi.mock('heic2any', () => ({
  default: heicMocks.convert,
}))

vi.mock('./useChatFileHandler', () => ({
  ensureFileCached: fileCacheMocks.ensureFileCached,
  getCachedFileObjectUrl: fileCacheMocks.getCachedFileObjectUrl,
}))

vi.mock('../../services/chatUploadBackground', () => ({
  submitUpload: mediaMocks.submitUpload,
  cancelUpload: mediaMocks.cancelUpload,
  subscribeToUploads: (handler: (event: any) => void) => {
    mediaMocks.uploadHandler = handler
    return () => {
      mediaMocks.uploadUnsubscribe(handler)
      if (mediaMocks.uploadHandler === handler) {
        mediaMocks.uploadHandler = null
      }
    }
  },
  getPendingForUser: mediaMocks.getPendingForUser,
  buildOptimisticMessageFromUpload: mediaMocks.buildOptimisticMessageFromUpload,
  waitForChatUploadBackgroundReady: mediaMocks.waitForReady,
}))

vi.mock('../../services/chatDocumentDownloadBackground', () => ({
  cancelDocumentDownload: mediaMocks.cancelDocumentDownload,
  getCompletedDocumentDownloadUrl: mediaMocks.getCompletedDocumentDownloadUrl,
  getPendingDocumentDownloadsForUser: mediaMocks.getPendingDocumentDownloadsForUser,
  startDocumentDownload: mediaMocks.startDocumentDownload,
  subscribeToDocumentDownloads: (handler: (event: any) => void) => {
    mediaMocks.documentHandler = handler
    return () => {
      mediaMocks.documentUnsubscribe(handler)
      if (mediaMocks.documentHandler === handler) {
        mediaMocks.documentHandler = null
      }
    }
  },
}))

function makeImageMessage(id: number, overrides: Partial<Message> = {}): Message {
  return {
    id,
    sender_id: 5,
    receiver_id: -7,
    content: JSON.stringify({ file_id: `file-${id}`, thumbnail: `thumb-${id}`, album_id: 'album-1', album_index: id === 1 ? 0 : 1 }),
    message_type: 'image',
    is_read: true,
    created_at: `2026-05-14T00:00:0${id}Z`,
    local_blob_url: `blob:image-${id}`,
    ...overrides,
  }
}

function makeDocumentMessage(id: number, overrides: Partial<Message> = {}): Message {
  return {
    id,
    sender_id: 6,
    receiver_id: -7,
    content: JSON.stringify({ file_id: `doc-${id}`, file_name: `doc-${id}.pdf`, mime_type: 'application/pdf' }),
    message_type: 'document',
    is_read: true,
    created_at: '2026-05-14T00:00:00Z',
    ...overrides,
  }
}

function mountHarness(initialMessages: Message[] = [], selectedUser: number | null = -7) {
  const scrollToBottom = vi.fn()

  const Harness = defineComponent({
    setup(_, { expose }) {
      const messages = ref<Message[]>([...initialMessages])
      const selectedUserId = ref<number | null>(selectedUser)
      const selectedRoomKind = ref<'direct' | 'group' | 'channel'>('channel')
      const error = ref('')
      const isUploading = ref(false)

      const media = useChatMedia({
        apiBaseUrl: 'https://coin.test',
        jwtToken: 'jwt',
        currentUserId: 5,
        selectedUserId,
        selectedRoomKind,
        messages,
        error,
        isUploading,
        scrollToBottom,
        sendMediaMessage: vi.fn(async () => null),
      })

      expose({
        ...media,
        messages,
        selectedUserId,
        selectedRoomKind,
        error,
        isUploading,
      })

      return () => h('div')
    },
  })

  const wrapper = mount(Harness)
  return { wrapper, scrollToBottom }
}

describe('useChatMedia', () => {
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  const originalCreateImageBitmap = globalThis.createImageBitmap
  const originalRequestAnimationFrame = globalThis.requestAnimationFrame
  const originalCancelAnimationFrame = globalThis.cancelAnimationFrame
  const originalImage = globalThis.Image
  const originalFileReader = globalThis.FileReader
  const originalIndexedDB = globalThis.indexedDB

  function stubCanvasImagePipeline(width = 640, height = 320) {
    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      writable: true,
      value: vi.fn(async () => ({
        width,
        height,
        close: vi.fn(),
      })),
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D))
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function toBlob(callback: BlobCallback) {
      callback(new Blob(['canvas-result'], { type: 'image/jpeg' }))
    })
  }

  function createFakeVideoElement(options: {
    width?: number
    height?: number
    duration?: number
    emitError?: boolean
    suppressAutomaticEvents?: boolean
  }) {
    let srcValue = ''
    let currentTimeValue = 0

    const video = {
      preload: '',
      muted: false,
      playsInline: false,
      videoWidth: options.width ?? 0,
      videoHeight: options.height ?? 0,
      duration: options.duration ?? 0,
      onloadedmetadata: null as null | (() => void),
      onloadeddata: null as null | (() => void),
      onseeked: null as null | (() => void),
      onerror: null as null | (() => void),
      pause: vi.fn(),
      removeAttribute: vi.fn(),
      load: vi.fn(),
    }

    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      get: () => currentTimeValue,
      set: (value: number) => {
        currentTimeValue = value
        queueMicrotask(() => {
          video.onseeked?.()
        })
      },
    })

    Object.defineProperty(video, 'src', {
      configurable: true,
      get: () => srcValue,
      set: (value: string) => {
        srcValue = value
        queueMicrotask(() => {
          if (options.suppressAutomaticEvents) {
            return
          }

          if (options.emitError) {
            video.onerror?.()
            return
          }

          video.onloadedmetadata?.()

          if ((options.duration ?? 0) <= 0) {
            video.onloadeddata?.()
          }
        })
      },
    })

    return video as unknown as HTMLVideoElement
  }

  function installVideoElementFactory(videoElements: HTMLVideoElement[]) {
    const realCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      if (String(tagName).toLowerCase() === 'video') {
        const nextVideo = videoElements.shift()
        if (!nextVideo) {
          throw new Error('Missing fake video element')
        }
        return nextVideo as unknown as HTMLElement
      }

      return realCreateElement(tagName as keyof HTMLElementTagNameMap, options)
    }) as typeof document.createElement)
  }

  function installIndexedDbMock(options: {
    initialEntries?: Record<string, Blob>
    failOpen?: boolean
    failGet?: boolean
    failPut?: boolean
  } = {}) {
    const store = new Map(Object.entries(options.initialEntries ?? {}))
    let hasStore = false

    const db = {
      objectStoreNames: {
        contains: vi.fn(() => hasStore),
      },
      createObjectStore: vi.fn(() => {
        hasStore = true
      }),
      close: vi.fn(),
      onversionchange: null as null | (() => void),
      transaction: vi.fn((_storeName: string, mode: string) => {
        const tx = {
          objectStore: vi.fn(() => ({
            get: vi.fn((key: string) => {
              const request = {
                result: undefined as Blob | null | undefined,
                onsuccess: null as null | (() => void),
                onerror: null as null | (() => void),
              }

              queueMicrotask(() => {
                if (options.failGet) {
                  request.onerror?.()
                  return
                }

                request.result = store.get(key) ?? null
                request.onsuccess?.()
              })

              return request
            }),
            put: vi.fn((blob: Blob, key: string) => {
              if (!options.failPut) {
                store.set(key, blob)
              }
            }),
          })),
          oncomplete: null as null | (() => void),
          onerror: null as null | (() => void),
        }

        queueMicrotask(() => {
          if (mode === 'readwrite' && options.failPut) {
            tx.onerror?.()
            return
          }

          tx.oncomplete?.()
        })

        return tx
      }),
    } as unknown as IDBDatabase

    const openMock = vi.fn(() => {
      const request = {
        result: db,
        error: options.failOpen ? new Error('indexeddb open failed') : null,
        onupgradeneeded: null as null | ((event: Event) => void),
        onsuccess: null as null | (() => void),
        onerror: null as null | (() => void),
      }

      queueMicrotask(() => {
        if (options.failOpen) {
          request.onerror?.()
          return
        }

        request.onupgradeneeded?.({ target: { result: db } } as unknown as Event)
        request.onsuccess?.()
      })

      return request
    })

    vi.stubGlobal('indexedDB', {
      open: openMock,
    })

    return {
      db,
      store,
      openMock,
    }
  }

  function installImageConstructor(options: {
    width?: number
    height?: number
    emitError?: boolean
  } = {}) {
    class FakeImage {
      onload: null | (() => void) = null
      onerror: null | ((error: Error) => void) = null
      naturalWidth = options.width ?? 0
      naturalHeight = options.height ?? 0

      set src(_value: string) {
        queueMicrotask(() => {
          if (options.emitError) {
            this.onerror?.(new Error('image decode failed'))
            return
          }

          this.onload?.()
        })
      }
    }

    vi.stubGlobal('Image', FakeImage)
  }

  beforeEach(() => {
    mediaMocks.submitUpload.mockClear()
    mediaMocks.cancelUpload.mockClear()
    mediaMocks.getPendingForUser.mockReset()
    mediaMocks.buildOptimisticMessageFromUpload.mockClear()
    mediaMocks.waitForReady.mockReset()
    mediaMocks.waitForReady.mockResolvedValue(undefined)
    mediaMocks.uploadHandler = null
    mediaMocks.uploadUnsubscribe.mockReset()

    mediaMocks.cancelDocumentDownload.mockClear()
    mediaMocks.getCompletedDocumentDownloadUrl.mockReset()
    mediaMocks.getCompletedDocumentDownloadUrl.mockReturnValue('')
    mediaMocks.getPendingDocumentDownloadsForUser.mockReset()
    mediaMocks.getPendingDocumentDownloadsForUser.mockReturnValue([])
    mediaMocks.startDocumentDownload.mockClear()
    mediaMocks.documentHandler = null
    mediaMocks.documentUnsubscribe.mockReset()

    preprocessMocks.canUseImagePreprocessWorker.mockReset()
    preprocessMocks.canUseImagePreprocessWorker.mockReturnValue(false)
    preprocessMocks.getRecommendedImagePreprocessParallelism.mockReset()
    preprocessMocks.getRecommendedImagePreprocessParallelism.mockReturnValue(1)
    preprocessMocks.processImageInWorker.mockReset()
    preprocessMocks.primeMediaPreprocessTelemetry.mockClear()
    preprocessMocks.recordMediaPreprocessTelemetry.mockClear()
    heicMocks.convert.mockReset()
    heicMocks.convert.mockResolvedValue(new Blob(['converted-heic'], { type: 'image/jpeg' }))
    fileCacheMocks.ensureFileCached.mockReset()
    fileCacheMocks.ensureFileCached.mockResolvedValue(null)
    fileCacheMocks.getCachedFileObjectUrl.mockReset()
    fileCacheMocks.getCachedFileObjectUrl.mockResolvedValue(null)

    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:generated-media'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    })
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(0)
      return 1
    })
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
    if (originalCreateObjectURL) {
      Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: originalCreateObjectURL })
    }
    if (originalRevokeObjectURL) {
      Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: originalRevokeObjectURL })
    }
    if (originalCreateImageBitmap) {
      Object.defineProperty(globalThis, 'createImageBitmap', {
        configurable: true,
        writable: true,
        value: originalCreateImageBitmap,
      })
    } else {
      Reflect.deleteProperty(globalThis, 'createImageBitmap')
    }

    if (originalRequestAnimationFrame) {
      Object.defineProperty(globalThis, 'requestAnimationFrame', {
        configurable: true,
        writable: true,
        value: originalRequestAnimationFrame,
      })
    } else {
      Reflect.deleteProperty(globalThis, 'requestAnimationFrame')
    }

    if (originalCancelAnimationFrame) {
      Object.defineProperty(globalThis, 'cancelAnimationFrame', {
        configurable: true,
        writable: true,
        value: originalCancelAnimationFrame,
      })
    } else {
      Reflect.deleteProperty(globalThis, 'cancelAnimationFrame')
    }

    if (originalImage) {
      vi.stubGlobal('Image', originalImage)
    } else {
      Reflect.deleteProperty(globalThis, 'Image')
    }

    if (originalFileReader) {
      vi.stubGlobal('FileReader', originalFileReader)
    } else {
      Reflect.deleteProperty(globalThis, 'FileReader')
    }

    if (originalIndexedDB) {
      vi.stubGlobal('indexedDB', originalIndexedDB)
    } else {
      Reflect.deleteProperty(globalThis, 'indexedDB')
    }
  })

  it('adopts pending uploads and reflects upload service events into the visible message list', async () => {
    mediaMocks.getPendingForUser.mockReturnValue(
      [
        {
          id: -22,
          userId: -7,
          senderId: 5,
          msgType: 'image' as const,
          fileId: 'pending-file',
          createdAt: '2026-05-14T00:00:00Z',
        },
      ] as any
    )

    const existing = makeImageMessage(-11, {
      content: JSON.stringify({ placeholder: true }),
      is_sending: true,
      upload_handoff_pending: true,
    })
    const { wrapper } = mountHarness([existing])
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.messages.some((message: Message) => message.id === -22)).toBe(true)

    mediaMocks.uploadHandler?.({ type: 'progress', userId: -7, optimisticId: -11, progress: 45, uploadedBytes: 9, totalBytes: 20 })
    expect(vm.messages.find((message: Message) => message.id === -11)).toMatchObject({
      is_sending: true,
      upload_progress: 45,
      upload_loaded: 9,
      upload_total: 20,
      upload_handoff_pending: false,
    })

    mediaMocks.uploadHandler?.({ type: 'uploaded', userId: -7, optimisticId: -11, fileId: 'file-11', content: JSON.stringify({ file_id: 'file-11' }) })
    expect(JSON.parse(vm.messages.find((message: Message) => message.id === -11).content)).toEqual({ file_id: 'file-11' })

    mediaMocks.uploadHandler?.({
      type: 'sent',
      userId: -7,
      optimisticId: -11,
      localBlobUrl: 'blob:sent-image',
      serverMessage: makeImageMessage(111, { content: JSON.stringify({ file_id: 'file-sent' }), local_blob_url: undefined }),
    })
    expect(vm.messages.find((message: Message) => message.id === 111)?.local_blob_url).toBe('blob:sent-image')
    expect(vm.imageCache['file-sent']).toBe('blob:sent-image')

    mediaMocks.uploadHandler?.({ type: 'added', userId: -7, optimisticId: -33, message: makeImageMessage(-33) })
    expect(vm.messages.some((message: Message) => message.id === -33)).toBe(true)

    mediaMocks.uploadHandler?.({ type: 'error', userId: -7, optimisticId: -33, errorMessage: 'broken' })
    expect(vm.messages.find((message: Message) => message.id === -33)).toMatchObject({ is_error: true, is_sending: false })

    mediaMocks.uploadHandler?.({ type: 'cancelled', userId: -7, optimisticId: -33 })
    expect(vm.messages.some((message: Message) => message.id === -33)).toBe(false)
  })

  it('covers media helper branches for file normalization, metadata, capability, and payload parsing', async () => {
    const { wrapper } = mountHarness([
      makeImageMessage(1),
      makeImageMessage(2),
      makeImageMessage(3, { content: JSON.stringify({ file_id: 'file-3' }), is_error: true }),
    ])
    const vm = wrapper.vm as any
    const hooks = vm.__testHooks

    expect(hooks.formatFileSizeMb(1024 * 1024)).toBe('1.0MB')
    expect(hooks.buildUploadTooLargeMessage()).toContain('50MB')
    expect(hooks.buildUploadTooLargeMessage(52 * 1024 * 1024)).toContain('52.0MB')
    expect(hooks.isHeicLikeFile(new File(['x'], 'photo.HEIC', { type: '' }))).toBe(true)
    expect(hooks.isHeicLikeFile(new File(['x'], 'photo.jpg', { type: 'image/jpeg' }))).toBe(false)
    expect(hooks.buildConvertedImageName('IMG_1001.HEIC')).toBe('IMG_1001.jpg')
    expect(hooks.buildConvertedImageName('')).toMatch(/^image_\d+\.jpg$/)

    const editedFile = new File(['edited'], 'edited.png', { type: 'image/png' }) as File & { __chatEditedImage?: boolean }
    Object.defineProperty(editedFile, '__chatEditedImage', { value: true })
    expect(hooks.isEditedImageUploadFile(editedFile)).toBe(true)
    await expect(hooks.normalizeImageUploadFile(editedFile)).resolves.toBe(editedFile)
    const regularFile = new File(['regular'], 'regular.png', { type: 'image/png' })
    await expect(hooks.normalizeImageUploadFile(regularFile)).resolves.toBe(regularFile)
    const heicFile = new File(['heic'], 'ios.heic', { type: 'image/heic' })
    await expect(hooks.normalizeImageUploadFile(heicFile)).resolves.toMatchObject({
      name: 'ios.jpg',
      type: 'image/jpeg',
    })
    expect(heicMocks.convert).toHaveBeenCalledWith(expect.objectContaining({ blob: heicFile, toType: 'image/jpeg' }))

    expect(hooks.getScaledDimensions(4000, 2000, 1000)).toEqual({ width: 1000, height: 500 })
    expect(hooks.getScaledDimensions(500, 300, 1000)).toEqual({ width: 500, height: 300 })
    expect(hooks.appendAlbumMetadata({}, 'image', 'album-x', 4)).toEqual({ album_id: 'album-x', album_index: 4 })
    expect(hooks.appendAlbumMetadata({}, 'document', 'album-x', 4)).toEqual({})
    expect(hooks.appendCaptionMetadata({}, 'video', 'caption')).toEqual({ caption: 'caption' })
    expect(hooks.appendCaptionMetadata({}, 'voice', 'caption')).toEqual({})
    expect(hooks.getAlbumIdFromMessage(null)).toBeNull()
    expect(hooks.getAlbumIdFromMessage({ content: '{bad' })).toBeNull()
    expect(hooks.getAlbumIdFromMessage(makeImageMessage(1))).toBe('album-1')

    expect(hooks.getAdaptiveHydrationLimit({ tier: 'strong' })).toBe(3)
    expect(hooks.getAdaptiveHydrationLimit({ tier: 'mid' })).toBe(2)
    expect(hooks.getAdaptiveHydrationLimit({ tier: 'weak' })).toBe(1)
    expect(hooks.getAdaptivePreprocessLimit(10, 'image')).toBe(1)
    expect(hooks.getAdaptivePreprocessLimit(1, 'document')).toBe(3)
    expect(hooks.getAdaptiveUploadLimit(1, 'voice')).toBe(1)
    expect(hooks.getAdaptiveUploadLimit(2, 'image')).toBe(2)

    expect(hooks.buildMediaLoadKey('file-1', true)).toBe('file-1:network')
    expect(hooks.getFileId('')).toBe('')
    expect(hooks.getFileId('{bad')).toBe('')
    expect(hooks.getFileId(JSON.stringify({ file_id: 'abc' }))).toBe('abc')
    expect(hooks.getDocumentFileName(makeDocumentMessage(7, { content: '{}' }))).toBe('file_7')
    expect(hooks.parseMediaPayload('{bad')).toEqual({})
    expect(hooks.parseMediaPayload(JSON.stringify({ file_id: 'abc' }))).toEqual({ file_id: 'abc' })
    expect(hooks.buildAuthenticatedMediaUrl('')).toBe('')
    expect(hooks.buildAuthenticatedMediaUrl('abc')).toBe('https://coin.test/api/chat/files/abc?token=jwt')
    expect(hooks.getAlbumIndexFromMessage(makeImageMessage(1))).toBe(0)
    expect(hooks.getAlbumIndexFromMessage(makeImageMessage(4, { content: JSON.stringify({ file_id: 'file-4' }) }))).toBe(Number.MAX_SAFE_INTEGER)
    expect(hooks.getAlbumMessages(makeImageMessage(2)).map((message: Message) => message.id)).toEqual([1, 2])
  })

  it('derives strong client capability limits and uses the second createImageBitmap fallback', async () => {
    Object.defineProperty(navigator, 'hardwareConcurrency', {
      configurable: true,
      value: 10,
    })
    Object.defineProperty(navigator, 'deviceMemory', {
      configurable: true,
      value: 8,
    })
    Object.defineProperty(navigator, 'connection', {
      configurable: true,
      value: { effectiveType: '4g', saveData: false },
    })
    preprocessMocks.canUseImagePreprocessWorker.mockReturnValue(true)
    preprocessMocks.getRecommendedImagePreprocessParallelism.mockReturnValue(2)

    const capabilityHarness = mountHarness([])
    const capabilityHooks = (capabilityHarness.wrapper.vm as any).__testHooks
    expect(capabilityHooks.getMediaClientCapability()).toMatchObject({
      tier: 'strong',
      cpuCount: 10,
      deviceMemory: 8,
      effectiveType: '4g',
      saveData: false,
      hasWorkerPreprocess: true,
    })
    expect(capabilityHooks.getAdaptivePreprocessLimit(9, 'image')).toBe(2)
    expect(capabilityHooks.getAdaptivePreprocessLimit(4, 'image')).toBe(3)
    expect(capabilityHooks.getAdaptivePreprocessLimit(1, 'video')).toBe(2)
    expect(capabilityHooks.getAdaptivePreprocessLimit(1, 'document')).toBe(4)
    expect(capabilityHooks.getAdaptiveUploadLimit(9, 'image')).toBe(2)
    expect(capabilityHooks.getAdaptiveUploadLimit(3, 'image')).toBe(3)
    expect(capabilityHooks.getAdaptiveUploadLimit(1, 'document')).toBe(2)

    preprocessMocks.canUseImagePreprocessWorker.mockReturnValue(false)
    const bitmapClose = vi.fn()
    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      writable: true,
      value: vi.fn()
        .mockRejectedValueOnce(new Error('option unsupported'))
        .mockResolvedValueOnce({ width: 3000, height: 1500, close: bitmapClose }),
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D))
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function toBlob(callback: BlobCallback) {
      callback(new Blob(['fallback-bitmap'], { type: 'image/jpeg' }))
    })

    const uploadHarness = mountHarness([])
    const uploadVm = uploadHarness.wrapper.vm as any
    await uploadVm.handleMediaUploadWrapper(new File([new Blob(['raw'])], 'fallback.jpg', { type: 'image/jpeg' }))
    await flushPromises()

    expect(globalThis.createImageBitmap).toHaveBeenCalledTimes(2)
    expect(bitmapClose).toHaveBeenCalled()
    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      msgType: 'image',
      width: 1920,
      height: 960,
    }))
  })

  it('covers nativeImageCompress image fallback, null-blob rejection, and image decode failure', async () => {
    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      writable: true,
      value: vi.fn()
        .mockRejectedValueOnce(new Error('option unsupported'))
        .mockRejectedValueOnce(new Error('bitmap failed')),
    })
    installImageConstructor({ width: 2400, height: 1200 })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D))
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function toBlob(callback: BlobCallback) {
      callback(new Blob(['fallback-image'], { type: 'image/jpeg' }))
    })

    const { wrapper } = mountHarness([])
    const hooks = (wrapper.vm as any).__testHooks

    await expect(hooks.nativeImageCompress(new File(['img'], 'fallback.jpg', { type: 'image/jpeg' }), 1000, 0.7)).resolves.toMatchObject({
      width: 1000,
      height: 500,
    })

    ;(globalThis.createImageBitmap as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      width: 400,
      height: 200,
      close: vi.fn(),
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementationOnce(function toBlob(callback: BlobCallback) {
      callback(null)
    })
    await expect(hooks.nativeImageCompress(new File(['img'], 'null-blob.jpg', { type: 'image/jpeg' }))).rejects.toThrow('Canvas toBlob failed')

    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      writable: true,
      value: vi.fn()
        .mockRejectedValueOnce(new Error('option unsupported'))
        .mockRejectedValueOnce(new Error('bitmap failed')),
    })
    installImageConstructor({ emitError: true })
    await expect(hooks.nativeImageCompress(new File(['img'], 'broken.jpg', { type: 'image/jpeg' }))).rejects.toThrow('image decode failed')
  })

  it('covers scheduler, cache, cancellation, hydration, and lightbox helper branches', async () => {
    const { wrapper } = mountHarness([
      makeImageMessage(11, { local_blob_url: undefined }),
      makeImageMessage(12, { local_blob_url: undefined }),
      makeDocumentMessage(13, { is_downloading: true, download_progress: 44 }),
    ])
    const vm = wrapper.vm as any
    const hooks = vm.__testHooks

    const firstPreprocess = hooks.runAdaptivePreprocessTask(1, async () => 'first')
    const secondPreprocess = hooks.runAdaptivePreprocessTask(1, async () => 'second')
    await expect(firstPreprocess).resolves.toBe('first')
    await expect(secondPreprocess).resolves.toBe('second')

    const uploadAbortController = new AbortController()
    uploadAbortController.abort()
    await expect(hooks.runAdaptiveUploadTask(1, async () => 'never', uploadAbortController.signal)).rejects.toThrow('UploadCancelled')
    await expect(hooks.runAdaptiveUploadTask(1, async () => 'uploaded')).resolves.toBe('uploaded')

    let releaseQueuedUpload: (() => void) | null = null
    const firstUpload = hooks.runAdaptiveUploadTask(1, () => new Promise((resolve) => {
      releaseQueuedUpload = () => resolve('first-upload')
    }))
    const queuedAbortController = new AbortController()
    const queuedUpload = hooks.runAdaptiveUploadTask(1, async () => 'second-upload', queuedAbortController.signal)
    queuedAbortController.abort()
    await expect(queuedUpload).rejects.toThrow('UploadCancelled')
    if (!releaseQueuedUpload) {
      throw new Error('Expected queued upload release handler')
    }
    (releaseQueuedUpload as () => void)()
    await expect(firstUpload).resolves.toBe('first-upload')

    hooks.setCachedMediaUrl('cached-1', 'blob:first')
    hooks.setCachedMediaUrl('cached-1', 'blob:first')
    hooks.setCachedMediaUrl('cached-1', 'blob:second')
    expect(vm.imageCache['cached-1']).toBe('blob:second')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first')

    hooks.scheduleMediaHydration('', 'image', { allowNetwork: true })
    hooks.scheduleMediaHydration(JSON.stringify({ file_id: 'cached-1' }), 'image', { allowNetwork: true })
    hooks.scheduleMediaHydration(JSON.stringify({ file_id: 'queued-1' }), 'image', { allowNetwork: true })
    hooks.scheduleMediaHydration(JSON.stringify({ file_id: 'queued-1' }), 'image', { allowNetwork: true })
    expect(hooks.state.hydrationQueue.length).toBe(1)
    expect(hooks.state.pendingHydrationKeys.has('queued-1:network')).toBe(true)

    hooks.cancelDocumentDownload(13)
    expect(mediaMocks.cancelDocumentDownload).toHaveBeenCalledWith(13)
    expect(vm.messages.find((message: Message) => message.id === 13)).toMatchObject({ is_downloading: false, download_progress: 0 })
    hooks.cancelDocumentDownload(999)
    hooks.cancelMediaDownload(999)

    expect(await hooks.buildLightboxMediaItem({ ...vm.messages[0], content: '{}' })).toBeNull()
    const lightboxItem = await hooks.buildLightboxMediaItem(vm.messages[0])
    expect(lightboxItem).toMatchObject({ msgId: 11, fileId: 'file-11', url: 'https://coin.test/api/chat/files/file-11?token=jwt' })

    const abortedVideoController = new AbortController()
    abortedVideoController.abort()
    await expect(hooks.preprocessVideoPreview('blob:video', abortedVideoController.signal)).rejects.toThrow('UploadCancelled')
  })

  it('covers adaptive preprocess/upload limit helpers for large albums and document media', async () => {
    const hardwareDescriptor = Object.getOwnPropertyDescriptor(navigator, 'hardwareConcurrency')
    const memoryDescriptor = Object.getOwnPropertyDescriptor(navigator as object, 'deviceMemory')

    Object.defineProperty(navigator, 'hardwareConcurrency', {
      configurable: true,
      value: 16,
    })
    Object.defineProperty(navigator as object, 'deviceMemory', {
      configurable: true,
      value: 8,
    })

    preprocessMocks.getRecommendedImagePreprocessParallelism.mockReturnValueOnce(2)

    const { wrapper } = mountHarness([])
    const hooks = (wrapper.vm as any).__testHooks

    expect(hooks.getAdaptivePreprocessLimit(6, 'image')).toBeGreaterThanOrEqual(1)
    expect(hooks.getAdaptiveUploadLimit(6, 'image')).toBeGreaterThanOrEqual(1)
    expect(hooks.getAdaptiveUploadLimit(1, 'document')).toBeGreaterThanOrEqual(1)

    if (hardwareDescriptor) {
      Object.defineProperty(navigator, 'hardwareConcurrency', hardwareDescriptor)
    }
    if (memoryDescriptor) {
      Object.defineProperty(navigator as object, 'deviceMemory', memoryDescriptor)
    } else {
      Reflect.deleteProperty(navigator as object, 'deviceMemory')
    }
  })

  it('covers weak capability fallback, preprocess timeout, pending media-load reuse, and non-media clicks', async () => {
    vi.useFakeTimers()

    const originalRequestIdleCallback = (window as any).requestIdleCallback
    Object.defineProperty(window, 'requestIdleCallback', {
      configurable: true,
      writable: true,
      value: (callback: IdleRequestCallback) => {
        callback({ didTimeout: false, timeRemaining: () => 50 } as IdleDeadline)
        return 1
      },
    })
    Reflect.deleteProperty(globalThis, 'requestAnimationFrame')

    installIndexedDbMock({ failOpen: true })

    let resolveFetch: ((value: Response) => void) | null = null
    let fetchCallCount = 0
    const fetchMock = vi.fn(() => {
      fetchCallCount += 1
      if (fetchCallCount === 1) {
        return new Promise<Response>((resolve) => {
          resolveFetch = resolve
        })
      }

      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['hydrated-image'], { type: 'image/png' }),
      } as Response)
    })
    vi.stubGlobal('fetch', fetchMock)

    try {
      const { wrapper } = mountHarness([
        makeImageMessage(141, {
          local_blob_url: undefined,
          content: JSON.stringify({ file_id: 'shared-pending' }),
        }),
        {
          ...makeDocumentMessage(142),
          message_type: 'text',
          content: 'plain text',
        } as Message,
      ])
      const vm = wrapper.vm as any
      const hooks = vm.__testHooks

      vi.stubGlobal('navigator', undefined as any)
      expect(hooks.getMediaClientCapability()).toMatchObject({
        tier: 'weak',
        cpuCount: 1,
        deviceMemory: 0,
        effectiveType: '',
        saveData: false,
        hasWorkerPreprocess: false,
      })

      const timedOutPreprocess = hooks.runAdaptivePreprocessTask(1, () => new Promise(() => {}))
      const timedOutExpectation = expect(timedOutPreprocess).rejects.toThrow('Preprocessing step timed out')
      await vi.advanceTimersByTimeAsync(90_000)
      await timedOutExpectation

      const firstLoad = vm.loadImageForMessage(JSON.stringify({ file_id: 'shared-pending' }), 'image', {
        allowNetwork: true,
      })
      const secondLoad = vm.loadImageForMessage(JSON.stringify({ file_id: 'shared-pending' }), 'image', {
        allowNetwork: true,
      })
      await Promise.resolve()
      expect(hooks.state.pendingMediaLoads.size).toBe(1)
      await flushPromises()
      const sharedPendingFetchCount = fetchMock.mock.calls.reduce((count, call) => (
        count + (String((call as any[])[0]).includes('/api/chat/files/shared-pending?token=jwt') ? 1 : 0)
      ), 0)
      expect(sharedPendingFetchCount).toBe(1)

      if (!resolveFetch) {
        throw new Error('Expected pending media fetch resolver')
      }
      (resolveFetch as (v: Response) => void)(new Response(new Blob(['pending-image'], { type: 'image/png' }), {
        status: 200,
        headers: { 'Content-Type': 'image/png' },
      }))
      await expect(firstLoad).resolves.toBe('blob:generated-media')
      await expect(secondLoad).resolves.toBe('blob:generated-media')

      vm.scheduleMediaHydration(JSON.stringify({ file_id: 'hydrated-fallback' }), 'image', { allowNetwork: true })
      await vi.advanceTimersByTimeAsync(0)
      await flushPromises()
      expect(fetchMock).toHaveBeenCalledWith('https://coin.test/api/chat/files/hydrated-fallback?token=jwt')

      await vm.handleMediaClick(vm.messages[1])
      expect(vm.lightboxMedia).toBeNull()
    } finally {
      if (originalRequestIdleCallback) {
        Object.defineProperty(window, 'requestIdleCallback', {
          configurable: true,
          writable: true,
          value: originalRequestIdleCallback,
        })
      } else {
        Reflect.deleteProperty(window, 'requestIdleCallback')
      }
      vi.useRealTimers()
    }
  })

  it('swallows upload and document unsubscribe failures during unmount', async () => {
    mediaMocks.uploadUnsubscribe.mockImplementation(() => {
      throw new Error('upload unsubscribe failed')
    })
    mediaMocks.documentUnsubscribe.mockImplementation(() => {
      throw new Error('document unsubscribe failed')
    })

    const { wrapper } = mountHarness([makeDocumentMessage(199)])
    expect(() => wrapper.unmount()).not.toThrow()
  })

  it('falls back when video preview capture throws during seek scheduling or timing out cleanup', async () => {
    vi.useFakeTimers()

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const seekFailureVideo = createFakeVideoElement({ width: 800, height: 400, duration: 2 })
      Object.defineProperty(seekFailureVideo, 'currentTime', {
        configurable: true,
        get: () => 0,
        set: () => {
          throw new Error('seek failed')
        },
      })
      installVideoElementFactory([seekFailureVideo])
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
        drawImage: vi.fn(),
      } as unknown as CanvasRenderingContext2D))
      vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => {
        throw new Error('canvas encode failed')
      })

      const firstHarness = mountHarness([])
      const firstHooks = (firstHarness.wrapper.vm as any).__testHooks
      const seekFailurePromise = firstHooks.preprocessVideoPreview('blob:seek-fail')
      await vi.advanceTimersByTimeAsync(3000)
      await expect(seekFailurePromise).resolves.toEqual({
        thumbnailDataUrl: '',
        width: 800,
        height: 400,
      })

      const timeoutVideo = createFakeVideoElement({ suppressAutomaticEvents: true })
      ;(timeoutVideo.pause as ReturnType<typeof vi.fn>).mockImplementation(() => {
        throw new Error('pause failed')
      })
      installVideoElementFactory([timeoutVideo])

      const secondHarness = mountHarness([])
      const secondHooks = (secondHarness.wrapper.vm as any).__testHooks
      const timeoutPromise = secondHooks.preprocessVideoPreview('blob:timeout')
      await vi.advanceTimersByTimeAsync(3000)
      await expect(timeoutPromise).resolves.toEqual({
        thumbnailDataUrl: '',
        width: 0,
        height: 0,
      })
      expect(warnSpy).toHaveBeenCalledWith('Video preview preprocessing timed out after 3s.')
    } finally {
      warnSpy.mockRestore()
      vi.useRealTimers()
    }
  })

  it('handles upload sent events without local blobs and ignores malformed cache payloads', async () => {
    const existing = makeImageMessage(-12, {
      content: JSON.stringify({ placeholder: true }),
      is_sending: true,
    })
    const { wrapper } = mountHarness([existing])
    await flushPromises()
    const vm = wrapper.vm as any

    mediaMocks.uploadHandler?.({
      type: 'sent',
      userId: -7,
      optimisticId: -12,
      serverMessage: makeImageMessage(112, {
        content: 'not-json',
        local_blob_url: undefined,
      }),
    })

    expect(vm.messages.some((message: Message) => message.id === -12)).toBe(false)
    expect(vm.messages.find((message: Message) => message.id === 112)?.local_blob_url).toBeUndefined()

    mediaMocks.uploadHandler?.({
      type: 'sent',
      userId: -7,
      optimisticId: -99,
      serverMessage: makeImageMessage(113, {
        content: JSON.stringify({ file_id: 'late-file' }),
        local_blob_url: undefined,
      }),
    })

    expect(vm.messages.some((message: Message) => message.id === 113)).toBe(true)
    expect(vm.imageCache['late-file']).toBeUndefined()
  })

  it('adopts document download state, downloads completed files immediately, and starts background document downloads otherwise', async () => {
    mediaMocks.getPendingDocumentDownloadsForUser.mockReturnValue(
      [{ messageId: 20, progress: 40 }] as any
    )
    mediaMocks.getCompletedDocumentDownloadUrl.mockImplementation((fileId: string) => (fileId === 'doc-20' ? 'blob:ready-doc' : ''))

    const readyDoc = makeDocumentMessage(20)
    const queuedDoc = makeDocumentMessage(21)
    const { wrapper } = mountHarness([readyDoc, queuedDoc])
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.messages.find((message: Message) => message.id === 20)).toMatchObject({
      is_downloading: true,
      download_progress: 40,
      local_blob_url: 'blob:ready-doc',
    })

    await vm.downloadMedia(vm.messages.find((message: Message) => message.id === 20))
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled()
    expect(mediaMocks.startDocumentDownload).not.toHaveBeenCalled()

    await vm.downloadMedia(vm.messages.find((message: Message) => message.id === 21))
    expect(mediaMocks.startDocumentDownload).toHaveBeenCalledWith({
      messageId: 21,
      userId: -7,
      fileId: 'doc-21',
      fileName: 'doc-21.pdf',
      mimeType: 'application/pdf',
    })

    mediaMocks.documentHandler?.({ type: 'completed', userId: -7, messageId: 21, fileId: 'doc-21', objectUrl: 'blob:downloaded-doc', fileName: 'doc-21.pdf' })
    expect(vm.messages.find((message: Message) => message.id === 21)).toMatchObject({
      is_downloading: false,
      download_progress: 100,
      local_blob_url: 'blob:downloaded-doc',
    })

    mediaMocks.documentHandler?.({ type: 'cancelled', userId: -7, messageId: 21, fileId: 'doc-21' })
    expect(vm.messages.find((message: Message) => message.id === 21)).toMatchObject({
      is_downloading: false,
      download_progress: 0,
    })
  })

  it('skips document downloads with no resolvable target and aborts a previous media download when retried', async () => {
    const noTargetHarness = mountHarness([
      makeDocumentMessage(22, { sender_id: 5, receiver_id: 0 }),
    ], null)
    const noTargetVm = noTargetHarness.wrapper.vm as any
    await noTargetVm.downloadMedia(noTargetVm.messages[0])
    expect(mediaMocks.startDocumentDownload).not.toHaveBeenCalled()

    const imageMessage = makeImageMessage(23, { local_blob_url: undefined })
    const { wrapper } = mountHarness([imageMessage])
    const vm = wrapper.vm as any
    let firstSignal: AbortSignal | undefined
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (!firstSignal) {
        firstSignal = init?.signal ?? undefined
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            const abortError = new Error('aborted') as Error & { name: string }
            abortError.name = 'AbortError'
            reject(abortError)
          }, { once: true })
        })
      }

      return Promise.resolve(new Response(new Blob(['second-download'], { type: 'image/png' }), {
        status: 200,
        headers: { 'Content-Type': 'image/png' },
      }))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('indexeddb unavailable')
      },
    })

    const firstDownload = vm.downloadMedia(vm.messages[0])
    await nextTick()
    const secondDownload = vm.downloadMedia(vm.messages[0])
    await Promise.all([firstDownload, secondDownload])
    await flushPromises()

    expect(firstSignal?.aborted).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(vm.imageCache['file-23']).toBe('blob:generated-media')
  })

  it('applies document download progress/error events and ignores unrelated download events', async () => {
    const documentMessage = makeDocumentMessage(25)
    const imageMessage = makeImageMessage(26)
    const { wrapper } = mountHarness([documentMessage, imageMessage])
    await flushPromises()
    const vm = wrapper.vm as any

    mediaMocks.documentHandler?.({ type: 'progress', userId: -77, messageId: 25, fileId: 'doc-25', progress: 80 })
    expect(vm.messages.find((message: Message) => message.id === 25)?.is_downloading).toBeUndefined()

    mediaMocks.documentHandler?.({ type: 'added', userId: -7, messageId: 26, fileId: 'file-26', progress: 10 })
    expect(vm.messages.find((message: Message) => message.id === 26)?.is_downloading).toBeUndefined()

    mediaMocks.documentHandler?.({ type: 'progress', userId: -7, messageId: 25, fileId: 'doc-25', progress: 80 })
    expect(vm.messages.find((message: Message) => message.id === 25)).toMatchObject({
      is_downloading: true,
      download_progress: 80,
    })

    mediaMocks.documentHandler?.({ type: 'error', userId: -7, messageId: 25, fileId: 'doc-25' })
    expect(vm.messages.find((message: Message) => message.id === 25)).toMatchObject({
      is_downloading: false,
      download_progress: 0,
    })
  })

  it('opens album media in the lightbox and updates lightbox navigation state', async () => {
    const first = makeImageMessage(1)
    const second = makeImageMessage(2)
    const { wrapper } = mountHarness([first, second])
    const vm = wrapper.vm as any

    await vm.handleMediaClick(first)
    await nextTick()

    expect(vm.lightboxMedia).toMatchObject({
      currentIndex: 0,
      albumId: 'album-1',
    })
    expect(vm.lightboxMedia.items).toHaveLength(2)

    vm.setLightboxIndex(1)
    await nextTick()
    expect(vm.lightboxMedia.currentIndex).toBe(1)

    vm.closeLightbox()
    await nextTick()
    expect(vm.lightboxMedia).toBeNull()
  })

  it('hands document uploads off to the background upload service and marks the optimistic message as sending', async () => {
    const { wrapper, scrollToBottom } = mountHarness([])
    const vm = wrapper.vm as any
    const file = new File([new Blob(['document'])], 'report.pdf', { type: 'application/pdf' })

    await vm.handleMediaUploadWrapper(file, null, 0, 1, { sendAsDocument: true, roomKindOverride: 'channel' })
    await flushPromises()

    expect(scrollToBottom).toHaveBeenCalled()
    expect(mediaMocks.submitUpload).toHaveBeenCalledTimes(1)
    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'channel',
      msgType: 'document',
      fileName: 'report.pdf',
      mimeType: 'application/pdf',
      localBlobUrl: 'blob:generated-media',
    }))

    expect(vm.messages).toHaveLength(1)
    expect(vm.messages[0]).toMatchObject({
      message_type: 'document',
      is_sending: true,
      upload_handoff_pending: false,
      local_blob_url: 'blob:generated-media',
    })
    expect(JSON.parse(vm.messages[0].content)).toMatchObject({
      file_name: 'report.pdf',
      mime_type: 'application/pdf',
      size: file.size,
    })
    expect(vm.isUploading).toBe(false)
  })

  it('cancels optimistic uploads and document downloads while cleaning local message state', async () => {
    const sendingMessage = makeImageMessage(-51, { is_sending: true })
    const documentMessage = makeDocumentMessage(52, { is_downloading: true, download_progress: 67 })
    const { wrapper } = mountHarness([sendingMessage, documentMessage])
    const vm = wrapper.vm as any

    vm.cancelUpload(-51)
    expect(mediaMocks.cancelUpload).toHaveBeenCalledWith(-51)
    expect(vm.messages.some((message: Message) => message.id === -51)).toBe(false)

    vm.cancelDocumentDownload(52)
    expect(mediaMocks.cancelDocumentDownload).toHaveBeenCalledWith(52)
    expect(vm.messages.find((message: Message) => message.id === 52)).toMatchObject({
      is_downloading: false,
      download_progress: 0,
    })
  })

  it('aborts in-flight media downloads without surfacing an error and resets the visible progress', async () => {
    const imageMessage = makeImageMessage(61, { is_downloading: false, download_progress: 0, local_blob_url: undefined })
    const { wrapper } = mountHarness([imageMessage])
    const vm = wrapper.vm as any
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        const abortError = new Error('aborted') as Error & { name: string }
        abortError.name = 'AbortError'
        reject(abortError)
      })
    }))
    vi.stubGlobal('fetch', fetchMock)

    const downloadPromise = vm.downloadMedia(vm.messages[0])
    await nextTick()

    expect(vm.messages[0]).toMatchObject({
      is_downloading: true,
      download_progress: 0,
    })

    vm.cancelMediaDownload(61)
    await downloadPromise

    expect(vm.messages[0]).toMatchObject({
      is_downloading: false,
      download_progress: 0,
    })
    expect(window.alert).not.toHaveBeenCalled()
  })

  it('downloads image media into the cache and hydrates uncached images from the network when allowed', async () => {
    const imageMessage = makeImageMessage(71, { local_blob_url: undefined })
    const { wrapper } = mountHarness([imageMessage])
    const vm = wrapper.vm as any
    const networkBlob = new Blob(['network-image'], { type: 'image/png' })
    const fetchMock = vi.fn(async () => ({
      ok: true,
      headers: {
        get: (name: string) => (name.toLowerCase() === 'content-length' ? null : 'image/png'),
      },
      body: {} as ReadableStream<Uint8Array>,
      blob: async () => networkBlob,
    }))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('indexeddb unavailable')
      },
    })

    await vm.downloadMedia(vm.messages[0])
    expect(vm.imageCache['file-71']).toBe('blob:generated-media')

    vm.imageCache = {}
    const hydratedUrl = await vm.loadImageForMessage(imageMessage.content, 'image', { allowNetwork: true })

    expect(hydratedUrl).toBe('blob:generated-media')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(vm.imageCache['file-71']).toBe('blob:generated-media')
  })

  it('hydrates uncached media from the unified file cache before falling back to network', async () => {
    const imageMessage = makeImageMessage(73, { local_blob_url: undefined })
    const { wrapper } = mountHarness([imageMessage])
    const vm = wrapper.vm as any
    const fetchMock = vi.fn()

    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('indexeddb unavailable')
      },
    })
    fileCacheMocks.ensureFileCached.mockResolvedValueOnce({
      blob: new Blob(['cached-inline-media'], { type: 'image/png' }),
      fileName: 'file-73.png',
      mimeType: 'image/png',
      size: 19,
      cachedAt: Date.now(),
    })

    const hydratedUrl = await vm.loadImageForMessage(imageMessage.content, 'image', { allowNetwork: true })

    expect(hydratedUrl).toBe('blob:generated-media')
    expect(vm.imageCache['file-73']).toBe('blob:generated-media')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(fileCacheMocks.ensureFileCached).toHaveBeenCalledWith(
      'file-73',
      'file-73.jpg',
      { mimeType: undefined },
    )
  })

  it('tracks streaming media download progress before caching the combined blob', async () => {
    installIndexedDbMock()
    const imageMessage = makeImageMessage(72, { local_blob_url: undefined, download_progress: 0 })
    const { wrapper } = mountHarness([imageMessage])
    const vm = wrapper.vm as any
    const chunks = [new Uint8Array([1, 2]), new Uint8Array([3, 4, 5, 6])]
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({ done: false, value: chunks[0] })
        .mockResolvedValueOnce({ done: false, value: chunks[1] })
        .mockResolvedValueOnce({ done: true, value: undefined }),
    }
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      headers: {
        get: (name: string) => {
          const normalized = name.toLowerCase()
          if (normalized === 'content-length') return '6'
          if (normalized === 'content-type') return 'image/png'
          return null
        },
      },
      body: {
        getReader: () => reader,
      },
    })))

    await vm.downloadMedia(vm.messages[0])
    await flushPromises()

    expect(reader.read).toHaveBeenCalledTimes(3)
    expect(vm.messages[0]).toMatchObject({
      is_downloading: false,
      download_progress: 0,
    })
    expect(vm.imageCache['file-72']).toBe('blob:generated-media')
  })

  it('deduplicates scheduled image hydration work and skips network for images when network hydration is disallowed', async () => {
    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const hydrationBlob = new Blob(['hydrate'], { type: 'image/png' })
    const fetchMock = vi.fn(async () => ({
      ok: true,
      blob: async () => hydrationBlob,
    }))
    const originalRequestIdleCallback = (window as any).requestIdleCallback
    const originalRequestAnimationFrame = window.requestAnimationFrame

    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('indexeddb unavailable')
      },
    })
    Object.defineProperty(window, 'requestIdleCallback', {
      configurable: true,
      writable: true,
      value: (callback: IdleRequestCallback) => {
        callback({ didTimeout: false, timeRemaining: () => 50 } as IdleDeadline)
        return 1
      },
    })
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      writable: true,
      value: (callback: FrameRequestCallback) => {
        callback(0)
        return 1
      },
    })

    const content = JSON.stringify({ file_id: 'hydrate-99' })
    expect(await vm.loadImageForMessage(content, 'image', { allowNetwork: false })).toBeNull()

    vm.scheduleMediaHydration(content, 'image', { allowNetwork: true })
    vm.scheduleMediaHydration(content, 'image', { allowNetwork: true })
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vm.imageCache['hydrate-99']).toBe('blob:generated-media')

    if (originalRequestIdleCallback) {
      Object.defineProperty(window, 'requestIdleCallback', {
        configurable: true,
        writable: true,
        value: originalRequestIdleCallback,
      })
    } else {
      Reflect.deleteProperty(window, 'requestIdleCallback')
    }

    if (originalRequestAnimationFrame) {
      Object.defineProperty(window, 'requestAnimationFrame', {
        configurable: true,
        writable: true,
        value: originalRequestAnimationFrame,
      })
    }
  })

  it('falls back to authenticated media URLs in the lightbox when no local or cached image is available', async () => {
    const uncachedImage = makeImageMessage(81, {
      local_blob_url: undefined,
      content: JSON.stringify({ file_id: 'file-81' }),
    })
    const { wrapper } = mountHarness([uncachedImage])
    const vm = wrapper.vm as any
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('indexeddb unavailable')
      },
    })
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false })))

    await vm.handleMediaClick(uncachedImage)
    await nextTick()

    expect(vm.lightboxMedia).toMatchObject({
      currentIndex: 0,
      items: [
        expect.objectContaining({
          fileId: 'file-81',
          url: 'https://coin.test/api/chat/files/file-81?token=jwt',
        }),
      ],
    })
  })

  it('prefers unified file-cache object URLs in the lightbox before authenticated media fallbacks', async () => {
    const cachedImage = makeImageMessage(82, {
      local_blob_url: undefined,
      content: JSON.stringify({ file_id: 'file-82' }),
    })
    fileCacheMocks.getCachedFileObjectUrl.mockResolvedValueOnce('blob:file-handler-cache')

    const { wrapper } = mountHarness([cachedImage])
    const vm = wrapper.vm as any

    await vm.handleMediaClick(cachedImage)
    await nextTick()

    expect(fileCacheMocks.getCachedFileObjectUrl).toHaveBeenCalledWith('file-82')
    expect(vm.lightboxMedia).toMatchObject({
      currentIndex: 0,
      items: [
        expect.objectContaining({
          fileId: 'file-82',
          url: 'blob:file-handler-cache',
        }),
      ],
    })
  })

  it('hydrates cached media from IndexedDB, resets the cached handle on version changes, and saves downloaded blobs back into the store', async () => {
    const { db, store, openMock } = installIndexedDbMock({
      initialEntries: {
        'file-111': new Blob(['cached-image'], { type: 'image/png' }),
      },
    })
    const fetchBlob = new Blob(['downloaded-image'], { type: 'image/png' })
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      headers: {
        get: (name: string) => (name.toLowerCase() === 'content-length' ? null : 'image/png'),
      },
      body: null,
      blob: async () => fetchBlob,
    })))

    const cachedContent = JSON.stringify({ file_id: 'file-111' })
    const downloadMessage = makeImageMessage(112, {
      local_blob_url: undefined,
      content: JSON.stringify({ file_id: 'file-112' }),
    })
    const { wrapper } = mountHarness([downloadMessage])
    const vm = wrapper.vm as any

    const cachedUrl = await vm.loadImageForMessage(cachedContent, 'image', { allowNetwork: true })
    expect(cachedUrl).toBe('blob:generated-media')
    expect(openMock).toHaveBeenCalledTimes(1)
    expect((db.objectStoreNames.contains as any)).toHaveBeenCalledWith('images')
    expect((db.createObjectStore as any)).toHaveBeenCalledWith('images')

    db.onversionchange?.({ target: { result: db } } as any)
    expect((db.close as any)).toHaveBeenCalled()

    await vm.downloadMedia(vm.messages[0])
    await flushPromises()

    expect(openMock).toHaveBeenCalledTimes(2)
    expect(store.get('file-112')).toBe(fetchBlob)
    expect(vm.imageCache['file-112']).toBe('blob:generated-media')
  })

  it('gracefully falls back to the network when opening the IndexedDB cache fails', async () => {
    installIndexedDbMock({ failOpen: true })
    const fetchMock = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(['network-fallback'], { type: 'image/png' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any

    const objectUrl = await vm.loadImageForMessage(JSON.stringify({ file_id: 'file-open-error' }), 'image', {
      allowNetwork: true,
    })

    expect(objectUrl).toBe('blob:generated-media')
    expect(fetchMock).toHaveBeenCalledWith('https://coin.test/api/chat/files/file-open-error?token=jwt')
  })

  it('alerts when a non-document media download fails', async () => {
    const imageMessage = makeImageMessage(91, { local_blob_url: undefined })
    const { wrapper } = mountHarness([imageMessage])
    const vm = wrapper.vm as any
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, headers: { get: () => null } })))

    await vm.downloadMedia(vm.messages[0])

    expect(window.alert).toHaveBeenCalledWith('خطا در دانلود فایل')
    expect(vm.messages[0]).toMatchObject({
      is_downloading: false,
      download_progress: 0,
    })
  })

  it('waits for background readiness before adopting uploads and skips stale user switches', async () => {
    let resolveReady: ((value?: void) => void) | null = null
    mediaMocks.waitForReady.mockReturnValueOnce(new Promise<void>((resolve) => {
      resolveReady = resolve
    }))
    mediaMocks.getPendingForUser.mockImplementation((userId: number) => (
      userId === -7
        ? [{ id: -88, userId: -7, senderId: 5, msgType: 'image' as const, createdAt: '2026-05-14T00:00:00Z' } as any]
        : []
    ))

    const { wrapper } = mountHarness([], null)
    const vm = wrapper.vm as any

    vm.selectedUserId = -8
    const ready = resolveReady as ((value?: void) => void) | null
    ready?.()
    await flushPromises()

    expect(vm.messages.some((message: Message) => message.id === -88)).toBe(false)
  })

  it('resets stale document download state, hydrates completed urls, and derives download targets without an active selection', async () => {
    mediaMocks.getPendingDocumentDownloadsForUser.mockReturnValue([])
    mediaMocks.getCompletedDocumentDownloadUrl.mockImplementation((fileId: string) => (
      fileId === 'doc-301' ? 'blob:completed-doc' : ''
    ))

    const staleDoc = makeDocumentMessage(301, {
      is_downloading: true,
      download_progress: 33,
    })
    const { wrapper } = mountHarness([staleDoc], -7)
    await flushPromises()
    const vm = wrapper.vm as any

    expect(vm.messages[0]).toMatchObject({
      is_downloading: false,
      download_progress: 0,
      local_blob_url: 'blob:completed-doc',
    })

    const noSelectionWrapper = mountHarness([
      makeDocumentMessage(302, { sender_id: 9, receiver_id: 5 }),
      makeDocumentMessage(303, { sender_id: 5, receiver_id: 12 }),
    ], null)
    const noSelectionVm = noSelectionWrapper.wrapper.vm as any

    await noSelectionVm.downloadMedia(noSelectionVm.messages[0])
    await noSelectionVm.downloadMedia(noSelectionVm.messages[1])

    expect(mediaMocks.startDocumentDownload).toHaveBeenNthCalledWith(1, expect.objectContaining({
      messageId: 302,
      userId: 9,
    }))
    expect(mediaMocks.startDocumentDownload).toHaveBeenNthCalledWith(2, expect.objectContaining({
      messageId: 303,
      userId: 12,
    }))
  })

  it('ignores upload events for other users and blocks uploads without a target or when files exceed the size limit', async () => {
    const existing = makeImageMessage(-401, {
      content: JSON.stringify({ placeholder: true }),
      is_sending: true,
      upload_handoff_pending: true,
    })
    const { wrapper } = mountHarness([existing], -7)
    const vm = wrapper.vm as any

    mediaMocks.uploadHandler?.({ type: 'progress', userId: -70, optimisticId: -401, progress: 50, uploadedBytes: 5, totalBytes: 10 })
    expect(vm.messages[0].upload_progress).toBeUndefined()

    const noTargetHarness = mountHarness([], null)
    const noTargetVm = noTargetHarness.wrapper.vm as any
    const docFile = new File([new Blob(['document'])], 'report.pdf', { type: 'application/pdf' })

    await noTargetVm.handleMediaUploadWrapper(docFile, null, 0, 1, { sendAsDocument: true })
    expect(mediaMocks.submitUpload).not.toHaveBeenCalled()

    const tooLargeFile = new File([new Blob(['x'])], 'huge.pdf', { type: 'application/pdf' })
    Object.defineProperty(tooLargeFile, 'size', {
      configurable: true,
      value: 51 * 1024 * 1024,
    })

    await vm.handleMediaUploadWrapper(tooLargeFile, null, 0, 1, { sendAsDocument: true })

    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('50MB'))
    expect(mediaMocks.submitUpload).not.toHaveBeenCalled()
  })

  it('hands voice uploads to the background service without image preprocessing', async () => {
    const { wrapper, scrollToBottom } = mountHarness([])
    const vm = wrapper.vm as any
    const voiceFile = new File([new Blob(['voice'])], 'voice.ogg', { type: 'audio/ogg' })
    ;(voiceFile as File & { durationMs?: number }).durationMs = 1234

    await vm.handleMediaUploadWrapper(voiceFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(preprocessMocks.processImageInWorker).not.toHaveBeenCalled()
    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'direct',
      msgType: 'voice',
      file: voiceFile,
      fileName: 'voice.ogg',
      mimeType: 'audio/ogg',
      durationMs: 1234,
      thumbnail: '',
      width: 0,
      height: 0,
    }))
    expect(vm.messages[0]).toMatchObject({
      message_type: 'voice',
      is_sending: true,
      upload_handoff_pending: false,
    })
    expect(JSON.parse(vm.messages[0].content)).toMatchObject({
      placeholder: true,
      durationMs: 1234,
    })
    expect(scrollToBottom).toHaveBeenCalled()
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'voice',
      path: 'voice_passthrough',
      status: 'success',
    }))
  })

  it('preprocesses image uploads through the worker path before handing them off', async () => {
    preprocessMocks.canUseImagePreprocessWorker.mockReturnValue(true)
    const processedBlob = new Blob(['processed-image'], { type: 'image/jpeg' })
    preprocessMocks.processImageInWorker.mockResolvedValue({
      blob: processedBlob,
      width: 320,
      height: 160,
      thumbnailDataUrl: 'data:image/jpeg;base64,thumb',
    })

    const { wrapper, scrollToBottom } = mountHarness([])
    const vm = wrapper.vm as any
    const imageFile = new File([new Blob(['raw-image'])], 'photo.jpg', { type: 'image/jpeg' })

    await vm.handleMediaUploadWrapper(imageFile, 'album-1', 0, 2, {
      caption: 'caption text',
      roomKindOverride: 'group',
    })
    await flushPromises()

    expect(preprocessMocks.processImageInWorker).toHaveBeenCalledWith(imageFile, expect.any(AbortSignal))
    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'group',
      msgType: 'image',
      file: processedBlob,
      fileName: 'photo.jpg',
      mimeType: 'image/jpeg',
      thumbnail: 'data:image/jpeg;base64,thumb',
      width: 320,
      height: 160,
      caption: 'caption text',
      albumId: 'album-1',
      albumIndex: 0,
      albumSize: 2,
    }))
    expect(JSON.parse(vm.messages[0].content)).toMatchObject({
      thumbnail: 'data:image/jpeg;base64,thumb',
      width: 320,
      height: 160,
      caption: 'caption text',
      album_id: 'album-1',
      album_index: 0,
    })
    expect(vm.messages[0]).toMatchObject({
      message_type: 'image',
      is_sending: true,
      upload_handoff_pending: false,
    })
    expect(scrollToBottom).toHaveBeenCalled()
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'image',
      path: 'image_worker',
      status: 'success',
      usedWorker: true,
      width: 320,
      height: 160,
    }))
  })

  it('normalizes HEIC images to jpeg and preprocesses them on the main thread when the worker path is unavailable', async () => {
    stubCanvasImagePipeline(640, 320)

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const heicFile = new File([new Blob(['heic-image'])], 'camera.heic', { type: 'image/heic' })

    await vm.handleMediaUploadWrapper(heicFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(heicMocks.convert).toHaveBeenCalledWith(expect.objectContaining({
      blob: heicFile,
      toType: 'image/jpeg',
      quality: 0.9,
    }))
    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'direct',
      msgType: 'image',
      fileName: 'camera.jpg',
      mimeType: 'image/jpeg',
      width: 640,
      height: 320,
      thumbnail: expect.stringContaining('data:image/jpeg;base64,'),
    }))
    expect(JSON.parse(vm.messages[0].content)).toMatchObject({
      width: 640,
      height: 320,
      thumbnail: expect.stringContaining('data:image/jpeg;base64,'),
    })
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'image',
      path: 'image_main_thread',
      status: 'success',
      usedWorker: false,
      width: 640,
      height: 320,
    }))
  })

  it('marks HEIC uploads as failed when conversion returns no blob', async () => {
    heicMocks.convert.mockResolvedValueOnce([] as unknown as Blob)
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const heicFile = new File([new Blob(['heic-image'])], '', { type: 'image/heif' })

    await vm.handleMediaUploadWrapper(heicFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(mediaMocks.submitUpload).not.toHaveBeenCalled()
    expect(vm.error).toContain('تبدیل تصویر HEIC')
    expect(vm.messages[0]).toMatchObject({
      is_error: true,
      is_sending: false,
      upload_handoff_pending: false,
    })
    expect(errorSpy).toHaveBeenCalledWith('HEIC conversion failed:', expect.any(Error))

    errorSpy.mockRestore()
  })

  it('keeps edited image uploads as-is while still extracting dimensions and generating thumbnails', async () => {
    stubCanvasImagePipeline(512, 256)

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const editedFile = new File([new Blob(['edited-image'])], 'poster_edited.jpg', { type: 'image/jpeg' })

    await vm.handleMediaUploadWrapper(editedFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(heicMocks.convert).not.toHaveBeenCalled()
    expect(preprocessMocks.processImageInWorker).not.toHaveBeenCalled()
    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'direct',
      msgType: 'image',
      file: editedFile,
      fileName: 'poster_edited.jpg',
      mimeType: 'image/jpeg',
      width: 512,
      height: 256,
      thumbnail: expect.stringContaining('data:image/jpeg;base64,'),
    }))
    expect(JSON.parse(vm.messages[0].content)).toMatchObject({
      width: 512,
      height: 256,
      thumbnail: expect.stringContaining('data:image/jpeg;base64,'),
    })
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'image',
      path: 'image_edited_passthrough',
      status: 'success',
      usedWorker: false,
      width: 512,
      height: 256,
    }))
  })

  it('extracts video preview thumbnails and dimensions before handing uploads off to the background service', async () => {
    installVideoElementFactory([
      createFakeVideoElement({ width: 960, height: 540, duration: 2 }),
    ])
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D))
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/jpeg;base64,video-thumb')

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const videoFile = new File([new Blob(['video'])], 'clip.mp4', { type: 'video/mp4' })

    await vm.handleMediaUploadWrapper(videoFile, 'album-video', 0, 2, {
      caption: 'video caption',
      roomKindOverride: 'group',
    })
    await flushPromises()

    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'group',
      msgType: 'video',
      file: videoFile,
      fileName: 'clip.mp4',
      mimeType: 'video/mp4',
      thumbnail: 'data:image/jpeg;base64,video-thumb',
      width: 960,
      height: 540,
      caption: 'video caption',
      albumId: 'album-video',
      albumIndex: 0,
      albumSize: 2,
    }))
    expect(JSON.parse(vm.messages[0].content)).toMatchObject({
      thumbnail: 'data:image/jpeg;base64,video-thumb',
      width: 960,
      height: 540,
      placeholder: true,
      caption: 'video caption',
      album_id: 'album-video',
      album_index: 0,
    })
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'video',
      path: 'video_preview',
      status: 'success',
      width: 960,
      height: 540,
    }))
  })

  it('returns an empty video thumbnail when canvas context is unavailable during preview extraction', async () => {
    installVideoElementFactory([
      createFakeVideoElement({ width: 640, height: 360, duration: 0 }),
    ])
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)

    const { wrapper } = mountHarness([])
    const hooks = (wrapper.vm as any).__testHooks
    await expect(hooks.preprocessVideoPreview('blob:preview-no-context')).resolves.toEqual({
      thumbnailDataUrl: '',
      width: 640,
      height: 360,
    })
  })

  it('falls back to video metadata probing when preview extraction cannot determine final dimensions', async () => {
    installVideoElementFactory([
      createFakeVideoElement({ emitError: true }),
      createFakeVideoElement({ width: 1280, height: 720, duration: 0 }),
    ])

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const videoFile = new File([new Blob(['video'])], 'fallback.mp4', { type: 'video/mp4' })

    await vm.handleMediaUploadWrapper(videoFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'direct',
      msgType: 'video',
      thumbnail: '',
      width: 1280,
      height: 720,
    }))
    expect(JSON.parse(vm.messages[0].content)).toMatchObject({
      thumbnail: '',
      width: 1280,
      height: 720,
      placeholder: true,
    })
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'video',
      path: 'video_metadata_fallback',
      status: 'success',
      fallbackReason: 'missing_preview_dimensions',
      width: 1280,
      height: 720,
    }))
  })

  it('cancels video uploads while preview preprocessing is still pending', async () => {
    installVideoElementFactory([
      createFakeVideoElement({ suppressAutomaticEvents: true }),
    ])

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const videoFile = new File([new Blob(['video'])], 'pending-preview.mp4', { type: 'video/mp4' })

    const uploadPromise = vm.handleMediaUploadWrapper(videoFile, null, 0, 1, { roomKindOverride: 'direct' })
    await nextTick()

    expect(vm.messages).toHaveLength(1)
    const optimisticId = vm.messages[0].id
    vm.cancelUpload(optimisticId)

    await uploadPromise
    await flushPromises()

    expect(mediaMocks.submitUpload).not.toHaveBeenCalled()
    expect(vm.messages.some((message: Message) => message.id === optimisticId)).toBe(false)
    expect(vm.isUploading).toBe(false)
  })

  it('falls back to Image decoding when createImageBitmap cannot decode the upload source', async () => {
    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      writable: true,
      value: vi.fn(async () => {
        throw new Error('bitmap decode failed')
      }),
    })
    installImageConstructor({ width: 480, height: 320 })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D))
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function toBlob(callback: BlobCallback) {
      callback(new Blob(['canvas-image'], { type: 'image/jpeg' }))
    })

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const imageFile = new File([new Blob(['fallback-image'])], 'fallback.jpg', { type: 'image/jpeg' })

    await vm.handleMediaUploadWrapper(imageFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'direct',
      msgType: 'image',
      width: 480,
      height: 320,
      thumbnail: expect.stringContaining('data:image/jpeg;base64,'),
    }))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:generated-media')
  })

  it('falls back to the legacy image compression path when worker preprocessing fails', async () => {
    preprocessMocks.canUseImagePreprocessWorker.mockReturnValue(true)
    preprocessMocks.processImageInWorker.mockRejectedValue(new Error('worker failed'))
    stubCanvasImagePipeline(720, 360)

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const imageFile = new File([new Blob(['raw-image'])], 'legacy.jpg', { type: 'image/jpeg' })

    await vm.handleMediaUploadWrapper(imageFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(mediaMocks.submitUpload).toHaveBeenCalledWith(expect.objectContaining({
      userId: -7,
      roomKind: 'direct',
      msgType: 'image',
      width: 720,
      height: 360,
      thumbnail: expect.stringContaining('data:image/jpeg;base64,'),
    }))
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'image',
      path: 'image_worker',
      status: 'failed',
      fallbackReason: 'worker_failed',
    }))
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'image',
      path: 'image_legacy_fallback',
      status: 'success',
      fallbackReason: 'worker_failed',
      width: 720,
      height: 360,
    }))
    expect(warnSpy).toHaveBeenCalledWith('Image preprocessing fast path failed, using legacy fallback:', expect.any(Error))

    warnSpy.mockRestore()
  })

  it('marks image uploads as failed when the generated caption payload exceeds the message limit', async () => {
    stubCanvasImagePipeline(640, 320)

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const imageFile = new File([new Blob(['caption-image'])], 'caption.jpg', { type: 'image/jpeg' })

    await vm.handleMediaUploadWrapper(imageFile, null, 0, 1, {
      caption: 'x'.repeat(10_000),
      roomKindOverride: 'direct',
    })
    await flushPromises()

    expect(mediaMocks.submitUpload).not.toHaveBeenCalled()
    expect(vm.error).toContain('متن کپشن برای این رسانه بیش از حد طولانی است.')
    expect(vm.messages[0]).toMatchObject({
      is_error: true,
      is_sending: false,
      upload_handoff_pending: false,
    })
  })

  it('surfaces background upload handoff failures on passthrough voice uploads', async () => {
    mediaMocks.submitUpload.mockRejectedValueOnce(new Error('service unavailable'))

    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const voiceFile = new File([new Blob(['voice'])], 'handoff.ogg', { type: 'audio/ogg' })

    await vm.handleMediaUploadWrapper(voiceFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(vm.error).toContain('service unavailable')
    expect(vm.messages[0]).toMatchObject({
      message_type: 'voice',
      is_error: true,
      is_sending: false,
      upload_handoff_pending: false,
    })
    expect(errorSpy).toHaveBeenCalledWith('Upload error at step [submit_to_service]:', expect.any(Error))

    errorSpy.mockRestore()
  })

  it('uses thumbnail-only fallbacks in the lightbox and ignores media with no resolvable source', async () => {
    const thumbOnlyMessage = makeImageMessage(201, {
      local_blob_url: undefined,
      content: JSON.stringify({ thumbnail: 'data:image/jpeg;base64,thumb-only' }),
    })
    const { wrapper } = mountHarness([thumbOnlyMessage])
    const vm = wrapper.vm as any

    await vm.handleMediaClick(thumbOnlyMessage)
    await nextTick()

    expect(vm.lightboxMedia).toMatchObject({
      currentIndex: 0,
      items: [
        expect.objectContaining({
          msgId: 201,
          url: 'data:image/jpeg;base64,thumb-only',
          thumbnail: 'data:image/jpeg;base64,thumb-only',
        }),
      ],
    })

    vm.closeLightbox()
    await nextTick()

    const noSourceMessage = makeImageMessage(202, {
      local_blob_url: undefined,
      content: '{}',
    })
    vm.messages = [noSourceMessage]

    await vm.handleMediaClick(noSourceMessage)
    await nextTick()

    expect(vm.lightboxMedia).toBeNull()
  })

  it('marks edited image uploads as failed when FileReader cannot serialize the generated thumbnail', async () => {
    stubCanvasImagePipeline(512, 256)

    class BrokenFileReader {
      result: string | ArrayBuffer | null = null
      onloadend: null | (() => void) = null
      onerror: null | ((event: ProgressEvent<FileReader>) => void) = null

      readAsDataURL(_blob: Blob) {
        this.onerror?.(new ProgressEvent('error') as ProgressEvent<FileReader>)
      }
    }

    vi.stubGlobal('FileReader', BrokenFileReader as unknown as typeof FileReader)

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const editedFile = new File([new Blob(['edited-image'])], 'broken_edited.jpg', { type: 'image/jpeg' })

    await vm.handleMediaUploadWrapper(editedFile, null, 0, 1, { roomKindOverride: 'direct' })
    await flushPromises()

    expect(mediaMocks.submitUpload).not.toHaveBeenCalled()
    expect(vm.messages[0]).toMatchObject({
      is_error: true,
      is_sending: false,
      upload_handoff_pending: false,
    })
    expect(preprocessMocks.recordMediaPreprocessTelemetry).toHaveBeenCalledWith(expect.objectContaining({
      mediaType: 'image',
      path: 'image_edited_passthrough',
      status: 'failed',
    }))
  })

  it('cancels image uploads before background handoff and removes the optimistic message', async () => {
    preprocessMocks.canUseImagePreprocessWorker.mockReturnValue(true)
    preprocessMocks.processImageInWorker.mockImplementation((_file: File, signal?: AbortSignal) => new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new Error('worker aborted')), { once: true })
    }))

    const { wrapper } = mountHarness([])
    const vm = wrapper.vm as any
    const imageFile = new File([new Blob(['raw-image'])], 'cancel-me.jpg', { type: 'image/jpeg' })

    const uploadPromise = vm.handleMediaUploadWrapper(imageFile)
    await nextTick()

    expect(vm.messages).toHaveLength(1)
    const optimisticId = vm.messages[0].id
    vm.cancelUpload(optimisticId)

    await uploadPromise
    await flushPromises()

    expect(mediaMocks.submitUpload).not.toHaveBeenCalled()
    expect(preprocessMocks.processImageInWorker).not.toHaveBeenCalled()
    expect(vm.messages.some((message: Message) => message.id === optimisticId)).toBe(false)
    expect(vm.isUploading).toBe(false)
  })
})
