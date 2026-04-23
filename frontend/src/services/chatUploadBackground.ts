/**
 * Chat Upload Background Service
 *
 * Module-level singleton that owns the lifecycle of chat media uploads.
 * Unlike a Vue composable, this service's state survives navigation/unmount
 * of the `ChatView` component, so in-flight uploads continue to completion
 * even after the user leaves the messenger.
 *
 * Responsibilities:
 *  - Queue + dispatch XHR upload to `/api/chat/upload-media`
 *  - Post to `/api/chat/send` with the captured receiver id (NOT reading
 *    `selectedUserId.value` — which may have been cleared when the user
 *    navigated away)
 *  - Album batching: collect sibling uploads by `album_id`, and only trigger
 *    the per-message `/chat/send` calls once all album members finish uploading
 *  - IndexedDB persistence of pending uploads (including raw File blobs),
 *    so a page reload or app close does not lose in-progress sends
 *  - Resume-on-init: on app mount, restore unfinished uploads from IDB and
 *    re-enter the pipeline at the correct phase
 *  - Event emission for any subscribed chat UI (`useChatMedia`) to update
 *    the visible optimistic messages in real time
 *
 * This is Phase 1 of the upload reliability plan. It does NOT yet implement
 * chunked resumable uploads (Phase 2) or Service Worker Background Fetch
 * (Phase 3) — uploads are still monolithic XHR requests, so a complete tab
 * close mid-upload will still drop that upload on non-Android-Chrome platforms.
 */

import type { Message } from '../types/chat'

// -----------------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------------

export type UploadMsgType = 'image' | 'video' | 'voice'

export type UploadPhase =
    | 'queued'
    | 'uploading'
    | 'uploaded'
    | 'sending'
    | 'sent'
    | 'failed'
    | 'cancelled'

export interface PendingUpload {
    id: number // optimistic id (negative)
    userId: number // receiver id (captured at submit time)
    senderId: number
    msgType: UploadMsgType
    file: Blob // preprocessed blob — IndexedDB can store Blobs/Files
    fileName: string
    thumbnail: string // base64 data URL
    width: number
    height: number
    durationMs?: number
    albumId: string | null
    albumIndex: number
    albumSize: number
    phase: UploadPhase
    progress: number
    uploadedBytes: number
    totalBytes: number
    fileId?: string // server-returned after upload-media
    serverThumbnail?: string
    errorMessage?: string
    createdAt: string // ISO timestamp
    localBlobUrl?: string // UI-only, not persisted
    retryCount?: number // number of transient retries attempted (upload-media)
    sendRetryCount?: number // number of transient retries attempted (/chat/send)
}

export interface SubmitUploadParams {
    optimisticId: number
    userId: number
    senderId: number
    msgType: UploadMsgType
    file: Blob
    fileName: string
    thumbnail: string
    width: number
    height: number
    durationMs?: number
    albumId: string | null
    albumIndex: number
    albumSize: number
    localBlobUrl?: string
}

export type UploadEvent =
    | { type: 'added'; userId: number; optimisticId: number; message: Message }
    | {
          type: 'progress'
          userId: number
          optimisticId: number
          progress: number
          uploadedBytes: number
          totalBytes: number
      }
    | {
          type: 'uploaded'
          userId: number
          optimisticId: number
          fileId: string
          content: string
      }
    | {
          type: 'sent'
          userId: number
          optimisticId: number
          serverMessage: Message
          localBlobUrl?: string
      }
    | { type: 'error'; userId: number; optimisticId: number; errorMessage: string }
    | { type: 'cancelled'; userId: number; optimisticId: number }

export type UploadEventHandler = (event: UploadEvent) => void

interface ServiceConfig {
    apiBaseUrl: string
    getAuthToken: () => string | null
}

interface AlbumBatchState {
    albumId: string
    userId: number
    expectedCount: number
    optimisticIds: Set<number>
    flushing: boolean
}

