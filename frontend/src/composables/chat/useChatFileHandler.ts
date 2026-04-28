import { reactive, readonly } from 'vue'
import localforage from 'localforage'

/**
 * Native-like PWA file handler.
 *
 * - Caches downloaded chat files in IndexedDB via `localforage` so re-clicking
 *   a document does NOT redownload it.
 * - Triggers the OS "Open With…" dialog by creating a local Object URL and
 *   programmatically clicking a hidden <a download> element.
 * - Supports the Web Share API (`navigator.share({ files })`) for forwarding
 *   the cached file to other apps installed on the device.
 * - Exposes cache-size and clear-cache helpers for the storage management UI
 *   inside the user profile.
 *
 * State is held at module level so every component (chat bubble, profile UI,
 * etc.) sees the same `downloadingFiles` map and cache instance.
 */

const fileStore = localforage.createInstance({
    name: 'trading-bot-chat-files',
    storeName: 'files',
    description: 'Cached chat document/file blobs for offline reuse and Web Share.',
})

interface CachedFileEntry {
    blob: Blob
    fileName: string
    mimeType: string
    size: number
    cachedAt: number
}

const downloadingFiles = reactive<Record<string, boolean>>({})

function isCachedFileEntry(value: unknown): value is CachedFileEntry {
    return Boolean(
        value
        && typeof value === 'object'
        && (value as CachedFileEntry).blob instanceof Blob,
    )
}

async function readCachedEntry(fileId: string): Promise<CachedFileEntry | null> {
    try {
        const entry = await fileStore.getItem(fileId)
        return isCachedFileEntry(entry) ? entry : null
    } catch (err) {
        console.warn('[useChatFileHandler] cache read failed', err)
        return null
    }
}

function navHasShareFiles(): boolean {
    const navAny = navigator as Navigator & {
        canShare?: (data: ShareData) => boolean
        share?: (data: ShareData) => Promise<void>
    }
    return typeof navAny.share === 'function' && typeof navAny.canShare === 'function'
}

function isInlineViewable(mimeType: string): boolean {
    if (!mimeType) return false
    return (
        mimeType === 'application/pdf'
        || mimeType.startsWith('image/')
        || mimeType.startsWith('video/')
        || mimeType.startsWith('audio/')
        || mimeType.startsWith('text/')
    )
}

async function shareBlob(entry: CachedFileEntry, fallbackName: string): Promise<boolean> {
    if (!navHasShareFiles()) return false
    const navAny = navigator as Navigator & {
        canShare?: (data: ShareData) => boolean
        share?: (data: ShareData) => Promise<void>
    }
    const file = new File(
        [entry.blob],
        entry.fileName || fallbackName || 'file',
        { type: entry.mimeType || entry.blob.type || 'application/octet-stream' },
    )
    const shareData: ShareData = { files: [file] }
    try {
        if (typeof navAny.canShare === 'function' && !navAny.canShare(shareData)) {
            return false
        }
        if (typeof navAny.share !== 'function') return false
        await navAny.share(shareData)
        return true
    } catch (err) {
        // AbortError = user closed the share sheet; treat as success-equivalent
        // so we don't fall back to a duplicate download prompt.
        if ((err as DOMException)?.name === 'AbortError') return true
        return false
    }
}

function openBlobInTab(blob: Blob, fileName: string): boolean {
    const objectUrl = URL.createObjectURL(blob)
    const win = window.open(objectUrl, '_blank', 'noopener,noreferrer')
    setTimeout(() => {
        try { URL.revokeObjectURL(objectUrl) } catch { /* noop */ }
    }, 60_000)
    if (!win) return false
    try { win.document && (win.document.title = fileName || 'file') } catch { /* cross-origin no-op */ }
    return true
}

function triggerAnchorDownload(blob: Blob, fileName: string) {
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = fileName || 'file'
    anchor.rel = 'noopener'
    anchor.style.display = 'none'
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    setTimeout(() => {
        try { URL.revokeObjectURL(objectUrl) } catch { /* noop */ }
    }, 4000)
}

async function presentCachedFile(entry: CachedFileEntry, fileName: string, force: 'auto' | 'share' | 'download' = 'auto'): Promise<void> {
    if (force === 'download') {
        triggerAnchorDownload(entry.blob, fileName)
        return
    }
    if (force === 'share') {
        const shared = await shareBlob(entry, fileName)
        if (shared) return
        // Fall through to viewer path
    }
    // Auto: prefer share sheet on mobile (native "Open With"), then in-tab viewer,
    // then anchor download as a last resort.
    if (force === 'auto' && navHasShareFiles()) {
        const shared = await shareBlob(entry, fileName)
        if (shared) return
    }
    if (isInlineViewable(entry.mimeType || entry.blob.type || '')) {
        if (openBlobInTab(entry.blob, fileName)) return
    }
    triggerAnchorDownload(entry.blob, fileName)
}

async function fetchAndCacheFile(fileId: string, fileUrl: string, fileName: string): Promise<CachedFileEntry> {
    const response = await fetch(fileUrl, { credentials: 'include' })
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
    }
    const blob = await response.blob()
    const entry: CachedFileEntry = {
        blob,
        fileName: fileName || 'file',
        mimeType: blob.type || 'application/octet-stream',
        size: blob.size,
        cachedAt: Date.now(),
    }
    try {
        await fileStore.setItem(fileId, entry)
    } catch (err) {
        // Storage quota / private mode / etc. Cache failure is non-fatal — we
        // still hand the freshly fetched blob to the caller.
        console.warn('[useChatFileHandler] cache write failed', err)
    }
    return entry
}

