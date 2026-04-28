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

// Strict whitelist: only mime types Chrome/Safari can actually render in-tab
// without falling back to a forced download. HEIC, xlsx, docx, zip, etc. are
// intentionally excluded — sending them to window.open would re-trigger the
// browser's download manager every time (the bug we're fixing).
const INLINE_VIEWABLE_EXACT = new Set([
    'application/pdf',
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/gif',
    'image/webp',
    'image/svg+xml',
    'image/avif',
    'image/bmp',
    'image/x-icon',
    'video/mp4',
    'video/webm',
    'video/ogg',
    'audio/mpeg',
    'audio/mp4',
    'audio/aac',
    'audio/wav',
    'audio/ogg',
    'audio/webm',
    'audio/x-m4a',
    'text/plain',
    'text/html',
    'text/css',
    'text/csv',
    'text/markdown',
    'application/json',
    'application/xml',
    'text/xml',
])

function isInlineViewable(mimeType: string, fileName: string): boolean {
    const mt = (mimeType || '').toLowerCase()
    if (mt && INLINE_VIEWABLE_EXACT.has(mt)) return true
    // Some servers send a generic image/* or video/* with a subtype the browser
    // can't render (e.g. image/heic). Only allow common inline subtypes.
    const ext = (fileName.split('.').pop() || '').toLowerCase()
    if (['pdf', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'avif', 'bmp', 'mp4', 'webm', 'ogg', 'mp3', 'wav', 'm4a', 'aac', 'txt', 'json', 'xml', 'md', 'csv', 'html'].includes(ext)) {
        // Block known browser-unfriendly cases even if extension looks viewable.
        if (mt.includes('heic') || mt.includes('heif')) return false
        return true
    }
    return false
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


async function presentCachedFile(entry: CachedFileEntry, fileName: string, mode: 'open' | 'share' | 'download' = 'open'): Promise<void> {
    const displayName = entry.fileName || fileName || 'file'
    const mimeType = entry.mimeType || entry.blob.type || ''

    if (mode === 'download') {
        triggerAnchorDownload(entry.blob, displayName)
        return
    }

    if (mode === 'share') {
        // Share = always share sheet. If unavailable on this device, we still
        // do NOT trigger an anchor download (would prompt "Download again?");
        // we open in tab if possible, otherwise no-op gracefully.
        const shared = await shareBlob(entry, displayName)
        if (shared) return
        if (isInlineViewable(mimeType, displayName) && openBlobInTab(entry.blob, displayName)) return
        // Last resort only if neither share nor inline viewer is available:
        triggerAnchorDownload(entry.blob, displayName)
        return
    }

    // mode === 'open' (tap on file)
    // Priority: in-tab viewer for browser-renderable types, else share sheet
    // (which on Android exposes "Open With…" via installed apps), and only
    // anchor download when nothing else is reachable. This keeps "tap to open"
    // and "tap to share" behaviorally distinct: tap usually shows the file
    // inline, share always shows the OS share sheet.
    if (isInlineViewable(mimeType, displayName) && openBlobInTab(entry.blob, displayName)) {
        return
    }
    const shared = await shareBlob(entry, displayName)
    if (shared) return
    triggerAnchorDownload(entry.blob, displayName)
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
 * Open a chat file (tap-to-open intent). If the file is cached, opens it
 * immediately offline; otherwise downloads once, caches it, and then opens.
 *
 * Presentation prefers an in-tab viewer for browser-renderable types and
 * falls back to the OS share sheet (which exposes "Open With…" on Android)
 * for binary formats the browser can't render. Tap-to-open and the share
 * button are intentionally distinct: tap shows the file, share shows the
 * share sheet.
 */
export async function handleFileClick(fileId: string, fileUrl: string, fileName: string): Promise<void> {
    if (!fileId) return
    if (downloadingFiles[fileId]) return

    const cached = await readCachedEntry(fileId)
    if (cached) {
        await presentCachedFile(cached, cached.fileName || fileName, 'open')
        return
    }

    downloadingFiles[fileId] = true
    try {
        const entry = await fetchAndCacheFile(fileId, fileUrl, fileName)
        await presentCachedFile(entry, entry.fileName, 'open')
    } catch (err) {
        console.error('[useChatFileHandler] file download failed', err)
        throw err
    } finally {
        delete downloadingFiles[fileId]
    }
}

/**
 * Force a real save-to-disk action. Reserved for explicit "download" UI.
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
 * Always renders the share sheet (never an anchor download) so the user
 * always sees a different UI from the tap-to-open viewer path.
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

    if (mimeType && !entry.mimeType) {
        entry = { ...entry, mimeType }
    }

    await presentCachedFile(entry, entry.fileName || fileName, 'share')
    return true
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
