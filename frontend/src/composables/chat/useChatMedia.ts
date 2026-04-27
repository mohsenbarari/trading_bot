import { ref, type Ref, nextTick, onUnmounted, watch } from 'vue'
import type { Message } from '../../types/chat'
import router from '../../router'
import { canUseImagePreprocessWorker, getRecommendedImagePreprocessParallelism, processImageInWorker } from '../../utils/imagePreprocessClient'
import { primeMediaPreprocessTelemetry, recordMediaPreprocessTelemetry } from '../../utils/chatMediaTelemetry'
import {
    submitUpload as backgroundSubmitUpload,
    cancelUpload as backgroundCancelUpload,
    subscribeToUploads as backgroundSubscribeToUploads,
    getPendingForUser as backgroundGetPendingForUser,
    buildOptimisticMessageFromUpload,
    type UploadEvent,
} from '../../services/chatUploadBackground'
import {
    cancelDocumentDownload as backgroundCancelDocumentDownload,
    getCompletedDocumentDownloadUrl as backgroundGetCompletedDocumentDownloadUrl,
    getPendingDocumentDownloadsForUser as backgroundGetPendingDocumentDownloadsForUser,
    restoreCompletedDocumentDownloadUrl as backgroundRestoreCompletedDocumentDownloadUrl,
    startDocumentDownload as backgroundStartDocumentDownload,
    subscribeToDocumentDownloads as backgroundSubscribeToDocumentDownloads,
    type DocumentDownloadEvent,
} from '../../services/chatDocumentDownloadBackground'
import {
    getCachedAttachmentBlob,
    persistObjectUrlToAttachmentCache,
    putCachedAttachmentBlob,
    restoreCachedAttachmentUrl,
    setLiveAttachmentUrl,
} from '../../utils/chatAttachmentCache'

const CHAT_MEDIA_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
const CHAT_MEDIA_MAX_UPLOAD_LABEL = '50MB'
const CHAT_MEDIA_PERSISTED_THUMBNAIL_MAX_EDGE = 64
const CHAT_MEDIA_PERSISTED_THUMBNAIL_QUALITY = 0.58
const CHAT_EDITED_IMAGE_FLAG = '__chatEditedImage'
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