// -----------------------------------------------------------------------------
// Module-level state (survives ChatView unmount)
// -----------------------------------------------------------------------------

let config: ServiceConfig | null = null
let initialized = false
let resumePromise: Promise<void> | null = null

const pendingUploads = new Map<number, PendingUpload>()
const xhrControllers = new Map<number, XMLHttpRequest>()
const abortFlags = new Set<number>()
const albumBatches = new Map<string, AlbumBatchState>()
const subscribers = new Set<UploadEventHandler>()

// -----------------------------------------------------------------------------
// Concurrency gate
//
// Browsers cap concurrent HTTP requests per-origin at ~6. If we fire an XHR
// per media item (e.g. a 15-image album), those XHRs saturate the connection
// pool and block ALL other `/api/*` traffic — `/api/chat/send`, `/api/chat/poll`,
// `/api/offers`, `/api/commodities`, etc. This is what made the rest of the app
// feel slow while an album was uploading.
//
// We intentionally cap concurrent upload-media requests to a small number so
// unrelated API calls (including text-message sends to OTHER conversations)
// always have connection slots available. Extra uploads sit in `uploadQueue`
// and drain FIFO as slots free up.
// -----------------------------------------------------------------------------

const MAX_CONCURRENT_UPLOADS = 2
const MAX_UPLOAD_RETRIES = 3 // initial attempt + 3 retries
// Hard cap on a single XHR upload. Protects against half-dead connections
// where bytes are uploaded (progress hits 100%) but the response never
// arrives — without this the XHR hangs forever, `phase='uploading'` is
// never cleared, and `flushAlbumBatchIfReady` blocks on `hasStillUploading`
// for the entire album — preventing ANY of the album's `/chat/send` calls
// from ever firing. Reproduced in the field: 13-image album never sent
// even after several hours despite all progress circles showing complete.
const XHR_UPLOAD_TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes per file
let activeUploadCount = 0
const uploadQueue: PendingUpload[] = []

function enqueueUpload(upload: PendingUpload): void {
    // Avoid duplicates: if the upload is already queued or in-flight, ignore.
    if (uploadQueue.some((u) => u.id === upload.id)) return
    if (xhrControllers.has(upload.id)) return
    uploadQueue.push(upload)
    pumpUploadQueue()
}

function pumpUploadQueue(): void {
    while (activeUploadCount < MAX_CONCURRENT_UPLOADS && uploadQueue.length > 0) {
        const next = uploadQueue.shift()
        if (!next) break
        if (abortFlags.has(next.id)) continue
        if (!pendingUploads.has(next.id)) continue
        activeUploadCount++
        void runUploadWithGate(next)
    }
}

async function runUploadWithGate(upload: PendingUpload): Promise<void> {
    try {
        await runUpload(upload)
    } finally {
        activeUploadCount = Math.max(0, activeUploadCount - 1)
        pumpUploadQueue()
        // Safety net: when everything has drained (no active uploads AND no
        // queued uploads), force-check every album batch for stalled items.
        // This recovers from two edge cases that would otherwise leave an
        // album permanently stuck with no `/chat/send` dispatches:
        //   (1) A preprocessing error silently dropped one of the N files
        //       before `submitUpload` was called, so `optimisticIds.size`
        //       stays < `expectedCount` forever.
        //   (2) A sibling album's flush loop was racing and every previous
        //       flush check hit `hasStillUploading` true before the last
        //       `runUpload` settled.
        if (activeUploadCount === 0 && uploadQueue.length === 0) {
            void forceFlushStalledAlbums()
        }
    }
}

