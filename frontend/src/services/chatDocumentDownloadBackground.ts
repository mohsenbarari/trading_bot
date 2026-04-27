export type DocumentDownloadPhase = 'queued' | 'downloading' | 'completed' | 'failed' | 'cancelled'

export interface PendingDocumentDownload {
    messageId: number
    userId: number
    fileId: string
    fileName: string
    mimeType: string
    phase: DocumentDownloadPhase
    progress: number
    downloadedBytes: number
    totalBytes: number
    createdAt: string
    retryCount?: number
}

export type DocumentDownloadEvent =
    | {
        type: 'added'
        userId: number
        messageId: number
        fileId: string
        progress: number
        downloadedBytes: number
        totalBytes: number
    }
    | {
        type: 'progress'
        userId: number
        messageId: number
        fileId: string
        progress: number
        downloadedBytes: number
        totalBytes: number
    }
    | {
        type: 'completed'
        userId: number
        messageId: number
        fileId: string
        objectUrl: string
        fileName: string
    }
    | {
        type: 'error'
        userId: number
        messageId: number
        fileId: string
        errorMessage: string
    }
    | {
        type: 'cancelled'
        userId: number
        messageId: number
        fileId: string
    }

export type DocumentDownloadEventHandler = (event: DocumentDownloadEvent) => void

interface ServiceConfig {
    apiBaseUrl: string
    getAuthToken: () => string | null
}

interface StartDocumentDownloadParams {
    messageId: number
    userId: number
    fileId: string
    fileName: string
    mimeType?: string
}

const DB_NAME = 'chat_document_download_queue'
const DB_VERSION = 1
const STORE_NAME = 'pending'
const MAX_CONCURRENT_DOWNLOADS = 2
const MAX_DOWNLOAD_RETRIES = 3

let config: ServiceConfig | null = null
let initialized = false
let resumePromise: Promise<void> | null = null
let dbPromise: Promise<IDBDatabase> | null = null
let activeDownloadCount = 0

const pendingDownloads = new Map<number, PendingDocumentDownload>()
const activeControllers = new Map<number, AbortController>()
const completedDownloadUrls = new Map<string, string>()
const subscribers = new Set<DocumentDownloadEventHandler>()
const downloadQueue: PendingDocumentDownload[] = []
const abortFlags = new Set<number>()

function emit(event: DocumentDownloadEvent) {
    for (const handler of subscribers) {
        try {
            handler(event)
        } catch (error) {
            console.warn('[documentDownloadService] subscriber failed:', error)
        }
    }
}