function isEditedImageUploadFile(file: File) {
    return Boolean(
        (file as File & Record<string, unknown>)[CHAT_EDITED_IMAGE_FLAG] === true
        || /_edited\.(jpe?g|png|webp)$/i.test(file.name || '')
    )
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

async function readImageDimensions(file: File | Blob) {
    const decoded = await decodeImageSource(file)

    try {
        return {
            width: decoded.width,
            height: decoded.height,
        }
    } finally {
        decoded.cleanup()
    }
}

async function preprocessImageOnMainThread(
    file: File | Blob,
    maxWidthOrHeight = 1920,
    quality = 0.85,
    thumbnailMaxWidthOrHeight = CHAT_MEDIA_PERSISTED_THUMBNAIL_MAX_EDGE,
    thumbnailQuality = CHAT_MEDIA_PERSISTED_THUMBNAIL_QUALITY,
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
    senderId: number | null
    createdAt: string
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

type UploadJob = {
    limit: number
    run: () => Promise<unknown>
    resolve: (value: unknown) => void
    reject: (reason?: unknown) => void
    cleanupAbort?: () => void
}

type DevicePerformanceTier = 'weak' | 'mid' | 'strong'

type MediaClientCapability = {
    tier: DevicePerformanceTier
    cpuCount: number
    deviceMemory: number
    effectiveType: string
    saveData: boolean
    hasWorkerPreprocess: boolean
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
    const thumb = await nativeImageCompress(file, CHAT_MEDIA_PERSISTED_THUMBNAIL_MAX_EDGE, CHAT_MEDIA_PERSISTED_THUMBNAIL_QUALITY)
    return await blobToDataUrl(thumb.blob)
}

type MediaHydrationOptions = {
    allowNetwork?: boolean
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
        // sendMediaMessage is accepted for backward compatibility with callers
        // but is no longer used here — the background upload service performs
        // the final /api/chat/send call internally so uploads complete across
        // ChatView unmount.
    } = options
    void options.sendMediaMessage

    let activeUploadsCount = 0
    let activePreprocessCount = 0
    let activeNetworkUploadCount = 0
    // Local preprocessing-phase abort controllers. Once the service takes
    // ownership (submitUpload), cancellation is delegated to the service so
    // that uploads continue across ChatView unmount.
    const preprocessingAborts = new Map<number, () => void>()
    const preprocessQueue: PreprocessJob[] = []
    const networkUploadQueue: UploadJob[] = []
    const pendingMediaLoads = new Map<string, Promise<string | null>>()
    const pendingDocumentCacheRestores = new Set<string>()
    const pendingHydrationKeys = new Set<string>()
    const hydrationQueue: Array<{
        fileId: string
        content: string
        type?: string
        allowNetwork: boolean
        queueKey: string
    }> = []
    let activeHydrationCount = 0
    let hydrationPumpScheduled = false

    function getMediaClientCapability(): MediaClientCapability {
        if (typeof navigator === 'undefined') {
            return {
                tier: 'weak',
                cpuCount: 1,
                deviceMemory: 0,
                effectiveType: '',
                saveData: false,
                hasWorkerPreprocess: false,
            }
        }

        const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection
        const saveData = Boolean(connection?.saveData)
        const effectiveType = connection?.effectiveType || ''
        const cpuCount = navigator.hardwareConcurrency || 4
        const deviceMemory = typeof (navigator as Navigator & { deviceMemory?: number }).deviceMemory === 'number'
            ? (navigator as Navigator & { deviceMemory?: number }).deviceMemory || 0
            : 0
        const hasWorkerPreprocess = canUseImagePreprocessWorker()

        const slowConnection = saveData || effectiveType === 'slow-2g' || effectiveType === '2g'
        const constrainedDevice = (deviceMemory > 0 && deviceMemory <= 2) || cpuCount <= 4

        if (slowConnection || constrainedDevice) {
            return {
                tier: 'weak',
                cpuCount,
                deviceMemory,
                effectiveType,
                saveData,
                hasWorkerPreprocess,
            }
        }

        const strongCpu = cpuCount >= 8
        const strongMemory = deviceMemory === 0 ? cpuCount >= 10 : deviceMemory >= 6
        const fastConnection = !effectiveType || effectiveType === '4g' || effectiveType === '5g'

        return {
            tier: strongCpu && strongMemory && fastConnection && hasWorkerPreprocess ? 'strong' : 'mid',
            cpuCount,
            deviceMemory,
            effectiveType,
            saveData,
            hasWorkerPreprocess,
        }
    }

    const clientCapability = getMediaClientCapability()

    function getAdaptiveHydrationLimit(capability: MediaClientCapability) {
        if (capability.tier === 'strong') return 3
        if (capability.tier === 'mid') return 2
        return 1
    }

    const maxConcurrentHydrations = getAdaptiveHydrationLimit(clientCapability)

    primeMediaPreprocessTelemetry()

    function getAdaptivePreprocessLimit(albumSize: number, mediaType: 'image' | 'video' | 'voice' | 'document') {
        const capability = clientCapability

        if (mediaType === 'voice' || mediaType === 'document') {
            if (capability.tier === 'strong') return 4
            if (capability.tier === 'mid') return 3
            return 2
        }

        if (capability.tier === 'weak') return 1

        if (albumSize >= 8) {
            return capability.tier === 'strong' ? 2 : 1
        }

        if (albumSize >= 6) {
            return capability.tier === 'strong' ? 2 : 1
        }

        const recommended = getRecommendedImagePreprocessParallelism()
        if (mediaType === 'video') {
            return capability.tier === 'strong' ? 2 : 1
        }

        if (albumSize >= 4) {
            return capability.tier === 'strong'
                ? Math.max(1, Math.min(recommended + 1, 3))
                : Math.max(1, Math.min(recommended, 2))
        }

        return capability.tier === 'strong'
            ? Math.max(1, Math.min(recommended + 1, 4))
            : Math.max(1, Math.min(recommended + 1, 3))
    }

    function getAdaptiveUploadLimit(albumSize: number, mediaType: 'image' | 'video' | 'voice' | 'document') {
        const capability = clientCapability

        if (mediaType === 'voice' || mediaType === 'document') {
            return capability.tier === 'strong' ? 2 : 1
        }

        if (capability.tier === 'weak') return 1

        if (albumSize >= 8) {
            return capability.tier === 'strong' ? 2 : 1
        }

        if (albumSize >= 6) {
            return capability.tier === 'strong' ? 2 : 1
        }

        if (mediaType === 'video') {
            return capability.tier === 'strong' ? 2 : 1
        }

        if (albumSize >= 2) {
            return capability.tier === 'strong' ? 3 : 2
        }

        return capability.tier === 'strong' ? 3 : 2
    }

    function launchPreprocessJob(job: PreprocessJob) {
        activePreprocessCount += 1
        // Hard cap per job: if a preprocess step hangs (e.g. a mobile Worker
        // got OOM-killed and neither `onmessage` nor `onerror` fires, or an
        // off-main-thread createImageBitmap never settles), the composable's
        // `activePreprocessCount` would never decrement and every subsequent
        // album item would sit forever in `preprocessQueue`. This is what
        // the user saw as "preparing" locking up for large albums. The
        // worker client has its own 60s internal timeout that will reject
        // the underlying promise; this outer guard is defense-in-depth for
        // any other path (main-thread fallback, video metadata probe, etc.).
        let settled = false
        let timeoutId: ReturnType<typeof setTimeout> | null = setTimeout(() => {
            if (settled) return
            settled = true
            activePreprocessCount = Math.max(0, activePreprocessCount - 1)
            job.reject(new Error('Preprocessing step timed out'))
            pumpPreprocessQueue()
        }, 90_000)
        void job.run()
            .then((value) => {
                if (settled) return
                settled = true
                if (timeoutId !== null) { clearTimeout(timeoutId); timeoutId = null }
                activePreprocessCount = Math.max(0, activePreprocessCount - 1)
                job.resolve(value)
                pumpPreprocessQueue()
            })
            .catch((reason) => {
                if (settled) return
                settled = true
                if (timeoutId !== null) { clearTimeout(timeoutId); timeoutId = null }
                activePreprocessCount = Math.max(0, activePreprocessCount - 1)
                job.reject(reason)
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

    function launchUploadJob(job: UploadJob) {
        activeNetworkUploadCount += 1
        job.cleanupAbort?.()
        void job.run()
            .then((value) => job.resolve(value))
            .catch((reason) => job.reject(reason))
            .finally(() => {
                activeNetworkUploadCount = Math.max(0, activeNetworkUploadCount - 1)
                pumpUploadQueue()
            })
    }

    function pumpUploadQueue() {
        while (networkUploadQueue.length > 0) {
            const nextIndex = networkUploadQueue.findIndex(job => activeNetworkUploadCount < job.limit)
            if (nextIndex === -1) {
                return
            }

            const [nextJob] = networkUploadQueue.splice(nextIndex, 1)
            if (!nextJob) {
                return
            }

            launchUploadJob(nextJob)
        }
    }

    function runAdaptiveUploadTask<T>(
        limit: number,
        run: () => Promise<T>,
        signal?: AbortSignal,
    ): Promise<T> {
        return new Promise<T>((resolve, reject) => {
            const normalizedLimit = Math.max(1, limit)

            if (signal?.aborted) {
                reject(new Error('UploadCancelled'))
                return
            }

            const job: UploadJob = {
                limit: normalizedLimit,
                run: () => run() as Promise<unknown>,
                resolve: (value) => resolve(value as T),
                reject,
            }

            if (signal) {
                const handleAbort = () => {
                    const queuedIndex = networkUploadQueue.indexOf(job)
                    if (queuedIndex !== -1) {
                        networkUploadQueue.splice(queuedIndex, 1)
                    }
                    reject(new Error('UploadCancelled'))
                }

                signal.addEventListener('abort', handleAbort, { once: true })
                job.cleanupAbort = () => signal.removeEventListener('abort', handleAbort)
            }

            if (activeNetworkUploadCount < normalizedLimit) {
                launchUploadJob(job)
                return
            }

            networkUploadQueue.push(job)
        })
    }

    function trackPreprocessEvent(payload: {
        mediaType: 'image' | 'video' | 'voice'
        path: 'image_worker' | 'image_main_thread' | 'image_legacy_fallback' | 'image_edited_passthrough' | 'video_preview' | 'video_metadata_fallback' | 'voice_passthrough' | 'document_passthrough'
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
        msgType: 'image' | 'video' | 'voice' | 'document',
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

    function cancelUpload(id: number) {
        // First, trigger any preprocessing abort that may be running inside
        // this composable. The upload pipeline's catch block will splice the
        // optimistic message from `messages.value` when the abort propagates.
        const preAbort = preprocessingAborts.get(id)
        if (preAbort) {
            preAbort()
            preprocessingAborts.delete(id)
        }

        // Delegate to the background service for any already-submitted upload.
        // If the id is not yet tracked by the service this is a safe no-op.
        backgroundCancelUpload(id)

        // Fallback UI cleanup: if the message somehow outlives both paths
        // (e.g. stale state after reload) splice it immediately.
        const index = messages.value.findIndex(m => m.id === id)
        const msg = messages.value[index]
        if (msg && msg.is_sending && !msg.is_error) {
            messages.value.splice(index, 1)
        }
    }

    function cancelDocumentDownload(messageId: number) {
        backgroundCancelDocumentDownload(messageId)

        const index = messages.value.findIndex(message => message.id === messageId)
        const msg = index !== -1 ? messages.value[index] : null
        if (msg) {
            msg.is_downloading = false
            msg.download_progress = 0
        }
    }

    // === IndexedDB Image Cache ===
    const imageCache = ref<Record<string, string>>({})

    function setCachedMediaUrl(fileId: string, objectUrl: string) {
        const previousUrl = imageCache.value[fileId]
        if (previousUrl === objectUrl) {
            return
        }

        if (
            previousUrl
            && previousUrl !== objectUrl
            && previousUrl.startsWith('blob:')
            && !messages.value.some(message => message.local_blob_url === previousUrl)
        ) {
            URL.revokeObjectURL(previousUrl)
        }

        setLiveAttachmentUrl(fileId, objectUrl)
        imageCache.value[fileId] = objectUrl
    }

    function buildMediaLoadKey(fileId: string, allowNetwork: boolean) {
        return `${fileId}:${allowNetwork ? 'network' : 'cache'}`
    }

    function pumpHydrationQueue() {
        hydrationPumpScheduled = false

        while (activeHydrationCount < maxConcurrentHydrations && hydrationQueue.length > 0) {
            const nextItem = hydrationQueue.shift()
            if (!nextItem) {
                return
            }

            if (imageCache.value[nextItem.fileId]) {
                pendingHydrationKeys.delete(nextItem.queueKey)
                continue
            }

            activeHydrationCount += 1

            scheduleIdleTask(() => {
                void (async () => {
                    try {
                        await waitForNextPaint()
                        if (!imageCache.value[nextItem.fileId]) {
                            await loadImageForMessage(nextItem.content, nextItem.type, {
                                allowNetwork: nextItem.allowNetwork,
                            })
                        }
                    } finally {
                        pendingHydrationKeys.delete(nextItem.queueKey)
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

    function scheduleMediaHydration(content: string, type?: string, options: MediaHydrationOptions = {}) {
        const fileId = getFileId(content)
        if (!fileId) {
            return
        }

        const allowNetwork = Boolean(options.allowNetwork && type === 'image')
        const queueKey = buildMediaLoadKey(fileId, allowNetwork)

        if (imageCache.value[fileId] || pendingMediaLoads.has(queueKey) || pendingHydrationKeys.has(queueKey)) {
            return
        }

        pendingHydrationKeys.add(queueKey)
        hydrationQueue.push({ fileId, content, type, allowNetwork, queueKey })
        scheduleHydrationPump()
    }

    async function loadImageForMessage(content: string, type?: string, options: MediaHydrationOptions = {}): Promise<string | null> {
        const fileId = getFileId(content)
        if (!fileId) return null
        if (imageCache.value[fileId]) return imageCache.value[fileId] || null

        const allowNetwork = Boolean(options.allowNetwork && type === 'image')
        const loadKey = buildMediaLoadKey(fileId, allowNetwork)

        const pendingLoad = pendingMediaLoads.get(loadKey)
        if (pendingLoad) {
            return pendingLoad
        }

        const loadPromise = (async () => {
            const cached = await getCachedAttachmentBlob(fileId)
            if (cached) {
                const objectUrl = URL.createObjectURL(cached)
                setCachedMediaUrl(fileId, objectUrl)
                return objectUrl
            }

            if (!allowNetwork && type !== 'voice' && type !== 'sticker') {
                return null
            }

            try {
                const res = await fetch(`${apiBaseUrl}/api/chat/files/${fileId}?token=${jwtToken}`)
                if (!res.ok) return null
                const blob = await res.blob()
                await putCachedAttachmentBlob(fileId, blob)
                const objectUrl = URL.createObjectURL(blob)
                setCachedMediaUrl(fileId, objectUrl)
                return objectUrl
            } catch {
                return null
            }
        })()

        pendingMediaLoads.set(loadKey, loadPromise)

        try {
            return await loadPromise
        } finally {
            pendingMediaLoads.delete(loadKey)
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

    function getDocumentFileName(msg: Message): string {
        const payload = parseMediaPayload(msg.content)
        const fileName = typeof payload.file_name === 'string' ? payload.file_name.trim() : ''
        return fileName || `file_${msg.id}`
    }

    function triggerBrowserDownload(url: string, fileName: string) {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = fileName
        anchor.rel = 'noopener'
        anchor.style.display = 'none'
        document.body.appendChild(anchor)
        anchor.click()
        document.body.removeChild(anchor)
    }

    function openDocumentViewer(fileId: string, mimeType: string, fileName: string) {
        if (!fileId) return

        void router.push({
            name: 'attachment-view',
            query: {
                file_id: fileId,
                mime_type: mimeType || 'application/octet-stream',
                file_name: fileName || `file_${fileId}`,
            },
        })
    }

    function parseMediaPayload(content: string): Record<string, any> {
        if (!content || !content.startsWith('{')) return {}
        try {
            return JSON.parse(content)
        } catch {
            return {}
        }
    }

    function buildAuthenticatedMediaUrl(fileId: string): string {
        if (!fileId || !jwtToken) {
            return ''
        }

        return `${apiBaseUrl}/api/chat/files/${fileId}?token=${jwtToken}`
    }

    async function restoreDocumentUrlForMessage(message: Message): Promise<string> {
        const fileId = getFileId(message.content)
        if (!fileId) return ''

        const existing = message.local_blob_url || imageCache.value[fileId] || backgroundGetCompletedDocumentDownloadUrl(fileId)
        if (existing) {
            setCachedMediaUrl(fileId, existing)
            return existing
        }

        const restored = await backgroundRestoreCompletedDocumentDownloadUrl(fileId) || await restoreCachedAttachmentUrl(fileId)
        if (!restored) return ''

        setCachedMediaUrl(fileId, restored)
        message.local_blob_url = restored
        return restored
    }

    function scheduleDocumentCacheRestore(message: Message) {
        if (message.message_type !== 'document') return

        const fileId = getFileId(message.content)
        if (!fileId || message.local_blob_url || imageCache.value[fileId] || pendingDocumentCacheRestores.has(fileId)) {
            return
        }

        pendingDocumentCacheRestores.add(fileId)
        void restoreDocumentUrlForMessage(message)
            .finally(() => {
                pendingDocumentCacheRestores.delete(fileId)
            })
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
        const fallbackUrl = resolvedUrl
            || msg.local_blob_url
            || imageCache.value[fileId]
            || buildAuthenticatedMediaUrl(fileId)
            || payload.thumbnail
            || ''

        if (!fallbackUrl) return null

        return {
            msgId: msg.id,
            fileId,
            type: msg.message_type === 'video' ? 'video' : 'image',
            url: fallbackUrl,
            thumbnail: payload.thumbnail || fallbackUrl,
            senderId: typeof msg.sender_id === 'number' ? msg.sender_id : null,
            createdAt: msg.created_at,
        }
    }

    // === Media Download ===
    async function downloadMedia(msg: Message) {
        const fileId = getFileId(msg.content)
        if (!fileId) return

        const targetMsg = messages.value.find(m => m.id === msg.id) || msg
        const isDocument = msg.message_type === 'document'
        const documentFileName = getDocumentFileName(msg)

        if (isDocument) {
            const completedUrl = await restoreDocumentUrlForMessage(targetMsg)
            if (completedUrl) {
                targetMsg.local_blob_url = completedUrl
                openDocumentViewer(
                    fileId,
                    parseMediaPayload(msg.content).mime_type || 'application/octet-stream',
                    documentFileName,
                )
                return
            }

            const targetUserId = selectedUserId.value
                ?? (msg.sender_id === currentUserId ? msg.receiver_id : msg.sender_id)

            if (!targetUserId) return

            await backgroundStartDocumentDownload({
                messageId: msg.id,
                userId: targetUserId,
                fileId,
                fileName: documentFileName,
                mimeType: parseMediaPayload(msg.content).mime_type || 'application/octet-stream',
            })
            return
        }

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
                if (isDocument) {
                    const objectUrl = URL.createObjectURL(blob)
                    await putCachedAttachmentBlob(fileId, blob)
                    setCachedMediaUrl(fileId, objectUrl)
                    targetMsg.local_blob_url = objectUrl
                    return
                }
                await putCachedAttachmentBlob(fileId, blob)
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
            if (isDocument) {
                const objectUrl = URL.createObjectURL(combinedBlob)
                await putCachedAttachmentBlob(fileId, combinedBlob)
                setCachedMediaUrl(fileId, objectUrl)
                targetMsg.local_blob_url = objectUrl
                return
            }
            await putCachedAttachmentBlob(fileId, combinedBlob)
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
                    const targetSize = CHAT_MEDIA_PERSISTED_THUMBNAIL_MAX_EDGE
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
                        thumbnailDataUrl: canvas.toDataURL('image/jpeg', CHAT_MEDIA_PERSISTED_THUMBNAIL_QUALITY),
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

    async function handleMediaUploadWrapper(
        file: File,
        albumId?: string | null,
        albumIndex?: number,
        albumSize?: number,
        options: { sendAsDocument?: boolean } = {}
    ) {
        if (!file) return

        const sendAsDocument = options.sendAsDocument === true
        const isVideo = file.type.startsWith('video/')
        const isAudio = file.type.startsWith('audio/')
        
        let msgType: 'video' | 'image' | 'voice' | 'document' = 'image'
        if (sendAsDocument) msgType = 'document'
        else if (isVideo) msgType = 'video'
        else if (isAudio) msgType = 'voice'

        const normalizedAlbumId = (!sendAsDocument && (msgType === 'image' || msgType === 'video')) ? albumId : null
        const normalizedAlbumIndex = typeof albumIndex === 'number' ? albumIndex : 0
        const normalizedAlbumSize = normalizedAlbumId ? Math.max(albumSize ?? 0, 1) : 0
        const preprocessBatchSize = normalizedAlbumSize || 1
        const preprocessLimit = getAdaptivePreprocessLimit(preprocessBatchSize, msgType)
        // NOTE: uploadLimit previously gated XHR uploads in this composable.
        // Background service now owns the upload concurrency; kept here only
        // so the adaptive helper reference stays exercised.
        void getAdaptiveUploadLimit(preprocessBatchSize, msgType)

        if (!selectedUserId.value) return
        // Capture the receiver id at submit time. After this point we never
        // read `selectedUserId.value` again for this upload — the user may
        // navigate away, and the upload must still land on the correct chat.
        const capturedReceiverId = selectedUserId.value

        if ((isVideo || isAudio || sendAsDocument) && file.size > CHAT_MEDIA_MAX_UPLOAD_BYTES) {
            const tooLargeMessage = buildUploadTooLargeMessage(file.size)
            alert(tooLargeMessage)
            return
        }

        activeUploadsCount++
        isUploading.value = activeUploadsCount > 0
        let step = 'start'
        const processingAbortController = new AbortController()
        const documentPayload = {
            file_name: file.name || 'file',
            mime_type: file.type || 'application/octet-stream',
            size: file.size,
        }

        const optimisticId = createOptimisticUploadId()
        const localUrl = URL.createObjectURL(file)
        const initialContent = appendAlbumMetadata(
            sendAsDocument
                ? {
                    placeholder: true,
                    ...documentPayload,
                }
                : {
                    placeholder: true,
                    durationMs: (file as any).durationMs,
                },
            msgType,
            normalizedAlbumId,
            normalizedAlbumIndex,
        )
        const optimisticMsg: Message = {
            id: optimisticId,
            sender_id: currentUserId,
            receiver_id: capturedReceiverId,
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

        messages.value.push(optimisticMsg)

        let isCancelledLocally = false;
        preprocessingAborts.set(optimisticId, () => {
            isCancelledLocally = true
            processingAbortController.abort()
            const index = messages.value.findIndex(m => m.id === optimisticId)
            if (index !== -1) messages.value.splice(index, 1)
            preprocessingAborts.delete(optimisticId)
        })

        const getOptimisticTarget = () => messages.value.find(m => m.id === optimisticId) || optimisticMsg;

        await nextTick()
        scrollToBottom()
        await waitForNextPaint()

        try {
            let sourceFile = file
            if (!sendAsDocument && !isVideo && !isAudio) {
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

            if (sendAsDocument) {
                step = 'document_passthrough'
                trackPreprocessEvent({
                    mediaType: isVideo ? 'video' : isAudio ? 'voice' : 'image',
                    path: 'document_passthrough',
                    status: 'success',
                    startedAt: performance.now(),
                    batchSize: preprocessBatchSize,
                    schedulerLimit: preprocessLimit,
                    usedWorker: false,
                })
            } else if (isVideo) {
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

                if (isEditedImageUploadFile(sourceFile)) {
                    step = 'edited_image_passthrough'
                    const editedImageStartedAt = performance.now()

                    try {
                        const dimensions = await runAdaptivePreprocessTask(preprocessLimit, () =>
                            readImageDimensions(sourceFile)
                        )
                        finalWidth = dimensions.width
                        finalHeight = dimensions.height
                        thumbBase64 = await runAdaptivePreprocessTask(preprocessLimit, () =>
                            generateImageThumbnailDataUrl(sourceFile)
                        )
                        uploadFile = sourceFile

                        trackPreprocessEvent({
                            mediaType: 'image',
                            path: 'image_edited_passthrough',
                            status: 'success',
                            startedAt: editedImageStartedAt,
                            batchSize: preprocessBatchSize,
                            schedulerLimit: preprocessLimit,
                            usedWorker: false,
                            width: finalWidth,
                            height: finalHeight,
                        })
                    } catch (editedWarn) {
                        trackPreprocessEvent({
                            mediaType: 'image',
                            path: 'image_edited_passthrough',
                            status: isCancelledLocally ? 'cancelled' : 'failed',
                            startedAt: editedImageStartedAt,
                            batchSize: preprocessBatchSize,
                            schedulerLimit: preprocessLimit,
                            usedWorker: false,
                            errorMessage: editedWarn instanceof Error ? editedWarn.message : String(editedWarn),
                        })
                        if (isCancelledLocally) throw new Error('UploadCancelled')
                        throw editedWarn
                    }
                } else {
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
            const optimisticContent: any = appendAlbumMetadata(
                sendAsDocument
                    ? {
                        ...documentPayload,
                    }
                    : {
                        thumbnail: thumbBase64,
                    },
                msgType,
                normalizedAlbumId,
                normalizedAlbumIndex,
            );
            if (!sendAsDocument && finalWidth && finalHeight) {
                optimisticContent.width = finalWidth;
                optimisticContent.height = finalHeight;
            }
            if (isVideo) optimisticContent.placeholder = true;
            targetMsg.content = JSON.stringify(optimisticContent);

            step = 'prepare_form'
            if (uploadFile.size > CHAT_MEDIA_MAX_UPLOAD_BYTES) {
                throw new Error(buildUploadTooLargeMessage(uploadFile.size))
            }

            if (processingAbortController.signal.aborted || isCancelledLocally) {
                throw new Error('UploadCancelled')
            }

            step = 'submit_to_service'
            // Hand off ownership of the upload to the module-level background
            // service. The service:
            //   - persists the pending upload (including the preprocessed
            //     Blob) to IndexedDB so it survives page reload
            //   - runs the XHR to /api/chat/upload-media with progress events
            //   - posts /api/chat/send using the *captured* receiver id
            //     (which cannot be invalidated by the user navigating away)
            //   - orchestrates album batching so siblings are sent together
            //
            // From this point on, cancellation must go through the service
            // (see `cancelUpload` above which delegates via backgroundCancelUpload).
            preprocessingAborts.delete(optimisticId)
            const finalLocalUrl = getOptimisticTarget()?.local_blob_url || localUrl
            await backgroundSubmitUpload({
                optimisticId,
                userId: capturedReceiverId,
                senderId: currentUserId,
                msgType,
                file: uploadFile,
                fileName: sourceFile.name,
                mimeType: sourceFile.type || 'application/octet-stream',
                thumbnail: thumbBase64,
                width: finalWidth,
                height: finalHeight,
                durationMs: (file as any).durationMs,
                albumId: normalizedAlbumId ?? null,
                albumIndex: normalizedAlbumIndex,
                albumSize: normalizedAlbumSize || 1,
                localBlobUrl: finalLocalUrl,
            })

            // Seed the local image cache with the preprocessed blob so the
            // current UI does not need to re-download the file after the
            // service's 'sent' event replaces the optimistic message.
            // NOTE: we don't yet know the server-assigned file_id here; the
            // 'uploaded' event from the service will fill that in via the
            // subscription handler below.
        } catch (e: any) {
            if (e.message === 'UploadCancelled') {
                console.log('Upload was cancelled explicitly.');
                preprocessingAborts.delete(optimisticId)
                return; // Early return to avoid error UI
            }
            console.error(`Upload error at step [${step}]:`, e);
            const errString = e && e.message ? e.message : JSON.stringify(e);
            error.value = `خطا در پردازش: ` + errString;
            optimisticMsg.is_error = true;
            optimisticMsg.is_sending = false;
            preprocessingAborts.delete(optimisticId)
        } finally {
            activeUploadsCount--
            isUploading.value = activeUploadsCount > 0
        }
    }

    // -------------------------------------------------------------------------
    // Background upload service — subscription bridge
    //
    // The background service continues XHR uploads and /api/chat/send calls
    // across ChatView unmount. This composable's job here is to reflect the
    // service's events back into the currently rendered `messages.value` for
    // the selected conversation.
    // -------------------------------------------------------------------------

    function applyUploadEventToMessages(event: UploadEvent) {
        // Only update the visible message list when the event targets the
        // conversation the user is actually looking at. Any other pending
        // upload continues in the background and will be grafted back onto
        // the messages list next time the user opens that conversation (via
        // `adoptPendingUploadsForUser`).
        if (event.userId !== selectedUserId.value) return

        const msgs = messages.value
        const index = msgs.findIndex(m => m.id === event.optimisticId)

        switch (event.type) {
            case 'added': {
                if (index === -1) {
                    msgs.push(event.message)
                }
                break
            }
            case 'progress': {
                const m = index !== -1 ? msgs[index] : undefined
                if (m) {
                    m.upload_progress = event.progress
                    m.upload_loaded = event.uploadedBytes
                    m.upload_total = event.totalBytes
                }
                break
            }
            case 'uploaded': {
                const m = index !== -1 ? msgs[index] : undefined
                if (m) {
                    m.upload_progress = 100
                    // Upgrade the optimistic message content to include the
                    // server-returned file_id + final dimensions so renderers
                    // that key off `payload.file_id` start to work even before
                    // /chat/send completes.
                    m.content = event.content
                }
                break
            }
            case 'sent': {
                const server = event.serverMessage
                const hydrated: Message = event.localBlobUrl
                    ? { ...server, local_blob_url: event.localBlobUrl }
                    : server

                if (index !== -1) {
                    msgs[index] = hydrated
                } else {
                    msgs.push(hydrated)
                }

                // Seed the media cache so the newly-sent message renders
                // instantly from the local blob instead of re-fetching.
                try {
                    const payload = JSON.parse(server.content || '{}')
                    if (payload.file_id && event.localBlobUrl) {
                        setCachedMediaUrl(payload.file_id, event.localBlobUrl)
                        void persistObjectUrlToAttachmentCache(payload.file_id, event.localBlobUrl)
                    }
                } catch {
                    // non-JSON content — ignore
                }
                break
            }
            case 'error': {
                const m = index !== -1 ? msgs[index] : undefined
                if (m) {
                    m.is_error = true
                    m.is_sending = false
                }
                break
            }
            case 'cancelled': {
                if (index !== -1) {
                    msgs.splice(index, 1)
                }
                break
            }
        }
    }

    const unsubscribeFromUploadService = backgroundSubscribeToUploads(applyUploadEventToMessages)
    onUnmounted(() => {
        try {
            unsubscribeFromUploadService()
        } catch {
            /* ignore */
        }
    })

    function applyDocumentDownloadEventToMessages(event: DocumentDownloadEvent) {
        if (event.userId !== selectedUserId.value) return

        const index = messages.value.findIndex(message => message.id === event.messageId)
        const target = index !== -1 ? messages.value[index] : undefined
        if (!target || target.message_type !== 'document') return

        switch (event.type) {
            case 'added':
            case 'progress': {
                target.is_downloading = true
                target.download_progress = event.progress
                break
            }
            case 'completed': {
                target.is_downloading = false
                target.download_progress = 100
                target.local_blob_url = event.objectUrl
                setCachedMediaUrl(event.fileId, event.objectUrl)
                break
            }
            case 'error':
            case 'cancelled': {
                target.is_downloading = false
                target.download_progress = 0
                break
            }
        }
    }

    const unsubscribeFromDocumentDownloadService = backgroundSubscribeToDocumentDownloads(applyDocumentDownloadEventToMessages)
    onUnmounted(() => {
        try {
            unsubscribeFromDocumentDownloadService()
        } catch {
            /* ignore */
        }
    })

    function adoptDocumentDownloadStateForVisibleMessages(userId: number | null) {
        if (typeof userId !== 'number') return

        const pendingByMessageId = new Map(
            backgroundGetPendingDocumentDownloadsForUser(userId).map(download => [download.messageId, download])
        )

        for (const message of messages.value) {
            if (message.message_type !== 'document') continue

            const pending = pendingByMessageId.get(message.id)
            if (pending) {
                message.is_downloading = true
                message.download_progress = pending.progress
            } else if (message.is_downloading) {
                message.is_downloading = false
                message.download_progress = 0
            }

            const fileId = getFileId(message.content)
            if (!fileId) continue

            const completedUrl = backgroundGetCompletedDocumentDownloadUrl(fileId)
            if (completedUrl) {
                message.local_blob_url = completedUrl
                setCachedMediaUrl(fileId, completedUrl)
                continue
            }

            scheduleDocumentCacheRestore(message)
        }
    }

    watch([selectedUserId, messages], ([userId]) => {
        adoptDocumentDownloadStateForVisibleMessages(userId)
    }, { immediate: true })

    /**
     * Graft any pending background uploads for the given user into the
     * current `messages.value`. Called by `useChatMessages.loadMessages`
     * after server messages have been loaded, so the user sees any
     * in-flight or resumed uploads even after navigating away and back.
     */
    function adoptPendingUploadsForUser(userId: number) {
        const pending = backgroundGetPendingForUser(userId)
        if (pending.length === 0) return

        const existingIds = new Set(messages.value.map(m => m.id))
        for (const upload of pending) {
            if (existingIds.has(upload.id)) continue
            messages.value.push(buildOptimisticMessageFromUpload(upload))
        }
    }

    return {
        cancelUpload,
        cancelDocumentDownload,
        imageCache,
        loadImageForMessage,
        scheduleMediaHydration,
        downloadMedia,
        lightboxMedia,
        handleMediaClick,
        setLightboxIndex,
        closeLightbox,
        handleMediaUploadWrapper,
        adoptPendingUploadsForUser,
    }
}