function isTransientUploadError(error: unknown): boolean {
    const msg = error instanceof Error ? error.message : String(error)
    if (!msg) return false
    // Treat browser-level network failures and common 5xx/timeout-ish server
    // errors as retryable. 4xx (except timeouts) are surfaced as hard failures.
    if (/network error/i.test(msg)) return true
    // fetch() throws `TypeError: Failed to fetch` (Chromium/Safari/Firefox)
    // or `NetworkError when attempting to fetch resource` (Firefox) on
    // transient connectivity hiccups. These occur when the browser's
    // per-origin pool is saturated (lots of concurrent XHR uploads) and a
    // fetch() call races into a closed keep-alive socket.
    if (/failed to fetch/i.test(msg)) return true
    if (/networkerror/i.test(msg)) return true
    if (/load failed/i.test(msg)) return true // Safari
    if (/connection was lost/i.test(msg)) return true
    if (/\b(502|503|504|520|521|522|524|408)\b/.test(msg)) return true
    if (/خطای سرور \((5\d\d|408)\)/.test(msg)) return true
    if (/خطای ارسال \((5\d\d|408)\)/.test(msg)) return true
    return false
}

const MAX_SEND_RETRIES = 3

function computeRetryDelayMs(attempt: number): number {
    // Exponential backoff with jitter: ~1s, 2s, 4s (+/- 250ms)
    const base = 1000 * Math.pow(2, Math.max(0, attempt))
    const jitter = Math.floor(Math.random() * 500) - 250
    return Math.max(500, base + jitter)
}

// -----------------------------------------------------------------------------
// IndexedDB
// -----------------------------------------------------------------------------

const DB_NAME = 'chat_upload_queue'
const DB_VERSION = 1
const STORE_NAME = 'pending'

let dbPromise: Promise<IDBDatabase> | null = null

function openDB(): Promise<IDBDatabase> {
    if (dbPromise) return dbPromise

    dbPromise = new Promise((resolve, reject) => {
        try {
            const req = indexedDB.open(DB_NAME, DB_VERSION)
            req.onupgradeneeded = (event) => {
                const db = (event.target as IDBOpenDBRequest).result
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME, { keyPath: 'id' })
                }
            }
            req.onsuccess = () => {
                const db = req.result
                db.onversionchange = () => {
                    db.close()
                    dbPromise = null
                }
                resolve(db)
            }
            req.onerror = () => {
                dbPromise = null
                reject(req.error)
            }
        } catch (error) {
            dbPromise = null
            reject(error)
        }
    })

    return dbPromise
}

async function idbPut(upload: PendingUpload) {
    try {
        const db = await openDB()
        const record: PendingUpload = { ...upload }
        // Do not persist UI-only fields
        delete record.localBlobUrl
        await new Promise<void>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readwrite')
                tx.objectStore(STORE_NAME).put(record)
                tx.oncomplete = () => resolve()
                tx.onerror = () => resolve()
                tx.onabort = () => resolve()
            } catch (e) {
                console.warn('[uploadService] idbPut failed:', e)
                resolve()
            }
        })
    } catch (e) {
        console.warn('[uploadService] idbPut open failed:', e)
    }
}

async function idbDelete(id: number) {
    try {
        const db = await openDB()
        await new Promise<void>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readwrite')
                tx.objectStore(STORE_NAME).delete(id)
                tx.oncomplete = () => resolve()
                tx.onerror = () => resolve()
                tx.onabort = () => resolve()
            } catch {
                resolve()
            }
        })
    } catch {
        /* ignore */
    }
}

async function idbGetAll(): Promise<PendingUpload[]> {
    try {
        const db = await openDB()
        return await new Promise<PendingUpload[]>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readonly')
                const req = tx.objectStore(STORE_NAME).getAll()
                req.onsuccess = () => resolve((req.result as PendingUpload[]) || [])
                req.onerror = () => resolve([])
            } catch {
                resolve([])
            }
        })
    } catch {
        return []
    }
}

// -----------------------------------------------------------------------------
// Event emission
// -----------------------------------------------------------------------------

function emit(event: UploadEvent) {
    for (const handler of subscribers) {
        try {
            handler(event)
        } catch (e) {
            console.warn('[uploadService] subscriber failed:', e)
        }
    }
}

