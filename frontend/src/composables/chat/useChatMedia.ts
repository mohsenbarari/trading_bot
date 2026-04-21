import { ref, type Ref, nextTick } from 'vue'
import type { Message } from '../../types/chat'
import { canUseImagePreprocessWorker, getRecommendedImagePreprocessParallelism, processImageInWorker } from '../../utils/imagePreprocessClient'
import { primeMediaPreprocessTelemetry, recordMediaPreprocessTelemetry } from '../../utils/chatMediaTelemetry'

const CHAT_MEDIA_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
const CHAT_MEDIA_MAX_UPLOAD_LABEL = '50MB'
const HEIC_MIME_TYPES = new Set([
    'image/heic',
    'image/heic-sequence',
    'image/heif',
    'image/heif-sequence',
])

let optimisticUploadSequence = 0

function formatFileSizeMb(bytes: number) {
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

function buildUploadTooLargeMessage(bytes?: number) {
    const actualSize = typeof bytes === 'number' && Number.isFinite(bytes) && bytes > 0
        ? ` (حجم فایل: ${formatFileSizeMb(bytes)})`
        : ''

    return `حجم فایل از حد مجاز ${CHAT_MEDIA_MAX_UPLOAD_LABEL} بیشتر است${actualSize}.`
}

function isHeicLikeFile(file: File) {
    const mimeType = (file.type || '').toLowerCase()
    const fileName = (file.name || '').toLowerCase()

    return HEIC_MIME_TYPES.has(mimeType) || fileName.endsWith('.heic') || fileName.endsWith('.heif')
}

function buildConvertedImageName(fileName: string) {
    if (!fileName) {
        return `image_${Date.now()}.jpg`
    }

    return fileName.replace(/\.(heic|heif)$/i, '.jpg')
}

async function normalizeImageUploadFile(file: File): Promise<File> {
    if (!isHeicLikeFile(file)) {
        return file
    }

    try {
        const { default: heic2any } = await import('heic2any')
        const converted = await heic2any({
            blob: file,
            toType: 'image/jpeg',
            quality: 0.9,
        })
        const convertedBlob = Array.isArray(converted) ? converted[0] : converted

        if (!(convertedBlob instanceof Blob)) {
            throw new Error('HEIC conversion returned no blob')
        }

        return new File([convertedBlob], buildConvertedImageName(file.name), {
            type: 'image/jpeg',
            lastModified: file.lastModified || Date.now(),
        })
    } catch (error) {
        console.error('HEIC conversion failed:', error)
        throw new Error('تبدیل تصویر HEIC در این دستگاه ناموفق بود. لطفاً فایل را به JPG یا PNG تبدیل کنید.')
    }
}

function createOptimisticUploadId() {
    optimisticUploadSequence = (optimisticUploadSequence + 1) % 1000
    return -((Date.now() * 1000) + optimisticUploadSequence)
}

/**
 * Native, foolproof image compressor that relies on modern browser engines
 * to automatically handle EXIF orientation without double-rotating.
 * Uses createImageBitmap (with correct imageOrientation property) as primary path,
 * and falls back to <img> element drawing for browsers that don't support the option.
 */
const nativeImageCompress = (file: File | Blob, maxWidthOrHeight: number = 1920, quality: number = 0.8): Promise<{ blob: Blob, width: number, height: number }> => {
  return new Promise(async (resolve, reject) => {
    try {
      let bitmap: ImageBitmap | null = null;
      try {
        // imageOrientation (NOT 'orientation') is the correct property name
        bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      } catch {
        // Fallback: some browsers don't support the imageOrientation option
        try {
          bitmap = await createImageBitmap(file);
        } catch {
          bitmap = null;
        }
      }

      let width: number;
      let height: number;
      let drawSource: CanvasImageSource;

      if (bitmap) {
        width = bitmap.width;
        height = bitmap.height;
        drawSource = bitmap;
      } else {
        // Final fallback: use <img> which auto-applies EXIF in all modern browsers
        const img = await new Promise<HTMLImageElement>((res, rej) => {
          const el = new Image();
          el.onload = () => res(el);
          el.onerror = rej;
          el.src = URL.createObjectURL(file);
        });
        width = img.naturalWidth;
        height = img.naturalHeight;
        drawSource = img;
      }

      if (width > maxWidthOrHeight || height > maxWidthOrHeight) {
        const ratio = Math.min(maxWidthOrHeight / width, maxWidthOrHeight / height);
        width = Math.round(width * ratio);
        height = Math.round(height * ratio);
      }

      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) return reject(new Error('No canvas context'));

      ctx.drawImage(drawSource, 0, 0, width, height);
      if (bitmap) bitmap.close();

      canvas.toBlob((blob) => {
        if (blob) resolve({ blob, width, height });
        else reject(new Error('Canvas toBlob failed'));
      }, 'image/jpeg', quality);
    } catch (e) {
      reject(e);
    }
  });
};

type DecodedImageSource = {
    source: CanvasImageSource
    width: number
    height: number
    cleanup: () => void
}

function getScaledDimensions(width: number, height: number, maxWidthOrHeight: number) {
    let nextWidth = width
    let nextHeight = height

    if (nextWidth > maxWidthOrHeight || nextHeight > maxWidthOrHeight) {
        const ratio = Math.min(maxWidthOrHeight / nextWidth, maxWidthOrHeight / nextHeight)
        nextWidth = Math.max(1, Math.round(nextWidth * ratio))
        nextHeight = Math.max(1, Math.round(nextHeight * ratio))
    }

    return { width: nextWidth, height: nextHeight }
}

async function decodeImageSource(file: File | Blob): Promise<DecodedImageSource> {
    try {
        let bitmap: ImageBitmap | null = null
        try {
            bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
        } catch {
            bitmap = await createImageBitmap(file)
        }

        return {
            source: bitmap,
            width: bitmap.width,
            height: bitmap.height,
            cleanup: () => bitmap?.close(),
        }
    } catch {
        return await new Promise((resolve, reject) => {
            const img = new Image()
            const objectUrl = URL.createObjectURL(file)

            img.onload = () => {
                resolve({
                    source: img,
                    width: img.naturalWidth,
                    height: img.naturalHeight,
                    cleanup: () => {
                        URL.revokeObjectURL(objectUrl)
                        img.onload = null
                        img.onerror = null
                    }
                })
            }

            img.onerror = (error) => {
                URL.revokeObjectURL(objectUrl)
                reject(error)
            }

            img.src = objectUrl
        })
    }
}

function htmlCanvasToBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
    return new Promise((resolve, reject) => {
        canvas.toBlob((blob) => {
            if (blob) {
                resolve(blob)
                return
            }

            reject(new Error('Canvas toBlob failed'))
        }, 'image/jpeg', quality)
    })
}

async function preprocessImageOnMainThread(
    file: File | Blob,
    maxWidthOrHeight = 1920,
    quality = 0.85,
    thumbnailMaxWidthOrHeight = 20,
    thumbnailQuality = 0.5,
) {
    const decoded = await decodeImageSource(file)

    try {
        const scaled = getScaledDimensions(decoded.width, decoded.height, maxWidthOrHeight)
        const canvas = document.createElement('canvas')
        canvas.width = scaled.width
        canvas.height = scaled.height

        const context = canvas.getContext('2d')
        if (!context) {
            throw new Error('No canvas context')
        }

        context.drawImage(decoded.source, 0, 0, scaled.width, scaled.height)
        const blob = await htmlCanvasToBlob(canvas, quality)

        const thumbnailScaled = getScaledDimensions(scaled.width, scaled.height, thumbnailMaxWidthOrHeight)
        const thumbnailCanvas = document.createElement('canvas')
        thumbnailCanvas.width = thumbnailScaled.width
        thumbnailCanvas.height = thumbnailScaled.height

        const thumbnailContext = thumbnailCanvas.getContext('2d')
        if (!thumbnailContext) {
            throw new Error('No thumbnail canvas context')
        }

        thumbnailContext.drawImage(canvas, 0, 0, thumbnailScaled.width, thumbnailScaled.height)
        const thumbnailBlob = await htmlCanvasToBlob(thumbnailCanvas, thumbnailQuality)

        return {
            blob,
            width: scaled.width,
            height: scaled.height,
            thumbnailDataUrl: await blobToDataUrl(thumbnailBlob),
        }
    } finally {
        decoded.cleanup()
    }
}

const waitForNextPaint = (): Promise<void> => {
    return new Promise((resolve) => {
        if (typeof requestAnimationFrame !== 'function') {
            setTimeout(() => resolve(), 0)
            return
        }

        requestAnimationFrame(() => {
            requestAnimationFrame(() => resolve())
        })
    })
}

const scheduleIdleTask = (task: () => void) => {
    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
        ;(window as Window & {
            requestIdleCallback: (callback: IdleRequestCallback, options?: IdleRequestOptions) => number
        }).requestIdleCallback(() => task(), { timeout: 500 })
        return
    }

    setTimeout(task, 16)
}

export interface UseChatMediaOptions {
    apiBaseUrl: string
    jwtToken: string | null
    currentUserId: number
    selectedUserId: Ref<number | null>
    messages: Ref<Message[]>
    error: Ref<string>
    isUploading: Ref<boolean>
    scrollToBottom: () => void
    sendMediaMessage: (type: 'image' | 'video' | 'voice' | 'sticker', content: string, localBlobUrl?: string, optimisticId?: number) => Promise<Message | null>
}

type LightboxMediaItem = {
    msgId: number
    fileId: string
    type: 'image' | 'video'
    url: string
    thumbnail: string
}

type LightboxState = {
    items: LightboxMediaItem[]
    currentIndex: number
    albumId: string | null
}

type AlbumBatchItemStatus = 'uploading' | 'uploaded' | 'sending' | 'sent' | 'cancelled' | 'failed'

type PendingAlbumItem = {
    optimisticId: number
    albumIndex: number
    type: 'image' | 'video'
    content?: string
    localBlobUrl?: string
    status: AlbumBatchItemStatus
}

type PendingAlbumBatch = {
    expectedCount: number
    items: Map<number, PendingAlbumItem>
    flushPromise: Promise<void> | null
}

type VideoPreviewResult = {
    thumbnailDataUrl: string
    width: number
    height: number
}

type PreprocessJob = {
    limit: number
    run: () => Promise<unknown>
    resolve: (value: unknown) => void
    reject: (reason?: unknown) => void
}

function blobToDataUrl(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onloadend = () => resolve(reader.result as string)
        reader.onerror = (event) => reject(event)
        reader.readAsDataURL(blob)
    })
}

async function generateImageThumbnailDataUrl(file: Blob): Promise<string> {
    const thumb = await nativeImageCompress(file, 20, 0.5)
    return await blobToDataUrl(thumb.blob)
}

