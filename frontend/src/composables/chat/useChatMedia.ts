import { ref, type Ref, nextTick } from 'vue'
import PhotoSwipeLightbox from 'photoswipe/lightbox'
import 'photoswipe/style.css'
import type { Message } from '../../types/chat'
import { processImageInWorker } from '../../utils/imagePreprocessClient'

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

type LightboxItem = {
    src: string
    msrc: string
    msgId: number
    element?: HTMLImageElement
    w: number
    h: number
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
    const uploadControllers = new Map<number, { abort: () => void }>()
    const albumBatches = new Map<string, PendingAlbumBatch>()

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
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION)
            req.onupgradeneeded = (e) => {
                const db = (e.target as IDBOpenDBRequest).result
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME)
                }
            }
            req.onsuccess = () => resolve(req.result)
            req.onerror = () => reject(req.error)
        })
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

    async function loadImageForMessage(content: string, type?: string): Promise<void> {
        if (!content || !content.startsWith('{')) return
        let fileId = ''
        try {
            const parsed = JSON.parse(content)
            fileId = parsed.file_id
        } catch { return }
        if (!fileId || imageCache.value[fileId]) return // already loaded

        // 1. Check IndexedDB first
        const cached = await getFromDB(fileId)
        if (cached) {
            imageCache.value = { ...imageCache.value, [fileId]: URL.createObjectURL(cached) }
            return
        }

        // For voice messages, we auto-download if it's not in cache
        // but image/video might still require manual download click.
        // We'll allow auto-downloading voice here.
        if (type !== 'voice' && type !== 'sticker') {
            // Stickers are small, voice are small enough. 
            // Images/Videos are large, we only load them if they are in cache,
            // otherwise the user must click Download.
            return 
        }

        // 2. Fetch from server
        try {
            const res = await fetch(`${apiBaseUrl}/api/chat/files/${fileId}?token=${jwtToken}`)
            if (!res.ok) return
            const blob = await res.blob()
            await saveToDB(fileId, blob)
            imageCache.value = { ...imageCache.value, [fileId]: URL.createObjectURL(blob) }
        } catch { /* silently fail */ }
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

    function getRenderedImageElement(messageId: number): HTMLImageElement | null {
        return document.querySelector(`img[data-media-msg-id="${messageId}"]`) as HTMLImageElement | null
    }

    function loadImageDimensions(src: string): Promise<{ width: number; height: number }> {
        return new Promise((resolve) => {
            const img = new Image()
            img.onload = () => {
                resolve({
                    width: Math.max(1, img.naturalWidth || 0),
                    height: Math.max(1, img.naturalHeight || 0)
                })
            }
            img.onerror = () => resolve({ width: 0, height: 0 })
            img.src = src
        })
    }

    async function resolveLightboxImageData(messageId: number, src: string, fallbackWidth = 0, fallbackHeight = 0) {
        const element = getRenderedImageElement(messageId)
        const resolvedSrc = element?.currentSrc || element?.src || src

        if (element?.naturalWidth && element?.naturalHeight) {
            return {
                src: resolvedSrc,
                element,
                width: element.naturalWidth,
                height: element.naturalHeight,
            }
        }

        const loaded = await loadImageDimensions(resolvedSrc)
        return {
            src: resolvedSrc,
            element: element ?? undefined,
            width: loaded.width || fallbackWidth || 1,
            height: loaded.height || fallbackHeight || 1,
        }
    }

    async function buildLightboxItem(msg: Message): Promise<LightboxItem | null> {
        const payload = parseMediaPayload(msg.content)
        const fileId = typeof payload.file_id === 'string' ? payload.file_id : getFileId(msg.content)
        const fallbackSrc = msg.local_blob_url || imageCache.value[fileId] || payload.thumbnail || ''

        if (!fallbackSrc) return null

        const fallbackWidth = Number(payload.width) || 0
        const fallbackHeight = Number(payload.height) || 0
        const resolved = await resolveLightboxImageData(msg.id, fallbackSrc, fallbackWidth, fallbackHeight)

        return {
            src: resolved.src,
            msrc: payload.thumbnail || resolved.src,
            msgId: msg.id,
            element: resolved.element,
            w: resolved.width,
            h: resolved.height,
        }
    }

    async function syncLightboxContentSize(content: any) {
        if (content.type !== 'image' || !content.data?.src) return

        const resolved = await resolveLightboxImageData(
            Number(content.data.msgId),
            content.data.src,
            Number(content.data.w) || 0,
            Number(content.data.h) || 0,
        )

        const sizeChanged = resolved.width !== content.data.w || resolved.height !== content.data.h
        const srcChanged = resolved.src !== content.data.src

        if (!sizeChanged && !srcChanged) return

        content.data.src = resolved.src
        content.data.element = resolved.element
        content.data.w = resolved.width
        content.data.h = resolved.height

        content.width = resolved.width
        content.height = resolved.height

        if (content.element instanceof HTMLImageElement && content.element.src !== resolved.src) {
            content.element.src = resolved.src
        }

        if (content.slide) {
            content.slide.width = resolved.width
            content.slide.height = resolved.height
            if (typeof content.slide.updateContentSize === 'function') {
                content.slide.updateContentSize(true)
            }
        }
    }

    function openCachedImage(fileId: string) {
        const url = imageCache.value[fileId]
        if (url) window.open(url, '_blank')
    }

    // === Media Download ===
    async function downloadMedia(msg: Message) {
        const fileId = getFileId(msg.content);
        if (!fileId) return;

        // Get reactive proxy of the message
        const targetMsg = messages.value.find(m => m.id === msg.id) || msg;
        targetMsg.is_downloading = true;
        targetMsg.download_progress = 0;

        try {
            const res = await fetch(`${apiBaseUrl}/api/chat/files/${fileId}?token=${jwtToken}`);
            if (!res.ok) throw new Error("Download failed");

            const contentType = res.headers.get('content-type') || 'application/octet-stream';
            const contentLength = res.headers.get('content-length');
            const total = contentLength ? parseInt(contentLength, 10) : 0;

            if (!total || !res.body) {
                const blob = await res.blob();
                await saveToDB(fileId, blob);
                imageCache.value = { ...imageCache.value, [fileId]: URL.createObjectURL(blob) };
                return;
            }

            const reader = res.body.getReader();
            const chunks: Uint8Array[] = [];
            let received = 0;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                if (value) {
                    chunks.push(value);
                    received += value.length;
                    targetMsg.download_progress = Math.round((received / total) * 100);
                }
            }

            const combinedBlob = new Blob(chunks as BlobPart[], { type: contentType });
            await saveToDB(fileId, combinedBlob);

            // Update cache with the new blob URL
            const newUrl = URL.createObjectURL(combinedBlob);
            imageCache.value = { ...imageCache.value, [fileId]: newUrl };

        } catch (e) {
            console.error("Download failed:", e);
            alert("خطا در دانلود فایل");
        } finally {
            targetMsg.is_downloading = false;
        }
    }

    // === Lightbox State ===
    const lightboxMedia = ref<{ url: string, type: 'image' | 'video' } | null>(null);

    async function handleMediaClick(msg: Message) {
        const fileId = getFileId(msg.content);
        const cacheUrl = imageCache.value[fileId];
        const url = msg.local_blob_url || cacheUrl;

        if (url) {
            if (msg.message_type === 'image') {
                // Collect all images in current chat for the gallery
                const imageMessages = messages.value.filter(m => m.message_type === 'image');

                const dataSource = (await Promise.all(imageMessages.map(buildLightboxItem)))
                    .filter((item): item is LightboxItem => !!item);

                if (dataSource.length === 0) {
                    return;
                }
                
                const startIndex = dataSource.findIndex(item => item.msgId === msg.id);

                const lightbox = new PhotoSwipeLightbox({
                    dataSource,
                    index: Math.max(0, startIndex),
                    pswpModule: () => import('photoswipe'),
                    bgOpacity: 0.9,
                    wheelToZoom: true,
                    arrowPrev: false,
                    arrowNext: false,
                    counter: false,
                });

                lightbox.on('uiRegister', function() {
                    // Download Button
                    lightbox.pswp?.ui?.registerElement({
                        name: 'download-button',
                        order: 8,
                        isButton: true,
                        tagName: 'button',
                        html: '<svg width="32" height="32" viewBox="0 0 32 32" fill="none"><path d="M20.5 14L16 18.5L11.5 14M16 8V18.5M8 22H24" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                        onClick: (event: any, el: any, pswp: any) => {
                            const currSlide = pswp.currSlide;
                            if (!currSlide || !currSlide.data.src) return;
                            const link = document.createElement('a');
                            link.href = currSlide.data.src;
                            link.download = `media_${currSlide.data.msgId}.jpg`;
                            document.body.appendChild(link);
                            link.click();
                            document.body.removeChild(link);
                        }
                    });

                    // Thumbnails Carousel
                    lightbox.pswp?.ui?.registerElement({
                        name: 'thumbnails-carousel',
                        order: 9,
                        isButton: false,
                        appendTo: 'wrapper',
                        html: '<div class="pswp-thumbnails" style="position:absolute; bottom:20px; width:100%; display:flex; justify-content:center; gap:8px; overflow-x:auto; padding: 0 20px; z-index:10000;"></div>',
                        onInit: (el: any, pswp: any) => {
                            const container = el.querySelector('.pswp-thumbnails');
                            dataSource.forEach((item, i) => {
                               const img = document.createElement('img');
                               img.src = item.msrc || item.src || '';
                               img.style.height = '48px';
                               img.style.width = '48px';
                               img.style.objectFit = 'cover';
                               img.style.borderRadius = '4px';
                               img.style.cursor = 'pointer';
                               img.style.opacity = i === pswp.currIndex ? '1' : '0.5';
                               img.style.transition = 'opacity 0.2s';
                               img.onclick = () => pswp.goTo(i);
                               container.appendChild(img);
                            });

                            pswp.on('change', () => {
                                const imgs = container.querySelectorAll('img');
                                imgs.forEach((img: any, idx: number) => {
                                    img.style.opacity = idx === pswp.currIndex ? '1' : '0.5';
                                    if (idx === pswp.currIndex) {
                                        img.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
                                    }
                                });
                            });
                        }
                    });
                });

                lightbox.on('contentLoad', (e) => {
                    void syncLightboxContentSize(e.content);
                });

                lightbox.init();
                lightbox.loadAndOpen(Math.max(0, startIndex));
            } else {
                lightboxMedia.value = {
                    url,
                    type: 'video'
                };
            }
        }
    }

    function closeLightbox() {
        lightboxMedia.value = null;
    }

    // === Media Upload ===
    async function generateVideoThumbnail(file: File): Promise<string> {
        return new Promise((resolve, reject) => {
            const video = document.createElement('video')
            video.preload = 'metadata'
            video.src = URL.createObjectURL(file)
            video.muted = true
            video.playsInline = true

            const fallbackTimeout = setTimeout(() => {
                console.warn("Video thumbnail generation timed out after 3s.");
                resolve('');
            }, 3000);


            video.onloadeddata = () => {
                video.currentTime = 0.1
            }

            video.onseeked = () => {
                const canvas = document.createElement('canvas')
                const targetSize = 20
                const scale = Math.min(targetSize / (video.videoWidth || 1), targetSize / (video.videoHeight || 1))
                canvas.width = Math.max(1, (video.videoWidth || 1) * scale)
                canvas.height = Math.max(1, (video.videoHeight || 1) * scale)
                const ctx = canvas.getContext('2d')
                if (ctx) {
                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
                    resolve(canvas.toDataURL('image/jpeg', 0.5))
                } else {
                    resolve('')
                }
                clearTimeout(fallbackTimeout);
                URL.revokeObjectURL(video.src)
            }
            video.onerror = (e) => {
                clearTimeout(fallbackTimeout);
                reject(e);
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
        
        if (!selectedUserId.value) return

        activeUploadsCount++
        isUploading.value = activeUploadsCount > 0
        let step = 'start'
        const processingAbortController = new AbortController()

        const optimisticId = -Date.now()
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
            let uploadFile: File | Blob = file;
            let thumbBase64 = '';

            let finalWidth = 0;
            let finalHeight = 0;

            if (isVideo) {
                step = 'video_thumb'
                try {
                    thumbBase64 = await generateVideoThumbnail(file)
                    getOptimisticTarget().content = JSON.stringify(
                        appendAlbumMetadata({ thumbnail: thumbBase64, placeholder: true }, msgType, normalizedAlbumId, normalizedAlbumIndex)
                    )
                } catch (warn) {
                    console.warn("Video thumbnail failed:", warn)
                }
            } else if (isAudio) {
                step = 'skip_audio_thumb'
                // No thumbnail processing for voice
            } else {
                if (isCancelledLocally) throw new Error('UploadCancelled');

                try {
                    step = 'worker_preprocess'
                    const processed = await processImageInWorker(file, processingAbortController.signal)

                    uploadFile = processed.blob
                    finalWidth = processed.width
                    finalHeight = processed.height
                    thumbBase64 = processed.thumbnailDataUrl
                } catch (workerWarn) {
                    if (isCancelledLocally) throw new Error('UploadCancelled');

                    console.warn("Image worker preprocessing failed, using main thread fallback:", workerWarn)

                    step = 'compress_main'
                    try {
                        const compressed = await nativeImageCompress(file, 1920, 0.85);
                        uploadFile = compressed.blob;
                        finalWidth = compressed.width;
                        finalHeight = compressed.height;
                    } catch (warn) {
                        console.warn("Image compression failed, using original:", warn)
                        try {
                            const original = await nativeImageCompress(file, 9999, 1.0);
                            finalWidth = original.width;
                            finalHeight = original.height;
                        } catch (error) {
                            console.warn("Original image dimension fallback failed:", error)
                        }
                    }

                    if (isCancelledLocally) throw new Error('UploadCancelled');
                    step = 'compress_thumb'
                    try {
                        thumbBase64 = await generateImageThumbnailDataUrl(uploadFile)
                    } catch (warn) {
                        console.warn("Image thumbnail generation failed:", warn)
                    }
                }

                if (isCancelledLocally) throw new Error('UploadCancelled');

                const rotatedUrl = URL.createObjectURL(uploadFile);
                getOptimisticTarget().local_blob_url = rotatedUrl;
            }

            if (msgType === 'video') {
                try {
                    await new Promise<void>((resolve) => {
                        const rotatedUrl = URL.createObjectURL(uploadFile);
                        getOptimisticTarget().local_blob_url = rotatedUrl; // Update UI with video immediately
                        const video = document.createElement('video');
                        video.onloadedmetadata = () => {
                            finalWidth = video.videoWidth;
                            finalHeight = video.videoHeight;
                            resolve();
                        };
                        video.onerror = () => resolve();
                        video.src = rotatedUrl;
                    });
                } catch (e) {
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
            const formData = new FormData()
            formData.append('file', uploadFile, file.name)
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
                imageCache.value = { ...imageCache.value, [data.file_id]: finalLocalUrl }
            } else {
                imageCache.value = { ...imageCache.value, [data.file_id]: finalLocalUrl }
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
                await flushAlbumBatchIfReady(normalizedAlbumId)
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
            error.value = `[${step}] ${errString}`;
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
        openCachedImage,
        downloadMedia,
        lightboxMedia,
        handleMediaClick,
        closeLightbox,
        handleMediaUploadWrapper
    }
}