export function subscribeToUploads(handler: UploadEventHandler): () => void {
    subscribers.add(handler)
    return () => subscribers.delete(handler)
}

// -----------------------------------------------------------------------------
// Album batching
// -----------------------------------------------------------------------------

function ensureAlbumBatch(albumId: string, userId: number, expectedCount: number) {
    const existing = albumBatches.get(albumId)
    if (existing) {
        existing.expectedCount = Math.max(existing.expectedCount, expectedCount)
        return existing
    }
    const created: AlbumBatchState = {
        albumId,
        userId,
        expectedCount,
        optimisticIds: new Set(),
        flushing: false,
    }
    albumBatches.set(albumId, created)
    return created
}

function cleanupAlbumBatchIfSettled(albumId: string) {
    const batch = albumBatches.get(albumId)
    if (!batch) return

    const allSettled = Array.from(batch.optimisticIds).every((id) => {
        const upload = pendingUploads.get(id)
        if (!upload) return true // already removed
        return (
            upload.phase === 'sent' ||
            upload.phase === 'cancelled' ||
            upload.phase === 'failed'
        )
    })

    if (allSettled && batch.optimisticIds.size >= batch.expectedCount) {
        albumBatches.delete(albumId)
    }
}

async function flushAlbumBatchIfReady(albumId: string) {
    const batch = albumBatches.get(albumId)
    if (!batch || batch.flushing) return

    if (batch.optimisticIds.size < batch.expectedCount) return

    const ids = Array.from(batch.optimisticIds)
    const hasStillUploading = ids.some((id) => {
        const u = pendingUploads.get(id)
        return u && (u.phase === 'queued' || u.phase === 'uploading')
    })
    if (hasStillUploading) return

    const sendable = ids
        .map((id) => pendingUploads.get(id))
        .filter((u): u is PendingUpload => !!u && u.phase === 'uploaded')
        .sort((a, b) => a.albumIndex - b.albumIndex)

    if (sendable.length === 0) {
        cleanupAlbumBatchIfSettled(albumId)
        return
    }

    batch.flushing = true
    try {
        for (const upload of sendable) {
            if (abortFlags.has(upload.id)) continue
            await sendOne(upload)
        }
    } finally {
        batch.flushing = false
        cleanupAlbumBatchIfSettled(albumId)
    }
}

// Safety net invoked whenever the upload pipeline fully drains. Guarantees
// that any album whose items are all in a terminal-ish phase gets its
// pending `/chat/send` dispatches fired, even if the last `runUpload` did
// not reach the per-item flush path (e.g. due to a dropped preprocessing
// step reducing the actual submitted count below the expected count).
async function forceFlushStalledAlbums(): Promise<void> {
    if (activeUploadCount > 0 || uploadQueue.length > 0) return

    // Snapshot album ids — flushAlbumBatchIfReady may mutate albumBatches
    // (via cleanupAlbumBatchIfSettled on completion).
    const albumIds = Array.from(albumBatches.keys())
    for (const albumId of albumIds) {
        const batch = albumBatches.get(albumId)
        if (!batch || batch.flushing) continue

        // If preprocessing silently dropped one or more items, the batch
        // will never reach its original expectedCount. Recompute it from
        // the actual submitted count so the flush gate can pass.
        if (batch.optimisticIds.size > 0 && batch.optimisticIds.size < batch.expectedCount) {
            console.warn(
                `[uploadService] album ${albumId} was gathering ${batch.expectedCount} ` +
                    `items but only ${batch.optimisticIds.size} were submitted. ` +
                    `Adjusting expectedCount so the album can flush.`,
            )
            batch.expectedCount = batch.optimisticIds.size
        }

        await flushAlbumBatchIfReady(albumId)
    }
}

