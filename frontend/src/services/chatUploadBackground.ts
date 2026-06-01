/**
 * Chat Upload Background Service
 *
 * Module-level singleton that owns the lifecycle of chat media uploads.
 * Unlike a Vue composable, this service's state survives navigation/unmount
 * of the `ChatView` component, so in-flight uploads continue to completion
 * even after the user leaves the messenger.
 *
 * Responsibilities:
 *  - For session-backed direct/group uploads: create resumable upload batches
 *    + sessions, append chunks, finalize the uploaded file, then commit the
 *    batch into the final chat message.
 *  - For channel uploads: keep the legacy `/api/chat/upload-media` path until
 *    the resumable channel slice is migrated.
 *  - Post committed messages with the captured conversation target (NOT
 *    reading `selectedUserId.value` — which may have been cleared when the
 *    user navigated away)
 *  - Album batching: collect sibling uploads by `album_id`, and only trigger
 *    the per-message `/chat/send` calls once all album members finish uploading
 *  - IndexedDB persistence of pending uploads (including raw File blobs),
 *    so a page reload or app close does not lose in-progress sends
 *  - Resume-on-init: on app mount, restore unfinished uploads from IDB and
 *    re-enter the pipeline at the correct phase
 *  - Event emission for any subscribed chat UI (`useChatMedia`) to update
 *    the visible optimistic messages in real time
 *
 * This service now uses the resumable upload backend for direct/group uploads,
 * including albums. Channel media still use the legacy monolithic upload path
 * until the dedicated channel slice is migrated.
 */

import type { Message } from '../types/chat'
import {
    buildChatActivityBody,
    buildChatActivityEndpoint,
    buildChatSendBody,
    buildChatSendEndpoint,
} from '../utils/chatRoomRouting'
import { serializeChatMediaMessagePayload } from '../utils/chatMediaMessagePayload'
import { markMessengerPerformance } from '../utils/messengerRefactor'
import { measureMessengerStage2, recordMessengerMetric } from '../utils/messengerStage2Metrics'
import { hasPendingUploadResumeHint as hasStoredUploadResumeHint, setUploadResumeHint } from './chatTransferResumeHints'

// -----------------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------------

export type UploadMsgType = 'image' | 'video' | 'voice' | 'document'
export type UploadRoomKind = 'direct' | 'group' | 'channel'

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
    userId: number // conversation key captured at submit time
    roomKind: UploadRoomKind
    senderId: number
    msgType: UploadMsgType
    file: Blob // preprocessed blob — IndexedDB can store Blobs/Files
    fileName: string
    mimeType: string
    thumbnail: string // base64 data URL
    width: number
    height: number
    durationMs?: number
    caption?: string
    albumId: string | null
    albumIndex: number
    albumSize: number
    phase: UploadPhase
    progress: number
    uploadedBytes: number
    totalBytes: number
    fileId?: string // server-returned after upload-media
    serverThumbnail?: string
    batchId?: string
    sessionId?: string
    resumeToken?: string
    nextOffset?: number
    sessionExpiresAt?: string
    errorMessage?: string
    createdAt: string // ISO timestamp
    localBlobUrl?: string // UI-only, not persisted
    retryCount?: number // number of transient retries attempted (upload-media)
    sendRetryCount?: number // number of transient retries attempted (/chat/send)
    activitySignalActive?: boolean
}

export interface SubmitUploadParams {
    optimisticId: number
    userId: number // conversation key captured at submit time
    roomKind: UploadRoomKind
    senderId: number
    msgType: UploadMsgType
    file: Blob
    fileName: string
    mimeType: string
    thumbnail: string
    width: number
    height: number
    durationMs?: number
    caption?: string
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
    roomKind: UploadRoomKind
    expectedCount: number
    optimisticIds: Set<number>
    batchId?: string
    commitRetryCount?: number
    flushing: boolean
}

type PersistedPendingUpload = Omit<PendingUpload, 'file'> & {
    file?: Blob
    fileBytes?: ArrayBuffer
    fileDataUrl?: string
}

// -----------------------------------------------------------------------------
// Module-level state (survives ChatView unmount)
// -----------------------------------------------------------------------------

let config: ServiceConfig | null = null
let initialized = false
let resumePromise: Promise<void> | null = null

const pendingUploads = new Map<number, PendingUpload>()
const xhrControllers = new Map<number, XMLHttpRequest>()
const sendControllers = new Map<number, AbortController>()
const abortFlags = new Set<number>()
const albumBatches = new Map<string, AlbumBatchState>()
const subscribers = new Set<UploadEventHandler>()
const uploadActivityCounts = new Map<number, number>()
const serviceWorkerOwnedUploads = new Set<number>()
const serviceWorkerHandoffAbortIds = new Set<number>()
const serviceWorkerHandoffResolvers = new Map<number, () => void>()
const firstProgressMetricUploadIds = new Set<number>()

export function hasPendingUploadResumeHint(): boolean {
    return pendingUploads.size > 0 || hasStoredUploadResumeHint()
}

export function primeChatUploadBackgroundConfig(cfg: ServiceConfig): void {
    config = cfg
}

function refreshUploadResumeHintFromState(): void {
    setUploadResumeHint(pendingUploads.size > 0)
}

async function deletePersistedUpload(id: number): Promise<void> {
    await idbDelete(id)
    refreshUploadResumeHintFromState()
}

function deletePersistedUploadSoon(id: number): void {
    void deletePersistedUpload(id)
}

function createDefaultServiceConfig(): ServiceConfig {
    return {
        apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
        getAuthToken: () => {
            if (typeof window === 'undefined') return null
            try {
                return window.localStorage.getItem('auth_token')
            } catch {
                return null
            }
        },
    }
}

function getOrCreateServiceConfig(): ServiceConfig {
    if (!config) {
        config = createDefaultServiceConfig()
    }
    return config
}

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
const DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES = 512 * 1024
// Hard cap on a single XHR upload. Protects against half-dead connections
// where bytes are uploaded (progress hits 100%) but the response never
// arrives — without this the XHR hangs forever, `phase='uploading'` is
// never cleared, and `flushAlbumBatchIfReady` blocks on `hasStillUploading`
// for the entire album — preventing ANY of the album's `/chat/send` calls
// from ever firing. Reproduced in the field: 13-image album never sent
// even after several hours despite all progress circles showing complete.
const XHR_UPLOAD_TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes per file
const SEND_REQUEST_TIMEOUT_MS = 2 * 60 * 1000 // 2 minutes per send
let activeUploadCount = 0
const uploadQueue: PendingUpload[] = []

function shouldSkipNonCriticalResumeDeferral(): boolean {
    const userAgent = typeof navigator !== 'undefined' ? String(navigator.userAgent || '') : ''
    if (userAgent.toLowerCase().includes('jsdom')) {
        return true
    }

    const runtime = globalThis as typeof globalThis & {
        process?: {
            env?: Record<string, string | undefined>
        }
    }
    return Boolean(runtime.process?.env?.VITEST)
}

function waitForNonCriticalResumeSlot(): Promise<void> {
    if (shouldSkipNonCriticalResumeDeferral()) {
        return Promise.resolve()
    }

    return new Promise((resolve) => {
        const runtime = typeof window !== 'undefined' ? window : globalThis
        const requestIdle = (runtime as typeof globalThis & {
            requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number
        }).requestIdleCallback

        if (typeof requestIdle === 'function') {
            requestIdle(() => resolve(), { timeout: 150 })
            return
        }

        if (typeof runtime.requestAnimationFrame === 'function') {
            runtime.requestAnimationFrame(() => {
                runtime.setTimeout(resolve, 0)
            })
            return
        }

        setTimeout(resolve, 0)
    })
}