function buildDownloadUrl(fileId: string) {
    if (!config) return ''

    const token = config.getAuthToken()
    const query = token ? `?token=${encodeURIComponent(token)}` : ''
    return `${config.apiBaseUrl}/api/chat/files/${encodeURIComponent(fileId)}${query}`
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

function isTransientDownloadError(error: unknown) {
    const message = error instanceof Error ? error.message : String(error)
    if (!message) return false

    if (/network error/i.test(message)) return true
    if (/failed to fetch/i.test(message)) return true
    if (/networkerror/i.test(message)) return true
    if (/load failed/i.test(message)) return true
    if (/connection was lost/i.test(message)) return true
    if (/\b(502|503|504|520|521|522|524|408)\b/.test(message)) return true
    return false
}

function computeRetryDelayMs(attempt: number) {
    const base = Math.min(1000 * Math.pow(2, Math.max(0, attempt)), 8000)
    const jitter = Math.floor(Math.random() * 1000) - 500
    return Math.max(750, base + jitter)
}

function openDB(): Promise<IDBDatabase> {
    if (dbPromise) return dbPromise

    dbPromise = new Promise((resolve, reject) => {
        try {
            const req = indexedDB.open(DB_NAME, DB_VERSION)
            req.onupgradeneeded = (event) => {
                const db = (event.target as IDBOpenDBRequest).result
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME, { keyPath: 'messageId' })
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

async function idbPut(download: PendingDocumentDownload) {
    try {
        const db = await openDB()
        await new Promise<void>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readwrite')
                tx.objectStore(STORE_NAME).put({ ...download })
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

async function idbDelete(messageId: number) {
    try {
        const db = await openDB()
        await new Promise<void>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readwrite')
                tx.objectStore(STORE_NAME).delete(messageId)
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

async function idbGetAll(): Promise<PendingDocumentDownload[]> {
    try {
        const db = await openDB()
        return await new Promise<PendingDocumentDownload[]>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readonly')
                const req = tx.objectStore(STORE_NAME).getAll()
                req.onsuccess = () => resolve((req.result as PendingDocumentDownload[]) || [])
                req.onerror = () => resolve([])
            } catch {
                resolve([])
            }
        })
    } catch {
        return []
    }
}

function enqueueDownload(download: PendingDocumentDownload) {
    if (downloadQueue.some(item => item.messageId === download.messageId)) return
    if (activeControllers.has(download.messageId)) return
    downloadQueue.push(download)
    pumpDownloadQueue()
}

function pumpDownloadQueue() {
    while (activeDownloadCount < MAX_CONCURRENT_DOWNLOADS && downloadQueue.length > 0) {
        const next = downloadQueue.shift()
        if (!next) break
        if (abortFlags.has(next.messageId)) continue
        if (!pendingDownloads.has(next.messageId)) continue
        activeDownloadCount += 1
        void runDownloadWithGate(next)
    }
}

async function runDownloadWithGate(download: PendingDocumentDownload) {
    try {
        await runDownload(download)
    } finally {
        activeDownloadCount = Math.max(0, activeDownloadCount - 1)
        pumpDownloadQueue()
    }
}

async function markFailed(download: PendingDocumentDownload, errorMessage: string) {
    download.phase = 'failed'
    pendingDownloads.delete(download.messageId)
    await idbDelete(download.messageId)
    emit({
        type: 'error',
        userId: download.userId,
        messageId: download.messageId,
        fileId: download.fileId,
        errorMessage,
    })
}

async function runDownload(download: PendingDocumentDownload) {
    if (!config) return
    if (abortFlags.has(download.messageId)) return

    const controller = new AbortController()
    activeControllers.set(download.messageId, controller)
    download.phase = 'downloading'
    await idbPut(download)

    try {
        const response = await fetch(buildDownloadUrl(download.fileId), {
            signal: controller.signal,
        })

        if (!response.ok) {
            throw new Error(`Download failed (${response.status})`)
        }

        const contentType = response.headers.get('content-type') || download.mimeType || 'application/octet-stream'
        const contentLength = response.headers.get('content-length')
        const total = contentLength ? parseInt(contentLength, 10) : 0

        if (!response.body || !total) {
            const blob = await response.blob()
            const objectUrl = URL.createObjectURL(blob)
            const previousUrl = completedDownloadUrls.get(download.fileId)
            if (previousUrl && previousUrl !== objectUrl) {
                URL.revokeObjectURL(previousUrl)
            }
            completedDownloadUrls.set(download.fileId, objectUrl)
            triggerBrowserDownload(objectUrl, download.fileName)
            pendingDownloads.delete(download.messageId)
            await idbDelete(download.messageId)
            emit({
                type: 'completed',
                userId: download.userId,
                messageId: download.messageId,
                fileId: download.fileId,
                objectUrl,
                fileName: download.fileName,
            })
            return
        }

        const reader = response.body.getReader()
        const chunks: Uint8Array[] = []
        let received = 0

        while (true) {
            const { done, value } = await reader.read()
            if (done) break
            if (!value) continue
            chunks.push(value)
            received += value.length
            download.progress = Math.round((received / total) * 100)
            download.downloadedBytes = received
            download.totalBytes = total
            emit({
                type: 'progress',
                userId: download.userId,
                messageId: download.messageId,
                fileId: download.fileId,
                progress: download.progress,
                downloadedBytes: received,
                totalBytes: total,
            })
        }

        const blob = new Blob(chunks as BlobPart[], { type: contentType })
        const objectUrl = URL.createObjectURL(blob)
        const previousUrl = completedDownloadUrls.get(download.fileId)
        if (previousUrl && previousUrl !== objectUrl) {
            URL.revokeObjectURL(previousUrl)
        }
        completedDownloadUrls.set(download.fileId, objectUrl)
        triggerBrowserDownload(objectUrl, download.fileName)
        pendingDownloads.delete(download.messageId)
        await idbDelete(download.messageId)
        emit({
            type: 'completed',
            userId: download.userId,
            messageId: download.messageId,
            fileId: download.fileId,
            objectUrl,
            fileName: download.fileName,
        })
    } catch (error) {
        if (abortFlags.has(download.messageId)) {
            pendingDownloads.delete(download.messageId)
            abortFlags.delete(download.messageId)
            await idbDelete(download.messageId)
            emit({
                type: 'cancelled',
                userId: download.userId,
                messageId: download.messageId,
                fileId: download.fileId,
            })
            return
        }

        if (isTransientDownloadError(error) && (download.retryCount ?? 0) < MAX_DOWNLOAD_RETRIES) {
            const attempt = (download.retryCount ?? 0) + 1
            download.retryCount = attempt
            download.phase = 'queued'
            await idbPut(download)
            const delay = computeRetryDelayMs(attempt - 1)
            window.setTimeout(() => {
                if (abortFlags.has(download.messageId)) return
                if (!pendingDownloads.has(download.messageId)) return
                enqueueDownload(download)
            }, delay)
            return
        }

        await markFailed(download, error instanceof Error ? error.message : String(error))
    } finally {
        activeControllers.delete(download.messageId)
    }
}

export function subscribeToDocumentDownloads(handler: DocumentDownloadEventHandler): () => void {
    subscribers.add(handler)
    return () => subscribers.delete(handler)
}

export function getPendingDocumentDownloadsForUser(userId: number): PendingDocumentDownload[] {
    const result: PendingDocumentDownload[] = []
    for (const download of pendingDownloads.values()) {
        if (download.userId !== userId) continue
        if (download.phase === 'completed' || download.phase === 'cancelled' || download.phase === 'failed') continue
        result.push(download)
    }
    return result
}

export function getCompletedDocumentDownloadUrl(fileId: string): string {
    return completedDownloadUrls.get(fileId) || ''
}

export function cancelDocumentDownload(messageId: number): void {
    const download = pendingDownloads.get(messageId)
    if (!download) return

    abortFlags.add(messageId)
    const controller = activeControllers.get(messageId)
    if (controller) {
        controller.abort()
        return
    }

    pendingDownloads.delete(messageId)
    abortFlags.delete(messageId)
    void idbDelete(messageId)
    emit({
        type: 'cancelled',
        userId: download.userId,
        messageId: download.messageId,
        fileId: download.fileId,
    })
}

export async function startDocumentDownload(params: StartDocumentDownloadParams): Promise<void> {
    if (!config) {
        throw new Error('[documentDownloadService] not initialized')
    }

    const existing = pendingDownloads.get(params.messageId)
    if (existing && (existing.phase === 'queued' || existing.phase === 'downloading')) {
        return
    }

    const completedUrl = completedDownloadUrls.get(params.fileId)
    if (completedUrl) {
        triggerBrowserDownload(completedUrl, params.fileName)
        return
    }

    const download: PendingDocumentDownload = {
        messageId: params.messageId,
        userId: params.userId,
        fileId: params.fileId,
        fileName: params.fileName,
        mimeType: params.mimeType || 'application/octet-stream',
        phase: 'queued',
        progress: 0,
        downloadedBytes: 0,
        totalBytes: 0,
        createdAt: new Date().toISOString(),
    }

    pendingDownloads.set(download.messageId, download)
    await idbPut(download)
    emit({
        type: 'added',
        userId: download.userId,
        messageId: download.messageId,
        fileId: download.fileId,
        progress: 0,
        downloadedBytes: 0,
        totalBytes: 0,
    })
    enqueueDownload(download)
}

export function initChatDocumentDownloadBackground(cfg: ServiceConfig): Promise<void> {
    config = cfg

    if (initialized) {
        return resumePromise || Promise.resolve()
    }
    initialized = true

    resumePromise = (async () => {
        try {
            const stored = await idbGetAll()
            for (const record of stored) {
                if (record.phase === 'completed' || record.phase === 'cancelled' || record.phase === 'failed') {
                    await idbDelete(record.messageId)
                    continue
                }

                record.phase = 'queued'
                pendingDownloads.set(record.messageId, record)
                emit({
                    type: 'added',
                    userId: record.userId,
                    messageId: record.messageId,
                    fileId: record.fileId,
                    progress: record.progress,
                    downloadedBytes: record.downloadedBytes,
                    totalBytes: record.totalBytes,
                })
                enqueueDownload(record)
            }
        } catch (error) {
            console.warn('[documentDownloadService] resume failed:', error)
        }
    })()

    return resumePromise
}