// Periodic watchdog: forces a safety flush every 30s to catch album
// batches that ended up stuck despite the drain-triggered force flush
// (e.g. the tab was throttled during the last `runUploadWithGate` finally,
// or an XHR got stuck without firing `onerror`/`ontimeout`).
let stalledAlbumsWatchdog: ReturnType<typeof setInterval> | null = null
function ensureStalledAlbumsWatchdog(): void {
    if (stalledAlbumsWatchdog) return
    stalledAlbumsWatchdog = setInterval(() => {
        void forceFlushStalledAlbums()
    }, 30_000)
}

// -----------------------------------------------------------------------------
// Content payload construction
// -----------------------------------------------------------------------------

function buildContent(upload: PendingUpload, phase: 'preview' | 'final'): string {
    const payload: Record<string, unknown> = {}

    if (phase === 'final' && upload.fileId) {
        payload.file_id = upload.fileId
    } else {
        payload.placeholder = true
    }

    payload.thumbnail =
        phase === 'final' ? upload.serverThumbnail || upload.thumbnail : upload.thumbnail

    if (upload.width && upload.height) {
        payload.width = upload.width
        payload.height = upload.height
    }

    if (upload.albumId) {
        payload.album_id = upload.albumId
        payload.album_index = upload.albumIndex
    }

    if (typeof upload.durationMs === 'number') {
        payload.durationMs = upload.durationMs
    }

    return JSON.stringify(payload)
}

export function buildOptimisticMessageFromUpload(upload: PendingUpload): Message {
    const phase: 'preview' | 'final' = upload.fileId ? 'final' : 'preview'
    return {
        id: upload.id,
        sender_id: upload.senderId,
        receiver_id: upload.userId,
        content: buildContent(upload, phase),
        message_type: upload.msgType,
        is_read: true,
        is_sending: upload.phase !== 'failed',
        is_error: upload.phase === 'failed',
        upload_progress: upload.progress,
        upload_loaded: upload.uploadedBytes,
        upload_total: upload.totalBytes,
        local_blob_url: upload.localBlobUrl,
        created_at: upload.createdAt,
    }
}

// -----------------------------------------------------------------------------
// Pipeline phases
// -----------------------------------------------------------------------------