async function yieldUploadRestoreLoop(index: number): Promise<void> {
    if (index === 0 || index % 6 !== 0) {
        return
    }

    if (shouldSkipNonCriticalResumeDeferral()) {
        return
    }

    await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

const READY_LIKE_UPLOAD_SESSION_STATUSES = new Set(['ready', 'committed'])
const TERMINAL_UPLOAD_SESSION_STATUSES = new Set(['failed', 'cancelled', 'expired'])

class UploadApiError extends Error {
    status: number

    constructor(status: number, message: string) {
        super(message)
        this.name = 'UploadApiError'
        this.status = status
    }
}

function enqueueUpload(upload: PendingUpload): void {
    // Avoid duplicates: if the upload is already queued or in-flight, ignore.
    if (serviceWorkerOwnedUploads.has(upload.id)) return
    if (uploadQueue.some((u) => u.id === upload.id)) return
    if (xhrControllers.has(upload.id)) return
    uploadQueue.push(upload)
    pumpUploadQueue()
}

function normalizeUploadRoomKind(value: unknown, conversationKey: number): UploadRoomKind {
    if (value === 'direct' || value === 'group' || value === 'channel') {
        return value
    }

    return conversationKey < 0 ? 'channel' : 'direct'
}

function isSessionBackedUploadRoomKind(roomKind: UploadRoomKind): roomKind is 'direct' | 'group' {
    return roomKind === 'direct' || roomKind === 'group'
}

function shouldUseSessionBackedUpload(upload: PendingUpload): boolean {
    return isSessionBackedUploadRoomKind(upload.roomKind)
}

function isSingleSessionBackedUpload(upload: PendingUpload): boolean {
    return shouldUseSessionBackedUpload(upload) && !upload.albumId
}

function buildAlbumServiceWorkerHandoffKey(
    upload: Pick<PendingUpload, 'roomKind' | 'userId' | 'albumId'>,
): string | null {
    if (!upload.albumId) return null
    return `${upload.roomKind}:${upload.userId}:${upload.albumId}`
}

function canUseUploadServiceWorker(): boolean {
    if (typeof window === 'undefined' || typeof navigator === 'undefined') {
        return false
    }
    if (!navigator.serviceWorker?.controller) {
        return false
    }
    const ua = navigator.userAgent || ''
    return /(Chrome|Chromium|Edg)/i.test(ua) && !/Firefox/i.test(ua)
}

function resolveServiceWorkerHandoff(id: number): void {
    const resolve = serviceWorkerHandoffResolvers.get(id)
    if (!resolve) return
    serviceWorkerHandoffResolvers.delete(id)
    resolve()
}

function resolveUploadTargetId(upload: Pick<PendingUpload, 'roomKind' | 'userId'>): number {
    if (upload.roomKind === 'direct') {
        return upload.userId
    }

    return Math.abs(upload.userId)
}

function buildSingleUploadBatchIdempotencyKey(upload: PendingUpload): string {
    return [
        'single',
        upload.roomKind,
        String(upload.senderId),
        String(resolveUploadTargetId(upload)),
        String(upload.id),
        upload.msgType,
    ].join(':').slice(0, 128)
}

function buildAlbumUploadBatchIdempotencyKey(upload: PendingUpload): string {
    return [
        'album',
        upload.roomKind,
        String(upload.senderId),
        String(resolveUploadTargetId(upload)),
        String(upload.albumId || ''),
    ].join(':').slice(0, 128)
}

function applyPreviewMetadataToUpload(upload: PendingUpload, previewMetadata: Record<string, unknown> | null | undefined) {
    if (!previewMetadata || typeof previewMetadata !== 'object') return

    const thumbnail = previewMetadata.thumbnail
    if (typeof thumbnail === 'string' && thumbnail.trim()) {
        upload.serverThumbnail = thumbnail
        if (!upload.thumbnail) {
            upload.thumbnail = thumbnail
        }
    }

    const width = Number(previewMetadata.width)
    const height = Number(previewMetadata.height)
    if (Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0) {
        upload.width = width
        upload.height = height
    }

    const durationMs = Number(previewMetadata.duration_ms ?? previewMetadata.durationMs)
    if (Number.isFinite(durationMs) && durationMs >= 0) {
        upload.durationMs = durationMs
    }
}

function getUploadPreviewMetadata(upload: PendingUpload): Record<string, unknown> {
    const payload: Record<string, unknown> = {}

    if (upload.thumbnail && upload.msgType !== 'document') {
        payload.thumbnail = upload.thumbnail
    }
    if (upload.width > 0 && upload.msgType !== 'document') {
        payload.width = upload.width
    }
    if (upload.height > 0 && upload.msgType !== 'document') {
        payload.height = upload.height
    }
    if (typeof upload.durationMs === 'number' && upload.durationMs >= 0 && upload.msgType !== 'document') {
        payload.duration_ms = upload.durationMs
    }
    if (upload.albumId && upload.msgType !== 'document') {
        payload.album_index = upload.albumIndex
    }
    if ((upload.msgType === 'image' || upload.msgType === 'video') && upload.caption) {
        payload.caption = upload.caption
    }

    return payload
}

function getUploadResumeProgress(upload: PendingUpload): number {
    const totalBytes = upload.file.size || upload.totalBytes || 0
    const nextOffset = Math.max(0, Math.min(upload.nextOffset ?? upload.uploadedBytes ?? 0, totalBytes))
    if (totalBytes <= 0) return 0
    return Math.max(0, Math.min(100, Math.round((nextOffset / totalBytes) * 100)))
}

async function uploadApiFetch<T>(path: string, init?: RequestInit): Promise<T> {
    if (!config) {
        throw new Error('[uploadService] not initialized')
    }

    const token = config.getAuthToken()
    const response = await fetch(`${config.apiBaseUrl}/api${path}`, {
        ...init,
        headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(init?.headers || {}),
        },
    })

    if (!response.ok) {
        let detail = ''
        try {
            const parsed = await response.json() as { detail?: string }
            if (typeof parsed?.detail === 'string') {
                detail = parsed.detail
            }
        } catch {
            /* ignore */
        }

        if (response.status === 401) {
            throw new UploadApiError(response.status, 'نشست شما منقضی شده است. لطفاً صفحه را رفرش کنید.')
        }
        if (response.status === 413) {
            throw new UploadApiError(response.status, 'حجم فایل از حد مجاز ۵۰ مگابایت بیشتر است.')
        }

        throw new UploadApiError(response.status, detail || `خطای سرور (${response.status})`)
    }

    if (response.status === 204) {
        return undefined as T
    }

    return (await response.json()) as T
}

