import { ref, type Ref, nextTick } from 'vue'
import imageCompression from 'browser-image-compression'
import type { Message } from '../../types/chat'

export interface UseChatMediaOptions {
    apiBaseUrl: string
    jwtToken: string | null
    currentUserId: number
    selectedUserId: Ref<number | null>
    messages: Ref<Message[]>
    error: Ref<string>
    isUploading: Ref<boolean>
    scrollToBottom: () => void
    sendMediaMessage: (type: 'image' | 'video' | 'sticker', content: string, localBlobUrl?: string) => Promise<void>
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
                const tx = db.transaction(STORE_NAME, 'readwrite')
                tx.objectStore(STORE_NAME).put(blob, key)
                tx.oncomplete = () => resolve()
                tx.onerror = () => resolve()
            })
        } catch { /* ignore */ }
    }

    async function loadImageForMessage(content: string): Promise<void> {
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

    function handleMediaClick(msg: Message) {
        const fileId = getFileId(msg.content);
        const cacheUrl = imageCache.value[fileId];
        const url = msg.local_blob_url || cacheUrl;

        if (url) {
            lightboxMedia.value = {
                url,
                type: msg.message_type === 'video' ? 'video' : 'image'
            };
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
                URL.revokeObjectURL(video.src)
            }
            video.onerror = (e) => reject(e)
        })
    }

    async function handleMediaUploadWrapper(file: File) {
        if (!file) return

        const isVideo = file.type.startsWith('video/')
        if (!selectedUserId.value) return

        isUploading.value = true
        let step = 'start'

        const optimisticId = -Date.now()
        const localUrl = URL.createObjectURL(file)
        const optimisticMsg: Message = {
            id: optimisticId,
            sender_id: currentUserId,
            receiver_id: selectedUserId.value,
            content: JSON.stringify({ placeholder: true }),
            message_type: isVideo ? 'video' : 'image',
            is_read: true,
            is_sending: true,
            upload_progress: 0,
            local_blob_url: localUrl,
            created_at: new Date().toISOString()
        }
        messages.value.push(optimisticMsg)

        const getOptimisticTarget = () => messages.value.find(m => m.id === optimisticId) || optimisticMsg;

        await nextTick()
        scrollToBottom()

        try {
            let uploadFile = file;
            let thumbBase64 = '';

            if (isVideo) {
                step = 'video_thumb'
                try {
                    thumbBase64 = await generateVideoThumbnail(file)
                } catch (warn) {
                    console.warn("Video thumbnail failed:", warn)
                }
            } else {
                step = 'compress_main'
                try {
                    const options = { maxSizeMB: 0.5, maxWidthOrHeight: 1280, useWebWorker: false }
                    uploadFile = await imageCompression(file, options)
                } catch (warn) {
                    console.warn("Image compression failed, using original:", warn)
                }

                step = 'compress_thumb'
                try {
                    const thumbOptions = { maxSizeMB: 0.05, maxWidthOrHeight: 20, useWebWorker: false }
                    const thumbFile = await imageCompression(file, thumbOptions)
                    thumbBase64 = await new Promise<string>((resolve, reject) => {
                        const reader = new FileReader()
                        reader.onloadend = () => resolve(reader.result as string)
                        reader.onerror = (e) => reject(e)
                        reader.readAsDataURL(thumbFile)
                    })
                } catch (warn) {
                    console.warn("Image thumbnail generation failed:", warn)
                }
            }

            const targetMsg = getOptimisticTarget();
            targetMsg.content = JSON.stringify({ thumbnail: thumbBase64 })

            step = 'prepare_form'
            const formData = new FormData()
            formData.append('file', uploadFile, file.name)
            formData.append('thumbnail', thumbBase64)

            step = 'xhr_upload'
            const data = await new Promise<any>((resolve, reject) => {
                const xhr = new XMLHttpRequest()
                xhr.open('POST', `${apiBaseUrl}/api/chat/upload-image`)
                xhr.setRequestHeader('Authorization', `Bearer ${jwtToken}`)

                xhr.upload.onprogress = (e) => {
                    if (e.lengthComputable) {
                        const target = getOptimisticTarget();
                        target.upload_progress = Math.round((e.loaded / e.total) * 100)
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
                        reject(new Error(`مشکل سرور (${xhr.status}): ${xhr.responseText.substring(0, 100)}`))
                    }
                }
                xhr.onerror = () => reject(new Error("Network Error"))
                xhr.send(formData)
            })

            step = 'prepare_json'
            const messageContent = JSON.stringify({
                file_id: data.file_id,
                thumbnail: data.thumbnail
            })

            step = 'save_local_cache'
            await saveToDB(data.file_id, uploadFile)
            imageCache.value = { ...imageCache.value, [data.file_id]: localUrl }

            step = 'send_ws_message'
            await sendMediaMessage(isVideo ? 'video' : 'image', messageContent, localUrl)

        } catch (e: any) {
            console.error(`Upload error at step [${step}]:`, e);
            const errString = e && e.message ? e.message : JSON.stringify(e);
            error.value = `[${step}] ${errString}`;
            alert(`خطا در آپلود: ` + errString);
            optimisticMsg.is_error = true;
        } finally {
            isUploading.value = false
            if (!optimisticMsg.is_error) {
                messages.value = messages.value.filter(m => m.id !== optimisticId)
            }
        }
    }

    return {
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