async function runUpload(upload: PendingUpload): Promise<void> {
    if (!config) {
        console.warn('[uploadService] runUpload before init')
        return
    }
    if (abortFlags.has(upload.id)) return

    upload.phase = 'uploading'
    upload.progress = 0
    upload.uploadedBytes = 0
    upload.totalBytes = upload.file.size
    await idbPut(upload)

    try {
        const data = await new Promise<any>((resolve, reject) => {
            const xhr = new XMLHttpRequest()
            xhrControllers.set(upload.id, xhr)

            const formData = new FormData()
            formData.append('file', upload.file, upload.fileName)
            formData.append('thumbnail', upload.thumbnail)

            xhr.open('POST', `${config!.apiBaseUrl}/api/chat/upload-media`)
            xhr.timeout = XHR_UPLOAD_TIMEOUT_MS
            const token = config!.getAuthToken()
            if (token) {
                xhr.setRequestHeader('Authorization', `Bearer ${token}`)
            }

            xhr.upload.onprogress = (e) => {
                if (!e.lengthComputable) return
                const progress = Math.round((e.loaded / e.total) * 100)
                upload.progress = progress
                upload.uploadedBytes = e.loaded
                upload.totalBytes = e.total
                emit({
                    type: 'progress',
                    userId: upload.userId,
                    optimisticId: upload.id,
                    progress,
                    uploadedBytes: e.loaded,
                    totalBytes: e.total,
                })
            }

            xhr.onload = () => {
                xhrControllers.delete(upload.id)
                if (xhr.status === 401) {
                    reject(new Error('نشست شما منقضی شده است. لطفاً صفحه را رفرش کنید.'))
                    return
                }
                if (xhr.status === 413) {
                    reject(new Error('حجم فایل از حد مجاز ۵۰ مگابایت بیشتر است.'))
                    return
                }
                if (xhr.status >= 200 && xhr.status < 300) {
                    try {
                        resolve(JSON.parse(xhr.responseText))
                    } catch {
                        reject(new Error('پاسخ نامعتبر سرور'))
                    }
                } else {
                    let detail = ''
                    try {
                        const parsed = JSON.parse(xhr.responseText)
                        if (parsed?.detail) detail = parsed.detail
                    } catch {
                        /* ignore */
                    }
                    reject(new Error(detail || `خطای سرور (${xhr.status})`))
                }
            }
            xhr.onerror = () => {
                xhrControllers.delete(upload.id)
                reject(new Error('Network Error'))
            }
            xhr.onabort = () => {
                xhrControllers.delete(upload.id)
                reject(new Error('UploadCancelled'))
            }
            xhr.ontimeout = () => {
                xhrControllers.delete(upload.id)
                // Treat as transient so the retry path kicks in.
                reject(new Error('Network Error (timeout)'))
            }
            xhr.send(formData)
        })

        if (abortFlags.has(upload.id)) return

        upload.fileId = data.file_id
        upload.serverThumbnail = data.thumbnail
        if (typeof data.width === 'number' && typeof data.height === 'number') {
            upload.width = data.width
            upload.height = data.height
        }
        upload.phase = 'uploaded'
        upload.progress = 100
        upload.uploadedBytes = upload.totalBytes
        await idbPut(upload)

        emit({
            type: 'uploaded',
            userId: upload.userId,
            optimisticId: upload.id,
            fileId: upload.fileId!,
            content: buildContent(upload, 'final'),
        })

        if (upload.albumId) {
            await flushAlbumBatchIfReady(upload.albumId)
        } else {
            await sendOne(upload)
        }
    } catch (error) {
        xhrControllers.delete(upload.id)
        if (abortFlags.has(upload.id)) {
            upload.phase = 'cancelled'
            await idbDelete(upload.id)
            pendingUploads.delete(upload.id)
            abortFlags.delete(upload.id)
            emit({ type: 'cancelled', userId: upload.userId, optimisticId: upload.id })
            if (upload.albumId) cleanupAlbumBatchIfSettled(upload.albumId)
            return
        }
        // Auto-retry transient network / 5xx errors with exponential backoff
        // before giving up. This prevents single flaky-network hiccups during
        // large album uploads from showing up as "failed" media bubbles.
        if (isTransientUploadError(error) && (upload.retryCount ?? 0) < MAX_UPLOAD_RETRIES) {
            const attempt = (upload.retryCount ?? 0) + 1
            upload.retryCount = attempt
            upload.phase = 'queued'
            upload.progress = 0
            upload.uploadedBytes = 0
            await idbPut(upload)
            const delay = computeRetryDelayMs(attempt - 1)
            console.warn(
                `[uploadService] transient upload error (attempt ${attempt}/${MAX_UPLOAD_RETRIES}), retrying in ${delay}ms:`,
                error,
            )
            setTimeout(() => {
                if (abortFlags.has(upload.id)) return
                if (!pendingUploads.has(upload.id)) return
                enqueueUpload(upload)
            }, delay)
            return
        }
        await markFailed(upload, error instanceof Error ? error.message : String(error))
    }
}