export function useChatMedia(options: UseChatMediaOptions) {
    const {
        apiBaseUrl,
        jwtToken,
        currentUserId,
        selectedUserId,
        messages,
        error,
        isUploading,
        scrollToBottom,
        sendMediaMessage
    } = options

    let activeUploadsCount = 0
    let activePreprocessCount = 0
    const uploadControllers = new Map<number, { abort: () => void }>()
    const albumBatches = new Map<string, PendingAlbumBatch>()
    const preprocessQueue: PreprocessJob[] = []
    const pendingMediaLoads = new Map<string, Promise<string | null>>()
    const pendingHydrationIds = new Set<string>()
    const hydrationQueue: Array<{ fileId: string; content: string; type?: string }> = []
    let activeHydrationCount = 0
    let hydrationPumpScheduled = false
    let imageDbPromise: Promise<IDBDatabase> | null = null

    const MAX_CONCURRENT_HYDRATIONS = 1

    primeMediaPreprocessTelemetry()

    function getAdaptivePreprocessLimit(albumSize: number, mediaType: 'image' | 'video' | 'voice') {
        if (typeof navigator === 'undefined') return 1

        const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection
        const saveData = Boolean(connection?.saveData)
        const effectiveType = connection?.effectiveType || ''
        const cpuCount = navigator.hardwareConcurrency || 4
        const deviceMemory = typeof (navigator as Navigator & { deviceMemory?: number }).deviceMemory === 'number'
            ? (navigator as Navigator & { deviceMemory?: number }).deviceMemory || 0
            : 0

        if (mediaType === 'voice') return 3
        if (saveData || effectiveType === 'slow-2g' || effectiveType === '2g') return 1
        if (deviceMemory > 0 && deviceMemory <= 2) return 1
        if (cpuCount <= 4) return 1

        const recommended = getRecommendedImagePreprocessParallelism()
        if (albumSize >= 5) return Math.max(1, Math.min(recommended, 2))
        if (mediaType === 'video') return 1
        return Math.max(1, Math.min(recommended + 1, 3))
    }

    function launchPreprocessJob(job: PreprocessJob) {
        activePreprocessCount += 1
        void job.run()
            .then((value) => job.resolve(value))
            .catch((reason) => job.reject(reason))
            .finally(() => {
                activePreprocessCount = Math.max(0, activePreprocessCount - 1)
                pumpPreprocessQueue()
            })
    }

    function pumpPreprocessQueue() {
        while (preprocessQueue.length > 0) {
            const nextIndex = preprocessQueue.findIndex(job => activePreprocessCount < job.limit)
            if (nextIndex === -1) {
                return
            }

            const [nextJob] = preprocessQueue.splice(nextIndex, 1)
            if (!nextJob) {
                return
            }

            launchPreprocessJob(nextJob)
        }
    }

    function runAdaptivePreprocessTask<T>(
        limit: number,
        run: () => Promise<T>
    ): Promise<T> {
        return new Promise<T>((resolve, reject) => {
            const normalizedLimit = Math.max(1, limit)
            const job: PreprocessJob = {
                limit: normalizedLimit,
                run: () => run() as Promise<unknown>,
                resolve: (value) => resolve(value as T),
                reject,
            }

            if (activePreprocessCount < normalizedLimit) {
                launchPreprocessJob(job)
                return
            }

            preprocessQueue.push(job)
        })
    }

    function trackPreprocessEvent(payload: {
        mediaType: 'image' | 'video' | 'voice'
        path: 'image_worker' | 'image_main_thread' | 'image_legacy_fallback' | 'video_preview' | 'video_metadata_fallback' | 'voice_passthrough'
        status: 'success' | 'failed' | 'cancelled'
        startedAt: number
        batchSize: number
        schedulerLimit: number
        usedWorker: boolean
        fallbackReason?: string
        width?: number
        height?: number
        errorMessage?: string
    }) {
        recordMediaPreprocessTelemetry({
            userId: currentUserId,
            mediaType: payload.mediaType,
            path: payload.path,
            status: payload.status,
            durationMs: Math.max(0, Math.round(performance.now() - payload.startedAt)),
            batchSize: payload.batchSize,
            schedulerLimit: payload.schedulerLimit,
            usedWorker: payload.usedWorker,
            fallbackReason: payload.fallbackReason,
            width: payload.width,
            height: payload.height,
            errorMessage: payload.errorMessage,
            timestamp: new Date().toISOString(),
        })
    }

    function appendAlbumMetadata(
        content: Record<string, unknown>,
        msgType: 'image' | 'video' | 'voice',
        albumId?: string | null,
        albumIndex?: number
    ) {
        if ((msgType === 'image' || msgType === 'video') && albumId) {
            content.album_id = albumId
            content.album_index = typeof albumIndex === 'number' ? albumIndex : 0
        }
        return content
    }

    function getAlbumIdFromMessage(msg?: Message | null) {
        if (!msg?.content) return null

        try {
            const parsed = JSON.parse(msg.content)
            return typeof parsed.album_id === 'string' && parsed.album_id.trim()
                ? parsed.album_id.trim()
                : null
        } catch {
            return null
        }
    }

    function ensureAlbumBatch(albumId: string, expectedCount: number) {
        const existing = albumBatches.get(albumId)
        if (existing) {
            existing.expectedCount = Math.max(existing.expectedCount, expectedCount)
            return existing
        }

        const created: PendingAlbumBatch = {
            expectedCount,
            items: new Map(),
            flushPromise: null
        }
        albumBatches.set(albumId, created)
        return created
    }

    function updateAlbumBatchItem(albumId: string, optimisticId: number, updates: Partial<PendingAlbumItem>) {
        const batch = albumBatches.get(albumId)
        if (!batch) return

        const existing = batch.items.get(optimisticId)
        if (!existing) return

        batch.items.set(optimisticId, { ...existing, ...updates })
    }

    function cleanupAlbumBatchIfSettled(albumId: string) {
        const batch = albumBatches.get(albumId)
        if (!batch) return

        const allSettled = Array.from(batch.items.values()).every(item =>
            item.status === 'sent' || item.status === 'cancelled' || item.status === 'failed'
        )

        if (allSettled && batch.items.size >= batch.expectedCount) {
            albumBatches.delete(albumId)
        }
    }

    async function flushAlbumBatchIfReady(albumId: string) {
        const batch = albumBatches.get(albumId)
        if (!batch) return

        if (batch.flushPromise) {
            await batch.flushPromise
            return
        }

        if (batch.items.size < batch.expectedCount) {
            return
        }

        const hasPendingUploads = Array.from(batch.items.values()).some(item =>
            item.status === 'uploading' || item.status === 'sending'
        )
        if (hasPendingUploads) {
            return
        }

        const itemsToSend = Array.from(batch.items.values())
            .filter(item => item.status === 'uploaded' && item.content)
            .sort((left, right) => left.albumIndex - right.albumIndex)

        if (itemsToSend.length === 0) {
            cleanupAlbumBatchIfSettled(albumId)
            return
        }

        batch.flushPromise = (async () => {
            for (const queuedItem of itemsToSend) {
                const currentItem = batch.items.get(queuedItem.optimisticId)
                if (!currentItem || currentItem.status !== 'uploaded' || !currentItem.content) {
                    continue
                }

                updateAlbumBatchItem(albumId, queuedItem.optimisticId, { status: 'sending' })
                const result = await sendMediaMessage(
                    currentItem.type,
                    currentItem.content,
                    currentItem.localBlobUrl,
                    currentItem.optimisticId
                )

                if (result) {
                    updateAlbumBatchItem(albumId, queuedItem.optimisticId, { status: 'sent' })
                } else {
                    updateAlbumBatchItem(albumId, queuedItem.optimisticId, { status: 'failed' })
                    const failedMessage = messages.value.find(message => message.id === queuedItem.optimisticId)
                    if (failedMessage) {
                        failedMessage.is_error = true
                        failedMessage.is_sending = false
                    }
                }
            }
        })()

        try {
            await batch.flushPromise
        } finally {
            batch.flushPromise = null
            cleanupAlbumBatchIfSettled(albumId)
        }
    }

    function markAlbumItemState(albumId: string | null | undefined, optimisticId: number, status: 'cancelled' | 'failed') {
        if (!albumId) return

        updateAlbumBatchItem(albumId, optimisticId, { status })
        void flushAlbumBatchIfReady(albumId)
    }

    function cancelUpload(id: number) {
        const controller = uploadControllers.get(id);
        if (controller) {
            controller.abort();
            uploadControllers.delete(id);
            // Counter decremented in finally block of handleMediaUploadWrapper
        } else {
            // Forcible cleanup if clicked before XHR starts or after XHR finished but stuck in IndexedDB step
            const index = messages.value.findIndex(m => m.id === id);
            const msg = messages.value[index];
            if (msg && msg.is_sending) {
                const albumId = getAlbumIdFromMessage(msg)
                markAlbumItemState(albumId, id, 'cancelled')
                msg.is_error = true; // prevents 'finally' from removing a non-error message if it shouldn't, though splice removes it anyway
                messages.value.splice(index, 1);
            }
        }
    }

    // === IndexedDB Image Cache ===
    const imageCache = ref<Record<string, string>>({})
    const DB_NAME = 'chat_image_cache'
    const DB_VERSION = 1
    const STORE_NAME = 'images'

    function openImageDB(): Promise<IDBDatabase> {
        if (imageDbPromise) {
            return imageDbPromise
        }

        imageDbPromise = new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION)
            req.onupgradeneeded = (e) => {
                const db = (e.target as IDBOpenDBRequest).result
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME)
                }
            }
            req.onsuccess = () => {
                const db = req.result
                db.onversionchange = () => {
                    db.close()
                    imageDbPromise = null
                }
                resolve(db)
            }
            req.onerror = () => {
                imageDbPromise = null
                reject(req.error)
            }
        })

        return imageDbPromise
    }

    function setCachedMediaUrl(fileId: string, objectUrl: string) {
        const previousUrl = imageCache.value[fileId]
        if (previousUrl === objectUrl) {
            return
        }

        if (previousUrl && previousUrl !== objectUrl && previousUrl.startsWith('blob:')) {
            URL.revokeObjectURL(previousUrl)
        }

        imageCache.value[fileId] = objectUrl
    }

    function pumpHydrationQueue() {
        hydrationPumpScheduled = false

        while (activeHydrationCount < MAX_CONCURRENT_HYDRATIONS && hydrationQueue.length > 0) {
            const nextItem = hydrationQueue.shift()
            if (!nextItem) {
                return
            }

            if (imageCache.value[nextItem.fileId]) {
                pendingHydrationIds.delete(nextItem.fileId)
                continue
            }

            activeHydrationCount += 1

            scheduleIdleTask(() => {
                void (async () => {
                    try {
                        await waitForNextPaint()
                        if (!imageCache.value[nextItem.fileId]) {
                            await loadImageForMessage(nextItem.content, nextItem.type)
                        }
                    } finally {
                        pendingHydrationIds.delete(nextItem.fileId)
                        activeHydrationCount = Math.max(0, activeHydrationCount - 1)
                        if (hydrationQueue.length > 0) {
                            scheduleHydrationPump()
                        }
                    }
                })()
            })
        }
    }

    function scheduleHydrationPump() {
        if (hydrationPumpScheduled) {
            return
        }

        hydrationPumpScheduled = true
        scheduleIdleTask(() => pumpHydrationQueue())
    }

    function scheduleMediaHydration(content: string, type?: string) {
        const fileId = getFileId(content)
        if (!fileId) {
            return
        }

        if (imageCache.value[fileId] || pendingMediaLoads.has(fileId) || pendingHydrationIds.has(fileId)) {
            return
        }

        pendingHydrationIds.add(fileId)
        hydrationQueue.push({ fileId, content, type })
        scheduleHydrationPump()
    }

    async function getFromDB(key: string): Promise<Blob | null> {
        try {
            const db = await openImageDB()
            return new Promise((resolve) => {
                const tx = db.transaction(STORE_NAME, 'readonly')
                const req = tx.objectStore(STORE_NAME).get(key)
                req.onsuccess = () => resolve(req.result ?? null)
                req.onerror = () => resolve(null)
            })
        } catch { return null }
    }

    async function saveToDB(key: string, blob: Blob): Promise<void> {
        try {
            const db = await openImageDB()
            await new Promise<void>((resolve) => {
                try {
                    const tx = db.transaction(STORE_NAME, 'readwrite')
                    tx.objectStore(STORE_NAME).put(blob, key)
                    tx.oncomplete = () => resolve()
                    tx.onerror = () => resolve()
                } catch (e) {
                    console.warn('IndexedDB put error, skipping cache:', e)
                    resolve()
                }
            })
        } catch { /* ignore */ }
    }

    async function loadImageForMessage(content: string, type?: string): Promise<string | null> {
        const fileId = getFileId(content)
        if (!fileId) return null
        if (imageCache.value[fileId]) return imageCache.value[fileId] || null

        const pendingLoad = pendingMediaLoads.get(fileId)
        if (pendingLoad) {
            return pendingLoad
        }

        const loadPromise = (async () => {
            const cached = await getFromDB(fileId)
            if (cached) {
                const objectUrl = URL.createObjectURL(cached)
                setCachedMediaUrl(fileId, objectUrl)
                return objectUrl
            }

            if (type !== 'voice' && type !== 'sticker') {
                return null
            }

            try {
                const res = await fetch(`${apiBaseUrl}/api/chat/files/${fileId}?token=${jwtToken}`)
                if (!res.ok) return null
                const blob = await res.blob()
                await saveToDB(fileId, blob)
                const objectUrl = URL.createObjectURL(blob)
                setCachedMediaUrl(fileId, objectUrl)
                return objectUrl
            } catch {
                return null
            }
        })()

        pendingMediaLoads.set(fileId, loadPromise)

        try {
            return await loadPromise
        } finally {
            pendingMediaLoads.delete(fileId)
        }
    }

    async function resolveMediaUrlForMessage(msg: Message): Promise<string> {
        const fileId = getFileId(msg.content)
        const existingUrl = msg.local_blob_url || imageCache.value[fileId] || ''
        if (existingUrl) return existingUrl

        const restoredUrl = await loadImageForMessage(msg.content, msg.message_type)
        return msg.local_blob_url || restoredUrl || imageCache.value[fileId] || ''
    }

    function getFileId(content: string): string {
        if (!content || !content.startsWith('{')) return ''
        try { return JSON.parse(content).file_id ?? '' } catch { return '' }
    }

    function parseMediaPayload(content: string): Record<string, any> {
        if (!content || !content.startsWith('{')) return {}
        try {
            return JSON.parse(content)
        } catch {
            return {}
        }
    }

    function getAlbumIndexFromMessage(msg: Message) {
        const payload = parseMediaPayload(msg.content)
        return typeof payload.album_index === 'number' && Number.isFinite(payload.album_index)
            ? payload.album_index
            : Number.MAX_SAFE_INTEGER
    }

    function getAlbumMessages(msg: Message) {
        const albumId = getAlbumIdFromMessage(msg)
        if (!albumId) {
            return [msg]
        }

        const albumMessages = messages.value
            .filter(candidate => {
                if (candidate.message_type !== 'image' && candidate.message_type !== 'video') return false
                if (candidate.is_error) return false
                if (candidate.sender_id !== msg.sender_id) return false
                return getAlbumIdFromMessage(candidate) === albumId
            })
            .sort((left, right) => {
                const byIndex = getAlbumIndexFromMessage(left) - getAlbumIndexFromMessage(right)
                if (byIndex !== 0) return byIndex

                const byCreatedAt = new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
                if (byCreatedAt !== 0) return byCreatedAt

                return left.id - right.id
            })

        return albumMessages.length > 0 ? albumMessages : [msg]
    }

    async function buildLightboxMediaItem(msg: Message): Promise<LightboxMediaItem | null> {
        const payload = parseMediaPayload(msg.content)
        const fileId = typeof payload.file_id === 'string' ? payload.file_id : getFileId(msg.content)
        const resolvedUrl = await resolveMediaUrlForMessage(msg)
        const fallbackUrl = resolvedUrl || msg.local_blob_url || imageCache.value[fileId] || payload.thumbnail || ''

        if (!fallbackUrl) return null

        return {
            msgId: msg.id,
            fileId,
            type: msg.message_type === 'video' ? 'video' : 'image',
            url: fallbackUrl,
            thumbnail: payload.thumbnail || fallbackUrl,
        }
    }

    // === Media Download ===
    async function downloadMedia(msg: Message) {
        const fileId = getFileId(msg.content)
        if (!fileId) return

        const targetMsg = messages.value.find(m => m.id === msg.id) || msg
        targetMsg.is_downloading = true
        targetMsg.download_progress = 0

        try {
            const res = await fetch(`${apiBaseUrl}/api/chat/files/${fileId}?token=${jwtToken}`)
            if (!res.ok) throw new Error('Download failed')

            const contentType = res.headers.get('content-type') || 'application/octet-stream'
            const contentLength = res.headers.get('content-length')
            const total = contentLength ? parseInt(contentLength, 10) : 0

            if (!total || !res.body) {
                const blob = await res.blob()
                await saveToDB(fileId, blob)
                setCachedMediaUrl(fileId, URL.createObjectURL(blob))
                return
            }

            const reader = res.body.getReader()
            const chunks: Uint8Array[] = []
            let received = 0

            while (true) {
                const { done, value } = await reader.read()
                if (done) break
                if (value) {
                    chunks.push(value)
                    received += value.length
                    targetMsg.download_progress = Math.round((received / total) * 100)
                }
            }

            const combinedBlob = new Blob(chunks as BlobPart[], { type: contentType })
            await saveToDB(fileId, combinedBlob)
            setCachedMediaUrl(fileId, URL.createObjectURL(combinedBlob))
        } catch (e) {
            console.error('Download failed:', e)
            alert('خطا در دانلود فایل')
        } finally {
            targetMsg.is_downloading = false
        }
    }

    // === Lightbox State ===
    const lightboxMedia = ref<LightboxState | null>(null)

    async function handleMediaClick(msg: Message) {
        if (msg.message_type !== 'image' && msg.message_type !== 'video') {
            return
        }

        const albumMessages = getAlbumMessages(msg)
        const items = (await Promise.all(albumMessages.map(buildLightboxMediaItem)))
            .filter((item): item is LightboxMediaItem => !!item)

        if (items.length === 0) {
            return
        }

        const currentIndex = Math.max(0, items.findIndex(item => item.msgId === msg.id))
        const albumId = items.length > 1 ? getAlbumIdFromMessage(msg) : null

        lightboxMedia.value = {
            items,
            currentIndex,
            albumId,
        }
    }

    function setLightboxIndex(index: number) {
        const state = lightboxMedia.value
        if (!state) return

        const nextIndex = Math.max(0, Math.min(index, state.items.length - 1))
        if (nextIndex === state.currentIndex) return

        lightboxMedia.value = {
            ...state,
            currentIndex: nextIndex,
        }
    }

    function closeLightbox() {
        lightboxMedia.value = null
    }

    // === Media Upload ===
    async function preprocessVideoPreview(srcUrl: string, signal?: AbortSignal): Promise<VideoPreviewResult> {
        return new Promise((resolve, reject) => {
            const video = document.createElement('video')
            let settled = false
            let width = 0
            let height = 0
            let seekScheduled = false

            const cleanup = () => {
                clearTimeout(fallbackTimeout)
                signal?.removeEventListener('abort', handleAbort)
                video.onloadedmetadata = null
                video.onloadeddata = null
                video.onseeked = null
                video.onerror = null
                try {
                    video.pause()
                    video.removeAttribute('src')
                    video.load()
                } catch {
                    // Ignore cleanup failures.
                }
            }

            const finish = (result: VideoPreviewResult) => {
                if (settled) return
                settled = true
                cleanup()
                resolve(result)
            }

            const fail = (error: Error) => {
                if (settled) return
                settled = true
                cleanup()
                reject(error)
            }

            const captureFrame = () => {
                try {
                    const sourceWidth = video.videoWidth || width || 1
                    const sourceHeight = video.videoHeight || height || 1
                    const canvas = document.createElement('canvas')
                    const targetSize = 20
                    const scale = Math.min(targetSize / sourceWidth, targetSize / sourceHeight)
                    canvas.width = Math.max(1, Math.round(sourceWidth * scale))
                    canvas.height = Math.max(1, Math.round(sourceHeight * scale))
                    const ctx = canvas.getContext('2d')
                    if (!ctx) {
                        finish({ thumbnailDataUrl: '', width, height })
                        return
                    }

                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
                    finish({
                        thumbnailDataUrl: canvas.toDataURL('image/jpeg', 0.5),
                        width,
                        height,
                    })
                } catch {
                    finish({ thumbnailDataUrl: '', width, height })
                }
            }

            const handleAbort = () => fail(new Error('UploadCancelled'))

            signal?.addEventListener('abort', handleAbort, { once: true })

            video.preload = 'auto'
            video.muted = true
            video.playsInline = true
            video.src = srcUrl

            const fallbackTimeout = setTimeout(() => {
                console.warn("Video preview preprocessing timed out after 3s.");
                finish({ thumbnailDataUrl: '', width, height })
            }, 3000);

            video.onloadedmetadata = () => {
                width = video.videoWidth || width
                height = video.videoHeight || height

                const targetTime = Number.isFinite(video.duration) && video.duration > 0
                    ? Math.min(0.1, Math.max(video.duration * 0.25, 0))
                    : 0

                if (targetTime > 0) {
                    seekScheduled = true
                    try {
                        video.currentTime = targetTime
                    } catch {
                        seekScheduled = false
                    }
                }
            }

            video.onloadeddata = () => {
                if (!seekScheduled) {
                    captureFrame()
                }
            }

            video.onseeked = () => {
                captureFrame()
            }

            video.onerror = () => {
                finish({ thumbnailDataUrl: '', width, height })
            }
        })
    }

    async function handleMediaUploadWrapper(file: File, albumId?: string | null, albumIndex?: number, albumSize?: number) {
        if (!file) return

        const isVideo = file.type.startsWith('video/')
        const isAudio = file.type.startsWith('audio/')
        
        let msgType: 'video' | 'image' | 'voice' = 'image'
        if (isVideo) msgType = 'video'
        else if (isAudio) msgType = 'voice'

        const normalizedAlbumId = (msgType === 'image' || msgType === 'video') ? albumId : null
        const normalizedAlbumIndex = typeof albumIndex === 'number' ? albumIndex : 0
        const normalizedAlbumSize = normalizedAlbumId ? Math.max(albumSize ?? 0, 1) : 0
        const preprocessBatchSize = normalizedAlbumSize || 1
        const preprocessLimit = getAdaptivePreprocessLimit(preprocessBatchSize, msgType)
        
        if (!selectedUserId.value) return

        if ((isVideo || isAudio) && file.size > CHAT_MEDIA_MAX_UPLOAD_BYTES) {
            const tooLargeMessage = buildUploadTooLargeMessage(file.size)
            alert(tooLargeMessage)
            return
        }

        activeUploadsCount++
        isUploading.value = activeUploadsCount > 0
        let step = 'start'
        const processingAbortController = new AbortController()

        const optimisticId = createOptimisticUploadId()
        const localUrl = URL.createObjectURL(file)
        const initialContent = appendAlbumMetadata({
            placeholder: true,
            durationMs: (file as any).durationMs,
        }, msgType, normalizedAlbumId, normalizedAlbumIndex)
        const optimisticMsg: Message = {
            id: optimisticId,
            sender_id: currentUserId,
            receiver_id: selectedUserId.value,
            content: JSON.stringify(initialContent),
            message_type: msgType,
            is_read: true,
            is_sending: true,
            upload_progress: 0,
            upload_loaded: 0,
            upload_total: 0,
            local_blob_url: localUrl,
            created_at: new Date().toISOString()
        }

        if (normalizedAlbumId) {
            const batch = ensureAlbumBatch(normalizedAlbumId, normalizedAlbumSize)
            batch.items.set(optimisticId, {
                optimisticId,
                albumIndex: normalizedAlbumIndex,
                type: msgType === 'video' ? 'video' : 'image',
                status: 'uploading'
            })
        }

        messages.value.push(optimisticMsg)

        let isCancelledLocally = false;
        uploadControllers.set(optimisticId, {
            abort: () => {
                isCancelledLocally = true;
                processingAbortController.abort();
                const index = messages.value.findIndex(m => m.id === optimisticId);
                if (index !== -1) messages.value.splice(index, 1);
                markAlbumItemState(normalizedAlbumId, optimisticId, 'cancelled')
                uploadControllers.delete(optimisticId);
            }
        });

        const getOptimisticTarget = () => messages.value.find(m => m.id === optimisticId) || optimisticMsg;

        await nextTick()
        scrollToBottom()
        await waitForNextPaint()

        try {
            let sourceFile = file
            if (!isVideo && !isAudio) {
                step = 'normalize_heic'
                sourceFile = await normalizeImageUploadFile(file)

                if (sourceFile !== file) {
                    const normalizedUrl = URL.createObjectURL(sourceFile)
                    const target = getOptimisticTarget()
                    const previousUrl = target.local_blob_url

                    if (previousUrl && previousUrl.startsWith('blob:') && previousUrl !== normalizedUrl) {
                        URL.revokeObjectURL(previousUrl)
                    }

                    target.local_blob_url = normalizedUrl
                }
            }

            let uploadFile: File | Blob = sourceFile;
            let thumbBase64 = '';

            let finalWidth = 0;
            let finalHeight = 0;

            if (isVideo) {
                step = 'video_preprocess'
                const videoPreviewStartedAt = performance.now()
                try {
                    const preview = await runAdaptivePreprocessTask(preprocessLimit, () =>
                        preprocessVideoPreview(localUrl, processingAbortController.signal)
                    )
                    thumbBase64 = preview.thumbnailDataUrl
                    finalWidth = preview.width
                    finalHeight = preview.height

                    trackPreprocessEvent({
                        mediaType: 'video',
                        path: 'video_preview',
                        status: 'success',
                        startedAt: videoPreviewStartedAt,
                        batchSize: preprocessBatchSize,
                        schedulerLimit: preprocessLimit,
                        usedWorker: false,
                        width: finalWidth,
                        height: finalHeight,
                    })

                    const previewContent: any = appendAlbumMetadata({
                        thumbnail: thumbBase64,
                        placeholder: true,
                    }, msgType, normalizedAlbumId, normalizedAlbumIndex)

                    if (finalWidth && finalHeight) {
                        previewContent.width = finalWidth
                        previewContent.height = finalHeight
                    }

                    getOptimisticTarget().content = JSON.stringify(previewContent)
                } catch (warn) {
                    trackPreprocessEvent({
                        mediaType: 'video',
                        path: 'video_preview',
                        status: isCancelledLocally ? 'cancelled' : 'failed',
                        startedAt: videoPreviewStartedAt,
                        batchSize: preprocessBatchSize,
                        schedulerLimit: preprocessLimit,
                        usedWorker: false,
                        fallbackReason: 'preview_failed',
                        errorMessage: warn instanceof Error ? warn.message : String(warn),
                    })
                    if (isCancelledLocally) throw new Error('UploadCancelled')
                    console.warn("Video preview preprocessing failed:", warn)
                }
            } else if (isAudio) {
                step = 'skip_audio_thumb'
                trackPreprocessEvent({
                    mediaType: 'voice',
                    path: 'voice_passthrough',
                    status: 'success',
                    startedAt: performance.now(),
                    batchSize: preprocessBatchSize,
                    schedulerLimit: preprocessLimit,
                    usedWorker: false,
                })
                // No thumbnail processing for voice
            } else {
                if (isCancelledLocally) throw new Error('UploadCancelled');

                const imageFastPathStartedAt = performance.now()
                let imagePath: 'image_worker' | 'image_main_thread' = canUseImagePreprocessWorker()
                    ? 'image_worker'
                    : 'image_main_thread'

                try {
                    if (canUseImagePreprocessWorker()) {
                        step = 'worker_preprocess'
                        const processed = await runAdaptivePreprocessTask(preprocessLimit, () =>
                            processImageInWorker(sourceFile, processingAbortController.signal)
                        )

                        uploadFile = processed.blob
                        finalWidth = processed.width
                        finalHeight = processed.height
                        thumbBase64 = processed.thumbnailDataUrl
                    } else {
                        step = 'main_thread_preprocess'
                        await waitForNextPaint()
                        const processed = await runAdaptivePreprocessTask(preprocessLimit, () =>
                            preprocessImageOnMainThread(sourceFile)
                        )

                        uploadFile = processed.blob
                        finalWidth = processed.width
                        finalHeight = processed.height
                        thumbBase64 = processed.thumbnailDataUrl
                    }

                    trackPreprocessEvent({
                        mediaType: 'image',
                        path: imagePath,
                        status: 'success',
                        startedAt: imageFastPathStartedAt,
                        batchSize: preprocessBatchSize,
                        schedulerLimit: preprocessLimit,
                        usedWorker: imagePath === 'image_worker',
                        width: finalWidth,
                        height: finalHeight,
                    })
                } catch (workerWarn) {
                    trackPreprocessEvent({
                        mediaType: 'image',
                        path: imagePath,
                        status: isCancelledLocally ? 'cancelled' : 'failed',
                        startedAt: imageFastPathStartedAt,
                        batchSize: preprocessBatchSize,
                        schedulerLimit: preprocessLimit,
                        usedWorker: imagePath === 'image_worker',
                        fallbackReason: imagePath === 'image_worker' ? 'worker_failed' : 'main_thread_failed',
                        errorMessage: workerWarn instanceof Error ? workerWarn.message : String(workerWarn),
                    })
                    if (isCancelledLocally) throw new Error('UploadCancelled');

                    console.warn("Image preprocessing fast path failed, using legacy fallback:", workerWarn)

                    step = 'compress_main'
                    const imageFallbackStartedAt = performance.now()
                    try {
                        const compressed = await runAdaptivePreprocessTask(preprocessLimit, () =>
                            nativeImageCompress(sourceFile, 1920, 0.85)
                        )
                        uploadFile = compressed.blob;
                        finalWidth = compressed.width;
                        finalHeight = compressed.height;
                    } catch (warn) {
                        console.warn("Image compression failed, using original:", warn)
                        try {
                            const original = await nativeImageCompress(sourceFile, 9999, 1.0);
                            finalWidth = original.width;
                            finalHeight = original.height;
                        } catch (error) {
                            console.warn("Original image dimension fallback failed:", error)
                        }
                    }

                    if (isCancelledLocally) throw new Error('UploadCancelled');
                    step = 'compress_thumb'
                    try {
                        thumbBase64 = await runAdaptivePreprocessTask(preprocessLimit, () =>
                            generateImageThumbnailDataUrl(uploadFile)
                        )
                    } catch (warn) {
                        console.warn("Image thumbnail generation failed:", warn)
                    }

                    trackPreprocessEvent({
                        mediaType: 'image',
                        path: 'image_legacy_fallback',
                        status: 'success',
                        startedAt: imageFallbackStartedAt,
                        batchSize: preprocessBatchSize,
                        schedulerLimit: preprocessLimit,
                        usedWorker: false,
                        fallbackReason: imagePath === 'image_worker' ? 'worker_failed' : 'main_thread_failed',
                        width: finalWidth,
                        height: finalHeight,
                    })
                }

                if (isCancelledLocally) throw new Error('UploadCancelled');

                const rotatedUrl = URL.createObjectURL(uploadFile);
                getOptimisticTarget().local_blob_url = rotatedUrl;
            }

            if (msgType === 'video' && (!finalWidth || !finalHeight)) {
                const videoMetadataStartedAt = performance.now()
                try {
                    await runAdaptivePreprocessTask(preprocessLimit, () => new Promise<void>((resolve) => {
                        const video = document.createElement('video');
                        const cleanup = () => {
                            video.onloadedmetadata = null
                            video.onerror = null
                            try {
                                video.pause()
                                video.removeAttribute('src')
                                video.load()
                            } catch {
                                // Ignore cleanup failures.
                            }
                        }

                        const timeoutId = setTimeout(() => {
                            cleanup()
                            resolve()
                        }, 1500)

                        video.preload = 'metadata'
                        video.onloadedmetadata = () => {
                            finalWidth = video.videoWidth;
                            finalHeight = video.videoHeight;
                            clearTimeout(timeoutId)
                            cleanup()
                            resolve();
                        };
                        video.onerror = () => {
                            clearTimeout(timeoutId)
                            cleanup()
                            resolve()
                        };
                        video.src = localUrl;
                    }));

                    trackPreprocessEvent({
                        mediaType: 'video',
                        path: 'video_metadata_fallback',
                        status: 'success',
                        startedAt: videoMetadataStartedAt,
                        batchSize: preprocessBatchSize,
                        schedulerLimit: preprocessLimit,
                        usedWorker: false,
                        fallbackReason: 'missing_preview_dimensions',
                        width: finalWidth,
                        height: finalHeight,
                    })
                } catch (e) {
                    trackPreprocessEvent({
                        mediaType: 'video',
                        path: 'video_metadata_fallback',
                        status: 'failed',
                        startedAt: videoMetadataStartedAt,
                        batchSize: preprocessBatchSize,
                        schedulerLimit: preprocessLimit,
                        usedWorker: false,
                        fallbackReason: 'metadata_failed',
                        errorMessage: e instanceof Error ? e.message : String(e),
                    })
                    console.warn("Could not extract final video dimensions:", e);
                }
            }

            const targetMsg = getOptimisticTarget();
            const optimisticContent: any = appendAlbumMetadata({ thumbnail: thumbBase64 }, msgType, normalizedAlbumId, normalizedAlbumIndex);
            if (finalWidth && finalHeight) {
                optimisticContent.width = finalWidth;
                optimisticContent.height = finalHeight;
            }
            if (isVideo) optimisticContent.placeholder = true;
            targetMsg.content = JSON.stringify(optimisticContent);

            step = 'prepare_form'
            if (uploadFile.size > CHAT_MEDIA_MAX_UPLOAD_BYTES) {
                throw new Error(buildUploadTooLargeMessage(uploadFile.size))
            }

            const formData = new FormData()
            formData.append('file', uploadFile, sourceFile.name)
            formData.append('thumbnail', thumbBase64)

            step = 'xhr_upload'
            const data = await new Promise<any>((resolve, reject) => {
                const xhr = new XMLHttpRequest()
                uploadControllers.set(optimisticId, {
                    abort: () => {
                        const target = getOptimisticTarget();
                        if (target) target.is_error = false; // to prevent error UI
                        xhr.abort();
                        const index = messages.value.findIndex(m => m.id === optimisticId);
                        if (index !== -1) messages.value.splice(index, 1);
                        markAlbumItemState(normalizedAlbumId, optimisticId, 'cancelled')
                        reject(new Error('UploadCancelled'))
                    }
                });
                xhr.open('POST', `${apiBaseUrl}/api/chat/upload-media`)
                xhr.setRequestHeader('Authorization', `Bearer ${localStorage.getItem('auth_token') || jwtToken}`)

                xhr.upload.onprogress = (e) => {
                    if (e.lengthComputable) {
                        const target = getOptimisticTarget();
                        target.upload_progress = Math.round((e.loaded / e.total) * 100)
                        target.upload_loaded = e.loaded
                        target.upload_total = e.total
                    }
                }

                xhr.onload = () => {
                    if (xhr.status === 401) {
                        reject(new Error("نشست شما منقضی شده است. لطفاً صفحه را رفرش کنید."))
                        return
                    }
                    if (xhr.status === 413) {
                        reject(new Error(buildUploadTooLargeMessage(uploadFile.size)))
                        return
                    }
                    if (xhr.status >= 200 && xhr.status < 300) {
                        try {
                            resolve(JSON.parse(xhr.responseText))
                        } catch (err) {
                            reject(new Error("Invalid JSON response"))
                        }
                    } else {
                        try {
                            const parsed = JSON.parse(xhr.responseText)
                            if (parsed.detail) {
                                reject(new Error(`مشکل سرور (${xhr.status}): ${parsed.detail}`))
                                return
                            }
                        } catch (e) { }
                        
                        let safeResponse = xhr.responseText.substring(0, 100);
                        if (safeResponse.toLowerCase().includes('<html')) {
                            safeResponse = "خطای سرور یا عدم اتصال"; // Sanitize HTML
                        }
                        reject(new Error(`مشکل سرور (${xhr.status}): ${safeResponse}`))
                    }
                }
                xhr.onerror = () => reject(new Error("Network Error"))
                xhr.onabort = () => {
                uploadControllers.delete(optimisticId)
            }
            
            xhr.onloadend = () => {
                uploadControllers.delete(optimisticId)
            }
            
            xhr.send(formData)
            })
            uploadControllers.delete(optimisticId);

            step = 'prepare_json'
            // Prefer server-returned dimensions (EXIF-transposed) over local ones
            if (data.width && data.height) {
                finalWidth = data.width;
                finalHeight = data.height;
            }
            const contentObj: any = appendAlbumMetadata({
                file_id: data.file_id,
                thumbnail: data.thumbnail
            }, msgType, normalizedAlbumId, normalizedAlbumIndex)
            if (finalWidth && finalHeight) {
                contentObj.width = finalWidth;
                contentObj.height = finalHeight;
            }
            if ((file as any).durationMs !== undefined) {
                contentObj.durationMs = (file as any).durationMs
            }
            const messageContent = JSON.stringify(contentObj)

            step = 'save_local_cache'
            await saveToDB(data.file_id, uploadFile)
            const finalLocalUrl = getOptimisticTarget()?.local_blob_url || localUrl
            if (!isAudio) {
                // we probably don't need a Blob URL in the image cache for voice, but it's safe to store
                setCachedMediaUrl(data.file_id, finalLocalUrl)
            } else {
                setCachedMediaUrl(data.file_id, finalLocalUrl)
            }

            const targetAfterUpload = getOptimisticTarget()
            if (targetAfterUpload) {
                targetAfterUpload.upload_progress = 100
                targetAfterUpload.upload_loaded = targetAfterUpload.upload_total || file.size
                targetAfterUpload.upload_total = targetAfterUpload.upload_total || file.size
            }

            if (normalizedAlbumId && (msgType === 'image' || msgType === 'video')) {
                updateAlbumBatchItem(normalizedAlbumId, optimisticId, {
                    content: messageContent,
                    localBlobUrl: finalLocalUrl,
                    status: 'uploaded'
                })
                void flushAlbumBatchIfReady(normalizedAlbumId)
            } else {
                step = 'send_ws_message'
                await sendMediaMessage(msgType, messageContent, finalLocalUrl, optimisticId)
            }

        } catch (e: any) {
            if (e.message === 'UploadCancelled') {
                console.log('Upload was cancelled explicitly.');
                return; // Early return to avoid error UI
            }
            console.error(`Upload error at step [${step}]:`, e);
            const errString = e && e.message ? e.message : JSON.stringify(e);
            alert(`خطا در آپلود: ` + errString);
            optimisticMsg.is_error = true;
            markAlbumItemState(normalizedAlbumId, optimisticId, 'failed')
        } finally {
            activeUploadsCount--
            isUploading.value = activeUploadsCount > 0
        }
    }

    return {
        cancelUpload,
        imageCache,
        loadImageForMessage,
        scheduleMediaHydration,
        downloadMedia,
        lightboxMedia,
        handleMediaClick,
        setLightboxIndex,
        closeLightbox,
        handleMediaUploadWrapper
    }
}