function pumpUploadQueue(): void {
    while (activeUploadCount < MAX_CONCURRENT_UPLOADS && uploadQueue.length > 0) {
        const next = uploadQueue.shift()
        if (!next) break
        if (abortFlags.has(next.id)) continue
        if (serviceWorkerOwnedUploads.has(next.id)) continue
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

const MAX_SEND_RETRIES = 10

function computeRetryDelayMs(attempt: number): number {
    // Exponential backoff with jitter: ~1s, 2s, 4s (+/- 250ms)
    const base = 1000 * Math.pow(2, Math.max(0, attempt))
    const jitter = Math.floor(Math.random() * 500) - 250
    return Math.max(500, base + jitter)
}

function computeSendRetryDelayMs(attempt: number): number {
    // /chat/send retries are cheap (tiny JSON POST) and the upload blob is
    // already on the server, so be generous. Exponential up to 15s with
    // jitter: ~1s, 2s, 4s, 8s, then 15s ceiling.
    const base = Math.min(1000 * Math.pow(2, Math.max(0, attempt)), 15000)
    const jitter = Math.floor(Math.random() * 1000) - 500
    return Math.max(750, base + jitter)
}

async function publishUploadActivitySignal(conversationKey: number, active: boolean) {
    if (!config) return

    try {
        const token = config.getAuthToken()
        await fetch(`${config.apiBaseUrl}/api${buildChatActivityEndpoint(conversationKey)}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify(buildChatActivityBody(conversationKey, {
                activity: 'uploading_file',
                active,
            })),
        })
    } catch (error) {
        console.warn('[uploadService] activity signal failed:', error)
    }
}

function startUploadActivity(upload: PendingUpload) {
    if (upload.activitySignalActive) return

    upload.activitySignalActive = true
    const nextCount = (uploadActivityCounts.get(upload.userId) ?? 0) + 1
    uploadActivityCounts.set(upload.userId, nextCount)

    if (nextCount === 1) {
        void publishUploadActivitySignal(upload.userId, true)
    }
}

function stopUploadActivity(upload: PendingUpload) {
    if (!upload.activitySignalActive) return

    upload.activitySignalActive = false
    const currentCount = uploadActivityCounts.get(upload.userId) ?? 0

    if (currentCount <= 1) {
        uploadActivityCounts.delete(upload.userId)
        void publishUploadActivitySignal(upload.userId, false)
        return
    }

    uploadActivityCounts.set(upload.userId, currentCount - 1)
}

// -----------------------------------------------------------------------------
// IndexedDB
// -----------------------------------------------------------------------------

const DB_NAME = 'chat_upload_queue'
const DB_VERSION = 1
const STORE_NAME = 'pending'

let dbPromise: Promise<IDBDatabase> | null = null

function blobToDataUrl(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        try {
            const reader = new FileReader()
            reader.onload = () => resolve(String(reader.result || ''))
            reader.onerror = () => reject(reader.error)
            reader.readAsDataURL(blob)
        } catch (error) {
            reject(error)
        }
    })
}

function dataUrlToBlob(dataUrl: string, fallbackType?: string): Blob {
    const [header, base64] = dataUrl.split(',', 2)
    const mimeMatch = /^data:([^;]+);base64$/i.exec(header || '')
    const mimeType = mimeMatch?.[1] || fallbackType || 'application/octet-stream'
    const binary = atob(base64 || '')
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index)
    }
    return new Blob([bytes], { type: mimeType })
}

function restorePersistedFile(record: PersistedPendingUpload): Blob | null {
    if (record.file instanceof Blob) {
        if (record.msgType === 'document' && !(record.file instanceof File)) {
            return new File([record.file], record.fileName, {
                type: record.file.type || record.mimeType || 'application/octet-stream',
            })
        }
        return record.file
    }

    if (record.msgType === 'document' && record.fileDataUrl) {
        const blob = dataUrlToBlob(record.fileDataUrl, record.mimeType)
        return new File([blob], record.fileName, {
            type: blob.type || record.mimeType || 'application/octet-stream',
        })
    }

    if (record.fileBytes) {
        const blob = new Blob([record.fileBytes], { type: record.mimeType })
        if (record.msgType === 'document') {
            return new File([blob], record.fileName, {
                type: blob.type || record.mimeType || 'application/octet-stream',
            })
        }
        return blob
    }

    return null
}

function normalizePersistedUpload(record: PersistedPendingUpload, restoredFile: Blob): PendingUpload {
    return {
        ...record,
        roomKind: normalizeUploadRoomKind((record as Partial<PendingUpload>).roomKind, Number(record.userId)),
        file: restoredFile,
    } as PendingUpload
}

async function putRecord(db: IDBDatabase, record: PersistedPendingUpload): Promise<boolean> {
    return await new Promise<boolean>((resolve) => {
        try {
            const tx = db.transaction(STORE_NAME, 'readwrite')
            tx.objectStore(STORE_NAME).put(record)
            tx.oncomplete = () => resolve(true)
            tx.onerror = () => resolve(false)
            tx.onabort = () => resolve(false)
        } catch (e) {
            console.warn('[uploadService] idbPut failed:', e)
            resolve(false)
        }
    })
}

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
        const record: PersistedPendingUpload = { ...upload }
        // Do not persist UI-only fields
        delete record.localBlobUrl

        if (upload.msgType === 'document') {
            try {
                record.fileDataUrl = await blobToDataUrl(upload.file)
            } catch {
                // Non-fatal. Blob/byte persistence below may still succeed.
            }
        }

        // Prefer persisting the original Blob/File directly. WebKit proved
        // unreliable when resuming document uploads from ArrayBuffer-first
        // records after a full reload, producing a corrupted payload that the
        // backend rejected with 400. Blob cloning through IndexedDB is the
        // more faithful path here; only fall back to bytes if the browser
        // refuses to store the Blob record.
        record.file = upload.file
        delete record.fileBytes

        const storedBlob = await putRecord(db, record)
        if (storedBlob) return

        try {
            record.fileBytes = await upload.file.arrayBuffer()
            delete record.file
            await putRecord(db, record)
        } catch {
            // Give up quietly; the caller keeps the in-memory upload alive.
        }
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

async function idbGet(id: number): Promise<PendingUpload | null> {
    try {
        const db = await openDB()
        return await new Promise<PendingUpload | null>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readonly')
                const req = tx.objectStore(STORE_NAME).get(id)
                req.onsuccess = () => {
                    const record = req.result as PersistedPendingUpload | undefined
                    if (!record) {
                        resolve(null)
                        return
                    }

                    const restoredFile = restorePersistedFile(record)
                    if (!restoredFile) {
                        resolve(null)
                        return
                    }

                    resolve(normalizePersistedUpload(record, restoredFile))
                }
                req.onerror = () => resolve(null)
            } catch {
                resolve(null)
            }
        })
    } catch {
        return null
    }
}

async function idbGetAll(): Promise<PendingUpload[]> {
    try {
        const db = await openDB()
        return await new Promise<PendingUpload[]>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readonly')
                const req = tx.objectStore(STORE_NAME).getAll()
                req.onsuccess = () => {
                    const records = ((req.result as PersistedPendingUpload[]) || []).map((record) => {
                        const restoredFile = restorePersistedFile(record)
                        if (!restoredFile) return null
                        return normalizePersistedUpload(record, restoredFile)
                    }).filter((record): record is PendingUpload => record !== null)

                    resolve(records)
                }
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
    if (event.type === 'progress' && !firstProgressMetricUploadIds.has(event.optimisticId)) {
        firstProgressMetricUploadIds.add(event.optimisticId)
        const firstProgressMark = `upload-handoff-${event.optimisticId}-first-progress`
        markMessengerPerformance(firstProgressMark)
        measureMessengerStage2('upload-handoff-to-first-progress', `upload-handoff-${event.optimisticId}-start`, firstProgressMark, {
            userId: event.userId,
            optimisticId: event.optimisticId,
        })
        recordMessengerMetric('upload-first-progress-percent', event.progress, 'count', {
            userId: event.userId,
            optimisticId: event.optimisticId,
        })
    }
    if (event.type === 'sent' || event.type === 'error' || event.type === 'cancelled') {
        firstProgressMetricUploadIds.delete(event.optimisticId)
    }

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

function ensureAlbumBatch(
    albumId: string,
    userId: number,
    expectedCount: number,
    roomKind: UploadRoomKind,
    batchId?: string,
) {
    const existing = albumBatches.get(albumId)
    if (existing) {
        existing.expectedCount = Math.max(existing.expectedCount, expectedCount)
        if (batchId && !existing.batchId) {
            existing.batchId = batchId
        }
        return existing
    }
    const created: AlbumBatchState = {
        albumId,
        userId,
        roomKind,
        expectedCount,
        optimisticIds: new Set(),
        batchId,
        commitRetryCount: 0,
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

    const uploads = ids
        .map((id) => pendingUploads.get(id))
        .filter((u): u is PendingUpload => !!u)
        .sort((a, b) => a.albumIndex - b.albumIndex)

    const hasFailed = uploads.some((upload) => upload.phase === 'failed')
    if (hasFailed) return

    const sendable = uploads.filter((upload) => upload.phase === 'uploaded')

    if (sendable.length === 0) {
        cleanupAlbumBatchIfSettled(albumId)
        return
    }

    batch.flushing = true
    try {
        const useResumableAlbumCommit = sendable.every((upload) => shouldUseSessionBackedUpload(upload))
        if (useResumableAlbumCommit) {
            await commitAlbumBatch(batch, sendable)
        } else {
            for (const upload of sendable) {
                if (abortFlags.has(upload.id)) continue
                await sendOne(upload)
            }
        }
    } finally {
        batch.flushing = false
        cleanupAlbumBatchIfSettled(albumId)
    }
}

async function restoreAlbumBatchFromSendingState(albumId: string): Promise<void> {
    const batch = albumBatches.get(albumId)
    if (!batch) return

    const ids = Array.from(batch.optimisticIds)
    await Promise.all(ids.map(async (id) => {
        const upload = pendingUploads.get(id)
        if (!upload || upload.phase !== 'sending') return

        if (upload.fileId) {
            upload.phase = 'uploaded'
            upload.progress = 100
            upload.totalBytes = upload.file.size
            upload.uploadedBytes = upload.totalBytes
        } else if (shouldUseSessionBackedUpload(upload)) {
            upload.phase = 'queued'
            upload.totalBytes = upload.file.size
            upload.uploadedBytes = Math.max(0, Math.min(upload.nextOffset ?? upload.uploadedBytes ?? 0, upload.file.size))
            upload.progress = getUploadResumeProgress(upload)
        } else {
            upload.phase = 'queued'
            upload.progress = 0
            upload.uploadedBytes = 0
            upload.totalBytes = upload.file.size
        }

        upload.errorMessage = undefined
        await idbPut(upload)
    }))
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

async function pauseUploadForServiceWorker(upload: PendingUpload): Promise<void> {
    if (serviceWorkerOwnedUploads.has(upload.id)) return

    const xhr = xhrControllers.get(upload.id)
    const sendController = sendControllers.get(upload.id)
    if (!xhr && !sendController) {
        serviceWorkerOwnedUploads.add(upload.id)
        return
    }

    await new Promise<void>((resolve) => {
        serviceWorkerHandoffAbortIds.add(upload.id)
        serviceWorkerHandoffResolvers.set(upload.id, () => {
            serviceWorkerOwnedUploads.add(upload.id)
            resolve()
        })

        try {
            xhr?.abort()
        } catch {
            /* ignore */
        }

        try {
            sendController?.abort()
        } catch {
            /* ignore */
        }

        if (!xhr && !sendController) {
            resolveServiceWorkerHandoff(upload.id)
        }
    })
}

async function postUploadsToServiceWorker(uploadIds: number[]): Promise<boolean> {
    if (!config || !canUseUploadServiceWorker()) return false
    const authToken = config.getAuthToken()
    if (!authToken) return false

    try {
        navigator.serviceWorker.controller?.postMessage({
            type: 'chat-upload:handoff',
            apiBaseUrl: config.apiBaseUrl,
            authToken,
            uploadIds,
        })
        return true
    } catch (error) {
        console.warn('[uploadService] failed to hand off uploads to service worker:', error)
        return false
    }
}

async function handoffEligibleUploadsToServiceWorker(): Promise<void> {
    if (!canUseUploadServiceWorker()) return

    const initialEligibleUploads = Array.from(pendingUploads.values()).filter((upload) => {
        if (!shouldUseSessionBackedUpload(upload)) return false
        if (serviceWorkerOwnedUploads.has(upload.id)) return false
        if (abortFlags.has(upload.id)) return false
        return upload.phase !== 'sent' && upload.phase !== 'cancelled' && upload.phase !== 'failed'
    })

    const albumHandoffReadiness = new Map<string, boolean>()
    const eligibleUploads: PendingUpload[] = []

    for (const upload of initialEligibleUploads.sort(comparePendingUploadsForTimeline)) {
        const albumKey = buildAlbumServiceWorkerHandoffKey(upload)
        if (!albumKey) {
            eligibleUploads.push(upload)
            continue
        }

        let isReady = albumHandoffReadiness.get(albumKey)
        if (isReady === undefined) {
            try {
                await ensureResumableUploadBatch(upload)
                isReady = Boolean(upload.batchId)
            } catch (error) {
                console.warn('[uploadService] failed to prepare album handoff batch:', error)
                isReady = false
            }
            albumHandoffReadiness.set(albumKey, isReady)
        }

        if (isReady) {
            eligibleUploads.push(upload)
        }
    }

    if (eligibleUploads.length === 0) return

    for (const upload of eligibleUploads) {
        await pauseUploadForServiceWorker(upload)
    }

    const handedOff = await postUploadsToServiceWorker(eligibleUploads.map((upload) => upload.id))
    if (!handedOff) {
        for (const upload of eligibleUploads) {
            serviceWorkerOwnedUploads.delete(upload.id)
        }
    }
}

async function reclaimUploadsFromServiceWorker(): Promise<void> {
    if (serviceWorkerOwnedUploads.size === 0) return

    const ownedIds = Array.from(serviceWorkerOwnedUploads)
    serviceWorkerOwnedUploads.clear()

    try {
        navigator.serviceWorker.controller?.postMessage({
            type: 'chat-upload:reclaim',
            uploadIds: ownedIds,
        })
    } catch (error) {
        console.warn('[uploadService] failed to reclaim uploads from service worker:', error)
    }

    for (const id of ownedIds) {
        const refreshedUpload = await idbGet(id)
        if (!refreshedUpload) {
            pendingUploads.delete(id)
            refreshUploadResumeHintFromState()
            continue
        }

        const existingUpload = pendingUploads.get(id)
        if (existingUpload?.localBlobUrl && !refreshedUpload.localBlobUrl) {
            refreshedUpload.localBlobUrl = existingUpload.localBlobUrl
        } else if (!refreshedUpload.localBlobUrl) {
            try {
                refreshedUpload.localBlobUrl = URL.createObjectURL(refreshedUpload.file)
            } catch {
                /* ignore */
            }
        }

        pendingUploads.set(id, refreshedUpload)
    }
}

let uploadServiceWorkerBridgeBound = false
function ensureUploadServiceWorkerBridge(): void {
    if (uploadServiceWorkerBridgeBound || typeof navigator === 'undefined') return

    navigator.serviceWorker?.addEventListener('message', (event) => {
        const data = event.data as {
            type?: string
            uploadId?: number
            serverMessage?: Message
            errorMessage?: string
        } | null
        if (!data || typeof data.type !== 'string' || typeof data.uploadId !== 'number') {
            return
        }

        const upload = pendingUploads.get(data.uploadId)
        if (!upload) return

        serviceWorkerOwnedUploads.delete(data.uploadId)

        if (data.type === 'chat-upload:sent' && data.serverMessage) {
            upload.phase = 'sent'
            stopUploadActivity(upload)
            pendingUploads.delete(upload.id)
            if (upload.albumId) {
                cleanupAlbumBatchIfSettled(upload.albumId)
            }
            deletePersistedUploadSoon(upload.id)
            emit({
                type: 'sent',
                userId: upload.userId,
                optimisticId: upload.id,
                serverMessage: data.serverMessage,
                localBlobUrl: upload.localBlobUrl,
            })
            return
        }

        if (data.type === 'chat-upload:error') {
            void markFailed(upload, data.errorMessage || 'Service worker upload failed')
        }
    })

    uploadServiceWorkerBridgeBound = true
}

let uploadForegroundRecoveryBound = false
function ensureUploadForegroundRecovery(): void {
    if (uploadForegroundRecoveryBound || typeof window === 'undefined') return

    const handleForegroundWake = () => {
        if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
            void handoffEligibleUploadsToServiceWorker()
            return
        }
        void reclaimUploadsFromServiceWorker().then(() => resumePendingUploadsAfterForegroundWake())
    }

    window.addEventListener('focus', handleForegroundWake)
    window.addEventListener('pageshow', handleForegroundWake)
    if (typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', handleForegroundWake)
    }

    uploadForegroundRecoveryBound = true
}

async function resumePendingUploadsAfterForegroundWake(): Promise<void> {
    if (!config) return

    const resumedAlbumIds = new Set<string>()

    for (const upload of pendingUploads.values()) {
        if (abortFlags.has(upload.id)) continue
        if (serviceWorkerOwnedUploads.has(upload.id)) continue

        startUploadActivity(upload)

        if (upload.phase === 'queued') {
            enqueueUpload(upload)
            continue
        }

        if (upload.phase === 'uploading') {
            const alreadyQueued = uploadQueue.some((queuedUpload) => queuedUpload.id === upload.id)
            if (!xhrControllers.has(upload.id) && !alreadyQueued) {
                upload.phase = 'queued'
                if (shouldUseSessionBackedUpload(upload)) {
                    upload.totalBytes = upload.file.size
                    upload.uploadedBytes = Math.max(0, Math.min(upload.nextOffset ?? upload.uploadedBytes ?? 0, upload.file.size))
                    upload.progress = getUploadResumeProgress(upload)
                } else {
                    upload.progress = 0
                    upload.uploadedBytes = 0
                    upload.totalBytes = upload.file.size
                }
                await idbPut(upload)
                enqueueUpload(upload)
            }
            continue
        }

        if (upload.phase === 'uploaded') {
            if (upload.albumId) {
                if (resumedAlbumIds.has(upload.albumId)) continue
                resumedAlbumIds.add(upload.albumId)
                void flushAlbumBatchIfReady(upload.albumId)
            } else {
                void sendOne(upload)
            }
            continue
        }

        if (upload.phase === 'sending' && !sendControllers.has(upload.id)) {
            if (upload.albumId) {
                if (resumedAlbumIds.has(upload.albumId)) continue
                resumedAlbumIds.add(upload.albumId)
                await restoreAlbumBatchFromSendingState(upload.albumId)
                void flushAlbumBatchIfReady(upload.albumId)
            } else {
                void sendOne(upload)
            }
        }
    }
}

// -----------------------------------------------------------------------------
// Content payload construction
// -----------------------------------------------------------------------------

function buildContent(upload: PendingUpload, phase: 'preview' | 'final'): string {
    return serializeChatMediaMessagePayload({
        phase,
        msgType: upload.msgType,
        fileId: upload.fileId,
        thumbnail: upload.thumbnail,
        serverThumbnail: upload.serverThumbnail,
        width: upload.width,
        height: upload.height,
        durationMs: upload.durationMs,
        albumId: upload.albumId,
        albumIndex: upload.albumIndex,
        caption: upload.caption,
        fileName: upload.fileName,
        mimeType: upload.mimeType,
        fileSize: upload.file.size,
    })
}

type UploadSessionStatePayload = {
    session_id: string
    status: string
    next_offset: number
    received_bytes: number
    total_bytes: number
    preview_metadata?: Record<string, unknown>
    final_chat_file_id?: string | null
}

type UploadBatchCreatePayload = {
    batch_id: string
    status: string
    expires_at: string
}

type UploadSessionCreatePayload = {
    session_id: string
    resume_token: string
    next_offset: number
    chunk_size: number
    expires_at: string
    status: string
}

type UploadSessionChunkPayload = {
    session_id: string
    received_bytes: number
    next_offset: number
    status: string
}

type UploadSessionFinalizePayload = {
    session_id: string
    status: string
    final_chat_file_id?: string | null
}

type UploadBatchCommitPayload = {
    batch_id: string
    status: string
    committed_items: number
    messages: Message[]
}

function clearResumableUploadState(upload: PendingUpload) {
    upload.batchId = undefined
    upload.sessionId = undefined
    upload.resumeToken = undefined
    upload.nextOffset = 0
    upload.sessionExpiresAt = undefined
    upload.fileId = undefined
}

async function syncResumableUploadState(upload: PendingUpload): Promise<UploadSessionStatePayload | null> {
    if (!upload.sessionId) return null

    try {
        const state = await uploadApiFetch<UploadSessionStatePayload>(`/chat/upload-sessions/${upload.sessionId}`)
        upload.totalBytes = Math.max(Number(state.total_bytes || 0), upload.file.size)
        upload.nextOffset = Math.max(0, Number(state.next_offset || 0))
        upload.uploadedBytes = Math.max(0, Math.min(Number(state.received_bytes || 0), upload.totalBytes))
        upload.progress = getUploadResumeProgress(upload)
        applyPreviewMetadataToUpload(upload, state.preview_metadata)
        if (typeof state.final_chat_file_id === 'string' && state.final_chat_file_id) {
            upload.fileId = state.final_chat_file_id
        }

        return state
    } catch (error) {
        if (error instanceof UploadApiError && error.status === 404) {
            clearResumableUploadState(upload)
            await idbPut(upload)
            return null
        }
        throw error
    }
}

async function ensureResumableUploadBatch(upload: PendingUpload): Promise<void> {
    if (upload.batchId) return

    const isAlbumUpload = Boolean(upload.albumId)
    const expectedItems = isAlbumUpload ? Math.max(upload.albumSize || 1, 1) : 1
    const localAlbumBatch = isAlbumUpload
        ? ensureAlbumBatch(
              upload.albumId!,
              upload.userId,
              expectedItems,
              upload.roomKind,
              upload.batchId,
          )
        : null

    if (localAlbumBatch?.batchId) {
        upload.batchId = localAlbumBatch.batchId
        await idbPut(upload)
        return
    }

    const payload = await uploadApiFetch<UploadBatchCreatePayload>('/chat/upload-batches', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            room_kind: upload.roomKind,
            target_id: resolveUploadTargetId(upload),
            message_kind: isAlbumUpload ? 'album' : 'single',
            expected_items: expectedItems,
            caption_policy: isAlbumUpload ? 'first_item_only' : 'none',
            idempotency_key: isAlbumUpload
                ? buildAlbumUploadBatchIdempotencyKey(upload)
                : buildSingleUploadBatchIdempotencyKey(upload),
        }),
    })

    upload.batchId = payload.batch_id
    if (localAlbumBatch) {
        localAlbumBatch.batchId = payload.batch_id
        localAlbumBatch.commitRetryCount = 0
        await Promise.all(
            Array.from(localAlbumBatch.optimisticIds).map(async (id) => {
                const sibling = pendingUploads.get(id)
                if (!sibling) return
                sibling.batchId = payload.batch_id
                await idbPut(sibling)
            }),
        )
        return
    }

    await idbPut(upload)
}

async function ensureResumableUploadSession(upload: PendingUpload): Promise<UploadSessionStatePayload | null> {
    const syncedState = await syncResumableUploadState(upload)
    if (syncedState) {
        if (TERMINAL_UPLOAD_SESSION_STATUSES.has(syncedState.status)) {
            clearResumableUploadState(upload)
        } else {
            return syncedState
        }
    }

    await ensureResumableUploadBatch(upload)

    const payload = await uploadApiFetch<UploadSessionCreatePayload>('/chat/upload-sessions', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            batch_id: upload.batchId,
            room_kind: upload.roomKind,
            target_id: resolveUploadTargetId(upload),
            media_type: upload.msgType,
            file_name: upload.fileName,
            mime_type: upload.mimeType || 'application/octet-stream',
            total_bytes: upload.file.size,
            chunk_size: DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES,
            preview_metadata: getUploadPreviewMetadata(upload),
        }),
    })

    upload.sessionId = payload.session_id
    upload.resumeToken = payload.resume_token
    upload.nextOffset = Math.max(0, Number(payload.next_offset || 0))
    upload.sessionExpiresAt = payload.expires_at
    upload.totalBytes = upload.file.size
    upload.uploadedBytes = Math.max(0, Math.min(upload.nextOffset, upload.file.size))
    upload.progress = getUploadResumeProgress(upload)
    await idbPut(upload)
    return null
}

async function appendResumableUploadChunk(
    upload: PendingUpload,
    chunkBlob: Blob,
    offset: number,
    isLastChunk: boolean,
): Promise<UploadSessionChunkPayload> {
    if (!config || !upload.sessionId || !upload.resumeToken) {
        throw new Error('Missing resumable upload session state')
    }

    const localConfig = config
    const sessionId = upload.sessionId
    const resumeToken = upload.resumeToken

    return await new Promise<UploadSessionChunkPayload>((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhrControllers.set(upload.id, xhr)

        const formData = new FormData()
        formData.append('resume_token', resumeToken)
        formData.append('offset', String(offset))
        formData.append('is_last_chunk', String(isLastChunk))
        formData.append('chunk', chunkBlob, upload.fileName)

        xhr.open('PATCH', `${localConfig.apiBaseUrl}/api/chat/upload-sessions/${sessionId}/chunk`)
        xhr.timeout = XHR_UPLOAD_TIMEOUT_MS
        const token = localConfig.getAuthToken()
        if (token) {
            xhr.setRequestHeader('Authorization', `Bearer ${token}`)
        }

        xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable) return
            const totalBytes = upload.file.size || upload.totalBytes || event.total
            const uploadedBytes = Math.max(0, Math.min(offset + event.loaded, totalBytes))
            const progress = totalBytes > 0 ? Math.round((uploadedBytes / totalBytes) * 100) : 0
            upload.progress = progress
            upload.uploadedBytes = uploadedBytes
            upload.totalBytes = totalBytes
            emit({
                type: 'progress',
                userId: upload.userId,
                optimisticId: upload.id,
                progress,
                uploadedBytes,
                totalBytes,
            })
        }

        xhr.onload = () => {
            xhrControllers.delete(upload.id)
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    resolve(JSON.parse(xhr.responseText) as UploadSessionChunkPayload)
                } catch {
                    reject(new Error('پاسخ نامعتبر سرور'))
                }
                return
            }

            let detail = ''
            try {
                const parsed = JSON.parse(xhr.responseText) as { detail?: string }
                if (typeof parsed?.detail === 'string') {
                    detail = parsed.detail
                }
            } catch {
                /* ignore */
            }

            if (xhr.status === 401) {
                reject(new UploadApiError(xhr.status, 'نشست شما منقضی شده است. لطفاً صفحه را رفرش کنید.'))
                return
            }

            reject(new UploadApiError(xhr.status, detail || `خطای سرور (${xhr.status})`))
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
            reject(new Error('Network Error (timeout)'))
        }
        xhr.send(formData)
    })
}

async function finalizeResumableUploadSession(upload: PendingUpload): Promise<void> {
    if (!upload.sessionId) {
        throw new Error('Missing upload session id')
    }

    const finalizePayload = await uploadApiFetch<UploadSessionFinalizePayload>(`/chat/upload-sessions/${upload.sessionId}/finalize`, {
        method: 'POST',
    })
    if (typeof finalizePayload.final_chat_file_id === 'string' && finalizePayload.final_chat_file_id) {
        upload.fileId = finalizePayload.final_chat_file_id
    }

    const syncedState = await syncResumableUploadState(upload)
    if (syncedState?.final_chat_file_id) {
        upload.fileId = syncedState.final_chat_file_id
    }
}

async function cancelServerUploadState(upload: PendingUpload): Promise<void> {
    try {
        if (upload.batchId) {
            await uploadApiFetch(`/chat/upload-batches/${upload.batchId}/cancel`, { method: 'POST' })
            return
        }
        if (upload.sessionId) {
            await uploadApiFetch(`/chat/upload-sessions/${upload.sessionId}/cancel`, { method: 'POST' })
        }
    } catch {
        /* ignore */
    }
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

function comparePendingUploadsForTimeline(left: Pick<PendingUpload, 'id' | 'createdAt' | 'albumId' | 'albumIndex'>, right: Pick<PendingUpload, 'id' | 'createdAt' | 'albumId' | 'albumIndex'>): number {
    if (left.albumId && right.albumId && left.albumId === right.albumId) {
        const albumIndexDiff = left.albumIndex - right.albumIndex
        if (albumIndexDiff !== 0) {
            return albumIndexDiff
        }
    }

    const leftTimestamp = Date.parse(left.createdAt)
    const rightTimestamp = Date.parse(right.createdAt)
    if (Number.isFinite(leftTimestamp) && Number.isFinite(rightTimestamp) && leftTimestamp !== rightTimestamp) {
        return leftTimestamp - rightTimestamp
    }

    if (left.createdAt !== right.createdAt) {
        return left.createdAt.localeCompare(right.createdAt)
    }

    return Math.abs(left.id) - Math.abs(right.id)
}

// -----------------------------------------------------------------------------
// Pipeline phases
// -----------------------------------------------------------------------------

async function runResumableUpload(upload: PendingUpload): Promise<void> {
    if (!config) {
        console.warn('[uploadService] runUpload before init')
        return
    }
    if (abortFlags.has(upload.id)) return

    upload.phase = 'uploading'
    upload.totalBytes = upload.file.size
    upload.uploadedBytes = Math.max(0, Math.min(upload.nextOffset ?? upload.uploadedBytes ?? 0, upload.file.size))
    upload.progress = getUploadResumeProgress(upload)
    await idbPut(upload)

    try {
        const syncedState = await ensureResumableUploadSession(upload)
        if (abortFlags.has(upload.id)) return

        if (syncedState && READY_LIKE_UPLOAD_SESSION_STATUSES.has(syncedState.status)) {
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
            return
        }

        let offset = Math.max(0, Math.min(upload.nextOffset ?? 0, upload.file.size))
        while (offset < upload.file.size) {
            const nextOffset = Math.min(offset + DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES, upload.file.size)
            const chunkBlob = upload.file.slice(offset, nextOffset)
            const chunkPayload = await appendResumableUploadChunk(upload, chunkBlob, offset, nextOffset >= upload.file.size)
            if (abortFlags.has(upload.id)) return

            offset = Math.max(0, Math.min(Number(chunkPayload.next_offset || nextOffset), upload.file.size))
            upload.nextOffset = offset
            upload.uploadedBytes = Math.max(0, Math.min(Number(chunkPayload.received_bytes || offset), upload.file.size))
            upload.totalBytes = upload.file.size
            upload.progress = getUploadResumeProgress(upload)
            await idbPut(upload)
        }

        await finalizeResumableUploadSession(upload)
        if (abortFlags.has(upload.id)) return
        if (!upload.fileId) {
            throw new Error('شناسه فایل نهایی از سرور دریافت نشد')
        }

        upload.phase = 'uploaded'
        upload.progress = 100
        upload.uploadedBytes = upload.totalBytes
        await idbPut(upload)

        emit({
            type: 'uploaded',
            userId: upload.userId,
            optimisticId: upload.id,
            fileId: upload.fileId,
            content: buildContent(upload, 'final'),
        })

        if (upload.albumId) {
            await flushAlbumBatchIfReady(upload.albumId)
        } else {
            await sendOne(upload)
        }
    } catch (error) {
        xhrControllers.delete(upload.id)
        if (serviceWorkerHandoffAbortIds.has(upload.id)) {
            serviceWorkerHandoffAbortIds.delete(upload.id)
            upload.phase = upload.fileId ? 'uploaded' : 'queued'
            upload.totalBytes = upload.file.size
            upload.uploadedBytes = Math.max(0, Math.min(upload.nextOffset ?? upload.uploadedBytes ?? 0, upload.file.size))
            upload.progress = upload.fileId ? 100 : getUploadResumeProgress(upload)
            await idbPut(upload)
            resolveServiceWorkerHandoff(upload.id)
            return
        }
        if (abortFlags.has(upload.id)) {
            upload.phase = 'cancelled'
            stopUploadActivity(upload)
            pendingUploads.delete(upload.id)
            abortFlags.delete(upload.id)
            void cancelServerUploadState(upload)
            await deletePersistedUpload(upload.id)
            emit({ type: 'cancelled', userId: upload.userId, optimisticId: upload.id })
            return
        }
        if (isTransientUploadError(error) && (upload.retryCount ?? 0) < MAX_UPLOAD_RETRIES) {
            const attempt = (upload.retryCount ?? 0) + 1
            upload.retryCount = attempt
            upload.phase = 'queued'
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

async function runLegacyUpload(upload: PendingUpload): Promise<void> {
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
            if (upload.thumbnail) {
                formData.append('thumbnail', upload.thumbnail)
            }

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
                reject(new Error('Network Error (timeout)'))
            }
            xhr.send(formData)
        })

        if (abortFlags.has(upload.id)) return

        upload.fileId = data.file_id
        upload.fileName = typeof data.file_name === 'string' && data.file_name.trim()
            ? data.file_name.trim()
            : upload.fileName
        upload.mimeType = typeof data.mime_type === 'string' && data.mime_type.trim()
            ? data.mime_type.trim()
            : upload.mimeType
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
            stopUploadActivity(upload)
            pendingUploads.delete(upload.id)
            await deletePersistedUpload(upload.id)
            abortFlags.delete(upload.id)
            emit({ type: 'cancelled', userId: upload.userId, optimisticId: upload.id })
            if (upload.albumId) cleanupAlbumBatchIfSettled(upload.albumId)
            return
        }
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

async function runUpload(upload: PendingUpload): Promise<void> {
    if (shouldUseSessionBackedUpload(upload)) {
        await runResumableUpload(upload)
        return
    }

    await runLegacyUpload(upload)
}

async function commitSingleUploadBatch(upload: PendingUpload): Promise<void> {
    if (!config) return
    if (abortFlags.has(upload.id)) return
    if (!upload.batchId) {
        throw new Error('Missing upload batch id')
    }

    upload.phase = 'sending'
    await idbPut(upload)

    const sendController = new AbortController()
    sendControllers.set(upload.id, sendController)
    let sendTimedOut = false
    const sendTimeoutId = window.setTimeout(() => {
        if (sendControllers.get(upload.id) !== sendController) return
        sendTimedOut = true
        sendController.abort()
    }, SEND_REQUEST_TIMEOUT_MS)

    try {
        const commitPayload = await uploadApiFetch<UploadBatchCommitPayload>(`/chat/upload-batches/${upload.batchId}/commit`, {
            method: 'POST',
            signal: sendController.signal,
        })

        const serverMessage = Array.isArray(commitPayload.messages) ? commitPayload.messages[0] : null
        if (!serverMessage) {
            throw new Error('پیام نهایی از سرور دریافت نشد')
        }

        upload.phase = 'sent'
        stopUploadActivity(upload)
        emit({
            type: 'sent',
            userId: upload.userId,
            optimisticId: upload.id,
            serverMessage,
            localBlobUrl: upload.localBlobUrl,
        })

        pendingUploads.delete(upload.id)
        await deletePersistedUpload(upload.id)
    } catch (error) {
        if (serviceWorkerHandoffAbortIds.has(upload.id)) {
            serviceWorkerHandoffAbortIds.delete(upload.id)
            upload.phase = 'uploaded'
            await idbPut(upload)
            resolveServiceWorkerHandoff(upload.id)
            return
        }
        if (abortFlags.has(upload.id)) {
            upload.phase = 'cancelled'
            stopUploadActivity(upload)
            pendingUploads.delete(upload.id)
            await deletePersistedUpload(upload.id)
            abortFlags.delete(upload.id)
            emit({ type: 'cancelled', userId: upload.userId, optimisticId: upload.id })
            return
        }

        const normalizedError = sendTimedOut ? new Error('Network Error (send timeout)') : error

        if (
            isTransientUploadError(normalizedError) &&
            (upload.sendRetryCount ?? 0) < MAX_SEND_RETRIES &&
            !abortFlags.has(upload.id)
        ) {
            const attempt = (upload.sendRetryCount ?? 0) + 1
            upload.sendRetryCount = attempt
            upload.phase = 'uploaded'
            await idbPut(upload)
            const delay = computeSendRetryDelayMs(attempt - 1)
            console.warn(
                `[uploadService] transient /chat/send error (attempt ${attempt}/${MAX_SEND_RETRIES}), retrying in ${delay}ms:`,
                normalizedError,
            )
            setTimeout(() => {
                if (abortFlags.has(upload.id)) return
                if (!pendingUploads.has(upload.id)) return
                void commitSingleUploadBatch(upload)
            }, delay)
            return
        }
        await markFailed(
            upload,
            normalizedError instanceof Error ? normalizedError.message : String(normalizedError),
        )
    } finally {
        window.clearTimeout(sendTimeoutId)
        if (sendControllers.get(upload.id) === sendController) {
            sendControllers.delete(upload.id)
        }
    }
}

function getCommittedAlbumMessageIndex(message: Message): number {
    try {
        const parsed = JSON.parse(message.content) as { album_index?: unknown }
        const albumIndex = Number(parsed?.album_index)
        if (Number.isFinite(albumIndex) && albumIndex >= 0) {
            return albumIndex
        }
    } catch {
        /* ignore malformed payloads and fall back to message order */
    }
    return Number.MAX_SAFE_INTEGER
}

async function commitAlbumBatch(batch: AlbumBatchState, uploads: PendingUpload[]): Promise<void> {
    if (!config) return
    if (!batch.batchId) {
        throw new Error('Missing album upload batch id')
    }

    const sendableUploads = uploads
        .filter((upload) => !abortFlags.has(upload.id))
        .sort((a, b) => a.albumIndex - b.albumIndex)

    if (sendableUploads.length === 0) {
        cleanupAlbumBatchIfSettled(batch.albumId)
        return
    }

    await Promise.all(sendableUploads.map(async (upload) => {
        upload.phase = 'sending'
        await idbPut(upload)
    }))

    const controller = new AbortController()
    let sendTimedOut = false
    const timeoutId = window.setTimeout(() => {
        sendTimedOut = true
        controller.abort()
    }, SEND_REQUEST_TIMEOUT_MS)

    try {
        const commitPayload = await uploadApiFetch<UploadBatchCommitPayload>(
            `/chat/upload-batches/${batch.batchId}/commit`,
            {
                method: 'POST',
                signal: controller.signal,
            },
        )

        const committedMessages = Array.isArray(commitPayload.messages)
            ? [...commitPayload.messages].sort((left, right) => {
                  const leftIndex = getCommittedAlbumMessageIndex(left)
                  const rightIndex = getCommittedAlbumMessageIndex(right)
                  if (leftIndex !== rightIndex) {
                      return leftIndex - rightIndex
                  }
                  return left.id - right.id
              })
            : []

        if (committedMessages.length < sendableUploads.length) {
            throw new Error('همه پیام های آلبوم از سرور دریافت نشد')
        }

        await Promise.all(sendableUploads.map(async (upload, index) => {
            const serverMessage = committedMessages[index]!
            upload.phase = 'sent'
            stopUploadActivity(upload)
            emit({
                type: 'sent',
                userId: upload.userId,
                optimisticId: upload.id,
                serverMessage,
                localBlobUrl: upload.localBlobUrl,
            })
            pendingUploads.delete(upload.id)
            await deletePersistedUpload(upload.id)
        }))
    } catch (error) {
        const normalizedError = sendTimedOut ? new Error('Network Error (send timeout)') : error
        const shouldRetry =
            isTransientUploadError(normalizedError) &&
            (batch.commitRetryCount ?? 0) < MAX_SEND_RETRIES &&
            !sendableUploads.some((upload) => abortFlags.has(upload.id))

        if (shouldRetry) {
            const attempt = (batch.commitRetryCount ?? 0) + 1
            batch.commitRetryCount = attempt
            await Promise.all(sendableUploads.map(async (upload) => {
                upload.phase = 'uploaded'
                upload.sendRetryCount = attempt
                await idbPut(upload)
            }))
            const delay = computeSendRetryDelayMs(attempt - 1)
            console.warn(
                `[uploadService] transient album commit error (attempt ${attempt}/${MAX_SEND_RETRIES}), retrying in ${delay}ms:`,
                normalizedError,
            )
            setTimeout(() => {
                if (!albumBatches.has(batch.albumId)) return
                void flushAlbumBatchIfReady(batch.albumId)
            }, delay)
            return
        }

        await Promise.all(sendableUploads.map(async (upload) => {
            await markFailed(
                upload,
                normalizedError instanceof Error ? normalizedError.message : String(normalizedError),
            )
        }))
    } finally {
        window.clearTimeout(timeoutId)
    }
}

async function sendOneLegacy(upload: PendingUpload): Promise<void> {
    if (!config) return
    if (abortFlags.has(upload.id)) return

    upload.phase = 'sending'
    await idbPut(upload)

    const content = buildContent(upload, 'final')
    const token = config.getAuthToken()
    const endpoint = `${config.apiBaseUrl}/api${buildChatSendEndpoint(upload.userId)}`
    const body = buildChatSendBody(upload.userId, {
        content,
        message_type: upload.msgType,
    })

    const sendController = new AbortController()
    sendControllers.set(upload.id, sendController)
    let sendTimedOut = false
    const sendTimeoutId = window.setTimeout(() => {
        if (sendControllers.get(upload.id) !== sendController) return
        sendTimedOut = true
        sendController.abort()
    }, SEND_REQUEST_TIMEOUT_MS)

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            signal: sendController.signal,
            body: JSON.stringify(body),
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
        stopUploadActivity(upload)
        emit({
            type: 'sent',
            userId: upload.userId,
            optimisticId: upload.id,
            serverMessage,
            localBlobUrl: upload.localBlobUrl,
        })

        pendingUploads.delete(upload.id)
        await deletePersistedUpload(upload.id)
        if (upload.albumId) cleanupAlbumBatchIfSettled(upload.albumId)
    } catch (error) {
        if (abortFlags.has(upload.id)) {
            upload.phase = 'cancelled'
            stopUploadActivity(upload)
            pendingUploads.delete(upload.id)
            await deletePersistedUpload(upload.id)
            abortFlags.delete(upload.id)
            emit({ type: 'cancelled', userId: upload.userId, optimisticId: upload.id })
            if (upload.albumId) cleanupAlbumBatchIfSettled(upload.albumId)
            return
        }

        const normalizedError = sendTimedOut ? new Error('Network Error (send timeout)') : error

        if (
            isTransientUploadError(normalizedError) &&
            (upload.sendRetryCount ?? 0) < MAX_SEND_RETRIES &&
            !abortFlags.has(upload.id)
        ) {
            const attempt = (upload.sendRetryCount ?? 0) + 1
            upload.sendRetryCount = attempt
            upload.phase = 'uploaded'
            await idbPut(upload)
            const delay = computeSendRetryDelayMs(attempt - 1)
            console.warn(
                `[uploadService] transient /chat/send error (attempt ${attempt}/${MAX_SEND_RETRIES}), retrying in ${delay}ms:`,
                normalizedError,
            )
            setTimeout(() => {
                if (abortFlags.has(upload.id)) return
                if (!pendingUploads.has(upload.id)) return
                void sendOneLegacy(upload)
            }, delay)
            return
        }
        await markFailed(
            upload,
            normalizedError instanceof Error ? normalizedError.message : String(normalizedError),
        )
    } finally {
        window.clearTimeout(sendTimeoutId)
        if (sendControllers.get(upload.id) === sendController) {
            sendControllers.delete(upload.id)
        }
    }
}

async function sendOne(upload: PendingUpload): Promise<void> {
    if (shouldUseSessionBackedUpload(upload)) {
        await commitSingleUploadBatch(upload)
        return
    }

    await sendOneLegacy(upload)
}

async function markFailed(upload: PendingUpload, errorMessage: string) {
    upload.phase = 'failed'
    upload.errorMessage = errorMessage
    stopUploadActivity(upload)
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
    ensureUploadServiceWorkerBridge()
    ensureUploadForegroundRecovery()

    resumePromise = (async () => {
        try {
            await waitForNonCriticalResumeSlot()
            const stored = (await idbGetAll()).sort(comparePendingUploadsForTimeline)
            if (!stored.length) {
                setUploadResumeHint(false)
            }
            for (const [index, record] of stored.entries()) {
                await yieldUploadRestoreLoop(index)
                // Reset transient XHR state; progress is preserved from last write
                pendingUploads.set(record.id, record)
                refreshUploadResumeHintFromState()
                if (record.albumId) {
                    const batch = ensureAlbumBatch(
                        record.albumId,
                        record.userId,
                        record.albumSize || 1,
                        record.roomKind,
                        record.batchId,
                    )
                    batch.optimisticIds.add(record.id)
                }
            }

            // Resume each upload in the background
            const resumedAlbumIds = new Set<string>()
            let resumeIndex = 0
            for (const upload of pendingUploads.values()) {
                await yieldUploadRestoreLoop(resumeIndex)
                resumeIndex += 1
                // Rebuild a local blob URL so UI can show the preview again
                if (upload.file && !upload.localBlobUrl) {
                    try {
                        upload.localBlobUrl = URL.createObjectURL(upload.file)
                    } catch {
                        /* ignore */
                    }
                }

                if (upload.phase === 'failed' || upload.phase === 'sent' || upload.phase === 'cancelled') {
                    // Stale — drop
                    pendingUploads.delete(upload.id)
                    await deletePersistedUpload(upload.id)
                    continue
                }

                startUploadActivity(upload)

                emit({
                    type: 'added',
                    userId: upload.userId,
                    optimisticId: upload.id,
                    message: buildOptimisticMessageFromUpload(upload),
                })

                // Re-enter at the appropriate phase
                if (
                    upload.phase === 'uploaded' ||
                    upload.phase === 'sending'
                ) {
                    // upload-media already done; just re-send
                    if (upload.albumId) {
                        if (resumedAlbumIds.has(upload.albumId)) continue
                        resumedAlbumIds.add(upload.albumId)
                        if (upload.phase === 'sending') {
                            await restoreAlbumBatchFromSendingState(upload.albumId)
                        }
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

export async function waitForChatUploadBackgroundReady(): Promise<void> {
    await (resumePromise || Promise.resolve())
}

export async function submitUpload(params: SubmitUploadParams): Promise<void> {
    const serviceConfig = getOrCreateServiceConfig()

    if (!initialized) {
        await initChatUploadBackground(serviceConfig)
    }

    const handoffStartMark = `upload-handoff-${params.optimisticId}-start`
    const handoffQueuedMark = `upload-handoff-${params.optimisticId}-queued`
    markMessengerPerformance(handoffStartMark)

    const upload: PendingUpload = {
        id: params.optimisticId,
        userId: params.userId,
        roomKind: params.roomKind,
        senderId: params.senderId,
        msgType: params.msgType,
        file: params.file,
        fileName: params.fileName,
        mimeType: params.mimeType,
        thumbnail: params.thumbnail,
        width: params.width,
        height: params.height,
        durationMs: params.durationMs,
        caption: params.caption,
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

    setUploadResumeHint(true)
    pendingUploads.set(upload.id, upload)
    startUploadActivity(upload)

    if (upload.albumId) {
        const batch = ensureAlbumBatch(
            upload.albumId,
            upload.userId,
            upload.albumSize,
            upload.roomKind,
            upload.batchId,
        )
        batch.optimisticIds.add(upload.id)
    }

    await idbPut(upload)
    markMessengerPerformance(handoffQueuedMark)
    measureMessengerStage2('upload-handoff-to-persisted', handoffStartMark, handoffQueuedMark, {
        userId: upload.userId,
        optimisticId: upload.id,
        roomKind: upload.roomKind,
        msgType: upload.msgType,
    })
    recordMessengerMetric('upload-handoff-bytes', upload.file.size, 'bytes', {
        userId: upload.userId,
        optimisticId: upload.id,
        roomKind: upload.roomKind,
        msgType: upload.msgType,
    })

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

    const sendController = sendControllers.get(id)
    if (sendController) {
        try {
            sendController.abort()
        } catch {
            /* ignore */
        }
        sendControllers.delete(id)
    }

    // If XHR already finished or never started, clean up eagerly
    if (upload.phase !== 'uploading' && (upload.phase !== 'sending' || !sendController)) {
        upload.phase = 'cancelled'
        stopUploadActivity(upload)
        pendingUploads.delete(id)
        abortFlags.delete(id)
        if (shouldUseSessionBackedUpload(upload)) {
            void cancelServerUploadState(upload)
        }
        deletePersistedUploadSoon(id)
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
    return result.sort(comparePendingUploadsForTimeline)
}

export function retryFailedUpload(id: number): void {
    const upload = pendingUploads.get(id)
    if (!upload) return
    if (upload.phase !== 'failed') return
    abortFlags.delete(id)
    upload.retryCount = 0
    upload.errorMessage = undefined
    startUploadActivity(upload)

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

export const __chatUploadBackgroundTestHooks = {
    UploadApiError,
    normalizeUploadRoomKind,
    isSessionBackedUploadRoomKind,
    shouldUseSessionBackedUpload,
    isSingleSessionBackedUpload,
    canUseUploadServiceWorker,
    resolveUploadTargetId,
    buildSingleUploadBatchIdempotencyKey,
    buildAlbumUploadBatchIdempotencyKey,
    applyPreviewMetadataToUpload,
    getUploadPreviewMetadata,
    getUploadResumeProgress,
    uploadApiFetch,
    isTransientUploadError,
    computeRetryDelayMs,
    computeSendRetryDelayMs,
    blobToDataUrl,
    dataUrlToBlob,
    restorePersistedFile,
    normalizePersistedUpload,
    putRecord,
    openDB,
    idbPut,
    idbDelete,
    idbGet,
    idbGetAll,
    emit,
    startUploadActivity,
    stopUploadActivity,
    ensureAlbumBatch,
    cleanupAlbumBatchIfSettled,
    flushAlbumBatchIfReady,
    ensureStalledAlbumsWatchdog,
    clearResumableUploadState,
    buildContent,
    syncResumableUploadState,
    ensureResumableUploadBatch,
    ensureResumableUploadSession,
    appendResumableUploadChunk,
    finalizeResumableUploadSession,
    cancelServerUploadState,
    runResumableUpload,
    runLegacyUpload,
    commitSingleUploadBatch,
    getCommittedAlbumMessageIndex,
    commitAlbumBatch,
    sendOneLegacy,
    sendOne,
    markFailed,
    forceFlushStalledAlbums,
    restoreAlbumBatchFromSendingState,
    pauseUploadForServiceWorker,
    postUploadsToServiceWorker,
    handoffEligibleUploadsToServiceWorker,
    reclaimUploadsFromServiceWorker,
    resumePendingUploadsAfterForegroundWake,
    state: {
        pendingUploads,
        albumBatches,
        uploadQueue,
        xhrControllers,
        sendControllers,
        abortFlags,
        serviceWorkerOwnedUploads,
        serviceWorkerHandoffAbortIds,
        serviceWorkerHandoffResolvers,
        uploadActivityCounts,
    },
}