async function sendOne(upload: PendingUpload): Promise<void> {
    if (!config) return
    if (abortFlags.has(upload.id)) return

    upload.phase = 'sending'
    await idbPut(upload)

    const content = buildContent(upload, 'final')
    const token = config.getAuthToken()

    try {
        const res = await fetch(`${config.apiBaseUrl}/api/chat/send`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({
                receiver_id: upload.userId,
                content,
                message_type: upload.msgType,
            }),
        })

        if (!res.ok) {
            let detail = ''
            try {
                const body = await res.json()
                if (body?.detail) detail = body.detail
            } catch {
                /* ignore */
            }
            throw new Error(detail || `خطای ارسال (${res.status})`)
        }

        const serverMessage = (await res.json()) as Message

        upload.phase = 'sent'
        emit({
            type: 'sent',
            userId: upload.userId,
            optimisticId: upload.id,
            serverMessage,
            localBlobUrl: upload.localBlobUrl,
        })

        pendingUploads.delete(upload.id)
        await idbDelete(upload.id)
        if (upload.albumId) cleanupAlbumBatchIfSettled(upload.albumId)
    } catch (error) {
        // Retry transient /chat/send failures before giving up. Without this,
        // a single flaky fetch() (e.g. browser per-origin pool saturated by
        // 30+ concurrent album uploads across multiple chats) becomes a
        // permanent "failed" media bubble even though the upload itself
        // succeeded and the server is healthy.
        if (
            isTransientUploadError(error) &&
            (upload.sendRetryCount ?? 0) < MAX_SEND_RETRIES &&
            !abortFlags.has(upload.id)
        ) {
            const attempt = (upload.sendRetryCount ?? 0) + 1
            upload.sendRetryCount = attempt
            // Revert to `uploaded` so a watchdog pass treats it as ready-to-send.
            upload.phase = 'uploaded'
            await idbPut(upload)
            const delay = computeRetryDelayMs(attempt - 1)
            console.warn(
                `[uploadService] transient /chat/send error (attempt ${attempt}/${MAX_SEND_RETRIES}), retrying in ${delay}ms:`,
                error,
            )
            setTimeout(() => {
                if (abortFlags.has(upload.id)) return
                if (!pendingUploads.has(upload.id)) return
                void sendOne(upload)
            }, delay)
            return
        }
        await markFailed(upload, error instanceof Error ? error.message : String(error))
    }
}

async function markFailed(upload: PendingUpload, errorMessage: string) {
    upload.phase = 'failed'
    upload.errorMessage = errorMessage
    await idbPut(upload)
    emit({
        type: 'error',
        userId: upload.userId,
        optimisticId: upload.id,
        errorMessage,
    })
    if (upload.albumId) cleanupAlbumBatchIfSettled(upload.albumId)
}

// -----------------------------------------------------------------------------
// Public API
// -----------------------------------------------------------------------------

export function initChatUploadBackground(cfg: ServiceConfig): Promise<void> {
    config = cfg

    if (initialized) {
        return resumePromise || Promise.resolve()
    }
    initialized = true
    ensureStalledAlbumsWatchdog()

    resumePromise = (async () => {
        try {
            const stored = await idbGetAll()
            for (const record of stored) {
                // Reset transient XHR state; progress is preserved from last write
                pendingUploads.set(record.id, record)
                if (record.albumId) {
                    const batch = ensureAlbumBatch(
                        record.albumId,
                        record.userId,
                        record.albumSize || 1
                    )
                    batch.optimisticIds.add(record.id)
                }
            }

            // Resume each upload in the background
            for (const upload of pendingUploads.values()) {
                // Rebuild a local blob URL so UI can show the preview again
                if (upload.file && !upload.localBlobUrl) {
                    try {
                        upload.localBlobUrl = URL.createObjectURL(upload.file)
                    } catch {
                        /* ignore */
                    }
                }

                if (upload.phase === 'failed' || upload.phase === 'sent') {
                    // Stale — drop
                    pendingUploads.delete(upload.id)
                    await idbDelete(upload.id)
                    continue
                }

                // Re-enter at the appropriate phase
                if (
                    upload.phase === 'uploaded' ||
                    upload.phase === 'sending'
                ) {
                    // upload-media already done; just re-send
                    if (upload.albumId) {
                        void flushAlbumBatchIfReady(upload.albumId)
                    } else {
                        void sendOne(upload)
                    }
                } else {
                    // queued / uploading — restart the upload
                    enqueueUpload(upload)
                }
            }
        } catch (e) {
            console.warn('[uploadService] resume failed:', e)
        }
    })()

    return resumePromise
}