/**
 * Open a chat file. If the file is cached, it opens immediately offline;
 * otherwise it downloads once, caches it, and then opens it.
 *
 * Default presentation prefers `navigator.share({ files })` on mobile so the
 * OS shows its native "Open With…" sheet instead of forcing a redundant
 * "Download again?" prompt for already-downloaded files.
 */
export async function handleFileClick(fileId: string, fileUrl: string, fileName: string): Promise<void> {
    if (!fileId) return
    if (downloadingFiles[fileId]) return

    const cached = await readCachedEntry(fileId)
    if (cached) {
        await presentCachedFile(cached, cached.fileName || fileName, 'auto')
        return
    }

    downloadingFiles[fileId] = true
    try {
        const entry = await fetchAndCacheFile(fileId, fileUrl, fileName)
        await presentCachedFile(entry, entry.fileName, 'auto')
    } catch (err) {
        console.error('[useChatFileHandler] file download failed', err)
        throw err
    } finally {
        delete downloadingFiles[fileId]
    }
}

/**
 * Force a real download to disk (last-resort path used by the chat context
 * menu / explicit "download" actions).
 */
export async function downloadFileToDisk(fileId: string, fileUrl: string, fileName: string): Promise<void> {
    if (!fileId) return
    const cached = await readCachedEntry(fileId)
    if (cached) {
        await presentCachedFile(cached, cached.fileName || fileName, 'download')
        return
    }
    if (downloadingFiles[fileId]) return
    downloadingFiles[fileId] = true
    try {
        const entry = await fetchAndCacheFile(fileId, fileUrl, fileName)
        await presentCachedFile(entry, entry.fileName, 'download')
    } finally {
        delete downloadingFiles[fileId]
    }
}

/**
 * Forward the cached file to another app via the Web Share API.
 * If the file is not cached yet, it is fetched first (best-effort) so the
 * share button doesn't silently fail on first click.
 * Returns `true` if the share dialog was reachable.
 */
export async function shareFile(fileId: string, fileName: string, mimeType: string, fileUrl?: string): Promise<boolean> {
    if (!fileId) return false
    if (!navHasShareFiles()) return false

    let entry = await readCachedEntry(fileId)
    if (!entry && fileUrl) {
        if (downloadingFiles[fileId]) return false
        downloadingFiles[fileId] = true
        try {
            entry = await fetchAndCacheFile(fileId, fileUrl, fileName)
        } catch (err) {
            console.warn('[useChatFileHandler] share pre-fetch failed', err)
            return false
        } finally {
            delete downloadingFiles[fileId]
        }
    }
    if (!entry) return false

    const file = new File(
        [entry.blob],
        entry.fileName || fileName || 'file',
        { type: entry.mimeType || mimeType || 'application/octet-stream' },
    )

    const navAny = navigator as Navigator & {
        canShare?: (data: ShareData) => boolean
        share?: (data: ShareData) => Promise<void>
    }

    const shareData: ShareData = { files: [file] }
    try {
        if (typeof navAny.canShare === 'function' && !navAny.canShare(shareData)) {
            return false
        }
        if (typeof navAny.share !== 'function') return false
        await navAny.share(shareData)
        return true
    } catch (err) {
        if ((err as DOMException)?.name === 'AbortError') return true
        console.warn('[useChatFileHandler] share failed', err)
        return false
    }
}

/** Returns true if Web Share with files is reachable on this device. */
export function canShareFiles(): boolean {
    const navAny = navigator as Navigator & {
        canShare?: (data: ShareData) => boolean
        share?: (data: ShareData) => Promise<void>
    }
    if (typeof navAny.share !== 'function') return false
    if (typeof navAny.canShare !== 'function') return false
    try {
        const probe = new File([new Blob([''], { type: 'text/plain' })], 'probe.txt', { type: 'text/plain' })
        return navAny.canShare({ files: [probe] })
    } catch {
        return false
    }
}

/** Drop the entire cached-files store. */
export async function clearFileCache(): Promise<void> {
    try {
        await fileStore.clear()
    } catch (err) {
        console.error('[useChatFileHandler] clear cache failed', err)
        throw err
    }
}

/** Total bytes currently held in the cache. */
export async function getCacheBytes(): Promise<number> {
    let total = 0
    try {
        await fileStore.iterate<unknown, void>((value) => {
            if (isCachedFileEntry(value)) {
                total += value.size || value.blob.size || 0
            }
        })
    } catch (err) {
        console.warn('[useChatFileHandler] cache size scan failed', err)
    }
    return total
}

/** Total cache size in MB, formatted with 2 decimals. */
export async function getCacheSize(): Promise<string> {
    const bytes = await getCacheBytes()
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export function useChatFileHandler() {
    return {
        handleFileClick,
        downloadFileToDisk,
        shareFile,
        canShareFiles,
        clearFileCache,
        getCacheSize,
        getCacheBytes,
        downloadingFiles: readonly(downloadingFiles),
    }
}