export async function submitUpload(params: SubmitUploadParams): Promise<void> {
    if (!config) {
        throw new Error('[uploadService] not initialized')
    }

    const upload: PendingUpload = {
        id: params.optimisticId,
        userId: params.userId,
        senderId: params.senderId,
        msgType: params.msgType,
        file: params.file,
        fileName: params.fileName,
        thumbnail: params.thumbnail,
        width: params.width,
        height: params.height,
        durationMs: params.durationMs,
        albumId: params.albumId,
        albumIndex: params.albumIndex,
        albumSize: params.albumSize,
        phase: 'queued',
        progress: 0,
        uploadedBytes: 0,
        totalBytes: params.file.size,
        createdAt: new Date().toISOString(),
        localBlobUrl: params.localBlobUrl,
    }

    pendingUploads.set(upload.id, upload)

    if (upload.albumId) {
        const batch = ensureAlbumBatch(upload.albumId, upload.userId, upload.albumSize)
        batch.optimisticIds.add(upload.id)
    }

    await idbPut(upload)

    // Emit 'added' so any subscriber that may have been mounted AFTER the
    // optimistic push (e.g. on resume) can render it.
    emit({
        type: 'added',
        userId: upload.userId,
        optimisticId: upload.id,
        message: buildOptimisticMessageFromUpload(upload),
    })

    // Fire and forget; promise chain is owned by the service.
    // The concurrency gate ensures we never swamp the browser's per-origin
    // connection pool, so unrelated API calls (including text-message sends
    // to other conversations) keep working while large albums upload.
    enqueueUpload(upload)
}

export function cancelUpload(id: number): void {
    const upload = pendingUploads.get(id)
    if (!upload) return

    abortFlags.add(id)
    const xhr = xhrControllers.get(id)
    if (xhr) {
        try {
            xhr.abort()
        } catch {
            /* ignore */
        }
        xhrControllers.delete(id)
    }

    // If XHR already finished or never started, clean up eagerly
    if (upload.phase !== 'uploading') {
        upload.phase = 'cancelled'
        pendingUploads.delete(id)
        abortFlags.delete(id)
        void idbDelete(id)
        emit({ type: 'cancelled', userId: upload.userId, optimisticId: id })
        if (upload.albumId) cleanupAlbumBatchIfSettled(upload.albumId)
    }
    // If currently uploading, the xhr.onabort handler inside runUpload will
    // complete the cleanup and emit 'cancelled'.
}

export function getPendingForUser(userId: number): PendingUpload[] {
    const result: PendingUpload[] = []
    for (const upload of pendingUploads.values()) {
        if (upload.userId !== userId) continue
        if (upload.phase === 'sent' || upload.phase === 'cancelled') continue
        result.push(upload)
    }
    return result
}

export function retryFailedUpload(id: number): void {
    const upload = pendingUploads.get(id)
    if (!upload) return
    if (upload.phase !== 'failed') return
    abortFlags.delete(id)
    upload.retryCount = 0
    upload.errorMessage = undefined

    if (upload.fileId) {
        // upload-media already succeeded; re-send only
        if (upload.albumId) {
            void flushAlbumBatchIfReady(upload.albumId)
        } else {
            void sendOne(upload)
        }
    } else {
        enqueueUpload(upload)
    }
}

/**
 * Generate a unique optimistic id. Mirrors the sequence-based generator in
 * `useChatMedia.ts` so both call sites produce collision-free ids without
 * relying on `-Date.now()`.
 */
let optimisticIdSequence = 0
export function createOptimisticUploadId(): number {
    optimisticIdSequence = (optimisticIdSequence + 1) % 1000
    return -(Date.now() * 1000 + optimisticIdSequence)
}
